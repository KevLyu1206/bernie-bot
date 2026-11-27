# -*- coding: utf-8 -*-
"""/今日运势 进真实 handler 的冒烟测试（mock 发送，不连 QQ 不花 token）。

    python fortune_group_test.py

验证：
    1. 群里 at_only=true，不 @ 不发指令 → 没反应
    2. 群里 @ 了再发 /今日运势 → 出签
    3. 私聊直接发 → 出签
    4. 同一 QQ 同一天连发两次 → 同一份签
    5. 不同 QQ → 不同的签
"""
from __future__ import annotations

import asyncio

import bot

SENT: list[tuple] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, len(png), caption, png))


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


def priv(qq, text):
    return {"post_type": "message", "message_type": "private", "self_id": 3678328823,
            "user_id": qq, "sender": {"nickname": "测试"},
            "message": [{"type": "text", "data": {"text": text}}], "raw_message": text}


async def main() -> int:
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678328823"

    ok = True
    check = lambda c, m: (globals().__setitem__("ok", ok and c), print(f"[{'OK' if c else 'FAIL'}] {m}"))
    print(f"at_only = {bot.CFG['group'].get('at_only')}")

    n = len(SENT)
    await bot.handle_group(grp(77002, "/今日运势"))
    check(len(SENT) == n, "群里不 @ 就发 → 不回应")

    await bot.handle_group(grp(77002, "/今日运势", at_me=True))
    check(len(SENT) == n + 1 and SENT[-1][0] == "image" and SENT[-1][1] == "group",
          "@ 了再发 → 出签")
    check(SENT[-1][4] == "", "只发图，不带文字")

    n = len(SENT)
    await bot.handle_private(priv(77003, "/今日运势"))
    check(len(SENT) == n + 1 and SENT[-1][0] == "image" and SENT[-1][1] == "private",
          "私聊直接发 → 出签")
    check(SENT[-1][4] == "", "私聊也只发图")

    first = SENT[-1][5]          # 图本身
    await bot.handle_private(priv(77003, "/今日运势"))
    check(SENT[-1][5] == first, "同一 QQ 同一天再问 → 同一张图")

    await bot.handle_private(priv(77004, "/今日运势"))
    check(SENT[-1][5] != first, "不同 QQ → 不同的图")

    for s in SENT:
        print(("  IMAGE " if s[0] == "image" else "  TEXT  ") + s[1], "|",
              s[4] if s[0] == "image" else s[3][:60])
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
