# -*- coding: utf-8 -*-
"""/随机西八 的自检（mock 发送，不连 QQ）。

    python xiba_test.py

覆盖：
    1. 不带数字 → 1 张；带 2 / 3 → 对应张数；乱写 99 → 压到 3 张
    2. 发的都是真图片（JPEG/PNG/GIF 头）
    3. 群里不 @ 没反应，@ 了才发
    4. 文件夹空 → 一句提示
"""
from __future__ import annotations

import asyncio
import io
import shutil

import bot

SENT: list[tuple] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_images(mt, target, images, caption=""):
    SENT.append(("images", mt, target, list(images)))


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def grp(qq, text, gid=222333, at_me=False):
    segs = []
    if at_me:
        segs.append({"type": "at", "data": {"qq": "3678328823"}})
        segs.append({"type": "text", "data": {"text": " "}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "self_id": 3678328823,
            "group_id": gid, "user_id": qq, "sender": {"card": "测试", "nickname": "测试"},
            "message": segs, "raw_message": text}


def is_image(raw: bytes) -> bool:
    return raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:4] == b"GIF8" or raw[:4] == b"RIFF"


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

    score_bak = bot.X8_DIR / "score.json.testbak"
    had_score = (bot.X8_DIR / "score.json").exists()
    if had_score:
        shutil.copyfile(bot.X8_DIR / "score.json", score_bak)

    files = bot.xiba_files()
    print(f"西八文件夹：{len(files)} 张")
    if len(files) < 3:
        from PIL import Image
        bot.X8_DIR.mkdir(parents=True, exist_ok=True)
        for i in range(3 - len(files)):
            buf = io.BytesIO()
            Image.new("RGB", (60, 60), (i * 70, 120, 200)).save(buf, format="PNG")
            (bot.X8_DIR / f"_test_{i}.png").write_bytes(buf.getvalue())
        files = bot.xiba_files()
        print(f"补了测试图，现在 {len(files)} 张")

    master = str((bot.CFG.get("master_ids") or ["3035888633"])[0])

    for text, want in (("/随机西八", 1), ("/随机西八 2", 2), ("/随机西八 3", 3), ("/随机西八 99", 3)):
        n = len(SENT)
        hit = await bot.do_random_xiba(master, text, "private", int(master))
        got = [s for s in SENT[n:] if s[0] == "images"]
        n_img = sum(len(s[3]) for s in got)
        check(hit and len(got) == 1 and n_img == want,
              f"{text} → 1 条消息 / {n_img} 张（期望 1 条 {want} 张）")
        check(all(is_image(raw) for s in got for raw in s[3]), f"{text} 发出来的是图片")

    n = len(SENT)
    await bot.handle_group(grp(77002, "/随机西八 2"))
    check(len(SENT) == n, "群里不 @ → 没反应")

    bot.USER_LAST.clear()
    n = len(SENT)
    await bot.handle_group(grp(77002, "/随机西八 2", at_me=True))
    got = [s for s in SENT[n:] if s[0] == "images"]
    check(len(got) == 1 and len(got[0][3]) == 2,
          f"群里 @ + /随机西八 2 → 1 条消息 / {sum(len(s[3]) for s in got)} 张")

    # 空文件夹
    saved = bot.X8_DIR
    bot.X8_DIR = saved.parent / "_不存在的西八"
    n = len(SENT)
    await bot.do_random_xiba(master, "/随机西八", "private", int(master))
    check(len(SENT) == n + 1 and SENT[-1][0] == "text", f"空文件夹 → 只回一句：{SENT[-1][3] if SENT else ''}")
    bot.X8_DIR = saved

    if had_score:
        shutil.copyfile(score_bak, bot.X8_DIR / "score.json")
        score_bak.unlink(missing_ok=True)
    elif (bot.X8_DIR / "score.json").exists():
        (bot.X8_DIR / "score.json").unlink()

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
