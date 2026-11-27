# -*- coding: utf-8 -*-
"""「操」点赞 + /西八赞榜 的自检（mock 发送，不连 QQ）。

    python xiba_like_test.py

覆盖：
    1. /随机西八 3 发出去 → 记住 message_id 和顺序
    2. 引用那条消息回「操2」→ 第 2 张 +1（不需要 @）
    3. 同一个人对同一条重复「操2」→ 不重复加
    4. 「操 1 3」→ 第 1、3 张各 +1
    5. 不带编号「操」→ 第 1 张
    6. /西八赞榜 出图
    7. 跑前备份、跑完还原 sent.json / likes.json
"""
from __future__ import annotations

import asyncio
import shutil

import bot
import xiba

SENT: list[tuple] = []
MID_SEQ = {"n": 0}


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_images(mt, target, images, caption=""):
    MID_SEQ["n"] += 1
    mid = f"mid{MID_SEQ['n']}"
    SENT.append(("images", mt, target, list(images)))
    return mid


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def grp(qq, text, gid=222333, at_me=False, reply=None):
    segs = []
    if reply:
        segs.append({"type": "reply", "data": {"id": reply}})
    if at_me:
        segs.append({"type": "at", "data": {"qq": "3678328823"}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "self_id": 3678328823,
            "group_id": gid, "user_id": qq, "sender": {"card": "测试", "nickname": "测试"},
            "message": segs, "raw_message": text}


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

    baks = {}
    for name in ("sent.json", "likes.json"):
        p = xiba.X8_DIR / name
        b = p.with_suffix(".testbak")
        if p.exists():
            shutil.copyfile(p, b)
            baks[name] = (p, b)

    try:
        n = len(SENT)
        await bot.do_random_xiba(master, "/随机西八 3", "private", int(master))
        check(len(SENT) == n + 1 and len(SENT[-1][3]) == 3, "发 3 张")
        mid = f"mid{MID_SEQ['n']}"
        names = xiba.sent_files(mid)
        check(len(names) == 3, f"记住 message_id {mid} → {len(names)} 张")

        def likes_of(idx):
            return int((xiba.load_likes().get(names[idx]) or {}).get("n") or 0)

        # 操2（不需要 @）——成功后回「操操操。！」
        n = len(SENT)
        await bot.handle_group(grp(5555, "操2", reply=mid))
        check(likes_of(1) == 1 and likes_of(0) == 0 and likes_of(2) == 0,
              f"操2 → 第 2 张 +1（{likes_of(0)},{likes_of(1)},{likes_of(2)}）")
        acks = [x for x in SENT[n:] if x[0] == "text"]
        check(len(acks) == 1 and acks[0][3] == "操操操。！", f"点赞成功回：{acks[0][3] if acks else '（没回）'}")

        # 同一人重复 —— 不再加，也不回
        n = len(SENT)
        await bot.handle_group(grp(5555, "操2", reply=mid))
        check(likes_of(1) == 1, "同一人重复操2 → 不重复加")
        check(len([x for x in SENT[n:] if x[0] == "text"]) == 0, "重复点赞不回话")

        # 操 1 3
        await bot.handle_group(grp(6666, "操 1 3", reply=mid))
        check(likes_of(0) == 1 and likes_of(2) == 1, "操 1 3 → 第 1、3 张各 +1")

        # 不带编号
        await bot.handle_group(grp(7777, "操", reply=mid))
        check(likes_of(0) == 2, f"操（不带编号）→ 第 1 张（{likes_of(0)}）")

        # 越界编号：不赞也不报错
        before = tuple(likes_of(i) for i in range(3))
        await bot.handle_group(grp(8888, "操9", reply=mid))
        check(tuple(likes_of(i) for i in range(3)) == before, "操9（越界）→ 不动")

        # 不是西八消息
        await bot.handle_group(grp(9999, "操", reply="不是西八的消息"))
        check(tuple(likes_of(i) for i in range(3)) == before, "引用普通消息回操 → 不记")

        # 赞榜
        n = len(SENT)
        hit = await bot.do_xiba_like_rank(master, "/西八赞榜", "private", int(master))
        kb = len(SENT[-1][3]) // 1024 if SENT and SENT[-1][0] == "image" else 0
        check(hit and SENT[-1][0] == "image" and kb > 3, f"/西八赞榜 → 出图（{kb} KB）")

        rows = xiba.top_liked(5)
        check(rows and rows[0][0] == names[0] and rows[0][1] == 2,
              f"赞榜第一：{rows[0][0][:8]} {rows[0][1]} 赞" if rows else "赞榜为空")

        # 留一张预览
        png = xiba.render_rank(rows, len(bot.xiba_files()), xiba.total_likes(),
                               "西八赞榜", "西八赞榜 · 被「操」的次数", "赞",
                               "贝尔娜提塔记的账 · 每被「操」一次 +1", "累计")
        if png:
            (bot.BASE_DIR / "xiba_like_preview.jpg").write_bytes(png)
            print(f"预览：xiba_like_preview.jpg（{len(png) // 1024} KB）")
    finally:
        for name, (p, b) in baks.items():
            shutil.copyfile(b, p)
            b.unlink(missing_ok=True)
        for name in ("sent.json", "likes.json"):
            if name not in baks:
                (xiba.X8_DIR / name).unlink(missing_ok=True)

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
