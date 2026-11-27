"""批量下载官方牌子图到 plates/。

牌子清单来自 /api/v0/maimai/plate/list（400 条），存到 data/plate_list.json。
图片来自 https://assets.lxns.net/maimai/plate/{id}.png （720×116 RGBA）

用法：
    python prefetch_plates.py            全部下载
    python prefetch_plates.py --refresh  先重新拉牌子清单
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
PLATE_DIR = BASE / "plates"
DATA = BASE / "data"
PLATE_LIST = DATA / "plate_list.json"
LIST_URL = "https://maimai.lxns.net/api/v0/maimai/plate/list"
IMG_URL = "https://assets.lxns.net/maimai/plate/{}.png"


async def refresh_list(c: httpx.AsyncClient) -> list[dict]:
    DATA.mkdir(exist_ok=True)
    try:
        r = await c.get(LIST_URL)
        if r.status_code == 200:
            data = r.json()
            if data.get("plates"):
                PLATE_LIST.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                print(f"牌子清单已更新：{len(data['plates'])} 条")
                return data["plates"]
    except Exception as exc:
        print(f"拉清单失败：{exc}")
    if PLATE_LIST.exists():
        return json.loads(PLATE_LIST.read_text(encoding="utf-8")).get("plates") or []
    return []


async def fetch_one(c: httpx.AsyncClient, pid: int, sem: asyncio.Semaphore) -> tuple[int, int]:
    p = PLATE_DIR / f"{pid}.png"
    if p.exists() and p.stat().st_size > 2000:
        return pid, p.stat().st_size
    async with sem:
        try:
            r = await c.get(IMG_URL.format(pid))
            if r.status_code == 200 and len(r.content) > 2000:
                p.write_bytes(r.content)
                return pid, len(r.content)
        except Exception:
            pass
    return pid, 0


async def main() -> int:
    PLATE_DIR.mkdir(exist_ok=True)
    force = "--refresh" in sys.argv

    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0),
                                 follow_redirects=True) as c:
        if force or not PLATE_LIST.exists():
            plates = await refresh_list(c)
        else:
            plates = json.loads(PLATE_LIST.read_text(encoding="utf-8")).get("plates") or []

        if not plates:
            print("没有牌子清单，先跑一次 --refresh")
            return 1

        ids = []
        seen = set()
        for it in plates:
            pid = it.get("id")
            if isinstance(pid, int) and pid not in seen:
                seen.add(pid)
                ids.append(pid)

        print(f"要下载 {len(ids)} 个牌子")
        sem = asyncio.Semaphore(10)
        ok = miss = 0
        total = 0
        t0 = time.time()
        tasks = [asyncio.create_task(fetch_one(c, i, sem)) for i in ids]
        done = 0
        for fut in asyncio.as_completed(tasks):
            pid, size = await fut
            done += 1
            if size:
                ok += 1
                total += size
            else:
                miss += 1
            if done % 50 == 0 or done == len(ids):
                print(f"  {done}/{len(ids)}  成功 {ok}  失败 {miss}  {total // 1024 // 1024} MB")

    print(f"\n完成：{ok} 成功 / {miss} 失败，{total // 1024 // 1024} MB，"
          f"{time.time() - t0:.0f} 秒")
    files = list(PLATE_DIR.glob("*.png"))
    print(f"plates/ 现在有 {len(files)} 个文件，"
          f"{sum(f.stat().st_size for f in files) // 1024 // 1024} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
