#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地测试台：不接 QQ，直接在终端扮演群友 / 私聊聊贝尔娜提塔。

用同一个 bot.py 的管道（人格 + 世界书 + 好感度 + 分条发送），所以在这里调顺了，
接上 QQ 就是一样的效果。

    python console.py                # 以主人身份私聊
    python console.py 22222          # 以 QQ 22222 的同学身份私聊
    python console.py 22222 /group    # 直接以群聊模式进入

游戏内命令：
    /as 22222 小明      切换身份（换个群友来聊）
    /group              切到群聊（相当于在群里 @ 她）
    /private            切回私聊
    /stage              看当前身份的好感阶段
    /set 4              改阶段
    /who                看自己在 bot 眼里是谁
    /history            看当前身份最近的对话记录
    /quit               退出（自动存盘）
"""

from __future__ import annotations

import asyncio
import logging
import random
import sys

import bot

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# 群聊模式下喂给她看的「刚才群里在聊」，让上下文更像真群
FAKE_GROUP_CHAT = [
    "甲：今天的讲义谁有啊",
    "乙：我有，等下发群里",
    "多洛：贝尔今天来上课了吗",
]


def role_of(qq: str) -> str:
    return "老师 · 唯一的主人" if bot.is_master(qq) else "同学"


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not bot.CFG["llm"].get("api_key"):
        print("！config.json 里没有 api_key，先填一个（或 set LLM_API_KEY=...）")
        return

    bot.LLM_CLIENT = bot.LLM()

    argv = [a for a in sys.argv[1:] if a]
    qq = argv[0] if argv and argv[0].isdigit() else str(bot.CFG["master_ids"][0] if bot.CFG["master_ids"] else "10001")
    surface = "group" if "/group" in argv or "--group" in argv else "private"
    nick = "Kevin" if bot.is_master(qq) else f"同学{qq[-4:]}"

    u = bot.STORE.user(qq)
    if not u.get("name"):
        u["name"] = nick
    else:
        nick = u["name"]

    def header() -> None:
        where = "群聊（相当于在群里 @ 她）" if surface == "group" else "私聊"
        print(f"—— {nick}（{role_of(qq)}，QQ {qq}）· {where} · 阶段 {u['stage']} ——")
        print("   /group 切群聊   /private 切私聊   /as 22222 换个人   /quit 退出\n")

    print("========================================")
    print("  贝尔娜提塔 · 本地测试台")
    print("========================================\n")
    header()

    while True:
        try:
            tag = f"{nick}@{'群' if surface == 'group' else '私'}> "
            line = input(tag).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        cmd = line.split(maxsplit=2)
        head = cmd[0].lower()

        if head in ("/quit", "/q", "/exit"):
            break
        if head == "/group":
            surface = "group"
            header()
            continue
        if head == "/private":
            surface = "private"
            header()
            continue
        if head == "/as":
            if len(cmd) < 2 or not cmd[1].isdigit():
                print("（用法：/as 22222 [昵称]）")
                continue
            qq = cmd[1]
            u = bot.STORE.user(qq)
            nick = cmd[2] if len(cmd) > 2 else (u.get("name") or f"同学{qq[-4:]}")
            u["name"] = nick
            print()
            header()
            continue
        if head == "/stage":
            print(f"（阶段 {u['stage']} · {bot.STAGE_NAMES.get(int(u['stage']), '?')} · 已聊 {u['msg_count']} 条）")
            continue
        if head == "/set":
            try:
                u["stage"] = max(1, min(5, int(cmd[1])))
                print(f"（阶段改为 {u['stage']} · {bot.STAGE_NAMES.get(u['stage'], '?')}）")
            except Exception:
                print("（用法：/set 1-5）")
            continue
        if head == "/who":
            print(f"（身份：{role_of(qq)}｜昵称 {nick}｜阶段 {u['stage']}｜场合 {'群聊' if surface == 'group' else '私聊'}）")
            continue
        if head == "/history":
            hist = u["history"][-10:]
            if not hist:
                print("（还没有对话记录）")
            for m in hist:
                who = "你" if m["role"] == "user" else "贝尔"
                print(f"  {who}: {m['content'][:80]}")
            print()
            continue
        if head in ("/help", "/?"):
            print("  /group  /private  /as <qq> [昵称]  /stage  /set n  /who  /history  /quit")
            continue

        ctx = "群聊" if surface == "group" else "私聊"
        recent = FAKE_GROUP_CHAT if surface == "group" else None
        shown = f"@贝尔 {line}" if surface == "group" else line
        messages = bot.build_messages(qq, nick, ctx, shown, recent)
        reply = await bot.LLM_CLIENT.chat(messages)
        if not reply:
            print("（……没有回应。检查 api_key / 网络）\n")
            continue

        reply, delta = bot.clean_reply(reply, ctx)
        parts = bot.split_replies(reply, ctx)

        u["stage"] = max(1, min(5, int(u["stage"]) + delta))
        u["history"].append({"role": "user", "content": shown})
        u["history"].append({"role": "assistant", "content": " ".join(parts)})
        u["history"] = u["history"][-40:]
        u["msg_count"] = int(u.get("msg_count", 0)) + 1
        await bot.STORE.save()

        print()
        for i, piece in enumerate(parts):
            print(f"贝尔> {piece}")
            if i < len(parts) - 1:
                await asyncio.sleep(random.uniform(0.5, 1.0))
        if delta:
            print(f"      （好感 {delta:+d} → 阶段 {u['stage']} {bot.STAGE_NAMES.get(int(u['stage']), '')}）")
        print()

    await bot.STORE.save()
    print("（已保存 state.json）")


if __name__ == "__main__":
    asyncio.run(main())
