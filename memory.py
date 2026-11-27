"""把 state.json 里的记忆导出成人类可读的 Markdown。

    memory/主人.md   —— 只放主人（master_ids）
    memory/群友.md   —— 所有群友，按 QQ 分节

state.json 仍然是运行时真相，这里只是给人看的镜像；每次存盘后重新生成。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

MEM_DIR = Path(__file__).resolve().parent / "memory"

STAGE_NAMES = {1: "门缝", 2: "试探", 3: "依赖", 4: "心动", 5: "恋人"}


def _headline(qq: str, u: dict[str, Any]) -> list[str]:
    stage = int(u.get("stage", 1))
    cost = float(u.get("cost", 0.0))
    calls = int(u.get("calls", 0))
    lines = [
        f"- 好感阶段：{stage}（{STAGE_NAMES.get(stage, '?')}）",
        f"- 今天花了：{cost:.3f} 元 / {calls} 次调用",
        f"- 累计消息：{int(u.get('msg_count', 0))} 条",
        f"- 最近更新：{time.strftime('%Y-%m-%d %H:%M')}",
    ]
    return lines


def _history_lines(hist: list[dict[str, str]], limit: int = 60) -> list[str]:
    rows = hist[-limit:] if limit else hist
    out = []
    for m in rows:
        who = "你" if m.get("role") == "user" else "贝尔"
        text = (m.get("content") or "").replace("\n", " ").strip()
        out.append(f"- **{who}**：{text}")
    return out or ["- （还没有对话）"]


def _summary_lines(summaries: list[dict[str, str]], limit: int = 12) -> list[str]:
    rows = summaries[-limit:]
    if not rows:
        return []
    out = ["", "### 记忆摘要"]
    for s in rows:
        stamp = s.get("date", "")
        text = (s.get("text") or "").strip()
        text = text.replace("\n", " / ")
        out.append(f"- `{stamp}` {text}")
    return out


def export(users: dict[str, dict[str, Any]], master_ids: list[int]) -> None:
    MEM_DIR.mkdir(exist_ok=True)
    masters, others = [], []

    for qq, u in users.items():
        try:
            is_master = int(qq) in master_ids
        except (TypeError, ValueError):
            is_master = False
        (masters if is_master else others).append((qq, u))

    # ---- 主人
    head = ["# 主人 · 记忆", "", f"> 生成于 {time.strftime('%Y-%m-%d %H:%M')}", ""]
    if not masters:
        head.append("（还没有主人账号的对话记录）")
    for qq, u in masters:
        name = u.get("name") or "主人"
        head.append(f"## {name}（QQ {qq}）")
        head += _headline(qq, u)
        head += _summary_lines(u.get("summaries") or [])
        head += ["", "### 未总结的对话", ""]
        head += _history_lines(u.get("history") or [], limit=80)
        head.append("")
    (MEM_DIR / "主人.md").write_text("\n".join(head), encoding="utf-8")

    # ---- 群友
    body = ["# 群友 · 记忆", "", f"> 生成于 {time.strftime('%Y-%m-%d %H:%M')}", ""]
    if not others:
        body.append("（还没有群友的对话记录）")
    others.sort(key=lambda x: -(int(x[1].get("msg_count", 0) or 0)))
    for qq, u in others:
        name = u.get("name") or f"QQ{qq}"
        body.append(f"## {name}（QQ {qq}）")
        body += _headline(qq, u)
        if u.get("notes"):
            body.append("- 记得的事：" + "；".join(u["notes"][-5:]))
        body += _summary_lines(u.get("summaries") or [])
        body += ["", "### 未总结的对话", ""]
        body += _history_lines(u.get("history") or [], limit=30)
        body.append("")
    (MEM_DIR / "群友.md").write_text("\n".join(body), encoding="utf-8")
