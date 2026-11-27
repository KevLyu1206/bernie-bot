# -*- coding: utf-8 -*-
"""/今日运势 离线自检：区间、随机性、每天换签口径、图卡。

    python fortune_test.py
"""
from __future__ import annotations

import datetime as dt
import re

import fortune as F
import songdb

FORTUNE_RE = re.compile(r"^[/!！]\s*(?:今日运势|运势|fortune)\s*$", re.I)

ok_all = True


def check(cond: bool, msg: str) -> None:
    global ok_all
    ok_all = ok_all and cond
    print(f"[{'OK' if cond else 'FAIL'}] {msg}")


# 1) 指令写法
for t in ("/今日运势", "/运势", "！今日运势", "!fortune"):
    check(bool(FORTUNE_RE.match(t)), f"识别 {t}")
for t in ("今日运势", "/今日运势 please", "/今日", "/b50"):
    check(not FORTUNE_RE.match(t), f"不误吃 {t}")

# 2) 区间（1000 个分数）
bad = [s for s in range(101) if F.kind_of(s) != (
    "凶" if s <= 24 else "平" if s <= 50 else "吉" if s <= 74 else "大吉"
)]
check(not bad, "0-24 凶 / 25-50 平 / 51-74 吉 / 75-100 大吉")
check(F.kind_of(-5) == "凶" and F.kind_of(999) == "大吉", "越界分数夹到两端")

# 3) 北京日期：UTC 23:59 → 北京次日 07:59
u = dt.timezone.utc
check(F.beijing_day(dt.datetime(2026, 11, 21, 15, 59, tzinfo=u)) == "2026-11-21", "北京 23:59 还是当天")
check(F.beijing_day(dt.datetime(2026, 11, 21, 16, 0, tzinfo=u)) == "2026-11-22", "北京 00:00 换签")

# 4) 同人同日固定，换人/换日变化
songdb.DB.load()
a1 = F.draw("3035888633", "2026-11-22", songdb.DB)
a2 = F.draw("3035888633", "2026-11-22", songdb.DB)
b = F.draw("10001", "2026-11-22", songdb.DB)
c = F.draw("3035888633", "2026-11-23", songdb.DB)
check(a1["score"] == a2["score"] and a1["food"] == a2["food"], "同一人同一天结果固定")
check((a1["score"], a1["food"]["item"]) != (b["score"], b["food"]["item"]), "换人有变化")
check((a1["score"], a1["song"]["id"] if a1["song"] else 0) !=
      (c["score"], c["song"]["id"] if c["song"] else 0), "换日有变化")

# 5) 分布：不同人不该都挤在同一个区间
kinds = {}
for i in range(300):
    k = F.draw(str(90000 + i), "2026-11-22", songdb.DB)["kind"]
    kinds[k] = kinds.get(k, 0) + 1
check(all(kinds.get(k, 0) > 0 for k in ("凶", "平", "吉", "大吉")), f"四个等级都出得来 {kinds}")

# 6) 曲子：有曲绘、曲师、MASTER 定数与谱师；有 Re:MASTER 就一起给
import random as _random
for kind in ("凶", "平", "吉", "大吉"):
    s = F.pick_song(_random.Random(7), F.get_db(), kind)
    check(s is not None, f"{kind} 抽得到曲子")
    if not s:
        continue
    ids = [c["index"] for c in s["charts"]]
    check(F._cached_cover(s["id"]), f"{kind}：{s['title']} 有本地曲绘")
    check(bool(s["artist"]) and s["artist"] != "未知", f"{kind}：曲师 = {s['artist']}")
    check(all(c["index"] in (3, 4) and c["level"] and c["designer"] for c in s["charts"]),
          f"{kind}：谱面行 = " + " / ".join(f"{c['name']} {c['level']} {c['designer']}" for c in s["charts"]))
    check(3 in ids, f"{kind}：有 MASTER 行")
    check(bool(s["version"]) and not s["version"].isdigit(), f"{kind}：版本 = {s['version']}")

# 7) 图卡能出图，四种等级都渲染一遍
for kind, score in (("凶", 3), ("平", 30), ("吉", 60), ("大吉", 95)):
    r = F.draw("42", "2026-11-22", songdb.DB)
    r["score"], r["kind"] = score, kind
    png = F.render_card(r)
    check(bool(png) and len(png) > 20000, f"{kind} 图卡 {len(png or b'') // 1024} KB")

# 8) 文字版不空、带曲师
txt = F.text_of(a1)
check("今日运势" in txt and "曲师" in txt and a1["kind"] in txt, "文字版齐全")

print("\n" + ("全部通过" if ok_all else "有失败项"))
raise SystemExit(0 if ok_all else 1)
