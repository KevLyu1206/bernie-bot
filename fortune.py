# -*- coding: utf-8 -*-
"""今日运势 —— /今日运势（群里要先 @ 再发）

规则：
    · 分数 0-100，完全随机
        0-24 凶 / 25-50 平 / 51-74 吉 / 75-100 大吉
    · 每个 QQ 每天一份，种子 = 北京日期 + QQ
        同一天再问还是同一份，零点（北京时间）自动换
    · 附赠：一家洋快餐店的单品 + 今天该打的 maimai 曲子
        曲子带曲绘、曲师、MASTER / Re:MASTER 的定数与谱师（有哪张写哪张）

对外：
    kind_of(score) -> str             分数 → 凶/平/吉/大吉
    beijing_day() -> str              "2026-11-22"
    get_db()                          拿曲库（没加载就先读本地缓存）
    draw(qq, date=None, db=None)      抽一份签（dict）
    text_of(res) -> str               纯文字版
    render_card(res) -> bytes         760×920 图卡（白色 Apple 风，仿 B50 版面）
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import random
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # 没装 Pillow 就只发文字
    Image = ImageDraw = ImageFont = None  # type: ignore

import maimai as maimai_mod

BASE_DIR = Path(__file__).resolve().parent
COVER_DIR = BASE_DIR / "covers"
FONT_DIR = Path("C:/Windows/Fonts")
BJ_TZ = _dt.timezone(_dt.timedelta(hours=8), "Asia/Shanghai")   # 中国没有夏令时，固定 +8 就够

# ---------------------------------------------------------------- 等级

KINDS = {
    "凶": (0, 24),
    "平": (25, 50),
    "吉": (51, 74),
    "大吉": (75, 100),
}

KIND_STARS = {"凶": "★", "平": "★★", "吉": "★★★", "大吉": "★★★★"}


def kind_of(score: int) -> str:
    s = max(0, min(100, int(score)))
    for name, (lo, hi) in KINDS.items():
        if lo <= s <= hi:
            return name
    return "平"


def beijing_day(now: _dt.datetime | None = None) -> str:
    """北京时间的日期字符串。每天零点自动换。"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return now.astimezone(BJ_TZ).strftime("%Y-%m-%d")


