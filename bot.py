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
import json
import logging
import os
import random
import re
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

try:  # websockets >= 13
    from websockets.asyncio.server import serve  # type: ignore
except Exception:  # websockets <= 12
    from websockets import serve  # type: ignore

# ---------------------------------------------------------------- 基础

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"

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
CARD = persona.load_card()["data"]

# 群聊上下文保留条数（70 人活跃群要留多一点，不然刚说的话就被冲掉）
GROUP_WINDOW = max(10, int(CFG.get("group", {}).get("context_window", 20)))


def is_master(qq: str) -> bool:
    try:
        return int(qq) in CFG["master_ids"]
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 状态

_LOCK = asyncio.Lock()


def _blank_user() -> dict[str, Any]:
    return {"stage": 1, "name": "", "history": [], "notes": [], "last_reply": 0.0, "msg_count": 0}


class Store:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, deque] = defaultdict(lambda: deque(maxlen=GROUP_WINDOW))
        self.meta: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.users = raw.get("users", {})
            self.meta = raw.get("meta", {}) or {}
            for gid, rows in (raw.get("groups") or {}).items():
                self.groups[gid] = deque(rows, maxlen=GROUP_WINDOW)
            log.info("已载入 %d 个用户的状态", len(self.users))
        except Exception as exc:
            log.warning("state.json 读失败，从空白开始：%s", exc)

    async def save(self) -> None:
        async with _LOCK:
            tmp = STATE_FILE.with_suffix(".tmp")
            payload = {
                "users": self.users,
                "groups": {g: list(d) for g, d in self.groups.items()},
                "meta": self.meta,
            }
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(STATE_FILE)

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


def ensure_day() -> bool:
    """跨天就把当日花费清零。"""
    if STORE.meta.get("date") != _today():
        STORE.meta = {"date": _today(), "cost": 0.0, "ptok": 0, "ctok": 0, "calls": 0}
        return True
    for k, v in (("cost", 0.0), ("ptok", 0), ("ctok", 0), ("calls", 0)):
        STORE.meta.setdefault(k, v)
    return False


def budget_spent() -> float:
    ensure_day()
    return float(STORE.meta.get("cost", 0.0))


def budget_exhausted() -> bool:
    if not BUDGET.get("enabled", True):
        return False
    return budget_spent() >= float(BUDGET.get("daily_cny", 3.0))


def add_usage(usage: dict[str, Any] | None) -> float:
    """按 DeepSeek 计价累加。缓存命中/未命中分开算。"""
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
    STORE.meta["cost"] = round(float(STORE.meta.get("cost", 0.0)) + cost, 6)
    STORE.meta["ptok"] = int(STORE.meta.get("ptok", 0)) + pt
    STORE.meta["ctok"] = int(STORE.meta.get("ctok", 0)) + ct
    STORE.meta["calls"] = int(STORE.meta.get("calls", 0)) + 1
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
    )

    msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
    msgs += hist
    msgs.append({"role": "user", "content": text if text else "（对你做了个动作）"})
    return msgs

# ---------------------------------------------------------------- 文本处理

def clean_reply(raw: str, ctx: str) -> tuple[str, int]:
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
    cap = int(CFG["style"]["max_chars_group"] if ctx == "群聊" else CFG["style"]["max_chars_private"])
    if len(raw) > cap:
        cut = raw[:cap]
        idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("…"), cut.rfind("」"))
        raw = cut[: idx + 1] if idx > cap * 0.4 else cut

    # 最多 N 句（括号动作也算一句）
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


def split_replies(text: str, ctx: str) -> list[str]:
    """把一次回复切成最多 N 条消息（像真人那样分几条发）。"""
    limit = int(
        CFG["style"].get("max_messages_group" if ctx == "群聊" else "max_messages_private", 3)
    )
    per = int(CFG["style"].get("max_chars_per_message", 45))

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        parts = [p.strip() for p in SENT_RE.findall(text) if p.strip()]
        # 超出的句子直接丢掉——她本来就说不了那么多，不要塞成一大坨
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


