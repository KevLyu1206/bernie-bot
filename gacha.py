# -*- coding: utf-8 -*-
"""曲绘抽卡（demo）—— 卡池是 maimai + phigros 的曲绘，难度越高越稀有。

    import gacha
    card = gacha.draw("3035888633", "2026-11-23")   # 确定性：同一人同一天同一张
    png = gacha.render_card(card)

稀有度怎么来：**各来源内部按难度分位切档**，不硬写阈值。
每种来源自己按难度从低到高排序，按 40/27/22/8/3 切 N/R/SR/SSR/UR ——
这样 maimai 的 UR 一定是它自己最难的 3%，phigros 的 UR 也是，不会因为
phigros 的 IN 数值天生比 maimai 定数大就全挤到高档。

抽取：先按 40/27/22/8/3 roll 稀有度，再在该档的池子里等概率抽一张。
"""
from __future__ import annotations

import hashlib
import io
import math
import random
import re
import shutil
import time

import httpx
from pathlib import Path
from typing import Any

import fortune as F

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont
except Exception:  # 没 Pillow 就只出文字
    Image = ImageDraw = ImageEnhance = ImageFont = None  # type: ignore

import json

BASE_DIR = Path(__file__).resolve().parent
PHIGROS_JSON = BASE_DIR / "phigros" / "songs.json"
SAVE_FILE = BASE_DIR / "gacha.json"      # 收藏 + 每天抽了几次
SAVE_BAK = BASE_DIR / "gacha.json.bak"   # 上一版的备份，主文件坏了自动读它

RARITIES: list[tuple[str, float]] = [("N", 0.40), ("R", 0.27), ("SR", 0.22), ("SSR", 0.08), ("UR", 0.03)]
RARITY_COLOR = {
    "N": (142, 142, 147),      # 灰
    "R": (74, 144, 226),       # 蓝
    "SR": (175, 82, 222),      # 紫
    "SSR": (255, 149, 0),      # 橙
    "UR": (216, 62, 62),       # 红
}
RARITY_STARS = {"N": "★", "R": "★★", "SR": "★★★", "SSR": "★★★★", "UR": "★★★★★"}
RARITY_SCORE = {"N": 1, "R": 64, "SR": 4096, "SSR": 262144, "UR": 16777216}   # 64 进制：同抽数下高档永远压过低档   # 算欧气值用

SOURCE_NAME = {"maimai": "maimai", "phigros": "Phigros"}

_POOL: dict[str, list[dict[str, Any]]] | None = None


# ---------------------------------------------------------------- 建池

def _rarity_maimai(value: float) -> str:
    """maimai 按最高定数：14.8+ 才算 UR，12.5 以下是 N。"""
    if value >= 14.8:
        return "UR"
    if value >= 14.3:
        return "SSR"
    if value >= 13.6:
        return "SR"
    if value >= 12.6:
        return "R"
    return "N"


def _rarity_phigros(in_lv: float, at_lv: float | None) -> str:
    """phigros 取「有 AT 用 AT，没有用 IN」的最高难度值来分档。

    阈值比 maimai 高，因为 phigros 谱面整体更硬：IN 16 才是主流、17 是极限。
    """
    lv = at_lv or in_lv
    if lv >= 17.0:
        return "UR"
    if lv >= 16.0:
        return "SSR"
    if lv >= 15.0:
        return "SR"
    if lv >= 13.0:
        return "R"
    return "N"


def _maimai_cards() -> list[dict[str, Any]]:
    try:
        import songdb
    except Exception:
        return []
    try:
        if not songdb.DB.all:
            songdb.DB.load()
    except Exception:
        return []
    cards: list[dict[str, Any]] = []
    for s in songdb.DB.all or []:
        try:
            ds = [d for d in songdb.DB.difficulties(int(s["id"]))
                  if d.get("value") and d.get("level") not in (None, "?")]
        except Exception:
            continue
        if not ds:
            continue
        best = max(ds, key=lambda d: float(d["value"]))
        cover = F._cover_path(int(s["id"]))
        if cover is None:
            continue
        value = float(best["value"])
        cards.append({
            "source": "maimai",
            "title": s.get("title") or f"#{s['id']}",
            "artist": s.get("artist") or "未知",
            "power": value,
            "level": str(best.get("level") or best["value"]),
            "diff": F.DIFF_CN.get(best.get("index", 3), "?"),
            "rarity": _rarity_maimai(value),
            "cover": cover,
        })
    return cards


def _phigros_cards() -> list[dict[str, Any]]:
    try:
        rows = json.loads(PHIGROS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    cards: list[dict[str, Any]] = []
    for r in rows:
        cover = F.PHIGROS_COVER_DIR / str(r.get("cover") or "")
        if not cover.exists():
            continue
        try:
            in_lv = float(r.get("in_level") or 0)
        except (TypeError, ValueError):
            continue
        at_raw = r.get("at_level") or ""
        try:
            at_lv = float(at_raw) if at_raw else None
        except ValueError:
            at_lv = None
        lv = at_lv or in_lv
        if lv <= 0:
            continue
        cards.append({
            "source": "phigros",
            "title": r.get("title") or "?",
            "artist": r.get("artist") or "未知",
            "power": lv,
            "level": str(at_raw or r.get("in_level")),
            "diff": "AT" if at_lv else "IN",
            "rarity": _rarity_phigros(in_lv, at_lv),
            "cover": cover,
        })
    return cards


def pool() -> dict[str, list[dict[str, Any]]]:
    """{稀有度: [卡]}；两个来源用各自的难度阈值分档后合并。"""
    global _POOL
    if _POOL is not None:
        return _POOL
    merged: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in RARITIES}
    for card in _maimai_cards() + _phigros_cards():
        merged[card["rarity"]].append(card)
    _POOL = merged
    return _POOL