def beijing_stamp(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return now.astimezone(BJ_TZ).strftime("%Y-%m-%d %H:%M")


def beijing_weekday(day: str) -> str:
    try:
        dt = _dt.date.fromisoformat(day)
    except ValueError:
        return ""
    return "周" + "一二三四五六日"[dt.weekday()]


# ---------------------------------------------------------------- 洋快餐

# 只收「洋快餐 / 西式连锁」：汉堡、炸鸡、披萨、三明治、墨西哥卷
FASTFOOD: dict[str, dict[str, Any]] = {
    "麦当劳": {
        "tag": "McDonald's",
        "items": [
            ("板烧鸡腿堡", "18-22 元"),
            ("巨无霸", "22-25 元"),
            ("麦辣鸡腿堡", "18-21 元"),
            ("双层吉士汉堡", "14-16 元"),
            ("麦乐鸡 10 块", "16-18 元"),
            ("薯条（大）", "11-13 元"),
            ("麦旋风", "14-16 元"),
            ("那么大鸡排", "12-15 元"),
        ],
    },
    "肯德基": {
        "tag": "KFC",
        "items": [
            ("吮指原味鸡（2 块）", "18-22 元"),
            ("香辣鸡腿堡", "18-20 元"),
            ("劲脆鸡腿堡", "18-20 元"),
            ("新奥尔良烤翅（2 只）", "15-18 元"),
            ("葡式蛋挞", "7-8 元/个"),
            ("上校鸡块（5 块）", "12-14 元"),
            ("薯条（大）", "11-13 元"),
            ("老北京鸡肉卷", "14-16 元"),
        ],
    },
    "汉堡王": {
        "tag": "Burger King",
        "items": [
            ("皇堡", "22-25 元"),
            ("双层芝士皇堡", "26-29 元"),
            ("脆鸡堡", "16-19 元"),
            ("薯条（中）", "10-12 元"),
            ("洋葱圈", "10-12 元"),
            ("鸡块（6 块）", "13-15 元"),
        ],
    },
    "必胜客": {
        "tag": "Pizza Hut",
        "items": [
            ("超级至尊披萨（个人装）", "30-38 元"),
            ("夏威夷风情披萨（9 寸）", "45-55 元"),
            ("烤鸡翅（4 只）", "20-24 元"),
            ("芝士焗薯角", "18-22 元"),
            ("意式肉酱面", "25-30 元"),
        ],
    },
    "棒约翰": {
        "tag": "Papa John's",
        "items": [
            ("超级棒约翰披萨（9 寸）", "55-65 元"),
            ("蒜香面包条", "15-18 元"),
            ("烤鸡翅（4 只）", "22-26 元"),
        ],
    },
    "达美乐": {
        "tag": "Domino's",
        "items": [
            ("超级至尊披萨（9 寸）", "50-60 元"),
            ("夏威夷披萨（9 寸）", "45-55 元"),
            ("蒜香面包棒", "12-15 元"),
            ("烤鸡翅（4 只）", "22-26 元"),
        ],
    },
    "赛百味": {
        "tag": "Subway",
        "items": [
            ("金枪鱼三明治（6 寸）", "22-26 元"),
            ("意大利香肠三明治（6 寸）", "20-24 元"),
            ("烤鸡胸三明治（12 寸）", "36-42 元"),
            ("曲奇（2 块）", "8-10 元"),
        ],
    },
    "塔可钟": {
        "tag": "Taco Bell",
        "items": [
            ("脆皮塔可", "12-15 元"),
            ("墨西哥卷饼", "18-22 元"),
            ("芝士薯条", "12-15 元"),
        ],
    },
    "华莱士": {
        "tag": "Wallace",
        "items": [
            ("香辣鸡腿堡", "10-13 元"),
            ("全鸡（半只）", "18-22 元"),
            ("鸡米花", "9-12 元"),
            ("薯条（大）", "9-11 元"),
        ],
    },
    "塔斯汀": {
        "tag": "Tastien · 中国汉堡",
        "items": [
            ("手擀现烤中国汉堡（香辣鸡）", "12-15 元"),
            ("手擀现烤中国汉堡（牛肉）", "15-18 元"),
            ("鸡米花", "10-12 元"),
            ("薯条", "9-11 元"),
        ],
    },
    "德克士": {
        "tag": "Dicos",
        "items": [
            ("脆皮手枪腿", "18-22 元"),
            ("照烧鸡腿堡", "16-19 元"),
            ("炸鸡（2 块）", "16-20 元"),
            ("薯条（大）", "10-12 元"),
        ],
    },
    "Shake Shack": {
        "tag": "Shake Shack",
        "items": [
            ("ShackBurger", "58-65 元"),
            ("Shroom Burger", "62-68 元"),
            ("Crinkle Cut 薯条", "26-32 元"),
            ("Shake 奶昔", "38-45 元"),
        ],
    },
    "Popeyes": {
        "tag": "Popeyes",
        "items": [
            ("炸鸡（2 块）", "26-30 元"),
            ("香辣鸡腿堡", "30-35 元"),
            ("Cajun 薯条", "18-22 元"),
        ],
    },
    "Five Guys": {
        "tag": "Five Guys",
        "items": [
            ("Cheeseburger", "68-78 元"),
            ("Little Burger", "48-55 元"),
            ("Cajun 薯条（小）", "36-42 元"),
        ],
    },
}

FASTFOOD_NOTE = {
    "凶": "今天别点新品，点最熟的那个。",
    "平": "常规口味就行，别加限定酱。",
    "吉": "可以加一份小食。",
    "大吉": "加个套餐，今天值得。",
}

FASTFOOD_HOUR = {
    "凶": "建议 17:00 前吃完，别拖到夜里。",
    "平": "午餐或晚餐都行，别当夜宵。",
    "吉": "饭点人少的时候去，出餐会更好。",
    "大吉": "想吃就现在去，别等。",
}


def pick_fastfood(rng: random.Random) -> dict[str, str]:
    brand = rng.choice(list(FASTFOOD.keys()))
    name, price = rng.choice(FASTFOOD[brand]["items"])
    return {
        "brand": brand,
        "tag": FASTFOOD[brand]["tag"],
        "item": name,
        "price": price,
    }


# ---------------------------------------------------------------- 冰淇淋

# 只收正常口味：乳基（gelato / ice cream / soft serve）+ 雪芭（sorbet）。
# (中文名, 英文名, 类型, 一句备注)
ICECREAM: list[tuple[str, str, str, str]] = [
    # —— 乳基：经典款
    ("香草", "Vanilla", "gelato", "奶味最老实的一球，配什么都行。"),
    ("大溪地香草", "Tahitian Vanilla", "gelato", "香草籽看得见，贵一点也认。"),
    ("巧克力", "Chocolate", "gelato", "可可味厚，不腻的那种。"),
    ("黑巧克力", "Dark Chocolate", "gelato", "70% 那种，苦完回甘。"),
    ("牛奶巧克力", "Milk Chocolate", "ice cream", "小时候的味道，别嫌它普通。"),
    ("草莓", "Strawberry", "gelato", "要真果肉，不要挤出来的果酱。"),
    ("薄荷巧克力", "Mint Chip", "ice cream", "薄荷凉得干净，巧克力片要脆。"),
    ("饼干面团", "Cookie Dough", "ice cream", "面团生着吃才有意思。"),
    ("曲奇奶油", "Cookies & Cream", "ice cream", "饼干碎要压得粗。"),
    ("咖啡", "Espresso", "gelato", "浓缩兑进去的，提神。"),
    ("摩卡", "Mocha", "gelato", "咖啡和可可各占一半。"),
    ("提拉米苏", "Tiramisu", "gelato", "有酒味才对，马斯卡彭要足。"),
    ("榛子", "Hazelnut", "gelato", "坚果烤过的香，甜度别太高。"),
    ("开心果", "Pistachio", "gelato", "绿得发灰才对，亮绿是假的。"),
    ("杏仁", "Amaretto", "gelato", "带点苦杏仁的药香，一口就够。"),
    ("海盐焦糖", "Salted Caramel", "gelato", "咸压住甜，越吃越想。"),
    ("焦糖布丁", "Crème Brûlée", "ice cream", "上面那层焦糖碎是重点。"),
    ("枫糖核桃", "Maple Walnut", "ice cream", "核桃要烤过，糖浆别省。"),
    ("兰姆葡萄", "Rum Raisin", "ice cream", "葡萄干泡过酒，会上头。"),
    ("芝士蛋糕", "Cheesecake", "gelato", "酸一点更好吃。"),
    ("布朗尼", "Brownie", "ice cream", "巧克力块要软心。"),
    ("花生酱", "Peanut Butter", "ice cream", "粗粒的那种，咸脆。"),
    ("香蕉", "Banana", "gelato", "熟透的香蕉，不是香精。"),
    ("椰子", "Coconut", "gelato", "椰浆做的，越白越假。"),
    ("抹茶", "Matcha", "gelato", "苦味要先出来。"),
    ("焙茶", "Hōjicha", "gelato", "炒过的茶香，比抹茶温和。"),
    ("黑芝麻", "Black Sesame", "gelato", "磨得细，颗粒感留一点。"),
    ("红豆", "Red Bean", "ice cream", "要带皮煮的，不要豆沙。"),
    ("芋头", "Taro", "gelato", "紫得淡一点才对。"),
    ("伯爵红茶", "Earl Grey", "gelato", "佛手柑的香，别盖过奶。"),
    ("蜂蜜", "Honey", "ice cream", "花香味比糖味重要。"),
    ("白巧克力", "White Chocolate", "ice cream", "甜，配莓果刚好。"),
    ("肉桂卷", "Cinnamon Roll", "ice cream", "冬天的味道，夏天吃也行。"),
    ("苹果派", "Apple Pie", "ice cream", "有肉桂、有酥皮碎。"),
    ("南瓜派", "Pumpkin", "ice cream", "感恩节那一口，香料别重。"),
    ("酸奶", "Yogurt", "gelato", "酸得清爽，解腻用。"),
    ("玫瑰", "Rose", "gelato", "花香要淡，浓了像香皂。"),
    ("栗子", "Chestnut", "gelato", "秋天的味道，粉糯。"),
    ("橙花", "Orange Blossom", "gelato", "橙皮屑一撒，整个活了。"),
    # —— 雪芭：果汁基，不含奶
    ("柠檬雪芭", "Lemon Sorbet", "sorbet", "酸到眯眼，最解暑。"),
    ("青柠雪芭", "Lime Sorbet", "sorbet", "比柠檬更冲，带皮香。"),
    ("血橙雪芭", "Blood Orange Sorbet", "sorbet", "颜色好看，酸里带甜。"),
    ("覆盆子雪芭", "Raspberry Sorbet", "sorbet", "籽要留着，酸得干净。"),
    ("草莓雪芭", "Strawberry Sorbet", "sorbet", "比冰淇淋版更清爽。"),
    ("芒果雪芭", "Mango Sorbet", "sorbet", "要熟到流汁的芒果。"),
    ("百香果雪芭", "Passion Fruit Sorbet", "sorbet", "籽嚼着吃，酸得凶。"),
    ("菠萝雪芭", "Pineapple Sorbet", "sorbet", "带点纤维感最好。"),
    ("西瓜雪芭", "Watermelon Sorbet", "sorbet", "夏天限定，水多。"),
    ("青苹果雪芭", "Green Apple Sorbet", "sorbet", "清脆的酸，带皮。"),
    ("梨雪芭", "Pear Sorbet", "sorbet", "西洋梨，沙沙的口感。"),
    ("桃子雪芭", "Peach Sorbet", "sorbet", "白桃最好，绒毛味别留。"),
    ("柚子雪芭", "Yuzu Sorbet", "sorbet", "日本柚子，皮香冲。"),
    ("荔枝雪芭", "Lychee Sorbet", "sorbet", "甜得直接，冰一下更好。"),
    ("石榴雪芭", "Pomegranate Sorbet", "sorbet", "带点涩才像真的。"),
    ("蓝莓雪芭", "Blueberry Sorbet", "sorbet", "深紫色，酸压甜。"),
    ("葡萄雪芭", "Grape Sorbet", "sorbet", "麝香味那种，别用巨峰。"),
    ("奇异果雪芭", "Kiwi Sorbet", "sorbet", "籽要看得见。"),
    ("桑葚雪芭", "Mulberry Sorbet", "sorbet", "染嘴的那种。"),
    # —— 软冰淇淋
    ("香草甜筒", "Vanilla Soft Serve", "soft serve", "机器挤出来的那圈尖才是本体。"),
    ("巧克力脆皮甜筒", "Chocolate Dip", "soft serve", "壳一咬就裂，脆响。"),
    ("抹茶甜筒", "Matcha Soft Serve", "soft serve", "苦甜配脆筒。"),
    ("草莓圣代", "Strawberry Sundae", "soft serve", "酱要淋在顶上，别拌。"),
]


ICECREAM_NOTE = {
    "凶": "就吃一球，别贪。",
    "平": "一球够了，配杯水。",
    "吉": "可以要双球，今天配得上。",
    "大吉": "双球再加个脆筒，别克制。",
}


def pick_icecream(rng: random.Random) -> dict[str, str]:
    name, en, style, note = rng.choice(ICECREAM)
    return {"name": name, "en": en, "style": style, "note": note}


# ---------------------------------------------------------------- phigros

PHIGROS_JSON = BASE_DIR / "phigros" / "songs.json"
PHIGROS_COVER_DIR = BASE_DIR / "phigros" / "covers"
_PHIGROS: list[dict[str, Any]] | None = None


def phigros_songs() -> list[dict[str, Any]]:
    """fetch_phigros.py 生成的曲目表；封面文件不在的条目直接剔掉。"""
    global _PHIGROS
    if _PHIGROS is None:
        try:
            rows = json.loads(PHIGROS_JSON.read_text(encoding="utf-8"))
        except Exception:
            rows = []
        _PHIGROS = [
            r for r in rows
            if isinstance(r, dict) and r.get("cover")
            and (PHIGROS_COVER_DIR / str(r["cover"])).exists()
        ]
    return _PHIGROS


def pick_phigros(rng: random.Random) -> dict[str, Any] | None:
    songs = phigros_songs()
    if not songs:
        return None
    return dict(rng.choice(songs))


# ---------------------------------------------------------------- maimai 曲子

GENRE_SHORT = {
    "maimai": "maimai 原创",
    "POPSアニメ": "POPS & ANIME",
    "ゲームバラエティ": "GAME & VARIETY",
    "niconicoボーカロイド": "niconico & VOCALOID",
    "東方Project": "東方Project",
    "オンゲキCHUNITHM": "ONGEKI & CHUNITHM",
    "宴会場": "宴会場",
}

DIFF_CN = {0: "BASIC", 1: "ADVANCED", 2: "EXPERT", 3: "MASTER", 4: "Re:MASTER"}


def get_db() -> Any:
    """拿曲库：没加载就先读本地缓存（不发网络请求，够快）。"""
    try:
        import songdb
    except Exception:
        return None
    if not songdb.DB.all:
        try:
            songdb.DB.load()
        except Exception:
            return None
    return songdb.DB if songdb.DB.all else None


def _cover_path(song_id: int) -> Path | None:
    """曲绘文件：水鱼的 ID 有时要走 DX/SD 互转（1788 → 11788），按候选顺序找。"""
    try:
        ids = maimai_mod._cover_ids(int(song_id))
    except Exception:
        ids = [int(song_id)]
    for cid in ids:
        p = COVER_DIR / f"{cid:05d}.png"
        if p.exists() and p.stat().st_size > 1000:
            return p
    return None


def _cached_cover(song_id: int) -> bool:
    return _cover_path(song_id) is not None


def _charts_of(db: Any, sid: int) -> dict[str, dict[int, dict[str, Any]]]:
    """按谱面类型分开：`{"DX": {3: …, 4: …}, "SD": {3: …}}`。

    **不能按 index 去重。** 同一 index 的 SD 和 DX 是两张不同的谱，定数经常不一样
    （ジングルベル：SD MASTER 14.4 / DX MASTER 14.6），而且常常只有一边有 Re:MASTER。
    以前「同 index 优先留 DX」会把 DX 的 MASTER 和 SD 的 Re:MASTER 拼成一条假的难度序列，
    看起来就像「DX 也有 Re:MASTER」—— 2026-11-23 用户指出来的就是这个。
    """
    out: dict[str, dict[int, dict[str, Any]]] = {}
    for d in db.difficulties(sid):
        if not d.get("level"):
            continue
        t = "DX" if str(d.get("type") or "").upper() == "DX" else "SD"
        out.setdefault(t, {})[int(d["index"])] = d
    return out


def pick_song(rng: random.Random, db: Any, kind: str = "平") -> dict[str, Any] | None:
    """从有本地曲绘、且带 MASTER 的曲里**均匀**抽一首。

    不偏向「有两张谱面」的歌；抽到哪首算哪首：
    有 Re:MASTER 就把 MASTER 和 Re:MASTER 两行都写，只有 MASTER 就写一行。
    """
    if db is None or not getattr(db, "all", None):
        return None

    pool: list[tuple[dict[str, Any], dict[int, dict[str, Any]]]] = []
    for s in db.all:
        try:
            sid = int(s.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not sid or not _cached_cover(sid):
            continue
        charts = _charts_of(db, sid)
        if 3 in (charts.get("DX") or {}) or 3 in (charts.get("SD") or {}):
            pool.append((s, charts))

    if not pool:
        return None

    s, charts = rng.choice(pool)
    sid = int(s["id"])
    try:
        ver = maimai_mod_version(sid, db) or db.version_name(sid)
    except Exception:
        ver = ""

    # SD 和 DX 都在的曲子，两类都要列出来：同一 index 是两张不同的谱，
    # 定数经常不一样，而且常常只有一边有 Re:MASTER（ジングルベル 就是）。
    # 两类都在时在定数后面挂个小 DX / SD，免得看串。
    rows = []
    for t in ("DX", "SD"):                     # DX 在前，跟游戏里的排序一致
        group = charts.get(t) or {}
        for idx in (3, 4):
            d = group.get(idx)
            if not d:
                continue
            rows.append(
                {
                    "index": idx,
                    "name": DIFF_CN[idx],
                    "level": str(d.get("level") or "?"),
                    "designer": d.get("designer") or "?",
                    "type": t,
                }
            )
    if not rows:
        return None
    multi_type = len({r["type"] for r in rows}) > 1

    return {
        "id": sid,
        "title": str(s.get("title") or "?"),
        "artist": _artist_of(s),
        "genre": GENRE_SHORT.get(str(s.get("genre") or ""), str(s.get("genre") or "?")),
        "bpm": s.get("bpm") or "?",
        "version": ver,
        "type": rows[0]["type"],
        "multi_type": multi_type,
        "charts": rows,
    }


def maimai_mod_version(song_id: int, db: Any) -> str:
    try:
        s = db.get(song_id)
        if not s:
            return ""
        import songdb  # 延迟导入，避免循环
        return songdb.version_of(s.get("version"))
    except Exception:
        return ""


def _artist_of(song: dict[str, Any]) -> str:
    a = str(song.get("artist") or "").strip()
    return a or "未知"


# ---------------------------------------------------------------- 抽签

def _seed_of(qq: str, day: str, reroll: int = 0) -> int:
    """日期 + QQ 一起做种子：同一天同人稳定，换天换人。
    reroll 只在「重新抽签」时非 0，换一条独立序列，不影响原本那一份。
    """
    raw = f"{day}|{qq}" if not reroll else f"{day}|{qq}|{reroll}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def draw(qq: str, date: str | None = None, db: Any = None, reroll: int = 0) -> dict[str, Any]:
    """抽一份今日运势。date 用北京日期，不传就取当前；db 不传就自动读缓存曲库。
    reroll=1 是「重新抽签」，种子跟着变。
    """
    day = date or beijing_day()
    rng = random.Random(_seed_of(str(qq), day, reroll))
    if db is None:
        db = get_db()

    score = rng.randint(0, 100)          # 完全随机，0 和 100 都可能
    kind = kind_of(score)

    food = pick_fastfood(rng)
    food["note"] = FASTFOOD_NOTE[kind]
    food["hour"] = FASTFOOD_HOUR[kind]

    ice = pick_icecream(rng)
    ice["note"] = f"{ice['note']} · {ICECREAM_NOTE[kind]}"

    phigros = pick_phigros(rng)

    song = pick_song(rng, db, kind)

    return {
        "day": day,
        "weekday": beijing_weekday(day),
        "stamp": beijing_stamp(),
        "score": score,
        "kind": kind,
        "stars": KIND_STARS[kind],
        "reroll": reroll,
        "food": food,
        "ice": ice,
        "phigros": phigros,
        "song": song,
    }


def text_of(res: dict[str, Any]) -> str:
    """纯文字版（发不出图时兜底）。"""
    f = res["food"]
    lines = [
        f"今日运势：{res['kind']}　{res['score']} 分　{res['stars']}",
        "",
        f"美食：{f['brand']} · {f['item']}（{f['price']}）",
        f"　　{f['note']}",
    ]
    ice = res.get("ice")
    if ice:
        lines.append("")
        lines.append(f"冰淇淋：{ice['name']}（{ice['en']} · {ice['style']}）")
        lines.append(f"　　{ice['note']}")
    pg = res.get("phigros")
    if pg:
        lines.append("")
        lines.append(f"phigros：{pg['title']}")
        lines.append(f"　　曲师：{pg.get('artist') or '未知'}")
        lines.append(f"　　IN {pg['in_level']}　谱师 {pg['in_charter']}")
        if pg.get("at_level"):
            lines.append(f"　　AT {pg['at_level']}　谱师 {pg['at_charter']}")
    s = res.get("song")
    if s:
        lines.append("")
        lines.append(f"今日曲：{s['title']}")
        lines.append(f"　　曲师：{s['artist']}")
        lines.append(f"　　{s['genre']} · BPM {s['bpm']} · {s['version']}")
        for c in s.get("charts") or []:
            tag = f" {c['type']}" if s.get("multi_type") else ""
            lines.append(f"　　{c['name']} {c['level']}{tag}　谱师 {c['designer']}")
        lines.append(f"　　曲目 ID：{s['id']}")
    lines.append("")
    lines.append(f"{res['day']} 北京时间　零点换签")
    return "\n".join(lines)


# ---------------------------------------------------------------- 配色（白色 Apple 风，难度色沿用 B50 light）

BG = (245, 245, 247)          # systemGray6
CARD = (255, 255, 255)
LINE = (232, 232, 237)
TEXT = (29, 29, 31)           # label
SUB = (110, 110, 115)         # secondaryLabel
SOFT = (142, 142, 147)        # tertiaryLabel
FILL = (242, 242, 247)

KIND_COLOR = {
    "凶": (255, 59, 48),      # systemRed
    "平": (142, 142, 147),    # systemGray
    "吉": (52, 199, 89),      # systemGreen
    "大吉": (255, 149, 0),    # systemOrange
}

# 难度色跟 B50 的 light 主题一致
DIFF_COLOR = {
    0: (52, 199, 89),
    1: (255, 149, 0),
    2: (255, 59, 48),
    3: (175, 82, 222),        # systemPurple
    4: (28, 28, 30),          # 近黑
}

ICE = (196, 88, 120)          # 冰淇淋：莓粉
FOOD = (199, 124, 2)          # 洋快餐：琥珀
PG_IN = (216, 62, 62)         # phigros IN：红药丸
PG_AT = (28, 28, 30)          # phigros AT：黑药丸

W, H = 760, 1290


# ---------------------------------------------------------------- 字体

def _font(size: int, bold: bool = False, kind: str = "cn", heavy: bool = False) -> Any:
    """cn = 微软雅黑；num = Segoe UI（比 Bahnschrift 更接近苹果的 SF）。

    heavy=True 时用 Segoe UI Black，给难度胶囊这种要压得住场子的地方。
    """
    if ImageFont is None:
        return None
    if kind == "num":
        if heavy:
            names = ["seguibl.ttf", "segoeuib.ttf", "ariblk.ttf"]
        elif bold:
            names = ["segoeuib.ttf", "seguisb.ttf", "bahnschrift.ttf"]
        else:
            names = ["segoeui.ttf", "bahnschrift.ttf"]
    else:
        names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhl.ttc"]
    for n in names:
        p = FONT_DIR / n
        if not p.exists():
            continue
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    try:
        return maimai_mod._font(size, bold, "cn")
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------- 画图小工具

def _panel(img: Any, box: tuple[int, int, int, int], r: int = 24) -> Any:
    """白卡片 + 很淡的投影（跟 B50 一样的做法）。"""
    img = maimai_mod._soft_shadow(img, box, r, spread=9, alpha=24, dy=3)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=r, fill=CARD)
    return img


def _wrap(d: Any, text: str, font: Any, max_w: int, max_lines: int = 1) -> str:
    """按像素宽截断，超出加省略号。"""
    if d.textlength(text, font=font) <= max_w:
        return text
    cur = text
    while cur and d.textlength(cur + "…", font=font) > max_w:
        cur = cur[:-1]
    return cur + "…"


def _cover(song_id: int, size: int) -> Any:
    p = _cover_path(song_id)
    if p is None or Image is None:
        return None
    try:
        im = Image.open(p).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    return maimai_mod._circle(im.resize((size, size), Image.LANCZOS), size, radius=int(size * 0.12))


def _pill(d: Any, x: int, y: int, text: str, color: tuple[int, int, int],
          font: Any, height: int = 34) -> int:
    """难度胶囊：大一点、字加粗，返回宽度。"""
    pw = int(d.textlength(text, font=font)) + 30
    d.rounded_rectangle((x, y, x + pw, y + height), radius=height // 2, fill=color)
    d.text((x + 15, y + height / 2), text, font=font, fill=(255, 255, 255), anchor="lm")
    return pw


def _clip(d: Any, text: str, font: Any, max_w: int) -> str:
    """太长就截断加省略号（谱师名有些长得离谱）。"""
    if d.textlength(text, font=font) <= max_w:
        return text
    t = str(text)
    while t and d.textlength(t + "…", font=font) > max_w:
        t = t[:-1]
    return t + "…"


def _phigros_cover(name: str, w: int, h: int) -> Any:
    """phigros 曲绘是宽幅（512×269 那种），等比铺满再居中裁到 w×h，最后切圆角。"""
    if Image is None:
        return None
    try:
        im = Image.open(PHIGROS_COVER_DIR / str(name)).convert("RGB")
    except Exception:
        return None
    iw, ih = im.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, int(iw * scale + 0.5)), max(h, int(ih * scale + 0.5))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    im = im.crop((left, top, left + w, top + h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=int(h * 0.10), fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


# ---------------------------------------------------------------- 图卡

def render_card(res: dict[str, Any]) -> bytes | None:
    """760×920：白色 Apple 风，版面语言沿用 B50（面板 + 圆角卡 + 发丝线 + 难度色）。"""
    if Image is None:
        return None

    kind = res["kind"]
    accent = KIND_COLOR.get(kind, SUB)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_label = _font(19, True, "cn")
    f_date = _font(15, False, "cn")
    f_kind = _font(56, True, "cn")
    f_star = _font(22, True, "cn")
    f_score = _font(102, True, "num")
    f_unit = _font(14, False, "cn")
    f_sec = _font(15, True, "cn")
    f_brand = _font(34, True, "cn")
    f_tag = _font(14, False, "num")
    f_item = _font(25, True, "cn")
    f_price = _font(15, False, "cn")
    f_note = _font(15, False, "cn")
    f_songtitle = _font(26, True, "cn")
    f_meta = _font(14, False, "cn")
    f_pill = _font(19, True, "num", heavy=True)
    f_level = _font(30, True, "num")
    f_designer = _font(15, False, "cn")
    f_tiny = _font(13, False, "cn")

    M = 32

    # ================= 顶部：分数
    img = _panel(img, (M, 24, W - M, 236), 30)
    d = ImageDraw.Draw(img)

    d.text((M + 28, 44), "今日运势", font=f_label, fill=SUB)
    d.text((W - M - 28, 47), f"{res['day']}　{res.get('weekday', '')}", font=f_date,
           fill=SOFT, anchor="ra")

    d.text((M + 28, 74), kind, font=f_kind, fill=accent)
    kw = d.textlength(kind, font=f_kind)
    d.text((M + 28 + kw + 18, 102), res["stars"], font=f_star, fill=accent)

    d.text((W - M - 28, 62), str(res["score"]), font=f_score, fill=TEXT, anchor="ra")
    d.text((W - M - 28, 174), "/ 100", font=f_unit, fill=SOFT, anchor="ra")

    bar_y = 196
    d.rounded_rectangle((M + 28, bar_y, W - M - 28, bar_y + 8), radius=4, fill=LINE)
    fill_w = int((W - M * 2 - 56) * res["score"] / 100)
    if fill_w > 0:
        d.rounded_rectangle((M + 28, bar_y, M + 28 + max(fill_w, 8), bar_y + 8), radius=4, fill=accent)

    # ================= 美食
    img = _panel(img, (M, 256, W - M, 500), 24)
    d = ImageDraw.Draw(img)
    f = res["food"]
    d.text((M + 28, 274), "最适合去吃的洋快餐", font=f_sec, fill=SOFT)
    d.text((M + 28, 296), f["brand"], font=f_brand, fill=TEXT)
    bw = d.textlength(f["brand"], font=f_brand)
    d.text((M + 28 + bw + 12, 310), f["tag"], font=f_tag, fill=(174, 174, 178))
    d.text((M + 28, 356), _wrap(d, f["item"], f_item, W - M * 2 - 56 - 140), font=f_item,
           fill=(199, 124, 2))
    d.text((W - M - 28, 364), f["price"], font=f_price, fill=SUB, anchor="ra")
    d.line([(M + 28, 424), (W - M - 28, 424)], fill=FILL, width=1)
    d.text((M + 28, 436), f["note"], font=f_note, fill=SUB)
    d.text((M + 28, 462), f["hour"], font=f_note, fill=SOFT)

    # ================= 曲子
    img = _panel(img, (M, 520, W - M, 846), 24)
    d = ImageDraw.Draw(img)
    d.text((M + 28, 538), "MAIMAI · 今天推荐打的歌", font=f_sec, fill=SOFT)

    s = res.get("song")
    if s:
        size = 184
        cx, cy = M + 28, 562
        cov = _cover(s["id"], size)
        if cov is not None:
            img.paste(cov, (cx, cy), cov)
            d.rounded_rectangle((cx, cy, cx + size, cy + size), radius=int(size * 0.12),
                                outline=LINE, width=1)
        else:
            d.rounded_rectangle((cx, cy, cx + size, cy + size), radius=int(size * 0.12),
                                fill=FILL, outline=LINE, width=1)
            d.text((cx + size // 2, cy + size // 2), "♪", font=f_kind, fill=SOFT, anchor="mm")

        tx = cx + size + 26
        tw = W - M - 28 - tx
        d.text((tx, cy - 2), _wrap(d, s["title"], f_songtitle, tw), font=f_songtitle, fill=TEXT)
        d.text((tx, cy + 32), _wrap(d, "曲师：" + s["artist"], f_meta, tw), font=f_meta, fill=SUB)
        d.text((tx, cy + 56), _wrap(d, f"{s['genre']}　BPM {s['bpm']}　{s['version']}", f_meta, tw),
               font=f_meta, fill=SOFT)

        # 难度胶囊：DX / SD 两类都列，两类都在时在定数后面挂个小 DX / SD
        ry = cy + 98
        right = W - M - 28
        for c in (s.get("charts") or [])[:3]:
            color = DIFF_COLOR.get(c["index"], SOFT)
            pw = _pill(d, tx, ry, c["name"], color, f_pill, 34)
            lx = tx + pw + 14
            d.text((lx, ry + 17), c["level"], font=f_level, fill=TEXT, anchor="lm")
            lw = d.textlength(c["level"], font=f_level)
            if s.get("multi_type"):
                d.text((lx + lw + 7, ry + 21), c["type"], font=f_tiny, fill=SOFT, anchor="lm")
                lw += 7 + d.textlength(c["type"], font=f_tiny) + 10
            avail = int(right - (lx + lw) - 12)
            d.text((right, ry + 17), _clip(d, f"谱师 {c['designer']}", f_designer, avail),
                   font=f_designer, fill=SUB, anchor="rm")
            ry += 40

        # 底部发丝线 + 曲目 ID
        d.line([(cx, 782), (W - M - 28, 782)], fill=FILL, width=1)
        d.text((cx, 794), f"曲目 ID {s['id']}　{s.get('type', 'SD')}", font=f_tiny, fill=SOFT)
    else:
        d.text((M + 28, 600), "曲库还没加载好，等会儿再抽一次。", font=f_item, fill=SUB)

    # ================= phigros
    img = _panel(img, (M, 866, W - M, 1050), 24)
    d = ImageDraw.Draw(img)
    pg = res.get("phigros")
    d.text((M + 28, 878), "PHIGROS · 今天推荐打的歌", font=f_sec, fill=SOFT)
    if pg:
        cw, ch = 220, 116
        cx, cy = M + 28, 900
        cov = _phigros_cover(pg["cover"], cw, ch)
        if cov is not None:
            img.paste(cov, (cx, cy), cov)
        else:
            d.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=int(ch * 0.10),
                                fill=FILL, outline=LINE, width=1)
        tx = cx + cw + 22
        tw = W - M - 28 - tx
        d.text((tx, cy - 6), _clip(d, pg["title"], f_songtitle, tw), font=f_songtitle, fill=TEXT)
        d.text((tx, cy + 26), _clip(d, "曲师 " + (pg.get("artist") or "未知"), f_meta, tw),
               font=f_meta, fill=SUB)

        f_pg_pill = _font(16, True, "num", heavy=True)
        f_pg_lv = _font(24, True, "num")

        def _diff_row(ry: int, label: str, color: tuple[int, int, int], lv: str, ch_name: str) -> None:
            pw = _pill(d, tx, ry, label, color, f_pg_pill, 28)
            d.text((tx + pw + 10, ry + 14), str(lv), font=f_pg_lv, fill=TEXT, anchor="lm")
            lw = d.textlength(str(lv), font=f_pg_lv)
            d.text((tx + pw + 22 + lw, ry + 15),
                   _clip(d, "谱师 " + ch_name, f_designer, tw - pw - 26 - lw),
                   font=f_designer, fill=SUB, anchor="lm")

        _diff_row(cy + 50, "IN", PG_IN, pg["in_level"], pg["in_charter"])
        if pg.get("at_level"):                      # 有 AT 才画这一行
            _diff_row(cy + 94, "AT", PG_AT, pg["at_level"], pg["at_charter"])
    else:
        d.text((M + 28, 930), "phigros 曲库还没抓，先跑 fetch_phigros.py。", font=f_note, fill=SUB)

    # ================= 冰淇淋
    img = _panel(img, (M, 1070, W - M, 1188), 24)
    d = ImageDraw.Draw(img)
    ice = res.get("ice") or {}
    d.text((M + 28, 1082), "今天推荐的冰淇淋口味", font=f_sec, fill=SOFT)
    if ice:
        f_ice = _font(30, True, "cn")
        d.text((M + 28, 1106), ice["name"], font=f_ice, fill=ICE)
        nw = d.textlength(ice["name"], font=f_ice)
        d.text((M + 28 + nw + 12, 1120), ice["en"], font=f_tag, fill=(174, 174, 178))
        d.text((W - M - 28, 1114), ice["style"], font=f_tag, fill=SOFT, anchor="ra")
        d.text((M + 28, 1160), _wrap(d, ice["note"], f_note, W - M * 2 - 56), font=f_note, fill=SUB)
    else:
        d.text((M + 28, 1130), "今天没抽到口味，明天再来。", font=f_item, fill=SUB)

    # ================= 页脚
    d.line([(M, H - 48), (W - M, H - 48)], fill=LINE, width=1)
    d.text((M, H - 36), f"贝尔娜提塔给你算的 · {res['stamp']}", font=f_tiny, fill=SOFT)
    d.text((W - M, H - 36), "每日一签 · 北京时间零点换", font=f_tiny, fill=SOFT, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    _db = get_db()
    r = draw(sys.argv[1] if len(sys.argv) > 1 else "3035888633", db=_db)
    print(text_of(r))
    png = render_card(r)
    if png:
        out = BASE_DIR / "fortune_preview.png"
        out.write_bytes(png)
        print(f"\n图卡：{out}（{len(png) // 1024} KB）")
