#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
贝尔娜提塔·冯·瓦立 —— QQ bot 主程序
OneBot v11 反向 WebSocket  +  OpenAI 兼容 LLM（DeepSeek / Ollama / 任意）

人格与设定在 plugins/bernie/ 里：
    persona.py    —— 角色卡读取 + system prompt 组装
    worldbook.py  —— 关键词触发的世界书注入
本文件只负责：连 QQ、收消息、调 LLM、存好感度。

用法：
    1. 把 config.example.json 复制成 config.json，填 api_key 和 master_ids
    2. pip install -r requirements.txt
    3. python bot.py
    4. NapCat 里加一条「WebSocket 客户端(反向)」指向 ws://127.0.0.1:8080/onebot/v11/ws
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx

from plugins.bernie import persona, worldbook

try:
    import maimai as maimai_mod  # 水鱼 B50
except Exception:  # 没装 Pillow 之类就静默关掉
    maimai_mod = None

try:
    import memory as memory_mod  # 记忆导出成 md
except Exception:
    memory_mod = None

try:
    import songdb  # 落雪曲库
except Exception:
    songdb = None

try:
    import fortune as fortune_mod  # /今日运势
except Exception:
    fortune_mod = None

try:
    import songcard as songcard_mod  # /随一首 /定数表
except Exception:
    songcard_mod = None

try:
    import menu5c as menu5c_mod  # /5C吃什么（主人专属）
except Exception:
    menu5c_mod = None

try:
    import xiba as xiba_mod  # /随机西八 计分 + /西八榜
except Exception:
    xiba_mod = None

try:
    import members_md  # 群成员.md（欧非榜按群统计用）
except Exception:
    members_md = None

try:
    import vision as vision_mod  # 私聊识图（可选，没配 key 就等于关）
except Exception:
    vision_mod = None

try:
    import maimai_oauth as moauth_mod  # 水鱼账号 OAuth（绑定后能查隐私账号）
except Exception:
    moauth_mod = None

try:
    import gacha as gacha_mod  # 抽卡（曲绘卡池）
except Exception:
    gacha_mod = None

try:  # websockets >= 13
    from websockets.asyncio.server import serve  # type: ignore
except Exception:  # websockets <= 12
    from websockets import serve  # type: ignore

# ---------------------------------------------------------------- 基础

BASE_DIR = Path(__file__).resolve().parent
def _is_test_run() -> bool:
    """跑 *_test.py 时自动换一份独立存档，别让测试把真人的状态冲了。

    子进程也算：测试脚本 `subprocess.Popen([sys.executable, "bot.py"])` 时
    argv[0] 是 bot.py，光看文件名会漏，所以再认一个环境变量。
    """
    if os.environ.get("BERNIE_TEST"):
        return True
    name = Path(sys.argv[0]).name.lower()
    return name.endswith("_test.py") or name.startswith("test_")


STATE_NAME = "state_test.json" if _is_test_run() else "state.json"
STATE_FILE = BASE_DIR / STATE_NAME
STATE_BAK = BASE_DIR / (STATE_NAME + ".bak")     # save() 前自动备份，主文件坏了就读它
KEEP_DAYS = 30                                   # meta.history 里保留多少天的花费账单
STALE_SECONDS = 90                               # 一条反向 WS 超过这么久没动静，就当僵尸连接踢掉

STAGE_NAMES = persona.STAGE_NAMES
STAGE_DESC = persona.STAGE_DESC

STAGE_RE = re.compile(r"\[\[\s*STAGE\s*:\s*([+-]?\d+)\s*\]\]", re.I)
MD_RE = re.compile(r"(\*\*|__|\*|`{1,3})")
SENT_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]*")

log = logging.getLogger("bernie")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- 配置

def load_config() -> dict[str, Any]:
    path = BASE_DIR / "config.json"
    if not path.exists():
        path = BASE_DIR / "config.example.json"
        log.warning("没找到 config.json，先用 config.example.json 顶着（记得填 api_key）")
    text = path.read_text(encoding="utf-8")
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as exc:
        lines = text.splitlines()
        bad = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else ""
        msg = (
            f"\n{path.name} 第 {exc.lineno} 行 JSON 写坏了：{exc.msg}\n"
            f"    >>> {bad}\n"
            "    常见原因：少了引号 / 多了引号 / 用了中文引号「」“”/ 最后一项多了逗号\n"
        )
        raise SystemExit(msg) from None

    env_key = os.environ.get("LLM_API_KEY")
    if env_key:
        cfg.setdefault("llm", {})["api_key"] = env_key
    env_base = os.environ.get("LLM_BASE_URL")
    if env_base:
        cfg.setdefault("llm", {})["base_url"] = env_base

    cfg.setdefault("master_ids", [])
    clean = []
    for x in cfg["master_ids"]:
        try:
            clean.append(int(x))
        except (TypeError, ValueError):
            log.warning("master_ids 里有个不是数字的项，忽略：%r", x)
    cfg["master_ids"] = clean
    return cfg


CFG = load_config()
persona.set_card(CFG.get("card") or persona.DEFAULT_CARD)
CARD = persona.load_card()["data"]

# 群聊上下文保留条数（70 人活跃群要留多一点，不然刚说的话就被冲掉）
GROUP_WINDOW = max(10, int(CFG.get("group", {}).get("context_window", 20)))


def is_master(qq: str) -> bool:
    try:
        return int(qq) in CFG["master_ids"]
    except (TypeError, ValueError):
        return False


def display_name(qq: str, name: str) -> str:
    """主人固定显示成 `master_name`（默认「主人」），其他人用协议端给的昵称。

    这条在存 `STORE.user(qq)["name"]` 之前过一遍，否则手动改的名字会被
    下一条消息写回 QQ 昵称。
    """
    if is_master(qq):
        fixed = str(CFG.get("master_name") or "主人").strip()
        return fixed or "主人"
    return name


# ---------------------------------------------------------------- 状态

_LOCK = asyncio.Lock()


def _blank_user() -> dict[str, Any]:
    return {"stage": 1, "name": "", "history": [], "notes": [], "last_reply": 0.0, "msg_count": 0}


class Store:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, deque] = defaultdict(lambda: deque(maxlen=GROUP_WINDOW))
        self.meta: dict[str, Any] = {}
        self.members: dict[str, dict[str, float]] = {}     # gid → {qq: 最后一次说话时间}
        self._members_dirty = False
        self._load()

    def _load(self) -> None:
        raw: dict[str, Any] | None = None
        for path in (STATE_FILE, STATE_BAK):
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raw = None
                    continue
                if path is STATE_BAK:
                    log.warning("state.json 读不到，改用 %s", STATE_BAK.name)
                break
            except Exception as exc:
                log.warning("%s 读失败：%s", path.name, exc)
                raw = None
        if not raw:
            return
        self.users = raw.get("users", {})
        self.meta = raw.get("meta", {}) or {}
        for gid, rows in (raw.get("members") or {}).items():
            if isinstance(rows, dict):
                self.members[str(gid)] = {str(k): float(v or 0) for k, v in rows.items()}
        for gid, rows in (raw.get("groups") or {}).items():
            self.groups[gid] = deque(rows, maxlen=GROUP_WINDOW)
        log.info("已载入 %d 个用户的状态", len(self.users))

    async def save(self) -> None:
        async with _LOCK:
            # 先把现有文件备份一份，写坏了/被覆盖了还能捞回来
            if STATE_FILE.exists():
                try:
                    shutil.copyfile(STATE_FILE, STATE_BAK)
                except Exception:
                    pass
            tmp = STATE_FILE.with_suffix(".tmp")
            payload = {
                "users": self.users,
                "groups": {g: list(d) for g, d in self.groups.items()},
                "members": self.members,
                "meta": self.meta,
            }
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(STATE_FILE)
            # 同步导出可读版：memory/主人.md 和 memory/群友.md
            if memory_mod is not None:
                try:
                    memory_mod.export(self.users, CFG.get("master_ids", []))
                except Exception as exc:
                    log.warning("记忆导出失败：%s", exc)
            self._members_dirty = False

    def touch_member(self, gid: str, qq: str) -> None:
        """记一下谁在这个群说过话（欧非榜按群统计用）。"""
        if not gid or not qq or str(qq) == "0":
            return
        bucket = self.members.setdefault(str(gid), {})
        old = bucket.get(str(qq))
        now = time.time()
        bucket[str(qq)] = now
        if old is None or now - old > 3600:
            self._members_dirty = True
        if len(bucket) > 800:                      # 别无限长，老的先扔
            keep = sorted(bucket.items(), key=lambda kv: kv[1])[-800:]
            self.members[str(gid)] = dict(keep)

    def user(self, qq: str) -> dict[str, Any]:
        u = self.users.get(qq)
        if u is None:
            u = _blank_user()
            self.users[qq] = u
        u.setdefault("stage", 1)
        u.setdefault("history", [])
        u.setdefault("notes", [])
        u.setdefault("last_reply", 0.0)
        u.setdefault("msg_count", 0)
        return u


STORE = Store()

# ---------------------------------------------------------------- 预算


BUDGET = CFG.get("budget", {})
BLOCK_NOTICE: dict[str, float] = defaultdict(float)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _archive_day() -> None:
    """跨天前把当天花费存进 history —— 不然昨天的账就查不到了。

    `meta["history"]` 是 {日期: {"cost", "ptok", "ctok", "calls", "users": {qq: cost}}}，
    只留最近 `KEEP_DAYS` 天。
    """
    day = STORE.meta.get("date")
    if not day:
        return
    hist = STORE.meta.setdefault("history", {})
    if not isinstance(hist, dict):
        hist = {}
        STORE.meta["history"] = hist
    hist[day] = {
        "cost": round(float(STORE.meta.get("cost") or 0), 4),
        "ptok": int(STORE.meta.get("ptok") or 0),
        "ctok": int(STORE.meta.get("ctok") or 0),
        "calls": int(STORE.meta.get("calls") or 0),
        "users": {
            qq: round(float(u.get("cost") or 0), 4)
            for qq, u in STORE.users.items()
            if float(u.get("cost") or 0) > 0
        },
    }
    for old in sorted(hist)[:-KEEP_DAYS]:
        hist.pop(old, None)


def ensure_day() -> bool:
    """跨天只清零花费相关字段，别把 bonds 之类的设定也一起清掉。"""
    if STORE.meta.get("date") != _today():
        _archive_day()                       # 先把昨天存起来再清
        STORE.meta["date"] = _today()
        STORE.meta["cost"] = 0.0
        STORE.meta["ptok"] = 0
        STORE.meta["ctok"] = 0
        STORE.meta["calls"] = 0
        STORE.meta["owner_cost"] = 0.0
        return True
    for k, v in (("cost", 0.0), ("ptok", 0), ("ctok", 0), ("calls", 0)):
        STORE.meta.setdefault(k, v)
    return False


def ensure_user_day(u: dict[str, Any]) -> None:
    """每个人的当日额度跨天清零。`calls` 一起清，不然它会越滚越大、和 cost 对不上。"""
    if u.get("cost_date") != _today():
        u["cost_date"] = _today()
        u["cost"] = 0.0
        u["ptok"] = 0
        u["ctok"] = 0
        u["calls"] = 0


def budget_spent(qq: str) -> float:
    u = STORE.user(qq)
    ensure_user_day(u)
    return float(u.get("cost", 0.0))


def global_spent() -> float:
    ensure_day()
    return float(STORE.meta.get("cost", 0.0))


def global_exhausted() -> bool:
    if not BUDGET.get("enabled", True):
        return False
    return global_spent() >= float(BUDGET.get("global_daily_cny", 8.0))


def budget_exhausted(qq: str, master: bool = False) -> bool:
    """两级额度，主人完全不参与：既不占全局，也不受全局和个人限制。"""
    if master or not BUDGET.get("enabled", True):
        return False
    if global_exhausted():
        return True
    return budget_spent(qq) >= float(BUDGET.get("user_daily_cny", 2.0))


def add_usage(qq: str, usage: dict[str, Any] | None, master: bool = False) -> float:
    """记在触发这条消息的人头上。主人的花费只记他自己，不进全局池。"""
    if not usage:
        return 0.0
    ensure_day()
    p_in = float(BUDGET.get("prompt_price_per_m", 2.0))
    p_hit = float(BUDGET.get("cache_hit_price_per_m", 0.5))
    p_out = float(BUDGET.get("completion_price_per_m", 8.0))

    pt = int(usage.get("prompt_tokens") or 0)
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or max(0, pt - hit))
    ct = int(usage.get("completion_tokens") or 0)

    cost = (miss * p_in + hit * p_hit + ct * p_out) / 1_000_000

    u = STORE.user(qq)
    ensure_user_day(u)
    u["cost"] = round(float(u.get("cost", 0.0)) + cost, 6)
    u["ptok"] = int(u.get("ptok", 0)) + pt
    u["ctok"] = int(u.get("ctok", 0)) + ct
    u["calls"] = int(u.get("calls", 0)) + 1

    if not master:
        STORE.meta["cost"] = round(float(STORE.meta.get("cost", 0.0)) + cost, 6)
        STORE.meta["ptok"] = int(STORE.meta.get("ptok", 0)) + pt
        STORE.meta["ctok"] = int(STORE.meta.get("ctok", 0)) + ct
        STORE.meta["calls"] = int(STORE.meta.get("calls", 0)) + 1
    else:
        STORE.meta["owner_cost"] = round(float(STORE.meta.get("owner_cost", 0.0)) + cost, 6)
    return cost


class LLM:
    def __init__(self) -> None:
        c = CFG["llm"]
        self.base = c["base_url"].rstrip("/")
        self.key = c.get("api_key") or ""
        self.model = c["model"]
        self.temp = float(c.get("temperature", 1.15))
        self.max_tokens = int(c.get("max_tokens", 700))
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))
        self.last_usage: dict[str, Any] = {}

    async def chat(self, messages: list[dict[str, str]]) -> str | None:
        if not self.key:
            log.error("没填 llm.api_key（config.json 或环境变量 LLM_API_KEY）")
            return None
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temp,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                r = await self.client.post(f"{self.base}/chat/completions", json=body, headers=headers)
                if r.status_code >= 400:
                    log.error("LLM %s: %s", r.status_code, r.text[:300])
                    if r.status_code in (401, 403):
                        return None
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                data = r.json()
                self.last_usage = data.get("usage") or {}
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception as exc:
                log.warning("LLM 请求出错（第 %d 次）：%s", attempt + 1, exc)
                await asyncio.sleep(1.5 * (attempt + 1))
        return None


LLM_CLIENT: LLM | None = None

# ---------------------------------------------------------------- 提示词

def build_messages(
    qq: str,
    name: str,
    ctx: str,
    text: str,
    group_recent: list[str] | None = None,
) -> list[dict[str, str]]:
    u = STORE.user(qq)
    limit = int(CFG["private"]["history_limit"] if ctx == "私聊" else CFG["group"]["history_limit"])
    hist = u["history"][-limit:]

    intimate = is_intimate(text or "", hist)
    system = persona.build_system_prompt(
        is_master=is_master(qq),
        nick=name,
        qq=qq,
        stage=int(u["stage"]),
        surface="group" if ctx == "群聊" else "private",
        lore=worldbook.compose_lore(f"{text} {name}"),
        notes=u["notes"],
        group_recent=group_recent,
        group_size=int(CFG["group"].get("group_size", 0)) if ctx == "群聊" else 0,
        bonds=STORE.meta.get("bonds") or {},
        intimate=intimate,
        mood=today_mood(),
    )

    msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
    msgs += hist

    # 成人向场景：在 user 侧再压一次长度（system 里的要求容易被角色设定盖掉）
    final_text = text if text else "（对你做了个动作）"
    if ctx == "私聊" and intimate:
        final_text += "\n（这一轮请写 150–250 字，分 3–5 条，以描写为主，不要只回一两句）"
    msgs.append({"role": "user", "content": final_text})
    return msgs

# ---------------------------------------------------------------- 文本处理

def clean_reply(raw: str, ctx: str, stage: int = 1, intimate: bool = False) -> tuple[str, int]:
    stage_delta = 0
    m = STAGE_RE.search(raw)
    if m:
        try:
            stage_delta = max(-1, min(1, int(m.group(1))))
        except Exception:
            stage_delta = 0
        raw = STAGE_RE.sub("", raw)

    if CFG["style"].get("strip_markdown", True):
        raw = MD_RE.sub("", raw)

    raw = raw.strip()
    adult = ctx != "群聊" and intimate
    if ctx == "群聊":
        cap = int(CFG["style"]["max_chars_group"])
    elif adult:
        cap = int(CFG["style"].get("max_chars_private_adult", 600))
    else:
        cap = int(CFG["style"]["max_chars_private"])
    if len(raw) > cap:
        cut = raw[:cap]
        idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("…"), cut.rfind("」"))
        raw = cut[: idx + 1] if idx > cap * 0.4 else cut

    # 最多 N 句（括号动作也算一句）。成人向场景不限句数，交给 split_replies 均分
    if adult:
        limit = 0
    else:
        limit = int(
            CFG["style"].get("max_sentences_group", 3)
            if ctx == "群聊"
            else CFG["style"].get("max_sentences_private", 3)
        )
    if limit > 0:
        parts = [p for p in SENT_RE.findall(raw) if p.strip()]
        if len(parts) > limit:
            raw = "".join(parts[:limit]).strip()
    return raw, stage_delta