def parse_message(segs: list[dict[str, Any]] | None, self_id: str) -> tuple[str, bool]:
    text: list[str] = []
    at_me = False
    for s in segs or []:
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            text.append(d.get("text", ""))
        elif t == "at":
            if str(d.get("qq")) == str(self_id):
                at_me = True
        elif t == "image":
            text.append("[图片]")
        elif t == "face":
            text.append("[表情]")
    joined = re.sub(r"[ \t]+", " ", "".join(text)).strip()
    return joined, at_me

# ---------------------------------------------------------------- OneBot

class OneBot:
    def __init__(self) -> None:
        self.ws = None
        self.self_id = ""

    async def call(self, action: str, params: dict[str, Any]) -> None:
        if self.ws is None:
            log.warning("还没有 QQ 连上来，丢弃：%s", action)
            return
        payload = json.dumps({"action": action, "params": params, "echo": action}, ensure_ascii=False)
        try:
            await self.ws.send(payload)
        except Exception as exc:
            log.warning("发送失败 %s：%s", action, exc)

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
    text, at_me = parse_message(evt.get("message"), self_id)

    if not text or qq == self_id:
        return

    STORE.groups[gid].append(f"{name}：{text}"[:120])

    g = CFG["group"]
    at_only = bool(g.get("at_only", False))

    # at_only=true 时只认 @，连关键词都不理（70 人群里必须这样，不然「贝尔」两个字会让她被刷爆）
    triggered = at_me
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
    if now - USER_LAST[ukey] < cd:
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

    # 今日预算用完 → 回一句就闭嘴，不再调模型（主人不限）
    if not master and budget_exhausted():
        if now - BLOCK_NOTICE[gid] >= float(BUDGET.get("exhausted_group_per_minute", 1)) * 60:
            BLOCK_NOTICE[gid] = now
            await BOT.send_text("group", int(gid), BUDGET.get("exhausted_reply", "明天再来吧"))
            log.info("[群 %s] 今日预算已用完（%.2f 元），回绝", gid, budget_spent())
        return

    STORE.user(qq)["name"] = name

    if await do_b50(qq, text, "group", int(gid)):
        return

    win = int(g.get("context_window", 20))
    recent = [x for x in list(STORE.groups[gid])[:-1]][-win:]
    messages = build_messages(qq, name, "群聊", text, recent)
    async with LLM_SLOTS:
        reply = await LLM_CLIENT.chat(messages)  # type: ignore[union-attr]
        add_usage(getattr(LLM_CLIENT, "last_usage", None))
    if not reply:
        return
    reply, delta = clean_reply(reply, "群聊")
    if not reply:
        return

    u = _commit(qq, text, reply, delta)
    parts = split_replies(reply, "群聊")
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


async def handle_private(evt: dict[str, Any]) -> None:
    self_id = str(evt.get("self_id", ""))
    qq = str(evt.get("user_id", ""))
    name = (evt.get("sender") or {}).get("nickname") or qq
    text, _ = parse_message(evt.get("message"), self_id)
    if qq == self_id:
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
    u["name"] = name

    # b50 要放在 admin 之前——否则 /b50 会被当成管理命令吃掉
    if await do_b50(qq, text, "private", int(qq)):
        return

    # 主人专属管理命令
    if is_master(qq) and text.startswith("/"):
        await handle_admin(qq, text)
        return

    if not master and budget_exhausted():
        await BOT.send_text("private", int(qq), BUDGET.get("exhausted_reply", "明天再来吧"))
        log.info("[私聊] %s 今日预算已用完（%.2f 元），回绝", name, budget_spent())
        return

    messages = build_messages(qq, name, "私聊", text or "（没说话）", None)
    async with LLM_SLOTS:
        reply = await LLM_CLIENT.chat(messages)  # type: ignore[union-attr]
        add_usage(getattr(LLM_CLIENT, "last_usage", None))
    if not reply:
        return
    reply, delta = clean_reply(reply, "私聊")
    if not reply:
        return

    u = _commit(qq, text or "（没说话）", reply, delta)
    parts = split_replies(reply, "私聊")
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


# ---------------------------------------------------------------- 舞萌 B50

B50_RE = re.compile(r"^[/!！]?\s*b50\s*(.*)$", re.I)


