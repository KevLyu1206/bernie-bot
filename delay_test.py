"""延迟回复测试：收到消息后要等 N 秒才发出去。"""
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websockets

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
CFG = BASE / "config.json"
BAK = BASE / "config.json.testbak"
PORT = 8094


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            self.rfile.read(n)
        except Exception:
            pass
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "……在的。"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def priv(qq, text):
    return {"post_type": "message", "message_type": "private", "self_id": 10000,
            "user_id": qq, "sender": {"nickname": f"u{qq}"},
            "message": [{"type": "text", "data": {"text": text}}]}


async def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 8906), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    shutil.copyfile(CFG, BAK)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    cfg["llm"].update({"base_url": "http://127.0.0.1:8906/v1", "api_key": "t", "model": "mock"})
    cfg["onebot"]["port"] = PORT
    # 缩短冷却，只看延迟
    cfg["private"] = {"cooldown_seconds": 0, "history_limit": 14, "max_per_hour": 999, "master_cooldown_seconds": 0}
    cfg["budget"] = {"enabled": False, "daily_cny": 3.0}
    cfg["reply_delay"] = {"master": 3, "user": 6}
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    (BASE / "state.json").unlink(missing_ok=True)

    proc = subprocess.Popen([sys.executable, "bot.py"], cwd=str(BASE),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
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

        async def measure(qq, label, expect_min):
            nonlocal ok
            t0 = time.time()
            await ws.send(json.dumps(priv(qq, "在吗"), ensure_ascii=False))
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=20)
                    d = json.loads(raw)
                    if d.get("action"):
                        break
            except asyncio.TimeoutError:
                print(f"[FAIL] {label} 超时没回复")
                ok = False
                return
            elapsed = time.time() - t0
            good = elapsed >= expect_min
            print(f"[{'OK' if good else 'FAIL'}] {label}：{elapsed:.1f} 秒后回复（要求 ≥{expect_min}s）")
            ok = ok and good

        await measure(3035888633, "主人（延迟设 3 秒）", 2.8)
        await measure(6666, "群友（延迟设 6 秒）", 5.8)

        await ws.close()
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srv.shutdown()
        shutil.move(BAK, CFG)

    print("\n结果：" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