def split_replies(text: str, ctx: str, stage: int = 1, intimate: bool = False) -> list[str]:
    """把一次回复切成最多 N 条消息。
    群里恒定 2 条；私聊阶段③起 5 条；成人向场景（私聊④⑤）每条可以长很多。"""
    adult = ctx != "群聊" and intimate
    if ctx == "群聊":
        limit = int(CFG["style"].get("max_messages_group", 2))
    else:
        key = "max_messages_private_high" if stage >= 3 else "max_messages_private"
        limit = int(CFG["style"].get(key, 5 if stage >= 3 else 3))
    per = int(
        CFG["style"].get(
            "max_chars_per_message_adult" if adult else "max_chars_per_message",
            160 if adult else 45,
        )
    )

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        parts = [p.strip() for p in SENT_RE.findall(text) if p.strip()]
        if adult and len(parts) > limit:
            # 成人向场景：不丢内容，均分合并成 limit 条
            n = -(-len(parts) // limit)  # ceil
            lines = ["".join(parts[i : i + n]) for i in range(0, len(parts), n)][:limit]
        else:
            # 日常：她本来就说不了那么多，超出的直接丢
            lines = parts[:limit]

    out: list[str] = []
    for ln in lines[:limit]:
        if len(ln) > per:
            cut = ln[:per]
            idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("…"), cut.rfind("」"))
            ln = cut[: idx + 1] if idx > per * 0.4 else cut
        if ln.strip():
            out.append(ln.strip())
    return out or ([text.strip()] if text.strip() else [])


def parse_message(segs: list[dict[str, Any]] | None, self_id: str) -> tuple[str, bool, list[str]]:
    """返回 (文本, 是否@了我, 被@的其他人 QQ 列表)"""
    text: list[str] = []
    at_me = False
    others: list[str] = []
    for s in segs or []:
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            text.append(d.get("text", ""))
        elif t == "at":
            q = str(d.get("qq"))
            if q == str(self_id):
                at_me = True
            elif q.isdigit():
                others.append(q)
        elif t == "image":
            text.append("[图片]")
        elif t == "face":
            text.append("[表情]")
    joined = re.sub(r"[ \t]+", " ", "".join(text)).strip()
    return joined, at_me, others


# ---------------------------------------------------------------- OneBot

class OneBot:
    def __init__(self) -> None:
        self.ws = None          # 最新连上来的那条（主动推送用）
        self.route = None       # 最近一次送消息过来的那条：回复优先走这条，保证是活的
        self.conns: set[Any] = set()
        self.last_seen: dict[Any, float] = {}   # 每条连接最后一次送消息过来的时间（判僵尸用）
        self.self_id = ""
        self.pending: dict[str, asyncio.Future] = {}   # echo → future（get_msg 这类要等回包的）

    def use(self, ws) -> None:  # noqa: ANN001
        self.conns.add(ws)
        self.last_seen[ws] = time.time()
        self.ws = ws

    def touch(self, ws) -> None:  # noqa: ANN001
        """收到任何东西就算这条连接还活着。"""
        self.last_seen[ws] = time.time()

    def drop(self, ws) -> None:  # noqa: ANN001
        self.conns.discard(ws)
        self.last_seen.pop(ws, None)
        if self.route is ws:
            self.route = None
        if self.ws is ws:
            self.ws = next(iter(self.conns), None)

    async def retire_stale(self, keep) -> None:  # noqa: ANN001
        """新连接进来时，把**早就没动静**的旧连接关掉。

        协议端（LuckyLillia）重连时，旧的 socket 经常在 TCP 层还半开着 ——
        `send()` 不报错，消息进了内核缓冲区就再也没有下文，表现就是
        「bot 说它回了、人却没收到」。所以要主动清理。

        **但不能无脑踢**：正常重连时旧连接可能几秒前还在发心跳，
        贸然关掉反而会把活的协议端断掉。所以只踢 `STALE_SECONDS` 秒内
        一条消息都没送过来的那种 —— 心跳通常是每分钟一次，阈值取 90 秒足够保守。
        """
        now = time.time()
        stale = [
            w for w in list(self.conns)
            if w is not keep and now - self.last_seen.get(w, 0) > STALE_SECONDS
        ]
        for w in stale:
            log.warning("关掉一条 %d 秒没动静的旧连接：%s",
                        int(now - self.last_seen.get(w, 0)), getattr(w, "remote_address", "?"))
            try:
                await asyncio.wait_for(w.close(), timeout=3)
            except Exception:
                pass
            finally:
                self.drop(w)

    async def call(self, action: str, params: dict[str, Any]) -> None:
        # 协议端重连时旧 socket 有时不会立刻断干净，会同时挂着两条。
        # 只发「最新」那条的话，万一发到死连接上就石沉大海——所以按 route 优先，失败再换下一条。
        targets: list[Any] = []
        for ws in (self.route, self.ws, *self.conns):
            if ws is not None and ws not in targets:
                targets.append(ws)
        if not targets:
            log.warning("还没有 QQ 连上来，丢弃：%s", action)
            return

        payload = json.dumps({"action": action, "params": params, "echo": action}, ensure_ascii=False)
        for ws in targets:
            try:
                await ws.send(payload)
                self.route = ws
                return
            except Exception as exc:
                log.warning("发送失败 %s（%s）：%s", action, getattr(ws, "remote_address", "?"), exc)
                self.drop(ws)
        log.warning("发送 %s 失败：所有连接都断了", action)

    async def api(self, action: str, params: dict[str, Any], timeout: float = 12.0) -> dict[str, Any] | None:
        """发一条要回包的 API（get_msg / get_image 之类），等 echo 对上再返回 data。"""
        targets: list[Any] = []
        for ws in (self.route, self.ws, *self.conns):
            if ws is not None and ws not in targets:
                targets.append(ws)
        if not targets:
            return None

        echo = f"{action}:{random.randint(100000, 999999)}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[echo] = fut
        payload = json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False)
        try:
            for ws in targets:
                try:
                    await ws.send(payload)
                    self.route = ws
                    break
                except Exception as exc:
                    log.warning("API %s 发送失败：%s", action, exc)
                    self.drop(ws)
            else:
                return None
            reply = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            log.warning("API %s 等回包超时", action)
            return None
        except Exception as exc:
            log.warning("API %s 失败：%s", action, exc)
            return None
        finally:
            self.pending.pop(echo, None)

        if not isinstance(reply, dict) or reply.get("retcode") not in (0, None):
            log.warning("API %s 返回异常：%s", action, reply)
            return None
        return reply.get("data") or {}

    async def send_text(self, message_type: str, target: int, text: str, at_qq: int | None = None) -> None:
        segs: list[dict[str, Any]] = []
        if at_qq:
            segs.append({"type": "at", "data": {"qq": str(at_qq)}})
            segs.append({"type": "text", "data": {"text": " "}})
        segs.append({"type": "text", "data": {"text": text}})
        if message_type == "group":
            await self.call("send_group_msg", {"group_id": target, "message": segs})
        else:
            await self.call("send_private_msg", {"user_id": target, "message": segs})

    async def send_images(self, message_type: str, target: int, images: list[bytes],
                          caption: str = "") -> str | None:
        """一条消息里放多张图（普通图文，不是合并转发）。返回 message_id（有的协议端不给）。"""
        segs: list[dict[str, Any]] = []
        if caption:
            segs.append({"type": "text", "data": {"text": caption}})
        for raw in images:
            segs.append({"type": "image", "data": {"file": "base64://" + base64.b64encode(raw).decode()}})
        if not segs:
            return None
        if message_type == "group":
            action, params = "send_group_msg", {"group_id": target, "message": segs}
        else:
            action, params = "send_private_msg", {"user_id": target, "message": segs}
        data = await self.api(action, params)
        if data is None:
            return None
        mid = data.get("message_id")
        return str(mid) if mid is not None else None

    async def send_file_image(self, message_type: str, target: int, path: Path,
                              caption: str = "") -> None:
        """发本地图片路径。大文件（gif 之类）走这条，不经 base64，不会撞 WS 上限。"""
        segs: list[dict[str, Any]] = []
        if caption:
            segs.append({"type": "text", "data": {"text": caption}})
        uri = path.resolve().as_uri()          # file:///C:/...
        segs.append({"type": "image", "data": {"file": uri}})
        if message_type == "group":
            await self.call("send_group_msg", {"group_id": target, "message": segs})
        else:
            await self.call("send_private_msg", {"user_id": target, "message": segs})

    async def send_image(self, message_type: str, target: int, png: bytes, caption: str = "") -> None:
        segs: list[dict[str, Any]] = []
        if caption:
            segs.append({"type": "text", "data": {"text": caption}})
        segs.append({"type": "image", "data": {"file": "base64://" + base64.b64encode(png).decode()}})
        if message_type == "group":
            await self.call("send_group_msg", {"group_id": target, "message": segs})
        else:
            await self.call("send_private_msg", {"user_id": target, "message": segs})


BOT = OneBot()

# ---------------------------------------------------------------- 业务

GROUP_LAST: dict[str, float] = defaultdict(float)
USER_LAST: dict[str, float] = defaultdict(float)          # "群:人" -> 上次回复时间
GROUP_BURST: dict[str, deque] = defaultdict(lambda: deque(maxlen=120))  # 每群最近一分钟的回复时刻
PRI_HOUR: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))     # 每人最近一小时的私聊时刻
PENDING: set[str] = set()                                               # 正在「打字」中的人，期间新消息走催促逻辑
NAGGED: set[str] = set()                                                # 本轮已经催过一次的，不再重复喊
QUEUED: dict[str, dict[str, Any]] = {}                                  # 打字期间进来的新消息：只留最新一条，发完再处理

NAG_LINES_GROUP = [
    "等、等一下啦，贝尔还在想。",
    "别催别催，贝尔在打字了。",
    "……那个，再等我一下下。",
    "呜，马上就好，真的。",
]
NAG_LINES_PRIVATE = [
    "等一下下……贝尔还在想。",
    "别催啦，贝尔在打字了。",
    "……呜，再给贝尔几秒钟。",
]


def reply_delay(master: bool) -> float:
    """收到消息后要「打字」多久才发出去。主人 10 秒，群友 60 秒。"""
    d = CFG.get("reply_delay", {})
    return float(d.get("master" if master else "user", 10 if master else 60))


async def think_and_delay(target: float, started: float, key: str) -> None:
    """把总延迟凑到 target 秒——模型快就多等一会儿，慢就少等。"""
    elapsed = time.time() - started
    left = target - elapsed
    if left > 0:
        await asyncio.sleep(left)
LLM_SLOTS = asyncio.Semaphore(int(CFG.get("concurrency", 3)))


def _commit(qq: str, text: str, reply: str, delta: int) -> dict[str, Any]:
    u = STORE.user(qq)
    u["stage"] = max(1, min(5, int(u["stage"]) + delta))
    u["history"].append({"role": "user", "content": text})
    u["history"].append({"role": "assistant", "content": reply})
    u["history"] = u["history"][-40:]
    u["msg_count"] = int(u.get("msg_count", 0)) + 1
    return u


async def handle_group(evt: dict[str, Any]) -> None:
    self_id = str(evt.get("self_id", ""))
    gid = str(evt.get("group_id", ""))
    sender = evt.get("sender") or {}
    qq = str(evt.get("user_id", ""))
    name = sender.get("card") or sender.get("nickname") or qq
    text, at_me, at_others = parse_message(evt.get("message"), self_id)

    if not text or qq == self_id:
        return

    STORE.groups[gid].append(f"{name}：{text}"[:120])
    STORE.touch_member(gid, qq)

    # 谁发一个「凿」就甩举牌表情包：不 @ 也算，不吃冷却和额度
    if await handle_zan(qq, text, "group", int(gid)):
        return

    # 连续 3 条一样的就复读（不 @ 也算）
    if await handle_repeat(qq, text, gid, evt.get("message")):
        return

    # 「操」点赞：不 @ 也算，必须引用一条 /随机西八 的消息
    if await handle_xiba_like(qq, text, "group", int(gid), evt.get("message")):
        return

    g = CFG["group"]
    at_only = bool(g.get("at_only", False))

    # 西八四连 + 今日运势不 @ 也算（群里随手刷图/看签），斜杠可有可无
    free_cmd = is_free_cmd(text)

    # 猜曲答题：不 @ 也算（否则别人永远答不对），答对只恭喜，不接着聊
    quiz_res = check_quiz_answer(qq, text, "group", int(gid))
    if quiz_res:
        await _quiz_announce("group", int(gid), quiz_res, at_qq=int(qq))
        return
    # 题还没答完：这条不管 @ 没 @，都不进聊天（猜歌就只是猜歌）
    if quiz_pending("group", int(gid), qq):
        return

    # at_only=true 时只认 @，连关键词都不理（70 人群里必须这样，不然「贝尔」两个字会让她被刷爆）
    # 例外：西八四连 + 今日运势不 @ 也放行
    triggered = at_me or free_cmd
    if not triggered and not at_only:
        hard = ["贝尔", "贝尔娜提塔", "bernie", "柏妮"]
        hit_keyword = any(k.lower() in text.lower() for k in (g.get("trigger_keywords") or []) + hard)
        triggered = hit_keyword
    if not triggered and not at_only:
        triggered = random.random() < float(g.get("random_reply_prob", 0))
    if not triggered:
        return

    now = time.time()
    master = is_master(qq)

    # 每人冷却（按 群+人 算，不是按群算——否则 A 说完 B 的 @ 就被吞了）
    # 主人也有冷却，只是短一点（master_cooldown_seconds）
    ukey = f"{gid}:{qq}"
    cd = float(g.get("master_cooldown_seconds", 10)) if master else float(g.get("user_cooldown_seconds", 60))
    # 免 @ 指令不吃按人冷却，不然连发第二下就没反应；整群每分钟上限还在
    if not free_cmd and now - USER_LAST[ukey] < cd:
        return

    # 整群限速：每分钟最多 N 条，防止 220 个人轮流 @
    burst = GROUP_BURST[gid]
    while burst and now - burst[0] > 60:
        burst.popleft()
    if not master:
        if len(burst) >= int(g.get("max_per_minute", 12)):
            log.info("[群 %s] 触发每分钟上限，跳过", gid)
            return
        burst.append(now)
    USER_LAST[ukey] = now

    # 这个人的今日额度用完 → 回一句就闭嘴，不再调模型（主人不限）
    # 免 @ 指令不花 token，额度用光也照发
    if not free_cmd and budget_exhausted(qq, master):
        if now - BLOCK_NOTICE[ukey] >= 60:
            BLOCK_NOTICE[ukey] = now
            await BOT.send_text("group", int(gid), BUDGET.get("exhausted_reply", "明天再来吧"), at_qq=int(qq))
            log.info("[群 %s] %s 今日额度已用完（%.2f 元），回绝", gid, name, budget_spent(qq))
        return

    STORE.user(qq)["name"] = display_name(qq, name)

    if HELP_RE.match(text.strip()):
        await send_help(qq, "group", int(gid))
        return

    if SECRET_RE.match(text.strip()) and not is_master(qq):
        await BOT.send_text("group", int(gid), random.choice(SECRET_LINES), at_qq=int(qq))
        return

    if await do_quiz(qq, text, "group", int(gid)):
        return

    if await do_random(qq, text, "group", int(gid)):
        return

    if await do_cost(qq, text, "group", int(gid)):
        return

    if await do_new(qq, text, "group", int(gid)):
        return

    if await do_song(qq, text, "group", int(gid)):
        return

    if await do_fortune(qq, text, "group", int(gid)):
        return

    if await do_fortune_reroll(qq, text, "group", int(gid)):
        return

    if await do_gacha(qq, text, "group", int(gid), name):
        return

    if await do_bag(qq, text, "group", int(gid), at_others):
        return

    if await do_pool(qq, text, "group", int(gid), at_others):
        return

    if await do_luck_rank(qq, text, "group", int(gid)):
        return

    if await do_members_md(qq, text, "group", int(gid)):
        return

    if await do_random_song(qq, text, "group", int(gid)):
        return

    if await do_ds_table(qq, text, "group", int(gid)):
        return

    if await do_menu5c(qq, text, "group", int(gid)):
        return

    if await do_random_xiba(qq, text, "group", int(gid)):
        return

    if await do_random_pjsk(qq, text, "group", int(gid)):
        return

    if await do_xiba_rank(qq, text, "group", int(gid)):
        return

    if await do_xiba_like_rank(qq, text, "group", int(gid)):
        return

    if await do_add_xiba(qq, text, "group", int(gid), evt.get("message")):
        return

    if await do_b50(qq, text, "group", int(gid), at_others):
        return

    if await do_lxns(qq, text, "group", int(gid)):
        return

    if await do_wmc_b50(qq, text, "group", int(gid)):
        return

    if await do_bind_fish(qq, text, "group", int(gid)):
        return

    if await do_refresh_b50(qq, text, "group", int(gid)):
        return

    # 群里默认不聊天，只跑指令（要开就 group.chat_enabled = true）
    if not bool(g.get("chat_enabled", False)):
        return

    # 正在「打字」中：这一条先存着，等手上这条整个发完再处理（不并发、也不丢）
    pkey = f"g:{gid}:{qq}"
    if pkey in PENDING:
        QUEUED[pkey] = evt
        if pkey not in NAGGED:
            NAGGED.add(pkey)
            await BOT.send_text("group", int(gid), random.choice(NAG_LINES_GROUP))
        return
    PENDING.add(pkey)
    NAGGED.discard(pkey)
    started = time.time()
    try:
        win = int(g.get("context_window", 20))
        recent = [x for x in list(STORE.groups[gid])[:-1]][-win:]
        messages = build_messages(qq, name, "群聊", text, recent)
        async with LLM_SLOTS:
            reply = await LLM_CLIENT.chat(messages)  # type: ignore[union-attr]
            add_usage(qq, getattr(LLM_CLIENT, "last_usage", None), master)
        if not reply:
            return
        reply, delta = clean_reply(reply, "群聊", int(STORE.user(qq)["stage"]), is_intimate(text, STORE.user(qq)["history"]))
        if not reply:
            return

        # 停一会儿再发——群友问一句，她要憋一分钟
        await think_and_delay(reply_delay(master), started, pkey)

        u = await _commit_and_maybe_summarize(qq, text, reply, delta, master)
        parts = split_replies(reply, "群聊", int(u["stage"]), is_intimate(text, u["history"]))
        if not parts:
            return
        at_target = int(qq) if (g.get("reply_with_at") in (True, "always")) else None
        for i, piece in enumerate(parts):
            await BOT.send_text("group", int(gid), piece, at_qq=at_target if i == 0 else None)
            if i < len(parts) - 1:
                await asyncio.sleep(
                    random.uniform(
                        float(CFG["style"].get("send_delay_min", 0.6)),
                        float(CFG["style"].get("send_delay_max", 1.5)),
                    )
                )
        await STORE.save()
        log.info("[群 %s] %s(阶段%d Δ%+d)：%s", gid, name, u["stage"], delta, " / ".join(parts)[:80])
    finally:
        PENDING.discard(pkey)
        NAGGED.discard(pkey)