def stats() -> dict[str, int]:
    return {name: len(v) for name, v in pool().items()}


def all_cards() -> list[dict[str, Any]]:
    return [c for name, _ in RARITIES for c in pool().get(name, [])]


def card_key(card: dict[str, Any]) -> str:
    """收藏用的唯一键：来源 + 曲名。

    **这个格式不要改** —— 一改所有人的老收藏就全对不上了。
    真要改名，走 `_norm_title()` 的松匹配兜底（曲名对得上就能认回来）。
    """
    return f"{card['source']}|{card['title']}"


def _norm_title(s: str) -> str:
    """曲名归一化：小写、去掉空格/标点/下划线，用来在 key 对不上时认回老卡。"""
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def by_key() -> dict[str, dict[str, Any]]:
    return {card_key(c): c for c in all_cards()}


def by_title() -> dict[str, dict[str, Any]]:
    """归一化曲名 → 卡；曲库刷新后 key 变了也能靠它对上。"""
    out: dict[str, dict[str, Any]] = {}
    for c in all_cards():
        out.setdefault(_norm_title(c["title"]), c)
    return out


# ---------------------------------------------------------------- 存档（收藏 + 每日次数）

def _load_save() -> dict[str, Any]:
    """读存档：主文件坏了就退回 `.bak`，两个都坏才当空档（**不会主动清空任何人的卡**）。"""
    for path in (SAVE_FILE, SAVE_BAK):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            data.setdefault("users", {})
            data.setdefault("daily", {})
            return data
    return {"users": {}, "daily": {}}


def _save_save(data: dict[str, Any]) -> None:
    """先备份当前文件到 `.bak`，再写 `.tmp` → `replace`。

    `.bak` 永远是「上一次写盘前的好版本」；主文件被写坏/被外部截断时读它。
    中途断电最多丢这一次的抽卡。
    """
    if SAVE_FILE.exists():
        try:
            shutil.copyfile(SAVE_FILE, SAVE_BAK)
        except Exception:
            pass
    tmp = SAVE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SAVE_FILE)


def owned(qq: str) -> list[str]:
    """这个人持有卡（key 列表），按拿到顺序。"""
    return list((_load_save().get("users") or {}).get(str(qq)) or [])


# ---------------------------------------------------------------- 欧非统计

def _blank_stat() -> dict[str, int]:
    rec: dict[str, int] = {"n": 0, "score": 0}
    for name, _ in RARITIES:
        rec[name] = 0
    return rec


def _stat_of(data: dict[str, Any], qq: str) -> dict[str, int]:
    stats = data.setdefault("stats", {})
    st = stats.get(str(qq))
    if not isinstance(st, dict):
        st = _blank_stat()
        stats[str(qq)] = st
    for k, v in _blank_stat().items():
        st.setdefault(k, v)
    return st


def _ensure_stats(data: dict[str, Any]) -> dict[str, Any]:
    """老存档没有 stats 时，用收藏里的卡补一份（去重后的，只能算近似）。

    只是补统计，**不动 `users` 的一条记录**；补过的不会被重算。
    """
    stats = data.setdefault("stats", {})
    idx = by_key()
    for qq, keys in (data.get("users") or {}).items():
        if str(qq) in stats:
            continue
        st = _blank_stat()
        for k in keys:
            card = idx.get(k)
            if card is None:
                continue
            st["n"] += 1
            st["score"] += RARITY_SCORE.get(card["rarity"], 1)
            st[card["rarity"]] = int(st.get(card["rarity"], 0)) + 1
        stats[str(qq)] = st
    return data


def luck_of(stat: dict[str, int]) -> float:
    """欧气值 0-100（对数刻度）。

    权重按 64 进制：N=1 / R=64 / SR=4096 / SSR=262144 / UR=16777216，所以
    **同抽数**下高档一定压过低档 —— SSR 一人也顶得过一堆 SR：
        SSR+N+N+N(65536/4) > SR*2+N+R(2064/4)
        UR+N(8.4M/2)      > SSR*2(262144/2)
    显示时取 log10 压到 0-100：全是 N = 0，全是 UR = 100，单张 SR≈50、SSR≈75。
    """
    n = int(stat.get("n") or 0)
    if n <= 0:
        return 0.0
    weighted = sum(RARITY_SCORE[k] * int(stat.get(k) or 0) for k in RARITY_SCORE)
    if weighted <= 1:                       # 极老存档没有分档计数时，按 score 字段估
        weighted = max(1, int(stat.get("score") or 0)) * n
    avg = float(weighted) / n
    if avg <= 1:
        return 0.0
    top = float(RARITY_SCORE["UR"])
    return max(0.0, min(100.0, 100.0 * math.log10(avg) / math.log10(top)))


def luck_table(min_pulls: int = 1) -> list[dict[str, Any]]:
    """[{qq, name, n, score, luck, UR, SSR, SR, R, N}]，按欧气降序。"""
    data = _load_save()
    before = len(data.get("stats") or {})
    data = _ensure_stats(data)
    if len(data.get("stats") or {}) > before:
        _save_save(data)          # 迁移结果落一次盘，之后就不用重算了
    rows: list[dict[str, Any]] = []
    for qq, st in (data.get("stats") or {}).items():
        n = int(st.get("n") or 0)
        if n < min_pulls:
            continue
        rows.append({
            "qq": str(qq),
            "name": str(st.get("name") or ""),
            "n": n,
            "score": int(st.get("score") or 0),
            "luck": luck_of(st),
            "UR": int(st.get("UR") or 0),
            "SSR": int(st.get("SSR") or 0),
            "SR": int(st.get("SR") or 0),
            "R": int(st.get("R") or 0),
            "N": int(st.get("N") or 0),
        })
    rows.sort(key=lambda r: (-r["luck"], -r["n"], r["qq"]))
    return rows


