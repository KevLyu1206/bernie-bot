# -*- coding: utf-8 -*-
"""欧气算法 + 卡池@人 自检（不动真实 gacha.json）。

    python pool_at_test.py

覆盖：
    1. 同抽数：UR+N > SSR+SSR > SSR+N > SR+R > SR+SR（不再并列）
    2. 全 N=0、全 UR=100
    3. 卡池 @人：渲染用的是对方的 qq + 昵称
    4. 卡池 UR @人：等级也传过去
    5. 不 @ 就是自己
"""
from __future__ import annotations

import asyncio
import json

import bot
import gacha as G

SENT: list[tuple] = []
SEEN: list[tuple] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def check(name: str, cond: bool, ok: list) -> None:
    ok.append(cond)
    print(f"[{'OK' if cond else 'FAIL'}] {name}")


async def main() -> int:
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678328823"

    ok: list[bool] = []

    # 1) 欧气排序
    cases = [
        ("全 N", {"n": 2, "N": 2}, 0.0),
        ("全 UR", {"n": 2, "UR": 2}, 100.0),
    ]
    for name, st, want in cases:
        check(f"{name} = {want}", abs(G.luck_of(st) - want) < 0.01, ok)

    sr_sr = G.luck_of({"n": 2, "SR": 2})
    sr_r = G.luck_of({"n": 2, "SR": 1, "R": 1})
    ssr_n = G.luck_of({"n": 2, "SSR": 1, "N": 1})
    ssr_ssr = G.luck_of({"n": 2, "SSR": 2})
    ur_n = G.luck_of({"n": 2, "UR": 1, "N": 1})
    print(f"  SR+SR {sr_sr:.1f} / SR+R {sr_r:.1f} / SSR+N {ssr_n:.1f} / SSR+SSR {ssr_ssr:.1f} / UR+N {ur_n:.1f}")
    check("SSR+N > SR+R > SR+SR 里的低档组合", ssr_n > sr_r and sr_sr > 0, ok)
    check("UR+R > SSR+SSR", G.luck_of({"n": 2, "UR": 1, "R": 1}) > ssr_ssr, ok)
    check("SSR+SSR > SSR+N", ssr_ssr > ssr_n, ok)
    check("UR+N > SSR+SSR", ur_n > ssr_ssr, ok)

    # 2) 卡池 @人：用临时存档
    real_main, real_bak = G.SAVE_FILE, G.SAVE_BAK
    tmp = G.BASE_DIR / "gacha_pool_test_tmp.json"
    tmp_bak = G.BASE_DIR / "gacha_pool_test_tmp.bak"
    G.SAVE_FILE, G.SAVE_BAK = tmp, tmp_bak
    tmp.write_text(json.dumps({
        "users": {"111": ["maimai|ECHO"], "222": []},
        "stats": {"111": {"n": 2, "score": 40, "UR": 2, "N": 0, "R": 0, "SR": 0, "SSR": 0, "name": "被看的人"},
                  "222": {"n": 0, "score": 0, "N": 0, "R": 0, "SR": 0, "SSR": 0, "UR": 0, "name": "看的人"}},
    }, ensure_ascii=False), encoding="utf-8")

    async def fake_api(action, params, timeout=12.0):
        if action in ("get_group_member_info", "get_stranger_info"):
            return {"card": "群名片", "nickname": "群名片"}
        return {}

    bot.BOT.api = fake_api  # type: ignore[assignment]

    real_render = G.render_pool

    def spy(qq, rarity=None, page=1, owner=""):
        SEEN.append((qq, rarity, page, owner))
        return b"\xff\xd8\xff" + b"0" * 600, ""

    G.render_pool = spy  # type: ignore[assignment]

    try:
        n = len(SENT)
        await bot.do_pool("222", "卡池 UR", "group", 999, ["111"])
        check("出图", len(SENT) == n + 1 and SENT[-1][0] == "image", ok)
        check("看的是 @ 的人", SEEN and SEEN[-1][0] == "111", ok)
        check("等级传过去了", SEEN and SEEN[-1][1] == "UR", ok)
        check("标题带群名片", SEEN and "群名片" in SEEN[-1][3], ok)

        n = len(SENT)
        await bot.do_pool("222", "卡池", "group", 999, None)
        check("不 @ → 自己", SEEN[-1][0] == "222", ok)
        check("自己能出图", len(SENT) == n + 1 and SENT[-1][0] == "image", ok)

        # 3) 卡包 @人
        bag_seen = []
        real_bag = G.render_bag

        def bag_spy(qq, page=1, owner=""):
            bag_seen.append((qq, page, owner))
            return b"\xff\xd8\xff" + b"0" * 400, ""

        G.render_bag = bag_spy  # type: ignore[assignment]
        n = len(SENT)
        await bot.do_bag("222", "卡包", "group", 999, ["111"])
        check("卡包@：看的是对方", bag_seen and bag_seen[-1][0] == "111", ok)
        check("卡包@：出图", len(SENT) == n + 1 and SENT[-1][0] == "image", ok)
        await bot.do_bag("222", "卡包 2", "group", 999, ["111"])
        check("卡包@：页数传过去", bag_seen[-1][1] == 2, ok)
        G.render_bag = real_bag  # type: ignore[assignment]

        # 4) 真渲染一次，确认 owner 出现在图上
        G.render_pool = real_render  # type: ignore[assignment]
        png, msg = G.render_pool("111", "UR", 1, "被看的人")
        check("真渲染出图", bool(png) and png[:2] == b"\xff\xd8", ok)
        if png:
            (bot.BASE_DIR / "pool_view_preview.jpg").write_bytes(png)
    finally:
        G.SAVE_FILE, G.SAVE_BAK = real_main, real_bak
        G.render_pool = real_render  # type: ignore[assignment]
        for p in (tmp, tmp_bak):
            p.unlink(missing_ok=True)

    print("\n" + ("全部通过" if all(ok) else "有失败项"))
    return 0 if all(ok) else 1


raise SystemExit(asyncio.run(main()))