VISION_CFG: dict[str, Any] = {}          # main() 里从 CFG 取一次


async def describe_incoming_image(segs: list[dict[str, Any]] | None) -> str | None:
    """私聊来图：交给 vision 模型写一句中文描述。

    没配 vision / 取不到图 / 请求失败都返回 None，调用方保持原来的「[图片]」就行。
    """
    if vision_mod is None or not vision_mod.enabled(VISION_CFG):
        return None
    for s in segs or []:
        if s.get("type") != "image":
            continue
        d = s.get("data") or {}
        src = str(d.get("url") or d.get("file") or "")
        if not src:
            continue
        raw = await _load_image(src)
        if not raw:
            continue
        desc = await vision_mod.describe(raw, VISION_CFG)
        if desc:
            log.info("[识图] %.0f KB → %s", len(raw) / 1024, desc[:50])
            return desc
    return None


async def handle_private(evt: dict[str, Any]) -> None:
    self_id = str(evt.get("self_id", ""))
    qq = str(evt.get("user_id", ""))
    name = (evt.get("sender") or {}).get("nickname") or qq
    text, _, _ = parse_message(evt.get("message"), self_id)
    if qq == self_id:
        return

    # 「操」点赞：私聊引用同样有效，放在冷却之前，别被吞
    if await handle_zan(qq, text, "private", int(qq)):
        return

    if await handle_xiba_like(qq, text, "private", int(qq), evt.get("message")):
        return

    u = STORE.user(qq)
    now = time.time()
    master = is_master(qq)

    # 主人也走冷却，只是短一点；其他人照旧
    cd_sec = float(
        CFG["private"].get("master_cooldown_seconds", 10)
        if master
        else CFG["private"].get("cooldown_seconds", 3)
    )
    if now - u.get("last_reply", 0) < cd_sec:
        return
    if not master:
        burst = PRI_HOUR[qq]
        while burst and now - burst[0] > 3600:
            burst.popleft()
        if len(burst) >= int(CFG["private"].get("max_per_hour", 80)):
            log.info("[私聊] %s 触发每小时上限，跳过", name)
            return
        burst.append(now)

    u["last_reply"] = now
    u["name"] = display_name(qq, name)

    # help / b50 放在 admin 之前——否则 / 开头的会被管理命令吃掉
    if HELP_RE.match(text.strip()):
        await send_help(qq, "private", int(qq))
        return

    # /stage 只给主人；别人问了就说"少女的秘密"
    if SECRET_RE.match(text.strip()) and not is_master(qq):
        await BOT.send_text("private", int(qq), random.choice(SECRET_LINES))
        return

    quiz_res = check_quiz_answer(qq, text, "private", int(qq))
    if quiz_res:
        await _quiz_announce("private", int(qq), quiz_res)
        return

    if await do_quiz(qq, text, "private", int(qq)):
        return

    if await do_random(qq, text, "private", int(qq)):
        return

    if await do_cost(qq, text, "private", int(qq)):
        return

    if await do_new(qq, text, "private", int(qq)):
        return

    if await do_song(qq, text, "private", int(qq)):
        return

    if await do_fortune(qq, text, "private", int(qq)):
        return

    if await do_fortune_reroll(qq, text, "private", int(qq)):
        return

    if await do_gacha(qq, text, "private", int(qq), name):
        return

    if await do_bag(qq, text, "private", int(qq)):
        return

    if await do_pool(qq, text, "private", int(qq)):
        return

    if await do_luck_rank(qq, text, "private", int(qq)):
        return

    if await do_members_md(qq, text, "private", int(qq)):
        return

    if await do_random_song(qq, text, "private", int(qq)):
        return

    if await do_ds_table(qq, text, "private", int(qq)):
        return

    if await do_menu5c(qq, text, "private", int(qq)):
        return

    if await do_random_xiba(qq, text, "private", int(qq)):
        return

    if await do_random_pjsk(qq, text, "private", int(qq)):
        return

    if await do_xiba_rank(qq, text, "private", int(qq)):
        return

    if await do_xiba_like_rank(qq, text, "private", int(qq)):
        return

    if await do_add_xiba(qq, text, "private", int(qq), evt.get("message")):
        return

    if await do_b50(qq, text, "private", int(qq)):
        return

    if await do_lxns(qq, text, "private", int(qq)):
        return

    if await do_wmc_b50(qq, text, "private", int(qq)):
        return

    if await do_bind_fish(qq, text, "private", int(qq)):
        return

    if await do_refresh_b50(qq, text, "private", int(qq)):
        return

    # 主人专属管理命令
    if is_master(qq) and text.startswith("/"):
        await handle_admin(qq, text)
        return

    if budget_exhausted(qq, master):
        await BOT.send_text("private", int(qq), BUDGET.get("exhausted_reply", "明天再来吧"))
        log.info("[私聊] %s 今日额度已用完（%.2f 元），回绝", name, budget_spent(qq))
        return

    pkey = f"p:{qq}"
    if pkey in PENDING:
        if pkey not in NAGGED:
            NAGGED.add(pkey)
            await BOT.send_text("private", int(qq), random.choice(NAG_LINES_PRIVATE))
        return
    PENDING.add(pkey)
    NAGGED.discard(pkey)
    started = time.time()
    try:
        if "[图片]" in text:
            hint = await describe_incoming_image(evt.get("message"))
            if hint:
                text = text.replace("[图片]", f"[图片：{hint}]", 1)
        messages = build_messages(qq, name, "私聊", text or "（没说话）", None)
        async with LLM_SLOTS:
            reply = await LLM_CLIENT.chat(messages)  # type: ignore[union-attr]
            add_usage(qq, getattr(LLM_CLIENT, "last_usage", None), master)
        if not reply:
            return
        reply, delta = clean_reply(reply, "私聊", int(u["stage"]), is_intimate(text, u["history"]))
        if not reply:
            return

        # 停一会儿再发——主人问一句，她要憋十秒
        await think_and_delay(reply_delay(master), started, pkey)

        u = await _commit_and_maybe_summarize(qq, text or "（没说话）", reply, delta, master)
        parts = split_replies(reply, "私聊", int(u["stage"]), is_intimate(text, u["history"]))
        for i, piece in enumerate(parts):
            await BOT.send_text("private", int(qq), piece)
            if i < len(parts) - 1:
                await asyncio.sleep(
                    random.uniform(
                        float(CFG["style"].get("send_delay_min", 0.6)),
                        float(CFG["style"].get("send_delay_max", 1.5)),
                    )
                )
        await STORE.save()
        log.info("[私聊] %s(阶段%d Δ%+d)：%s", name, u["stage"], delta, " / ".join(parts)[:80])
    finally:
        PENDING.discard(pkey)
        NAGGED.discard(pkey)


# ---------------------------------------------------------------- 每日状态

MOOD_POOL = [
    ("困", "刚睡醒，反应慢半拍。句子更短，说着说着会打哈欠，常冒「唔…」「嗯…」「再让贝尔躺五分钟」。"),
    ("饿", "一直在想吃的。提到食物会突然话多、追问细节；别的话题容易走神，会岔到「这个能不能吃」。"),
    ("亢奋", "心情莫名很好。话比平常多，语气上扬，偶尔哼歌，被夸会原地飘。"),
    ("低落", "情绪有点低。句子更碎，容易道歉说「对不起」，不太主动开话题，但被关心时会很小声地靠过去。"),
    ("认真", "今天格外正经。说话完整、条理清楚，不太开玩笑，被带偏会认真把人拉回来。"),
    ("懒", "什么都不想动。回复尽量短，能一个字解决绝不写两个字，被要求做事会拖。"),
    ("紧张", "心里装着事。说话绕，容易走神，被追问会慌，但问到最后会小声承认。"),
]


def today_mood() -> str:
    """按日期当种子，同一天内稳定。返回一句状态描述。"""
    seed = int(time.strftime("%Y%m%d"))
    name, desc = random.Random(seed).choice(MOOD_POOL)
    return f"今天是「{name}」。{desc}"


# ---------------------------------------------------------------- 猜曲

QUIZ: dict[str, dict[str, Any]] = {}   # key -> 当前题目状态
QUIZ_RECENT: "deque[int]" = deque(maxlen=50)   # 最近出过的 50 首，不再重复

ALIAS_LIST = Path(__file__).with_name("data") / "alias_list.json"
_ALIAS_MAP: dict[int, list[str]] | None = None

# 猜曲节奏：每 15 秒放一条提示，最后一条是模糊图
QUIZ_STEP_SEC = 15

GENRE_CN = {
    "maimai": "maimai 原创",
    "POPSアニメ": "POPS & ANIME（流行 / 动画）",
    "ゲームバラエティ": "GAME & VARIETY（游戏 / 综艺）",
    "niconicoボーカロイド": "niconico & VOCALOID",
    "東方Project": "東方Project",
    "オンゲキCHUNITHM": "ONGEKI & CHUNITHM（音击 / 中二节奏）",
    "宴会場": "宴会場",
}

QUIZ_WIN_LINES = [
    "答、答对了……「{t}」。（抱着熊在屏幕前小声欢呼）",
    "唔……「{t}」。……贝尔还以为没人知道呢。",
    "对、对了。「{t}」。（偷偷记在小本子上）这个人……有点厉害。",
    "「{t}」。……嗯，答对了。（把熊举起来挡住脸）才、才没有高兴。",
    "是「{t}」。……（声音很小）真厉害。",
    "「{t}」……答对了。（别扭地转开脸）不、不过贝尔本来也知道的啦。",
    "答对啦。「{t}」。（尾巴尖轻轻晃了一下，又立刻收住）",
]


def _alias_map() -> dict[int, list[str]]:
    """song_id → 别名列表，来自 /api/v0/maimai/alias/list。"""
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    m: dict[int, list[str]] = {}
    try:
        raw = json.loads(ALIAS_LIST.read_text(encoding="utf-8"))
        for it in raw.get("aliases") or []:
            sid = it.get("song_id")
            names = it.get("aliases") or []
            if isinstance(sid, int) and names:
                m[sid] = [str(x) for x in names if str(x).strip()]
    except Exception:
        pass
    _ALIAS_MAP = m
    return m


def _quiz_key(message_type: str, target: int, qq: str) -> str:
    return f"g:{target}" if message_type == "group" else f"p:{qq}"


def _norm_title(t: str) -> str:
    """答案比对用：去掉空格、括号、大小写、常见标点。"""
    t = (t or "").lower()
    return re.sub(r"[《》「」『』（）()\[\]【】<>·・.。,，!！?？'\"\s_\-—~]", "", t)


# ---------------------------------------------------------------- 舞萌 B50

# ---------------------------------------------------------------- 记忆压缩

MCFG = CFG.get("memory", {})

SUMMARY_PROMPT = (
    "把下面这段对话压缩成要点，给以后接着聊用。只记这几类：\n"
    "1. 对方说过关于自己的事（喜好、经历、身边的人、约定）\n"
    "2. 关系走到了哪一步、发生过什么有分量的事\n"
    "3. 她答应过或拒绝过的事\n"
    "不要复述原话，不要评价，第三人称，每条一行，最多 8 条。"
)


def _history_to_text(hist: list[dict[str, str]]) -> str:
    rows = []
    for m in hist:
        who = "对方" if m.get("role") == "user" else "贝尔"
        rows.append(f"{who}：{(m.get('content') or '').replace(chr(10), ' ')}")
    return "\n".join(rows)


async def maybe_summarize(qq: str, master: bool) -> None:
    """攒够条数就把这一段压成摘要，然后从 history 里删掉。"""
    u = STORE.user(qq)
    threshold = int(
        MCFG.get(
            "summarize_master_every" if master else "summarize_user_every",
            80 if master else 30,
        )
    )
    if threshold <= 0 or len(u["history"]) < threshold:
        return

    chunk = u["history"][:threshold]
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": _history_to_text(chunk)},
    ]
    async with LLM_SLOTS:
        out = await LLM_CLIENT.chat(messages)  # type: ignore[union-attr]
        add_usage(qq, getattr(LLM_CLIENT, "last_usage", None), master)
    if not out:
        log.warning("总结失败，先留着原文（%s）", qq)
        return

    keep = int(MCFG.get("keep_after_summary", 2))
    u.setdefault("summaries", []).append(
        {"date": time.strftime("%Y-%m-%d %H:%M"), "text": out.strip()}
    )
    u["summaries"] = u["summaries"][-int(MCFG.get("keep_summaries", 12)) :]
    u["history"] = u["history"][threshold:]
    log.info("[记忆] %s 压缩了 %d 条 → 摘要 %d 条，剩 %d 条", qq, len(chunk), len(u["summaries"]), len(u["history"]))
    await STORE.save()


async def _commit_and_maybe_summarize(qq: str, text: str, reply: str, delta: int, master: bool) -> dict[str, Any]:
    u = _commit(qq, text, reply, delta)
    try:
        await maybe_summarize(qq, master)
    except Exception as exc:
        log.warning("总结流程出错：%s", exc)
    return u

# 判断这一轮是不是真的在做：只看强信号词，避免「抱一下」也误判
INTIMATE_PAT = re.compile(
    r"做爱|插入|插进|抽送|射精|射了|内射|高潮|舔|吸吮|穴|乳|裸|脱光|呻吟|湿了|硬了|情事|摸我|亲我|要我|身体里|里面|进去"
)


def is_intimate(text: str, hist: list[dict[str, str]], look_back: int = 4) -> bool:
    """这一轮是不是性爱场景：看当前这句 + 最近几轮。"""
    if text and INTIMATE_PAT.search(text):
        return True
    for m in hist[-look_back:]:
        if INTIMATE_PAT.search(str(m.get("content") or "")):
            return True
    return False


