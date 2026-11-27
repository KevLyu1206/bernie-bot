# -*- coding: utf-8 -*-
"""/随一首 /定数表 自检：等级解析、抽歌范围、曲绘墙、指令分发。

    python songcard_test.py
"""
from __future__ import annotations

import asyncio
import re

import bot
import songcard as S
import songdb

DS_TABLE_RE = re.compile(r"^/定数表\s*(\S*)(?:\s+(\d+))?\s*$", re.I)
RANDOM_SONG_RE = re.compile(r"^/随一首\s*(\S*)\s*$", re.I)

ok_all = True


def check(cond: bool, msg: str) -> None:
    global ok_all
    ok_all = ok_all and cond
    print(f"[{'OK' if cond else 'FAIL'}] {msg}")


# 1) 等级解析
for arg, want in (("13", ("13", False)), ("13+", ("13+", True)), ("1", ("1", False)),
                  ("15", ("15", False)), ("15+", ("15+", True))):
    check(S.parse_level(arg) == want, f"parse_level({arg!r}) → {S.parse_level(arg)}")
for bad in ("", "0", "16", "abc", "13++"):
    check(S.parse_level(bad) is None, f"parse_level({bad!r}) 拒绝")

# 2) 指令正则
for t in ("/随一首", "/随一首 13", "/随一首 13+"):
    check(bool(RANDOM_SONG_RE.match(t)), f"识别 {t}")
for t, want in (("/定数表 13", ("13", None)), ("/定数表 13+", ("13+", None)),
                ("/定数表 13 2", ("13", "2"))):
    m = DS_TABLE_RE.match(t)
    check(bool(m) and m.groups() == want, f"识别 {t} → {m.groups() if m else None}")

songdb.DB.load()
db = songdb.DB

# 3) 抽歌：13 含 13+，等级对得上，抽到的谱面标出来
for arg in ("1", "7", "13", "13+", "14", "15"):
    lv = S.parse_level(arg)
    rows = S.rows_at(db, lv[0])
    r = S.pick(db, lv[0])
    allowed = (lv[0],) if lv[1] else (lv[0], lv[0] + "+")
    check(r is not None and r["level"] in allowed,
          f"{arg}：{len(rows)} 张，抽到「{r['song'].get('title')}」{r['level']}")
    if r:
        info = S.song_info(db, r["song"], r["chart"])
        picked = [c for c in info["charts"] if c["picked"]]
        check(picked and picked[0]["level"] == r["level"], f"{arg}：抽到的那张标出来了")
        png = S.render_song(info, arg)
        check(bool(png) and len(png) > 15000, f"{arg}：单曲卡 {len(png or b'') // 1024} KB")

# 4b) 标题上的定数：只有真的存在 N+ 才写「N / N+」
check(S.level_label(db, "15") == "15", "15 不写 15+")
check(S.level_label(db, "13") == "13 / 13+", "13 写成 13 / 13+")
check(S.level_label(db, "13+") == "13+", "13+ 只写 13+")
check(S.level_label(db, "6") == "6", "没有 6+ 就不写")

# 4) 定数表：精确到定数，长图按小数分段，只有曲绘
rows13 = S.rows_at(db, "13", exact=True)
check(rows13 and all(r["level"] == "13" for r in rows13), f"13 精确：{len(rows13)} 张，全是 13")
rows13p = S.rows_at(db, "13+", exact=True)
check(rows13p and all(r["level"] == "13+" for r in rows13p), f"13+ 精确：{len(rows13p)} 张，全是 13+")
sections = S._group_by_value(rows13p)
labels = [lab for lab, _ in sections]
check(labels == ["13.6", "13.7", "13.8", "13.9"], f"13+ 分段 {labels}")
check(sum(len(ids) for _, ids in sections) >= 350, "分段里的曲绘总数对得上")
ids = S.unique_songs(rows13)
check(len(ids) == len(set(ids)) and len(ids) > 400, f"13 去重后 {len(ids)} 首")
for lv, rows in (("13", rows13), ("13+", rows13p)):
    png = S.render_wall(rows, lv)
    from PIL import Image
    import io as _io
    im = Image.open(_io.BytesIO(png))
    check(bool(png) and im.width == 760 and im.height > 2000,
          f"{lv} 长图 {im.width}×{im.height}，{len(png) // 1024} KB")
png = S.render_wall([], "9+")
check(bool(png), "空表也能出图")

# 5) 缺曲绘能找回来（Löschen 1788 存在 11788.png）
check(S.F._cached_cover(1788), "Löschen 的曲绘能从 DX/SD 变体找到")
check(S.F._cover(1788, 120) is not None, "Löschen 曲绘能读出来")

# 6) 指令进 handler（mock 发送）
SENT = []


async def fake_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, len(png), caption))


async def fake_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def grp(qq, text, at_me=True):
    segs = []
    if at_me:
        segs += [{"type": "at", "data": {"qq": "3678328823"}}, {"type": "text", "data": {"text": " "}}]
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "self_id": 3678328823,
            "group_id": 999, "user_id": qq, "sender": {"card": "t", "nickname": "t"},
            "message": segs, "raw_message": text}


def priv(qq, text):
    return {"post_type": "message", "message_type": "private", "self_id": 3678328823,
            "user_id": qq, "sender": {"nickname": "t"},
            "message": [{"type": "text", "data": {"text": text}}], "raw_message": text}


async def main() -> int:
    bot.BOT.send_image = fake_image
    bot.BOT.send_text = fake_text
    bot.BOT.self_id = "3678328823"

    async def priv_send(qq, text):
        bot.STORE.user(str(qq))["last_reply"] = 0    # 测试里不等冷却
        await bot.handle_private(priv(qq, text))

    await bot.handle_group(grp(1001, "/随一首 13"))
    check(SENT and SENT[-1][0] == "image", "@ 后 /随一首 13 → 出图")

    await priv_send(1002, "/定数表 14")
    check(SENT and SENT[-1][0] == "image", "私聊 /定数表 14 → 出图")

    n = len(SENT)
    await priv_send(1002, "/随一首 99")
    check(len(SENT) == n + 1 and SENT[-1][0] == "text", "/随一首 99 → 提示用法")

    n = len(SENT)
    await priv_send(1002, "/定数表")
    check(len(SENT) == n + 1 and SENT[-1][0] == "text", "/定数表 不带等级 → 提示用法")

    print("\n" + ("全部通过" if ok_all else "有失败项"))
    return 0 if ok_all else 1


raise SystemExit(asyncio.run(main()))