def owned_cards(qq: str) -> list[dict[str, Any]]:
    """持有卡对象。

    先按 key 精确找；找不到再用归一化曲名认一次 —— 这样即使以后有人改了卡 key 的格式、
    或者曲目名多了个空格/符号，老收藏也不会凭空消失（只是那一条找不到对应卡时才会跳过）。
    """
    idx = by_key()
    titles = None
    out: list[dict[str, Any]] = []
    for k in owned(qq):
        card = idx.get(k)
        if card is None:
            if titles is None:
                titles = by_title()
            title = k.split("|", 1)[-1] if "|" in k else k
            card = titles.get(_norm_title(title))
        if card is not None:
            out.append(card)
    return out


def used_today(qq: str, day: str | None = None) -> int:
    day = day or F.beijing_day()
    rec = (_load_save().get("daily") or {}).get(str(qq)) or {}
    return int(rec.get("n") or 0) if rec.get("day") == day else 0


def take(qq: str, day: str | None = None, limit: int = 2, per_draw: int = 2,
         name: str = "") -> dict[str, Any]:
    """抽一次：**一次给 per_draw 张，每天共 limit 张**，两张一起发。

    返回 {"cards": [...], "new": [bool...], "used", "limit"}；额度用完 cards 为空。
    `name` 传了就把昵称记进 stats，欧非榜直接用，不依赖 state.json。
    """
    day = day or F.beijing_day()
    data = _ensure_stats(_load_save())
    rec = (data.get("daily") or {}).get(str(qq)) or {}
    used = int(rec.get("n") or 0) if rec.get("day") == day else 0
    if name:
        st0 = _stat_of(data, str(qq))
        if st0.get("name") != name:
            st0["name"] = name
            _save_save(data)
    left = max(0, limit - used)
    if left <= 0:
        return {"cards": [], "new": [], "used": used, "limit": limit}

    cards: list[dict[str, Any]] = []
    for i in range(min(per_draw, left)):
        c = draw(qq, day, used + i)
        if c is not None:
            cards.append(c)
    if not cards:
        return {"cards": [], "new": [], "used": used, "limit": limit}

    users = data.setdefault("users", {})
    xs = users.setdefault(str(qq), [])
    news: list[bool] = []
    for c in cards:
        key = card_key(c)
        is_new = key not in xs
        if is_new:
            xs.append(key)
        news.append(is_new)

    # 欧非统计：总抽数 + 加权分 + 各档计数（**只加不减**）
    st = _stat_of(data, str(qq))
    for c in cards:
        st["n"] += 1
        st["score"] += RARITY_SCORE.get(c["rarity"], 1)
        st[c["rarity"]] = int(st.get(c["rarity"], 0)) + 1

    data.setdefault("daily", {})[str(qq)] = {"day": day, "n": used + len(cards)}
    _save_save(data)
    return {"cards": cards, "new": news, "used": used + len(cards), "limit": limit}


# ---------------------------------------------------------------- 抽卡

def _seed(qq: str, day: str, n: int = 0) -> int:
    raw = f"gacha|{day}|{qq}" + (f"|{n}" if n else "")
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)


def draw(qq: str, day: str | None = None, seq: int = 0) -> dict[str, Any] | None:
    """同一人同一天第 seq 次抽的卡固定；day 不传就用北京日期。"""
    day = day or F.beijing_day()
    p = pool()
    if not any(p.values()):
        return None
    rng = random.Random(_seed(str(qq), day, seq))
    names = [n for n, _ in RARITIES]
    weights = [w for _, w in RARITIES]
    # 空档就按剩余权重重挑
    while True:
        name = rng.choices(names, weights=weights, k=1)[0]
        if p.get(name):
            break
        i = names.index(name)
        names.pop(i)
        weights.pop(i)
        if not names:
            return None
    return dict(rng.choice(p[name]))


def text_of(card: dict[str, Any]) -> str:
    return (
        f"【{card['rarity']}】{RARITY_STARS[card['rarity']]}\n"
        f"{card['title']}\n"
        f"曲师：{card['artist']}\n"
        f"{SOURCE_NAME.get(card['source'], card['source'])} · {card['diff']} {card['level']}"
    )


# ---------------------------------------------------------------- 欧非榜

# ---------------------------------------------------------------- 昵称排版（中韩混排）

KR_RANGES = ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7A3))   # 韩文字母 / 兼容字母 / 音节


def _is_kr(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in KR_RANGES)


def _kr_font(size: int, bold: bool = False) -> Any:
    """韩文用 Malgun Gothic —— 微软雅黑根本没有韩文字形，只能分字体画。"""
    if ImageFont is None:
        return None
    names = ["malgunbd.ttf", "malgun.ttf"] if bold else ["malgun.ttf", "malgunbd.ttf"]
    for n in names:
        p = F.FONT_DIR / n
        if not p.exists():
            continue
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return None


def _mixed_width(d: Any, text: str, f_main: Any, f_kr: Any) -> float:
    total = 0.0
    for ch in text:
        f = f_kr if (f_kr is not None and _is_kr(ch)) else f_main
        total += d.textlength(ch, font=f)
    return total


def _clip_mixed(d: Any, text: str, f_main: Any, f_kr: Any, max_w: float) -> str:
    if _mixed_width(d, text, f_main, f_kr) <= max_w:
        return text
    out = ""
    for ch in text:
        trial = out + ch + "…"
        if _mixed_width(d, trial, f_main, f_kr) > max_w:
            break
        out += ch
    return (out + "…") if out else text[:1]


def _draw_mixed(d: Any, xy: tuple[int, int], text: str, f_main: Any, f_kr: Any,
                fill: Any) -> None:
    """逐字画：中文走雅黑、韩文走 Malgun，谁都别想画成方框。"""
    x, y = xy
    for ch in text:
        f = f_kr if (f_kr is not None and _is_kr(ch)) else f_main
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f)