B50_RE = re.compile(r"^/b50\s*(.*)$", re.I)
LXNS_RE = re.compile(r"^/?(?:lxns|落雪)\s*(\d*)\s*$", re.I)
# 国服 WMC ID（好友码）。落雪要 15 位，13 位是国际版，先收下再交给落雪判
WMC_RE = re.compile(r"^/?(?:wmcb50|sgwmc|wmc)\s*(\d{13,15})\s*$", re.I)
BIND_FISH_RE = re.compile(r"^/?绑定水鱼\s*$", re.I)
REFRESH_B50_RE = re.compile(r"^/?更新b50\s*$", re.I)
FORTUNE_RE = re.compile(r"^[/!！]?\s*(?:今日运势|运势|fortune)\s*$", re.I)
FORTUNE_REROLL_RE = re.compile(r"^/?重新抽签\s*$", re.I)   # 每人每天一次，斜杠可有可无
GACHA_RE = re.compile(r"^/?抽卡\s*$", re.I)   # 曲绘抽卡，每人每天 3 次
BAG_RE = re.compile(r"^/?卡包\s*(\d+)?\s*$", re.I)    # 自己的收藏
POOL_RE = re.compile(r"^/?卡池\s*([A-Za-z]+)?\s*(\d+)?\s*$", re.I)   # 卡池 [等级] [页数]
LUCK_RE = re.compile(r"^/?([欧非]榜|欧非榜)\s*$", re.I)   # 欧榜 / 非榜（欧非榜 = 欧榜）
ZAN_RE = re.compile(r"凿")                        # 话里带「凿」就出表情包

# 复读：同一个群里连续 3 条一模一样的话，贝尔跟着复读一次
REPEAT: dict[str, deque] = defaultdict(lambda: deque(maxlen=8))
REPEAT_N = 3                                     # 连续几条算复读
_REPEAT_LAST: dict[str, float] = {}              # 群 -> 上次复读时间（防连环）
REPEAT_GAP = 20.0                                # 同群 20 秒内只复读一次
ZAN_FILE = BASE_DIR / "assets" / "凿.gif"
_ZAN_LAST: dict[str, float] = {}                 # 每人 3 秒内只发一次，防刷屏
MEMBERS_MD_RE = re.compile(r"^/?群名单\s*$", re.I)   # 手写/刷新 群成员.md
RANDOM_SONG_RE = re.compile(r"^/随一首\s*(\S*)\s*$", re.I)
DS_TABLE_RE = re.compile(r"^/定数表\s*(\S*)(?:\s+(\d+))?\s*$", re.I)
MENU5C_RE = re.compile(r"^/5c\s*吃(?:什么|啥)\s*(.*)$", re.I)   # /5C吃什么 [collins|cmc|明天]
X8_RE = re.compile(r"^/?随机西八\s*(\d+)?\s*$", re.I)   # 随机西八 / /随机西八 [1-3]
ADD_X8_RE = re.compile(r"^/?添加西八\s*$", re.I)   # 添加西八（引用带图的消息）
X8_RANK_RE = re.compile(r"^/?西八榜\s*(\d+)?\s*$", re.I)   # 西八榜 [N]
X8_LIKE_RANK_RE = re.compile(r"^/?西八赞榜\s*(\d+)?\s*$", re.I)   # 西八赞榜 [N]
PJSK_RE = re.compile(r"^/?随机烧烤\s*(\d+)?\s*$", re.I)   # 随机烧烤 [1-3]（pjsk 漫画）
# 免 @ 的一组：西八四连 + 随机烧烤 + 今日运势 + 重新抽签。
# 群里直接发就触发，斜杠可有可无（老的带斜杠写法照样认）
FREE_CMDS = (X8_RE, ADD_X8_RE, X8_RANK_RE, X8_LIKE_RANK_RE, PJSK_RE, FORTUNE_RE,
             FORTUNE_REROLL_RE, GACHA_RE, BAG_RE, POOL_RE, LUCK_RE, ZAN_RE)


def is_free_cmd(text: str) -> bool:
    """这条消息是不是免 @ 指令（西八四连 / 烧烤 / 运势 / 重抽 / 抽卡 / 卡包 / 卡池 / 欧榜 / 非榜）。"""
    t = (text or "").strip()
    return any(r.match(t) for r in FREE_CMDS)
X8_LIKE_RE = re.compile(r"^操\s*([\d\s]*)$")                     # 「操」/「操2」/「操 1 3」
HELP_RE = re.compile(r"^[/!！]?\s*(help|帮助|菜单)\s*$", re.I)
SONG_RE = re.compile(r"^/song\s+(.+)$", re.I)
NEW_RE = re.compile(r"^/new(?:\s+(\d+))?$", re.I)
COST_RE = re.compile(r"^/(?:cost|usage|用量|花费|余额)\s*(all|全部)?\s*$", re.I)
RANDOM_RE = re.compile(r"^/random\s*(\S*)\s*$", re.I)
QUIZ_RE = re.compile(r"^/quiz\s*$", re.I)
SECRET_RE = re.compile(r"^[/!！]?\s*stage\b", re.I)

# 别人问 /stage 时她这么说——不让看就是不让看
SECRET_LINES = [
    "少女的秘密，才不会让你知道呢。",
    "诶、那种事不能问的啦。",
    "……这个是秘密。贝尔不告诉你。",
    "才不说。反正是贝尔自己的事。",
    "你、你问这个干什么呀。不给看。",
]

# /5C吃什么 是主人专属——别人问就这么回
MENU5C_DENY_LINES = [
    "这个是主人专用的……你想知道就去问主人吧。",
    "唔，食堂情报只给主人看，贝尔不给你。",
    "诶、这个不能让给你啦。主人先看。",
]

HELP_GROUP = (
    "贝尔会做的事（群里先 @ 我，指令带斜杠；西八和运势不用 @）：\n"
    "· 查分 —— /b50（水鱼，注册后直接发，不用用户名）\n"
    "· 落雪 —— /lxns [好友码或QQ]（跟水鱼分开的一条路）\n"
    "· 好友码 —— /wmcb50 15位国服WMC ID，直接按码查 B50\n"
    "· 水鱼 —— 私聊「绑定水鱼」授权，之后「更新b50」查自己的（隐私号也行）\n"
    "· 曲库 —— /song 关键词　/new　/random 13+　/quiz\n"
    "· 定数 —— /随一首 13　/定数表 13\n"
    "· 抽签 —— 今日运势（不用 @、斜杠也不用；每人每天一份，零点换）\n"
    "· 重抽 —— 重新抽签（每人每天一次，第二次就明天再来）\n"
    "· 抽卡 —— 抽卡：一次两张、每天两张，maimai + Phigros 曲绘\n"
    "· 收藏 —— 卡包 [@人] [页]（自己的卡）　卡池 [等级] [@人] [页]（全池，抽到的亮）\n"
    "· 欧非 —— 欧榜 / 非榜（本群；私聊看全服）\n"
    "· 西八 —— 随机西八 1-3　西八榜　西八赞榜（不用 @，也不用斜杠）\n"
    "· 烧烤 —— 随机烧烤 1-3（pjsk 漫画，不用 @）\n"
    "· 加图 —— 引用图片发 添加西八\n"
    "· 点赞 —— 引用西八图回「操」，回「操操操。！」就是成功\n"
    "· 聊天 —— 群里不聊，私聊我才会说话\n"
    "· /help —— 就这个"
)

HELP_PRIVATE = (
    "贝尔会做的事（指令大多带斜杠，西八和运势不用）：\n"
    "· 聊天 —— 直接说话就行\n"
    "· 查分 —— /b50，把 B50 画成图（水鱼注册后直接发，不用用户名）\n"
    "· 落雪 —— /lxns 查自己；/lxns 好友码 或 /lxns QQ 查别人\n"
    "· 好友码 —— /wmcb50 15位国服WMC ID，直接按码出 B50 图\n"
    "· 水鱼 —— 绑定水鱼：授权一次，之后「更新b50」查自己的（隐私号也能查）\n"
    "· 曲库 —— /song 关键词　/new　/random 13+　/quiz\n"
    "· 定数 —— /随一首 13　/定数表 13\n"
    "· 运势 —— 今日运势：分数 + 洋快餐 + 今日曲（斜杠可以不打）\n"
    "· 重抽 —— 重新抽签：每人每天一次，第二次就明天再来\n"
    "· 抽卡 —— 抽卡：一次两张、每天两张\n"
    "· 收藏 —— 卡包 [@人] [页]　卡池 [等级] [@人] [页]　欧榜 / 非榜（本群）\n"
    "· 西八 —— 随机西八 1-3　西八榜　西八赞榜（没有斜杠也认）\n"
    "· 烧烤 —— 随机烧烤 1-3（pjsk 简体中文漫画）\n"
    "· 加图 —— 引用图片发 添加西八\n"
    "· 点赞 —— 引用西八图回「操」（多图用 操2、操 1 3）\n"
    "· /help —— 就这个\n"
    "（只打 b50 没斜杠，贝尔会当你在聊天）"
)

HELP_MASTER_EXTRA = (
    "\n\n主人专用：\n"
    "· /cost —— 看今天花了多少\n"
    "· 群名单 —— 把记到的群成员写进 群成员.md\n"
    "· /5C吃什么 —— 六个食堂今天吃什么（加 明天 / collins / cmc）\n"
    "· /set QQ 1-5　/note 内容　/reset　/reload —— 管好感、记事、重载角色卡"
)

async def send_help(qq: str, message_type: str, target: int) -> None:
    if message_type == "group":
        body = HELP_GROUP
    else:
        body = HELP_PRIVATE + (HELP_MASTER_EXTRA if is_master(qq) else "")
    await BOT.send_text(message_type, target, body, at_qq=int(qq) if message_type == "group" else None)
    log.info("[help] %s %s", message_type, qq)


async def do_random(qq: str, text: str, message_type: str, target: int) -> bool:
    """/random [定数] -- 随机抽一首歌。"""
    m = RANDOM_RE.match(text.strip())
    if not m or songdb is None:
        return False

    at = int(qq) if message_type == "group" else None
    db = songdb.DB
    if not db.all:
        await db.refresh()
    if not db.count():
        await BOT.send_text(message_type, target, "曲库还没加载好，过一会儿再试。", at_qq=at)
        return True

    want = (m.group(1) or "").strip()
    pool: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for s0 in db.all:
        for d in db.difficulties(s0["id"]):
            if d["index"] < 2:          # 只从 EXPERT 以上抽
                continue
            if want and d["level"] != want:
                continue
            pool.append((s0, d))

    if not pool:
        await BOT.send_text(
            message_type, target,
            f"没有定数是「{want}」的谱面。" if want else "抽不出来，稍后再试。",
            at_qq=at,
        )
        return True

    s0, d = random.choice(pool)
    lv = songdb.DIFF_CN.get(d["index"], "?")
    ver = songdb.VERSION_NAME.get(int(s0.get("version") or 0), "")
    lines = [f"抽到了：{s0.get('title')}"]
    lines.append(f"   {lv} {d['level']}" + (f" · {ver}" if ver else ""))
    lines.append(f"   ID {s0.get('id')}")
    await BOT.send_text(message_type, target, "\n".join(lines), at_qq=at)
    return True


def _quiz_title_of(q: dict[str, Any]) -> str:
    return str(q.get("title") or "")


def _quiz_cancel(key: str) -> None:
    q = QUIZ.pop(key, None)
    if q:
        task = q.get("task")
        if task is not None and not task.done():
            task.cancel()


async def _quiz_ticker(key: str, message_type: str, target: int, at_qq: int | None) -> None:
    """开局 15 秒后逐条放提示，最后一条是模糊曲绘。"""
    hints: list[str] = QUIZ.get(key, {}).get("hints") or []
    total_steps = len(hints) + 1        # 4 条文字 + 1 张图 = 5

    # 第 1 条在开局时已经发过了，从第 2 条开始
    for step in range(2, total_steps + 1):
        try:
            await asyncio.sleep(QUIZ_STEP_SEC)
        except asyncio.CancelledError:
            return
        q = QUIZ.get(key)
        if not q:
            return
        q["stage"] = step

        if step <= len(hints):
            left = (total_steps - step) * QUIZ_STEP_SEC
            try:
                await BOT.send_text(
                    message_type, target,
                    f"提示 {step}/{total_steps}　{hints[step - 1]}\n还有 {left} 秒放完",
                    at_qq=at_qq,
                )
            except Exception:
                return
            continue

        # 最后一步：时间到，公布答案 + 完整曲绘
        try:
            covers = await maimai_mod.fetch_covers([q["id"]])
            # level=0 → 不裁剪、不模糊，就是完整曲绘
            img = maimai_mod.make_quiz_image(covers, q["id"], level=0)
            reveal = q.get("reveal") or ""
            if img:
                await BOT.send_image(
                    message_type, target, img,
                    caption=f"时间到。答案是「{_quiz_title_of(q)}」\n{reveal}",
                )
            else:
                await BOT.send_text(
                    message_type, target,
                    f"时间到。答案是「{_quiz_title_of(q)}」\n{reveal}",
                )
        except Exception:
            pass
        QUIZ.pop(key, None)
        return


async def _quiz_announce(
    message_type: str,
    target: int,
    res: tuple[str, dict[str, Any]],
    at_qq: int | None = None,
) -> None:
    """答对之后：先恭喜，再完整公布（曲绘 + 全部信息）。"""
    line, q = res
    await BOT.send_text(message_type, target, line, at_qq=at_qq)
    reveal = q.get("reveal") or ""
    title = _quiz_title_of(q)
    try:
        covers = await maimai_mod.fetch_covers([q["id"]])
        img = maimai_mod.make_quiz_image(covers, q["id"], level=0)
        if img:
            await BOT.send_image(
                message_type, target, img,
                caption=f"「{title}」\n{reveal}",
            )
            return
    except Exception:
        pass
    await BOT.send_text(message_type, target, f"「{title}」\n{reveal}")


async def do_quiz(qq: str, text: str, message_type: str, target: int) -> bool:
    """/quiz -- 阶梯提示猜曲。"""
    m = QUIZ_RE.match(text.strip())
    if not m or songdb is None or maimai_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    db = songdb.DB
    if not db.all:
        await db.refresh()

    key = _quiz_key(message_type, target, qq)
    old = QUIZ.get(key)
    if old and time.time() - old["ts"] < QUIZ_STEP_SEC * 6:
        _quiz_cancel(key)
        await BOT.send_text(
            message_type, target,
            f"上一题作废了。答案是「{_quiz_title_of(old)}」。",
            at_qq=at,
        )

    # 先按曲风分组，再随机挑曲风 —— 否则 maimai 原创 375 首会占满概率
    by_genre: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for s0 in db.all:
        sid = int(s0["id"])
        if not (maimai_mod.COVER_DIR / f"{sid:05d}.png").exists():
            continue
        for d in db.difficulties(sid):
            if d["index"] >= 2:
                g = str(s0.get("genre") or "未知")
                by_genre.setdefault(g, []).append((s0, d))
                break

    if not by_genre:
        await BOT.send_text(message_type, target, "曲库还没准备好。", at_qq=at)
        return True

    # 最近 50 题不重复：只在没出过的曲风里挑，挑完再把新题记进去
    fresh_genres = [
        g for g, rows in by_genre.items()
        if any(int(r[0]["id"]) not in QUIZ_RECENT for r in rows)
    ]
    if not fresh_genres:
        QUIZ_RECENT.clear()          # 全都出过了，清空重来
        fresh_genres = list(by_genre.keys())

    genre_pick = random.choice(fresh_genres)
    pool = [r for r in by_genre[genre_pick] if int(r[0]["id"]) not in QUIZ_RECENT]
    if not pool:
        pool = by_genre[genre_pick]
    s0, d = random.choice(pool)
    sid = int(s0["id"])
    QUIZ_RECENT.append(sid)

    # 生成 4 条文字提示（第 5 步是图）
    genre = GENRE_CN.get(str(s0.get("genre") or ""), str(s0.get("genre") or "未知"))
    bpm = s0.get("bpm")
    artist = str(s0.get("artist") or "未知")
    if len(artist) > 36:
        artist = artist[:35] + "…"
    diffs = db.difficulties(sid)
    top = max(diffs, key=lambda x: float(x.get("value") or 0)) if diffs else None
    top_txt = (
        f"{songdb.DIFF_CN.get(top['index'], '?')} {top['level']}"
        if top
        else "?"
    )

    hints = [
        f"曲风：{genre}",
        f"BPM：{bpm}" if bpm else "BPM：未知",
        f"曲师：{artist}",
        f"最高难度：{top_txt}",
    ]

    aliases = _alias_map().get(sid, [])
    reveal_lines = [
        f"曲风：{genre}",
        f"BPM：{bpm}" if bpm else "BPM：未知",
        f"曲师：{artist}",
        f"最高难度：{top_txt}",
        f"曲目 ID：{sid}",
    ]
    if aliases:
        reveal_lines.append("别名：" + " / ".join(aliases[:6]))
    QUIZ[key] = {
        "id": sid,
        "title": str(s0.get("title") or ""),
        "aliases": aliases,
        "hints": hints,
        "reveal": "\n".join(reveal_lines),
        "stage": 0,
        "ts": time.time(),
        "tries": 0,
        "task": None,
    }
    total = (len(hints) + 1) * QUIZ_STEP_SEC
    await BOT.send_text(
        message_type, target,
        f"猜猜这是哪首？\n提示 1/{len(hints) + 1}　{hints[0]}\n"
        f"每 {QUIZ_STEP_SEC} 秒多一条线索，{total} 秒后公布答案。"
        f"\n直接发曲名或别名，别的话贝尔当没听见。",
        at_qq=at,
    )
    QUIZ[key]["task"] = asyncio.create_task(_quiz_ticker(key, message_type, target, at))
    return True


