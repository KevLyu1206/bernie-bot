# -*- coding: utf-8 -*-
"""曲绘抽卡 + 卡包 + 卡池的自检（不发网络请求，跑完还原 gacha.json）。

    python gacha_test.py

覆盖：
    1. 卡池五档都有货；同一来源难度单调升
    2. 6000 抽的稀有度比例贴近 40/27/22/8/3
    3. 同一人同一天第 N 次抽固定；换天换卡
    4. take()：每天 3 次，第 4 次给 None；抽到就进收藏、重复不算新
    5. 卡包出图（空包给提示）；卡池分页、每页一个稀有度
    6. bot：抽卡 / 卡包 / 卡池 群里免 @、私聊可用、不误触发
"""
from __future__ import annotations

import asyncio
import collections
import pathlib
import shutil

import bot
import gacha as G

TMP_MAIN = pathlib.Path("gacha_test_tmp.json")
TMP_BAK = pathlib.Path("gacha_test_tmp.json.bak")

SENT: list[tuple] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def grp(qq, text, gid=444555, at_me=False):
    segs = []
    if at_me:
        segs.append({"type": "at", "data": {"qq": "3678328823"}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "self_id": 3678328823,
            "group_id": gid, "user_id": qq, "sender": {"card": "测试", "nickname": "测试"},
            "message": segs, "raw_message": text}


def priv(qq, text):
    return {"post_type": "message", "message_type": "private", "self_id": 3678328823,
            "user_id": qq, "sender": {"nickname": "测试"},
            "message": [{"type": "text", "data": {"text": text}}], "raw_message": text}


async def main() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"[{'OK' if cond else 'FAIL'}] {msg}")

    # 测试全程用临时存档，**绝不碰真实 gacha.json**（bot 可能在跑）
    G.SAVE_FILE = TMP_MAIN
    G.SAVE_BAK = TMP_BAK
    for p in (TMP_MAIN, TMP_BAK, TMP_MAIN.with_suffix(".json.tmp")):
        p.unlink(missing_ok=True)
    try:
        await run_checks(check)
    finally:
        for p in (TMP_MAIN, TMP_BAK, TMP_MAIN.with_suffix(".json.tmp")):
            p.unlink(missing_ok=True)

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


