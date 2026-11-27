"""两个指令的文本行为测试：/help（分身份）与 /b50（必须带斜杠、必须给名字）。

出图部分由 b50_test.py / verify_cmd.py 覆盖，这里只验文本分支。
"""
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
PORT = 8105


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
            "group_id": 999, "user_id": qq, "sender": {"nickname": f"u{qq}", "card": ""},
            "message": [{"type": "at", "data": {"qq": "10000"}},
                        {"type": "text", "data": {"text": text}}]}


async def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 8916), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    shutil.copyfile(CFG, BAK)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    cfg["llm"].update({"base_url": "http://127.0.0.1:8916/v1", "api_key": "t", "model": "mock"})
    cfg["onebot"]["port"] = PORT
    cfg["private"] = {"cooldown_seconds": 0, "history_limit": 14, "max_per_hour": 999, "master_cooldown_seconds": 0}
    cfg["group"].update({"at_only": True, "user_cooldown_seconds": 0, "master_cooldown_seconds": 0, "max_per_minute": 99})
    cfg["budget"] = {"enabled": False}
    cfg["reply_delay"] = {"master": 0, "user": 0}
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

        async def send(e, wait=15.0):
            await ws.send(json.dumps(e, ensure_ascii=False))
            got, start = [], time.time()
            while time.time() - start < wait:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if d.get("action"):
                    got.append(d)
                    break
            return got

        def body(acts):
            return " ".join(
                "".join(s["data"].get("text", "") for s in a["params"]["message"] if s["type"] == "text")
                for a in acts
            )

        def kinds(acts):
            return [s["type"] for a in acts for s in a["params"]["message"]]

        # 1) 主人 /help
        b = body(await send(priv(3035888633, "/help")))
        good = "/b50" in b and "聊天" in b and "/set" in b and "/stage" not in b
        print(f"[{'OK' if good else 'FAIL'}] 主人 /help：有聊天+查分+管理命令，无 /stage")
        ok = ok and good

        # 2) 陌生人 /help
        b = body(await send(priv(4444, "/help")))
        good = "/b50" in b and "聊天" in b and "/set" not in b and "/stage" not in b
        print(f"[{'OK' if good else 'FAIL'}] 陌生人 /help：只有聊天和查分")
        ok = ok and good

        # 3) 群里 /help
        b = body(await send(grp(5555, " /help")))
        good = ("/b50" in b and "/随机西八" in b and "西八赞榜" in b
                and "两件事" not in b and len(b) < 600)
        print(f"[{'OK' if good else 'FAIL'}] 群里 /help：列全指令、不是「两件事」（{len(b)} 字）")
        ok = ok and good

        # 4) /b50 无名字 → 按 QQ 查（绑了出图，没绑提示去水鱼注册）
        got = await send(priv(3035888633, "/b50"))
        b = body(got)
        good = ("image" in kinds(got)) or ("水鱼" in b)
        print(f"[{'OK' if good else 'FAIL'}] /b50 无名字 → 按 QQ 查（{'出图' if 'image' in kinds(got) else b[:24]}）")
        ok = ok and good

        # 5) 无斜杠 b50 → 当聊天
        got = await send(priv(3035888633, "b50 Duoblade"))
        good = "image" not in kinds(got) and "用法" not in body(got)
        print(f"[{'OK' if good else 'FAIL'}] 无斜杠 b50 → 当聊天（她说：{body(got)[:20]}）")
        ok = ok and good

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