def quiz_pending(message_type: str, target: int, qq: str) -> bool:
    """这个会话里是不是还有没答完的猜曲题。"""
    key = _quiz_key(message_type, target, qq)
    q = QUIZ.get(key)
    if not q:
        return False
    if time.time() - q["ts"] > 300:
        QUIZ.pop(key, None)
        return False
    return True


def check_quiz_answer(
    qq: str, text: str, message_type: str, target: int
) -> tuple[str, dict[str, Any]] | None:
    """有人答题时调用。答对返回 (恭喜语, 题目数据)，否则 None。"""
    key = _quiz_key(message_type, target, qq)
    q = QUIZ.get(key)
    if not q:
        return None
    if time.time() - q["ts"] > QUIZ_STEP_SEC * 8:
        _quiz_cancel(key)
        return None

    guess = _norm_title(text or "")
    if not guess or len(guess) < 2:
        return None

    title = _quiz_title_of(q)
    answer = _norm_title(title)
    accepts = [answer] + [_norm_title(a) for a in (q.get("aliases") or [])]
    accepts = [a for a in accepts if a and len(a) >= 2]

    hit = False
    for a in accepts:
        if guess == a or (len(a) >= 3 and (a in guess or guess in a)):
            hit = True
            break
    if not hit:
        q["tries"] = int(q.get("tries", 0)) + 1
        return None

    picked = dict(q)          # 先把题目信息留下来，公布时要用
    _quiz_cancel(key)
    line = random.choice(QUIZ_WIN_LINES).format(t=title)
    return line, picked


async def do_cost(qq: str, text: str, message_type: str, target: int) -> bool:
    """/cost [all] 看费用和用量。所有人能看自己，主人加 all 看全部。"""
    m = COST_RE.match(text.strip())
    if not m:
        return False

    at = int(qq) if message_type == "group" else None
    want_all = bool(m.group(1)) and is_master(qq)
    ensure_day()
    for u0 in STORE.users.values():
        ensure_user_day(u0)

    u = STORE.user(qq)
    mine = float(u.get("cost", 0.0))
    my_calls = int(u.get("calls", 0))
    g = global_spent()
    gcap = float(BUDGET.get("global_daily_cny", 8.0))
    ucap = float(BUDGET.get("user_daily_cny", 2.0))
    master = is_master(qq)

    lines = ["今日用量"]
    if master:
        owner = float(STORE.meta.get("owner_cost", 0.0))
        lines.append(f"· 我（主人）：{mine:.3f} 元 / {my_calls} 次（不计入群友额度，累计 {owner:.3f}）")
    else:
        left = max(0.0, ucap - mine)
        lines.append(f"· 我：{mine:.3f} / {ucap:.1f} 元（剩 {left:.3f}）· {my_calls} 次")

    left_g = max(0.0, gcap - g)
    lines.append(f"· 群友合计：{g:.3f} / {gcap:.1f} 元（剩 {left_g:.3f}）")
    lines.append(f"· token：输入 {int(STORE.meta.get('ptok', 0)):,} / 输出 {int(STORE.meta.get('ctok', 0)):,}")

    if want_all:
        rows = []
        for k, v in STORE.users.items():
            try:
                st = int(k) in set(CFG.get("master_ids", []))
            except (TypeError, ValueError):
                st = False
            if st:
                continue
            c = float(v.get("cost", 0.0))
            if c <= 0:
                continue
            rows.append((c, v.get("name") or k, int(v.get("calls", 0))))
        rows.sort(reverse=True)
        if rows:
            lines.append("" + "排行")
            for i, (c, name, n) in enumerate(rows[:10], 1):
                lines.append(f"  {i}. {name}  {c:.3f} 元 / {n} 次")
        else:
            lines.append("" + "今天还没有群友花过钱。")

    await BOT.send_text(message_type, target, "\n".join(lines), at_qq=at)
    return True


async def do_new(qq: str, text: str, message_type: str, target: int) -> bool:
    """/new [批数]：看最近更新的曲目（按落雪更新日历）。"""
    m = NEW_RE.match(text.strip())
    if not m or songdb is None:
        return False

    at = int(qq) if message_type == "group" else None
    db = songdb.DB
    if not db.all:
        await db.refresh()
    if not db.count():
        await BOT.send_text(message_type, target, "曲库还没加载好，过一会儿再试。", at_qq=at)
        return True

    n = max(1, min(4, int(m.group(1) or 1)))
    batches = db.recent(n)
    if not batches:
        await BOT.send_text(message_type, target, "日历是空的。", at_qq=at)
        return True

    lines = []
    for b in batches:
        lines.append(f"{b['date']}  新曲 {b['count']}")
        for s0 in b["songs"][:5]:
            lines.append(f"  {s0['id']}  {s0['title']}")
        if b["count"] > 5:
            lines.append(f"  ……还有 {b['count'] - 5} 首")
        lines.append("")
    await BOT.send_text(message_type, target, "\n".join(lines).strip(), at_qq=at)
    return True


async def do_song(qq: str, text: str, message_type: str, target: int) -> bool:
    """/song 关键词 —— 查曲目 ID、定数、版本。"""
    m = SONG_RE.match(text.strip())
    if not m or songdb is None:
        return False

    at = int(qq) if message_type == "group" else None
    kw = m.group(1).strip()
    db = songdb.DB
    if not db.all:
        await db.refresh()
    if not db.count():
        await BOT.send_text(message_type, target, "曲库还没加载好，过一会儿再试。", at_qq=at)
        return True

    rows = db.search(kw, 5)
    if not rows:
        await BOT.send_text(message_type, target, f"曲库里没有「{kw}」", at_qq=at)
        return True

    lines = []
    for s0 in rows:
        sid = int(s0["id"])
        ver = songdb.VERSION_NAME.get(int(s0.get("version") or 0), "")
        head = f"{sid}  {s0.get('title')}"
        if ver:
            head += f"  [{ver}]"
        diffs = [d for d in db.difficulties(sid) if d["type"] == "DX"] or db.difficulties(sid)
        lv = "  ".join(
            f"{songdb.DIFF_CN.get(d['index'], '?')} {d['level']}" for d in diffs
        )
        lines.append(head + "\n   " + lv if lv else head)

    body = "\n".join(lines[:4])
    if len(rows) > 4:
        body += f"\n……还有 {len(rows) - 4} 首"
    await BOT.send_text(message_type, target, body, at_qq=at)
    return True


async def do_fortune(qq: str, text: str, message_type: str, target: int) -> bool:
    """今日运势 —— 今日签：分数 + 洋快餐单品 + maimai 今日曲（带曲绘）。

    群里不用 @、斜杠可有可无（2026-11-22 起）。
    不进模型、不花钱：结果按「北京日期 + QQ」算种子，同一天同一人固定，
    零点（UTC+8）自动换签。
    """
    m = FORTUNE_RE.match(text.strip())
    if not m or fortune_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    db = None
    if songdb is not None:
        db = songdb.DB
        if not db.all:
            try:
                await db.refresh()
            except Exception as exc:
                log.warning("运势：曲库加载失败 %s", exc)
                db = None

    # 今天如果重抽过，就继续用重抽那份，别又跳回原签
    reroll = 0
    rec = STORE.user(qq).get("fortune_reroll")
    if isinstance(rec, dict) and rec.get("day") == fortune_mod.beijing_day():
        if int(rec.get("n") or 0) >= 1:
            reroll = 1

    res = fortune_mod.draw(qq, db=db, reroll=reroll)
    png = fortune_mod.render_card(res)
    if png:
        # 只发图，不带文字
        await BOT.send_image(message_type, target, png)
    else:
        # 没有 Pillow 才退回文字
        await BOT.send_text(message_type, target, fortune_mod.text_of(res), at_qq=at)
    log.info("[运势] %s %s → %s %s", message_type, qq, res["kind"], res["score"])
    return True


async def do_fortune_reroll(qq: str, text: str, message_type: str, target: int) -> bool:
    """重新抽签 —— 每人每天只能重抽一次，第二次就说明天再来。

    群里不用 @、斜杠可有可无（跟其他免 @ 指令一样）。
    """
    if not FORTUNE_REROLL_RE.match(text.strip()) or fortune_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    today = fortune_mod.beijing_day()

    u = STORE.user(qq)
    rec = u.get("fortune_reroll")
    if not isinstance(rec, dict) or rec.get("day") != today:      # 换天自动清零
        rec = {"day": today, "n": 0}
    if int(rec.get("n") or 0) >= 1:
        await BOT.send_text(message_type, target, "今天已经重抽过了，明天再来吧。", at_qq=at)
        log.info("[重新抽签] %s %s → 今天已经用过", message_type, qq)
        return True

    rec["n"] = 1
    u["fortune_reroll"] = rec
    try:
        await STORE.save()
    except Exception as exc:
        log.warning("重新抽签：存 state 失败 %s", exc)

    db = None
    if songdb is not None:
        db = songdb.DB
        if not db.all:
            try:
                await db.refresh()
            except Exception as exc:
                log.warning("重新抽签：曲库加载失败 %s", exc)
                db = None

    res = fortune_mod.draw(qq, db=db, reroll=1)
    png = fortune_mod.render_card(res)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target, fortune_mod.text_of(res), at_qq=at)
    log.info("[重新抽签] %s %s → %s %s", message_type, qq, res["kind"], res["score"])
    return True


async def do_gacha(qq: str, text: str, message_type: str, target: int,
                   name: str = "") -> bool:
    """抽卡 —— 一次抽**两张**，两张放同一张图里发出来。不用 @、斜杠可有可无。

    每天共 2 张额度（`gacha.daily_limit` 可改），抽完就明天再来；
    抽到的卡进卡包（`卡包` 看），昵称顺手记进 gacha.json 给欧非榜用。
    """
    if not GACHA_RE.match(text.strip()) or gacha_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    limit = int((CFG.get("gacha") or {}).get("daily_limit", 2))
    res = gacha_mod.take(qq, limit=limit, name=name)
    cards = res.get("cards") or []
    if not cards:
        if res.get("used", 0) >= limit:
            await BOT.send_text(message_type, target,
                                f"今天两张抽完了，明天再来吧。", at_qq=at)
        else:
            await BOT.send_text(message_type, target, "卡池还是空的……贝尔没图可抽。", at_qq=at)
        return True

    png = await asyncio.to_thread(gacha_mod.render_two, cards, res["used"], limit,
                                  res.get("new"))   # 画图丢线程，别卡事件循环
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        body = "\n\n".join(gacha_mod.text_of(c) for c in cards)
        await BOT.send_text(message_type, target, body, at_qq=at)
    log.info("[抽卡] %s %s → %s（今天 %s/%s 张）", message_type, qq,
             " + ".join(f"{c['rarity']} {c['title']}" for c in cards),
             res["used"], limit)
    await STORE.save()      # 顺手把 state 里的昵称落盘，欧非榜才不会只剩 QQ 尾号
    return True


async def _who_is(qq: str, message_type: str, target: int,
                  at_others: list[str] | None) -> tuple[str, str]:
    """`卡包`/`卡池` 看谁：群里 @ 了人就看那个人，返回 (qq, 标题用的昵称)。"""
    view = str(qq)
    if message_type == "group" and at_others:
        cand = [x for x in at_others if str(x).isdigit()]
        if cand:
            view = cand[0]
    if view == str(qq):
        return view, ""
    got = await _member_name(int(target) if message_type == "group" else None, view)
    if got:
        STORE.user(view)["name"] = got
        return view, got
    owner = display_name(view, STORE.user(view).get("name", "")) or ""
    if not owner:
        try:
            owner = str(((gacha_mod._load_save().get("stats") or {}).get(view) or {}).get("name") or "")
        except Exception:
            owner = ""
    return view, owner or f"…{view[-4:]}"


async def do_bag(qq: str, text: str, message_type: str, target: int,
                  at_others: list[str] | None = None) -> bool:
    """卡包 [@人] [页] —— 自己（或 @ 的人）抽到的卡，只摆小曲绘。"""
    m = BAG_RE.match(text.strip())
    if not m or gacha_mod is None:
        return False
    at = int(qq) if message_type == "group" else None
    try:
        page = int(m.group(1) or 1)
    except ValueError:
        page = 1
    view, owner = await _who_is(qq, message_type, target, at_others)
    png, msg = await asyncio.to_thread(gacha_mod.render_bag, view, page, owner)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target, msg or "卡包画不出来。", at_qq=at)
    log.info("[卡包] %s %s → 看 %s 第 %d 页，共 %d 张", message_type, qq, view, page,
             len(gacha_mod.owned(view)))
    return True


async def do_pool(qq: str, text: str, message_type: str, target: int,
                   at_others: list[str] | None = None) -> bool:
    """卡池 [等级] [@人] —— 看某个等级的一页，抽到的亮、没抽到的灰。

    `卡池` = N 第 1 页；`卡池 UR` = UR 第 1 页；`卡池 UR 2` = UR 第 2 页；
    群里 @ 一个人就看他/她的卡池（`卡池 UR @某人` 也行）。
    """
    m = POOL_RE.match(text.strip())
    if not m or gacha_mod is None:
        return False
    at = int(qq) if message_type == "group" else None
    rarity = (m.group(1) or "").upper() or None
    try:
        page = int(m.group(2) or 1)
    except ValueError:
        page = 1

    view, owner = await _who_is(qq, message_type, target, at_others)

    png, msg = await asyncio.to_thread(gacha_mod.render_pool, view, rarity, page, owner)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target, msg or "卡池画不出来。", at_qq=at)
    log.info("[卡池] %s %s → 看 %s / %s 第 %d 页", message_type, qq, view, rarity or "默认档", page)
    return True


async def _member_name(gid: int | None, qq: str) -> str:
    """跟协议端要昵称：群里走 get_group_member_info，私聊走 get_stranger_info。"""
    try:
        if gid:
            data = await BOT.api("get_group_member_info",
                                 {"group_id": gid, "user_id": int(qq), "no_cache": False})
        else:
            data = await BOT.api("get_stranger_info", {"user_id": int(qq), "no_cache": False})
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("card") or data.get("nickname") or "").strip()


_SEEDED_GROUPS: set[str] = set()
_PROBED_GROUPS: set[str] = set()


def _apply_md_roster(gid: str) -> None:
    """`群成员.md` 是这个群名单的权威版：以它为准，再并上「本次进程里说过话的人」。

    这样在 md 里删掉一行 == 把这个人移出榜；新说话的人下次写盘时又会补回 md。
    """
    if members_md is None:
        return
    try:
        md = (members_md.load().get(str(gid)) or {})
    except Exception:
        return
    if not md:
        return
    cur = STORE.members.setdefault(str(gid), {})
    speakers = {q for q, ts in cur.items() if ts and ts > 0}   # 名单里是 0.0，说过话才有 ts
    merged = {q: cur.get(q, 0.0) for q in set(md) | speakers}
    if set(merged) != set(cur):
        log.info("[群成员] 群 %s 按 md 校正：%d → %d 人", gid, len(cur), len(merged))
        STORE.members[str(gid)] = merged
        STORE._members_dirty = True


async def _seed_group_members(gid: str) -> int:
    """第一次看某个群的榜时，问协议端要一次群成员名单（QQ + 群名片一起收）。"""
    if gid in _SEEDED_GROUPS:
        return 0
    _SEEDED_GROUPS.add(gid)
    data = None
    for payload in ({"group_id": int(gid)}, {"group_id": int(gid), "no_cache": False},
                    {"group_id": str(gid)}):
        try:
            data = await BOT.api("get_group_member_list", payload, timeout=25)
        except Exception as exc:
            log.warning("[欧非榜] 群 %s 取成员名单失败：%s", gid, exc)
            data = None
        if data:
            break
    if not data:
        log.warning("[欧非榜] 群 %s 拿不到成员名单（协议端不支持或超时）", gid)
        _sync_members_md()
        return 0

    if isinstance(data, dict):
        for key in ("list", "members", "data"):
            if key in data:
                members = data.get(key)
                break
        else:
            members = None
    else:
        members = data
    if not isinstance(members, list):
        log.warning("[欧非榜] 群 %s 成员名单格式不对：%s", gid, str(data)[:120])
        return 0

    bucket = STORE.members.setdefault(gid, {})
    added = 0
    named = 0
    if members_md is not None:                 # 先把 md 里的旧名单并进来
        try:
            added += members_md.merge_into(STORE.members, members_md.load())
        except Exception as exc:
            log.warning("[群成员] 读 md 失败：%s", exc)
    for m in members:
        if not isinstance(m, dict):
            continue
        who = ""
        for k in ("user_id", "uin", "qq", "member_uin"):
            v = m.get(k)
            if v is not None and str(v).isdigit():
                who = str(v)
                break
        if not who:
            continue
        if who not in bucket:
            added += 1
        bucket[who] = bucket.get(who) or 0.0
        nm = str(m.get("card") or m.get("nickname") or "").strip()
        if nm:
            if not STORE.user(who).get("name"):
                named += 1
            STORE.user(who)["name"] = display_name(who, nm)
    if added:
        STORE._members_dirty = True
        log.info("[欧非榜] 群 %s 补了 %d 个成员（%d 个有昵称）", gid, added, named)
    _sync_members_md()
    return added


