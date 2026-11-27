# -*- coding: utf-8 -*-
"""群成员名单 → 群成员.md（人可读、可手改）

结构：

    ## 群 123456789（3 人）
    - 111 群里甲
    - 222 群里乙

`欧非榜` 在本群名单不够时先读这个文件补；
协议端能拿到 `get_group_member_list` 时也会把结果写回来。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
MD_PATH = BASE_DIR / "群成员.md"

_HEAD = re.compile(r"^##\s*群\s*(\d+)\s*[（(]?\s*(\d+)?\s*人?\s*[）)]?\s*$")
_ROW = re.compile(r"^[-*]\s*(\d{4,})\s*(.*)$")


def render(members: dict[str, dict[str, float]], names: dict[str, str] | None = None) -> str:
    """把 {gid: {qq: ts}} 画成 md。"""
    names = names or {}
    lines = [
        "# 群成员名单",
        "",
        "> 贝尔自动统计的，也可以手改：`- QQ 昵称` 一行，删掉就等于移出这个群。",
        "> 欧非榜只认「这里 + 协议端名单 + 在这个群说过话的人」。",
        "",
    ]
    total = 0
    for gid in sorted(members, key=lambda g: (-len(members[g]), str(g))):
        bucket = members[gid] or {}
        if not bucket:
            continue
        total += len(bucket)
        lines.append(f"## 群 {gid}（{len(bucket)} 人）")
        for qq in sorted(bucket, key=lambda q: (-(bucket[q] or 0), str(q))):
            nm = (names.get(str(qq)) or "").strip()
            lines.append(f"- {qq} {nm}".rstrip())
        lines.append("")
    lines.insert(3, f"> 统计时间：{time.strftime('%Y-%m-%d %H:%M')}　共 {total} 人")
    return "\n".join(lines).rstrip() + "\n"


def load_names() -> dict[str, str]:
    """从现有 md 里把昵称捡回来（state 里没名字时别把 md 写秃）。"""
    out: dict[str, str] = {}
    if not MD_PATH.exists():
        return out
    try:
        text = MD_PATH.read_text(encoding="utf-8")
    except Exception:
        return out
    for raw in text.splitlines():
        m = _ROW.match(raw.strip())
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def write(members: dict[str, dict[str, float]], names: dict[str, str] | None = None) -> Path:
    merged = dict(names or {})
    for qq, nm in load_names().items():          # md 里已有的名字优先保留
        merged.setdefault(qq, nm)
    MD_PATH.write_text(render(members, merged), encoding="utf-8")
    return MD_PATH


def load() -> dict[str, dict[str, float]]:
    """读 md → {gid: {qq: ts}}；解析不了的行直接跳过。"""
    out: dict[str, dict[str, float]] = {}
    if not MD_PATH.exists():
        return out
    gid = ""
    try:
        text = MD_PATH.read_text(encoding="utf-8")
    except Exception:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        m = _HEAD.match(line)
        if m:
            gid = m.group(1)
            out.setdefault(gid, {})
            continue
        if not gid:
            continue
        m = _ROW.match(line)
        if m:
            out[gid][m.group(1)] = 0.0
    return out


def merge_into(members: dict[str, dict[str, float]],
               extra: dict[str, dict[str, float]]) -> int:
    """把 extra 并进 members（只加不减），返回新增人数。"""
    added = 0
    for gid, bucket in (extra or {}).items():
        dst = members.setdefault(str(gid), {})
        for qq, ts in (bucket or {}).items():
            if str(qq) not in dst:
                added += 1
            dst[str(qq)] = dst.get(str(qq)) or float(ts or 0.0)
    return added
