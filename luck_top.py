# -*- coding: utf-8 -*-
"""一次性：前 10 欧气榜（带头像，匿名不计）→ luck_top10.jpg"""
import io
import json
from pathlib import Path

import requests
from PIL import Image, ImageDraw

import fortune as F
import gacha as G

W, M = 760, 32
W_BG = F.BG


def avatar(qq: str, size: int = 64):
    for url in (f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100",
                f"https://q.qlogo.cn/headimg_dl?dst_uin={qq}&spec=100"):
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
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
        out = im.convert("RGBA")
        out.putalpha(mask)
        return out
    return None


def main() -> None:
    state = {}
    try:
        state = (json.loads(Path("state.json").read_text(encoding="utf-8")) or {}).get("users") or {}
    except Exception:
        pass

    rows = []
    for r in G.luck_table(min_pulls=1):
        name = (r.get("name") or (state.get(r["qq"]) or {}).get("name") or "").strip()
        if not name:                      # 无名不计
            continue
        r["who"] = G._clean_name(name)
        rows.append(r)
    rows.sort(key=lambda r: (-r["luck"], -r["n"]))
    rows = rows[:10]

    total_pulls = sum(r["n"] for r in rows)
    row_h, top_h, foot = 84, 132, 56
    H = 22 + top_h + 14 + len(rows) * (row_h + 10) + foot

    img = Image.new("RGB", (W, H), W_BG)
    d = ImageDraw.Draw(img)

    f_label = F._font(17, True, "cn")
    f_title = F._font(34, True, "cn")
    f_sub = F._font(13, False, "cn")
    f_rank = F._font(26, True, "num", heavy=True)
    f_name = F._font(19, True, "cn")
    f_name_kr = G._kr_font(19, bold=True)
    f_luck = F._font(30, True, "num", heavy=True)
    f_unit = F._font(11, False, "cn")
    f_meta = F._font(12, False, "cn")
    f_tiny = F._font(11, False, "cn")

    img = F._panel(img, (M, 22, W - M, 22 + top_h), 28)
    d = ImageDraw.Draw(img)
    d.text((M + 26, 40), "欧气榜 · 同抽数看稀有度 · 无名不计", font=f_label, fill=F.SUB)
    d.text((M + 26, 62), "运气 TOP 10", font=f_title, fill=F.TEXT)
    d.text((W - M - 26, 44), f"{len(rows)} 人 · 共 {total_pulls} 抽", font=f_sub, fill=F.SOFT, anchor="ra")
    d.text((W - M - 26, 66), "100 = 全是 UR，0 = 全是 N", font=f_tiny, fill=F.SOFT, anchor="ra")
    d.line([(M + 26, 104), (W - M - 26, 104)], fill=F.FILL, width=1)

    y = 22 + top_h + 14
    for i, r in enumerate(rows, 1):
        color = G._luck_color(r["luck"])
        img = F._panel(img, (M, y, W - M, y + row_h), 20)
        d = ImageDraw.Draw(img)

        rank_color = F.TEXT if i <= 3 else F.SOFT
        d.text((M + 28, y + row_h / 2), str(i), font=f_rank, fill=rank_color, anchor="lm")

        ax = M + 66
        av = avatar(r["qq"], 56)
        if av is not None:
            img.paste(av, (ax, y + 14), av)
        else:
            d.ellipse((ax, y + 14, ax + 56, y + 70), fill=F.FILL)
            d.text((ax + 28, y + 42), r["who"][:1], font=f_name, fill=F.SOFT, anchor="mm")

        nx = ax + 56 + 18
        who = G._clip_mixed(d, r["who"], f_name, f_name_kr, 300)
        G._draw_mixed(d, (nx, y + 18), who, f_name, f_name_kr, F.TEXT)
        d.text((nx, y + 44), f"UR {r['UR']} · SSR {r['SSR']} · SR {r['SR']} · 共 {r['n']} 抽",
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
    Path("luck_top10.jpg").write_bytes(buf.getvalue())
    print("ok", len(buf.getvalue()) // 1024, "KB")
    for i, r in enumerate(rows, 1):
        print(i, r["qq"], r["who"], round(r["luck"], 1), r["n"])


if __name__ == "__main__":
    main()
