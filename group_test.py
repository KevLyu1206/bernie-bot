#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""70 人活跃群的触发规则测试：at_only / 按人冷却 / 每分钟上限 / 群规模提示。

用假 LLM + 假 OneBot，不连真 QQ 不烧 token。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websockets

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
CFG = BASE / "config.json"
BACKUP = BASE / "config.json.testbak"
REPLY = "……在、在的。有什么事吗。"
PORT = 8090
LAST_SYS: dict = {}


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            payload = {}
        for m in payload.get("messages", []):
            if m.get("role") == "system":
                LAST_SYS["text"] = m["content"]
                break
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": REPLY}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def make_evt(qq: int, text: str, at: bool, gid: int = 888) -> dict:
    msg = []
    if at:
        msg.append({"type": "at", "data": {"qq": "10000"}})
    msg.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 10000,
        "group_id": gid,
        "user_id": qq,
        "sender": {"nickname": f"同学{qq % 1000}", "card": ""},
        "message": msg,
    }


async def wait_action(ws, timeout=12, idle=None):
    """读一条 action。给了 idle 就连续读到静默为止，返回整批（一次回复现在会分几条发）。"""
    if idle is None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            d = json.loads(raw)
            return d if d.get("action") else None
        except asyncio.TimeoutError:
            return None
    got = []
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=idle)
            d = json.loads(raw)
            if d.get("action"):
                got.append(d)
        except asyncio.TimeoutError:
            return got


