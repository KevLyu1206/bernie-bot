# -*- coding: utf-8 -*-
"""/5C吃什么 的自检（离线用例 + 真实接口冒烟，mock 发送不连 QQ）。

    python menu5c_test.py

覆盖：
    1. 关门 / 正常两种日期都能解析，酱料和「配方说明行」会被滤掉
    2. 食堂别名（cmc / mudd / pomona …）能认出来
    3. 图卡能画出来，文字版带全部食堂
    4. 非主人发指令 → 只回一句，不出图
    5. 主人私聊 /5C吃什么 → 出图；筛选只出一家；乱写食堂名 → 提示
"""
from __future__ import annotations

import asyncio

import bot
import menu5c

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


# ---------------------------------------------------------------- 离线夹具

FIXTURE_OPEN = {
    "response": {
        "menu": [
            {
                "date": "2026-11-22T00:00:00",
                "time": 1795334400,
                "open": True,
                "meals": {
                    "dinner": {
                        "meal": "DINNER",
                        "startTime": 1795351200,
                        "endTime": 1795358400,
                        "stations": [
                            {"station": "Grill", "menu": [
                                {"name": "grilled chicken breast", "calories": 240},
                                {"name": "toppings", "calories": 12},
                                {"name": "ketchup", "calories": 15},
                                {"name": "veggie burger", "calories": 180},
                            ]},
                            {"station": "Desserts", "menu": [
                                {"name": "Chocolate Cake", "calories": 380},
                            ]},
                            {"station": "⚠️ Earlier Menu (Not Posted Yet)", "menu": [
                                {"name": "Ghost Dish", "calories": 500},
                            ]},
                            {"station": "Soup", "menu": [
                                {"name": "fresh made to order with cage free eggs, egg whites", "calories": 200},
                                {"name": "tomato soup", "calories": 120},
                            ]},
                        ],
                    },
                    "breakfast": {
                        "meal": "BREAKFAST",
                        "stations": [{"station": "Breakfast @ Home", "menu": [
                            {"name": "scrambled eggs", "calories": 300},
                        ]}],
                    },
                },
            }
        ]
    }
}

FIXTURE_CLOSED = {
    "response": {
        "menu": [
            {"date": 20261122, "time": 1795334400, "open": False,
             "messages": {"screenMessage": "Closed today"}},
        ]
    }
}


def test_offline() -> bool:
    import datetime as dt

    import bot as _b  # noqa: F401  确认 bot 能 import（menu5c_mod 挂上了）

    day = dt.date(2026, 11, 22)
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"[{'OK' if cond else 'FAIL'}] {msg}")

    parsed = menu5c.parse_day(FIXTURE_OPEN, day, per_meal=6)
    items = [i for m in parsed["meals"] for i in m["items"]]
    names_en = [i["en"] for i in items]
    names_zh = [i["zh"] for i in items]
    check(parsed["open"] and [m["label"] for m in parsed["meals"]] == ["早餐", "晚餐"],
          f"开门日解析：{ [m['label'] for m in parsed['meals']] }")
    check(all(n not in names_en for n in ("Toppings", "Ketchup", "Ghost Dish")),
          "酱料 / 未发布档口被滤掉")
    check("Grilled chicken breast" in names_en, f"小写菜名会补大写：{names_en[:3]}")
    check(not any(n.startswith("Fresh made") for n in names_en), "配方说明行被滤掉")
    check("巧克力蛋糕" in names_zh, f"菜名翻成中文：{names_zh}")
    check(sum(1 for z in names_zh if any("\u4e00" <= c <= "\u9fff" for c in z)) >= len(names_zh) - 1,
          "几乎全部菜名都有中文（品牌名除外）")

    closed = menu5c.parse_day(FIXTURE_CLOSED, day)
    check(not closed["open"] and closed["note"] == "今天关门", f"关门日：{closed}")

    check(menu5c.resolve_halls(["cmc"]) == ["collins"], "cmc → collins")
    check(menu5c.resolve_halls(["mudd"]) == ["hoch"], "mudd → hoch")
    check(menu5c.resolve_halls(["pomona"]) == ["frank", "frary"], "pomona → frank + frary")
    check(menu5c.resolve_halls(["火星"]) is None, "认不出的词 → None")

    # 食堂配色：Frank / Frary 同属 Pomona，都是蓝色
    check(menu5c.HALL_COLOR["frank"] == menu5c.HALL_COLOR["frary"], "Frank / Frary 同色")
    fr = menu5c.HALL_COLOR["frank"]
    check(fr[2] > fr[0] and fr[2] > fr[1], f"Pomona 是蓝的 {fr}")
    check(len({tuple(v) for k, v in menu5c.HALL_COLOR.items() if k not in ("frank", "frary")}) == 4,
          "另外四家各自一个颜色")

    hall_open = dict(menu5c.HALL_BY_KEY["collins"], **parsed)
    hall_closed = dict(menu5c.HALL_BY_KEY["frank"], **closed)
    check(0 <= menu5c.menuzh.score_of(hall_open, "2026-11-22") <= 100, "贝尔评分在 0-100")
    _z = menu5c.menuzh
    _saved = dict(_z.HALL_BIAS)
    _hall_c = {"key": "collins", "meals": parsed["meals"]}
    _hall_h = {"key": "hoch", "meals": parsed["meals"]}
    _z.HALL_BIAS = {}
    _base_c = _z.score_of(_hall_c, "2026-11-22")
    _base_h = _z.score_of(_hall_h, "2026-11-22")
    _z.HALL_BIAS = _saved
    _c = _z.score_of(_hall_c, "2026-11-22")
    _h = _z.score_of(_hall_h, "2026-11-22")
    check(_c - _base_c == -5 and _h - _base_h == 5,
          f"口味偏置：Collins {_base_c}→{_c}（-5）／ Hoch {_base_h}→{_h}（+5）")
    check(_saved.get("collins") == -5 and _saved.get("hoch") == 5, "偏置表：Collins -5 / Hoch +5")
    # 亚洲菜偏置：亚洲菜越多分越高（最多 +6），燕麦粥不算
    _z2 = menu5c.menuzh
    check(_z2.is_asian("韩式烤鸡") and _z2.is_asian("牛肉越南河粉碗"), "亚洲菜能认出来")
    check(not _z2.is_asian("钢切燕麦粥") and not _z2.is_asian("鸡肉配牙买加咖喱")
          and not _z2.is_asian("玛格丽特披萨"), "燕麦粥 / 牙买加咖喱 / 披萨不算亚洲菜")
    _west = {"key": "malott", "meals": [{"label": "晚餐", "items": [
        {"en": "roast beef", "zh": "烤牛肉"}, {"en": "mashed potatoes", "zh": "土豆泥"}]}]}
    _asia = {"key": "malott", "meals": [{"label": "晚餐", "items": [
        {"en": "korean grilled chicken", "zh": "韩式烤鸡"}, {"en": "fried rice", "zh": "蔬菜炒饭"}]}]}
    check(_z2.asian_bias(_west["meals"]) == 0 and _z2.asian_bias(_asia["meals"]) == 4,
          "亚洲菜计数：0 道 +0 / 2 道 +4")
    check(_z2.score_of(_asia, "2026-11-22") > _z2.score_of(_west, "2026-11-22"),
          "有亚洲菜的食堂分更高")
    check(bool(menu5c.menuzh.pick_recommend(hall_open, "2026-11-22")), "有推荐语")
    check(menu5c.menuzh.pick_recommend(hall_open, "2026-11-22")
          == menu5c.menuzh.pick_recommend(hall_open, "2026-11-22"), "同一天推荐不会变")

    data = {"day": "2026-11-22", "weekday": "周日", "when": "今天", "offset": 0,
            "stamp": "08:00", "missing": [],
            "halls": [dict(hall_open, score=80, rec="贝尔推荐测试"),
                      dict(hall_closed, score=0, rec="今天没开，换一家")]}
    txt = menu5c.text_of(data)
    check("Collins" in txt and "Frank" in txt and "早" in txt, "文字版包含食堂和餐段")
    check("分" in txt and "贝尔" in txt, "文字版带评分和推荐")
    png = menu5c.render_card(data)
    check(bool(png) and png[:2] == b"\xff\xd8", f"图卡能画（{len(png or b'') // 1024} KB）")
    if png:
        (menu5c.BASE_DIR / "menu5c_test_card.jpg").write_bytes(png)
    return ok