async def do_members_md(qq: str, text: str, message_type: str, target: int) -> bool:
    """群名单 —— 把 state 里记的群成员写进 `群成员.md`（主人专用）。"""
    if not MEMBERS_MD_RE.match(text.strip()):
        return False
    at = int(qq) if message_type == "group" else None
    if not is_master(qq):
        await BOT.send_text(message_type, target, "这个是主人用的。", at_qq=at)
        return True
    if members_md is None:
        await BOT.send_text(message_type, target, "成员名单模块没加载上……", at_qq=at)
        return True
    _sync_members_md()
    groups = len([g for g, m in STORE.members.items() if m])
    people = sum(len(m) for m in STORE.members.values())
    await BOT.send_text(message_type, target,
                        f"已统计 {groups} 个群 / {people} 人 → 群成员.md", at_qq=at)
    log.info("[群名单] %s %s → %d 群 %d 人", message_type, qq, groups, people)
    return True


async def _probe_group_members(gid: str, qqs: list[str]) -> int:
    """名单接口不给时，逐个问「这个号在不在本群」（get_group_member_info）。

    只问有卡的人（最多 60 个），问到就顺手把群名片记下来。
    """
    if not qqs:
        return 0
    bucket = STORE.members.setdefault(gid, {})
    added = 0
    for who in qqs[:60]:
        if who in bucket:
            continue
        try:
            data = await BOT.api("get_group_member_info",
                                 {"group_id": int(gid), "user_id": int(who), "no_cache": False},
                                 timeout=15)
        except Exception:
            continue
        if not isinstance(data, dict) or not data:
            continue
        bucket[who] = 0.0
        added += 1
        nm = str(data.get("card") or data.get("nickname") or "").strip()
        if nm:                       # 群名片最准，覆盖旧值
            STORE.user(who)["name"] = display_name(who, nm)
    if added:
        STORE._members_dirty = True
        log.info("[欧非榜] 群 %s 逐个探测补了 %d 人", gid, added)
        _sync_members_md()
    return added


async def do_luck_rank(qq: str, text: str, message_type: str, target: int) -> bool:
    """欧榜 / 非榜 —— 群里只统计本群的人，私聊看全服。前 10 / 倒 10，无名不计。"""
    lm = LUCK_RE.match(text.strip())
    if not lm or gacha_mod is None:
        return False
    # 欧非榜 当欧榜，其它按字面
    word = (lm.group(1) or "欧榜").strip()
    mode = "bottom" if word == "非榜" else "top"
    at = int(qq) if message_type == "group" else None
    gid = str(target) if message_type == "group" else None

    all_rows = gacha_mod.luck_table()
    seeded = 0
    if gid:
        _apply_md_roster(gid)                          # md 是权威名单（可手改）
        if len(STORE.members.get(gid) or {}) < 2:      # 第一次看这个群：先问一次成员名单
            seeded = await _seed_group_members(gid)
        members = set((STORE.members.get(gid) or {}).keys())
        rows = [r for r in all_rows if r["qq"] in members]
        if len(rows) < len(all_rows) and gid not in _PROBED_GROUPS:
            # 名单不全：按有卡的人逐个探一次（每个群每个进程只探一次）
            _PROBED_GROUPS.add(gid)
            seeded += await _probe_group_members(gid, [r["qq"] for r in all_rows])
            members = set((STORE.members.get(gid) or {}).keys())
            rows = [r for r in all_rows if r["qq"] in members]
        scope = "本群"
    else:
        rows = all_rows
        scope = "全服"

    # 名单补完再读昵称（种子会把群名片写进 state）
    names = {str(k): (v or {}).get("name") or "" for k, v in (STORE.users or {}).items()}
    for who in list(names):                       # 主人的显示名固定，别被旧值盖住
        names[who] = display_name(who, names[who])

    if not rows:
        await BOT.send_text(message_type, target,
                            "本群还没人抽过卡，先发「抽卡」试试。" if gid else "还没有人抽过卡。",
                            at_qq=at)
        return True

    # 榜上没有昵称的，现问协议端要一次，问到就存进 state 下次直接用
    missing = [r["qq"] for r in rows if not names.get(r["qq"])]
    for who in missing[:20]:
        got = await _member_name(gid, who)
        if got:
            names[who] = got
            STORE.user(who)["name"] = got
    if missing:
        await STORE.save()

    # 没有昵称的不上榜（渲染层还有一层过滤，这里先滤掉省得白算）
    rows = [r for r in rows if (names.get(r["qq"]) or r.get("name") or "").strip()]
    if not rows:
        await BOT.send_text(message_type, target, "榜上这些人还都没昵称，等他们先说句话。", at_qq=at)
        return True

    await asyncio.to_thread(gacha_mod.backfill_names, names)   # 顺手把昵称记进 gacha.json
    png, msg = await asyncio.to_thread(gacha_mod.render_luck_board, rows, names, 10, scope, mode)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target, msg or "榜单画不出来。", at_qq=at)
    if gid and len(rows) < 2 and seeded == 0:
        await BOT.send_text(message_type, target,
                            "协议端没给群成员名单，现在只能统计在这个群说过话的人。",
                            at_qq=at)
    log.info("[%s] %s %s → %s %d 人（seed %d）", word, message_type, qq, scope, len(rows), seeded)
    return True


async def do_bind_fish(qq: str, text: str, message_type: str, target: int) -> bool:
    """绑定水鱼 —— 私聊发，给一条设备码链接；点开同意后就能查自己的成绩（含隐私号）。"""
    if not BIND_FISH_RE.match(text.strip()) or moauth_mod is None:
        return False

    mm = CFG.get("maimai", {})
    if not moauth_mod.enabled(mm):
        await BOT.send_text(message_type, target,
                            "水鱼 OAuth 还没配（`maimai.oauth_client_id` / `oauth_client_secret`）。")
        return True

    # 绑定链接谁点开就绑到谁的水鱼账号上，发群里等于让人冒名绑定 —— 只私聊给
    if message_type != "private":
        await BOT.send_text(message_type, target, "绑定要私聊我发，群里有别人看着呢。",
                            at_qq=int(qq))
        return True

    link = await moauth_mod.device_link(qq, mm)
    if not link:
        await BOT.send_text(message_type, target, "链接没生成出来，过一会儿再试。")
        return True
    await BOT.send_text(message_type, target,
                        f"点开这条链接，用水鱼账号登录并同意，就能把我接到你的成绩上：\n{link}\n"
                        f"链接 10 分钟内有效。")
    log.info("[绑定水鱼] %s 已发出绑定链接", qq)
    return True


async def do_refresh_b50(qq: str, text: str, message_type: str, target: int) -> bool:
    """更新b50 —— 用已经绑定的水鱼授权重新拉一次自己的 B50 出图。"""
    if not REFRESH_B50_RE.match(text.strip()) or moauth_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    mm = CFG.get("maimai", {})
    if not moauth_mod.enabled(mm):
        await BOT.send_text(message_type, target, "水鱼 OAuth 还没配。", at_qq=at)
        return True

    try:
        token = await moauth_mod.access_token(qq, mm)
    except moauth_mod.NotBound:
        link = await moauth_mod.device_link(qq, mm)
        tip = "还没绑定水鱼账号，先私聊我发「绑定水鱼」。"
        if link and message_type == "private":
            tip = f"还没绑定水鱼账号。点开这条链接同意一下（10 分钟内有效）：\n{link}"
        await BOT.send_text(message_type, target, tip, at_qq=at)
        return True
    except moauth_mod.QuotaExceeded:
        await BOT.send_text(message_type, target, "水鱼那边今天换票次数用完了，明天再来。", at_qq=at)
        return True
    except Exception as exc:
        log.warning("[更新b50] 换票失败 %s：%s", qq, exc)
        await BOT.send_text(message_type, target, "水鱼那边没回应，等会儿再试。", at_qq=at)
        return True

    if not token:
        await BOT.send_text(message_type, target, "拿不到授权，先私聊发「绑定水鱼」。", at_qq=at)
        return True

    try:
        avatar = await maimai_mod.fetch_qq_avatar(qq) if maimai_mod else None
        png, data = await maimai_mod.query_to_png(
            bearer=token,
            lxns_key=mm.get("lxns_key", ""),
            avatar=avatar,
            theme=mm.get("theme", "dark"),
        )
    except Exception as exc:
        log.warning("[更新b50] 查询失败 %s：%s", qq, exc)
        await BOT.send_text(message_type, target, f"查不到：{exc}", at_qq=at)
        return True

    await BOT.send_image(message_type, target, png,
                         caption=f"{data.get('nickname') or qq}　Rating {data.get('rating')}")
    log.info("[更新b50] %s → %s (%s)", qq, data.get("nickname"), data.get("rating"))
    return True


async def do_random_song(qq: str, text: str, message_type: str, target: int) -> bool:
    """/随一首 13（或 13+）—— 该等级随机抽一首，给全部信息。"""
    m = RANDOM_SONG_RE.match(text.strip())
    if not m or songcard_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    arg = (m.group(1) or "").strip()
    lv = songcard_mod.parse_level(arg)
    if lv is None:
        await BOT.send_text(
            message_type, target,
            "用法：/随一首 13　或　/随一首 13+　（1-15，13 会把 13 和 13+ 一起抽）",
            at_qq=at,
        )
        return True

    db = None
    if songdb is not None:
        db = songdb.DB
        if not db.all:
            try:
                await db.refresh()
            except Exception as exc:
                log.warning("随一首：曲库加载失败 %s", exc)
    if db is None or not db.all:
        await BOT.send_text(message_type, target, "曲库还没加载好，过一会儿再试。", at_qq=at)
        return True

    row = songcard_mod.pick(db, lv[0])
    if not row:
        await BOT.send_text(message_type, target, f"定数 {arg} 没有曲目。", at_qq=at)
        return True

    info = songcard_mod.song_info(db, row["song"], row["chart"])
    # 本地没这张曲绘就现拉一次（拉不到就用「无曲绘」占位）
    if not info.get("cover") and maimai_mod is not None:
        try:
            if await maimai_mod.fetch_covers([info["id"]]):
                info["cover"] = songcard_mod.F._cached_cover(info["id"])
        except Exception as exc:
            log.warning("随一首：拉曲绘失败 %s", exc)
    label = songcard_mod.level_label(db, lv[0])
    png = songcard_mod.render_song(info, label)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target, songcard_mod.text_of(info), at_qq=at)
    log.info("[随一首] %s %s → %s %s", message_type, qq, info["title"], info["id"])
    return True


async def do_ds_table(qq: str, text: str, message_type: str, target: int) -> bool:
    """/定数表 13 [页码] —— 列出该定数下所有谱面。"""
    m = DS_TABLE_RE.match(text.strip())
    if not m or songcard_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    ar = (m.group(1) or "").strip()
    lv = songcard_mod.parse_level(ar)
    if lv is None:
        await BOT.send_text(
            message_type, target,
            "用法：/定数表 13　或　/定数表 13+　（1-15，按小数定数分段画长图）",
            at_qq=at,
        )
        return True

    db = None
    if songdb is not None:
        db = songdb.DB
        if not db.all:
            try:
                await db.refresh()
            except Exception as exc:
                log.warning("定数表：曲库加载失败 %s", exc)
    if db is None or not db.all:
        await BOT.send_text(message_type, target, "曲库还没加载好，过一会儿再试。", at_qq=at)
        return True

    rows = songcard_mod.rows_at(db, lv[0], exact=True)
    if not rows:
        await BOT.send_text(message_type, target, f"定数 {ar} 没有曲目。", at_qq=at)
        return True

    # 长图里所有曲绘先拉齐（水鱼的 DX/SD ID 互转也认），拉不到就留灰块
    ids = songcard_mod.unique_songs(rows)
    missing = [sid for sid in ids if not fortune_mod._cached_cover(sid)] if fortune_mod else []
    if missing and maimai_mod is not None:
        try:
            await maimai_mod.fetch_covers(missing[:200])
        except Exception as exc:
            log.warning("定数表：拉曲绘失败 %s", exc)

    png = songcard_mod.render_wall(rows, lv[0])
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(
            message_type, target,
            f"定数 {lv[0]} 共 {len(ids)} 首（图没画出来，检查 Pillow）",
            at_qq=at,
        )
    log.info("[定数表] %s %s → %s，%d 首", message_type, qq, lv[0], len(ids))
    return True


X8_DIR = BASE_DIR / "西八"
X8_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def xiba_files() -> list[Path]:
    """西八文件夹里的图（子目录不管，非图片不管）。"""
    if not X8_DIR.is_dir():
        return []
    return sorted(p for p in X8_DIR.iterdir() if p.is_file() and p.suffix.lower() in X8_EXTS)


async def do_random_xiba(qq: str, text: str, message_type: str, target: int) -> bool:
    """随机西八 [1-3] —— 从「西八」文件夹随机抽图，最多三张。

    群里不用 @（at_only 的例外），斜杠可有可无；不带数字就发一张。
    """
    m = X8_RE.match(text.strip())
    if not m:
        return False

    at = int(qq) if message_type == "group" else None
    try:
        want = int(m.group(1) or 1)
    except ValueError:
        want = 1
    want = max(1, min(3, want))

    files = xiba_files()
    if not files:
        await BOT.send_text(message_type, target, "西八文件夹是空的……贝尔没东西发。", at_qq=at)
        return True

    picks = random.sample(files, min(want, len(files)))
    raws: list[bytes] = []
    sent_names: list[str] = []
    for path in picks:
        try:
            raws.append(path.read_bytes())
            sent_names.append(path.name)
        except Exception as exc:
            log.warning("[随机西八] 读不了 %s：%s", path, exc)
    if not raws:
        await BOT.send_text(message_type, target, "图读不出来……贝尔翻车了。", at_qq=at)
        return True

    # 多张合成一条消息（普通图文，不是合并转发）
    mid = await BOT.send_images(message_type, target, raws)
    if mid and xiba_mod is not None:
        try:
            xiba_mod.remember_sent(mid, sent_names)
        except Exception as exc:
            log.warning("[随机西八] 记 message_id 失败：%s", exc)
    if xiba_mod is not None:
        for name in sent_names:
            try:
                xiba_mod.bump(name, by=qq)
            except Exception as exc:
                log.warning("[随机西八] 记分失败 %s：%s", name, exc)
    log.info("[随机西八] %s %s → %d/%d 张（一条消息）", message_type, qq, len(raws), len(files))
    return True


PJSK_DIR = BASE_DIR / "pjsk"
PJSK_EXTS = X8_EXTS


def pjsk_files() -> list[Path]:
    """pjsk 文件夹里的漫画（子目录不管，非图片不管）。"""
    if not PJSK_DIR.is_dir():
        return []
    return sorted(p for p in PJSK_DIR.iterdir() if p.is_file() and p.suffix.lower() in PJSK_EXTS)


async def do_random_pjsk(qq: str, text: str, message_type: str, target: int) -> bool:
    """随机烧烤 [1-3] —— 从 pjsk/ 文件夹随机抽漫画，最多三张。

    群里不用 @（at_only 的例外），斜杠可有可无；不带数字就发一张。
    不做榜、不计分。
    """
    m = PJSK_RE.match(text.strip())
    if not m:
        return False

    at = int(qq) if message_type == "group" else None
    try:
        want = int(m.group(1) or 1)
    except ValueError:
        want = 1
    want = max(1, min(3, want))

    files = pjsk_files()
    if not files:
        await BOT.send_text(message_type, target, "pjsk 文件夹是空的……贝尔还没扒漫画。", at_qq=at)
        return True

    picks = random.sample(files, min(want, len(files)))
    raws: list[bytes] = []
    for path in picks:
        try:
            raws.append(path.read_bytes())
        except Exception as exc:
            log.warning("[随机烧烤] 读不了 %s：%s", path, exc)
    if not raws:
        await BOT.send_text(message_type, target, "图读不出来……贝尔翻车了。", at_qq=at)
        return True

    await BOT.send_images(message_type, target, raws)
    log.info("[随机烧烤] %s %s → %d/%d 张", message_type, qq, len(raws), len(files))
    return True


