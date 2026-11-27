"""落雪（LXNS）曲库：公开接口，不需要 key。

    GET https://maimai.lxns.net/api/v0/maimai/song/list

用途：
    1. 补全水鱼没给回来的曲名 / 定数 / 版本
    2. /song 关键词 检索
    3. 知道一首歌有哪些难度、定数多少

缓存到 data/song_list.json，默认 7 天过期。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

SONG_LIST_URL = "https://maimai.lxns.net/api/v0/maimai/song/list"
DATA_DIR = Path(__file__).with_name("data")
CACHE = DATA_DIR / "song_list.json"
ALIAS_CACHE = DATA_DIR / "alias_list.json"
ALIAS_URL = "https://maimai.lxns.net/api/v0/maimai/alias/list"

VERSION_NAME = {
    10000: "maimai",
    11000: "maimai PLUS",
    12000: "GreeN",
    13000: "GreeN PLUS",
    14000: "ORANGE",
    15000: "ORANGE PLUS",
    16000: "PiNK",
    17000: "PiNK PLUS",
    18000: "MURASAKi",
    19000: "MURASAKi PLUS",
    20000: "MiLK",
    21000: "MiLK PLUS",
    22000: "FiNALE",
    23000: "DX",
    23500: "DX PLUS",
    24000: "Splash",
    24500: "Splash PLUS",
    25000: "UNiVERSE",
    25500: "UNiVERSE PLUS",
    26000: "FESTiVAL",
    26500: "FESTiVAL PLUS",
    27000: "BUDDiES",
    27500: "BUDDiES PLUS",
    28000: "PRiSM",
    28500: "PRiSM PLUS",
    29000: "CiRCLE",
    29500: "CiRCLE PLUS",
}

DIFF_CN = {0: "BASIC", 1: "ADVANCED", 2: "EXPERT", 3: "MASTER", 4: "Re:MASTER"}


def version_of(raw: Any) -> str:
    """落雪的 version 是「大版本 × 1000 + 更新批次」的细码：18022 → MURASAKi。

    10000-10999 都属于同一个大版本，只是更新批次不同。
    """
    try:
        v = int(raw or 0)
    except (TypeError, ValueError):
        return ""
    if v < 10000:
        return ""
    base = (v // 1000) * 1000
    return VERSION_NAME.get(base, str(v))


class AliasDB:
    """曲目别名（茄子、qz、喜羊羊…），来自落雪 /alias/list。"""

    def __init__(self, path: Path = ALIAS_CACHE) -> None:
        self.path = path
        self.map: dict[int, list[str]] = {}

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return False
        items = raw.get("aliases") if isinstance(raw, dict) else raw
        if not isinstance(items, list) or not items:
            return False
        self.map = {}
        for it in items:
            try:
                sid = int(it.get("song_id") or 0)
            except (TypeError, ValueError):
                continue
            al = [str(a).strip() for a in (it.get("aliases") or []) if str(a).strip()]
            if sid and al:
                self.map[sid] = al
        return bool(self.map)

    async def refresh(self, force: bool = False, max_age_days: float = 14.0) -> bool:
        fresh = self.path.exists() and (time.time() - self.path.stat().st_mtime) < max_age_days * 86400
        if not force and fresh and self.load():
            return True
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as c:
                r = await c.get(ALIAS_URL)
                if r.status_code == 200:
                    DATA_DIR.mkdir(exist_ok=True)
                    self.path.write_text(r.text, encoding="utf-8")
        except Exception:
            pass
        return self.load()

    def get(self, song_id: int | str) -> list[str]:
        try:
            return self.map.get(int(song_id), [])
        except (TypeError, ValueError):
            return []

    def count(self) -> int:
        return sum(len(v) for v in self.map.values())


class SongDB:
    def __init__(self, path: Path = CACHE) -> None:
        self.path = path
        self.by_id: dict[int, dict[str, Any]] = {}
        self.all: list[dict[str, Any]] = []
        self.loaded_at: float = 0.0

    # ---------- 缓存
    def fresh(self, max_age_days: float = 7.0) -> bool:
        if not self.path.exists():
            return False
        return (time.time() - self.path.stat().st_mtime) < max_age_days * 86400

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return False
        songs = raw.get("songs") if isinstance(raw, dict) else raw
        if not isinstance(songs, list) or not songs:
            return False
        self.all = songs
        self.by_id = {int(s["id"]): s for s in songs if "id" in s}
        self.loaded_at = time.time()
        return True

    async def refresh(self, force: bool = False, max_age_days: float = 7.0) -> bool:
        """有缓存就用缓存；过期或强制才联网。"""
        if not force and self.fresh(max_age_days) and self.load():
            return True
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as c:
                r = await c.get(SONG_LIST_URL)
                if r.status_code != 200:
                    return self.load()
                payload = r.json()
                songs = payload.get("songs") if isinstance(payload, dict) else payload
                if not isinstance(songs, list) or not songs:
                    return self.load()
                DATA_DIR.mkdir(exist_ok=True)
                self.path.write_text(
                    json.dumps({"updated": int(time.time()), "songs": songs},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception:
            return self.load()
        return self.load()

    # ---------- 查询
    def get(self, song_id: int | str) -> dict[str, Any] | None:
        try:
            return self.by_id.get(int(song_id))
        except (TypeError, ValueError):
            return None

    def title(self, song_id: int | str) -> str:
        s = self.get(song_id)
        return str(s.get("title")) if s else ""

    def version_name(self, song_id: int | str) -> str:
        s = self.get(song_id)
        if not s:
            return ""
        return version_of(s.get("version"))

    def difficulties(self, song_id: int | str) -> list[dict[str, Any]]:
        """把 standard / deluxe 拉平成一列，带统一字段。"""
        s = self.get(song_id)
        if not s:
            return []
        out: list[dict[str, Any]] = []
        diffs = s.get("difficulties") or {}
        for key, rows in diffs.items():
            if not isinstance(rows, list):
                continue
            for d in rows:
                if not isinstance(d, dict):
                    continue
                out.append(
                    {
                        "type": "DX" if key.lower().startswith(("dx", "deluxe")) else "SD",
                        "index": int(d.get("difficulty") or 0),
                        "level": str(d.get("level") or ""),
                        "value": d.get("level_value"),
                        "designer": d.get("note_designer") or "",
                    }
                )
        out.sort(key=lambda x: (x["index"], x["type"]))
        return out

    def search(self, keyword: str, limit: int = 8) -> list[dict[str, Any]]:
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        hit, loose = [], []
        for s in self.all:
            t = str(s.get("title") or "")
            a = str(s.get("artist") or "")
            if kw in t.lower():
                hit.append(s)
            elif kw in a.lower():
                loose.append(s)
            if len(hit) >= limit:
                break
        return (hit + loose)[:limit]

    def count(self) -> int:
        return len(self.all)


    # ---------- 更新日历
    def calendar(self, limit: int = 12) -> list[dict[str, Any]]:
        """按 release_date 倒序分组，返回最近 limit 批。"""
        groups: dict[str, list[dict[str, Any]]] = {}
        for s in self.all:
            d = str(s.get("release_date") or "")
            if not d:
                continue
            groups.setdefault(d, []).append(s)
        out = []
        for d in sorted(groups, reverse=True)[:limit]:
            rows = sorted(groups[d], key=lambda x: int(x.get("id") or 0))
            out.append(
                {
                    "date": d,
                    "count": len(rows),
                    "songs": [
                        {"id": int(r["id"]), "title": r.get("title"), "version": r.get("version")}
                        for r in rows
                    ],
                }
            )
        return out

    def save_calendar(self) -> Path:
        import json as _json
        DATA_DIR.mkdir(exist_ok=True)
        p = DATA_DIR / "calendar.json"
        p.write_text(
            _json.dumps(
                {"updated": int(time.time()), "dates": self.calendar(24)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return p

    def recent(self, batches: int = 1) -> list[dict[str, Any]]:
        return self.calendar(max(1, batches))


DB = SongDB()
ALIAS = AliasDB()