def _clean_name(s: str, limit: int = 14) -> str:
    """昵称清洗：**保留韩文**（渲染时换字体），只剔 emoji / 扩展汉字这些真画不出来的。"""
    out: list[str] = []
    for ch in (s or ""):
        o = ord(ch)
        if o < 0x2500:                  # ASCII / 拉丁 / 希腊 / 常见标点
            out.append(ch)
        elif 0x3000 <= o <= 0x9FFF:      # CJK 标点 + 假名 + 统一表意
            out.append(ch)
        elif 0xAC00 <= o <= 0xD7A3:      # 韩文音节（Malgun 能画）
            out.append(ch)
        elif 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            out.append(ch)
        elif 0xFF00 <= o <= 0xFFEF:      # 全角
            out.append(ch)
        # 其余（emoji、扩展 B 汉字…）丢掉
    txt = "".join(out).strip()
    return (txt[:limit] + "…") if len(txt) > limit else (txt or "?")


def backfill_names(names: dict[str, str] | None) -> None:
    """把外面拿到的昵称补进 stats（只补已有的、只补空的）。

    **不给没抽过卡的人建条目** —— stats 里只该有真正抽过卡的人。
    """
    if not names:
        return
    data = _ensure_stats(_load_save())
    stats = data.get("stats") or {}
    changed = False
    for qq, nm in names.items():
        st = stats.get(str(qq))
        if not isinstance(st, dict) or not nm:
            continue
        if not st.get("name"):
            st["name"] = _clean_name(nm)
            changed = True
    if changed:
        _save_save(data)


def prune_stats() -> int:
    """清掉 `n == 0` 的空统计条目（历史遗留），返回删了几条。"""
    data = _ensure_stats(_load_save())
    stats = data.get("stats") or {}
    empty = [k for k, v in stats.items() if not isinstance(v, dict) or int(v.get("n") or 0) <= 0]
    for k in empty:
        stats.pop(k, None)
    if empty:
        _save_save(data)
    return len(empty)


def _luck_color(luck: float) -> tuple[int, int, int]:
    # 新刻度（64 进制 + 对数）下：单 UR≈100 / 单 SSR≈75 / 单 SR≈50 / 单 R≈25
    if luck >= 85:
        return RARITY_COLOR["SSR"]
    if luck >= 60:
        return RARITY_COLOR["SR"]
    if luck >= 35:
        return RARITY_COLOR["R"]
    return RARITY_COLOR["N"]


