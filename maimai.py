"""水鱼查分器（diving-fish）B50 查询 + 图片渲染（仿官方 5 列卡片布局）。

布局参考 Shio796/maimai-b50-image-generator（MIT）：
    每行 5 张卡片，卡片 276×114 的网格，B35 在上、B15 在下，
    卡片内部：封面 / 标题 / 达成率 / 定数→RA / 评级 / FC·FS / 曲目 ID
数据：POST /api/maimaidxprober/query/player
曲绘：https://www.diving-fish.com/covers/{song_id:05d}.png
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

API = "https://www.diving-fish.com/api/maimaidxprober/query/player"
COVER_URL = "https://www.diving-fish.com/covers/{:05d}.png"
COVER_DIR = Path(__file__).with_name("covers")
# 落雪（LXNS）曲绘，两条路：
#   1. assets.lxns.net 静态 CDN —— 公开，不需要 key（主力）
#   2. maimai.lxns.net/api/v0/maimai/cover/{id} —— 需要 API Key（备用）
LXNS_ASSET = "https://assets.lxns.net/maimai/jacket/{}.png"
LXNS_ASSET_WEBP = "https://assets.lxns.net/maimai/jacket/{}.png!webp"
LXNS_COVER = "https://maimai.lxns.net/api/v0/maimai/cover/{}"
LXNS_BASE = "https://maimai.lxns.net/api/v0/maimai"
LXNS_PLATE = "https://assets.lxns.net/maimai/plate/{}.png"
PLATE_DIR = Path(__file__).with_name("plates")

# 牌子名 → 官方编号（照 maimai-b50-image-generator 的 plate_finder）
PLATE_GEN = {
    "超": 6104, "檄": 6108, "橙": 6112, "暁": 6116, "桃": 6120, "櫻": 6124,
    "紫": 6128, "堇": 6132, "白": 6136, "雪": 6140, "辉": 6144, "舞": 6149,
    "熊": 55101, "爽": 159101, "宙": 259101, "祭": 359101, "双": 459101, "宴": 559101,
}
PLATE_LV = ("極", "将", "神", "舞舞")
# 简繁/异体写法归一到官方用的字
PLATE_ALIAS = {
    "极": "極", "将": "将", "神": "神",
    "樱": "櫻", "晓": "暁", "辉": "辉", "舞": "舞",
}


def dan_color(n: int | str) -> tuple[int, int, int]:
    """段位底色：1-5 绿 / 6-10 红 / 11-20 紫 / 21-22 金。"""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return (142, 142, 147)
    if v <= 5:
        return (52, 199, 89)      # systemGreen
    if v <= 10:
        return (255, 59, 48)      # systemRed
    if v <= 20:
        return (175, 82, 222)     # systemPurple
    return (28, 28, 30)           # 黑（21-22 段）


def dan_name(n: int | str) -> str:
    """段位（additional_rating）转成官方叫法。
    1-10: 1段…10段 / 11-20: 真1段…真10段 / 21: 真皆传 / 22: 里皆传"""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return str(n)
    if v <= 0:
        return ""
    if v <= 10:
        return f"{v}段"
    if v <= 20:
        return f"真{v - 10}段"
    if v == 21:
        return "真皆传"
    if v == 22:
        return "里皆传"
    return f"{v}段"


PLATE_LIST = Path(__file__).with_name("data") / "plate_list.json"
_PLATE_MAP: dict[str, int] | None = None


def _plate_map() -> dict[str, int]:
    """官方牌子表（name → id），来自 /api/v0/maimai/plate/list。"""
    global _PLATE_MAP
    if _PLATE_MAP is not None:
        return _PLATE_MAP
    m: dict[str, int] = {}
    try:
        raw = json.loads(PLATE_LIST.read_text(encoding="utf-8"))
        for it in raw.get("plates") or []:
            n = str(it.get("name") or "").strip()
            i = it.get("id")
            if n and isinstance(i, int):
                m[n] = i
    except Exception:
        pass
    _PLATE_MAP = m
    return m


# 简繁 / 异体写法，查不到时再归一一次
PLATE_FOLD = {
    "极": "極", "樱": "櫻", "晓": "暁", "辉": "輝", "堇": "菫",
    "华": "華", "镜": "鏡", "双": "双",
}


def _fold_name(n: str) -> str:
    return "".join(PLATE_FOLD.get(ch, ch) for ch in n)


def plate_id_from_name(name: str) -> int | None:
    """「檄将」→ 6109。认不出来就返回 None。"""
    n = (name or "").strip()
    if not n:
        return None

    # 官方牌子表优先（400 条，含所有代）
    table = _plate_map()
    if table:
        if n in table:
            return table[n]
        folded = _fold_name(n)
        if folded in table:
            return table[folded]
        for k, v in table.items():
            if _fold_name(k) == folded:
                return v

    # 回退：老规则（表没加载时用）
    if n == "霸者":
        return 6148

    head = PLATE_ALIAS.get(n[0], n[0])
    tail = n[1:] if len(n) > 1 else ""
    tail = PLATE_ALIAS.get(tail, tail)

    if head == "真":
        true_lv = ("極", "神", "舞舞")
        return 6101 + true_lv.index(tail) if tail in true_lv else None

    base = PLATE_GEN.get(head)
    if base is None or tail not in PLATE_LV:
        return None
    return base + PLATE_LV.index(tail)


async def fetch_plate(plate_name: str) -> bytes | None:
    """下载官方牌子图（720×116 RGBA），缓存到 plates/{id}.png。"""
    pid = plate_id_from_name(plate_name)
    if not pid:
        return None
    PLATE_DIR.mkdir(exist_ok=True)
    p = PLATE_DIR / f"{pid}.png"
    if p.exists() and p.stat().st_size > 2000:
        return p.read_bytes()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0),
                                     follow_redirects=True) as c:
            r = await c.get(LXNS_PLATE.format(pid))
            if r.status_code == 200 and len(r.content) > 2000:
                p.write_bytes(r.content)
                return r.content
    except Exception:
        pass
    return None
# QQ 头像（公开，不需要鉴权）
QQ_AVATAR = "https://q.qlogo.cn/headimg_dl?dst_uin={}&spec=640"
AVATAR_DIR = Path(__file__).with_name("avatars")

DIFF_NAME = {0: "BASIC", 1: "ADVANCED", 2: "EXPERT", 3: "MASTER", 4: "Re:MASTER"}
# 官方配色
DIFF_COLOR = {
    0: (129, 217, 85),
    1: (245, 189, 21),
    2: (255, 129, 141),
    3: (159, 81, 220),
    4: (138, 0, 226),
}

# 评级里 S 的逐字配色：第 1 个金、第 2 个天蓝、第 3 个粉；加号白色小一号
RANK_S_COLORS = [
    (255, 199, 64),    # 金
    (105, 203, 255),   # 天蓝
    (255, 138, 196),   # 粉
]
RANK_PLUS_COLOR = (255, 255, 255)
RANK_S_COLORS_LIGHT = [
    (255, 149, 0),     # systemOrange
    (0, 122, 255),     # systemBlue
    (255, 45, 85),     # systemPink
]
RANK_PLUS_COLOR_LIGHT = (60, 60, 67)

# 主题
THEMES = {
    "dark": {
        "bg": (26, 26, 34),
        "panel": (40, 40, 52),
        "card": (40, 40, 52),
        "line": (56, 56, 72),
        "text": (240, 240, 246),
        "sub": (150, 150, 168),
        "soft": (190, 190, 205),
        "accent": (255, 196, 92),
        "mark": (255, 214, 120),
        "avatar_bg": (58, 58, 76),
        "slot": (62, 62, 80),
        "frame": (56, 56, 72),
        "shadow": (0, 0, 0),
    },
    "light": {
        "bg": (245, 245, 247),      # Apple systemGray6
        "panel": (255, 255, 255),
        "card": (255, 255, 255),
        "line": (209, 209, 214),    # systemGray4
        "text": (29, 29, 31),       # label
        "sub": (134, 134, 139),     # secondaryLabel
        "soft": (72, 72, 74),       # tertiaryLabel
        "accent": (0, 113, 227),    # systemBlue
        "mark": (255, 149, 0),      # systemOrange
        "avatar_bg": (229, 229, 234),
        "slot": (242, 242, 247),
        "frame": (229, 229, 234),
        "shadow": (0, 0, 0),
    },
}
DIFF_COLOR_LIGHT = {
    0: (52, 199, 89),     # systemGreen
    1: (255, 149, 0),     # systemOrange
    2: (255, 59, 48),     # systemRed
    3: (175, 82, 222),    # systemPurple
    4: (28, 28, 30),      # 近黑
}

# 版面（仿官方）
W, H = 1412, 1870
PAD = 16
CARD_W, CARD_H = 260, 130
GAP_X, GAP_Y = 16, 12
COL, ROW = 5, 7
Y_B35 = 268
Y_B15 = 1334

BG = (26, 26, 34)
CARD_BG = (40, 40, 52)
LINE = (56, 56, 72)
TEXT = (240, 240, 246)
SUB = (150, 150, 168)
ACCENT = (255, 196, 92)

FONT_DIR = Path("C:/Windows/Fonts")
ASSET_DIR = Path(__file__).with_name("assets")
AVATAR = ASSET_DIR / "avatar.png"          # 顶部头像
NOART = ASSET_DIR / "avatar.png"           # 没有曲绘时的占位


# 金属金：顶部高光 → 暗金 → 底部回亮，竖向走，像镀金数字
METAL_STOPS = [
    (255, 253, 230),
    (255, 232, 140),
    (255, 198, 64),
    (226, 150, 26),
    (255, 206, 82),
    (255, 246, 190),
]


def _gold_gradient(w: int, h: int) -> Image.Image:
    """竖向金属金渐变：高光 → 暗 → 回亮。"""
    im = Image.new("RGB", (max(1, w), max(1, h)))
    px = im.load()
    n = len(METAL_STOPS) - 1
    for y in range(max(1, h)):
        t = (y / max(1, h - 1)) * n if h > 1 else 0.0
        i = min(int(t), n - 1)
        f = t - i
        a, b = METAL_STOPS[i], METAL_STOPS[i + 1]
        c = (
            int(a[0] + (b[0] - a[0]) * f),
            int(a[1] + (b[1] - a[1]) * f),
            int(a[2] + (b[2] - a[2]) * f),
        )
        for x in range(max(1, w)):
            px[x, y] = c
    return im


# 逐字彩虹：每个数字一个高饱和色，不跨色环做渐变，干净又鲜艳
RATING_COLORS = [
    (255, 59, 48),     # 红
    (255, 122, 0),     # 橙
    (255, 196, 0),     # 黄
    (0, 180, 96),      # 绿
    (0, 150, 220),     # 青蓝
    (110, 90, 235),    # 蓝紫
    (200, 70, 200),    # 品红
]


def _draw_rainbow_rating(
    base: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    right_align: bool = True,
    outline: tuple[int, int, int] | None = None,
) -> None:
    """逐字彩虹：每一位一个鲜艳色，整体右对齐。"""
    d0 = ImageDraw.Draw(base)
    widths = [d0.textlength(ch, font=font) for ch in text]
    total = sum(widths)
    cx = xy[0] - total if right_align else xy[0]
    cy = xy[1]
    for i, (ch, w) in enumerate(zip(text, widths)):
        col = RATING_COLORS[i % len(RATING_COLORS)]
        if outline is not None:
            d0.text(
                (cx, cy), ch, font=font, fill=col, anchor="la",
                stroke_width=3, stroke_fill=outline,
            )
        else:
            d0.text((cx, cy), ch, font=font, fill=col, anchor="la")
        cx += w


def _plate_color(name: str, theme: str = "dark") -> tuple[int, int, int]:
    """按牌子等级给颜色：舞舞金 / 神银 / 将紫 / 极蓝。"""
    n = name or ""
    if "舞舞" in n:
        return (255, 199, 64)
    if "神" in n:
        return (196, 202, 218)
    if "将" in n:
        return (176, 122, 240)
    if "极" in n:
        return (96, 158, 255)
    return (150, 156, 172)


def _draw_dan_badge(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    num: int | str,
    text: str,
    theme: str = "dark",
    size: int = 18,
) -> int:
    """段位药丸：底色按等级，白字。返回右边界。"""
    if not text:
        return x
    f = _font(size, True, "cn")
    pad_x, pad_y = 14, 6
    tw = int(d.textlength(text, font=f))
    th = size + 4
    color = dan_color(num)
    box = (x, y, x + tw + pad_x * 2, y + th + pad_y * 2)
    d.rounded_rectangle(box, radius=(th + pad_y * 2) // 2, fill=color)
    d.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), text,
           font=f, fill=(255, 255, 255), anchor="mm")
    return box[2]


def _draw_plate_badge(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    theme: str = "dark",
    size: int = 20,
) -> int:
    """画一个圆角牌子徽章，返回右边界的 x。"""
    if not text:
        return x
    f = _font(size, True, "cn")
    pad_x, pad_y = 14, 7
    tw = int(d.textlength(text, font=f))
    th = size + 6
    color = _plate_color(text, theme)
    box = (x, y, x + tw + pad_x * 2, y + th + pad_y * 2)

    if theme == "light":
        # 浅色主题：实底 + 白字
        d.rounded_rectangle(box, radius=(th + pad_y * 2) // 2, fill=color)
        d.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), text,
               font=f, fill=(255, 255, 255), anchor="mm")
    else:
        # 深色主题：半透明底 + 彩色字 + 描边
        d.rounded_rectangle(box, radius=(th + pad_y * 2) // 2,
                            fill=(28, 28, 34), outline=color, width=2)
        d.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), text,
               font=f, fill=color, anchor="mm")
    return box[2]


def _circle(img: Image.Image, size: int, radius: int | None = None) -> Image.Image:
    """缩放到 size×size 并做圆角。"""
    im = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    r = size // 2 if radius is None else radius
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _load_avatar(size: int) -> Image.Image | None:
    if not AVATAR.exists():
        return None
    try:
        return _circle(Image.open(AVATAR), size, radius=24)
    except Exception:
        return None


def _load_noart(size: int) -> Image.Image | None:
    """没曲绘时的占位：原图直接用，不压暗。"""
    if not NOART.exists():
        return None
    try:
        return _circle(Image.open(NOART), size, radius=8)
    except Exception:
        return None

# 可选：如果 vendor/maimai-b50-image-generator/static 里放了官方素材，
# 就自动改用原项目的贴图渲染（更还原）；没有素材就用本文件的内置布局。
VENDOR = Path(__file__).with_name("vendor") / "maimai-b50-image-generator"
VENDOR_STATIC = VENDOR / "static"


def vendor_ready() -> bool:
    """官方素材齐不齐（b50_bg + 5 张难度底图 + 字体）。"""
    need = [
        VENDOR_STATIC / "mai" / "pic" / "b50_bg.png",
        VENDOR_STATIC / "mai" / "pic" / "b50_score_master.png",
        VENDOR_STATIC / "ResourceHanRoundedCN-Bold.ttf",
    ]
    return all(p.exists() for p in need)


def _font(size: int, bold: bool = True, kind: str = "cn") -> ImageFont.FreeTypeFont:
    """kind: 'cn' 中文（微软雅黑），'num' 数字英文（Bahnschrift，SEGA 那种 DIN 感）。"""
    if kind == "num":
        for n in ("bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf"):
            p = FONT_DIR / n
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:
                    continue
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhl.ttc"]
    for n in names:
        p = FONT_DIR / n
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _mid(ch: dict[str, Any]) -> int:
    for k in ("song_id", "id", "music_id"):
        v = ch.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


def _rank(acc: float) -> str:
    if acc >= 100.5:
        return "SSS+"
    if acc >= 100.0:
        return "SSS"
    if acc >= 99.5:
        return "SS+"
    if acc >= 99.0:
        return "SS"
    if acc >= 98.0:
        return "S+"
    if acc >= 97.0:
        return "S"
    if acc >= 94.0:
        return "AAA"
    if acc >= 90.0:
        return "AA"
    if acc >= 80.0:
        return "A"
    if acc >= 75.0:
        return "BBB"
    if acc >= 70.0:
        return "BB"
    if acc >= 60.0:
        return "B"
    if acc >= 50.0:
        return "C"
    return "D"


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


async def fetch(
    username: str | None = None,
    qq: int | str | None = None,
    token: str = "",
    password: str = "",
    bearer: str = "",
) -> dict[str, Any]:
    """`bearer` 是水鱼 OAuth 的 access token：走它时**查询对象由令牌决定**，
    请求体里不带也不该带 username / qq（带了会被忽略）。"""
    payload: dict[str, Any] = {"b50": True}
    if bearer:
        pass                                  # 谁的成绩由 token 说了算
    elif username:
        payload["username"] = username
        if password:
            payload["credentials"] = password
    elif qq:
        payload["qq"] = int(qq)
    else:
        raise ValueError("username / qq / bearer 至少给一个")

    headers = {"Content-Type": "application/json", "User-Agent": "bernie-bot/2.0 (maimai b50)"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif token:
        headers["Import-Token"] = token

    async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=15.0)) as c:
        r = await c.post(API, json=payload, headers=headers)
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = None
            msg = ""
            if isinstance(body, dict):
                msg = str(body.get("message") or "")
            elif isinstance(body, str):
                msg = body[:120]
            if "not exists" in msg:
                raise RuntimeError("水鱼上查不到这个名字。WMC ID 不能直接查，要先在水鱼注册并导入成绩")
            if r.status_code == 403:
                raise RuntimeError(msg or "这个玩家设了隐私，需要 Import-Token 或密码")
            if r.status_code == 401:
                raise RuntimeError("密码不对")
            raise RuntimeError(msg or f"水鱼返回 {r.status_code}")

        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"水鱼返回了意外的格式：{str(data)[:120]}")
        return data


# ---------------------------------------------------------------- 落雪 LXNS

def _lxns_chart(s: dict[str, Any]) -> dict[str, Any]:
    """落雪的一条成绩 → 水鱼 charts 的字段（render 只认水鱼那套）。

    字段名做容错：不同版本落雪这条用过 `song_name`/`title`、`rating`/`ra`，
    两边都认，省得接口微调就全挂。
    """
    def pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            v = s.get(k)
            if v not in (None, ""):
                return v
        return default

    lvi = pick("level_index", "difficulty", default=0)
    try:
        lvi = int(lvi)
    except (TypeError, ValueError):
        lvi = 0
    return {
        "song_id": pick("id", "song_id", "music_id", default=0),
        "title": pick("song_name", "title", default="未知曲目"),
        "level": str(pick("level", default="?")),
        "ds": float(pick("ds", "level_value", default=0) or 0),
        "ra": int(float(pick("ra", "rating", default=0) or 0)),
        "achievements": float(pick("achievements", default=0) or 0),
        "rate": str(pick("rate", default="")),
        "fc": str(pick("fc", default="") or ""),
        "fs": str(pick("fs", default="") or ""),
        "level_index": lvi,
        "type": "DX" if str(pick("type", default="standard")).lower() == "dx" else "SD",
    }


async def fetch_lxns(
    qq: int | str | None = None,
    friend_code: int | str | None = None,
    key: str = "",
) -> dict[str, Any]:
    """查落雪，返回**水鱼 /query/player 那套结构**，直接喂给 `render()`。

    认证走 `Authorization: <key>`，**不带 Bearer**（带了会 401）。
    标识二选一：QQ 号（落雪那边绑过的）或 15 位好友码。先查 player 拿 friend_code，再拉 bests。
    """
    if not key:
        raise RuntimeError("没配 maimai.lxns_key")
    headers = {"Authorization": key, "User-Agent": "bernie-bot/2.0 (maimai b50)"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=15.0)) as c:
        if friend_code:
            player_url = f"{LXNS_BASE}/player/{friend_code}"
        elif qq:
            player_url = f"{LXNS_BASE}/player/qq/{qq}"
        else:
            raise ValueError("qq 或 friend_code 至少给一个")

        r = await c.get(player_url, headers=headers)
        if r.status_code == 401:
            raise RuntimeError("落雪的 key 不对（要用个人 API 密钥，头里不加 Bearer）")
        if r.status_code == 404:
            raise RuntimeError("落雪上查不到这个号。要先用落雪官方代理上传一次成绩，并允许第三方查询")
        if r.status_code >= 400:
            body = ""
            try:
                body = str((r.json() or {}).get("message") or "")
            except Exception:
                pass
            raise RuntimeError(body or f"落雪返回 {r.status_code}")

        raw = r.json()
        player = raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
        if not isinstance(player, dict):
            raise RuntimeError("落雪返回了意外的格式")

        fc = player.get("friend_code")
        if not fc:
            raise RuntimeError("这个号没有好友码，无法拉 B50")

        r2 = await c.get(f"{LXNS_BASE}/player/{fc}/bests", headers=headers)
        if r2.status_code >= 400:
            body = ""
            try:
                body = str((r2.json() or {}).get("message") or "")
            except Exception:
                pass
            raise RuntimeError(body or f"落雪 bests 返回 {r2.status_code}")

        raw2 = r2.json()
        best = raw2.get("data") if isinstance(raw2, dict) and "data" in raw2 else raw2
        if not isinstance(best, dict):
            raise RuntimeError("落雪 bests 格式不对")

        sd = [_lxns_chart(x) for x in (best.get("standard") or []) if isinstance(x, dict)]
        dx = [_lxns_chart(x) for x in (best.get("dx") or []) if isinstance(x, dict)]

        return {
            "nickname": player.get("name") or player.get("nickname") or str(qq or fc),
            "username": player.get("name") or "",
            "rating": player.get("rating") or 0,
            "additional_rating": player.get("course_rank") or 0,
            "plate": (player.get("trophy") or {}).get("name") or "",
            "charts": {"sd": sd, "dx": dx},
            "_source": "lxns",
        }


def _valid_cover(p: Path) -> bool:
    """缓存文件是不是真的曲绘。小文件可能是缩略图或错误页，要验尺寸。"""
    try:
        if not p.exists():
            return False
        n = p.stat().st_size
        if n < 2000:
            return False
        if n > 30000:
            return True          # 正常曲绘都 200-340KB，大文件直接信
        with Image.open(p) as im:  # 小文件（可能被 webp 顶过）验一下尺寸
            return min(im.size) >= 200
    except Exception:
        return True              # 读不了就不拦，交给上层判断


def _cover_ids(mid: int) -> list[int]:
    """水鱼曲绘 ID 的候选顺序（照抄 maimai-b50-image-generator 的 music_picture 逻辑）：
    先原 ID，再试 DX 偏移（-100000），再试 SD/DX 互转（±10000）。
    """
    if not mid:
        return []
    out = [mid]
    if mid > 100000:
        out.append(mid - 100000)
        # 宴谱 / 派生谱：110121 → 121、121852 → 1852
        out.append(mid % 10000)
    if mid > 11000:
        # 国服 DX 曲 ID 会到 11xxx / 111xxx，对应落雪的 1xxx
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


async def fetch_qq_avatar(qq: int | str, max_age_days: float = 3.0) -> bytes | None:
    """下载 QQ 头像，缓存到 avatars/qq_{uin}.jpg，默认 3 天过期。"""
    try:
        uin = int(qq)
    except (TypeError, ValueError):
        return None
    AVATAR_DIR.mkdir(exist_ok=True)
    p = AVATAR_DIR / f"qq_{uin}.jpg"
    if p.exists() and p.stat().st_size > 1000:
        import time as _t
        if (_t.time() - p.stat().st_mtime) < max_age_days * 86400:
            return p.read_bytes()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True) as c:
            r = await c.get(QQ_AVATAR.format(uin))
            if r.status_code == 200 and len(r.content) > 1000:
                p.write_bytes(r.content)
                return r.content
    except Exception:
        pass
    if p.exists():
        return p.read_bytes()
    return None


async def fetch_covers(ids: list[int], client: httpx.AsyncClient | None = None, lxns_key: str = "") -> dict[int, bytes]:
    COVER_DIR.mkdir(exist_ok=True)
    out: dict[int, bytes] = {}
    own = client is None
    c = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    sem = asyncio.Semaphore(8)

    async def get_one(cid: int) -> bytes | None:
        p = COVER_DIR / f"{cid:05d}.png"
        if _valid_cover(p):
            return p.read_bytes()
        async with sem:
            try:
                r = await c.get(COVER_URL.format(cid))
                if r.status_code == 200 and len(r.content) > 1000:
                    p.write_bytes(r.content)
                    return r.content
            except Exception:
                pass
        return None

    async def get_lxns_asset(cid: int) -> bytes | None:
        """落雪静态 CDN（png 原图 → webp 变体），公开可用，不需要 key。"""
        p = COVER_DIR / f"{cid:05d}.png"
        if _valid_cover(p):
            return p.read_bytes()
        for url in (LXNS_ASSET.format(cid), LXNS_ASSET_WEBP.format(cid)):
            async with sem:
                try:
                    r = await c.get(url)
                    if r.status_code == 200 and len(r.content) > 1000:
                        p.write_bytes(r.content)
                        return r.content
                except Exception:
                    pass
        return None

    async def get_lxns_api(cid: int) -> bytes | None:
        """落雪 cover 接口，需要 API Key。"""
        if not lxns_key:
            return None
        p = COVER_DIR / f"{cid:05d}.png"
        async with sem:
            try:
                r = await c.get(LXNS_COVER.format(cid), headers={"Authorization": lxns_key})
                if r.status_code == 200 and len(r.content) > 1000:
                    p.write_bytes(r.content)
                    return r.content
            except Exception:
                pass
        return None

    async def one(mid: int) -> None:
        for cid in _cover_ids(mid):
            got = await get_one(cid)
            if got:
                out[mid] = got
                return
        # 水鱼全空 → 落雪静态 CDN
        for cid in _cover_ids(mid):
            got = await get_lxns_asset(cid)
            if got:
                out[mid] = got
                return
        # 还不行 → 落雪 API（要 key）
        for cid in _cover_ids(mid):
            got = await get_lxns_api(cid)
            if got:
                out[mid] = got
                return

    try:
        await asyncio.gather(*(one(i) for i in set(ids) if i))
    finally:
        if own:
            await c.aclose()
    return out


def pick_b50(data: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """水鱼 b50 的 charts 是 {"sd": [35 首], "dx": [15 首]}；也兼容直接给 list。"""
    raw = data.get("charts")
    old: list[dict] = []
    new: list[dict] = []

    if isinstance(raw, dict):
        sd, dx = raw.get("sd"), raw.get("dx")
        if isinstance(sd, list) or isinstance(dx, list):
            old = [c for c in (sd or []) if isinstance(c, dict)]
            new = [c for c in (dx or []) if isinstance(c, dict)]
        else:
            merged: list[dict] = []
            for v in raw.values():
                if isinstance(v, list):
                    merged += [c for c in v if isinstance(c, dict)]
            merged.sort(key=lambda c: -float(c.get("ra") or 0))
            return merged[:35], merged[35:50]
    elif isinstance(raw, list):
        rows = [c for c in raw if isinstance(c, dict)]
        new = [c for c in rows if c.get("is_new") or c.get("isNew")]
        old = [c for c in rows if c not in new]
    else:
        return [], []

    for c in old + new:
        c.setdefault("ra", 0.0)
    old.sort(key=lambda c: -float(c.get("ra") or 0))
    new.sort(key=lambda c: -float(c.get("ra") or 0))
    return old[:35], new[:15]


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], r: int, fill) -> None:
    draw.rounded_rectangle(box, radius=r, fill=fill)


def _soft_shadow(img: Image.Image, box: tuple[int, int, int, int], radius: int,
                 spread: int = 8, alpha: int = 22, dy: int = 3) -> Image.Image:
    """Apple 那种很淡的投影：偏移 + 高斯模糊，不描边。"""
    from PIL import ImageFilter
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (box[0], box[1] + dy, box[2], box[3] + dy), radius=radius, fill=(0, 0, 0, alpha)
    )
    layer = layer.filter(ImageFilter.GaussianBlur(spread))
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert(img.mode)


def _draw_rank(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    size: int = 21,
    base: tuple[int, int, int] = (240, 240, 246),
    right_align: bool = True,
    theme: str = "dark",
) -> None:
    """逐字符画评级。

    规则：第 1 个 S 金色、第 2 个天蓝、第 3 个粉；加号白色且小一号；
    其余字母（A/B/C/D）用 base 色。
    """
    f_main = _font(size, True, "num")
    f_plus = _font(max(9, int(size * 0.68)), True, "num")
    scol = RANK_S_COLORS if theme != "light" else RANK_S_COLORS_LIGHT
    pcol = RANK_PLUS_COLOR if theme != "light" else RANK_PLUS_COLOR_LIGHT

    widths = []
    for ch in text:
        f = f_plus if ch == "+" else f_main
        widths.append(d.textlength(ch, font=f))

    cx = x - sum(widths) if right_align else x
    s_idx = 0
    for ch, w in zip(text, widths):
        if ch == "+":
            f, col = f_plus, pcol
        elif ch == "S":
            f, col = f_main, scol[min(s_idx, 2)]
            s_idx += 1
        else:
            f, col = f_main, base
        d.text((cx, y), ch, font=f, fill=col, anchor="lm")
        cx += w


def make_quiz_image(covers: dict[int, bytes], song_id: int, level: int = 2,
                    blur: int = 0) -> bytes | None:
    """猜曲用图：裁曲绘中间一块，放大 + 模糊。

    level 越高越难：0=整图轻微模糊，1=中心 60%，2=中心 40%，3=中心 28%
    """
    from PIL import ImageFilter
    raw = covers.get(song_id)
    if not raw:
        return None
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None

    w, h = im.size
    crop_ratio = {0: 1.0, 1: 0.60, 2: 0.40, 3: 0.28}.get(level, 0.40)
    cw, ch = int(w * crop_ratio), int(h * crop_ratio)
    left, top = (w - cw) // 2, (h - ch) // 2
    im = im.crop((left, top, left + cw, top + ch)).resize((420, 420), Image.LANCZOS)

    # 轻微降饱和 + 模糊，避免一眼认出
    try:
        from PIL import ImageEnhance
        im = ImageEnhance.Color(im).enhance(1.15)
        im = ImageEnhance.Brightness(im).enhance(1.03)
    except Exception:
        pass
    im = im.filter(ImageFilter.GaussianBlur(blur if blur else {0: 0, 1: 1.2, 2: 2.4, 3: 3.6}.get(level, 2.4)))

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def render(data: dict[str, Any], covers: dict[int, bytes] | None = None,
           avatar: bytes | None = None, theme: str = "dark",
           plate_img: bytes | None = None) -> bytes:
    covers = covers or {}
    T = THEMES.get(theme, THEMES["dark"])
    DC = DIFF_COLOR if theme != "light" else DIFF_COLOR_LIGHT
    nickname = str(data.get("nickname") or data.get("username") or "未知玩家")
    rating = data.get("rating")
    plate = data.get("plate") or ""
    add = data.get("additional_rating")

    old, new = pick_b50(data)

    img = Image.new("RGB", (W, H), T["bg"])
    d = ImageDraw.Draw(img)

    f_name = _font(34, True, "cn")       # 昵称：可能含中文
    f_big = _font(74, True, "num")       # 大 rating：数字
    f_sub = _font(19, False, "cn")       # 说明行：中文
    f_sec = _font(22, True, "cn")        # 分区标题
    f_title = _font(14, True, "cn")      # 曲名：中文为主
    f_acc = _font(28, True, "num")       # 达成率：数字，最有冲击力
    f_small = _font(13, False, "cn")     # 定数→RA（含箭头，需要中文字体）
    f_rank = _font(21, True, "num")      # SSS+
    f_id = _font(11, False, "num")       # 曲目 ID
    f_diff = _font(15, True, "num")      # 难度名（全大写英文）

    # ---------- 顶部信息区
    img = _soft_shadow(img, (PAD, 20, W - PAD, 194), 24, spread=10, alpha=26, dy=4)
    d = ImageDraw.Draw(img)
    _rounded(d, (PAD, 20, W - PAD, 194), 24, T["panel"])

    # 头像（QQ 头像优先，没有就大肥鱼）
    av = None
    if avatar:
        try:
            av = _circle(Image.open(io.BytesIO(avatar)), 120, radius=24)
        except Exception:
            av = None
    if av is None:
        av = _load_avatar(120)
    if av:
        img.paste(av, (PAD + 18, 40), av)
    else:
        d.rounded_rectangle((PAD + 18, 40, PAD + 138, 160), radius=24, fill=T["avatar_bg"])
        d.text((PAD + 78, 100), "头\n像", font=f_sub, fill=T["sub"], anchor="mm", align="center")

    # 名字 + 段位 + 牌子徽章
    name_x = PAD + 160
    name_txt = _truncate(d, nickname, f_name, 420)
    d.text((name_x, 48), name_txt, font=f_name, fill=T["text"])

    if plate:
        name_w = int(d.textlength(name_txt, font=f_name))
        bx = name_x + name_w + 16
        placed = False
        if plate_img:
            try:
                pi = Image.open(io.BytesIO(plate_img)).convert("RGBA")
                # 官方牌子 720×116，缩到高 52，与名字行居中对齐
                ph = 52
                pw = int(pi.width * ph / pi.height)
                pi = pi.resize((pw, ph), Image.LANCZOS)
                img.paste(pi, (bx, 42), pi)
                placed = True
            except Exception:
                placed = False
        if not placed:
            _draw_plate_badge(d, bx, 46, str(plate), theme=theme, size=20)

    if add:
        dn = dan_name(add)
        if dn:
            _draw_dan_badge(d, PAD + 160, 90, add, dn, theme=theme, size=19)

    # rating 分解
    sd_sum = sum(float(c.get("ra") or 0) for c in old)
    dx_sum = sum(float(c.get("ra") or 0) for c in new)
    d.text((PAD + 160, 132), f"B35 {sd_sum:.0f}  +  B15 {dx_sum:.0f}", font=f_sub, fill=T["soft"])

    # 右侧大 rating
    if rating is not None:
        rt = str(rating)
        try:
            rv = int(rating)
        except (TypeError, ValueError):
            rv = 0
        if rv >= 15000:
            # 15000 以上：逐字彩虹 + 描边
            _draw_rainbow_rating(
                img, (W - PAD - 24, 40), rt, f_big,
                right_align=True,
                outline=(255, 255, 255) if theme == "light" else (0, 0, 0),
            )
            d.text((W - PAD - 24, 124), "RATING", font=f_sub,
                   fill=T["sub"], anchor="ra")
        else:
            d.text((W - PAD - 24, 40), rt, font=f_big, fill=ACCENT, anchor="ra")
            d.text((W - PAD - 24, 124), "RATING", font=f_sub, fill=SUB, anchor="ra")

    # ---------- 卡片列表
    def draw_block(y0: int, title: str, rows: list[dict]) -> None:
        nonlocal img, d
        d.text((PAD, y0 - 56), title, font=f_sec, fill=T["accent"])
        for i, ch in enumerate(rows):
            r, col = divmod(i, COL)
            x = PAD + col * (CARD_W + GAP_X)
            y = y0 + r * (CARD_H + GAP_Y)

            di = int(ch.get("level_index", 3))
            dcolor = DC.get(di, T["sub"])

            # 卡片底
            if theme == "light":
                img = _soft_shadow(img, (x, y, x + CARD_W, y + CARD_H), 18, spread=6, alpha=26, dy=3)
                d = ImageDraw.Draw(img)
            d.rounded_rectangle((x, y, x + CARD_W, y + CARD_H), radius=18, fill=T["card"])
            # 左侧难度色条（Re:MASTER 用白色）
            rm = (255, 255, 255) if theme == "dark" else (17, 17, 24)
            d.rounded_rectangle((x, y, x + 5, y + CARD_H), radius=3,
                                fill=rm if di == 4 else dcolor)

            # 封面
            cov = covers.get(_mid(ch))
            thumb = None
            if cov:
                try:
                    thumb = Image.open(io.BytesIO(cov)).convert("RGB").resize((70, 70))
                except Exception:
                    thumb = None
            if thumb:
                rounded = _circle(thumb, 70, radius=10)
                img.paste(rounded, (x + 14, y + 14), rounded)
                if theme == "light":
                    d.rounded_rectangle(
                        (x + 14, y + 14, x + 84, y + 84), radius=10,
                        outline=(232, 232, 237), width=1,
                    )
            else:
                # 没有曲绘：贴大肥鱼 + 压暗 + 难度色边框
                noart = _load_noart(70)
                if noart:
                    img.paste(noart, (x + 14, y + 14), noart)
                    d.rounded_rectangle(
                        (x + 14, y + 14, x + 78, y + 78),
                        radius=8,
                        outline=dcolor,
                        width=2,
                    )
                else:
                    d.rounded_rectangle((x + 14, y + 14, x + 78, y + 78), radius=8, fill=T["slot"])
                    d.text((x + 46, y + 46), "♪", font=f_sub, fill=T["sub"], anchor="mm")

            # 曲名（占满整行，不与评级抢位置）
            name_txt = str(ch.get("title") or "?")
            if str(ch.get("type", "")).upper() == "DX":
                name_txt = "[DX] " + name_txt
            d.text((x + 86, y + 10), _truncate(d, name_txt, f_title, CARD_W - 96), font=f_title, fill=T["text"])

            # 达成率：官方所有难度都是白字，Re:MASTER 也是白（深紫底上不换色）
            try:
                acc = float(ch.get("achievements") or 0)
            except (TypeError, ValueError):
                acc = 0.0
            d.text((x + 86, y + 30), f"{acc:.4f}%", font=f_acc, fill=T["text"])

            # 定数 → RA
            ds = ch.get("ds")
            ra = float(ch.get("ra") or 0)
            d.text((x + 86, y + 84), f"{ds}  →  {ra:.0f}", font=f_small, fill=T["soft"])

            # 评级（右下，和难度名同一行）：S 逐字配色，+ 白色小一号
            _draw_rank(d, x + CARD_W - 12, y + 110, _rank(acc), size=21, theme=theme)

            # FC / AP / FS 标记（评级左边）
            # 水鱼的 fc：fc=FC / fcp=FC+ / ap=AP / app=AP+
            #   **ap 是全 PERFECT（不含 PERFECT+），app 才是全 PERFECT+**，
            #   别把 ap 写成 AP+ —— 达成率没到 101% 就是 AP。
            marks = []
            fc = str(ch.get("fc") or "").lower()
            fs = str(ch.get("fs") or "").lower()
            if fc:
                marks.append({"fc": "FC", "fcp": "FC+", "ap": "AP", "app": "AP+"}.get(fc, fc.upper()))
            if fs:
                marks.append({"fs": "FS", "fsp": "FS+", "fsd": "FDX", "fdx": "FDX", "fsdp": "FDX+", "fdxp": "FDX+", "sync": "SYNC"}.get(fs, fs.upper()))
            if marks:
                d.text((x + CARD_W - 12, y + 60), " ".join(marks), font=f_small, fill=T["mark"], anchor="ra")

            # 曲目 ID（封面下方）
            d.text((x + 44, y + 104), str(_mid(ch)), font=f_id, fill=T["sub"], anchor="mm")

            # 难度名（Re:MASTER 用白色，跟紫色底区分开）
            d.text(
                (x + 86, y + 110),
                DIFF_NAME.get(di, "?"),
                font=f_diff,
                fill=dcolor,
            )

    draw_block(Y_B35, f"旧版本 Best {len(old)}", old)
    draw_block(Y_B15, "当前版本 Best 15", new)

    # ---------- 页脚
    d.line([(PAD, H - 52), (W - PAD, H - 52)], fill=T["line"], width=2)
    d.text((PAD, H - 40), f"合计 {sd_sum + dx_sum:.0f}", font=f_sub, fill=T["accent"])
    d.text((W - PAD, H - 40), "水鱼查分器 · diving-fish.com", font=f_small, fill=T["sub"], anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True, subsampling=2)
    return buf.getvalue()


async def query_to_png(
    username: str | None = None,
    qq: int | str | None = None,
    token: str = "",
    password: str = "",
    bearer: str = "",
    with_covers: bool = True,
    save: Path | None = None,
    lxns_key: str = "",
    avatar: bytes | None = None,
    theme: str = "dark",
    with_plate: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    data = await fetch(username=username, qq=qq, token=token, password=password, bearer=bearer)
    covers = {}
    if with_covers:
        old, new = pick_b50(data)
        covers = await fetch_covers([_mid(c) for c in old + new], lxns_key=lxns_key)
    plate_img = None
    if with_plate:
        pname = data.get("plate")
        if pname:
            plate_img = await fetch_plate(str(pname))
    png = render(data, covers, avatar, theme, plate_img)
    if save:
        save.write_bytes(png)
    return png, data


def demo() -> None:
    fake = {"nickname": "贝尔娜提塔", "rating": 14732, "additional_rating": 3, "plate": "熊の子", "charts": []}
    titles = [
        "Grievous Lady", "QZKago Requiem", "Heaven and Hell", "Falsus",
        "AMAZING MIGHTYYYY!!!!", "Schwarzschild", "Emperor's Return", "Glorious Crown",
        "B.B.K.K.B.K.K.", "Metamorphose", "天火明命", "HECATONCHEIR",
        "Scythe of the Reaper", "Never Ending Story", "PANDORA PARADOXXX",
        "VIIIbit Explorer", "BEELZEBUB", "Ultimate Force", "Straight into the lights", "Conflict",
    ]
    real_ids = [8, 834, 11451, 10784, 11117, 835, 11236, 11047, 11102, 11223]
    for i in range(50):
        fake["charts"].append(
            {
                "title": titles[i % len(titles)] + ("" if i < 20 else f" #{i}"),
                "song_id": real_ids[i % len(real_ids)],
                "level_index": 3 if i % 4 else 4,
                "level": "13+" if i % 3 else "14",
                "ds": 13.8 if i % 3 else 14.2,
                "achievements": 100.5 - (i * 0.13),
                "ra": 297 - i * 2.1,
                "type": "DX" if i % 2 else "SD",
                "fc": "ap" if i % 5 == 0 else ("fc" if i % 3 == 0 else ""),
                "fs": "fsp" if i % 4 == 0 else "",
                "is_new": i >= 35,
            }
        )
    covers = asyncio.run(fetch_covers([_mid(c) for c in fake["charts"]]))
    print(f"抓到 {len(covers)} 张封面")
    png = render(fake, covers)
    out = Path(__file__).with_name("b50_demo.jpg")
    out.write_bytes(png)
    print(f"已生成 {out}（{len(png)//1024} KB）")


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        who = sys.argv[1] if len(sys.argv) > 1 else None
        if not who:
            print("用法：python maimai.py <水鱼用户名>  |  python maimai.py demo")
            raise SystemExit(1)
        png, data = asyncio.run(query_to_png(username=who, save=Path(f"b50_{who}.jpg")))
        print(f"OK：{data.get('nickname')} rating={data.get('rating')} → b50_{who}.jpg")
