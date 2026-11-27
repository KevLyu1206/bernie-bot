# -*- coding: utf-8 -*-
"""5C 食堂 · /5C吃什么（主人专属）

数据来自 menu.jojodmo.com 前端自己用的那个接口（cacheMaxAge 1800 秒）：

    GET https://5cmenu-cache.jojodmo.com/v1/getMenu/
        ?diningHall=<hoch|mcconnel|collins|malott|frank|frary>&startTime=<unix>&language=en

返回 response.menu[]，每个元素是一天：

    {"date": "2026-11-21T00:00:00" 或 20261121,
     "time": 1795248000,              # 当天零点（太平洋时间）
     "open": true,
     "messages": {"screenMessage": "..."},
     "meals": {"brunch": {"meal": "BRUNCH", "startTime": ..., "endTime": ...,
                          "stations": [{"station": "Grill",
                                        "menu": [{"name": "...", "vegan": false,
                                                  "vegetarian": true, "calories": 548}]}]}}}

关门的日子没有 meals，只有 messages.screenMessage（"Closed today"）。
startTime 往前给会自己跳过已经过完的日子，所以取今天 / 明天都直接传对应零点。

对外：
    HALLS                                         六个食堂（key / 名字 / 学校）
    pacific_now() / to_pacific(ts)                太平洋时间（自己算 DST，不依赖 tzdata）
    collect(offset=0, halls=None, per_meal=6)     扒当天的摘要（offset=1 是明天）
    text_of(data) -> str                          纯文字版（发不出图时兜底）
    render_card(data) -> bytes | None             760 宽长图（白色 Apple 风）
    resolve_halls(words) -> list[str] | None       "/5C吃什么 collins cmc" 这种参数
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

try:
    import fortune as F          # 借它的白色 Apple 风工具（字体 / 面板 / 投影）
except Exception:  # pragma: no cover
    F = None  # type: ignore

try:
    import menuzh                # 菜名翻中文 + 贝尔的推荐语
except Exception:  # pragma: no cover
    menuzh = None  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache" / "menu5c"

API = "https://5cmenu-cache.jojodmo.com/v1/getMenu/"
UA = "bernie-bot/2.0 (5C menu; +https://menu.jojodmo.com)"
DEFAULT_TTL_MIN = 20             # 网站 cacheMaxAge 1800 秒，我们缓存短一点，保证每天都是新菜

# ---------------------------------------------------------------- 食堂

HALLS: list[dict[str, str]] = [
    {"key": "collins",  "name": "Collins",   "college": "Claremont McKenna", "short": "CMC"},
    {"key": "hoch",     "name": "The Hoch",  "college": "Harvey Mudd",       "short": "HMC"},
    {"key": "mcconnel", "name": "McConnel",  "college": "Pitzer",            "short": "PZ"},
    {"key": "malott",   "name": "Malott",    "college": "Scripps",           "short": "SC"},
    {"key": "frank",    "name": "Frank",     "college": "Pomona",            "short": "PO"},
    {"key": "frary",    "name": "Frary",     "college": "Pomona",            "short": "PO"},
]

HALL_KEYS = [h["key"] for h in HALLS]
HALL_BY_KEY = {h["key"]: h for h in HALLS}

# 指令后面可以跟的筛选词：学校简称、食堂名、中文叫法都认
ALIASES: dict[str, list[str]] = {
    "collins": ["collins"], "cmc": ["collins"], "claremontmckenna": ["collins"],
    "克莱蒙特": ["collins"], "麦肯纳": ["collins"],
    "hoch": ["hoch"], "thehoch": ["hoch"], "mudd": ["hoch"], "hmc": ["hoch"],
    "哈维穆德": ["hoch"],
    "mcconnel": ["mcconnel"], "mcconnell": ["mcconnel"], "pitzer": ["mcconnel"], "pz": ["mcconnel"],
    "匹泽": ["mcconnel"],
    "malott": ["malott"], "scripps": ["malott"], "sc": ["malott"], "斯克里普斯": ["malott"],
    "frank": ["frank"], "frary": ["frary"],
    "pomona": ["frank", "frary"], "po": ["frank", "frary"], "波莫纳": ["frank", "frary"],
}

MEAL_ORDER = ["breakfast", "brunch", "lunch", "dinner", "latenight", "late_night", "late night"]
MEAL_CN = {
    "breakfast": "早餐",
    "brunch": "早午餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "latenight": "夜宵",
    "late_night": "夜宵",
    "late night": "夜宵",
}
# 胶囊底色（iOS 系统色，白字压得住）
MEAL_COLOR = {
    "早餐": (255, 149, 0),
    "早午餐": (204, 138, 0),
    "午餐": (52, 199, 89),
    "晚餐": (88, 86, 214),
    "夜宵": (175, 82, 222),
}

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# ---------------------------------------------------------------- 太平洋时间

_PST = dt.timedelta(hours=-8)
_PDT = dt.timedelta(hours=-7)


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (6 - first.weekday()) % 7          # 6 = Sunday
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _is_pdt(utc_naive: dt.datetime) -> bool:
    """美国夏令时：3 月第二个周日 02:00 PST → 11 月第一个周日 02:00 PDT。"""
    y = utc_naive.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(2, 0)) + dt.timedelta(hours=8)
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(2, 0)) + dt.timedelta(hours=7)
    return start <= utc_naive < end


def _offset_at(utc_naive: dt.datetime) -> dt.timedelta:
    return _PDT if _is_pdt(utc_naive) else _PST


def pacific_now() -> dt.datetime:
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    off = _offset_at(utc)
    return (utc + off).replace(tzinfo=dt.timezone(off))


def to_pacific(ts: float | int) -> dt.datetime:
    utc = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).replace(tzinfo=None)
    off = _offset_at(utc)
    return (utc + off).replace(tzinfo=dt.timezone(off))


def seconds_until(hour: int, minute: int = 0) -> float:
    """距离下一个「太平洋时间 hour:minute」还有多少秒（每日推送用）。"""
    now = pacific_now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def _midnight_ts(day: dt.date) -> int:
    """某天太平洋时间零点的 unix 时间戳。"""
    naive = dt.datetime.combine(day, dt.time(0, 0))
    off = _offset_at(naive + dt.timedelta(hours=12))    # 用中午判断当天用的是哪套时区
    return int((naive - off).replace(tzinfo=dt.timezone.utc).timestamp())


def _fmt_clock(ts: float | int | None) -> str:
    if not ts:
        return ""
    t = to_pacific(ts)
    h = t.hour % 12 or 12
    ap = "am" if t.hour < 12 else "pm"
    if t.minute:
        return f"{h}:{t.minute:02d} {ap}"
    return f"{h} {ap}"


def _fmt_range(start: float | int | None, end: float | int | None) -> str:
    a, b = _fmt_clock(start), _fmt_clock(end)
    if not a or not b:
        return ""
    return f"{a} – {b}"


# ---------------------------------------------------------------- 抓取

def _cache_path(day: dt.date, hall: str) -> Path:
    return CACHE_DIR / f"{day.isoformat()}_{hall}.json"


def _read_cache(path: Path) -> tuple[dict[str, Any] | None, float]:
    try:
        if not path.exists():
            return None, 0.0
        return json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime
    except Exception:
        return None, 0.0


def fetch_raw(hall: str, day: dt.date, ttl_min: int = DEFAULT_TTL_MIN,
              force: bool = False) -> dict[str, Any] | None:
    """抓一个食堂某一天的原始 JSON（带本地缓存；网断了就用旧缓存）。"""
    path = _cache_path(day, hall)
    cached, mtime = _read_cache(path)
    fresh = cached is not None and (time.time() - mtime) < ttl_min * 60
    if cached is not None and fresh and not force:
        return cached

    if requests is None:
        return cached

    try:
        r = requests.get(
            API,
            params={"diningHall": hall, "startTime": _midnight_ts(day), "language": "en"},
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=25,
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict) or "response" not in payload:
            raise ValueError("响应格式不对")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    except Exception:
        return cached          # 抓不到就退回旧缓存（哪怕过期），总比没有强


def _day_of(entry: dict[str, Any]) -> dt.date | None:
    raw = entry.get("date")
    if isinstance(raw, int):
        s = str(raw)
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    if isinstance(raw, str):
        try:
            return dt.date.fromisoformat(raw[:10])
        except ValueError:
            return None
    if entry.get("time"):
        return to_pacific(entry["time"]).date()
    return None


# ---------------------------------------------------------------- 选菜

_SKIP_STATION = re.compile(r"not posted|earlier menu|⚠", re.I)
_GARNISH = re.compile(
    r"^(toppings?|sauces?|condiments?|garnish|dressings?|utensils?|napkins?|cutlery|"
    r"silverware|plates?|bowls?|real maple syrup|maple syrup|syrup|butter|jam|jelly|"
    r"ketchup|mustard|mayo|mayonnaise|hot sauce|whipped cream|whipped butter|parmesan cheese and)\b",
    re.I,
)
_JUNK = re.compile(r"available upon request|fresh made to order|^\W*$", re.I)
MAX_NAME = 48

_MAIN_STATIONS = re.compile(
    r"grill|entree|entrée|mainline|main|comfort|action|exhibit|expo|chef|carve|"
    r"saute|sauté|oven|pizza|deli|home|options|creation|global|special|stock|hot cereal|"
    r"breakfast",
    re.I,
)
_LIGHT_STATIONS = re.compile(
    r"dessert|sweet|bakery|pastry|fruit|yogurt|salad|soup|beverage|drink|cereal|misc",
    re.I,
)


def _station_group(name: str) -> int:
    if _MAIN_STATIONS.search(name):
        return 0
    if _LIGHT_STATIONS.search(name):
        return 2
    return 1


def _clean_name(item: dict[str, Any]) -> str:
    nm = str(item.get("name") or "").strip()
    if not nm or _JUNK.search(nm) or _GARNISH.search(nm):
        return ""
    # 「X - 括号里一堆小项」只留主名
    if " - " in nm:
        nm = nm.split(" - ", 1)[0].strip()
    cal = item.get("calories")
    try:
        if cal is not None and float(cal) < 20:      # 酱料、装饰这类基本没热量的
            return ""
    except (TypeError, ValueError):
        pass
    if len(nm) > MAX_NAME:                           # 其实是配方的说明行，不是菜
        return ""
    if nm[:1].islower():                              # McConnel 的菜单全小写
        nm = nm[:1].upper() + nm[1:]
    return nm


def pick_items(meal: dict[str, Any], limit: int = 6, per_station: int = 2) -> list[dict[str, str]]:
    """一顿饭挑几样：先主菜档口，再其它，甜点沙拉垫后；同一档口最多 2 样。

    每样给 {"en": 原名, "zh": 中文}：中文是本地词表拼的，拼不出来就留英文。
    """
    stations = meal.get("stations") or []
    if not stations and meal.get("menu"):             # 兼容扁平的 menu 列表
        stations = [{"station": "Menu", "menu": meal.get("menu")}]

    pool: list[tuple[int, int, int, str]] = []
    for si, st in enumerate(stations):
        sname = str(st.get("station") or "")
        if _SKIP_STATION.search(sname):
            continue
        group = _station_group(sname)
        for ii, it in enumerate(st.get("menu") or []):
            nm = _clean_name(it)
            if nm:
                pool.append((group, si, ii, nm))

    pool.sort(key=lambda x: (x[0], x[1], x[2]))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    per: dict[int, int] = {}
    for _group, si, _ii, nm in pool:
        if len(out) >= limit:
            break
        if per.get(si, 0) >= per_station:
            continue
        key = nm.lower()
        if key in seen:
            continue
        seen.add(key)
        zh = menuzh.tr(nm) if menuzh is not None else nm
        if not zh:                                     # 网站那种「还没贴菜单」的条目
            continue
        out.append({"en": nm, "zh": zh})
        per[si] = per.get(si, 0) + 1
    return out


# ---------------------------------------------------------------- 组装

def parse_day(payload: dict[str, Any] | None, day: dt.date, per_meal: int = 6) -> dict[str, Any]:
    """把一天的原始 JSON 变成 {open, note, meals: [{label, time, items}]}。"""
    out: dict[str, Any] = {"open": False, "note": "", "meals": []}
    if not payload:
        out["note"] = "没抓到"
        return out

    entries = (((payload.get("response") or {}).get("menu")) or [])
    entry = None
    for e in entries:
        if _day_of(e) == day:
            entry = e
            break
    if entry is None and entries:
        entry = entries[0]
    if entry is None:
        out["note"] = "这天还没排菜单"
        return out

    msg = str(((entry.get("messages") or {}).get("screenMessage")) or "").strip()
    meals_raw = entry.get("meals") or {}

    order = {k: i for i, k in enumerate(MEAL_ORDER)}
    for key in sorted(meals_raw, key=lambda k: order.get(str(k).lower(), 99)):
        meal = meals_raw[key] or {}
        label = MEAL_CN.get(str(key).lower())
        if not label:
            label = str(meal.get("meal") or key).title()
        items = pick_items(meal, limit=per_meal)
        if not items:
            continue
        out["meals"].append(
            {
                "label": label,
                "time": _fmt_range(meal.get("startTime"), meal.get("endTime")),
                "items": items,
            }
        )

    out["open"] = bool(entry.get("open", True)) and bool(out["meals"])
    if not out["open"]:
        if msg and "closed" not in msg.lower():
            out["note"] = msg
        else:
            out["note"] = "今天关门"
    elif msg:
        out["note"] = msg
    return out


def resolve_halls(words: list[str]) -> list[str] | None:
    """/5C吃什么 collins cmc → ["collins"]；认不出来返回 None。"""
    if not words:
        return None
    picked: list[str] = []
    for w in words:
        key = re.sub(r"[\s'’_-]+", "", w).lower()
        for alias, halls in ALIASES.items():
            if key == alias:
                for h in halls:
                    if h not in picked:
                        picked.append(h)
    return picked or None


def collect(offset: int = 0, halls: list[str] | None = None, per_meal: int = 6,
            ttl_min: int = DEFAULT_TTL_MIN) -> dict[str, Any]:
    """扒菜单。offset=0 今天，1 明天；halls 不传就是六个食堂全扒。"""
    now = pacific_now()
    day = (now + dt.timedelta(days=offset)).date()
    keys = [k for k in (halls or HALL_KEYS) if k in HALL_BY_KEY]

    out_halls: list[dict[str, Any]] = []
    missing: list[str] = []
    for key in keys:
        h = HALL_BY_KEY[key]
        payload = fetch_raw(key, day, ttl_min=ttl_min)
        info = parse_day(payload, day, per_meal=per_meal)
        if not info["meals"]:
            missing.append(h["name"])
        hall = {**h, **info}
        if menuzh is not None:
            hall["score"] = menuzh.score_of(hall, day.isoformat())
            hall["rec"] = menuzh.pick_recommend(hall, day.isoformat())
        else:
            hall["score"] = 0
            hall["rec"] = ""
        out_halls.append(hall)

    # 关门的那家，如果同校另一家开着，就顺手推过去
    for hall in out_halls:
        if hall.get("meals"):
            continue
        siblings = [o for o in out_halls
                    if o["key"] != hall["key"] and o["college"] == hall["college"] and o.get("meals")]
        if siblings:
            hall["rec"] = f"今天没开，{hall['college']} 的另一家 {siblings[0]['name']} 开着，主人去那边。"

    return {
        "day": day.isoformat(),
        "weekday": WEEKDAY_CN[day.weekday()],
        "when": {0: "今天", 1: "明天", 2: "后天"}.get(offset, f"{offset} 天后"),
        "offset": offset,
        "stamp": now.strftime("%H:%M"),
        "halls": out_halls,
        "missing": missing,
    }


# ---------------------------------------------------------------- 文字版

def text_of(data: dict[str, Any]) -> str:
    def _zh(it: Any) -> str:
        return it["zh"] if isinstance(it, dict) else str(it)

    head = f"5C 食堂 · {data['when']}（{data['day'][5:].replace('-', '/')} {data['weekday']}）"
    lines = [head]
    for h in data["halls"]:
        tag = f"{h['name']}（{h['college']}）"
        if not h.get("meals"):
            lines.append(f"{tag}：{h.get('note') or '今天没菜单'}")
            continue
        lines.append(f"{tag}　贝尔给 {h.get('score', 0)} 分")
        for m in h["meals"]:
            t = f" {m['time']}" if m.get("time") else ""
            lines.append(f"  {m['label']}{t}：{'、'.join(_zh(i) for i in m['items'])}")
        if h.get("rec"):
            lines.append(f"  贝尔：{h['rec']}")
        if h.get("note"):
            lines.append(f"  ※ {h['note']}")
    lines.append(f"\n数据：menu.jojodmo.com · 扒于 {data['stamp']}（太平洋时间）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 图卡（B50 版式 · 白色 Apple 风）

W = 760
M = 28
PAD = 22

# 学校代表色：Collins 酒红 / Hoch 金 / McConnel 橙 / Malott 深绿 / Frank·Frary 同属 Pomona，都是蓝
HALL_COLOR = {
    "collins": (134, 38, 51),
    "hoch": (170, 132, 45),
    "mcconnel": (226, 101, 26),
    "malott": (0, 122, 83),
    "frank": (0, 87, 184),
    "frary": (0, 87, 184),
}
SCORE_GOOD = (52, 199, 89)
SCORE_MID = (255, 149, 0)
SCORE_LOW = (142, 142, 147)


def _tint(c: tuple[int, int, int], k: float = 0.10) -> tuple[int, int, int]:
    """把颜色兑白，当卡片里的浅底色。"""
    return tuple(int(255 - (255 - v) * k) for v in c)  # type: ignore[return-value]


def _score_color(v: int) -> tuple[int, int, int]:
    if v >= 85:
        return SCORE_GOOD
    if v >= 70:
        return SCORE_MID
    return SCORE_LOW


def _wrap_lines(d: Any, text: str, font: Any, max_w: float, max_lines: int = 2) -> list[str]:
    """按「、」切菜名，贪心塞行；超过 max_lines 最后一行加省略号。"""
    parts = [p.strip() for p in text.split("、") if p.strip()]
    lines: list[str] = []
    cur = ""
    for p in parts:
        while d.textlength(p, font=font) > max_w and len(p) > 4:      # 单个菜名太长先硬截
            p = p[:-2]
        cand = f"{cur}、{p}" if cur else p
        if not cur or d.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = p
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and d.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines or [""]


def _dish_text(meal: dict[str, Any]) -> str:
    return "、".join(it["zh"] if isinstance(it, dict) else str(it) for it in meal.get("items") or [])


def _fonts() -> dict[str, Any]:
    return {
        "small": F._font(17, True, "cn"),
        "title": F._font(36, True, "cn"),
        "date": F._font(13, False, "cn"),
        "chip": F._font(12, True, "cn"),
        "hall": F._font(25, True, "cn"),
        "coll": F._font(11, False, "cn"),
        "score": F._font(24, True, "num"),
        "unit": F._font(12, False, "cn"),
        "avg": F._font(58, True, "num"),
        "pill": F._font(14, True, "cn"),
        "time": F._font(11, False, "num"),
        "item": F._font(16, False, "cn"),
        "rec_tag": F._font(11, True, "cn"),
        "rec": F._font(13, False, "cn"),
        "tiny": F._font(11, False, "cn"),
    }


def _hall_height(hall: dict[str, Any], d: Any, fns: dict[str, Any], inner_w: float) -> int:
    h = 14 + 50                                    # 上留白 + 名字行（右边要放分数）
    if hall.get("meals"):
        for m in hall["meals"]:
            rows = len(_wrap_lines(d, _dish_text(m), fns["item"], inner_w, 2))
            h += 30 + rows * 24 + 10
    else:
        h += 8
    rec_rows = len(_wrap_lines(d, hall.get("rec") or "", fns["rec"], inner_w - 28, 2))
    h += 16 + 14 + rec_rows * 19 + 12
    return h + 12


def render_card(data: dict[str, Any]) -> bytes | None:
    """760 宽长图：顶部总览 + 每个食堂一张卡（B50 的版面语言）。"""
    if F is None or getattr(F, "Image", None) is None:
        return None
    Image, ImageDraw = F.Image, F.ImageDraw

    fns = _fonts()
    inner_w = W - M * 2 - PAD * 2

    scratch = ImageDraw.Draw(Image.new("RGB", (W, 10), F.BG))
    heights = [_hall_height(h, scratch, fns, inner_w) for h in data["halls"]]
    H = 22 + 168 + 16 + sum(heights) + 12 * max(0, len(heights) - 1) + 58

    img = Image.new("RGB", (W, H), F.BG)
    d = ImageDraw.Draw(img)

    # ===== 顶部：日期 + 今日平均分 + 六校色条
    img = F._panel(img, (M, 22, W - M, 22 + 168), 28)
    d = ImageDraw.Draw(img)

    d.text((M + 26, 40), "5C 食堂 · 贝尔的食堂情报", font=fns["small"], fill=F.SUB)
    d.text((M + 26, 64), f"{data['when']}吃什么", font=fns["title"], fill=F.TEXT)
    d.text(
        (M + 26, 112),
        f"{data['day'][5:].replace('-', '/')}　{data['weekday']}　·　现扒 menu.jojodmo.com",
        font=fns["date"], fill=F.SOFT,
    )

    open_halls = [h for h in data["halls"] if h.get("meals")]
    avg = int(round(sum(int(h.get("score") or 0) for h in open_halls) / len(open_halls))) if open_halls else 0
    d.text((W - M - 26, 34), "今日平均分", font=fns["date"], fill=F.SOFT, anchor="ra")
    d.text((W - M - 26, 52), str(avg), font=fns["avg"], fill=_score_color(avg), anchor="ra")
    d.text((W - M - 26, 116), f"/ 100　{len(open_halls)} 家开门", font=fns["date"], fill=F.SOFT, anchor="ra")

    # 六校色条（frank / frary 都是 Pomona 蓝）
    cx = M + 26
    for hall in data["halls"]:
        color = HALL_COLOR.get(hall["key"], F.SUB)
        short = hall.get("short") or hall["key"][:3].upper()
        label = f"{hall['name']}·{short}"
        w = int(d.textlength(label, font=fns["chip"])) + 22
        d.rounded_rectangle((cx, 140, cx + w, 166), radius=13, fill=color)
        d.text((cx + 11, 153), label, font=fns["chip"], fill=(255, 255, 255), anchor="lm")
        cx += w + 8

    # ===== 每个食堂一张卡
    y = 22 + 168 + 16
    for hall, ch in zip(data["halls"], heights):
        color = HALL_COLOR.get(hall["key"], F.SUB)
        img = F._panel(img, (M, y, W - M, y + ch), 22)
        d = ImageDraw.Draw(img)

        # 名字行：学校色胶囊 + 食堂名 + 分数
        short = hall.get("short") or hall["key"][:3].upper()
        pw = int(d.textlength(short, font=fns["chip"])) + 22
        d.rounded_rectangle((M + PAD, y + 16, M + PAD + pw, y + 40), radius=12, fill=color)
        d.text((M + PAD + 11, y + 28), short, font=fns["chip"], fill=(255, 255, 255), anchor="lm")
        d.text((M + PAD + pw + 10, y + 12), hall["name"], font=fns["hall"], fill=F.TEXT)
        d.text((W - M - PAD, y + 10), f"{hall['college']}", font=fns["coll"], fill=F.SOFT, anchor="ra")

        score = int(hall.get("score") or 0)
        if hall.get("meals"):
            # 「77 分」横着排：数字靠右，单位贴在它右边
            d.text((W - M - PAD - 18, y + 26), str(score), font=fns["score"],
                   fill=_score_color(score), anchor="ra")
            d.text((W - M - PAD, y + 42), "分", font=fns["tiny"], fill=F.SOFT, anchor="ra")
        else:
            d.text((M + PAD, y + 46), "今天关门", font=fns["coll"], fill=F.SOFT)

        cursor = y + 64
        if hall.get("meals"):
            for i, m in enumerate(hall["meals"]):
                meal_color = MEAL_COLOR.get(m["label"], F.SUB)
                mw = int(d.textlength(m["label"], font=fns["pill"])) + 20
                d.rounded_rectangle((M + PAD, cursor, M + PAD + mw, cursor + 22), radius=11, fill=meal_color)
                d.text((M + PAD + 10, cursor + 11), m["label"], font=fns["pill"], fill=(255, 255, 255), anchor="lm")
                if m.get("time"):
                    d.text((M + PAD + mw + 9, cursor + 11), m["time"], font=fns["time"], fill=F.SOFT, anchor="lm")

                ty = cursor + 28
                for row in _wrap_lines(d, _dish_text(m), fns["item"], inner_w, 2):
                    d.text((M + PAD, ty), row, font=fns["item"], fill=(48, 48, 53))
                    ty += 24
                cursor = ty + 10
                if i < len(hall["meals"]) - 1:
                    d.line([(M + PAD, cursor - 5), (W - M - PAD, cursor - 5)], fill=F.FILL, width=1)

        # 贝尔的推荐（浅色块）
        rec = hall.get("rec") or ""
        rec_lines = _wrap_lines(d, rec, fns["rec"], inner_w - 28, 2)
        box_h = 14 + 14 + len(rec_lines) * 19 + 10
        d.rounded_rectangle((M + PAD, cursor, W - M - PAD, cursor + box_h), radius=14, fill=_tint(color, 0.10))
        d.text((M + PAD + 14, cursor + 10), "贝尔推荐", font=fns["rec_tag"], fill=color)
        ry = cursor + 26
        for row in rec_lines:
            d.text((M + PAD + 14, ry), row, font=fns["rec"], fill=(60, 60, 66))
            ry += 19

        y += ch + 12

    # ===== 页脚
    d.line([(M, H - 46), (W - M, H - 46)], fill=F.LINE, width=1)
    d.text((M, H - 34), f"贝尔娜提塔给你扒的 · {data['stamp']} 太平洋时间", font=fns["tiny"], fill=F.SOFT)
    d.text((W - M, H - 34), "菜品中文是贝尔翻的，看不懂来问", font=fns["tiny"], fill=F.SOFT, anchor="ra")

    import io as _io

    buf = _io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    words = [w for w in sys.argv[1:] if w.strip()]
    offset = 1 if any(w in ("明天", "tomorrow", "tmr") for w in words) else 0
    picked = resolve_halls([w for w in words if w not in ("明天", "tomorrow", "tmr")])
    data = collect(offset=offset, halls=picked)
    print(text_of(data))
    png = render_card(data)
    if png:
        out = BASE_DIR / "menu5c_preview.jpg"
        out.write_bytes(png)
        print(f"\n图卡：{out}（{len(png) // 1024} KB）")