async def do_b50(qq: str, text: str, message_type: str, target: int) -> bool:
    """处理 b50 查询。返回 True 表示这条消息已经被它吃掉了。"""
    m = B50_RE.match(text.strip())
    if not m or maimai_mod is None:
        return False

    mm = CFG.get("maimai", {})
    if not mm.get("enable", True):
        return False

    user = m.group(1).strip()
    if not user:
        um = mm.get("user_map") or {}
        if isinstance(um, dict):
            user = str(um.get(str(qq), "") or "")
        elif isinstance(um, str):
            # 有的人会直接把名字写在 user_map 上，兼容一下
            user = um
    if not user:
        await BOT.send_text(
            message_type, target,
            "要先告诉贝尔名字哦，比如「b50 你的水鱼名」",
            at_qq=int(qq) if message_type == "group" else None,
        )
        return True

    try:
        png, data = await maimai_mod.query_to_png(
            username=user,
            token=mm.get("token", ""),
            password=mm.get("password", ""),
        )
    except Exception as exc:
        log.warning("B50 查询失败 %s：%s", user, exc)
        await BOT.send_text(
            message_type, target, f"查不到：{exc}",
            at_qq=int(qq) if message_type == "group" else None,
        )
        return True

    nickname = data.get("nickname") or user
    rating = data.get("rating")
    await BOT.send_image(message_type, target, png, caption=f"{nickname}　Rating {rating}")
    log.info("[B50] %s → %s (rating %s)", user, nickname, rating)
    return True


async def handle_admin(qq: str, text: str) -> None:
    """/stage [qq]  /set [qq] [n]  /note [qq] [内容]  /reset [qq]  /reload"""
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower()
    target = parts[1] if len(parts) > 1 and parts[1].isdigit() else qq
    arg = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and not parts[1].isdigit() else "")

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
        out = "/stage [qq] · /set [qq] 1-5 · /note [qq] 内容 · /reset [qq] · /reload · /b50 名字"

    await STORE.save()
    await BOT.send_text("private", int(qq), out)

# ---------------------------------------------------------------- 连接

async def handle_request(evt: dict[str, Any]) -> None:
    rt = evt.get("request_type")
    flag = evt.get("flag")
    f = CFG.get("friend", {})
    if rt == "friend" and f.get("auto_accept"):
        await BOT.call("set_friend_add_request", {"flag": flag, "approve": True})
        log.info("已同意好友申请：%s", evt.get("user_id"))
    elif rt == "group" and f.get("auto_accept_group_invite"):
        await BOT.call(
            "set_group_add_request",
            {"flag": flag, "sub_type": evt.get("sub_type", "invite"), "approve": True},
        )
        log.info("已同意入群邀请：群 %s", evt.get("group_id"))
    elif rt == "group":
        log.info("收到入群邀请（未开启自动同意）：群 %s，来自 %s", evt.get("group_id"), evt.get("user_id"))


async def handler(ws, *args) -> None:  # noqa: ANN001
    BOT.ws = ws
    peer = getattr(ws, "remote_address", ("?", 0))
    log.info("QQ 端已连接：%s", peer)
    try:
        async for raw in ws:
            try:
                evt = json.loads(raw)
            except Exception:
                continue
            if evt.get("self_id"):
                BOT.self_id = str(evt["self_id"])
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
        if BOT.ws is ws:
            BOT.ws = None
        log.info("QQ 端断开：%s", peer)


async def main() -> None:
    global LLM_CLIENT
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8")],
    )
    LLM_CLIENT = LLM()
    ob = CFG["onebot"]
    host, port = ob.get("host", "0.0.0.0"), int(ob.get("port", 8080))
    log.info("角色卡：%s（世界书 %d 条）", CARD.get("name"), worldbook.count())
    log.info("主人在线名单：%s", CFG["master_ids"] or "（空，所有人都按同学处理）")
    log.info("监听 ws://%s:%d%s —— 等待 QQ 协议端连上来", host, port, ob.get("path", "/onebot/v11/ws"))
    try:
        # 不开 websockets 自己的 ping：LuckyLillia / OneBot 端不一定回 pong，
        # 会被误判成 keepalive timeout 然后反复断线重连。它自己有 heartInterval 心跳。
        async with serve(handler, host, port, ping_interval=None, ping_timeout=None):
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