async def test_handlers() -> bool:
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678328823"

    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"[{'OK' if cond else 'FAIL'}] {msg}")

    master = str((bot.CFG.get("master_ids") or ["3035888633"])[0])

    # 1) 群里非主人 @ 了也不会给
    n = len(SENT)
    await bot.handle_group(grp(77002, "/5C吃什么", at_me=True))
    check(len(SENT) == n + 1 and SENT[-1][0] == "text" and "主人" in SENT[-1][3],
          f"非主人 → 只回一句：{SENT[-1][3] if len(SENT) > n else '（没反应）'}")

    # 2) 私聊非主人也拦
    n = len(SENT)
    await bot.handle_private(priv(77003, "/5C吃什么"))
    check(len(SENT) == n + 1 and SENT[-1][0] == "text", "私聊非主人 → 文字拒绝")

    # 3) 主人直接调 do_menu5c（跳过冷却）：出图
    bot.STORE.user(master)["last_reply"] = 0
    n = len(SENT)
    hit = await bot.do_menu5c(master, "/5C吃什么", "private", int(master))
    check(hit and len(SENT) == n + 1 and SENT[-1][0] == "image" and SENT[-1][3] > 20000,
          f"主人 /5C吃什么 → 出图（{(SENT[-1][3] if len(SENT) > n else 0) // 1024} KB）")

    # 4) 指定食堂
    n = len(SENT)
    await bot.do_menu5c(master, "/5C吃什么 collins", "private", int(master))
    check(len(SENT) == n + 1 and SENT[-1][0] == "image", "指定 collins → 还是出图")

    # 5) 明天
    n = len(SENT)
    await bot.do_menu5c(master, "/5C吃什么 明天 cmc", "private", int(master))
    check(len(SENT) == n + 1 and SENT[-1][0] == "image", "「明天 cmc」→ 出图")

    # 6) 乱写食堂
    n = len(SENT)
    await bot.do_menu5c(master, "/5C吃什么 火星食堂", "private", int(master))
    check(len(SENT) == n + 1 and SENT[-1][0] == "text" and "可选" in SENT[-1][3],
          "乱写食堂 → 提示可选")

    for s in SENT:
        print(("  IMAGE " if s[0] == "image" else "  TEXT  ") + s[1] + " |",
              s[4] if s[0] == "image" else s[3][:60].replace("\n", " / "))
    return ok


async def main() -> int:
    print("== 离线 ==")
    ok = test_offline()
    print("\n== 真实接口 + handler ==")
    ok = await test_handlers() and ok
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
