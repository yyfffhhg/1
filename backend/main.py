# -*- coding: utf-8 -*-
"""
电商生图 Studio —— 后端 (FastAPI)
=================================
一句话 / 一段描述 -> 成品视觉素材。覆盖 8 大电商生图场景:

  AI写真馆 / AI包装设计 / AI海报工厂 / AI家装设计
  AI餐饮视觉 / AI试衣间 / 电商一键生视频 / AI漫剧

核心流水线 (每个品类复用):
  描述文本 -> ① 英文提示词增强 (FLUX 免Key生图)
          -> ② Pollinations FLUX 生成画面
          -> ③ PIL 服务端排版引擎: 渐变遮罩 + 中文字体 + 品类版式模板
          -> 输出: 成品 PNG (base64)  /  视频品类输出 MP4/GIF

接口:
  GET  /api/meta            -> 品类 / 风格 / 尺寸 元数据
  POST /api/generate        -> {category, prompt, style?, size?, name?}
  GET  /api/health
"""

import os
import io
import json
import time
import math
import random
import base64
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
import numpy as np
import imageio.v2 as imageio
import imageio_ffmpeg
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("visual-studio")

# ---------------------------------------------------------------------------
# 尺寸预设 (输出尺寸)
# ---------------------------------------------------------------------------
SIZES = {
    "xhs":    {"id": "xhs",    "name": "小红书 3:4",  "w": 1080, "h": 1440},
    "square": {"id": "square", "name": "方图 1:1",    "w": 1080, "h": 1080},
    "gzh":    {"id": "gzh",    "name": "公众号 21:9", "w": 2100, "h": 900},
    "story":  {"id": "story",  "name": "短视频 9:16", "w": 1080, "h": 1920},
    "wide":   {"id": "wide",   "name": "横版 16:9",   "w": 1920, "h": 1080},
}

# FLUX 生成画面用比例 (接近目标比例, 减少裁切)
FLUX_SIZES = {
    "xhs":    (864, 1152),
    "square": (1024, 1024),
    "gzh":    (1344, 576),
    "story":  (720, 1280),
    "wide":   (1280, 720),
}

# ---------------------------------------------------------------------------
# 风格预设 (统一作用于 FLUX 调色 + PIL 强调色)
# ---------------------------------------------------------------------------
STYLES = {
    "gradient": {"name": "现代渐变", "dark": True,
                 "prompt": "vibrant gradient color grade, neon glow, dreamy premium aesthetic, soft bokeh",
                 "accent": (124, 123, 255), "accent2": (255, 107, 157)},
    "minimal":  {"name": "极简商务", "dark": False,
                 "prompt": "minimal bright pastel color grade, clean soft light, luxury editorial, lots of negative space",
                 "accent": (24, 26, 48), "accent2": (130, 132, 150)},
    "retro":    {"name": "国潮撞色", "dark": False,
                 "prompt": "retro film tone, warm red and imperial-blue contrast, vintage chinese poster vibe, grainy",
                 "accent": (214, 58, 47), "accent2": (29, 61, 143)},
    "nature":   {"name": "清新自然", "dark": False,
                 "prompt": "soft natural daylight, fresh green and cream tones, healing lifestyle, airy",
                 "accent": (62, 122, 79), "accent2": (168, 213, 184)},
    "cyber":    {"name": "暗黑科技", "dark": True,
                 "prompt": "cyberpunk neon lighting, dark background, futuristic grid, teal cyan glow, volumetric",
                 "accent": (0, 255, 200), "accent2": (0, 200, 255)},
}

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"
FONT_LIGHT = "C:/Windows/Fonts/msyhl.ttc"


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def text_w(draw: ImageDraw.ImageDraw, text: str, f) -> float:
    b = draw.textbbox((0, 0), text, font=f)
    return b[2] - b[0]


def fit_size(draw, text: str, base: int, max_w: float, min_s: int = 24) -> int:
    if not text:
        return base
    f = font(base)
    w = text_w(draw, text, f)
    if w <= max_w:
        return base
    return max(min_s, int(base * max_w / w))


def wrap(draw, text: str, f, max_w: float) -> list:
    """按字符贪婪换行 (适配中文)。"""
    lines, cur = [], ""
    for ch in text:
        if text_w(draw, cur + ch, f) <= max_w:
            cur += ch
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines or [""]


