# -*- coding: utf-8 -*-
"""西八计分 + /西八榜 图卡

计分文件放在 `西八/score.json`（跟着图走，备份文件夹就一起备份了）：

    {"<文件名>": {"n": 抽中次数, "last": 最后抽中时间, "by": 谁加的(可选)}}

对外：
    load_scores() -> dict
    bump(name)              抽中一次 +1
    top(n) -> [(name, count, Path)]
    render_rank(rows, total_files, total_picks) -> bytes | None
    text_of(rows, total_files, total_picks) -> str
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

try:
    import fortune as F
except Exception:  # pragma: no cover
    F = None  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
X8_DIR = BASE_DIR / "西八"
SCORE_FILE = X8_DIR / "score.json"
SCORE_BAK = X8_DIR / "score.json.bak"   # 每次写都留一份，主文件坏了还能救
LIKES_FILE = X8_DIR / "likes.json"      # 「操」赞：{文件名: {n, v: {"消息id|qq": 1}}}
SENT_FILE = X8_DIR / "sent.json"        # 发出去的图：{消息id: {f: [文件名...], t: 时间}}
EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

GOLD = (255, 159, 10)
SILVER = (142, 142, 147)
BRONZE = (196, 124, 60)


def files() -> list[Path]:
    if not X8_DIR.is_dir():
        return []
    return sorted(p for p in X8_DIR.iterdir() if p.is_file() and p.suffix.lower() in EXTS)


def load_scores() -> dict[str, dict[str, Any]]:
    """读 stats。主文件坏了就读 .bak；两边都没有就当空榜（不会写坏）。"""
    for path in (SCORE_FILE, SCORE_BAK):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            continue
    return {}


def save_scores(data: dict[str, dict[str, Any]]) -> None:
    try:
        X8_DIR.mkdir(parents=True, exist_ok=True)
        body = json.dumps(data, ensure_ascii=False, indent=1)
        tmp = SCORE_FILE.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(SCORE_FILE)
        try:
            SCORE_BAK.write_text(body, encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass


def bump(name: str, by: str = "") -> None:
    data = load_scores()
    rec = data.setdefault(name, {"n": 0})
    rec["n"] = int(rec.get("n", 0)) + 1
    rec["last"] = int(time.time())
    if by and not rec.get("by"):
        rec["by"] = str(by)
    save_scores(data)


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        X8_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


# ---------------------------------------------------------------- 发出去的图（给「操」点赞对号用）

MAX_SENT = 500


def remember_sent(message_id: str, names: list[str]) -> None:
    """记住这条消息发了哪几张图（顺序 = 第 1/2/3 张）。"""
    mid = str(message_id or "").strip()
    if not mid or not names:
        return
    data = _read_json(SENT_FILE)
    data[mid] = {"f": [str(n) for n in names], "t": int(time.time())}
    if len(data) > MAX_SENT:                       # 只留最近的
        keep = sorted(data.items(), key=lambda kv: int((kv[1] or {}).get("t") or 0))[-MAX_SENT:]
        data = dict(keep)
    _write_json(SENT_FILE, data)


def sent_files(message_id: str) -> list[str]:
    rec = (_read_json(SENT_FILE).get(str(message_id or "")) or {})
    names = rec.get("f") or []
    return [str(n) for n in names]


# ---------------------------------------------------------------- 赞

def like(name: str, token: str) -> int | None:
    """给一张图 +1 赞；同一个 token（消息id|qq）只算一次。返回新的赞数。"""
    data = _read_json(LIKES_FILE)
    rec = data.setdefault(name, {"n": 0, "v": {}})
    voters = rec.setdefault("v", {})
    if token in voters:
        return None
    voters[token] = 1
    rec["n"] = int(rec.get("n", 0)) + 1
    _write_json(LIKES_FILE, data)
    return int(rec["n"])


def load_likes() -> dict:
    return _read_json(LIKES_FILE)


def total_likes() -> int:
    return sum(int((v or {}).get("n") or 0) for v in load_likes().values())


def top_liked(n: int = 10) -> list[tuple[str, int, Path]]:
    data = load_likes()
    on_disk = {p.name: p for p in files()}
    rows = [
        (name, int(rec.get("n") or 0), on_disk[name])
        for name, rec in data.items()
        if name in on_disk
    ]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return [r for r in rows if r[1] > 0][:n]


def top(n: int = 10) -> list[tuple[str, int, Path]]:
    data = load_scores()
    on_disk = {p.name: p for p in files()}
    rows = [
        (name, int(rec.get("n") or 0), on_disk[name])
        for name, rec in data.items()
        if name in on_disk
    ]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return [r for r in rows if r[1] > 0][:n]


def text_of(rows: list[tuple[str, int, Path]], total_files: int, total_picks: int,
            title: str = "西八榜", unit: str = "次") -> str:
    if not rows:
        return f"{title}还是空的（一共 {total_files} 张，还没人点过）。"
    lines = [f"{title} · 共 {total_files} 张 / 合计 {total_picks} {unit}", ""]
    for i, (name, n, _p) in enumerate(rows, 1):
        lines.append(f"{i:>2}. {name[:16]}　{n} {unit}")
    return "\n".join(lines)


def _thumb(path: Path, size: int) -> Any:
    if F is None or getattr(F, "Image", None) is None:
        return None
    Image = F.Image
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    im = im.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    from PIL import ImageDraw

    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.18), fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(im, (0, 0), mask)
    return out


def render_rank(rows: list[tuple[str, int, Path]], total_files: int,
                total_picks: int, title: str = "西八榜",
                kicker: str = "西八榜 · 抽中次数排行", unit: str = "次",
                footer: str = "贝尔娜提塔记的账 · 每抽中一次 +1",
                counter: str = "累计抽过") -> bytes | None:
    """760 宽白卡：名次 + 缩略图 + 次数条。"""
    if F is None or getattr(F, "Image", None) is None:
        return None
    Image, ImageDraw = F.Image, F.ImageDraw

    W, M = 760, 28
    row_h = 92
    top_h = 116
    foot_h = 52
    n = max(1, len(rows))
    H = 22 + top_h + 14 + n * (row_h + 10) + foot_h

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    f_title = F._font(34, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_rank = F._font(28, True, "num")
    f_name = F._font(15, False, "cn")
    f_cnt = F._font(22, True, "num")
    f_unit = F._font(12, False, "cn")
    f_tiny = F._font(11, False, "cn")

    # 顶部
    img = F._panel(img, (M, 22, W - M, 22 + top_h), 28)
    d = ImageDraw.Draw(img)
    d.text((M + 26, 38), kicker, font=f_sub, fill=F.SUB)
    d.text((M + 26, 58), title, font=f_title, fill=F.TEXT)
    d.text((W - M - 26, 40), f"{total_files} 张图", font=f_sub, fill=F.SOFT, anchor="ra")
    d.text((W - M - 26, 62), f"{counter} {total_picks} {unit}", font=f_sub, fill=F.SOFT, anchor="ra")
    d.line([(M + 26, 104), (W - M - 26, 104)], fill=F.FILL, width=1)

    if not rows:
        d.text((M + 40, 22 + top_h + 30), "还没人抽过——去发一次 /随机西八 吧。", font=f_name, fill=F.SUB)

    max_n = max((r[1] for r in rows), default=1)
    y = 22 + top_h + 14
    for i, (name, count, path) in enumerate(rows, 1):
        img = F._panel(img, (M, y, W - M, y + row_h), 20)
        d = ImageDraw.Draw(img)

        color = GOLD if i == 1 else SILVER if i == 2 else BRONZE if i == 3 else F.SOFT
        d.text((M + 24, y + row_h / 2), str(i), font=f_rank, fill=color, anchor="lm")

        th = _thumb(path, 64)
        tx = M + 62
        if th is not None:
            img.paste(th, (tx, y + 14), th)
        else:
            d.rounded_rectangle((tx, y + 14, tx + 64, y + 78), radius=12, fill=F.FILL)

        nx = tx + 64 + 16
        # 文件名是 md5，太长没意义：只留前 6 位当编号，加过图的带上是谁加的
        label = f"#{name[:6]}"
        by = str((load_scores().get(name) or {}).get("by") or "")
        if by:
            label += f"　by {by}"
        d.text((nx, y + 20), label, font=f_name, fill=F.TEXT)
        if by:
            d.text((nx, y + 40), name, font=f_tiny, fill=F.SOFT)

        # 次数条（右边留 78px 给数字）
        bar_w = W - M - 26 - nx - 78
        bar_y = y + 60 if by else y + 56
        d.rounded_rectangle((nx, bar_y, nx + bar_w, bar_y + 8), radius=4, fill=F.FILL)
        fill_w = int(bar_w * count / max_n)
        if fill_w > 0:
            d.rounded_rectangle((nx, bar_y, nx + max(fill_w, 8), bar_y + 8), radius=4, fill=color)

        d.text((W - M - 26, y + 30), str(count), font=f_cnt, fill=color, anchor="ra")
        d.text((W - M - 26, y + 58), unit, font=f_unit, fill=F.SOFT, anchor="ra")

        y += row_h + 10

    d.line([(M, H - 44), (W - M, H - 44)], fill=F.LINE, width=1)
    d.text((M, H - 32), footer, font=f_tiny, fill=F.SOFT)
    d.text((W - M, H - 32), "西八/score.json · likes.json", font=f_tiny, fill=F.SOFT, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()
