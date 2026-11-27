"""批量下载全部曲绘到 covers/。

来源顺序（跟 maimai.fetch_covers 一致）：
    1. 水鱼 CDN      covers/{id:05d}.png
    2. 落雪静态 CDN   assets.lxns.net/maimai/jacket/{id}.png
    3. 落雪 API      （需要 lxns_key）

用法：
    python prefetch_covers.py            全部
    python prefetch_covers.py --limit 100  只下前 100 张
    python prefetch_covers.py --missing    只补还缺的
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
COVER_DIR = BASE / "covers"
DATA = BASE / "data"
LXNS_ASSET = "https://assets.lxns.net/maimai/jacket/{}.png"
LXNS_ASSET_WEBP = "https://assets.lxns.net/maimai/jacket/{}.png!webp"
LXNS_API = "https://maimai.lxns.net/api/v0/maimai/cover/{}"
DF = "https://www.diving-fish.com/covers/{:05d}.png"

CONCURRENCY = 12


def cover_ids(mid: int) -> list[int]:
    """和 maimai._cover_ids 保持一致的映射规则。"""
    if not mid:
        return []
    out = [mid]
    if mid > 100000:
        out.append(mid - 100000)
        # 宴谱 / 派生谱：110121 → 121、121852 → 1852
        out.append(mid % 10000)
    if mid > 11000:
        out.append(mid - 10000)
    if 1000 < mid < 10000 or 10000 < mid <= 11000:
        out.append(mid + 10000)
        out.append(mid - 10000)
    seen, uniq = set(), []
    for i in out:
        if i > 0 and i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def all_ids() -> list[int]:
    ids: set[int] = set()
    p = DATA / "song_list.json"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        for s in raw.get("songs") or []:
            try:
                ids.add(int(s["id"]))
            except Exception:
                pass
    # 加粗一点：B50 缓存里出现过的水鱼 ID 也补上
    st = BASE / "state.json"
    if st.exists():
        try:
            meta = json.loads(st.read_text(encoding="utf-8"))
            for u in (meta.get("users") or {}).values():
                for m in u.get("history") or []:
                    _ = m
        except Exception:
            pass
    return sorted(ids)


async def fetch_one(c: httpx.AsyncClient, mid: int, sem: asyncio.Semaphore, lxns_key: str) -> tuple[int, int]:
    """返回 (mid, 字节数)，失败为 0。"""
    p = COVER_DIR / f"{mid:05d}.png"
    if p.exists() and p.stat().st_size > 1000:
        return mid, p.stat().st_size

    for cid in cover_ids(mid):
        cp = COVER_DIR / f"{cid:05d}.png"
        if cp.exists() and cp.stat().st_size > 1000:
            return mid, cp.stat().st_size
        async with sem:
            try:
                r = await c.get(DF.format(cid))
                if r.status_code == 200 and len(r.content) > 1000:
                    cp.write_bytes(r.content)
                    return mid, len(r.content)
            except Exception:
                pass

    for cid in cover_ids(mid):
        cp = COVER_DIR / f"{cid:05d}.png"
        for url in (LXNS_ASSET.format(cid), LXNS_ASSET_WEBP.format(cid)):
            async with sem:
                try:
                    r = await c.get(url)
                    if r.status_code == 200 and len(r.content) > 1000:
                        cp.write_bytes(r.content)
                        return mid, len(r.content)
                except Exception:
                    pass

    if lxns_key:
        for cid in cover_ids(mid):
            cp = COVER_DIR / f"{cid:05d}.png"
            async with sem:
                try:
                    r = await c.get(LXNS_API.format(cid), headers={"Authorization": lxns_key})
                    if r.status_code == 200 and len(r.content) > 1000:
                        cp.write_bytes(r.content)
                        return mid, len(r.content)
                except Exception:
                    pass
    return mid, 0


async def main() -> int:
    args = sys.argv[1:]
    limit = None
    missing_only = "--missing" in args
    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            pass

    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    lxns_key = str(cfg.get("maimai", {}).get("lxns_key") or "")

    COVER_DIR.mkdir(exist_ok=True)
    ids = all_ids()
    if missing_only:
        ids = [i for i in ids if not (COVER_DIR / f"{i:05d}.png").exists()]
    if limit:
        ids = ids[:limit]

    print(f"曲库 {len(ids)} 个 ID 待处理，并发 {CONCURRENCY}")
    if not ids:
        print("没有要下的。")
        return 0

    sem = asyncio.Semaphore(CONCURRENCY)
    ok = fail = 0
    total_bytes = 0
    t0 = time.time()
    done = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0), follow_redirects=True) as c:
        tasks = [asyncio.create_task(fetch_one(c, i, sem, lxns_key)) for i in ids]
        for fut in asyncio.as_completed(tasks):
            mid, size = await fut
            done += 1
            if size:
                ok += 1
                total_bytes += size
            else:
                fail += 1
            if done % 50 == 0 or done == len(ids):
                el = time.time() - t0
                rate = done / el if el else 0
                print(f"  {done}/{len(ids)}  成功 {ok}  失败 {fail}  "
                      f"{total_bytes // 1024 // 1024} MB  {rate:.1f} 张/秒  {el:.0f}s")

    el = time.time() - t0
    print(f"\n完成：{ok} 成功 / {fail} 失败，共 {total_bytes // 1024 // 1024} MB，用时 {el:.0f} 秒")
    files = list(COVER_DIR.glob("*.png"))
    print(f"covers/ 现在有 {len(files)} 个文件，"
          f"{sum(f.stat().st_size for f in files) // 1024 // 1024} MB")
    return 0 if fail == 0 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