def pill(draw, cx, cy, text, f, fill, pad_x, pad_y=None):
    pad_y = pad_y or pad_x
    b = draw.textbbox((0, 0), text, font=f)
    tw, th = b[2] - b[0], b[3] - b[1]
    x0, x1 = cx - tw / 2 - pad_x, cx + tw / 2 + pad_x
    y0, y1 = cy - th / 2 - pad_y, cy + th / 2 + pad_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=(y1 - y0) / 2, fill=fill)
    draw.text((cx - tw / 2 - b[0], cy - th / 2 - b[1]), text, font=f, fill=(255, 255, 255))


def add_gradient(img: Image.Image, top: int = 0, bottom: int = 0,
                 top_stop: float = 0.28, bottom_stop: float = 0.62) -> Image.Image:
    """叠加顶部 / 底部黑色渐变遮罩, 保证文字可读。"""
    W, H = img.size
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    if bottom > 0:
        start = int(H * (1 - bottom_stop))
        for i in range(start, H):
            a = int(bottom * (i - start) / (H - start))
            md.line([(0, i), (W, i)], fill=min(255, a))
    if top > 0:
        for i in range(int(H * top_stop)):
            a = int(top * (1 - i / (H * top_stop)))
            md.line([(0, i), (W, i)], fill=min(255, a))
    black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    return Image.composite(black, img, mask)


def prep(art_bytes: bytes, W: int, H: int) -> Image.Image:
    art = Image.open(io.BytesIO(art_bytes)).convert("RGB")
    aw, ah = art.size
    tr, cr = W / H, aw / ah
    if cr > tr:
        nw = int(ah * tr); x = (aw - nw) // 2
        art = art.crop((x, 0, x + nw, ah))
    else:
        nh = int(aw / tr); y = (ah - nh) // 2
        art = art.crop((0, y, aw, y + nh))
    return art.resize((W, H), Image.LANCZOS).convert("RGBA")


def ink_for(dark: bool):
    """返回 (主文字, 次文字, 弱文字) 四元组 (含 alpha)。"""
    if dark:
        return (255, 255, 255, 255), (236, 239, 247, 255), (200, 206, 220, 255)
    return (22, 24, 46, 255), (60, 64, 92, 255), (110, 116, 140, 255)


# 叠加暗色渐变时, 文字统一用白色系
LIGHT_INK = ((255, 255, 255, 255), (232, 236, 247, 255), (180, 188, 210, 255))