X8_DENY_LINES = [
    "西八是主人管的，想加图先问主人。",
    "唔，这个只有主人能往里塞图。",
]


def _cq_image_sources(text: str) -> list[str]:
    """兼容 messageFormat=string 的协议端：从 CQ 码里抠图片地址。"""
    out: list[str] = []
    for m in re.finditer(r"\[CQ:image,([^\]]*)\]", str(text or "")):
        attrs = dict(
            kv.split("=", 1) for kv in m.group(1).split(",") if "=" in kv
        )
        src = (attrs.get("url") or attrs.get("file") or "").replace("&amp;", "&")
        if src and src not in out:
            out.append(src)
    return out


def _seg_image_sources(segs: list[dict[str, Any]] | None) -> list[str]:
    """从消息段里挑出图片地址（url / file 都认）。"""
    out: list[str] = []
    for s in segs or []:
        if s.get("type") != "image":
            continue
        d = s.get("data") or {}
        src = str(d.get("url") or d.get("file") or "").strip()
        if src and src not in out:
            out.append(src)
    return out


def _ext_of(raw: bytes, hint: str = "") -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:4] == b"GIF8":
        return ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    if raw[:2] == b"BM":
        return ".bmp"
    hit = re.search(r"\.(jpe?g|png|gif|webp|bmp)(?:\?|$)", hint, re.I)
    return "." + hit.group(1).lower() if hit else ".jpg"


async def _load_image(src: str, limit_mb: int = 20) -> bytes | None:
    """把 url / base64:// / 本地路径 统一读成 bytes。"""
    src = src.strip()
    if not src:
        return None
    if src.startswith("base64://"):
        try:
            return base64.b64decode(src[9:])
        except Exception:
            return None
    if src.startswith("file://"):
        src = src[7:]
    if src.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cli:
                r = await cli.get(src)
                if r.status_code != 200:
                    return None
                raw = r.content
                return raw if 0 < len(raw) <= limit_mb * 1024 * 1024 else None
        except Exception as exc:
            log.warning("[添加西八] 下载失败 %s：%s", src[:80], exc)
            return None
    path = Path(src)
    if path.exists() and path.is_file():
        try:
            raw = path.read_bytes()
            return raw if 0 < len(raw) <= limit_mb * 1024 * 1024 else None
        except Exception:
            return None
    return None


async def do_add_xiba(qq: str, text: str, message_type: str, target: int,
                      segs: list[dict[str, Any]] | None = None) -> bool:
    """添加西八 —— 引用一条带图的消息（或自己带图发），把图扒进「西八」文件夹。

    群里不用 @，斜杠可有可无；默认只有主人能加（config.json → xiba.only_master）。
    """
    if not ADD_X8_RE.match(text.strip()):
        return False

    at = int(qq) if message_type == "group" else None
    cfg = CFG.get("xiba") or {}
    if bool(cfg.get("only_master", True)) and not is_master(qq):
        await BOT.send_text(message_type, target, random.choice(X8_DENY_LINES), at_qq=at)
        return True

    sources: list[str] = []
    for seg in segs or []:
        if seg.get("type") != "reply":
            continue
        mid = str((seg.get("data") or {}).get("id") or "")
        if not mid:
            continue
        data = await BOT.api("get_msg", {"message_id": mid})
        if data:
            msg = data.get("message")
            if isinstance(msg, str):
                sources.extend(_cq_image_sources(msg))
            else:
                sources.extend(_seg_image_sources(msg))
    sources.extend(_seg_image_sources(segs))      # 本条一起发的图也算

    if not sources:
        await BOT.send_text(message_type, target, "要引用一条带图的消息，贝尔才知道扒哪张。", at_qq=at)
        return True

    X8_DIR.mkdir(parents=True, exist_ok=True)
    saved = dup = fail = 0
    for src in sources:
        raw = await _load_image(src)
        if not raw:
            fail += 1
            continue
        path = X8_DIR / (hashlib.md5(raw).hexdigest() + _ext_of(raw, src))
        if path.exists():
            dup += 1
            continue
        try:
            path.write_bytes(raw)
            saved += 1
        except Exception as exc:
            log.warning("[添加西八] 写入失败：%s", exc)
            fail += 1

    parts = [f"扒到 {len(sources)} 张"]
    if saved:
        parts.append(f"新存 {saved} 张")
    if dup:
        parts.append(f"{dup} 张已经有了")
    if fail:
        parts.append(f"{fail} 张没下下来")
    await BOT.send_text(
        message_type, target,
        "，".join(parts) + f"。西八现在 {len(xiba_files())} 张。",
        at_qq=at,
    )
    log.info("[添加西八] %s %s → 新 %d / 重 %d / 失败 %d", message_type, qq, saved, dup, fail)
    return True


async def do_xiba_rank(qq: str, text: str, message_type: str, target: int) -> bool:
    """西八榜 [N] —— 抽中次数排行，默认前 10。不用 @、不用斜杠。"""
    m = X8_RANK_RE.match(text.strip())
    if not m:
        return False

    at = int(qq) if message_type == "group" else None
    try:
        want = int(m.group(1) or 10)
    except ValueError:
        want = 10
    want = max(1, min(20, want))

    if xiba_mod is None:
        await BOT.send_text(message_type, target, "计分模块没加载上……", at_qq=at)
        return True

    rows = xiba_mod.top(want)
    total_files = len(xiba_files())
    scores = xiba_mod.load_scores()
    total_picks = sum(int(v.get("n") or 0) for v in scores.values())

    png = await asyncio.to_thread(xiba_mod.render_rank, rows, total_files, total_picks)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target,
                            xiba_mod.text_of(rows, total_files, total_picks), at_qq=at)
    log.info("[西八榜] %s %s → 前 %d（共 %d 张 / %d 次）", message_type, qq, len(rows), total_files, total_picks)
    return True


async def handle_repeat(qq: str, text: str, gid: str,
                        segs: list[dict[str, Any]] | None = None) -> bool:
    """群里连续 3 条一模一样的**文字**（不管是不是同一个人），跟着复读一次。
    图片不参与：图片消息既不进队列，也不会被复读。"""
    # 带图的消息直接跳过
    if any((x.get("type") == "image") for x in (segs or [])):
        return False

    t = (text or "").strip()
    if not t or t.startswith("/"):
        return False
    if ZAN_RE.search(t):            # 「凿」有自己的反应，不参与复读
        return False
    if len(t) > 60:                 # 太长的整段不跟
        return False

    q = REPEAT[gid]
    q.append(t)
    if len(q) < REPEAT_N:
        return False
    tail = list(q)[-REPEAT_N:]
    if len(set(tail)) != 1:         # 最后 N 条不是同一句
        return False

    now = time.time()
    if now - _REPEAT_LAST.get(gid, 0) < REPEAT_GAP:
        return False                # 刚跟过一次，歇会儿
    _REPEAT_LAST[gid] = now
    q.clear()

    try:
        await BOT.send_text("group", int(gid), tail[0])
        log.info("[复读] 群 %s 跟了一句：%s", gid, tail[0][:30])
    except Exception as exc:
        log.warning("[复读] 发送失败：%s", exc)
    return True


async def handle_zan(qq: str, text: str, message_type: str, target: int) -> bool:
    """群里谁发一个「凿」，就甩那张举牌子的表情包。不 @ 也算，不吃冷却和额度。"""
    if not ZAN_RE.search((text or "").strip()):
        return False
    if not ZAN_FILE.exists():
        return False
    now = time.time()
    if now - _ZAN_LAST.get(qq, 0) < 3:
        return True                                  # 3 秒内同一人不重复发
    _ZAN_LAST[qq] = now
    try:
        await BOT.send_file_image(message_type, target, ZAN_FILE)
    except Exception as exc:
        log.warning("[凿] 发图失败：%s", exc)
    return True


async def handle_xiba_like(qq: str, text: str, message_type: str, target: int,
                            segs: list[dict[str, Any]] | None) -> bool:
    """引用一条 /随机西八 的消息，只回一个「操」（可带编号）就算点赞。

    不需要 @、不吃冷却；「操2」「操 1 3」对应那条消息里的第几张。
    """
    if xiba_mod is None:
        return False
    m = X8_LIKE_RE.fullmatch((text or "").strip())
    if not m:
        return False

    reply_id = ""
    for seg in segs or []:
        if seg.get("type") == "reply":
            reply_id = str((seg.get("data") or {}).get("id") or "")
            break
    if not reply_id:
        return False

    names = xiba_mod.sent_files(reply_id)
    if not names:
        return False

    want = [int(d) for d in re.findall(r"\d", m.group(1) or "")]
    idx = [n - 1 for n in want if 1 <= n <= len(names)]
    if want and not idx:
        return True                      # 编号越界：不当成错误，就是不赞
    if not idx:
        idx = [0]                        # 不带编号就赞第一张

    liked = 0
    for i in idx:
        try:
            if xiba_mod.like(names[i], f"{reply_id}|{qq}") is not None:
                liked += 1
        except Exception as exc:
            log.warning("[西八赞] 记赞失败 %s：%s", names[i], exc)
    if liked:
        # 点赞成功就回这一句（主人指定的）
        await BOT.send_text(message_type, target, "操操操。！")
    log.info("[西八赞] %s %s → %d 张（消息 %s）", message_type, qq, liked, reply_id)
    return True


async def do_xiba_like_rank(qq: str, text: str, message_type: str, target: int) -> bool:
    """西八赞榜 [N] —— 被「操」得最多的图，默认前 10。不用 @、不用斜杠。"""
    m = X8_LIKE_RANK_RE.match(text.strip())
    if not m:
        return False

    at = int(qq) if message_type == "group" else None
    try:
        want = int(m.group(1) or 10)
    except ValueError:
        want = 10
    want = max(1, min(20, want))

    if xiba_mod is None:
        await BOT.send_text(message_type, target, "计分模块没加载上……", at_qq=at)
        return True

    rows = xiba_mod.top_liked(want)
    total = xiba_mod.total_likes()
    png = await asyncio.to_thread(
        xiba_mod.render_rank, rows, len(xiba_files()), total,
        "西八赞榜", "西八赞榜 · 被「操」的次数", "赞",
        "贝尔娜提塔记的账 · 每被「操」一次 +1", "累计", 
    )
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target,
                            xiba_mod.text_of(rows, len(xiba_files()), total, "西八赞榜", "赞"), at_qq=at)
    log.info("[西八赞榜] %s %s → 前 %d（共 %d 赞）", message_type, qq, len(rows), total)
    return True


async def do_menu5c(qq: str, text: str, message_type: str, target: int) -> bool:
    """/5C吃什么 [食堂|学校] [明天] —— 主人专属，现扒 menu.jojodmo.com 的六个食堂。

    只给主人看：别人发了就回一句，不查。数据每天都是新的（缓存 20 分钟）。
    """
    m = MENU5C_RE.match(text.strip())
    if not m or menu5c_mod is None:
        return False

    at = int(qq) if message_type == "group" else None
    cfg = CFG.get("menu5c") or {}
    if cfg.get("master_only", True) and not is_master(qq):
        await BOT.send_text(
            message_type, target,
            random.choice(MENU5C_DENY_LINES),
            at_qq=at,
        )
        return True

    args = [w for w in (m.group(1) or "").split() if w.strip()]
    offset = 0
    rest: list[str] = []
    for w in args:
        low = w.lower()
        if w in ("明天", "tomorrow", "tmr"):
            offset = 1
        elif w in ("后天",):
            offset = 2
        else:
            rest.append(w)

    picked = menu5c_mod.resolve_halls(rest)
    if rest and picked is None:
        names = " / ".join(h["key"] for h in menu5c_mod.HALLS)
        await BOT.send_text(
            message_type, target,
            f"没有「{' '.join(rest)}」这个食堂。可选：{names}\n（也可以写 cmc / mudd / pitzer / scripps / pomona）",
            at_qq=at,
        )
        return True

    started = time.time()
    try:
        data = await asyncio.to_thread(
            menu5c_mod.collect,
            offset,
            picked,
            int(cfg.get("per_meal", 6)),
            int(cfg.get("cache_minutes", menu5c_mod.DEFAULT_TTL_MIN)),
        )
    except Exception as exc:
        log.warning("[5C] 抓菜单失败：%s", exc)
        await BOT.send_text(message_type, target, "网站那边没扒下来……过一会儿再问贝尔。", at_qq=at)
        return True

    png = await asyncio.to_thread(menu5c_mod.render_card, data)
    if png:
        await BOT.send_image(message_type, target, png)
    else:
        await BOT.send_text(message_type, target, menu5c_mod.text_of(data), at_qq=at)
    log.info(
        "[5C] %s %s → %s 共 %d 店（%.1fs）",
        message_type, qq, data["when"], len(data["halls"]), time.time() - started,
    )
    return True


