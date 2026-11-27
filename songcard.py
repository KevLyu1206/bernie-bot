# -*- coding: utf-8 -*-
"""曲目卡片：/随一首（1-15）和 /定数表（1-15）。

两张图都走 fortune.py 那套白色 Apple 风（白卡片 + 淡投影 + 发丝线 + 难度色）。

    parse_level("13")   -> ("13", False)      13 和 13+ 都算
    parse_level("13+")  -> ("13+", True)      只算 13+
    rows_at(db, base, plus_only)               等级 → [(song, chart), ...]
    pick(db, level)                            随机一首（等概率，按谱面算）
    render_song(song, chart, level)            单曲卡 760×780
    render_table(rows, level, page, per_page)  定数表 760×1040
"""
from __future__ import annotations

import io
import random
import re
from typing import Any

from PIL import Image, ImageDraw

import fortune as F

LEVEL_RE = re.compile(r"^(\d{1,2})\s*(\+)?$")


def parse_level(arg: str) -> tuple[str, bool] | None:
    """「13」→ ('13', False)：13 和 13+ 都要；「13+」→ ('13+', True)：只要 13+。"""
    m = LEVEL_RE.match((arg or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    if not 1 <= n <= 15:
        return None
    plus = bool(m.group(2))
    return (f"{n}+" if plus else str(n), plus)


def _clean_level(lv: str) -> bool:
    return bool(lv) and "?" not in lv and lv[0].isdigit()


def rows_at(db: Any, level: str, exact: bool = False) -> list[dict[str, Any]]:
    """收集该等级下所有谱面（同曲不同难度算不同行）。

    exact=True 时只认这一个定数（定数表用）；否则 13 会把 13 和 13+ 一起收（抽歌用）。
    """
    out: list[dict[str, Any]] = []
    if db is None:
        return out
    if level.endswith("+"):
        exact = True          # 「13+」永远只指 13+
    base = level.rstrip("+")
    for s in db.all:
        try:
            sid = int(s.get("id") or 0)
        except (TypeError, ValueError):
            continue
        for d in db.difficulties(sid):
            lv = str(d.get("level") or "")
            if not _clean_level(lv):
                continue
            if exact:
                if lv != level:
                    continue
            elif lv not in (base, base + "+"):
                continue
            out.append({"song": s, "chart": d, "level": lv})
    out.sort(key=lambda r: (str(r["song"].get("title") or "").lower(), int(r["song"].get("id") or 0)))
    return out


def pick(db: Any, level: str, exact: bool = False, rng: random.Random | None = None) -> dict[str, Any] | None:
    rows = rows_at(db, level, exact)
    if not rows:
        return None
    return (rng or random).choice(rows)


def level_label(db: Any, level: str) -> str:
    """标题上写的定数：只有真的存在 N+ 时才写成「N / N+」。"""
    base = level.rstrip("+")
    if level.endswith("+"):
        return f"{base}+"
    has_plus = any(r["level"] == f"{base}+" for r in rows_at(db, base))
    return f"{base} / {base}+" if has_plus else base


def unique_songs(rows: list[dict[str, Any]]) -> list[int]:
    """同一首只留一张曲绘。"""
    seen: set[int] = set()
    out: list[int] = []
    for r in rows:
        try:
            sid = int(r["song"]["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def page_ids(rows: list[dict[str, Any]], page: int = 1, per_page: int = 25) -> list[int]:
    """某一页要显示的曲绘 ID（给下载用）。"""
    songs = unique_songs(rows)
    pages = max(1, (len(songs) + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    return songs[start:start + per_page]


def song_info(db: Any, song: dict[str, Any], chart: dict[str, Any]) -> dict[str, Any]:
    """整首歌的全部信息 + 它所有谱面（同一难度 SD/DX 只有一张时取 DX，抽到的优先）。"""
    sid = int(song["id"])
    picked_idx = int(chart["index"])
    by_idx: dict[int, dict[str, Any]] = {}
    for d in db.difficulties(sid):
        idx = int(d["index"])
        cur = by_idx.get(idx)
        if cur is None:
            by_idx[idx] = d
        elif idx == picked_idx and str(d.get("level")) == str(chart.get("level")):
            by_idx[idx] = d
        elif d.get("type") == "DX" and cur.get("type") != "DX":
            by_idx[idx] = d

    charts = []
    for idx in sorted(by_idx):
        d = by_idx[idx]
        charts.append(
            {
                "index": idx,
                "name": F.DIFF_CN.get(idx, "?"),
                "level": str(d.get("level") or "?"),
                "designer": (d.get("designer") or "").strip() or "—",
                "type": d.get("type") or "SD",
                "picked": idx == picked_idx and str(d.get("level")) == str(chart.get("level")),
            }
        )
    return {
        "id": sid,
        "title": str(song.get("title") or "?"),
        "artist": str(song.get("artist") or "未知"),
        "genre": F.GENRE_SHORT.get(str(song.get("genre") or ""), str(song.get("genre") or "?")),
        "bpm": song.get("bpm") or "?",
        "version": db.version_name(sid) or "?",
        "cover": F._cached_cover(sid),
        "charts": charts,
    }


def text_of(info: dict[str, Any]) -> str:
    lines = [
        f"{info['title']}",
        f"曲师：{info['artist']}",
        f"{info['genre']} · BPM {info['bpm']} · {info['version']}",
        f"曲目 ID：{info['id']}",
        "",
    ]
    for c in info["charts"]:
        mark = " ← 抽到这张" if c["picked"] else ""
        lines.append(f"{c['type']} {c['name']} {c['level']}　谱师 {c['designer']}{mark}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 画图

def render_song(info: dict[str, Any], rng_level: str) -> bytes | None:
    """单曲卡：曲绘 + 曲名/曲师/流派/BPM/版本 + 全部难度的定数与谱师。"""
    W, H = 760, 800
    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)
    M = 32

    f_label = F._font(17, True, "cn")
    f_tag = F._font(14, False, "cn")
    f_title = F._font(30, True, "cn")
    f_meta = F._font(15, False, "cn")
    f_small = F._font(13, False, "cn")
    f_pill = F._font(16, True, "num", heavy=True)
    f_level = F._font(26, True, "num")
    f_designer = F._font(14, False, "cn")
    f_tiny = F._font(12, False, "cn")

    img = F._panel(img, (M, 24, W - M, 700), 30)
    d = ImageDraw.Draw(img)

    d.text((M + 28, 48), f"随一首 · {rng_level}", font=f_label, fill=F.SUB)
    d.text((W - M - 28, 51), f"曲目 ID {info['id']}", font=f_tag, fill=F.SOFT, anchor="ra")

    size = 200
    cx, cy = M + 28, 84
    cov = F._cover(info["id"], size) if info.get("cover") else None
    if cov is not None:
        img.paste(cov, (cx, cy), cov)
        d.rounded_rectangle((cx, cy, cx + size, cy + size), radius=int(size * 0.12),
                            outline=F.LINE, width=1)
    else:
        d.rounded_rectangle((cx, cy, cx + size, cy + size), radius=int(size * 0.12),
                            fill=F.FILL, outline=F.LINE, width=1)
        d.text((cx + size // 2, cy + size // 2), "无曲绘", font=f_meta, fill=F.SOFT, anchor="mm")

    tx = cx + size + 26
    tw = W - M - 28 - tx
    ty = cy - 4
    for i, line in enumerate(_wrap2(d, info["title"], f_title, tw, 2)):
        d.text((tx, ty + i * 36), line, font=f_title, fill=F.TEXT)
        ty += 36 if i == 0 else 0
    ty = cy + 44 if len(_wrap2(d, info["title"], f_title, tw, 2)) > 1 else cy + 34
    d.text((tx, ty), _clip(d, "曲师：" + info["artist"], f_meta, tw), font=f_meta, fill=F.SUB)
    d.text((tx, ty + 26), _clip(d, info["genre"], f_meta, tw), font=f_meta, fill=F.SOFT)
    d.text((tx, ty + 52), _clip(d, f"BPM {info['bpm']}　{info['version']}", f_meta, tw),
           font=f_meta, fill=F.SOFT)

    # 全部谱面
    d.line([(M + 28, 320), (W - M - 28, 320)], fill=F.LINE, width=1)
    d.text((M + 28, 338), "全部难度", font=f_label, fill=F.SUB)

    ry = 372
    for c in info["charts"]:
        color = F.DIFF_COLOR.get(c["index"], F.SOFT)
        if c.get("picked"):
            d.rounded_rectangle((M + 16, ry - 6, W - M - 16, ry + 46), radius=14, fill=(255, 247, 237))
        pw = F._pill(d, M + 28, ry, c["name"], color, f_pill, 32)
        lv_w = d.textlength(c["level"], font=f_level)
        d.text((M + 28 + pw + 14, ry + 16), c["level"], font=f_level, fill=F.TEXT, anchor="lm")
        d.text((M + 28 + pw + 14 + lv_w + 10, ry + 18), c["type"], font=f_tiny, fill=F.SOFT, anchor="lm")
        d.text((W - M - 28, ry + 16), f"谱师 {c['designer']}", font=f_designer, fill=F.SUB, anchor="rm")
        if c.get("picked"):
            d.text((W - M - 28, ry + 44), "抽到的是这张", font=f_tiny, fill=(199, 124, 2), anchor="ra")
        ry += 56

    d.line([(M, 724), (W - M, 724)], fill=F.LINE, width=1)
    d.text((M, 736), "贝尔娜提塔给你抽的", font=f_tiny, fill=F.SOFT)
    d.text((W - M, 736), f"{F.beijing_stamp()} 北京时间", font=f_tiny, fill=F.SOFT, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _group_by_value(rows: list[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    """按 level_value 分组：13.7 / 13.8 / 13.9 …，组内按曲名。"""
    buckets: dict[float, list[dict[str, Any]]] = {}
    for r in rows:
        v = r["chart"].get("value")
        try:
            key = round(float(v), 1)
        except (TypeError, ValueError):
            key = -1.0
        buckets.setdefault(key, []).append(r)
    out: list[tuple[str, list[int]]] = []
    for key in sorted(buckets):
        out.append((f"{key:.1f}", unique_songs(buckets[key])))
    return out


def render_wall(rows: list[dict[str, Any]], level: str,
                cols: int = 7, size: int = 82, gap: int = 12) -> bytes | None:
    """定数表：长图曲绘墙，按小数定数分段（13.6 / 13.7 / 13.8 / 13.9），段内排小曲绘。"""
    sections = _group_by_value(rows)
    total = sum(len(ids) for _, ids in sections)

    W, M = 760, 32
    row_h = size + gap
    sec_h, sec_gap = 40, 24
    body_top = 156

    y = body_top
    plan: list[tuple[str, int, int]] = []
    for label, ids in sections:
        plan.append((label, len(ids), y))
        nrows = max(1, (len(ids) + cols - 1) // cols)
        y += sec_h + nrows * row_h - gap + sec_gap
    body_bottom = y - sec_gap + 20
    H = body_bottom + 90

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    f_label = F._font(17, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_tiny = F._font(12, False, "cn")
    f_sec = F._font(30, True, "num")
    f_cnt = F._font(13, False, "cn")

    # 标题
    img = F._panel(img, (M, 24, W - M, 116), 24)
    d = ImageDraw.Draw(img)
    span = f"{sections[0][0]} – {sections[-1][0]}" if len(sections) > 1 else (sections[0][0] if sections else "")
    d.text((M + 28, 40), f"定数表 · {level}", font=f_label, fill=F.TEXT)
    d.text((W - M - 28, 44), f"共 {total} 首", font=f_sub, fill=F.SOFT, anchor="ra")
    d.text((M + 28, 72), f"按小数定数分段　{span}", font=f_sub, fill=F.SOFT)

    # 长图主体
    img = F._panel(img, (M, 132, W - M, body_bottom), 24)
    d = ImageDraw.Draw(img)
    x0 = M + 24

    for label, count, sy in plan:
        d.text((x0, sy - 2), label, font=f_sec, fill=F.TEXT)
        d.text((W - M - 24, sy + 10), f"{count} 首", font=f_cnt, fill=F.SOFT, anchor="ra")
        ids = next(ids for lab, ids in sections if lab == label)
        for i, sid in enumerate(ids):
            r, c = divmod(i, cols)
            x = x0 + c * (size + gap)
            yy = sy + sec_h + r * row_h
            cov = F._cover(sid, size)
            if cov is not None:
                img.paste(cov, (x, yy), cov)
                d.rounded_rectangle((x, yy, x + size, yy + size), radius=12,
                                    outline=F.LINE, width=1)
            else:
                d.rounded_rectangle((x, yy, x + size, yy + size), radius=12, fill=F.FILL)

    if not sections:
        d.text((x0, body_top), "这个定数没有曲目。", font=f_label, fill=F.SUB)

    # 页脚
    d.line([(M, H - 46), (W - M, H - 46)], fill=F.LINE, width=1)
    d.text((M, H - 34), "贝尔娜提塔的定数表", font=f_tiny, fill=F.SOFT)
    d.text((W - M, H - 34), f"{F.beijing_stamp()} 北京时间", font=f_tiny, fill=F.SOFT, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- 小工具

def _clip(d: Any, text: str, font: Any, max_w: int) -> str:
    if d.textlength(text, font=font) <= max_w:
        return text
    cur = text
    while cur and d.textlength(cur + "…", font=font) > max_w:
        cur = cur[:-1]
    return cur + "…"


def _wrap2(d: Any, text: str, font: Any, max_w: int, max_lines: int = 2) -> list[str]:
    out, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) <= max_w:
            cur += ch
            continue
        if cur:
            out.append(cur)
        cur = ch
        if len(out) >= max_lines:
            break
    if cur and len(out) < max_lines:
        out.append(cur)
    if len(out) == max_lines:
        last = out[-1]
        while last and d.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        out[-1] = last + "…"
    return out or [""]
