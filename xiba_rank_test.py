# -*- coding: utf-8 -*-
"""/西八榜 的自检（mock 发送，不连 QQ）。

    python xiba_rank_test.py

覆盖：
    1. bump 记账 → top() 排序正确（次数多的靠前）
    2. /随机西八 抽一次 → 那张图的次数 +1
    3. /西八榜 出图；/西八榜 2 只取前 2；空榜给文字
    4. 测试前后备份、恢复 score.json，不动真实统计
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil

from PIL import Image

import bot
import xiba

SENT: list[tuple] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_images(mt, target, images, caption=""):
    SENT.append(("images", mt, target, list(images)))


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def make_png(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


async def main() -> int:
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_images = fake_send_images
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678328823"

    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"[{'OK' if cond else 'FAIL'}] {msg}")

    master = str((bot.CFG.get("master_ids") or ["3035888633"])[0])

    # 备份真实榜单
    backup = xiba.SCORE_FILE.with_suffix(".testbak")
    had = xiba.SCORE_FILE.exists()
    if had:
        shutil.copyfile(xiba.SCORE_FILE, backup)

    made: list = []
    try:
        # 造三张测试图
        for i, color in enumerate([(10, 120, 200), (200, 80, 40), (60, 180, 90)]):
            p = bot.X8_DIR / f"_ranktest_{i}.png"
            p.write_bytes(make_png(color))
            made.append(p)

        names = [p.name for p in made]
        scores = {names[0]: {"n": 5}, names[1]: {"n": 9}, names[2]: {"n": 2}}
        xiba.SCORE_FILE.write_text(json.dumps(scores, ensure_ascii=False), encoding="utf-8")

        rows = xiba.top(3)
        check([r[0] for r in rows][:3] == [names[1], names[0], names[2]],
              f"排序：{[ (r[0], r[1]) for r in rows ]}")

        # 随机西八：一条消息 + 计分总和按张数递增
        before_sum = sum(int(v.get("n", 0)) for v in xiba.load_scores().values())
        n = len(SENT)
        await bot.do_random_xiba(master, "/随机西八 3", "private", int(master))
        sent = [s for s in SENT[n:] if s[0] == "images"]
        n_img = len(sent[0][3]) if sent else 0
        after_sum = sum(int(v.get("n", 0)) for v in xiba.load_scores().values())
        check(len(sent) == 1 and n_img == 3, "抽一次 → 一条消息 3 张")
        check(after_sum - before_sum == n_img, f"记分：{before_sum} → {after_sum}（+{n_img}）")

        # 榜单出图
        n = len(SENT)
        hit = await bot.do_xiba_rank(master, "/西八榜", "private", int(master))
        kb = len(SENT[-1][3]) // 1024 if SENT and SENT[-1][0] == "image" else 0
        check(hit and SENT[-1][0] == "image" and len(SENT[-1][3]) > 5000,
              f"/西八榜 → 出图（{kb} KB）")

        n = len(SENT)
        await bot.do_xiba_rank(master, "/西八榜 2", "private", int(master))
        check(SENT[-1][0] == "image", "/西八榜 2 → 还是图")

        # 空榜
        xiba.SCORE_FILE.write_text("{}", encoding="utf-8")
        n = len(SENT)
        await bot.do_xiba_rank(master, "/西八榜", "private", int(master))
        check(SENT[-1][0] == "image", "空榜也出图（图里写「还没人抽过」）")
        txt = xiba.text_of([], 3, 0)
        check("空" in txt, f"空榜文字：{txt}")

        # 生成一张预览留档
        rows = xiba.top(10)
        png = xiba.render_rank(rows, len(bot.xiba_files()), 16)
        if png:
            (bot.BASE_DIR / "xiba_rank_preview.jpg").write_bytes(png)
            print(f"预览：xiba_rank_preview.jpg（{len(png) // 1024} KB）")
    finally:
        for p in made:
            p.unlink(missing_ok=True)
        if had:
            shutil.copyfile(backup, xiba.SCORE_FILE)
            backup.unlink(missing_ok=True)
        else:
            xiba.SCORE_FILE.unlink(missing_ok=True)

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