async def run_checks(check) -> None:
    pool = G.pool()
    total = sum(len(v) for v in pool.values())
    print(f"卡池 {total} 张：{ {k: len(v) for k, v in pool.items()} }")

    check(total > 500, f"卡池够大（{total} 张）")
    check(all(pool[n] for n, _ in G.RARITIES), "五个稀有度都有货")

    for src in ("maimai", "phigros"):
        prev_max = -1.0
        good = True
        detail = []
        for name, _ in G.RARITIES:
            xs = [c["power"] for c in pool[name] if c["source"] == src]
            if not xs:
                continue
            lo, hi = min(xs), max(xs)
            detail.append(f"{name} {lo}~{hi}")
            if lo < prev_max - 0.6:
                good = False
            prev_max = hi
        check(good, f"{src} 难度单调升：{' | '.join(detail)}")

    # 分布
    cnt = collections.Counter()
    n = 6000
    for i in range(n):
        c = G.draw(str(600000 + i), "2026-11-23")
        cnt[c["rarity"]] += 1
    for name, pct in G.RARITIES:
        got = cnt[name] / n
        check(abs(got - pct) < 0.02, f"{name} 实测 {got*100:.1f}%（目标 {pct*100:.0f}%）")

    # 同日第 N 次固定 / 换天换卡
    a0 = G.draw("3035888633", "2026-11-23", 0)
    a0b = G.draw("3035888633", "2026-11-23", 0)
    a1 = G.draw("3035888633", "2026-11-23", 1)
    a_new_day = G.draw("3035888633", "2026-11-24", 0)
    check(a0["title"] == a0b["title"], "同一天第 0 抽固定")
    check((a0["title"], a0["rarity"]) != (a1["title"], a1["rarity"]), "同一天第 2 抽换卡")
    check((a0["title"], a0["rarity"]) != (a_new_day["title"], a_new_day["rarity"]), "换天换卡")

    # take()：一次两张 + 每日额度 + 收藏
    qq = "990001"
    r1 = G.take(qq, "2026-11-23", limit=2)
    check(len(r1["cards"]) == 2, f"第一次抽到 2 张：{[c['title'] for c in r1['cards']]}")
    check(r1["used"] == 2 and r1["limit"] == 2, f"额度记成 {r1['used']}/{r1['limit']}")
    check(r1["cards"][0]["title"] != r1["cards"][1]["title"], "两张不是同一张")
    r2 = G.take(qq, "2026-11-23", limit=2)
    check(r2["cards"] == [] and r2["used"] == 2, "第二次超出额度 → 不给卡")

    own = G.owned(qq)
    keys = {G.card_key(c) for c in r1["cards"]}
    check(len(own) == len(keys), f"收藏 {len(own)} 张（去重后）")
    check(G.take(qq, "2026-11-24", limit=2)["cards"], "换天额度重置")

    # 出图：双卡一张图 + 单卡
    png = G.render_two(r1["cards"], r1["used"], r1["limit"], r1["new"])
    check(bool(png) and png[:3] == b"\xff\xd8\xff", f"双卡图是 JPEG（{len(png or b'')//1024} KB）")
    single = G.render_card(r1["cards"][0])
    check(bool(single) and single[:3] == b"\xff\xd8\xff", "单卡也是 JPEG")

    bag, msg = G.render_bag(qq, 1)
    check(bool(bag) and bag[:3] == b"\xff\xd8\xff", f"卡包出图（{len(bag or b'')//1024} KB）")
    empty_bag, empty_msg = G.render_bag("990999", 1)
    check(empty_bag is None and "空" in empty_msg, f"空卡包给提示：{empty_msg}")

    # 卡包：每 40 张一页
    G._save_save({"users": {"990777": list(G.by_key())[:41]}, "daily": {}})
    b1, _ = G.render_bag("990777", 1)
    b2, _ = G.render_bag("990777", 2)
    b99, _ = G.render_bag("990777", 99)
    check(bool(b1) and bool(b2), "41 张 → 两页都能出图")
    check(b99 == b2, "页码越界钳到最后一页")
    G._save_save({"users": {"990777": list(G.by_key())[:40]}, "daily": {}})
    b40, _ = G.render_bag("990777", 1)
    check(b40 == G.render_bag("990777", 2)[0], "40 张只有一页（第 2 页钳回第 1 页）")

    # 卡池：卡池 [等级] [页数]
    p_default, _ = G.render_pool(qq, None, 1)
    check(bool(p_default), "不给等级 → 默认第一档第 1 页")
    p_ur, _ = G.render_pool(qq, "UR", 1)
    p_ur_big, _ = G.render_pool(qq, "UR", 99)
    check(bool(p_ur) and p_ur == p_ur_big, "UR 档能出图，页数越界钳到最后一页")
    p_bad, msg_bad = G.render_pool(qq, "SSSR", 1)
    check(p_bad is None and "五档" in msg_bad, f"乱填等级给提示：{msg_bad}")
    for name, _ in G.RARITIES:
        ok_page, _ = G.render_pool(qq, name, 1)
        check(bool(ok_page), f"{name} 档第 1 页能出图")
    check(all(p[0] in dict(G.RARITIES) for p in G.pool_pages()), "pool_pages 每页归属某个稀有度")

    # 存档保护：.bak 兜底 + 曲名松匹配
    G._save_save({"users": {"990888": list(G.by_key())[:2]}, "daily": {}})
    check(G.SAVE_BAK.exists(), "写盘会刷一份 gacha.json.bak")
    good = G.SAVE_FILE.read_text(encoding="utf-8")
    G.SAVE_FILE.write_text("{ 坏掉了", encoding="utf-8")
    recovered = G._load_save()
    check(bool(recovered["users"]), "主文件坏了自动退回 .bak")
    G.SAVE_FILE.write_text(good, encoding="utf-8")

    G._save_save({"users": {"990889": ["maimai|  ECHO  "]}, "daily": {}})
    loose = G.owned_cards("990889")
    check(bool(loose), f"曲名多了空格/大小写变了也能认回：{[c['title'] for c in loose]}")

    # 欧非榜
    check(hasattr(G, "luck_table") and hasattr(G, "render_luck_rank"), "欧非榜接口在")
    G._save_save({
        "users": {"900001": ["a"], "900002": ["b"], "900003": ["c"]},
        "daily": {},
        "stats": {
            "900001": {"n": 10, "score": 41, "N": 3, "R": 3, "SR": 3, "SSR": 0, "UR": 1},
            "900002": {"n": 10, "score": 12, "N": 8, "R": 2, "SR": 0, "SSR": 0, "UR": 0},
            "900003": {"n": 10, "score": 25, "N": 5, "R": 5, "SR": 0, "SSR": 0, "UR": 0},
        },
    })
    table = G.luck_table()
    check([r["qq"] for r in table] == ["900001", "900003", "900002"], f"按欧气降序：{[r['qq'] for r in table]}")
    check(abs(table[0]["luck"] - 86.2) < 0.05, f"欧气算对（64 进制 + 对数）：{table[0]['luck']:.1f}")
    check(G.luck_of({"n": 0}) == 0.0, "没抽过 → 欧气 0")
    check(G.luck_of({"n": 4, "UR": 4}) == 100.0, "全 UR → 欧气 100")
    luck_png, _ = G.render_luck_rank({"900001": "欧皇"})
    check(bool(luck_png) and luck_png[:3] == b"\xff\xd8\xff",
          f"欧非榜出图（{len(luck_png or b'')//1024} KB）")

    # 老存档没有 stats 时能从收藏补
    G._save_save({"users": {"900004": list(G.by_key())[:1]}, "daily": {}})
    G._ensure_stats(G._load_save())
    migrated = G.luck_table()
    check(any(r["qq"] == "900004" and r["n"] == 1 for r in migrated), "老存档能从收藏补出 stats")

    # bot 接线
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678828823"

    bot.USER_LAST.clear()
    n0 = len(SENT)
    await bot.handle_group(grp(881001, "抽卡"))
    check(any(s[0] == "image" for s in SENT[n0:]), "群里不 @「抽卡」→ 出卡")

    bot.USER_LAST.clear()
    n0 = len(SENT)
    await bot.handle_group(grp(881001, "卡包"))
    check(any(s[0] == "image" for s in SENT[n0:]), "群里不 @「卡包」→ 出图")

    bot.USER_LAST.clear()
    n0 = len(SENT)
    await bot.handle_group(grp(881001, "卡池 2"))
    check(any(s[0] == "image" for s in SENT[n0:]), "群里不 @「卡池 2」→ 出第 2 页")

    bot.PRI_HOUR.clear()
    n0 = len(SENT)
    await bot.handle_private(priv(881002, "抽卡"))
    check(any(s[0] == "image" for s in SENT[n0:]), "私聊「抽卡」出卡")

    bot.USER_LAST.clear()
    n0 = len(SENT)
    await bot.handle_group(grp(881003, "卡包啦"))
    check(len(SENT) == n0, f"「卡包啦」→ 没反应（{len(SENT)-n0} 条）")

    bot.USER_LAST.clear()
    n0 = len(SENT)
    await bot.handle_group(grp(881004, "欧非榜"))
    check(any(s[0] == "image" for s in SENT[n0:]), "群里不 @「欧非榜」→ 出图")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
