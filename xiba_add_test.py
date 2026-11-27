# -*- coding: utf-8 -*-
"""/添加西八 的自检（mock get_msg + 本地假图，不连 QQ）。

    python xiba_add_test.py

覆盖：
    1. 引用一条带图的消息 → 图被存进 西八/（md5 命名）
    2. 同一张再发一次 → 提示「已经有了」，不重复存
    3. 非主人 → 只回一句
    4. 没引用、自己也没带图 → 提示要引用
    5. 本条消息直接带图（不引用）也能存
"""
from __future__ import annotations

import asyncio
import hashlib
import io

from PIL import Image

import bot

SENT: list[tuple] = []


async def fake_send_image(mt, target, png, caption=""):
    SENT.append(("image", mt, target, png))


async def fake_send_text(mt, target, text, at_qq=None):
    SENT.append(("text", mt, target, text))


def grp(qq, text, gid=222333, at_me=False, segs=None):
    segs = list(segs or [])
    if at_me:
        segs.insert(0, {"type": "at", "data": {"qq": "3678328823"}})
    segs.insert(0, {"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "self_id": 3678328823,
            "group_id": gid, "user_id": qq, "sender": {"card": "测试", "nickname": "测试"},
            "message": segs, "raw_message": text}


def priv(qq, text, segs=None):
    return {"post_type": "message", "message_type": "private", "self_id": 3678328823,
            "user_id": qq, "sender": {"nickname": "测试"},
            "message": [{"type": "text", "data": {"text": text}}] + list(segs or []),
            "raw_message": text}


def make_png(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buf, format="PNG")
    return buf.getvalue()


async def main() -> int:
    bot.BOT.send_image = fake_send_image
    bot.BOT.send_text = fake_send_text
    bot.BOT.self_id = "3678328823"

    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"[{'OK' if cond else 'FAIL'}] {msg}")

    master = str((bot.CFG.get("master_ids") or ["3035888633"])[0])
    png = make_png((20, 160, 90))
    md5 = hashlib.md5(png).hexdigest()
    target = bot.X8_DIR / f"{md5}.png"
    target.unlink(missing_ok=True)

    # 假图落在临时文件里，get_msg 返回 file 指向它
    tmp = bot.BASE_DIR / "_xiba_add_tmp.png"
    tmp.write_bytes(png)

    async def fake_api(action, params, timeout=12.0):
        if action == "get_msg":
            return {"message_id": params.get("message_id"),
                    "message": [{"type": "image", "data": {"file": str(tmp)}}]}
        return {}

    bot.BOT.api = fake_api  # type: ignore[assignment]

    reply_seg = [{"type": "reply", "data": {"id": "12345"}}]

    n = len(SENT)
    await bot.handle_private(priv(master, "/添加西八", reply_seg))
    check(target.exists(), f"引用带图消息 → 存进 {target.name}")
    check(SENT[-1][0] == "text" and "新存 1 张" in SENT[-1][3], f"回话：{SENT[-1][3] if SENT else ''}")

    n = len(SENT)
    await bot.do_add_xiba(master, "/添加西八", "private", int(master), reply_seg)
    check(SENT[-1][0] == "text" and "已经有了" in SENT[-1][3], f"重复 → {SENT[-1][3]}")

    # 群友权限：现在默认开着，谁都能加；把开关关掉才拦
    _cfg = bot.CFG.setdefault("xiba", {})
    check(not _cfg.get("only_master", True), "xiba.only_master = false（群友也能加）")
    n = len(SENT)
    await bot.handle_group(grp(77002, "/添加西八", at_me=True, segs=reply_seg))
    check(SENT[-1][0] == "text" and "已经有了" in SENT[-1][3],
          f"群友也能加（重复图）→ {SENT[-1][3]}")

    _cfg["only_master"] = True
    n = len(SENT)
    await bot.do_add_xiba("77002", "/添加西八", "private", 77002, reply_seg)   # 绕开群冷却
    check(SENT[-1][0] == "text" and "主人" in SENT[-1][3],
          f"开关关掉后非主人被拦 → {SENT[-1][3]}")
    _cfg["only_master"] = False

    n = len(SENT)
    await bot.do_add_xiba(master, "/添加西八", "private", int(master), None)
    check(SENT[-1][0] == "text" and "引用" in SENT[-1][3], f"没图 → {SENT[-1][3]}")

    # 直接带图（不引用）
    png2 = make_png((200, 60, 60))
    md5b = hashlib.md5(png2).hexdigest()
    target2 = bot.X8_DIR / f"{md5b}.png"
    target2.unlink(missing_ok=True)
    tmp2 = bot.BASE_DIR / "_xiba_add_tmp2.png"
    tmp2.write_bytes(png2)
    await bot.do_add_xiba(master, "/添加西八", "private", int(master),
                          [{"type": "image", "data": {"file": str(tmp2)}}])
    check(target2.exists(), "自己带的图也能存")

    # 收尾：删临时图和测试存进去的两张
    for f in (tmp, tmp2, target, target2):
        f.unlink(missing_ok=True)

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
