"""群聊 / 私聊两个场合的催促话术分别生效。"""
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
PORT = 8104

GROUP_LINES = {"等、等一下啦，贝尔还在想。", "别催别催，贝尔在打字了。", "……那个，再等我一下下。", "呜，马上就好，真的。"}
PRIV_LINES = {"等一下下……贝尔还在想。", "别催啦，贝尔在打字了。", "……呜，再给贝尔几秒钟。"}


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            self.rfile.read(n)
        except Exception:
            pass
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "……在的。"}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
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


def grp(qq, text):
    return {"post_type": "message", "message_type": "group", "self_id": 10000,
            "group_id": 888, "user_id": qq, "sender": {"nickname": f"u{qq}", "card": ""},
            "message": [{"type": "at", "data": {"qq": "10000"}},
                        {"type": "text", "data": {"text": text}}]}


async def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 8915), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    shutil.copyfile(CFG, BAK)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    cfg["llm"].update({"base_url": "http://127.0.0.1:8915/v1", "api_key": "t", "model": "mock"})
    cfg["onebot"]["port"] = PORT
    # 冷全部清零，只看延迟期间的催促
    cfg["private"] = {"cooldown_seconds": 0, "history_limit": 14, "max_per_hour": 999, "master_cooldown_seconds": 0}
    cfg["group"].update({"at_only": True, "chat_enabled": True, "user_cooldown_seconds": 0, "master_cooldown_seconds": 0, "max_per_minute": 99})
    cfg["budget"] = {"enabled": False}
    cfg["reply_delay"] = {"master": 5, "user": 5}
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

        async def collect(seconds):
            got, end = [], time.time() + seconds
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.2, end - time.time()))
                except asyncio.TimeoutError:
                    break
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if d.get("action"):
                    got.append("".join(s["data"].get("text", "") for s in d["params"]["message"] if s["type"] == "text"))
            return got

        # 1) 群里：@ 一次，趁她打字再 @
        await ws.send(json.dumps(grp(5555, " 在吗"), ensure_ascii=False))
        await asyncio.sleep(1.0)
        await ws.send(json.dumps(grp(5555, " 喂"), ensure_ascii=False))
        msgs = await collect(8)
        nags = [m for m in msgs if m in GROUP_LINES]
        good = len(nags) >= 1 and all(m in GROUP_LINES for m in nags)
        print(f"[{'OK' if good else 'FAIL'}] 群里被催 → {nags[:1] or '没催'}（全部来自群聊话术：{good}）")
        ok = ok and good
        wrong = [m for m in msgs if m in PRIV_LINES]
        good2 = not wrong
        print(f"[{'OK' if good2 else 'FAIL'}] 群里没串用私聊话术（串了：{wrong}）")
        ok = ok and good2

        # 2) 私聊：发一条，趁她打字再发
        await asyncio.sleep(1.0)
        await ws.send(json.dumps(priv(9999, "在吗"), ensure_ascii=False))
        await asyncio.sleep(1.0)
        await ws.send(json.dumps(priv(9999, "喂"), ensure_ascii=False))
        msgs = await collect(8)
        nags = [m for m in msgs if m in PRIV_LINES]
        good = len(nags) >= 1 and all(m in PRIV_LINES for m in nags)
        print(f"[{'OK' if good else 'FAIL'}] 私聊被催 → {nags[:1] or '没催'}（全部来自私聊话术：{good}）")
        ok = ok and good
        wrong = [m for m in msgs if m in GROUP_LINES]
        good2 = not wrong
        print(f"[{'OK' if good2 else 'FAIL'}] 私聊没串用群聊话术（串了：{wrong}）")
        ok = ok and good2

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