async def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8902), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    shutil.copyfile(CFG, BACKUP)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    cfg["llm"].update({"base_url": "http://127.0.0.1:8902/v1", "api_key": "t", "model": "mock"})
    cfg["onebot"]["port"] = PORT
    cfg["group"].update({"at_only": True, "chat_enabled": True, "user_cooldown_seconds": 30, "master_cooldown_seconds": 30, "max_per_minute": 2, "context_window": 25})
    cfg["private"] = {"cooldown_seconds": 30, "history_limit": 14, "max_per_hour": 80, "master_cooldown_seconds": 30}
    cfg["master_ids"] = list(cfg.get("master_ids", [])) + [7777]
    cfg["reply_delay"] = {"master": 0, "user": 0}
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    (BASE / "state.json").unlink(missing_ok=True)

    proc = subprocess.Popen(
        [sys.executable, "bot.py"], cwd=str(BASE),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    ok = True
    try:
        ws = None
        for _ in range(40):
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{PORT}/onebot/v11/ws")
                break
            except Exception:
                await asyncio.sleep(0.25)
        if ws is None:
            print("[FAIL] bot 没起来")
            return 1
        print("[OK] 已连上")

        async def check(name, evt, expect_reply, idle=3.0):
            nonlocal ok
            await ws.send(json.dumps(evt, ensure_ascii=False))
            got = await wait_action(ws, idle=idle)  # 一整批（一次回复会分几条发）
            good = (len(got) > 0) == expect_reply
            preview = ""
            if got:
                preview = " -> " + " / ".join(
                    "".join(s["data"].get("text", "") for s in a["params"]["message"] if s["type"] == "text")[:16]
                    for a in got
                )
            print(("[OK] " if good else "[FAIL] ") + name + preview)
            ok = ok and good

        # 1) 不带 @ 就不理（即使提到名字）
        await check("不 @ 只提名字 → 不回复", make_evt(111, "贝尔你在吗", at=False), False)

        # 2) @ 才回复
        await check("@ 她 → 回复", make_evt(222, " 在吗", at=True), True)

        # 3) 同一人 1 秒内再 @ → 被按人冷却吞掉
        await check("同一人连发 → 冷却吞掉", make_evt(222, " 再说一句", at=True), False)

        # 4) 另一个人立刻 @ → 照常回复（证明是按人不是按群）
        await check("换个人 @ → 立刻回复（按人冷却）", make_evt(333, " 你好", at=True), True)

        # 5) 已达 max_per_minute=2，第三个人 @ 应被整群限速拦下
        await check("超过每分钟上限 → 拦下", make_evt(444, " 喂", at=True), False)

        # 6) 群规模提示注入了（人数从 config 里读，不写死）
        size = int(json.loads(BACKUP.read_text(encoding="utf-8"))["group"].get("group_size", 0)) if False else int(cfg["group"]["group_size"])
        sys_txt = LAST_SYS.get("text", "")
        marker = "连名字都没见过" if size >= 150 else "只有被点到名"
        good = f"{size} 个人" in sys_txt and marker in sys_txt
        print(("[OK] " if good else "[FAIL] ") + f"system 注入了 {size} 人群的规模提示")
        ok = ok and good

        # 7) 群聊回复变短
        good = "群聊" in sys_txt and "最多回 2 条" in sys_txt
        print(("[OK] " if good else "[FAIL] ") + "system 群聊最多 2 条、每条短")
        ok = ok and good

        # 8) 好友申请自动同意
        await ws.send(json.dumps({"post_type": "request", "request_type": "friend", "self_id": 10000,
                                  "user_id": 5555, "flag": "flag-abc", "comment": ""}, ensure_ascii=False))
        got = await wait_action(ws, idle=3)
        first = got[0] if got else None
        good = (
            first is not None
            and first.get("action") == "set_friend_add_request"
            and first["params"].get("approve") is True
            and first["params"].get("flag") == "flag-abc"
        )
        print(("[OK] " if good else "[FAIL] ") + "好友申请 → 自动同意")
        ok = ok and good

        # 9) 非主人私聊：注入硬性尺度约束
        def pri(u, text="抱抱好不好"):
            return {"post_type": "message", "message_type": "private", "self_id": 10000,
                    "user_id": u, "sender": {"nickname": f"p{u}"},
                    "message": [{"type": "text", "data": {"text": text}}]}
        await ws.send(json.dumps(pri(333), ensure_ascii=False))
        got = await wait_action(ws, idle=3)
        sys_txt = LAST_SYS.get("text", "")
        good = got is not None and "硬性约束" in sys_txt and "阶段 ③ 之前" in sys_txt
        print(("[OK] " if good else "[FAIL] ") + "非主人阶段1 → 注入尺度硬约束")
        ok = ok and good

        # 10) 主人私聊：不该有那条约束
        await ws.send(json.dumps(pri(3035888633), ensure_ascii=False))
        got = await wait_action(ws, idle=3)
        sys_txt = LAST_SYS.get("text", "")
        good = got is not None and "硬性约束" not in sys_txt and "唯一的主人" in sys_txt
        print(("[OK] " if good else "[FAIL] ") + "主人私聊 → 无尺度约束、认主人")
        ok = ok and good

        # 11) 主人连发 3 条 → 只回第 1 条（主人也有冷却，测试设为 2 秒）
        replies = 0
        for i in range(3):
            await ws.send(json.dumps(pri(7777, f"连发{i}"), ensure_ascii=False))
            if await wait_action(ws, idle=2.5):
                replies += 1
        good = replies == 1
        print(("[OK] " if good else "[FAIL] ") + f"主人连发 3 条 → 回了 {replies} 条（期望 1，30 秒冷却）")
        ok = ok and good

        # 12) 非主人连发 3 条 → 只有第一条回（5 秒冷却）
        replies = 0
        for i in range(3):
            await ws.send(json.dumps(pri(6666, f"连发{i}"), ensure_ascii=False))
            if await wait_action(ws, idle=2.5):
                replies += 1
        good = replies == 1
        print(("[OK] " if good else "[FAIL] ") + f"非主人连发 3 条 → 回了 {replies} 条（期望 1）")
        ok = ok and good

        await ws.close()
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        server.shutdown()
        shutil.move(BACKUP, CFG)
        if out:
            print("\n---- bot 日志（末 10 行）----")
            print("\n".join(out.strip().splitlines()[-10:]))

    print("\n结果：" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
