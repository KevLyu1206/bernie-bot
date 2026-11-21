"""水鱼查分器（diving-fish）B50 查询 + 图片渲染（带曲绘）。

查询：POST /api/maimaidxprober/query/player
     {"username": "水鱼用户名", "b50": true}
     隐私玩家需要 Import-Token 头；也支持 {"username": ..., "credentials": "密码"}
曲绘：https://www.diving-fish.com/covers/{song_id:05d}.png
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

API = "https://www.diving-fish.com/api/maimaidxprober/query/player"
COVER_URL = "https://www.diving-fish.com/covers/{:05d}.png"
COVER_DIR = Path(__file__).with_name("covers")

DIFF_NAME = {0: "BASIC", 1: "ADVANCED", 2: "EXPERT", 3: "MASTER", 4: "Re:MASTER"}
DIFF_COLOR = {
    0: (110, 190, 110),
    1: (240, 200, 80),
    2: (235, 110, 110),
    3: (170, 120, 230),
    4: (210, 170, 250),
}

# 版面
W, H = 1180, 1742
PAD = 34
BG = (24, 24, 32)
CARD = (36, 36, 48)
LINE = (54, 54, 70)
TEXT = (232, 232, 240)
SUB = (150, 150, 168)
ACCENT = (255, 196, 92)
ACC = (205, 205, 218)
COVER = 30
ROW_H = 40

FONT_DIR = Path("C:/Windows/Fonts")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhl.ttc"]
    for n in names:
        p = FONT_DIR / n
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _mid(ch: dict[str, Any]) -> int:
    for k in ("song_id", "id", "music_id"):
        v = ch.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


async def fetch(
    username: str | None = None,
    qq: int | str | None = None,
    token: str = "",
    password: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"b50": True}
    if username:
        payload["username"] = username
        if password:
            payload["credentials"] = password
    elif qq:
        payload["qq"] = int(qq)
    else:
        raise ValueError("username 或 qq 至少给一个")

    headers = {"Content-Type": "application/json", "User-Agent": "bernie-bot/1.0 (maimai b50)"}
    if token:
        headers["Import-Token"] = token

    async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=15.0)) as c:
        r = await c.post(API, json=payload, headers=headers)
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = None
            msg = ""
            if isinstance(body, dict):
                msg = str(body.get("message") or "")
            elif isinstance(body, str):
                msg = body[:120]
            if "not exists" in msg:
                raise RuntimeError("水鱼上查不到这个名字。WMC ID 不能直接查，要先在水鱼注册并导入成绩")
            if r.status_code == 403:
                raise RuntimeError(msg or "这个玩家设了隐私，需要 Import-Token 或密码")
            if r.status_code == 401:
                raise RuntimeError("密码不对")
            raise RuntimeError(msg or f"水鱼返回 {r.status_code}")

        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"水鱼返回了意外的格式：{str(data)[:120]}")
        return data


async def fetch_covers(ids: list[int], client: httpx.AsyncClient | None = None) -> dict[int, bytes]:
    """并发抓封面，落盘缓存。抓不到的就不返回，渲染时留空。"""
    COVER_DIR.mkdir(exist_ok=True)
    out: dict[int, bytes] = {}
    own = client is None
    c = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    sem = asyncio.Semaphore(8)

    async def one(mid: int) -> None:
        if not mid:
            return
        p = COVER_DIR / f"{mid:05d}.png"
        if p.exists() and p.stat().st_size > 1000:
            out[mid] = p.read_bytes()
            return
        async with sem:
            try:
                r = await c.get(COVER_URL.format(mid))
                if r.status_code == 200 and len(r.content) > 1000:
                    p.write_bytes(r.content)
                    out[mid] = r.content
            except Exception:
                pass

    try:
        await asyncio.gather(*(one(i) for i in set(ids)))
    finally:
        if own:
            await c.aclose()
    return out


def pick_b50(data: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """水鱼 b50 的 charts 是 {"sd": [35 首], "dx": [15 首]}；也兼容直接给 list。"""
    raw = data.get("charts")
    old: list[dict] = []
    new: list[dict] = []

    if isinstance(raw, dict):
        sd, dx = raw.get("sd"), raw.get("dx")
        if isinstance(sd, list) or isinstance(dx, list):
            old = [c for c in (sd or []) if isinstance(c, dict)]
            new = [c for c in (dx or []) if isinstance(c, dict)]
        else:
            merged: list[dict] = []
            for v in raw.values():
                if isinstance(v, list):
                    merged += [c for c in v if isinstance(c, dict)]
            merged.sort(key=lambda c: -float(c.get("ra") or 0))
            return merged[:35], merged[35:50]
    elif isinstance(raw, list):
        rows = [c for c in raw if isinstance(c, dict)]
        new = [c for c in rows if c.get("is_new") or c.get("isNew")]
        old = [c for c in rows if c not in new]
    else:
        return [], []

    for c in old + new:
        c.setdefault("ra", 0.0)
    old.sort(key=lambda c: -float(c.get("ra") or 0))
    new.sort(key=lambda c: -float(c.get("ra") or 0))
    return old[:35], new[:15]


def _fmt_acc(v: Any) -> str:
    try:
        return f"{float(v):.4f}%"
    except (TypeError, ValueError):
        return str(v)


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render(data: dict[str, Any], covers: dict[int, bytes] | None = None) -> bytes:
    covers = covers or {}
    nickname = str(data.get("nickname") or data.get("username") or "未知玩家")
    rating = data.get("rating")
    plate = data.get("plate") or ""
    add = data.get("additional_rating")

    old, new = pick_b50(data)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = _font(46, bold=True)
    f_big = _font(64, bold=True)
    f_sub = _font(22)
    f_sec = _font(26, bold=True)
    f_row = _font(19)
    f_row_b = _font(19, bold=True)
    f_num = _font(17)
    f_ra = _font(20, bold=True)
    f_tiny = _font(15)

    # 头部
    d.rectangle([0, 0, W, 150], fill=CARD)
    d.text((PAD, 30), _truncate(d, nickname, f_title, W - 400), font=f_title, fill=TEXT)
    meta = []
    if rating is not None:
        meta.append(f"Rating {rating}")
    if add:
        meta.append(f"段位 {add}")
    if plate:
        meta.append(str(plate))
    meta.append(f"B50 共 {len(old) + len(new)} 首")
    d.text((PAD + 2, 88), " · ".join(meta), font=f_sub, fill=SUB)

    if rating is not None:
        rt = str(rating)
        tw = d.textlength(rt, font=f_big)
        d.text((W - PAD - tw, 34), rt, font=f_big, fill=ACCENT)
        lw = d.textlength("RATING", font=f_tiny)
        d.text((W - PAD - lw, 108), "RATING", font=f_tiny, fill=SUB)

    col_w = (W - PAD * 2) // 2
    top = 178

    def draw_block(x: int, y0: int, title: str, rows: list[dict], highlight: bool) -> None:
        d.text((x, y0), title, font=f_sec, fill=ACCENT if highlight else TEXT)
        y = y0 + 42
        d.line([(x, y - 10), (x + col_w - 20, y - 10)], fill=LINE, width=2)

        for i, ch in enumerate(rows, 1):
            ds = ch.get("ds")
            di = int(ch.get("level_index", 3))
            ra = ch.get("ra")
            title_txt = str(ch.get("title") or "?")
            if str(ch.get("type", "")).upper() == "DX":
                title_txt = "[DX] " + title_txt

            # 封面
            cov = covers.get(_mid(ch))
            if cov:
                try:
                    thumb = Image.open(io.BytesIO(cov)).convert("RGB").resize((COVER, COVER))
                    img.paste(thumb, (x, y + 4))
                except Exception:
                    d.rectangle([x, y + 4, x + COVER, y + 4 + COVER], fill=(60, 60, 76))
            else:
                d.rectangle([x, y + 4, x + COVER, y + 4 + COVER], fill=(60, 60, 76))

            d.text((x + COVER + 8, y + 6), f"{i:>2}", font=f_num, fill=SUB)

            name_x = x + COVER + 34
            max_w = col_w - COVER - 34 - 258
            d.text((name_x, y + 4), _truncate(d, title_txt, f_row, max_w), font=f_row, fill=TEXT)

            dcolor = DIFF_COLOR.get(di, SUB)
            d.text((x + col_w - 240, y + 6), DIFF_NAME.get(di, "?")[:4], font=f_tiny, fill=dcolor)
            d.text((x + col_w - 192, y + 4), f"{ds}", font=f_row_b, fill=dcolor)

            acc_s = _fmt_acc(ch.get("achievements"))
            aw = d.textlength(acc_s, font=f_tiny)
            d.text((x + col_w - 56 - aw, y + 6), acc_s, font=f_tiny, fill=ACC)

            ra_s = f"{float(ra):.0f}" if ra is not None else "-"
            rw = d.textlength(ra_s, font=f_ra)
            d.text((x + col_w - rw - 2, y + 2), ra_s, font=f_ra, fill=ACCENT)

            y += ROW_H

    draw_block(PAD, top, f"旧版本 Best {len(old)}", old, False)
    draw_block(PAD + col_w, top, f"当前版本 Best {len(new)}", new, True)

    old_sum = sum(float(c.get("ra") or 0) for c in old)
    new_sum = sum(float(c.get("ra") or 0) for c in new)
    d.line([(PAD, H - 66), (W - PAD, H - 66)], fill=LINE, width=2)
    d.text((PAD, H - 50), f"旧曲合计 {old_sum:.0f}", font=f_sub, fill=SUB)
    d.text((PAD + 220, H - 50), f"新曲合计 {new_sum:.0f}", font=f_sub, fill=SUB)
    total = f"合计 {old_sum + new_sum:.0f}"
    tw = d.textlength(total, font=f_sub)
    d.text((W - PAD - tw, H - 50), total, font=f_sub, fill=ACCENT)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def query_to_png(
    username: str | None = None,
    qq: int | str | None = None,
    token: str = "",
    password: str = "",
    with_covers: bool = True,
    save: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    data = await fetch(username=username, qq=qq, token=token, password=password)
    covers = {}
    if with_covers:
        old, new = pick_b50(data)
        covers = await fetch_covers([_mid(c) for c in old + new])
    png = render(data, covers)
    if save:
        save.write_bytes(png)
    return png, data


def demo() -> None:
    fake = {"nickname": "贝尔娜提塔", "rating": 14732, "additional_rating": 3, "plate": "熊の子", "charts": []}
    titles = [
        "Grievous Lady", "QZKago Requiem", "Heaven and Hell", "Falsus", "AMAZING MIGHTYYYY!!!!",
        "Schwarzschild", "Emperor's Return", "Glorious Crown", "B.B.K.K.B.K.K.", "Metamorphose",
        "天火明命", "HECATONCHEIR", "Scythe of the Reaper", "Never Ending Story", "PANDORA PARADOXXX",
        "VIIIbit Explorer", "BEELZEBUB", "Ultimate Force", "Straight into the lights", "Conflict",
    ]
    real_ids = [8, 834, 11451, 10784, 11117, 835, 11236, 11047, 11102, 11223, 11354, 11062, 10950, 11244, 11245]
    for i in range(50):
        fake["charts"].append(
            {
                "title": titles[i % len(titles)] + ("" if i < 20 else f" #{i}"),
                "song_id": real_ids[i % len(real_ids)] + (0 if i < 15 else i),
                "level_index": 3 if i % 4 else 4,
                "level": "13+" if i % 3 else "14",
                "ds": 13.8 if i % 3 else 14.2,
                "achievements": 100.5 - (i * 0.13),
                "ra": 297 - i * 2.1,
                "type": "DX" if i % 2 else "SD",
                "is_new": i >= 35,
            }
        )
    covers = asyncio.run(fetch_covers([_mid(c) for c in fake["charts"]]))
    print(f"抓到 {len(covers)} 张封面")
    png = render(fake, covers)
    out = Path(__file__).with_name("b50_demo.png")
    out.write_bytes(png)
    print(f"已生成 {out}（{len(png)//1024} KB）")


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        who = sys.argv[1] if len(sys.argv) > 1 else None
        if not who:
            print("用法：python maimai.py <水鱼用户名>  |  python maimai.py demo")
            raise SystemExit(1)
        png, data = asyncio.run(query_to_png(username=who, save=Path(f"b50_{who}.png")))
        print(f"OK：{data.get('nickname')} rating={data.get('rating')} → b50_{who}.png")
