"""世界书：关键词触发式设定注入。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CARD_PATH = Path(__file__).resolve().parents[2] / "贝尔娜提塔·冯·瓦立.json"

_book: dict[str, Any] | None = None


def _entries() -> list[dict[str, Any]]:
    global _book
    if _book is None:
        card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
        _book = card["data"]["character_book"]
    rows = [e for e in _book["entries"] if e.get("enabled", True)]
    rows.sort(key=lambda e: -int(e.get("insertion_order", 0)))
    return rows


def count() -> int:
    return len(_entries())


def const_lore() -> str:
    """常驻条目（constant=true），每次都注入。"""
    return "\n\n".join(
        f"【{e.get('name') or e.get('comment')}】{e['content']}" for e in _entries() if e.get("constant")
    )


def _hit(low: str, keys: list[str]) -> bool:
    for k in keys:
        if not k:
            continue
        k = k.lower()
        # 单字 key 太容易误命中（「亲」会命中「父亲」），忽略
        if len(k) < 2:
            continue
        if k in low:
            return True
    return False


def match_lore(text: str, limit: int = 4) -> str:
    """按关键词命中，取 priority 最高的前 limit 条非常驻条目。"""
    if not text:
        return ""
    low = text.lower()
    hits: list[tuple[int, int, str]] = []
    for e in _entries():
        if e.get("constant"):
            continue
        if _hit(low, e.get("keys", [])):
            name = e.get("name") or e.get("comment") or ""
            hits.append((int(e.get("priority", 0)), -int(e.get("insertion_order", 0)), f"【{name}】{e['content']}"))
    hits.sort(reverse=True)
    return "\n\n".join(c for _, _, c in hits[:limit])


def compose_lore(text: str, limit: int = 4, budget: int = 3200) -> str:
    parts = [p for p in (const_lore(), match_lore(text, limit)) if p]
    out = "\n\n".join(parts)
    return out[:budget]


def matched_names(text: str) -> list[str]:
    """调试用：这一句会命中哪些条目。"""
    low = (text or "").lower()
    names = []
    for e in _entries():
        if e.get("constant") or _hit(low, e.get("keys", [])):
            names.append(e.get("name") or e.get("comment") or "?")
    return names
