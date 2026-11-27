# -*- coding: utf-8 -*-
"""欧非榜（按群）自检 —— mock 发送，不动真实 gacha.json。

    python luck_board_test.py

覆盖：
    1. 群里发「欧非榜」→ 只统计本群成员，出图
    2. 同一个榜不包含没在这个群说过话的人
    3. 私聊 → 全服
    4. 本群没人抽过 → 文字提示
    5. 无名的人不上榜
"""
from __future__ import annotations

import asyncio
import json
import shutil

import bot
import gacha as G

SENT: list[tuple] = []
ROWS_SEEN: list[dict] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def grp(qq, text, gid=999):
    return {"post_type": "message", "message_type": "group", "self_id": 3678328823,
            "group_id": gid, "user_id": qq, "sender": {"card": "测试", "nickname": "测试"},
            "message": [{"type": "text", "data": {"text": text}}], "raw_message": text}


async def main() -> int:
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678328823"

    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"[{'OK' if cond else 'FAIL'}] {msg}")

    # 用临时存档，绝不碰真的 gacha.json
    real_main, real_bak = G.SAVE_FILE, G.SAVE_BAK
    tmp = G.BASE_DIR / "gacha_luck_test_tmp.json"
    tmp_bak = G.BASE_DIR / "gacha_luck_test_tmp.bak"
    G.SAVE_FILE, G.SAVE_BAK = tmp, tmp_bak
    users = {"111": ["maimai|ECHO"], "222": ["maimai|ECHO"], "333": ["maimai|ECHO"],
             "444": ["maimai|ECHO"]}
    stats = {
        "111": {"n": 2, "score": 6, "N": 0, "R": 0, "SR": 2, "SSR": 0, "UR": 0, "name": "群里甲"},
        "222": {"n": 2, "score": 2, "N": 2, "R": 0, "SR": 0, "SSR": 0, "UR": 0, "name": "群里乙"},
        "333": {"n": 2, "score": 4, "N": 0, "R": 0, "SR": 0, "SSR": 1, "UR": 0, "name": "别的群丙"},
        "444": {"n": 2, "score": 3, "N": 1, "R": 1, "SR": 0, "SSR": 0, "UR": 0, "name": ""},
    }
    tmp.write_text(json.dumps({"users": users, "stats": stats}, ensure_ascii=False), encoding="utf-8")

    saved_members = dict(bot.STORE.members)
    bot.STORE.members = {"999": {"111": 1.0, "222": 1.0, "444": 1.0}}   # 333 不在 999 群

    # 不联网拿头像
    G._AVATAR_CACHE.clear()
    G._avatar = lambda qq, size=56: None  # type: ignore[assignment]

    # 协议端：返回群成员名单（含一个从没说过话、但有卡的 333）
    async def fake_api(action, params, timeout=12.0):
        if action == "get_group_member_list":
            gid = int(params.get("group_id") or 0)
            if gid == 999:
                return {"list": [{"user_id": 111, "card": "群名片甲"},
                                 {"user_id": 222, "nickname": "群名片乙"},
                                 {"user_id": 333, "card": "别的群丙"}]}
            return {"list": []}          # 新群：协议端也查不到人
        if action == "get_group_member_info":
            gid = int(params.get("group_id") or 0)
            who = str(params.get("user_id") or "")
            if gid == 777 and who in ("111", "333"):
                return {"user_id": who, "card": f"探到{who[-3:]}"}
            return {}
        return {}

    bot.BOT.api = fake_api  # type: ignore[assignment]
    bot._SEEDED_GROUPS.clear()

    real_render = G.render_luck_board

    def spy(rows, names=None, top=10, scope=""):
        ROWS_SEEN.clear()
        ROWS_SEEN.extend(rows)
        return real_render(rows, names, top, scope)

    G.render_luck_board = spy  # type: ignore[assignment]

    try:
        n = len(SENT)
        await bot.handle_group(grp(111, "欧非榜"))      # 免 @ 指令
        check(len(SENT) == n + 1 and SENT[-1][0] == "image", "群里发「欧非榜」→ 出图")
        got = {r["qq"] for r in ROWS_SEEN}
        # 333 不在 999 群，但成员名单里没有他 → 不该出现（名单里只有 111/222）
        check("333" not in got, f"不在本群的人不上榜：{sorted(got)}")
        check("111" in got and "222" in got, "本群的人都在")
        check("444" not in got, "没昵称的人不上榜（444 无名）")

        n = len(SENT)
        await bot.do_luck_rank("111", "欧非榜", "private", 111)
        got2 = {r["qq"] for r in ROWS_SEEN}
        check(len(SENT) == n + 1 and SENT[-1][0] == "image", "私聊 → 出全服图")
        check("333" in got2, "私聊包含其他群的 333")

        # 名单补全 + 名字一起收：清空本群成员，让 seed 走一次
        bot._SEEDED_GROUPS.clear()
        bot.STORE.members = {}
        for qq in ("111", "222", "333"):
            bot.STORE.user(qq)["name"] = ""      # 清掉真实 state 里可能残留的名字
        n = len(SENT)
        await bot.do_luck_rank("111", "欧非榜", "group", 999)
        got3 = {r["qq"] for r in ROWS_SEEN}
        check("333" in got3, "名单补齐后 333 也能上榜")
        check((bot.STORE.users.get("111") or {}).get("name") == "群名片甲", "名单里的群名片被记下来")

        # 名单接口不出人 → 按有卡的人逐个探测（777 群里 111/333 在）
        bot._SEEDED_GROUPS.clear()
        bot.STORE.members = {}
        n = len(SENT)
        await bot.do_luck_rank("111", "欧非榜", "group", 777)
        got4 = {r["qq"] for r in ROWS_SEEN}
        check({"111", "333"} <= got4, f"逐个探测：111/333 进榜（got {sorted(got4)}）")
        check("探到111" in str((bot.STORE.users.get("111") or {}).get("name")), "探测来的群名片也记下了")

        # 新群：只有个没抽过卡的人说话（发指令本身会把他记进这个群）
        bot.STORE.members = {}
        n = len(SENT)
        await bot.handle_group(grp(555, "欧非榜", gid=123456))
        got_text = SENT[-1][3] if SENT and SENT[-1][0] == "text" else ""
        check(len(SENT) == n + 1 and "本群还没人" in got_text, f"空群 → {got_text}")

        print("\n" + ("全部通过" if ok else "有失败项"))
    finally:
        G.SAVE_FILE, G.SAVE_BAK = real_main, real_bak
        G.render_luck_board = real_render  # type: ignore[assignment]
        bot.STORE.members = saved_members
        for p in (tmp, tmp_bak):
            p.unlink(missing_ok=True)

    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