def render_luck_rank(names: dict[str, str] | None = None, top: int = 5) -> tuple[bytes | None, str]:
    """欧非榜：上半最欧、下半最非。返回 (图, 说明)。"""
    if Image is None:
        return None, "没有 Pillow，画不出欧非榜。"
    rows = luck_table(min_pulls=1)
    if not rows:
        return None, "还没有人抽过卡，先发「抽卡」。"

    names = names or {}
    total_pulls = sum(r["n"] for r in rows)

    # 两榜不重叠：按人数决定每榜几个，最多 top，最少 1
    k = max(1, min(top, len(rows) // 2 or 1))
    best = rows[:k]
    best_q = {r["qq"] for r in best}
    worst = list(reversed([r for r in rows if r["qq"] not in best_q]))[:k]

    W, M = 760, 32
    row_h, sec_h = 68, 46
    body_top = 132
    half = sec_h + max(len(best), len(worst)) * row_h
    body_bottom = body_top + 18 + half * 2 + 10
    H = body_bottom + 106

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    f_label = F._font(17, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_tiny = F._font(12, False, "cn")
    f_sec = F._font(22, True, "cn")
    f_rank = F._font(24, True, "num", heavy=True)
    f_name = F._font(19, True, "cn")
    f_name_kr = _kr_font(19, bold=True)
    f_luck = F._font(26, True, "num", heavy=True)

    img = F._panel(img, (M, 24, W - M, 116), 24)
    d = ImageDraw.Draw(img)
    d.text((M + 28, 40), "欧非榜", font=f_label, fill=F.TEXT)
    d.text((W - M - 28, 44), f"{len(rows)} 人 · 共 {total_pulls} 抽", font=f_sub,
           fill=F.SOFT, anchor="ra")
    d.text((M + 28, 72), "欧气 = 平均稀有度换算，0 是全是 N，100 是全是 UR",
           font=f_sub, fill=F.SOFT)

    img = F._panel(img, (M, body_top, W - M, body_bottom), 24)
    d = ImageDraw.Draw(img)

    x_rank, x_name, x_luck = M + 24, M + 70, W - M - 26

    def draw_section(title: str, sub: str, data: list[dict[str, Any]], y: int,
                     color: tuple[int, int, int]) -> int:
        d.text((x_rank, y), title, font=f_sec, fill=color)
        d.text((W - M - 24, y + 6), sub, font=f_tiny, fill=F.SOFT, anchor="ra")
        y += sec_h
        if not data:
            d.text((x_rank, y + 8), "还没有人。", font=f_sub, fill=F.SOFT)
            return y + row_h
        for i, r in enumerate(data, 1):
            rc = _luck_color(r["luck"])
            d.text((x_rank, y + 12), str(i), font=f_rank, fill=F.SOFT)
            # 昵称优先级：外部传的 > gacha.json 里记的 > QQ 尾号
            who_raw = names.get(r["qq"]) or r.get("name") or ""
            who = _clean_name(who_raw) if who_raw else f"…{r['qq'][-4:]}"
            who = _clip_mixed(d, who, f_name, f_name_kr, 380)
            _draw_mixed(d, (x_name, y + 6), who, f_name, f_name_kr, F.TEXT)
            d.text((x_name, y + 32),
                   f"UR {r['UR']} · SSR {r['SSR']} · SR {r['SR']} · 共 {r['n']} 抽",
                   font=f_tiny, fill=F.SOFT)
            d.text((x_luck, y + 8), f"{r['luck']:.0f}", font=f_luck, fill=rc, anchor="ra")
            # 进度条
            bar_x0, bar_x1 = x_name, x_luck - 46
            by = y + 52
            d.rounded_rectangle((bar_x0, by, bar_x1, by + 5), radius=3, fill=F.FILL)
            frac = max(0.0, min(1.0, r["luck"] / 100))
            if frac > 0:
                d.rounded_rectangle((bar_x0, by, bar_x0 + max(6, int((bar_x1 - bar_x0) * frac)),
                                     by + 5), radius=3, fill=rc)
            y += row_h
        return y

    y = body_top + 18
    y = draw_section("欧榜", "平均稀有度最高", best, y, RARITY_COLOR["SSR"])
    y += 14
    d.line([(x_rank, y), (W - M - 24, y)], fill=F.LINE, width=1)
    y += 16
    y = draw_section("非榜", "抽得最多但运气最差", worst, y, RARITY_COLOR["N"])

    d.line([(M, H - 46), (W - M, H - 46)], fill=F.LINE, width=1)
    d.text((M, H - 34), "贝尔娜提塔的欧非榜", font=f_tiny, fill=F.SOFT)
    d.text((W - M, H - 34), f"{F.beijing_stamp()} 北京时间", font=f_tiny, fill=F.SOFT, anchor="ra")

    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True, progressive=True)
    return buf.getvalue(), ""


# ---------------------------------------------------------------- 欧气榜（带头像）

_AVATAR_CACHE: dict[str, Any] = {}          # qq → RGBA 图 / None（拉不到）
_AVATAR_TTL = 6 * 3600


def _avatar(qq: str, size: int = 56) -> Any:
    """QQ 头像：内存缓存 6 小时，拉不到返回 None（调用方画灰圆）。"""
    now = time.time()
    hit = _AVATAR_CACHE.get(qq)
    if hit and now - hit[0] < _AVATAR_TTL:
        return hit[1]
    img = None
    for url in (f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100",
                f"https://q.qlogo.cn/headimg_dl?dst_uin={qq}&spec=100"):
        try:
            r = httpx.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or len(r.content) < 500:
                continue
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        im = im.resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        img = im.convert("RGBA")
        img.putalpha(mask)
        break
    _AVATAR_CACHE[qq] = (now, img)
    return img


def render_luck_board(rows: list[dict[str, Any]], names: dict[str, str] | None = None,
                      top: int = 10, scope: str = "") -> tuple[bytes | None, str]:
    """运气 TOP N：名次 + 头像 + 昵称 + 欧气分 + 分布条。无名的不上榜。"""
    if Image is None:
        return None, "没有 Pillow，画不出榜单。"
    names = names or {}
    picks = []
    for r in rows:
        who = (names.get(r["qq"]) or r.get("name") or "").strip()
        if not who:
            continue
        r = dict(r)
        r["who"] = _clean_name(who)
        picks.append(r)
    picks.sort(key=lambda r: (-r["luck"], -r["n"], r["qq"]))
    picks = picks[: max(1, top)]
    if not picks:
        return None, "本群还没人有昵称的卡，先发「抽卡」吧。"

    total_pulls = sum(r["n"] for r in picks)
    W, M = 760, 32
    row_h, top_h, foot = 84, 132, 56
    H = 22 + top_h + 14 + len(picks) * (row_h + 10) + foot

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    f_label = F._font(17, True, "cn")
    f_title = F._font(34, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_rank = F._font(26, True, "num", heavy=True)
    f_name = F._font(19, True, "cn")
    f_name_kr = _kr_font(19, bold=True)
    f_luck = F._font(30, True, "num", heavy=True)
    f_unit = F._font(11, False, "cn")
    f_meta = F._font(12, False, "cn")
    f_tiny = F._font(11, False, "cn")

    img = F._panel(img, (M, 22, W - M, 22 + top_h), 28)
    d = ImageDraw.Draw(img)
    d.text((M + 26, 40), f"欧气榜 · {scope or '全服'} · 同抽数看稀有度 · 无名不计", font=f_label, fill=F.SUB)
    d.text((M + 26, 62), f"运气 TOP {len(picks)}", font=f_title, fill=F.TEXT)
    d.text((W - M - 26, 44), f"{len(picks)} 人 · 共 {total_pulls} 抽", font=f_sub, fill=F.SOFT, anchor="ra")
    d.text((W - M - 26, 66), "100 = 全是 UR，0 = 全是 N", font=f_tiny, fill=F.SOFT, anchor="ra")
    d.line([(M + 26, 104), (W - M - 26, 104)], fill=F.FILL, width=1)

    y = 22 + top_h + 14
    for i, r in enumerate(picks, 1):
        color = _luck_color(r["luck"])
        img = F._panel(img, (M, y, W - M, y + row_h), 20)
        d = ImageDraw.Draw(img)

        d.text((M + 28, y + row_h / 2), str(i), font=f_rank,
               fill=F.TEXT if i <= 3 else F.SOFT, anchor="lm")

        ax = M + 66
        av = _avatar(r["qq"], 56)
        if av is not None:
            img.paste(av, (ax, y + 14), av)
        else:
            d.ellipse((ax, y + 14, ax + 56, y + 70), fill=F.FILL)
            d.text((ax + 28, y + 42), r["who"][:1], font=f_name, fill=F.SOFT, anchor="mm")

        nx = ax + 56 + 18
        who = _clip_mixed(d, r["who"], f_name, f_name_kr, 300)
        _draw_mixed(d, (nx, y + 18), who, f_name, f_name_kr, F.TEXT)
        d.text((nx, y + 44),
               f"UR {r['UR']} · SSR {r['SSR']} · SR {r['SR']} · 共 {r['n']} 抽",
               font=f_meta, fill=F.SOFT)

        bar_x0, bar_x1 = nx, W - M - 96
        by = y + 66
        d.rounded_rectangle((bar_x0, by, bar_x1, by + 6), radius=3, fill=F.FILL)
        frac = max(0.0, min(1.0, r["luck"] / 100))
        if frac:
            d.rounded_rectangle((bar_x0, by, bar_x0 + max(8, int((bar_x1 - bar_x0) * frac)), by + 6),
                                radius=3, fill=color)

        d.text((W - M - 24, y + 22), f"{r['luck']:.0f}", font=f_luck, fill=color, anchor="ra")
        d.text((W - M - 24, y + 56), "分", font=f_unit, fill=F.SOFT, anchor="ra")

        y += row_h + 10

    d.line([(M, H - 44), (W - M, H - 44)], fill=F.LINE, width=1)
    d.text((M, H - 32), "贝尔娜提塔统计的 · 无名不计", font=f_tiny, fill=F.SOFT)
    d.text((W - M, H - 32), F.beijing_stamp() + " 北京时间", font=f_tiny, fill=F.SOFT, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90, optimize=True, progressive=True)
    return buf.getvalue(), ""


# ---------------------------------------------------------------- 卡包 / 卡池（小曲绘墙）

COLS = 7
CELL = 82
GAP = 12
PER_PAGE = 84            # 卡池：7 列 × 12 行；1628 张 ≈ 20 页
PER_PAGE_BAG = 40        # 卡包：每 40 张一页
BAG_SEC_H = 38           # 卡包里每个稀有度的小标题高度


def _thumb(path: Path, size: int, dim: bool = False) -> Any:
    """小方块缩略图：居中裁方、切圆角；dim=True 时转灰并压暗（没抽到的卡）。"""
    if Image is None:
        return None
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    im = im.resize((size, size), Image.LANCZOS)
    if dim:
        im = im.convert("L").convert("RGB")
        im = ImageEnhance.Brightness(im).enhance(0.45)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=12, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def _paint_grid(img: Any, d: Any, cards: list[dict[str, Any]], have: set[str] | None,
                top: int) -> int:
    """把一堆卡画成网格，返回底边 y。have 为 None 表示全亮，否则不在 have 里的画灰。"""
    x0 = 32 + 24
    rows = max(1, (len(cards) + COLS - 1) // COLS)
    for i, card in enumerate(cards):
        r, c = divmod(i, COLS)
        x = x0 + c * (CELL + GAP)
        y = top + r * (CELL + GAP)
        dark = have is not None and card_key(card) not in have
        cov = _thumb(Path(card["cover"]), CELL, dim=dark)
        if cov is not None:
            img.paste(cov, (x, y), cov)
        else:
            d.rounded_rectangle((x, y, x + CELL, y + CELL), radius=12, fill=F.FILL)
    return top + rows * (CELL + GAP) - GAP


def render_bag(qq: str, page: int = 1, owner: str = "") -> tuple[bytes | None, str]:
    """卡包：**按稀有度分等级**排小曲绘，不带曲名/等级。

    每 40 张一页；只有一页时不显示页码。
    """
    if Image is None:
        return None, "没有 Pillow，画不出卡包。"
    cards = owned_cards(qq)
    if not cards:
        return None, "卡包还是空的，先去发「抽卡」。"

    order = {name: i for i, (name, _) in enumerate(reversed(RARITIES))}
    cards.sort(key=lambda c: (order.get(c["rarity"], 9), c["title"].lower()))

    total_pages = max(1, (len(cards) + PER_PAGE_BAG - 1) // PER_PAGE_BAG)
    page = max(1, min(total_pages, int(page or 1)))
    chunk = cards[(page - 1) * PER_PAGE_BAG: page * PER_PAGE_BAG]

    # 页内按稀有度分组（保持 UR→N 的先后）
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for c in chunk:
        if groups and groups[-1][0] == c["rarity"]:
            groups[-1][1].append(c)
        else:
            groups.append((c["rarity"], [c]))

    W, M = 760, 32
    body_top = 132
    body_h = sum(BAG_SEC_H + max(1, (len(g) + COLS - 1) // COLS) * (CELL + GAP)
                 for _, g in groups)
    body_bottom = body_top + 18 + body_h - GAP + 18
    H = body_bottom + 106

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    f_label = F._font(17, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_tiny = F._font(12, False, "cn")
    f_sec = F._font(26, True, "num", heavy=True)
    f_cnt = F._font(12, False, "cn")

    img = F._panel(img, (M, 24, W - M, 116), 24)
    d = ImageDraw.Draw(img)
    d.text((M + 28, 40), f"卡包 · {owner}" if owner else "卡包", font=f_label, fill=F.TEXT)
    d.text((W - M - 28, 44), f"共 {len(cards)} 张", font=f_sub, fill=F.SOFT, anchor="ra")
    if total_pages > 1:
        d.text((M + 28, 72), f"第 {page} / {total_pages} 页　每页 {PER_PAGE_BAG} 张",
               font=f_sub, fill=F.SOFT)
    else:
        d.text((M + 28, 72), "发「抽卡」就能往这里加", font=f_sub, fill=F.SOFT)

    img = F._panel(img, (M, body_top, W - M, body_bottom), 24)
    d = ImageDraw.Draw(img)

    x0 = M + 24
    y = body_top + 18
    for name, group in groups:
        color = RARITY_COLOR[name]
        d.text((x0, y), name, font=f_sec, fill=color)
        d.text((W - M - 24, y + 10), f"{len(group)} 张", font=f_cnt, fill=F.SOFT, anchor="ra")
        y += BAG_SEC_H
        for i, card in enumerate(group):
            r, c = divmod(i, COLS)
            x = x0 + c * (CELL + GAP)
            yy = y + r * (CELL + GAP)
            cov = _thumb(Path(card["cover"]), CELL)
            if cov is not None:
                img.paste(cov, (x, yy), cov)
            else:
                d.rounded_rectangle((x, yy, x + CELL, yy + CELL), radius=12, fill=F.FILL)
        y += max(1, (len(group) + COLS - 1) // COLS) * (CELL + GAP)

    d.line([(M, H - 46), (W - M, H - 46)], fill=F.LINE, width=1)
    d.text((M, H - 34), f"{owner} 的卡包" if owner else "贝尔娜提塔的卡包", font=f_tiny, fill=F.SOFT)
    d.text((W - M, H - 34), f"{F.beijing_stamp()} 北京时间", font=f_tiny, fill=F.SOFT, anchor="ra")

    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=84, optimize=True, progressive=True)
    return buf.getvalue(), ""


def pool_pages() -> list[tuple[str, list[dict[str, Any]], int, int]]:
    """[(rarity, 本页的卡, 档内第几页, 档内共几页)]，按 N→UR 排。"""
    pages: list[tuple[str, list[dict[str, Any]], int, int]] = []
    for name, _ in RARITIES:
        xs = pool().get(name, [])
        total = max(1, (len(xs) + PER_PAGE - 1) // PER_PAGE)
        for i in range(total):
            pages.append((name, xs[i * PER_PAGE:(i + 1) * PER_PAGE], i + 1, total))
    return pages


def render_pool(qq: str, rarity: str | None = None, page: int = 1,
                owner: str = "") -> tuple[bytes | None, str]:
    """卡池：**指定一个等级（稀有度）看一页**，抽到的亮、没抽到的灰。

    `卡池 UR 2` = UR 档第 2 页；不给等级就默认第一档（N）。
    """
    if Image is None:
        return None, "没有 Pillow，画不出卡池。"
    p = pool()
    if not any(p.values()):
        return None, "卡池是空的。"

    order = [name for name, _ in RARITIES]
    if rarity:
        key = str(rarity).upper()
        if key not in order:
            return None, f"只有 {' / '.join(order)} 这五档。"
    else:
        key = order[0]

    xs = p.get(key) or []
    if not xs:
        return None, f"{key} 档还是空的。"
    total_pages = max(1, (len(xs) + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(total_pages, int(page or 1)))
    chunk = xs[(page - 1) * PER_PAGE: page * PER_PAGE]

    have = set(owned(qq))
    got = sum(1 for c in xs if card_key(c) in have)

    W, M = 760, 32
    rows = max(1, (len(chunk) + COLS - 1) // COLS)
    body_top = 132
    body_bottom = body_top + 18 + rows * (CELL + GAP)
    H = body_bottom + 106

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    color = RARITY_COLOR[key]
    f_label = F._font(17, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_tiny = F._font(12, False, "cn")
    f_rare = F._font(40, True, "num", heavy=True)

    img = F._panel(img, (M, 24, W - M, 116), 24)
    d = ImageDraw.Draw(img)
    d.text((M + 28, 34), f"卡池 · {owner}" if owner else "卡池", font=f_label, fill=F.TEXT)
    d.text((W - M - 28, 18), key, font=f_rare, fill=color, anchor="ra")
    d.text((M + 28, 76), f"{key} 档 {len(xs)} 张，已收 {got} 张　·　第 {page} / {total_pages} 页",
           font=f_sub, fill=F.SOFT)

    img = F._panel(img, (M, body_top, W - M, body_bottom), 24)
    d = ImageDraw.Draw(img)
    _paint_grid(img, d, chunk, have, body_top + 18)

    d.line([(M, H - 46), (W - M, H - 46)], fill=F.LINE, width=1)
    d.text((M, H - 34), "亮 = 已经抽到　灰 = 还没抽到", font=f_tiny, fill=F.SOFT)
    others = " / ".join(n for n in order if n != key)
    tip = f"卡池 {key} {page + 1} 翻页　·　卡池 {others.split(' / ')[0]} 换档" if total_pages > 1 \
        else f"卡池 {others.split(' / ')[0]} 换档"
    d.text((W - M, H - 34), tip, font=f_tiny, fill=F.SOFT, anchor="ra")

    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=84, optimize=True, progressive=True)
    return buf.getvalue(), ""


# ---------------------------------------------------------------- 出图

W, H = 760, 1000


def _cover(path: Path, w: int, h: int) -> Any:
    """等比铺满再居中裁切，切圆角。"""
    if Image is None:
        return None
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    iw, ih = im.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, int(iw * scale + 0.5)), max(h, int(ih * scale + 0.5))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    im = im.crop((left, top, left + w, top + h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=int(min(w, h) * 0.06), fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


W2, H2 = 760, 830


def _one_row(img: Any, d: Any, card: dict[str, Any], top: int) -> None:
    """双卡图里的一行：左边曲绘，右边稀有度/曲名/曲师/难度。"""
    rarity = card["rarity"]
    color = RARITY_COLOR[rarity]
    M, row_h = 32, 292

    d.rounded_rectangle((M, top, W2 - M, top + row_h), radius=24, fill=F.CARD,
                        outline=F.LINE, width=1)
    d.rounded_rectangle((M, top + 22, M + 6, top + row_h - 22), radius=3, fill=color)

    size = 232
    cov = _cover(Path(card["cover"]), size, size)
    cx, cy = M + 26, top + 30
    if cov is not None:
        img.paste(cov, (cx, cy), cov)
    else:
        d.rounded_rectangle((cx, cy, cx + size, cy + size), radius=16, fill=F.FILL)

    tx = cx + size + 26
    f_rare = F._font(46, True, "num", heavy=True)
    f_star = F._font(17, True, "cn")
    f_title = F._font(24, True, "cn")
    f_artist = F._font(14, False, "cn")
    f_pill = F._font(15, True, "num", heavy=True)
    f_lv = F._font(20, True, "num")
    f_src = F._font(12, False, "num")

    d.text((tx, top + 24), rarity, font=f_rare, fill=color)
    kw = d.textlength(rarity, font=f_rare)
    d.text((tx + kw + 8, top + 48), RARITY_STARS[rarity], font=f_star, fill=color)
    d.text((W2 - M - 22, top + 30), SOURCE_NAME.get(card["source"], card["source"]),
           font=f_src, fill=F.SOFT, anchor="ra")

    d.text((tx, top + 92), F._clip(d, card["title"], f_title, W2 - M - 22 - tx),
           font=f_title, fill=F.TEXT)
    d.text((tx, top + 126), F._clip(d, "曲师 " + card["artist"], f_artist, W2 - M - 22 - tx),
           font=f_artist, fill=F.SUB)

    pw = F._pill(d, tx, top + 164, card["diff"], color, f_pill, 28)
    d.text((tx + pw + 10, top + 178), card["level"], font=f_lv, fill=F.TEXT, anchor="lm")


def render_two(cards: list[dict[str, Any]], used: int = 0, limit: int = 2,
               news: list[bool] | None = None) -> bytes | None:
    """一次抽出来的两张卡放在同一张图上（760×830）。"""
    if Image is None or not cards:
        return None

    img = Image.new("RGB", (W2, H2), F.BG)
    d = ImageDraw.Draw(img)

    f_label = F._font(15, True, "cn")
    f_sub = F._font(12, False, "cn")

    img = F._panel(img, (32, 24, W2 - 32, 104), 22)
    d = ImageDraw.Draw(img)
    d.text((60, 36), "抽卡", font=f_label, fill=F.TEXT)
    d.text((W2 - 60, 38), f"今天 {used} / {limit} 张", font=f_sub, fill=F.SOFT, anchor="ra")
    flags = ""
    if news:
        get = sum(1 for x in news if x)
        flags = f"新卡 {get} 张"
        if get < len(news):
            flags += f"・重复 {len(news) - get} 张"
    d.text((60, 60), flags or "　", font=f_sub, fill=F.SUB)

    top = 118
    for card in cards[:2]:
        _one_row(img, d, card, top)
        top += 306

    d.line([(32, H2 - 42), (W2 - 32, H2 - 42)], fill=F.LINE, width=1)
    d.text((32, H2 - 32), "贝尔娜提塔的曲绘卡池 · 每天两张", font=f_sub, fill=F.SOFT)

    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True, progressive=True)
    return buf.getvalue()


def render_card(card: dict[str, Any]) -> bytes | None:
    """760×1000：白底 + 稀有度色带 + 大曲绘 + 曲目信息（单张预览用）。"""
    if Image is None:
        return None

    rarity = card["rarity"]
    color = RARITY_COLOR[rarity]
    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    f_rare = F._font(84, True, "num", heavy=True)
    f_star = F._font(26, True, "cn")
    f_src = F._font(16, False, "num")
    f_title = F._font(36, True, "cn")
    f_artist = F._font(17, False, "cn")
    f_pill = F._font(20, True, "num", heavy=True)
    f_lv = F._font(34, True, "num")
    f_tiny = F._font(13, False, "cn")

    M = 32

    # 顶部：稀有度 + 来源 + 难度
    img = F._panel(img, (M, 24, W - M, 176), 28)
    d = ImageDraw.Draw(img)
    d.text((M + 30, 40), rarity, font=f_rare, fill=color)
    kw = d.textlength(rarity, font=f_rare)
    d.text((M + 30 + kw + 14, 84), RARITY_STARS[rarity], font=f_star, fill=color)
    d.text((W - M - 30, 46), "曲绘抽卡", font=f_tiny, fill=F.SOFT, anchor="ra")
    d.text((W - M - 30, 70), SOURCE_NAME.get(card["source"], card["source"]),
           font=f_src, fill=F.SUB, anchor="ra")
    d.text((W - M - 30, 100), f"{card['diff']} {card['level']}", font=f_src,
           fill=F.SOFT, anchor="ra")

    # 曲绘
    size = 600
    cx, cy = (W - size) // 2, 192
    cov = _cover(Path(card["cover"]), size, size)
    if cov is not None:
        img.paste(cov, (cx, cy), cov)
    else:
        d.rounded_rectangle((cx, cy, cx + size, cy + size), radius=36, fill=F.FILL, outline=F.LINE)
        d.text((cx + size // 2, cy + size // 2), "?", font=f_rare, fill=F.SOFT, anchor="mm")

    # 曲名 / 曲师
    ty = cy + size + 28
    d.text((M, ty), F._clip(d, card["title"], f_title, W - M * 2), font=f_title, fill=F.TEXT)
    d.text((M, ty + 48), F._clip(d, "曲师 " + card["artist"], f_artist, W - M * 2),
           font=f_artist, fill=F.SUB)

    # 底部难度胶囊
    py = ty + 84
    pw = F._pill(d, M, py, card["diff"], color, f_pill, 36)
    d.text((M + pw + 12, py + 18), card["level"], font=f_lv, fill=F.TEXT, anchor="lm")

    d.line([(M, H - 42), (W - M, H - 42)], fill=F.LINE, width=1)
    d.text((M, H - 32), "贝尔娜提塔的曲绘卡池 · 难度越高越难出", font=f_tiny, fill=F.SOFT)

    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True, progressive=True)
    return buf.getvalue()


if __name__ == "__main__":
    import sys
    st = stats()
    print("卡池:", st, "共", sum(st.values()))
    qq = sys.argv[1] if len(sys.argv) > 1 else "3035888633"
    c = draw(qq)
    print(text_of(c) if c else "抽不出来")
    if c:
        png = render_card(c)
        if png:
            Path("gacha_preview.jpg").write_bytes(png)
            print("预览：gacha_preview.jpg", len(png) // 1024, "KB")