def short(text: str, n: int = 12) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# FLUX 画面生成 (免 Key)
# ---------------------------------------------------------------------------
async def gen_artwork(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    w, h = FLUX_SIZES[size_id]
    url = (
        "https://image.pollinations.ai/prompt/" + quote(prompt)
        + f"?width={w}&height={h}&model=flux&nologo=true&seed={seed or random.randint(1, 999999)}"
    )
    try:
        async with httpx.AsyncClient(timeout=180.0) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and r.content:
                logger.info(f"FLUX ok size={len(r.content)} prompt_len={len(prompt)}")
                return r.content
            logger.error(f"FLUX HTTP {r.status_code}: {r.text[:160]}")
    except Exception as e:
        logger.error(f"FLUX 异常: {e}")
    return None


# ---------------------------------------------------------------------------
# 本地文案引擎 (兜底, 免 Key)
# ---------------------------------------------------------------------------
INDUSTRY = {
    "美妆": ["妆点", "焕颜", "水润", "高级感"], "护肤": ["水润", "焕活", "元气", "透亮"],
    "食品": ["美味", "鲜香", "真材实料", "匠心"], "零食": ["解馋", "酥脆", "停不下来", "上瘾"],
    "茶": ["醇香", "回甘", "自然", "慢生活"], "咖啡": ["香醇", "提神", "现磨", "精品"],
    "科技": ["智能", "高效", "领先", "未来感"], "AI": ["智能", "效率翻倍", "解放双手", "前沿"],
    "教育": ["高效学习", "名师", "提分", "系统"], "健身": ["燃脂", "塑形", "活力", "突破"],
    "家居": ["舒适", "质感", "温馨", "美好"], "家具": ["质感", "舒适", "耐看", "高级"],
    "服装": ["时尚", "百搭", "显瘦", "潮流"], "女装": ["显瘦", "气质", "百搭", "高级"],
    "电商": ["爆款", "热卖", "限时", "品质"], "旅游": ["说走就走", "美景", "惬意", "自由"],
    "健康": ["活力", "养护", "自然", "元气"], "宠物": ["陪伴", "快乐", "健康", "贴心"],
    "餐饮": ["地道", "现做", "烟火气", "食欲"], "母婴": ["安心", "温和", "亲肤", "守护"],
}

# 让 FLUX 更懂中文产品描述的英文提示词增强 (轻量关键词映射)
PRODUCT_HINTS = {
    "茶": "bottle of herbal tea drink beverage", "咖啡": "cup of specialty coffee",
    "饮料": "beverage drink can", "奶茶": "bubble milk tea drink cup",
    "零食": "snack food package", "坚果": "gourmet nuts gift box",
    "礼盒": "premium gift box packaging", "口红": "lipstick makeup product",
    "护肤": "skincare cosmetic product", "美妆": "makeup cosmetic product",
    "耳机": "wireless earphones product", "手机": "smartphone product",
    "家居": "home interior furniture", "家具": "furniture product",
    "服装": "fashion apparel clothing", "鞋": "shoes footwear product",
    "美食": "gourmet food dish", "菜": "delicious dish food",
    "饭": "rice bowl dish", "面": "noodle bowl dish",
    "蛋糕": "cake dessert", "巧克力": "chocolate product",
    "宠物": "pet product", "玩具": "toy product",
}


def product_hint(prompt: str) -> str:
    for kw, en in PRODUCT_HINTS.items():
        if kw.lower() in prompt.lower():
            return f" ({en})"
    return ""


def gen_copy(topic: str) -> dict:
    topic = (topic or "电商生图").strip()
    words = []
    for kw, tags in INDUSTRY.items():
        if kw.lower() in topic.lower():
            words.extend(tags)
    if not words:
        words = ["专业", "优质", "信赖之选", "全新体验", "值得拥有"]
    return {
        "demo": True,
        "tag": "AI 智能生成",
        "title": f"{short(topic, 10)} 全新上线",
        "subtitle": f"{words[0]} · {words[1]} 只为更好的你",
        "points": [f"{words[2]}体验", f"{words[3]}保障", f"{words[0]}之选"],
        "cta": "立即体验",
    }


# ===========================================================================
# 品类合成器
# ===========================================================================
def _style_mod(style_id: str) -> str:
    return STYLES[style_id]["prompt"]


# ---- 1. AI 海报工厂 -------------------------------------------------------
def compose_poster(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]
    W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = add_gradient(prep(art, W, H), top=150, bottom=210)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = LIGHT_INK
    k = W / 1080.0

    if size_id == "gzh":
        ts, ss, ps, cs, gs = int(96 * k), int(38 * k), int(30 * k), int(34 * k), int(20 * k)
    elif size_id == "xhs":
        ts, ss, ps, cs, gs = int(86 * k), int(34 * k), int(28 * k), int(32 * k), int(18 * k)
    else:
        ts, ss, ps, cs, gs = int(78 * k), int(32 * k), int(27 * k), int(30 * k), int(17 * k)

    copy = gen_copy(prompt)
    title = (name or copy["title"])
    rts = fit_size(draw, title, ts, W * 0.88, int(36 * k))
    sub = copy["subtitle"]
    rss = fit_size(draw, sub, ss, W * 0.86, int(20 * k)) if sub else ss
    f_tag, f_title = font(gs), font(rts)
    f_sub, f_point, f_cta = font(rss, False), font(ps, False), font(cs)

    tag = name and "AI 海报" or copy["tag"]
    tb = draw.textbbox((0, 0), tag, font=f_tag); tw, th = tb[2] - tb[0], tb[3] - tb[1]
    ty = int(H * 0.07); pad = int(14 * k)
    pill(draw, W / 2, ty + th / 2 + pad / 2, tag, f_tag, accent + (255,), pad)

    def cx(text, f, y, fill):
        b = draw.textbbox((0, 0), text, font=f); x = (W - (b[2] - b[0])) / 2 - b[0]
        draw.text((x, y), text, font=f, fill=fill)

    cx(title, f_title, int(H * 0.34), white)
    if sub:
        cx(sub, f_sub, int(H * 0.34) + rts + int(14 * k), soft)

    points = copy["points"]
    if size_id == "gzh":
        n = len(points[:3]); gap = W / (n + 1); py = int(H * 0.62)
        for i, p in enumerate(points[:3]):
            c = gap * (i + 1); r = int(16 * k)
            draw.ellipse([c - r, py, c + r, py + 2 * r], fill=accent + (255,))
            draw.text((c - r / 2 - 3, py + r / 2 - 6), str(i + 1), font=font(int(13 * k)), fill=white)
            pb = draw.textbbox((0, 0), p, font=f_point)
            draw.text((c - (pb[2] - pb[0]) / 2 - pb[0], py + 2 * r + int(8 * k)), p, font=f_point, fill=faint)
    else:
        start_y = int(H * 0.64); step = int(H * 0.072)
        for i, p in enumerate(points[:3]):
            y = start_y + i * step; r = int(15 * k); c = W / 2 - int(95 * k)
            draw.ellipse([c - r, y - r, c + r, y + r], fill=accent + (255,))
            draw.text((c - 5, y - int(9 * k)), str(i + 1), font=font(int(12 * k)), fill=white)
            pb = draw.textbbox((0, 0), p, font=f_point)
            draw.text((W / 2 - (pb[2] - pb[0]) / 2, y - pb[3] / 2), p, font=f_point, fill=faint)

    cta = copy["cta"]; cb = draw.textbbox((0, 0), cta, font=f_cta); ctw, cth = cb[2] - cb[0], cb[3] - cb[1]
    cy = H - int(92 * k) if size_id != "gzh" else H - int(70 * k); cpad = int(26 * k)
    draw.rounded_rectangle([W / 2 - ctw / 2 - cpad, cy, W / 2 + ctw / 2 + cpad, cy + cth + cpad],
                           radius=(cth + cpad) / 2, fill=accent + (255,))
    draw.text((W / 2 - ctw / 2 - cb[0], cy - cb[1] + cpad / 2), cta, font=f_cta, fill=white)

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 2. AI 写真馆 ---------------------------------------------------------
def compose_portrait(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = add_gradient(prep(art, W, H), bottom=200, bottom_stop=0.55)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = LIGHT_INK
    k = W / 1080.0

    # 细边框
    bw = int(10 * k)
    draw.rectangle([bw, bw, W - bw, H - bw], outline=accent + (220,), width=bw)

    # 顶部左上角标
    f_tag = font(int(26 * k))
    pill(draw, int(120 * k), int(70 * k), "AI 写真馆", f_tag, accent + (255,), int(20 * k))

    # 姓名 / 标题
    title = name or short(prompt, 10)
    f_name = font(fit_size(draw, title, int(72 * k), W * 0.7, int(34 * k)))
    b = draw.textbbox((0, 0), title, font=f_name); x = (W - (b[2] - b[0])) / 2 - b[0]
    draw.text((x, H - int(220 * k)), title, font=f_name, fill=white)
    # 副标题 / 风格
    sub = f"{lay['name']} · 高定写真"
    f_sub = font(int(30 * k), False)
    sb = draw.textbbox((0, 0), sub, font=f_sub); sx = (W - (sb[2] - sb[0])) / 2 - sb[0]
    draw.text((sx, H - int(120 * k)), sub, font=f_sub, fill=soft)
    # 签名水印
    f_sig = font(int(22 * k), False)
    draw.text((W - int(240 * k), H - int(70 * k)), "AI写真馆 · generated", font=f_sig, fill=faint)

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 3. AI 包装设计 -------------------------------------------------------
def compose_packaging(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = prep(art, W, H)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = ink_for(lay["dark"])
    k = W / 1080.0

    # 底部包装标签面板
    panel_h = int(H * 0.40)
    panel = Image.new("RGBA", (W, panel_h), (255, 255, 255, 244) if not lay["dark"] else (16, 18, 34, 240))
    img.paste(panel, (0, H - panel_h), panel)
    # 顶部分隔线 + 强调色条
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, H - panel_h, W, H - panel_h + int(10 * k)], fill=accent + (255,))

    brand = name or "BRAND"
    product = short(prompt, 12)
    tagline = gen_copy(prompt)["subtitle"]

    f_brand = font(int(34 * k))
    bb = draw.textbbox((0, 0), brand, font=f_brand)
    draw.text((int(60 * k) - bb[0], H - panel_h + int(50 * k)), brand, font=f_brand, fill=accent + (255,))
    # 产品名
    f_prod = font(fit_size(draw, product, int(64 * k), W - int(120 * k), int(32 * k)))
    pb = draw.textbbox((0, 0), product, font=f_prod)
    draw.text((int(60 * k) - pb[0], H - panel_h + int(110 * k)), product, font=f_prod, fill=(22, 24, 46, 255) if not lay["dark"] else white)
    # 标语
    f_tl = font(int(28 * k), False)
    tb = draw.textbbox((0, 0), tagline, font=f_tl)
    draw.text((int(62 * k) - tb[0], H - panel_h + int(110 * k) + int(74 * k)), tagline, font=f_tl, fill=(90, 94, 120, 255) if not lay["dark"] else faint)

    # 模拟条形码装饰
    bx0 = W - int(260 * k); by = H - int(80 * k); bh = int(70 * k)
    random.seed(hash(prompt) & 0xffff)
    x = bx0
    while x < W - int(60 * k):
        wbar = random.choice([3, 5, 2, 6]) * k
        draw.rectangle([x, by, x + wbar, by + bh], fill=(20, 20, 20, 255) if not lay["dark"] else (220, 220, 220, 255))
        x += wbar + 3 * k

    # 顶部品类角标
    pill(draw, int(120 * k), int(60 * k), "AI 包装设计", font(int(24 * k)), accent + (255,), int(18 * k))

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 4. AI 家装设计 -------------------------------------------------------
def compose_home(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = add_gradient(prep(art, W, H), bottom=190)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = LIGHT_INK
    k = W / 1080.0

    room = name or "空间设计"
    pill(draw, int(150 * k), int(64 * k), room, font(int(30 * k)), accent + (255,), int(22 * k))

    notes = [f"{lay['name']}风格", "自然采光通透", "材质质感优先"]
    f_n = font(int(30 * k), False)
    y = H - int(230 * k)
    for i, n in enumerate(notes):
        yy = y + i * int(56 * k)
        draw.ellipse([int(60 * k), yy, int(60 * k) + int(16 * k), yy + int(16 * k)], fill=accent + (255,))
        nb = draw.textbbox((0, 0), n, font=f_n)
        draw.text((int(94 * k), yy - int(4 * k)), n, font=f_n, fill=soft)

    # 底部标题
    f_t = font(fit_size(draw, short(prompt, 14), int(58 * k), W * 0.7, int(30 * k)))
    tb = draw.textbbox((0, 0), short(prompt, 14), font=f_t)
    draw.text((int(60 * k) - tb[0], H - int(70 * k)), short(prompt, 14), font=f_t, fill=white)

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 5. AI 餐饮视觉 -------------------------------------------------------
def compose_food(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = add_gradient(prep(art, W, H), bottom=190, top=120)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = LIGHT_INK
    k = W / 1080.0

    dish = name or short(prompt, 10)
    f_d = font(fit_size(draw, dish, int(70 * k), W * 0.66, int(34 * k)))
    db = draw.textbbox((0, 0), dish, font=f_d)
    draw.text((int(60 * k) - db[0], H - int(220 * k)), dish, font=f_d, fill=white)
    rest = "AI 餐饮视觉 · 限量出品"
    f_r = font(int(28 * k), False)
    rb = draw.textbbox((0, 0), rest, font=f_r)
    draw.text((int(62 * k) - rb[0], H - int(120 * k)), rest, font=f_r, fill=soft)

    # 价格徽章 (右上圆)
    price = "¥" + str(random.choice([19, 29, 39, 49, 59, 88, 128]))
    f_p = font(int(40 * k))
    pr = int(70 * k); pcx, pcy = W - int(120 * k), int(130 * k)
    draw.ellipse([pcx - pr, pcy - pr, pcx + pr, pcy + pr], fill=accent + (255,))
    pb = draw.textbbox((0, 0), price, font=f_p)
    draw.text((pcx - (pb[2] - pb[0]) / 2 - pb[0], pcy - (pb[3] - pb[1]) / 2 - pb[1]), price, font=f_p, fill=white)

    # 顶部角标
    pill(draw, int(140 * k), int(60 * k), "AI 餐饮视觉", font(int(24 * k)), (235, 80, 60, 255), int(18 * k))

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 6. AI 试衣间 ---------------------------------------------------------
def compose_fitting(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = add_gradient(prep(art, W, H), bottom=210)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = LIGHT_INK
    k = W / 1080.0

    pill(draw, int(140 * k), int(60 * k), "AI 试衣间", font(int(26 * k)), accent + (255,), int(18 * k))

    title = name or short(prompt, 12)
    f_t = font(fit_size(draw, title, int(60 * k), W * 0.7, int(30 * k)))
    tb = draw.textbbox((0, 0), title, font=f_t)
    draw.text((int(60 * k) - tb[0], H - int(230 * k)), title, font=f_t, fill=white)

    # 尺码徽章 S M L
    sizes = ["S", "M", "L"]
    f_s = font(int(30 * k))
    bx = int(64 * k); by = H - int(140 * k)
    for i, s in enumerate(sizes):
        cx = bx + i * int(96 * k)
        draw.rounded_rectangle([cx, by, cx + int(72 * k), by + int(72 * k)], radius=int(14 * k),
                               outline=accent + (255,), width=int(4 * k))
        sb = draw.textbbox((0, 0), s, font=f_s)
        draw.text((cx + int(36 * k) - (sb[2] - sb[0]) / 2 - sb[0], by + int(18 * k)), s, font=f_s, fill=soft)

    # 价格
    price = "¥" + str(random.choice([99, 159, 199, 259, 329, 499]))
    f_p = font(int(34 * k))
    pb = draw.textbbox((0, 0), price, font=f_p)
    draw.text((W - int(120 * k) - pb[2], by + int(14 * k)), price, font=f_p, fill=accent + (255,))

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 7. AI 漫剧 -----------------------------------------------------------
def compose_comic(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = prep(art, W, H)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = ink_for(lay["dark"])
    k = W / 1080.0

    # 漫画面板粗黑边框
    bw = int(16 * k)
    draw.rectangle([bw, bw, W - bw, H - bw], outline=(10, 10, 14, 255), width=bw)
    draw.rectangle([bw * 2, bw * 2, W - bw * 2, H - bw * 2], outline=(10, 10, 14, 255), width=int(4 * k))

    # 顶部场景角标
    pill(draw, int(150 * k), int(64 * k), name or "第 01 幕", font(int(26 * k)), accent + (255,), int(18 * k))

    # 底部对白框
    cap_h = int(H * 0.16)
    cap = Image.new("RGBA", (W - int(80 * k), cap_h), (12, 12, 18, 240))
    img.paste(cap, (int(40 * k), H - cap_h - int(40 * k)), cap)
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(40 * k), H - cap_h - int(40 * k), W - int(40 * k), H - int(40 * k)], outline=accent + (255,), width=int(4 * k))
    caption = short(prompt, 26)
    f_c = font(int(34 * k), False)
    lines = wrap(draw, caption, f_c, W - int(140 * k))
    yy = H - cap_h - int(40 * k) + int(28 * k)
    for ln in lines[:3]:
        lb = draw.textbbox((0, 0), ln, font=f_c)
        draw.text((int(70 * k) - lb[0], yy), ln, font=f_c, fill=(240, 240, 248, 255))
        yy += int(46 * k)

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


# ---- 8. 电商一键生视频 -----------------------------------------------------
def kenburns(img: Image.Image, n: int, zoom: float = 0.12, pan: float = 0.04) -> list:
    W, H = img.size
    frames = []
    for i in range(n):
        t = i / max(1, n - 1)
        z = 1 + zoom * t
        nw, nh = int(W / z), int(H / z)
        dx = int((W - nw) * pan * t)
        dy = int((H - nh) * pan * t)
        f = img.crop((dx, dy, dx + nw, dy + nh)).resize((W, H), Image.LANCZOS)
        frames.append(f.convert("RGB"))
    return frames


def build_video(frames: list, out_path: str) -> str:
    """优先 MP4 (libx264), 失败回退 GIF。返回 'mp4' / 'gif'。"""
    try:
        writer = imageio.get_writer(out_path + ".mp4", fps=14, codec="libx264", quality=7, macro_block_size=1)
        for f in frames:
            writer.append_data(np.array(f))
        writer.close()
        return "mp4"
    except Exception as e:
        logger.warning(f"MP4 编码失败, 回退 GIF: {e}")
        try:
            gif_path = out_path + ".gif"
            imageio.mimsave(gif_path, [np.array(f) for f in frames], fps=14, loop=0)
            return "gif"
        except Exception as e2:
            logger.error(f"GIF 也失败: {e2}")
            return "err"


async def compose_video(prompt: str, name: str, style_id: str, size_id: str) -> dict:
    """生成多镜头 -> Ken Burns 运镜 -> 视频。"""
    lay = STYLES[style_id]
    mod = _style_mod(style_id)
    scenes = [
        f"cinematic e-commerce hero shot of {prompt}, dramatic lighting, {mod}, premium product render, no text",
        f"close-up detail macro of {prompt}, texture focus, {mod}, studio, no text",
        f"lifestyle scene featuring {prompt} in real environment, {mod}, warm mood, no text",
        f"packaging and presentation of {prompt}, flat lay, {mod}, clean, no text",
    ]
    W, H = SIZES[size_id]["w"], SIZES[size_id]["h"]
    frames = []
    for i, sp in enumerate(scenes):
        art = await gen_artwork(sp, size_id)
        if art is None:
            raise RuntimeError(f"视频第 {i+1} 镜生成失败")
        img = prep(art, W, H)
        frames.extend(kenburns(img, 10, zoom=0.10 + 0.02 * i, pan=0.05))
    tmp = str(LOG_DIR / f"video_{int(time.time())}")
    kind = build_video(frames, tmp)
    if kind == "err":
        raise RuntimeError("视频编码失败")
    with open(tmp + "." + kind, "rb") as f:
        data = f.read()
    os.remove(tmp + "." + kind)
    return {"media_type": kind, "data_b64": base64.b64encode(data).decode(),
            "scenes": len(scenes), "frames": len(frames)}


# ---------------------------------------------------------------------------
# 品类注册表
# ---------------------------------------------------------------------------
CATEGORIES = {
    "poster":   {"name": "AI海报工厂", "icon": "🖼️", "desc": "一句话生成营销海报",
                 "sizes": ["xhs", "square", "gzh"], "allow_face": False,
                 "prompt": lambda p, s: (f"Professional product photography of {p}{product_hint(p)}. "
                                         f"A single product is the absolute main subject in the center, on a clean pedestal or surface, "
                                         f"{_style_mod(s)}, elegant marketing background, leave space for text overlay, "
                                         f"no human, no model, no woman, no man, no face, no hands, no body, no people, "
                                         f"no text, no words, no letters, no watermark, 8k, detailed")},
    "portrait": {"name": "AI写真馆", "icon": "📸", "desc": "高定风格人像写真",
                 "sizes": ["xhs", "square", "story"], "allow_face": True,
                 "prompt": lambda p, s: (f"Professional portrait photography of {p}, studio lighting, sharp focus on face, "
                                         f"{_style_mod(s)}, high end fashion editorial, 8k, highly detailed, no text, no watermark")},
    "packaging":{"name": "AI包装设计", "icon": "📦", "desc": "产品包装 / 礼盒效果图",
                 "sizes": ["square", "xhs"], "allow_face": False,
                 "prompt": lambda p, s: (f"Product packaging design mockup of {p}{product_hint(p)}, on clean white studio background, "
                                         f"professional product photography, centered, {_style_mod(s)}, "
                                         f"blank packaging surface without printed text, "
                                         f"no text no words, high quality, 8k, detailed")},
    "home":     {"name": "AI家装设计", "icon": "🛋️", "desc": "室内空间效果图",
                 "sizes": ["wide", "square", "xhs"], "allow_face": False,
                 "prompt": lambda p, s: (f"Interior design photography of a {p} room, {_style_mod(s)}, "
                                         f"professional architecture photography, bright, no people, no text, 8k, detailed")},
    "food":     {"name": "AI餐饮视觉", "icon": "🍜", "desc": "美食 / 菜品视觉",
                 "sizes": ["square", "xhs"], "allow_face": False,
                 "prompt": lambda p, s: (f"Professional food photography of {p}{product_hint(p)}, on a restaurant table, 45 degree angle, "
                                         f"appetizing, {_style_mod(s)}, no text, 8k, detailed")},
    "fitting":  {"name": "AI试衣间", "icon": "👗", "desc": "模特上身 / 穿搭效果",
                 "sizes": ["xhs", "story", "square"], "allow_face": True,
                 "prompt": lambda p, s: (f"Full body fashion photography of a model wearing {p}, studio background, "
                                         f"{_style_mod(s)}, high fashion, no face obstruction, no text, 8k, detailed")},
    "comic":    {"name": "AI漫剧", "icon": "🎬", "desc": "漫画分镜 / 同款二创",
                 "sizes": ["xhs", "square", "story"], "allow_face": False,
                 "prompt": lambda p, s: (f"Comic book panel, cinematic illustration of {p}, manhua art style, "
                                         f"{_style_mod(s)}, dramatic lighting, no text, high detail, 8k")},
    "video":    {"name": "电商一键生视频", "icon": "🎞️", "desc": "多镜头运镜短视频",
                 "sizes": ["story", "wide"], "allow_face": False, "video": True},
}

COMPOSERS = {
    "poster": compose_poster, "portrait": compose_portrait, "packaging": compose_packaging,
    "home": compose_home, "food": compose_food, "fitting": compose_fitting, "comic": compose_comic,
}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="电商生图 Studio", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


class GenRequest(BaseModel):
    category: str
    prompt: str
    style: str = "gradient"
    size: str = "xhs"
    name: str = ""


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    f = FRONTEND_DIR / "favicon.ico"
    if f.exists():
        return FileResponse(f)
    return JSONResponse({"error": "Not Found"}, status_code=404)


@app.get("/api/health")
async def health():
    return {"ok": True, "categories": list(CATEGORIES.keys())}


@app.get("/api/meta")
async def meta():
    return {
        "categories": [
            {"id": k, "name": v["name"], "icon": v["icon"], "desc": v["desc"],
             "sizes": v["sizes"], "video": v.get("video", False),
             "allow_face": v.get("allow_face", False)}
            for k, v in CATEGORIES.items()
        ],
        "styles": [{"id": k, "name": v["name"], "dark": v["dark"]} for k, v in STYLES.items()],
        "sizes": [{"id": k, **{kk: vv for kk, vv in v.items() if kk != "id"}} for k, v in SIZES.items()],
    }


@app.post("/api/generate")
async def generate(req: GenRequest):
    cat = req.category
    if cat not in CATEGORIES:
        return JSONResponse({"error": f"未知品类 {cat}"}, status_code=422)
    if req.style not in STYLES:
        return JSONResponse({"error": f"未知风格 {req.style}"}, status_code=422)
    c = CATEGORIES[cat]
    if req.size not in c["sizes"]:
        return JSONResponse({"error": f"品类 {cat} 不支持尺寸 {req.size}"}, status_code=422)
    prompt = req.prompt.strip()
    if not prompt:
        return JSONResponse({"error": "描述不能为空"}, status_code=422)

    # 视频品类
    if c.get("video"):
        try:
            vid = await compose_video(prompt, req.name, req.style, req.size)
        except Exception as e:
            logger.error(f"视频生成失败: {e}")
            return JSONResponse({"error": f"视频生成失败: {e}"}, status_code=502)
        return {
            "category": cat, "style": req.style, "size": req.size,
            "media_type": vid["media_type"], "media_b64": vid["data_b64"],
            "scenes": vid["scenes"], "frames": vid["frames"],
        }

    # 图像品类
    flx_prompt = c["prompt"](prompt, req.style)
    art = await gen_artwork(flx_prompt, req.size)
    if art is None:
        return JSONResponse({"error": "AI 画面生成失败，请检查网络 (见 logs/app.log)"}, status_code=502)
    try:
        png = COMPOSERS[cat](art, prompt, req.name.strip(), req.style, req.size)
    except Exception as e:
        logger.error(f"合成失败: {e}")
        return JSONResponse({"error": f"合成失败: {e}"}, status_code=500)

    return {
        "category": cat, "style": req.style, "size": req.size,
        "image_b64": base64.b64encode(png).decode(),
        "provider": "FLUX+PIL",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
