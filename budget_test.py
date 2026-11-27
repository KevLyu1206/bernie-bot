#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预算闸门测试：花费累计到上限 → 只回「明天再来吧」，不再调模型。

假 LLM 每次报 100 万 prompt token（按 2 元/百万算 = 每次 2 元），
daily_cny = 3，所以第二次就该超。
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
BAK = BASE / "config.json.testbak"
PORT = 8091
CALLS = {"n": 0}


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            self.rfile.read(n)
        except Exception:
            pass
        CALLS["n"] += 1
        body = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": "……在、在的。"}}],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "prompt_cache_miss_tokens": 1_000_000,
                    "prompt_cache_hit_tokens": 0,
                    "completion_tokens": 10,
                },
            },
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def grp(qq: int, text: str) -> dict:
    return {
        "post_type": "message", "message_type": "group", "self_id": 10000,
        "group_id": 999, "user_id": qq, "sender": {"nickname": f"u{qq}", "card": ""},
        "message": [{"type": "at", "data": {"qq": "10000"}}, {"type": "text", "data": {"text": text}}],
    }


def pri(qq: int, text: str) -> dict:
    return {
        "post_type": "message", "message_type": "private", "self_id": 10000,
        "user_id": qq, "sender": {"nickname": f"u{qq}"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


async def recv_text(ws, timeout=12):
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        d = json.loads(raw)
        if not d.get("action"):
            return None
        return "".join(s["data"].get("text", "") for s in d["params"]["message"] if s["type"] == "text")
    except asyncio.TimeoutError:
        return None


async def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8903), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    shutil.copyfile(CFG, BAK)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    cfg["llm"].update({"base_url": "http://127.0.0.1:8903/v1", "api_key": "t", "model": "mock"})
    cfg["onebot"]["port"] = PORT
    cfg["group"].update({"user_cooldown_seconds": 0, "max_per_minute": 10, "at_only": True, "chat_enabled": True})
    cfg["private"] = {"cooldown_seconds": 0, "history_limit": 14, "max_per_hour": 999}
    cfg["budget"] = {
        "enabled": True, "user_daily_cny": 2.0, "global_daily_cny": 100.0,
        "prompt_price_per_m": 2.0, "cache_hit_price_per_m": 0.5, "completion_price_per_m": 8.0,
        "exhausted_reply": "明天再来吧",
    }
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

        def state_cost():
            try:
                st = json.loads((BASE / "state.json").read_text(encoding="utf-8"))
                return float(st.get("meta", {}).get("cost", 0))
            except Exception:
                return -1.0

        # 第一次：正常回（花掉 2 元，正好用完个人额度）
        await ws.send(json.dumps(grp(101, " 在吗"), ensure_ascii=False))
        t1 = await recv_text(ws)
        await asyncio.sleep(0.5)
        c1 = state_cost()
        good = t1 is not None and "在的" in t1 and c1 > 1.5
        print(f"[{'OK' if good else 'FAIL'}] 第1次正常回复（花费 {c1:.2f} 元，个人额度 2 元）")
        ok = ok and good

        # 第二次：同一个人已满 2 元 → 直接回绝
        before2 = CALLS["n"]
        await ws.send(json.dumps(grp(101, " 你好"), ensure_ascii=False))
        t2 = await recv_text(ws)
        good = (t2 or "").strip() == "明天再来吧" and CALLS["n"] == before2
        print(f"[{'OK' if good else 'FAIL'}] 第2次 → 「{t2}」，未调模型（{before2}→{CALLS['n']}）")
        ok = ok and good

        # 第三次：换一个人，额度是满的 → 正常回
        before = CALLS["n"]
        await ws.send(json.dumps(grp(202, " 我是新来的"), ensure_ascii=False))
        t3 = await recv_text(ws)
        good = t3 is not None and "在的" in t3 and CALLS["n"] > before
        print(f"[{'OK' if good else 'FAIL'}] 换个人 → 正常回复（另算额度）")
        ok = ok and good

        # 第四次（同一人一分钟内）：只回绝一次，不重复喊
        await ws.send(json.dumps(grp(101, " 再试试"), ensure_ascii=False))
        t4 = await recv_text(ws, timeout=3)
        good = t4 is None
        print(f"[{'OK' if good else 'FAIL'}] 同一人一分钟内不重复回绝（收到 {t4!r}）")
        ok = ok and good

        # 私聊：同一个人同样被拦
        await ws.send(json.dumps(pri(101, "在吗"), ensure_ascii=False))
        t5 = await recv_text(ws)
        good = (t5 or "").strip() == "明天再来吧"
        print(f"[{'OK' if good else 'FAIL'}] 私聊 → 「{t5}」")
        ok = ok and good

        # 跨天重置
        st = json.loads((BASE / "state.json").read_text(encoding="utf-8"))
        st["meta"]["date"] = "2000-01-01"
        (BASE / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        # 进程内存里还是旧日期，需要重启才生效 → 这里只验证字段结构
        has_fields = all(k in st["meta"] for k in ("date", "cost", "ptok", "ctok", "calls"))
        print(f"[{'OK' if has_fields else 'FAIL'}] state.json 里有 date/cost/ptok/ctok/calls 字段")
        ok = ok and has_fields

        await ws.close()
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        server.shutdown()
        shutil.move(BAK, CFG)
        if out:
            print("\n---- bot 日志（末 8 行）----")
            print("\n".join(out.strip().splitlines()[-8:]))

    print("\n结果：" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