def build_max_b50(nickname: str = "Bernadetta") -> dict[str, Any]:
    """理论最高 B50。

    规则（和游戏一致）：
        B15 = 当前大版本内定数最高的 15 首（version 落在同一个大版本段）
        B35 = 更早版本里定数最高的 35 首
    RA  = floor(定数 × 22.5)，101% 达成率
    """
    db = songdb.DB if songdb else None
    if db is None or not db.all:
        return {}

    # 每首歌取它最高难度的定数
    # 按谱面去重：(曲 ID, 难度) —— 同一首歌的 MASTER 和 Re:MASTER 是两个谱面
    best: dict[tuple[int, int], tuple[float, dict[str, Any], dict[str, Any]]] = {}
    for s0 in db.all:
        try:
            sid = int(s0["id"])
        except (KeyError, TypeError, ValueError):
            continue
        for d in db.difficulties(sid):
            try:
                v = float(d.get("value") or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v <= 0:
                continue
            key = (sid, int(d["index"]))
            cur = best.get(key)
            if cur is None or v > cur[0]:
                best[key] = (v, s0, d)

    if not best:
        return {}

    # 当前大版本 = 最大 version 抹掉后两位
    vers = []
    for v, s0, _ in best.values():
        try:
            vers.append(int(s0.get("version") or 0))
        except (TypeError, ValueError):
            pass
    latest = max(vers) if vers else 0
    major = (latest // 100) * 100

    def ver_of(row: tuple[float, dict[str, Any], dict[str, Any]]) -> int:
        try:
            return int(row[1].get("version") or 0)
        except (TypeError, ValueError):
            return 0

    current = sorted(
        [r for r in best.values() if ver_of(r) >= major], key=lambda x: -x[0]
    )[:15]
    older = sorted(
        [r for r in best.values() if ver_of(r) < major], key=lambda x: -x[0]
    )[:35]

    def to_chart(v: float, s0: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": s0.get("title"),
            "song_id": int(s0["id"]),
            "level_index": int(d["index"]),
            "level": d["level"],
            "ds": v,
            "achievements": 101.0,
            "ra": int(v * 22.5),
            "type": d["type"],
            "fc": "ap",
            "fs": "fsp",
        }

    charts_sd = [to_chart(*r) for r in older]      # B35
    charts_dx = [to_chart(*r) for r in current]    # B15

    return {
        "nickname": nickname,
        "rating": sum(c["ra"] for c in charts_sd + charts_dx),
        "additional_rating": 22,
        "plate": "舞舞舞",
        "charts": {"sd": charts_sd, "dx": charts_dx},
    }


async def do_b50(qq: str, text: str, message_type: str, target: int, at_others: list[str] | None = None) -> bool:
    """处理 b50 查询。不带名字就按 QQ 号查（需要在水鱼绑过 QQ）。"""
    m = B50_RE.match(text.strip())
    if not m or maimai_mod is None:
        return False

    mm = CFG.get("maimai", {})
    if not mm.get("enable", True):
        return False

    at = int(qq) if message_type == "group" else None
    user = m.group(1).strip()

    # /b50 max —— 理论最高 B50
    if user.lower() in ("max", "最高", "理论"):
        # 曲库可能还没加载（bot 刚起或者一直没人用过 /song）
        if songdb is not None and not songdb.DB.all:
            try:
                await songdb.DB.refresh()
            except Exception as exc:
                log.warning("曲库加载失败：%s", exc)
        data = build_max_b50()
        if not data:
            await BOT.send_text(message_type, target, "曲库还没加载好，过一会儿再试。", at_qq=at)
            return True
        old_, new_ = maimai_mod.pick_b50(data)
        covers = await maimai_mod.fetch_covers([maimai_mod._mid(c) for c in old_ + new_])
        plate_img = await maimai_mod.fetch_plate(str(data.get("plate") or ""))
        # max 用 bot 自己的头像
        bot_avatar = None
        bot_uin = str(CFG.get("bot_uin") or BOT.self_id or "")
        if bot_uin.isdigit():
            bot_avatar = await maimai_mod.fetch_qq_avatar(bot_uin)
        png = maimai_mod.render(data, covers, avatar=bot_avatar, theme=mm.get("theme", "dark"),
                                plate_img=plate_img)
        log.info("[B50-max] rating=%s 曲数=%s 图 %s KB",
                 data["rating"], len(old_) + len(new_), len(png) // 1024)
        await BOT.send_image(
            message_type, target, png,
            caption=f"{data['nickname']}　理论最高 {data['rating']}",
        )
        log.info("[B50-max] rating=%s 曲数=%s", data["rating"], len(old_) + len(new_))
        return True

    # 头像：自己查用自己的 QQ 头像；查别人用大肥鱼（None → render 回退大肥鱼）
    avatar = None
    if not user:
        avatar = await maimai_mod.fetch_qq_avatar(qq)

    try:
        if user:
            png, data = await maimai_mod.query_to_png(
                username=user,
                token=mm.get("token", ""),
                password=mm.get("password", ""),
                lxns_key=mm.get("lxns_key", ""),
                avatar=avatar,
                theme=mm.get("theme", "dark"),
            )
        else:
            # 没给名字 → 用发送者 QQ 查
            png, data = await maimai_mod.query_to_png(
                qq=qq,
                token=mm.get("token", ""),
                password=mm.get("password", ""),
                lxns_key=mm.get("lxns_key", ""),
                avatar=avatar,
                theme=mm.get("theme", "dark"),
            )
    except Exception as exc:
        log.warning("B50 查询失败 %s：%s", user or f"qq:{qq}", exc)
        if not user:
            # 按 QQ 查不到，多半是没绑定
            await BOT.send_text(
                message_type, target,
                "查不到你的成绩。\n"
                "在水鱼注册好、把 QQ 填上，然后再发一次 /b50 就行：\n"
                "https://www.diving-fish.com/maimaidx/prober/\n"
                "（急着查也可以 /b50 水鱼用户名，或者试试落雪：/lxns）",
                at_qq=at,
            )
        else:
            await BOT.send_text(message_type, target, f"查不到：{exc}", at_qq=at)
        return True

    nickname = data.get("nickname") or user or qq
    rating = data.get("rating")
    await BOT.send_image(message_type, target, png, caption=f"{nickname}　Rating {rating}")
    log.info("[B50] %s → %s (rating %s)", user or f"qq:{qq}", nickname, rating)
    return True


async def _send_b50_image(data: dict[str, Any], qq: str, message_type: str, target: int,
                          tag: str) -> bool:
    """拿到水鱼结构的 data 之后统一出图发送。返回是否发成功。"""
    mm = CFG.get("maimai", {})
    at = int(qq) if message_type == "group" else None
    old_, new_ = maimai_mod.pick_b50(data)
    if not old_ and not new_:
        await BOT.send_text(message_type, target, f"{tag}：这个号还没有成绩。", at_qq=at)
        return False
    try:
        covers = await maimai_mod.fetch_covers(
            [maimai_mod._mid(c) for c in old_ + new_], lxns_key=mm.get("lxns_key", "")
        )
        avatar = await maimai_mod.fetch_qq_avatar(qq)
        plate = await maimai_mod.fetch_plate(str(data.get("plate") or ""))
        png = maimai_mod.render(data, covers, avatar, mm.get("theme", "dark"), plate)
    except Exception as exc:
        log.warning("[%s] 出图失败：%s", tag, exc)
        await BOT.send_text(message_type, target, f"{tag}出图失败：{exc}", at_qq=at)
        return False

    await BOT.send_image(
        message_type, target, png,
        caption=f"{data.get('nickname')}　Rating {data.get('rating')}（{tag}）",
    )
    return True


async def do_wmc_b50(qq: str, text: str, message_type: str, target: int) -> bool:
    """`/wmcb50 <国服 WMC ID>` —— 直接拿好友码查 B50。

    别名 `/sgwmc` `/wmc`。走落雪（它按 friend_code 查），所以号必须先在落雪传过成绩。
    """
    m = WMC_RE.match(text.strip())
    if not m or maimai_mod is None:
        return False

    mm = CFG.get("maimai", {})
    key = mm.get("lxns_key", "")
    at = int(qq) if message_type == "group" else None
    if not key:
        await BOT.send_text(message_type, target, "落雪还没配（`maimai.lxns_key`）。", at_qq=at)
        return True

    wmc = m.group(1)
    if len(wmc) != 15:
        await BOT.send_text(
            message_type, target,
            f"落雪的好友码是 15 位，你给的是 {len(wmc)} 位。\n"
            "（13 位是国际版，落雪不认）",
            at_qq=at,
        )
        return True

    try:
        data = await maimai_mod.fetch_lxns(friend_code=wmc, key=key)
    except Exception as exc:
        log.info("[wmcb50] %s 查询失败：%s", wmc, exc)
        await BOT.send_text(message_type, target, f"落雪：{exc}", at_qq=at)
        return True

    if await _send_b50_image(data, qq, message_type, target, "落雪"):
        log.info("[wmcb50] %s → %s (rating %s)", wmc, data.get("nickname"), data.get("rating"))
    return True


async def do_lxns(qq: str, text: str, message_type: str, target: int) -> bool:
    """落雪查分 —— 跟水鱼完全分开的一条路。

    `/lxns` 查自己（按 QQ），`/lxns 123456789000000` 按 15 位好友码查别人，
    `/lxns 3035888633` 按 QQ 查别人。
    """
    m = LXNS_RE.match(text.strip())
    if not m or maimai_mod is None:
        return False

    mm = CFG.get("maimai", {})
    key = mm.get("lxns_key", "")
    at = int(qq) if message_type == "group" else None
    if not key:
        await BOT.send_text(message_type, target, "落雪还没配（`maimai.lxns_key`）。", at_qq=at)
        return True

    arg = (m.group(1) or "").strip()
    kw: dict[str, Any] = {"key": key}
    if arg:
        if len(arg) >= 13:
            kw["friend_code"] = arg
        else:
            kw["qq"] = arg
    else:
        kw["qq"] = qq

    try:
        data = await maimai_mod.fetch_lxns(**kw)
        old_, new_ = maimai_mod.pick_b50(data)
        if not old_ and not new_:
            raise RuntimeError("这个号在落雪上还没有成绩")
    except Exception as exc:
        log.info("[落雪] %s 查询失败：%s", arg or qq, exc)
        await BOT.send_text(message_type, target, f"落雪：{exc}", at_qq=at)
        return True

    if await _send_b50_image(data, qq, message_type, target, "落雪"):
        log.info("[落雪] %s → %s (rating %s)", arg or f"qq:{qq}",
                 data.get("nickname"), data.get("rating"))
    return True


async def handle_admin(qq: str, text: str) -> None:
    """/stage [qq]  /set [qq] [n]  /note [qq] [内容]  /reset [qq]  /reload"""
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower()
    target = parts[1] if len(parts) > 1 and parts[1].isdigit() else qq
    arg = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and not parts[1].isdigit() else "")

    if cmd in ("/bond", "/羁绊", "/npc"):
        bonds = STORE.meta.setdefault("bonds", {})
        sub = text.split()[1:] if len(text.split()) > 1 else []
        if not sub:
            if not bonds:
                out = "还没有设定任何人的关系。用法：/bond 多洛 3"
            else:
                out = "她和这些人的关系：\n" + "\n".join(
                    f"· {name}：{int(v.get('stage', 1))}（{STAGE_NAMES.get(int(v.get('stage', 1)), '?')}）"
                    + (f" —— {v['note']}" if v.get("note") else "")
                    for name, v in bonds.items()
                )
        elif len(sub) == 1:
            bonds.pop(sub[0], None)
            out = f"已清掉「{sub[0]}」的关系设定"
        else:
            name, val = sub[0], sub[1]
            try:
                n = max(1, min(5, int(val)))
            except ValueError:
                out = "用法：/bond 多洛 3（1-5）"
            else:
                bonds.setdefault(name, {})["stage"] = n
                out = f"「{name}」→ {n}（{STAGE_NAMES.get(n, '?')}）"
        await STORE.save()
        await BOT.send_text("private", int(qq), out)
        return

    if cmd == "/stage":
        u = STORE.user(target)
        out = f"{target} 阶段 {u['stage']}（{STAGE_NAMES.get(int(u['stage']), '?')}），共 {u['msg_count']} 条消息"
    elif cmd == "/set":
        try:
            n = max(1, min(5, int(arg)))
        except (TypeError, ValueError):
            n = 1
        u = STORE.user(target)
        u["stage"] = n
        out = f"{target} 阶段已设为 {n}（{STAGE_NAMES.get(n, '?')}）"
    elif cmd == "/note":
        u = STORE.user(target)
        if arg:
            u["notes"].append(arg)
        out = f"{target} 现在记得 {len(u['notes'])} 条"
    elif cmd == "/reset":
        STORE.users[target] = _blank_user()
        out = f"{target} 记忆已清空"
    elif cmd == "/reload":
        persona.reload()
        out = "角色卡已重载"
    else:
        out = "/help · /stage [qq] · /set [qq] 1-5 · /note [qq] 内容 · /reset [qq] · /reload · /b50 名字"

    await STORE.save()
    await BOT.send_text("private", int(qq), out)

# ---------------------------------------------------------------- 连接

async def handle_request(evt: dict[str, Any]) -> None:
    rt = evt.get("request_type")
    flag = evt.get("flag")
    qq = str(evt.get("user_id", ""))
    f = CFG.get("friend", {})
    if rt == "friend" and f.get("auto_accept"):
        await BOT.call("set_friend_add_request", {"flag": flag, "approve": True})
        # 新加好友的人给一个起步好感，省得每次都从门缝开始
        init = max(1, min(5, int(f.get("initial_stage", 1))))
        u = STORE.user(qq)
        if int(u.get("msg_count", 0)) == 0 and init > 1:
            u["stage"] = init
        await STORE.save()
        log.info("已同意好友申请：%s（好感 %s）", qq, u["stage"])
    elif rt == "group" and f.get("auto_accept_group_invite"):
        await BOT.call(
            "set_group_add_request",
            {"flag": flag, "sub_type": evt.get("sub_type", "invite"), "approve": True},
        )
        log.info("已同意入群邀请：群 %s", evt.get("group_id"))
    elif rt == "group":
        log.info("收到入群邀请（未开启自动同意）：群 %s，来自 %s", evt.get("group_id"), qq)


async def handler(ws, *args) -> None:  # noqa: ANN001
    BOT.use(ws)
    peer = getattr(ws, "remote_address", ("?", 0))
    log.info("QQ 端已连接：%s", peer)
    await BOT.retire_stale(ws)
    if len(BOT.conns) > 1:
        log.warning("还有 %d 条反向 WS 连着（已经尽量踢掉旧的，剩下的等它自己报错）", len(BOT.conns))
    try:
        async for raw in ws:
            BOT.route = ws          # 这条刚送完消息，肯定是活的，回复就走它
            BOT.touch(ws)           # 记一下活跃时间，免得被当成僵尸踢掉
            try:
                evt = json.loads(raw)
            except Exception:
                continue
            if evt.get("self_id"):
                BOT.self_id = str(evt["self_id"])
            echo = evt.get("echo")
            if echo and echo in BOT.pending and "retcode" in evt:
                fut = BOT.pending.get(echo)
                if fut is not None and not fut.done():
                    fut.set_result(evt)
                continue
            pt = evt.get("post_type")
            if pt == "request":
                asyncio.create_task(handle_request(evt))
                continue
            if CFG.get("debug_events") and pt != "meta_event":
                log.info("收到事件 post_type=%s message_type=%s user=%s", pt, evt.get("message_type"), evt.get("user_id"))
            if pt != "message":
                continue
            mt = evt.get("message_type")
            if mt == "group":
                asyncio.create_task(handle_group(evt))
            elif mt == "private":
                asyncio.create_task(handle_private(evt))
    except Exception as exc:
        log.warning("连接断开：%s", exc)
    finally:
        BOT.drop(ws)
        log.info("QQ 端断开：%s（还剩 %d 条连接）", peer, len(BOT.conns))


MENU5C_PUSH_CAPTION = "主人，今天食堂的菜单贝尔都扒好了——起来看一眼？"


async def daily_menu_push_loop() -> None:
    """/5C吃什么 的每日推送：太平洋时间 push_time 给主人私聊发一张当天的。

    配置：config.json → menu5c.push_time（"08:00"；留空就不推）
    """
    cfg = CFG.get("menu5c") or {}
    when = str(cfg.get("push_time") or "").strip()
    if menu5c_mod is None or not when or not CFG.get("master_ids"):
        return
    try:
        hh, mm = [int(x) for x in when.split(":")[:2]]
    except ValueError:
        log.warning("[5C] menu5c.push_time 格式不对：%s（要写成 08:00）", when)
        return

    master = int(CFG["master_ids"][0])
    while True:
        wait = menu5c_mod.seconds_until(hh, mm)
        log.info("[5C] 下一条食堂推送：%.1f 小时后（%s 太平洋时间）", wait / 3600, when)
        await asyncio.sleep(wait + 3)
        try:
            data = await asyncio.to_thread(
                menu5c_mod.collect, 0, None,
                int(cfg.get("per_meal", 6)),
                int(cfg.get("cache_minutes", menu5c_mod.DEFAULT_TTL_MIN)),
            )
            png = await asyncio.to_thread(menu5c_mod.render_card, data)
            if png:
                await BOT.send_image("private", master, png, caption=MENU5C_PUSH_CAPTION)
            else:
                await BOT.send_text("private", master, menu5c_mod.text_of(data))
            log.info("[5C] 每日推送已发：%d 家", len(data["halls"]))
        except Exception as exc:
            log.warning("[5C] 每日推送失败：%s", exc)
        await asyncio.sleep(30)      # 爬起来以后别在同一分钟内连推两次


def _sync_members_md() -> None:
    if members_md is None:
        return
    try:
        names = {str(k): str((v or {}).get("name") or "") for k, v in (STORE.users or {}).items()}
        members_md.write(STORE.members, names)
    except Exception as exc:
        log.warning("[群成员] 写 md 失败：%s", exc)


async def members_flush_loop() -> None:
    """每 5 分钟把「谁在哪个群说过话」落一次盘（欧非榜按群统计用）。"""
    while True:
        await asyncio.sleep(300)
        try:
            if STORE._members_dirty:
                await STORE.save()
                _sync_members_md()
        except Exception as exc:
            log.warning("[members] 落盘失败：%s", exc)


async def main() -> None:
    global LLM_CLIENT
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8")],
    )
    LLM_CLIENT = LLM()
    VISION_CFG.update(CFG.get("vision") or {})
    if vision_mod is not None and vision_mod.enabled(VISION_CFG):
        bk = vision_mod.backend_of(VISION_CFG)
        if bk == "ocr":
            log.info("识图：本地 OCR（RapidOCR）—— 不收钱不联网，只读图里的字 + 判断外形")
        else:
            log.info("识图：%s（%s）—— 私聊收图会先描述再聊天",
                     VISION_CFG.get("model"), VISION_CFG.get("base_url"))
    else:
        log.info("识图：关着（vision.backend = off）")
    ob = CFG["onebot"]
    host, port = ob.get("host", "0.0.0.0"), int(ob.get("port", 8080))
    log.info("角色卡：%s（世界书 %d 条）", CARD.get("name"), worldbook.count())
    log.info("主人在线名单：%s", CFG["master_ids"] or "（空，所有人都按同学处理）")
    log.info("监听 ws://%s:%d%s —— 等待 QQ 协议端连上来", host, port, ob.get("path", "/onebot/v11/ws"))
    try:
        # 不开 websockets 自己的 ping：LuckyLillia / OneBot 端不一定回 pong，
        # 会被误判成 keepalive timeout 然后反复断线重连。它自己有 heartInterval 心跳。
        async with serve(handler, host, port, ping_interval=None, ping_timeout=None):
            asyncio.create_task(daily_menu_push_loop())
            asyncio.create_task(members_flush_loop())
            await asyncio.Future()
    except OSError as exc:
        if getattr(exc, "errno", None) in (48, 98, 10048) or "10048" in str(exc):
            print(
                f"\n端口 {port} 已被占用——多半是上一次的 bot.py 还在后台跑。\n\n"
                f"  看是谁占的：\n"
                f"    Get-NetTCPConnection -LocalPort {port} -State Listen | Select-Object OwningProcess\n\n"
                f"  杀掉它（把 <PID> 换成上面查到的数字）：\n"
                f"    Stop-Process -Id <PID> -Force\n\n"
                f"  或者干脆把 config.json 里的 onebot.port 改成别的（比如 8081），\n"
                f"  同时把 LuckyLillia 的 ws-reverse 地址也跟着改。\n"
            )
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，退出")
