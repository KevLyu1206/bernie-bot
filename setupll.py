#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 LuckyLillia 的 OneBot 反向 WS 接到 bot.py 上。

QQ 登录之后 LuckyLillia 会生成 data/config_<QQ号>.json，
本脚本自动把它（们）里的 ws-reverse 打开并指向 bot.py。

    python setupll.py                                  # 默认 ws://127.0.0.1:8080/onebot/v11/ws
    python setupll.py ws://127.0.0.1:8080/onebot/v11/ws

也可以直接改 default_config.json —— 那是「首次登录时用来生成配置」的模板。
本脚本是保险：QQ 登录后配置被重置、或者改了端口，再跑一次就行。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
LL_DIR = BASE / "LuckyLillia-Desktop-win-x64"
DEFAULT_CFG = LL_DIR / "bin" / "llbot" / "default_config.json"
DATA_DIR = LL_DIR / "data"

DEFAULT_URL = "ws://127.0.0.1:8080/onebot/v11/ws"

# 不同版本 Data 目录放的地方不一样，两个都找
DATA_DIRS = [LL_DIR / "bin" / "llbot" / "data", LL_DIR / "data"]


def patch_ob11(cfg: dict, url: str) -> bool:
    """把 ob11.connect 里的 ws-reverse 打开并改地址；没有就加一条。"""
    changed = False
    ob11 = cfg.setdefault("ob11", {})
    ob11["enable"] = True
    connect = ob11.setdefault("connect", [])

    found = False
    for item in connect:
        if item.get("type") == "ws-reverse":
            found = True
            if item.get("url") != url or not item.get("enable"):
                item["url"] = url
                item["enable"] = True
                item.setdefault("heartInterval", 60000)
                item.setdefault("token", "")
                item.setdefault("reportSelfMessage", False)
                item.setdefault("reportOfflineMessage", False)
                item["messageFormat"] = "array"
                item.setdefault("debug", False)
                changed = True

    if not found:
        connect.append(
            {
                "type": "ws-reverse",
                "enable": True,
                "url": url,
                "heartInterval": 60000,
                "token": "",
                "reportSelfMessage": False,
                "reportOfflineMessage": False,
                "messageFormat": "array",
                "debug": False,
            }
        )
        changed = True
    return changed


def patch_file(path: Path, url: str) -> str:
    raw = path.read_text(encoding="utf-8")
    try:
        cfg = json.loads(raw)
    except Exception as exc:
        return f"跳过 {path.name}：JSON 解析失败（{exc}）"

    changed = patch_ob11(cfg, url)
    if changed:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copyfile(path, backup)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"已修改 {path}（备份 {backup.name}）"
    return f"已经是接好的状态 {path}"


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    if not url.endswith("/onebot/v11/ws"):
        print(f"！注意：地址结尾通常是 /onebot/v11/ws，你给的是 {url}")

    if not LL_DIR.exists():
        print(f"！没找到 {LL_DIR}")
        print("  把 LuckyLillia 解压到本目录下，文件夹名保持 LuckyLillia-Desktop-win-x64")
        return 1

    print(f"目标地址：{url}\n")

    # 1) 默认模板：首次登录时会用它生成 config_<uin>.json
    if DEFAULT_CFG.exists():
        print("· " + patch_file(DEFAULT_CFG, url))
    else:
        print(f"· 没找到 {DEFAULT_CFG}（不影响，跳过）")

    # 2) 已经登录过的配置
    found_any = False
    for d in DATA_DIRS:
        if not d.exists():
            continue
        found_any = True
        files = sorted(d.glob("config_*.json"))
        if files:
            for f in files:
                print("· " + patch_file(f, url))
        else:
            print(f"· {d} 里还没有 config_<QQ号>.json —— 说明还没扫码登录过")
    if not found_any:
        print("· 没找到任何 data 目录 —— 说明 LuckyLillia 还没成功登录过 QQ")

    print("\n下一步：")
    print("  1) 先跑 bot.py（它要先把 ws://127.0.0.1:8080 听着）")
    print("  2) 再开 LuckyLillia，扫码登录小号")
    print("  3) bot 控制台出现「QQ 端已连接」就成功了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
