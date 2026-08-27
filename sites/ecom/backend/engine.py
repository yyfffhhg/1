# -*- coding: utf-8 -*-
"""
电商生图 · 共享引擎内核 (engine.py)
=================================
被 9 个独立站点 (ecom / portrait / packaging / poster / home / food /
fitting / video / comic) 复用。每个站点 backend/engine.py 是一份副本, 保证
站点可独立部署。

流水线: 描述 -> ① 英文提示词增强 -> ② 画面生成 (默认 Pollinations FLUX 免Key,
       也可配 SF_API_KEY / GEMINI_API_KEY / CF_* / DOUBAO_API_KEY 走
       硅基流动 / Google Gemini / Cloudflare / 豆包, 全部失败自动降级 FLUX)
       -> ③ PIL 服务端中文排版 -> 成品 PNG / MP4
视频: VIDEO_PROVIDER=dashscope + DASHSCOPE_API_KEY 走阿里通义万相真实文生视频
       (多镜头工作流: 拆分镜各生成短视频 -> ffmpeg 拼接成长片);
       不填 Key 自动回退 static_frames 兜底(纯静帧分镜, 免Key, 无任何运镜).

对外导出:
  SIZES, STYLES, CATEGORIES, COMPOSERS, compose_video
  build_app(category, frontend_dir) -> FastAPI 单品类应用
"""

import os
import io
import time
import json
import uuid
import math
import random
import base64
import asyncio
import logging
import datetime
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
import numpy as np
import imageio.v2 as imageio
import imageio_ffmpeg
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("visual-engine")

# ---------------------------------------------------------------------------
# 配置 (从 backend/.env 读取, 不入库). 默认走免费 Pollinations, 配了 Key 自动切换
# ---------------------------------------------------------------------------
def _load_dotenv():
    p = BASE_DIR / ".env"
    if p.exists():
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        except Exception as e:
            logger.warning(f"读取 .env 失败: {e}")

_load_dotenv()

IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "pollinations").lower()
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY", "")
DOUBAO_BASE_URL = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "doubao-seedream-4.5")

# Google Gemini (AI Studio 免费档, 不绑卡, 约 500 张/天, 免费里质量最高)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-image")

# Cloudflare Workers AI (免费档 10000 neurons/天 ≈ 230 张 FLUX.1 schnell, 不绑卡)
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_MODEL = os.getenv("CF_MODEL", "@cf/black-forest-labs/flux-1-schnell")

# 硅基流动 SiliconFlow (国内可达, 注册送免费额度, FLUX.1-schnell / KWAI-Kolors 免费生图)
SF_API_KEY = os.getenv("SF_API_KEY", "")
SF_BASE_URL = os.getenv("SF_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
# 默认模型 (部分账号 FLUX.1-schnell 被禁用, 用 KWAI-Kolors/Kolors 兜底; 可在控制台开通后改回)
SF_MODEL = os.getenv("SF_MODEL", "KWAI-Kolors/Kolors")
SF_FALLBACK_MODELS = ["KWAI-Kolors/Kolors", "black-forest-labs/FLUX.1-schnell"]

# 真实视频 AI 后端 (需对应平台免费 Key, 注册送额度不花钱). 不填 Key 自动回退 Ken Burns 伪视频.
# 阿里通义万相 (实测可达, 但部分账号免费额度已收紧/不可用)
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
DASHSCOPE_T2V_MODEL = os.getenv("DASHSCOPE_T2V_MODEL", "wanx2.1-t2v-video-synthesis")
DASHSCOPE_I2V_MODEL = os.getenv("DASHSCOPE_I2V_MODEL", "wanx2.1-i2v-video-synthesis")
# 智谱 CogVideoX / 清影 (实测可达, 有免费额度, cogvideox-flash 免费模型)
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_BASE_URL = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
ZHIPU_MODEL = os.getenv("ZHIPU_MODEL", "cogvideox-flash")
# 视频后端: kenburns(默认免Key纯静帧分镜兜底, 无运镜) | dashscope(阿里万相) | zhipu(智谱清影)
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "kenburns")
# 多镜头工作流: 把描述拆成 N 个分镜各生成短视频, 再用 ffmpeg 拼接成更长的成片
VIDEO_SCENES = int(os.getenv("VIDEO_SCENES", "3"))
VIDEO_CLIP_DURATION = int(os.getenv("VIDEO_CLIP_DURATION", "4"))  # 真实 AI 单分镜时长(秒)
# Ken Burns 兜底长片参数 (免 Key): 多镜头静态分镜拼成约 VIDEO_TOTAL_SECONDS 秒成片
VIDEO_TOTAL_SECONDS = int(os.getenv("VIDEO_TOTAL_SECONDS", "10"))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "14"))

# ---- 可灵 Kling (快手开放平台, 注册送额度/付费) ----
KLING_API_KEY = os.getenv("KLING_API_KEY", "")
KLING_BASE_URL = os.getenv("KLING_BASE_URL", "https://api.klingai.com/v1").rstrip("/")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v1-6")
# ---- 海螺 Minimax (注册送额度/付费) ----
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1").rstrip("/")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "video-01")
# ---- 腾讯混元 Hunyuan 视频 (注册送额度/付费) ----
HUNYUAN_API_KEY = os.getenv("HUNYUAN_API_KEY", "")
HUNYUAN_BASE_URL = os.getenv("HUNYUAN_BASE_URL", "https://api.hunyuan.cloud.tencent.com/v1").rstrip("/")
HUNYUAN_VIDEO_MODEL = os.getenv("HUNYUAN_VIDEO_MODEL", "hunyuan-video")
# ---- Pollinations 视频 (需 Key, 免费档可用) ----
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai/v1").rstrip("/")
# ---- 火山引擎 Seedance 视频 (Volcengine Ark, 注册/付费, 真实文生视频) ----
VOLC_API_KEY = os.getenv("VOLC_API_KEY", "")
VOLC_BASE_URL = os.getenv("VOLC_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
VOLC_MODEL = os.getenv("VOLC_MODEL", "doubao-seedance-1-0-pro-fast-251015")
# 单个视频模型最长尝试秒数, 超时即切换下一个模型或静态兜底 (防止限流模型卡死整条链路)
VIDEO_PROVIDER_TIMEOUT = int(os.getenv("VIDEO_PROVIDER_TIMEOUT", "180"))

# 豆包 Seedream 允许的常用尺寸 (按比例对齐 FLUX_SIZES)
DOUBAO_SIZES = {
    "xhs": (864, 1152), "square": (1024, 1024), "gzh": (1512, 648),
    "story": (720, 1280), "wide": (1280, 720),
}

# 硅基流动 FLUX.1-schnell 支持的尺寸 (按比例对齐 FLUX_SIZES)
SF_SIZES = {
    "xhs": (864, 1152), "square": (1024, 1024), "gzh": (1536, 640),
    "story": (720, 1280), "wide": (1280, 720),
}

# 各 Provider 是否可用 (按当前环境变量判定)
def _prov_available(name: str) -> bool:
    if name in ("pollinations", "flux"):
        return True
    if name == "doubao":
        return bool(DOUBAO_API_KEY)
    if name == "gemini":
        return bool(GEMINI_API_KEY)
    if name == "cloudflare":
        return bool(CF_ACCOUNT_ID and CF_API_TOKEN)
    if name == "siliconflow":
        return bool(SF_API_KEY)
    return False

# 记录最近一次实际使用的生图源 (供前端展示)
LAST_PROVIDER = "FLUX"

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

# 扩展风格库 (品类专属风格从这里挑)。新风格追加如下, 不影响已有渲染。
STYLES.update({
    "film":     {"name": "胶片质感", "dark": False,
                 "prompt": "warm film grain, kodak portra tones, soft halation, nostalgic analog photography, gentle vignette",
                 "accent": (193, 145, 92), "accent2": (150, 110, 70)},
    "morandi":  {"name": "莫兰迪灰", "dark": False,
                 "prompt": "muted morandi color palette, soft desaturated tones, elegant matte finish, gentle shadow, premium editorial",
                 "accent": (148, 142, 138), "accent2": (176, 166, 158)},
    "luxury":   {"name": "奢华暗金", "dark": True,
                 "prompt": "luxury dark gold accents, deep black background, premium product spotlight, chanel style elegance, metallic sheen",
                 "accent": (212, 175, 55), "accent2": (245, 222, 140)},
    "kawaii":   {"name": "清新可爱", "dark": False,
                 "prompt": "cute kawaii pastel style, soft rounded shapes, bright airy light, adorable, playful, dreamy",
                 "accent": (255, 160, 190), "accent2": (160, 210, 255)},
    "techblue": {"name": "科技蓝调", "dark": True,
                 "prompt": "cool tech blue tone, clean futuristic interface glow, sleek product render, icelandic minimal, crisp light",
                 "accent": (56, 132, 255), "accent2": (90, 200, 255)},
    "ink":      {"name": "水墨国风", "dark": False,
                 "prompt": "traditional chinese ink wash painting, sumi-e style, elegant brush strokes, rice paper texture, poetic atmosphere",
                 "accent": (60, 60, 66), "accent2": (150, 60, 50)},
})


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
# 画面生成 (多 Provider 分发)
# ---------------------------------------------------------------------------
async def gen_flux(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    """Pollinations FLUX 免 Key 兜底通道。开启 enhance 提升免费图质量。"""
    global LAST_PROVIDER
    w, h = FLUX_SIZES[size_id]
    base_url = "https://image.pollinations.ai/prompt/" + quote(prompt)
    last_err = None
    for attempt in range(6):
        s = seed or random.randint(1, 999999)
        url = base_url + f"?width={w}&height={h}&model=flux&nologo=true&enhance=true&seed={s}"
        try:
            async with httpx.AsyncClient(timeout=180.0) as c:
                r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and r.content:
                    logger.info(f"FLUX ok size={len(r.content)} prompt_len={len(prompt)} attempt={attempt+1}")
                    LAST_PROVIDER = "FLUX(免费)"
                    return r.content
                if r.status_code == 429:
                    wait = 8 + attempt * 7 + random.uniform(0, 4)
                    logger.warning(f"FLUX 429 限流, 第{attempt+1}次重试, 等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
                    last_err = 429
                    continue
                logger.error(f"FLUX HTTP {r.status_code}: {r.text[:160]}")
                last_err = r.status_code
        except Exception as e:
            logger.error(f"FLUX 异常: {e}")
            last_err = e
            await asyncio.sleep(3)
            continue
    logger.error(f"FLUX 最终失败 (last={last_err})")
    return None


async def gen_gemini(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    """Google Gemini 图像生成 (AI Studio 免费档, 不绑卡)。免费里质量最高。"""
    global LAST_PROVIDER
    if not GEMINI_API_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
           f":generateContent?key={GEMINI_API_KEY}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    headers = {"Content-Type": "application/json"}
    last_err = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=180.0) as c:
                r = await c.post(url, json=body, headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    parts = (d.get("candidates", [{}])[0]
                             .get("content", {}).get("parts", []))
                    for it in parts:
                        ind = it.get("inlineData") or it.get("inline_data")
                        if ind and ind.get("data"):
                            data = base64.b64decode(ind["data"])
                            logger.info(f"GEMINI ok model={GEMINI_MODEL} size={len(data)} attempt={attempt+1}")
                            LAST_PROVIDER = "Gemini(免费)"
                            return data
                    logger.error(f"GEMINI 响应无图像: {str(d)[:200]}")
                    last_err = "no-image"
                elif r.status_code == 429:
                    wait = 6 + attempt * 6 + random.uniform(0, 3)
                    logger.warning(f"GEMINI 429, 第{attempt+1}次重试, 等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
                    last_err = 429
                    continue
                else:
                    logger.error(f"GEMINI HTTP {r.status_code}: {r.text[:200]}")
                    last_err = r.status_code
                    await asyncio.sleep(3)
                    continue
        except Exception as e:
            logger.error(f"GEMINI 异常: {e}")
            last_err = e
            await asyncio.sleep(3)
            continue
    logger.error(f"GEMINI 最终失败 (last={last_err})")
    return None


async def gen_cloudflare(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    """Cloudflare Workers AI FLUX.1 schnell (免费档 10000 neurons/天 ≈ 230 张, 不绑卡)。"""
    global LAST_PROVIDER
    if not (CF_ACCOUNT_ID and CF_API_TOKEN):
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    body = {"prompt": prompt, "steps": 4, "seed": seed or random.randint(1, 999999)}
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    last_err = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=180.0) as c:
                r = await c.post(url, json=body, headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    img = None
                    if isinstance(d.get("result"), dict):
                        img = d["result"].get("image") or d["result"].get("images")
                    if isinstance(img, list) and img:
                        img = img[0]
                    if img:
                        data = base64.b64decode(img)
                        logger.info(f"CF ok model={CF_MODEL} size={len(data)} attempt={attempt+1}")
                        LAST_PROVIDER = "Cloudflare(免费)"
                        return data
                    logger.error(f"CF 响应无图像: {str(d)[:200]}")
                    last_err = "no-image"
                elif r.status_code == 429:
                    wait = 6 + attempt * 6 + random.uniform(0, 3)
                    logger.warning(f"CF 429, 第{attempt+1}次重试, 等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
                    last_err = 429
                    continue
                else:
                    logger.error(f"CF HTTP {r.status_code}: {r.text[:200]}")
                    last_err = r.status_code
                    await asyncio.sleep(3)
                    continue
        except Exception as e:
            logger.error(f"CF 异常: {e}")
            last_err = e
            await asyncio.sleep(3)
            continue
    logger.error(f"CF 最终失败 (last={last_err})")
    return None


async def gen_doubao(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    """豆包 / 即梦 Seedream (火山方舟 OpenAI 兼容接口)。中文 / 电商场景质量强。"""
    global LAST_PROVIDER
    w, h = DOUBAO_SIZES.get(size_id, (1024, 1024))
    body = {
        "model": DOUBAO_MODEL,
        "prompt": prompt,
        "size": f"{w}x{h}",
        "n": 1,
        "response_format": "b64_json",
    }
    headers = {"Authorization": f"Bearer {DOUBAO_API_KEY}", "Content-Type": "application/json"}
    last_err = None
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=180.0) as c:
                r = await c.post(f"{DOUBAO_BASE_URL}/images/generations", json=body, headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    b64 = url = None
                    if isinstance(d.get("data"), list) and d["data"]:
                        b64 = d["data"][0].get("b64_json"); url = d["data"][0].get("url")
                    elif isinstance(d.get("choices"), list) and d["choices"]:
                        msg = d["choices"][0].get("message", {})
                        b64 = msg.get("b64_json")
                        content = msg.get("content")
                        if isinstance(content, list):
                            for it in content:
                                if isinstance(it, dict) and it.get("type") == "image_url":
                                    url = it.get("image_url", {}).get("url")
                    if b64:
                        data = base64.b64decode(b64)
                        logger.info(f"DOUBAO ok model={DOUBAO_MODEL} size={len(data)} attempt={attempt+1}")
                        LAST_PROVIDER = "豆包Seedream"
                        return data
                    if url:
                        rr = await c.get(url)
                        if rr.status_code == 200 and rr.content:
                            logger.info(f"DOUBAO ok(url) size={len(rr.content)}")
                            LAST_PROVIDER = "豆包Seedream"
                            return rr.content
                    logger.error(f"DOUBAO 响应无图像字段: {r.text[:200]}")
                    last_err = "no-image"
                elif r.status_code == 429:
                    wait = 6 + attempt * 6 + random.uniform(0, 3)
                    logger.warning(f"DOUBAO 429 限流, 第{attempt+1}次重试, 等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
                    last_err = 429
                    continue
                else:
                    logger.error(f"DOUBAO HTTP {r.status_code}: {r.text[:200]}")
                    last_err = r.status_code
                    await asyncio.sleep(3)
                    continue
        except Exception as e:
            logger.error(f"DOUBAO 异常: {e}")
            last_err = e
            await asyncio.sleep(3)
            continue
    logger.error(f"DOUBAO 最终失败 (last={last_err})")
    return None


async def gen_siliconflow(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    """硅基流动 SiliconFlow 生图 (国内可达, 注册送免费额度)。
    自动尝试 SF_MODEL 及其备用模型, 遇到 'Model disabled' 自动切换到下一个。"""
    global LAST_PROVIDER
    if not SF_API_KEY:
        return None
    w, h = SF_SIZES.get(size_id, (1024, 1024))
    url = f"{SF_BASE_URL}/images/generations"
    headers = {"Authorization": f"Bearer {SF_API_KEY}", "Content-Type": "application/json"}
    candidates = []
    for m in [SF_MODEL] + SF_FALLBACK_MODELS:
        if m and m not in candidates:
            candidates.append(m)
    last_err = None
    for model in candidates:
        body = {
            "model": model, "prompt": prompt, "n": 1,
            "size": f"{w}x{h}", "response_format": "b64_json",
        }
        ok = False
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=180.0) as c:
                    r = await c.post(url, json=body, headers=headers)
                    if r.status_code == 200:
                        d = r.json()
                        if isinstance(d.get("data"), list) and d["data"]:
                            item = d["data"][0]
                            b64 = item.get("b64_json"); img_url = item.get("url")
                            if b64 and not str(b64).startswith("http"):
                                data = base64.b64decode(b64)
                                logger.info(f"SF ok model={model} size={len(data)} attempt={attempt+1}")
                                LAST_PROVIDER = "硅基流动(免费)"
                                return data
                            if img_url:
                                rr = await c.get(img_url)
                                if rr.status_code == 200 and rr.content:
                                    logger.info(f"SF ok(url) model={model} size={len(rr.content)}")
                                    LAST_PROVIDER = "硅基流动(免费)"
                                    return rr.content
                        logger.error(f"SF 响应无图像字段: {r.text[:200]}")
                        last_err = "no-image"
                    elif r.status_code in (403, 400) and "disabled" in r.text.lower():
                        # 该模型在当前账号被禁用, 切换到下一个候选模型
                        logger.warning(f"SF 模型 {model} 被禁用, 尝试下一个候选")
                        last_err = f"{model} disabled"
                        ok = False
                        break
                    elif r.status_code == 429:
                        wait = 6 + attempt * 6 + random.uniform(0, 3)
                        logger.warning(f"SF 429 限流 model={model}, 第{attempt+1}次重试, 等待 {wait:.1f}s")
                        await asyncio.sleep(wait)
                        last_err = 429
                        continue
                    else:
                        logger.error(f"SF HTTP {r.status_code}: {r.text[:200]}")
                        last_err = r.status_code
                        await asyncio.sleep(3)
                        continue
            except Exception as e:
                logger.error(f"SF 异常 model={model}: {e}")
                last_err = e
                await asyncio.sleep(3)
                continue
        if ok:
            break
    logger.error(f"SF 最终失败 (last={last_err})")
    return None


async def gen_artwork(prompt: str, size_id: str, seed: int = None) -> Optional[bytes]:
    """多 Provider 分发: 优先走 IMAGE_PROVIDER 指定的源 (需配 Key);
    若未配置或失败, 自动降级到免 Key 的 Pollinations FLUX。"""
    order = []
    pref = IMAGE_PROVIDER
    if pref in ("pollinations", "flux"):
        order = ["flux"]
    elif _prov_available(pref):
        order = [pref, "flux"]
    else:
        # 指定了源但没配 Key, 直接走兜底并提示
        logger.warning(f"未检测到 {pref} 的 Key/Token, 自动使用免 Key 的 Pollinations FLUX")
        order = ["flux"]
    for p in order:
        fn = {"flux": gen_flux, "doubao": gen_doubao,
              "gemini": gen_gemini, "cloudflare": gen_cloudflare,
              "siliconflow": gen_siliconflow}.get(p)
        if not fn:
            continue
        data = await fn(prompt, size_id, seed)
        if data:
            return data
        if p != "flux":
            logger.warning(f"{p} 生成失败, 尝试下一个源")
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


# ---- 0. 电商生图 (商品主图) -----------------------------------------------
def compose_ecom(art: bytes, prompt: str, name: str, style_id: str, size_id: str) -> bytes:
    sz = SIZES[size_id]; W, H = sz["w"], sz["h"]
    lay = STYLES[style_id]
    img = prep(art, W, H)
    draw = ImageDraw.Draw(img)
    accent = lay["accent"]
    white, soft, faint = ink_for(lay["dark"])
    k = W / 1080.0

    # 顶部商品标题胶囊
    title = name or short(prompt, 12)
    f_t = font(fit_size(draw, title, int(42 * k), W * 0.78, int(24 * k)))
    pill(draw, W / 2, int(72 * k), title, f_t, accent + (255,), int(20 * k))

    # 卖点标签条
    points = ["正品保障", "顺丰包邮", "7天无理由"]
    f_p = font(int(25 * k), False)
    n = len(points); gap = W / (n + 1); py = int(H * 0.85)
    for i, p in enumerate(points):
        pill(draw, gap * (i + 1), py, p, f_p, accent + (200,), int(15 * k))

    # 价格
    price = "¥" + str(random.choice([39, 69, 99, 129, 199, 299, 399]))
    f_pr = font(int(40 * k))
    pb = draw.textbbox((0, 0), price, font=f_pr)
    draw.text((W - int(140 * k) - pb[2], int(H * 0.78)), price, font=f_pr, fill=accent + (255,))

    out = io.BytesIO(); img.convert("RGB").save(out, "PNG")
    return out.getvalue()


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

    bw = int(10 * k)
    draw.rectangle([bw, bw, W - bw, H - bw], outline=accent + (220,), width=bw)

    f_tag = font(int(26 * k))
    pill(draw, int(120 * k), int(70 * k), "AI 写真馆", f_tag, accent + (255,), int(20 * k))

    title = name or short(prompt, 10)
    f_name = font(fit_size(draw, title, int(72 * k), W * 0.7, int(34 * k)))
    b = draw.textbbox((0, 0), title, font=f_name); x = (W - (b[2] - b[0])) / 2 - b[0]
    draw.text((x, H - int(220 * k)), title, font=f_name, fill=white)
    sub = f"{lay['name']} · 高定写真"
    f_sub = font(int(30 * k), False)
    sb = draw.textbbox((0, 0), sub, font=f_sub); sx = (W - (sb[2] - sb[0])) / 2 - sb[0]
    draw.text((sx, H - int(120 * k)), sub, font=f_sub, fill=soft)
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

    panel_h = int(H * 0.40)
    panel = Image.new("RGBA", (W, panel_h), (255, 255, 255, 244) if not lay["dark"] else (16, 18, 34, 240))
    img.paste(panel, (0, H - panel_h), panel)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, H - panel_h, W, H - panel_h + int(10 * k)], fill=accent + (255,))

    brand = name or "BRAND"
    product = short(prompt, 12)
    tagline = gen_copy(prompt)["subtitle"]

    f_brand = font(int(34 * k))
    bb = draw.textbbox((0, 0), brand, font=f_brand)
    draw.text((int(60 * k) - bb[0], H - panel_h + int(50 * k)), brand, font=f_brand, fill=accent + (255,))
    f_prod = font(fit_size(draw, product, int(64 * k), W - int(120 * k), int(32 * k)))
    pb = draw.textbbox((0, 0), product, font=f_prod)
    draw.text((int(60 * k) - pb[0], H - panel_h + int(110 * k)), product, font=f_prod, fill=(22, 24, 46, 255) if not lay["dark"] else white)
    f_tl = font(int(28 * k), False)
    tb = draw.textbbox((0, 0), tagline, font=f_tl)
    draw.text((int(62 * k) - tb[0], H - panel_h + int(110 * k) + int(74 * k)), tagline, font=f_tl, fill=(90, 94, 120, 255) if not lay["dark"] else faint)

    bx0 = W - int(260 * k); by = H - int(80 * k); bh = int(70 * k)
    random.seed(hash(prompt) & 0xffff)
    x = bx0
    while x < W - int(60 * k):
        wbar = random.choice([3, 5, 2, 6]) * k
        draw.rectangle([x, by, x + wbar, by + bh], fill=(20, 20, 20, 255) if not lay["dark"] else (220, 220, 220, 255))
        x += wbar + 3 * k

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

    price = "¥" + str(random.choice([19, 29, 39, 49, 59, 88, 128]))
    f_p = font(int(40 * k))
    pr = int(70 * k); pcx, pcy = W - int(120 * k), int(130 * k)
    draw.ellipse([pcx - pr, pcy - pr, pcx + pr, pcy + pr], fill=accent + (255,))
    pb = draw.textbbox((0, 0), price, font=f_p)
    draw.text((pcx - (pb[2] - pb[0]) / 2 - pb[0], pcy - (pb[3] - pb[1]) / 2 - pb[1]), price, font=f_p, fill=white)

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

    sizes = ["S", "M", "L"]
    f_s = font(int(30 * k))
    bx = int(64 * k); by = H - int(140 * k)
    for i, s in enumerate(sizes):
        cx = bx + i * int(96 * k)
        draw.rounded_rectangle([cx, by, cx + int(72 * k), by + int(72 * k)], radius=int(14 * k),
                               outline=accent + (255,), width=int(4 * k))
        sb = draw.textbbox((0, 0), s, font=f_s)
        draw.text((cx + int(36 * k) - (sb[2] - sb[0]) / 2 - sb[0], by + int(18 * k)), s, font=f_s, fill=soft)

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

    bw = int(16 * k)
    draw.rectangle([bw, bw, W - bw, H - bw], outline=(10, 10, 14, 255), width=bw)
    draw.rectangle([bw * 2, bw * 2, W - bw * 2, H - bw * 2], outline=(10, 10, 14, 255), width=int(4 * k))

    pill(draw, int(150 * k), int(64 * k), name or "第 01 幕", font(int(26 * k)), accent + (255,), int(18 * k))

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
def static_frames(img: Image.Image, n: int) -> list:
    """纯静帧: 同一张图重复 n 次, 不做任何运镜/缩放/平移。返回引用同一对象的列表。

    比 kenburns 更省内存(只持有一张图), 且画面完全静止, 符合「不要任何运镜」的需求。
    """
    return [img.convert("RGB")] * n


def build_video(frames: list, out_path: str, fps: int = VIDEO_FPS) -> str:
    """优先 MP4 (libx264), 失败回退 GIF。返回 'mp4' / 'gif'。"""
    try:
        writer = imageio.get_writer(out_path + ".mp4", fps=fps, codec="libx264", quality=7, macro_block_size=1)
        for f in frames:
            writer.append_data(np.array(f))
        writer.close()
        return "mp4"
    except Exception as e:
        logger.warning(f"MP4 编码失败, 回退 GIF: {e}")
        try:
            gif_path = out_path + ".gif"
            imageio.mimsave(gif_path, [np.array(f) for f in frames], fps=fps, loop=0)
            return "gif"
        except Exception as e2:
            logger.error(f"GIF 也失败: {e2}")
            return "err"


# 阿里通义万相视频尺寸映射 (按比例对齐 SIZES)
WANX_SIZE_MAP = {
    "story": "768*1280", "xhs": "768*1280", "wide": "1280*720",
    "gzh": "1280*720", "square": "720*720",
}

def build_scene_prompts(prompt: str, style_id: str, category: str) -> list:
    """把一个描述拆成多个分镜提示词 (多镜头工作流)。"""
    mod = _style_mod(style_id)
    if category == "comic":
        # AI 漫剧: 起承转合叙事分镜
        return [
            f"manhua comic panel, establishing shot: {prompt}, {mod}, cinematic, dramatic lighting, no text",
            f"manhua comic panel, the protagonist steps into {prompt}, dynamic action scene, {mod}, no text",
            f"manhua comic panel, climax confrontation around {prompt}, intense emotion, {mod}, no text",
            f"manhua comic panel, resolution after {prompt}, calm ending, {mod}, no text",
        ]
    # 电商 / 默认: 商品多镜头
    return [
        f"cinematic e-commerce hero shot of {prompt}, dramatic lighting, {mod}, premium product render, no text",
        f"close-up detail macro of {prompt}, texture focus, {mod}, studio, no text",
        f"lifestyle scene featuring {prompt} in real environment, {mod}, warm mood, no text",
        f"packaging and presentation of {prompt}, flat lay, {mod}, clean, no text",
    ]

async def gen_video_dashscope(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """阿里通义万相文生视频: 提交任务 -> 轮询 -> 下载 mp4。返回字节或 None。"""
    global LAST_PROVIDER
    if not DASHSCOPE_API_KEY:
        return None
    size = WANX_SIZE_MAP.get(size_id, "1280*720")
    url = f"{DASHSCOPE_BASE_URL}/services/aigc/video-generation/video-synthesis"
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
               "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    body = {"model": DASHSCOPE_T2V_MODEL, "input": {"prompt": prompt},
            "parameters": {"size": size, "duration": duration, "prompt_extend": True}}
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if progress:
                await progress("submit", "已提交 AI 视频任务，等待渲染…", pct=50)
            r = await c.post(url, json=body, headers=headers)
            if r.status_code != 200:
                logger.error(f"万相提交失败 {r.status_code}: {r.text[:200]}")
                return None
            d = r.json()
        task_id = (d.get("output") or {}).get("task_id") or d.get("task_id")
        if not task_id:
            logger.error(f"万相无 task_id: {str(d)[:200]}")
            return None
        poll_url = f"{DASHSCOPE_BASE_URL}/tasks/{task_id}"
        poll_headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
        async with httpx.AsyncClient(timeout=30.0) as c:
            for i in range(72):  # 最多约 6 分钟
                await asyncio.sleep(5)
                if progress and i % 3 == 0:
                    await progress("render", "AI 正在渲染视频（免费模型较慢，请耐心等待）…")
                pr = await c.get(poll_url, headers=poll_headers)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                out = pd.get("output") or {}
                status = out.get("task_status", "")
                if status == "SUCCEEDED":
                    if progress:
                        await progress("download", "AI 渲染完成，正在下载视频…", pct=88)
                    video_url = out.get("video_url")
                    if not video_url and out.get("results"):
                        video_url = (out["results"][0] or {}).get("url")
                    if not video_url:
                        logger.error("万相成功但无 video_url")
                        return None
                    vr = await c.get(video_url, follow_redirects=True)
                    if vr.status_code == 200 and vr.content:
                        logger.info(f"万相视频 OK task={task_id} size={len(vr.content)}")
                        LAST_PROVIDER = "阿里万相(免费)"
                        return vr.content
                    logger.error("万相视频下载失败")
                    return None
                elif status == "FAILED":
                    logger.error(f"万相任务失败: {out.get('message')}")
                    return None
        logger.error("万相轮询超时")
        return None
    except Exception as e:
        logger.error(f"万相异常: {e}")
        return None

# 智谱 CogVideoX / 清影 尺寸映射 (格式 widthxheight)
ZHIPU_SIZE_MAP = {
    "xhs": "864x1152", "square": "1024x1024", "gzh": "1512x648",
    "story": "1080x1920", "wide": "1920x1080",
}

async def gen_video_zhipu(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """智谱 CogVideoX / 清影 文生视频: 提交任务 -> 轮询 -> 下载 mp4。返回字节或 None。"""
    global LAST_PROVIDER
    if not ZHIPU_API_KEY:
        return None
    size = ZHIPU_SIZE_MAP.get(size_id, "1920x1080")
    url = f"{ZHIPU_BASE_URL}/videos/generations"
    headers = {"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"}
    body = {"model": ZHIPU_MODEL, "prompt": prompt, "size": size,
            "duration": duration, "with_audio": False, "fps": 30}
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            task_id = None
            last_err = None
            for attempt in range(2):  # 提交遇 429/5xx 退避重试, 失败后快速降级静态分镜兜底
                if attempt == 0 and progress:
                    await progress("submit", "已提交 AI 视频任务，等待渲染…", pct=50)
                r = await c.post(url, json=body, headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    task_id = d.get("id")
                    if task_id:
                        break
                    logger.error(f"智谱提交成功但无 task_id: {str(d)[:200]}")
                    return None
                last_err = r.status_code
                if r.status_code in (429, 500, 502, 503):
                    wait = 6 + attempt * 8 + random.uniform(0, 3)
                    if progress:
                        await progress("retry", f"免费模型繁忙，正在第 {attempt+1} 次重试…", pct=55)
                    logger.warning(f"智谱提交 {r.status_code} 限流, 第{attempt+1}次重试, 等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"智谱提交失败 {r.status_code}: {r.text[:200]}")
                return None
            if not task_id:
                logger.error(f"智谱提交重试后仍失败 (last={last_err})")
                return None
        poll_url = f"{ZHIPU_BASE_URL}/videos/{task_id}"
        async with httpx.AsyncClient(timeout=30.0) as c:
            for i in range(72):  # 最多约 6 分钟
                await asyncio.sleep(5)
                if progress and i % 3 == 0:
                    await progress("render", "AI 正在渲染视频（免费模型较慢，请耐心等待）…")
                pr = await c.get(poll_url, headers=headers)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                status = pd.get("task_status", "")
                if status in ("SUCCESS", "SUCCEEDED"):
                    if progress:
                        await progress("download", "AI 渲染完成，正在下载视频…", pct=88)
                    results = pd.get("video_result") or []
                    video_url = (results[0] or {}).get("url") if results else pd.get("url")
                    if not video_url:
                        logger.error("智谱成功但无 video_url")
                        return None
                    vr = await c.get(video_url, follow_redirects=True)
                    if vr.status_code == 200 and vr.content:
                        logger.info(f"智谱视频 OK task={task_id} size={len(vr.content)}")
                        LAST_PROVIDER = "智谱清影(免费)"
                        return vr.content
                    logger.error("智谱视频下载失败")
                    return None
                elif status == "FAILED":
                    logger.error(f"智谱任务失败: {pd.get('message') or str(pd)[:160]}")
                    return None
        logger.error("智谱轮询超时")
        return None
    except Exception as e:
        logger.error(f"智谱异常: {e}")
        return None

async def _real_video_workflow(provider_fn, prompt: str, style_id: str, size_id: str, category: str, progress=None) -> dict:
    """真实视频 AI 多镜头工作流: 拆分镜 -> 各生成短视频 -> ffmpeg 拼接成长片。"""
    scene_prompts = build_scene_prompts(prompt, style_id, category)
    n = max(1, min(VIDEO_SCENES, len(scene_prompts)))
    if progress:
        await progress("scene", f"正在并行生成 {n} 个分镜…", pct=10)
    async def gen_one(i, sp):
        return await provider_fn(sp, size_id, VIDEO_CLIP_DURATION, progress=progress)
    clips = await asyncio.gather(*[gen_one(i, sp) for i, sp in enumerate(scene_prompts[:n])])
    clips = [c for c in clips if c]
    if not clips:
        logger.warning("真实视频全部分镜失败, 回退 Ken Burns")
        raise RuntimeError("真实视频分镜全部失败")
    if len(clips) == 1:
        return {"media_type": "mp4", "media_b64": base64.b64encode(clips[0]).decode(),
                "scenes": n, "frames": 1, "provider": LAST_PROVIDER}
    paths = []
    for i, cb in enumerate(clips):
        p = str(LOG_DIR / f"scene_{i}.mp4")
        Path(p).write_bytes(cb)
        paths.append(p)
    if progress:
        await progress("concat", "拼接多镜头成片…", pct=90)
    out = concat_videos(paths, str(LOG_DIR / "latest_video"))
    if out and Path(out).exists():
        data = Path(out).read_bytes()
        return {"media_type": "mp4", "media_b64": base64.b64encode(data).decode(),
                "scenes": n, "frames": len(paths), "provider": LAST_PROVIDER}
    logger.warning("拼接失败, 退回首个分镜")
    return {"media_type": "mp4", "media_b64": base64.b64encode(clips[0]).decode(),
            "scenes": n, "frames": 1, "provider": LAST_PROVIDER}

def concat_videos(clip_paths: list, out_path: str) -> Optional[str]:
    """用 imageio-ffmpeg 自带 ffmpeg 把多个 mp4 拼接 -> 一条更长的成片。"""
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        logger.error(f"取 ffmpeg 失败: {e}")
        return None
    list_path = str(LOG_DIR / "concat_list.txt")
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in clip_paths:
                f.write(f"file '{p}'\n")
        out = out_path + ".mp4"
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return out
    except Exception as e:
        logger.error(f"ffmpeg 拼接失败: {e}")
        return None

# ===========================================================================
# 可灵 Kling 文生视频 (快手开放平台)
# ===========================================================================
async def gen_video_kling(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """可灵 Kling 文生视频: 提交任务 -> 轮询 -> 下载 mp4。返回字节或 None。"""
    global LAST_PROVIDER
    if not KLING_API_KEY:
        return None
    w, h = SIZES[size_id]["w"], SIZES[size_id]["h"]
    aspect = "16:9" if w / h > 1.05 else ("9:16" if w / h < 0.95 else "1:1")
    url = f"{KLING_BASE_URL}/videos/text2video"
    headers = {"Authorization": f"Bearer {KLING_API_KEY}", "Content-Type": "application/json"}
    body = {"model": KLING_MODEL, "prompt": prompt, "mode": "std",
            "duration": duration, "aspect_ratio": aspect}
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if progress:
                await progress("submit", "已提交可灵 Kling 视频任务…", pct=50)
            r = await c.post(url, json=body, headers=headers)
            d = r.json()
            if d.get("code") not in (0, None, "0"):
                logger.error(f"可灵提交失败 {r.status_code}: {str(d)[:200]}")
                return None
            task_id = (d.get("data") or {}).get("task_id")
            if not task_id:
                logger.error(f"可灵无 task_id: {str(d)[:200]}")
                return None
        poll_url = f"{KLING_BASE_URL}/videos/text2video/{task_id}"
        async with httpx.AsyncClient(timeout=30.0) as c:
            for i in range(90):  # 最多约 7.5 分钟
                await asyncio.sleep(5)
                if progress and i % 3 == 0:
                    await progress("render", "可灵正在渲染视频…")
                pr = await c.get(poll_url, headers=headers)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                data = pd.get("data") or {}
                status = data.get("task_status", "")
                if status in ("succeed", "SUCCESS", "finished"):
                    if progress:
                        await progress("download", "可灵渲染完成，正在下载…", pct=88)
                    res = data.get("task_result") or {}
                    videos = res.get("videos") or []
                    video_url = (videos[0] or {}).get("url") if videos else res.get("video_url")
                    if not video_url:
                        logger.error("可灵成功但无 video_url")
                        return None
                    vr = await c.get(video_url, follow_redirects=True)
                    if vr.status_code == 200 and vr.content:
                        logger.info(f"可灵视频 OK task={task_id} size={len(vr.content)}")
                        LAST_PROVIDER = "可灵Kling"
                        return vr.content
                    logger.error("可灵视频下载失败")
                    return None
                elif status in ("failed", "FAILED"):
                    logger.error(f"可灵任务失败: {data.get('task_status_msg') or str(pd)[:160]}")
                    return None
        logger.error("可灵轮询超时")
        return None
    except Exception as e:
        logger.error(f"可灵异常: {e}")
        return None


# ===========================================================================
# 海螺 Minimax 文生视频
# ===========================================================================
async def gen_video_minimax(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """海螺 Minimax 文生视频: 提交任务 -> 轮询 -> 下载。返回字节或 None。"""
    global LAST_PROVIDER
    if not MINIMAX_API_KEY:
        return None
    url = f"{MINIMAX_BASE_URL}/video_generation"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}
    body = {"model": MINIMAX_MODEL, "prompt": prompt, "duration": duration}
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if progress:
                await progress("submit", "已提交海螺 Minimax 视频任务…", pct=50)
            r = await c.post(url, json=body, headers=headers)
            d = r.json()
            if (d.get("base_resp") or {}).get("status_code", 0) != 0:
                logger.error(f"海螺提交失败 {r.status_code}: {str(d)[:200]}")
                return None
            task_id = d.get("task_id")
            if not task_id:
                logger.error(f"海螺无 task_id: {str(d)[:200]}")
                return None
        poll_url = f"{MINIMAX_BASE_URL}/query/video_generation"
        async with httpx.AsyncClient(timeout=30.0) as c:
            for i in range(90):
                await asyncio.sleep(5)
                if progress and i % 3 == 0:
                    await progress("render", "海螺正在渲染视频…")
                pr = await c.get(poll_url, params={"task_id": task_id}, headers=headers)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                status = pd.get("status", "")
                if status in ("Success", "success", "SUCCESS"):
                    if progress:
                        await progress("download", "海螺渲染完成，正在下载…", pct=88)
                    fu = pd.get("file_url") or pd.get("video_url")
                    if not fu:
                        logger.error("海螺成功但无 file_url")
                        return None
                    vr = await c.get(fu, follow_redirects=True)
                    if vr.status_code == 200 and vr.content:
                        logger.info(f"海螺视频 OK task={task_id} size={len(vr.content)}")
                        LAST_PROVIDER = "海螺Minimax"
                        return vr.content
                    logger.error("海螺视频下载失败")
                    return None
                elif status in ("Fail", "fail", "FAILED"):
                    logger.error(f"海螺任务失败: {pd.get('status_msg') or str(pd)[:160]}")
                    return None
        logger.error("海螺轮询超时")
        return None
    except Exception as e:
        logger.error(f"海螺异常: {e}")
        return None


# ===========================================================================
# 腾讯混元 Hunyuan 文生视频
# ===========================================================================
async def gen_video_hunyuan(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """腾讯混元文生视频: 提交任务 -> 轮询 -> 下载。返回字节或 None。"""
    global LAST_PROVIDER
    if not HUNYUAN_API_KEY:
        return None
    w, h = SIZES[size_id]["w"], SIZES[size_id]["h"]
    url = f"{HUNYUAN_BASE_URL}/video_generations"
    headers = {"Authorization": f"Bearer {HUNYUAN_API_KEY}", "Content-Type": "application/json"}
    body = {"model": HUNYUAN_VIDEO_MODEL, "prompt": prompt, "duration": duration, "size": f"{w}x{h}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if progress:
                await progress("submit", "已提交腾讯混元视频任务…", pct=50)
            r = await c.post(url, json=body, headers=headers)
            if r.status_code != 200:
                logger.error(f"混元提交失败 {r.status_code}: {r.text[:200]}")
                return None
            d = r.json()
        task_id = d.get("id") or (d.get("data") or {}).get("id")
        if not task_id:
            logger.error(f"混元无 id: {str(d)[:200]}")
            return None
        poll_url = f"{HUNYUAN_BASE_URL}/video_generations/{task_id}"
        async with httpx.AsyncClient(timeout=30.0) as c:
            for i in range(90):
                await asyncio.sleep(5)
                if progress and i % 3 == 0:
                    await progress("render", "混元正在渲染视频…")
                pr = await c.get(poll_url, headers=headers)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                status = (pd.get("status") or "").lower()
                if status in ("succeeded", "success", "completed", "finished"):
                    if progress:
                        await progress("download", "混元渲染完成，正在下载…", pct=88)
                    vu = pd.get("url") or pd.get("video_url") or (pd.get("data") or {}).get("url")
                    if not vu:
                        logger.error("混元成功但无 url")
                        return None
                    vr = await c.get(vu, follow_redirects=True)
                    if vr.status_code == 200 and vr.content:
                        logger.info(f"混元视频 OK id={task_id} size={len(vr.content)}")
                        LAST_PROVIDER = "腾讯混元"
                        return vr.content
                    logger.error("混元视频下载失败")
                    return None
                elif status in ("failed", "error"):
                    logger.error(f"混元任务失败: {pd.get('error') or str(pd)[:160]}")
                    return None
        logger.error("混元轮询超时")
        return None
    except Exception as e:
        logger.error(f"混元异常: {e}")
        return None


# ===========================================================================
# Pollinations 视频 (需 Key)
# ===========================================================================
async def gen_video_pollinations(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """Pollinations Keyed 视频: 提交 -> 下载。返回字节或 None。"""
    global LAST_PROVIDER
    if not POLLINATIONS_API_KEY:
        return None
    url = f"{POLLINATIONS_BASE_URL}/videos"
    headers = {"Authorization": f"Bearer {POLLINATIONS_API_KEY}", "Content-Type": "application/json"}
    w, h = SIZES[size_id]["w"], SIZES[size_id]["h"]
    body = {"model": "video", "prompt": prompt, "width": w, "height": h,
            "duration": duration, "nologo": True}
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as c:
            if progress:
                await progress("submit", "已提交 Pollinations 视频任务…", pct=50)
            r = await c.post(url, json=body, headers=headers)
            if r.status_code != 200:
                logger.error(f"Pollinations 提交失败 {r.status_code}: {r.text[:200]}")
                return None
            ct = r.headers.get("content-type", "")
            if "application/json" in ct:
                d = r.json()
                vu = d.get("url") or d.get("video_url") or (d.get("data") or {}).get("url")
                if not vu:
                    logger.error("Pollinations 返回 JSON 但无视频地址")
                    return None
                vr = await c.get(vu, follow_redirects=True)
                if vr.status_code == 200 and vr.content:
                    LAST_PROVIDER = "Pollinations"
                    return vr.content
                return None
            else:
                if r.content:
                    LAST_PROVIDER = "Pollinations"
                    return r.content
                return None
    except Exception as e:
        logger.error(f"Pollinations 异常: {e}")
        return None


# ===========================================================================
# 火山引擎 Seedance 文生视频 (Volcengine Ark, 真实文生视频)
# ===========================================================================
async def gen_video_volcengine(prompt: str, size_id: str, duration: int = 5, progress=None) -> Optional[bytes]:
    """火山引擎 Seedance 文生视频: 提交任务 -> 轮询 -> 下载。返回字节或 None。"""
    global LAST_PROVIDER
    if not VOLC_API_KEY:
        return None
    w, h = SIZES[size_id]["w"], SIZES[size_id]["h"]
    # 由宽高比推导 Seedance 的 ratio 参数
    if abs(w / h - 16 / 9) < 0.05:
        ratio = "16:9"
    elif abs(w / h - 9 / 16) < 0.05:
        ratio = "9:16"
    elif abs(w / h - 1) < 0.05:
        ratio = "1:1"
    else:
        ratio = "16:9"
    resolution = "1080p" if h >= 1080 else "720p"
    dur = duration if duration and duration in (5, 10) else 5
    url = f"{VOLC_BASE_URL}/contents/generations/tasks"
    headers = {"Authorization": f"Bearer {VOLC_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": VOLC_MODEL,
        "content": [{"type": "text", "text": prompt}],
        "duration": dur,
        "ratio": ratio,
        "resolution": resolution,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as c:
            if progress:
                await progress("submit", "已提交火山 Seedance 视频任务…", pct=50)
            r = await c.post(url, json=body, headers=headers)
            if r.status_code != 200:
                logger.error(f"火山提交失败 {r.status_code}: {r.text[:200]}")
                return None
            d = r.json()
        gen_id = d.get("id") or (d.get("data") or {}).get("id")
        if not gen_id:
            logger.error(f"火山无任务 id: {str(d)[:200]}")
            return None
        poll_url = f"{VOLC_BASE_URL}/contents/generations/tasks/{gen_id}"
        async with httpx.AsyncClient(timeout=30.0, verify=False) as c:
            for i in range(120):
                await asyncio.sleep(5)
                if progress and i % 3 == 0:
                    await progress("render", "火山 Seedance 正在渲染视频…")
                pr = await c.get(poll_url, headers=headers)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                st = pd.get("status")
                if st == "succeeded":
                    if progress:
                        await progress("download", "火山渲染完成，正在下载…", pct=88)
                    content = pd.get("content") or {}
                    vu = (content.get("video_url") if isinstance(content, dict) else None) \
                         or (pd.get("content") or [{}])[0].get("video_url") \
                         or pd.get("video_url")
                    if not vu:
                        logger.error("火山成功但无 video_url")
                        return None
                    vr = await c.get(vu, follow_redirects=True)
                    if vr.status_code == 200 and vr.content:
                        logger.info(f"火山视频 OK id={gen_id} size={len(vr.content)}")
                        LAST_PROVIDER = "火山Seedance"
                        return vr.content
                    logger.error("火山视频下载失败")
                    return None
                elif st in ("failed", "cancelled", "expired"):
                    logger.error(f"火山任务失败: {pd.get('status_msg') or str(pd)[:160]}")
                    return None
        logger.error("火山轮询超时")
        return None
    except Exception as e:
        logger.error(f"火山异常: {e}")
        return None


# ===========================================================================
# 多模型视频编排: 按可用 Key 自动串联, 全部失败才回退静态分镜兜底
# ===========================================================================
async def compose_video(prompt: str, name: str, style_id: str, size_id: str, category: str = None, progress=None) -> dict:
    """多模型视频生成: 按可用 Key 自动串联多个视频大模型, 全部失败才回退静态分镜兜底。
    progress 为可选异步回调, 用于流式上报进度。"""
    mod = _style_mod(style_id)

    registry = {
        "zhipu":        (gen_video_zhipu,        bool(ZHIPU_API_KEY),        "智谱清影"),
        "dashscope":    (gen_video_dashscope,    bool(DASHSCOPE_API_KEY),    "阿里万相"),
        "kling":        (gen_video_kling,        bool(KLING_API_KEY),        "可灵Kling"),
        "minimax":      (gen_video_minimax,      bool(MINIMAX_API_KEY),      "海螺Minimax"),
        "hunyuan":      (gen_video_hunyuan,      bool(HUNYUAN_API_KEY),      "腾讯混元"),
        "pollinations": (gen_video_pollinations, bool(POLLINATIONS_API_KEY), "Pollinations"),
        "volcengine":   (gen_video_volcengine,   bool(VOLC_API_KEY),        "火山Seedance"),
    }
    pref = (VIDEO_PROVIDER or "kenburns").lower()

    # 优先级编排:
    #   kenburns -> 仅静态兜底(即使配了 Key 也不尝试在线模型)
    #   auto     -> 所有已配 Key 的模型
    #   指定名   -> 该模型优先, 其余已配 Key 的模型兜底
    order = []
    if pref == "kenburns":
        order = []
    elif pref == "auto":
        order = [k for k, (fn, ok, label) in registry.items() if ok]
    else:
        if pref in registry and registry[pref][1]:
            order.append(pref)
        for k, (fn, ok, label) in registry.items():
            if ok and k not in order:
                order.append(k)

    if order:
        if progress:
            await progress("start", f"开始生成视频（可用模型: {', '.join(registry[o][2] for o in order)}）…", pct=3)
        for o in order:
            fn, ok, label = registry[o]
            try:
                if progress:
                    await progress("provider", f"正在调用 {label} …", pct=8)
                return await asyncio.wait_for(
                    _real_video_workflow(fn, prompt, style_id, size_id, category, progress=progress),
                    timeout=VIDEO_PROVIDER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(f"{label} 视频工作流超时({VIDEO_PROVIDER_TIMEOUT}s), 尝试下一个模型")
                if progress:
                    await progress("fallback", f"{label} 响应超时，自动切换下一个模型…", pct=40)
            except Exception as e:
                logger.error(f"{label} 视频工作流失败, 尝试下一个模型: {e}")
                if progress:
                    await progress("fallback", f"{label} 暂不可用，自动切换下一个模型…", pct=40)
        if progress:
            await progress("fallback", "所有在线视频模型均不可用，已用静态分镜兜底…", pct=55)

    # ---- 静态分镜兜底 (多镜头静态长片, 免 Key) ----
    # 每个镜头生成一张独立底图 -> 各自做一段静态画面 -> ffmpeg 拼接成约 10 秒长片,
    # 真正体现「多个视频串成一个长视频」, 而非单图 1 秒快切。
    W, H = SIZES[size_id]["w"], SIZES[size_id]["h"]
    base_scenes = [
        f"cinematic e-commerce hero shot of {prompt}, dramatic lighting, {mod}, premium product render, no text",
        f"close-up detail macro of {prompt}, texture focus, {mod}, studio, no text",
        f"lifestyle scene featuring {prompt} in real environment, {mod}, warm mood, no text",
        f"packaging and presentation of {prompt}, flat lay, {mod}, clean, no text",
    ]
    n = len(base_scenes)
    per = max(8, round(VIDEO_TOTAL_SECONDS * VIDEO_FPS / n))  # 每镜帧数, 总时长≈VIDEO_TOTAL_SECONDS秒

    async def make_scene(i, sp):
        if progress:
            await progress("image", f"生成分镜画面 {i+1}/{n}…", index=i+1, total=n, pct=62 + round(28 * i / n))
        art = await gen_artwork(sp, size_id)
        if art is None:
            raise RuntimeError(f"视频第 {i+1} 镜生成失败")
        img = prep(art, W, H)
        frames = static_frames(img, per)
        tmp_scene = str(LOG_DIR / f"kb_scene_{i}")
        kind = build_video(frames, tmp_scene)
        if kind == "err":
            raise RuntimeError(f"视频第 {i+1} 镜编码失败")
        return tmp_scene + "." + kind

    # 多镜头底图并行生成, 显著缩短兜底耗时
    scene_paths = await asyncio.gather(*[make_scene(i, sp) for i, sp in enumerate(base_scenes)])
    if progress:
        await progress("concat", "拼接多镜头成片…", pct=92)
    out = concat_videos(scene_paths, str(LOG_DIR / "latest_video"))
    if not out or not Path(out).exists():
        logger.warning("长片拼接失败, 退回首个分镜")
        out = scene_paths[0]
    with open(out, "rb") as f:
        data = f.read()
    return {"media_type": "mp4" if out.endswith(".mp4") else "gif",
            "media_b64": base64.b64encode(data).decode(),
            "scenes": n, "frames": per * n, "provider": "静态分镜兜底(免Key)"}


# ---------------------------------------------------------------------------
# SSE 辅助
# ---------------------------------------------------------------------------
def _sse(obj: dict) -> str:
    """把事件字典格式化为 SSE 数据帧。"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


# ---------------------------------------------------------------------------
# 品类注册表
# ---------------------------------------------------------------------------
CATEGORIES = {
    "ecom":     {"name": "电商生图", "icon": "🛍️", "desc": "商品主图 / 详情图 / 白底图",
                 "sizes": ["square", "xhs", "wide"], "allow_face": False,
                 "prompt": lambda p, s: (f"E-commerce product photography of {p}{product_hint(p)}, "
                                         f"centered on a clean pure white or light background, soft studio lighting, "
                                         f"{_style_mod(s)}, commercial main image, sharp product focus, "
                                         f"no human, no model, no face, no hands, no body, no people, "
                                         f"no text, no words, no watermark, 8k, detailed")},
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
                 "sizes": ["xhs", "square", "story"], "allow_face": False, "video": True,
                 "prompt": lambda p, s: (f"Comic book panel, cinematic illustration of {p}, manhua art style, "
                                         f"{_style_mod(s)}, dramatic lighting, no text, high detail, 8k")},
    "video":    {"name": "电商一键生视频", "icon": "🎞️", "desc": "多镜头静态分镜短视频",
                 "sizes": ["story", "wide"], "allow_face": False, "video": True},
}

# 每个品类挑选的专属风格 (从 STYLES 中取 id)。缺省回退到全局全部风格。
CATEGORY_STYLES = {
    "ecom":      ["gradient", "minimal", "nature", "luxury", "techblue"],
    "portrait":  ["gradient", "minimal", "film", "luxury", "retro"],
    "packaging": ["minimal", "morandi", "luxury", "retro", "nature"],
    "poster":    ["gradient", "retro", "cyber", "luxury", "minimal"],
    "home":      ["minimal", "nature", "morandi", "luxury"],
    "food":      ["nature", "film", "kawaii", "minimal"],
    "fitting":   ["gradient", "minimal", "film", "luxury", "cyber"],
    "comic":     ["cyber", "ink", "retro", "film"],
    "video":     ["gradient", "cyber", "film", "luxury"],
}


# ---------------------------------------------------------------------------
# 作品持久化 (每个站点 backend/outputs/ 存图 + works.json 索引)
# ---------------------------------------------------------------------------
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
WORKS_JSON = OUTPUTS_DIR / "works.json"


def _read_works() -> list:
    if WORKS_JSON.exists():
        try:
            return json.loads(WORKS_JSON.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取作品索引失败: {e}")
    return []


def _write_works(arr: list):
    try:
        WORKS_JSON.write_text(json.dumps(arr, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"写入作品索引失败: {e}")


def store_work(category, prompt, style, size, provider, media_type, data: bytes) -> dict:
    """保存一张生成的成品到磁盘, 并记录到索引。返回记录。"""
    ts = datetime.datetime.now()
    wid = ts.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    ext = "mp4" if media_type == "mp4" else ("gif" if media_type == "gif" else "png")
    fname = f"{wid}.{ext}"
    try:
        (OUTPUTS_DIR / fname).write_bytes(data)
    except Exception as e:
        logger.error(f"作品存盘失败: {e}")
    rec = {
        "id": wid, "category": category, "prompt": prompt, "style": style,
        "size": size, "provider": provider, "media_type": media_type or "png",
        "filename": fname, "created_at": ts.isoformat(timespec="seconds"),
    }
    arr = _read_works()
    arr.insert(0, rec)
    _write_works(arr)
    return rec


def list_works(limit: int = 80) -> list:
    return _read_works()[:limit]


def get_work_path(wid: str):
    for r in _read_works():
        if r["id"] == wid:
            return OUTPUTS_DIR / r["filename"]
    return None


def delete_work(wid: str) -> bool:
    arr = _read_works()
    new = [r for r in arr if r["id"] != wid]
    if len(new) == len(arr):
        return False
    _write_works(new)
    for r in arr:
        if r["id"] == wid:
            fp = OUTPUTS_DIR / r["filename"]
            if fp.exists():
                try:
                    fp.unlink()
                except Exception as e:
                    logger.warning(f"删除作品文件失败: {e}")
    return True


COMPOSERS = {
    "ecom": compose_ecom, "poster": compose_poster, "portrait": compose_portrait,
    "packaging": compose_packaging, "home": compose_home, "food": compose_food,
    "fitting": compose_fitting, "comic": compose_comic,
}


# ---------------------------------------------------------------------------
# 单品类应用工厂
# ---------------------------------------------------------------------------
def build_app(category: str, frontend_dir) -> FastAPI:
    if category not in CATEGORIES:
        raise ValueError(f"未知品类 {category}")
    cat = CATEGORIES[category]
    is_video = cat.get("video", False)
    app = FastAPI(title=cat["name"], version="1.0.0")
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(frontend_dir / "index.html")

    @app.get("/favicon.ico")
    async def favicon():
        return JSONResponse(status_code=204, content={})

    @app.get("/api/health")
    async def health():
        return {"ok": True, "category": category, "name": cat["name"]}

    @app.get("/api/meta")
    async def meta():
        style_ids = CATEGORY_STYLES.get(category, list(STYLES.keys()))
        return {
            "category": {"id": category, **{k: v for k, v in cat.items() if k != "prompt"}},
            "styles": [{"id": k, "name": STYLES[k]["name"], "dark": STYLES[k]["dark"]} for k in style_ids if k in STYLES],
            "sizes": [{"id": k, **{kk: vv for kk, vv in v.items() if kk != "id"}} for k, v in SIZES.items()],
        }

    class GenRequest(BaseModel):
        prompt: str
        style: str = "gradient"
        size: str = cat["sizes"][0]
        name: str = ""
        variants: int = 1

    @app.get("/api/works")
    async def works(limit: int = 80):
        return {"works": list_works(limit)}

    @app.get("/api/works/{wid}")
    async def work_file(wid: str):
        p = get_work_path(wid)
        if not p or not p.exists():
            return JSONResponse({"error": "作品不存在"}, status_code=404)
        return FileResponse(p)

    @app.delete("/api/works/{wid}")
    async def work_del(wid: str):
        return {"ok": delete_work(wid)}

    @app.post("/api/generate")
    async def generate(req: GenRequest):
        if req.style not in STYLES:
            return JSONResponse({"error": f"未知风格 {req.style}"}, status_code=422)
        if req.size not in cat["sizes"]:
            return JSONResponse({"error": f"品类 {category} 不支持尺寸 {req.size}"}, status_code=422)
        prompt = req.prompt.strip()
        if not prompt:
            return JSONResponse({"error": "描述不能为空"}, status_code=422)
        variants = max(1, min(4, int(req.variants or 1)))

        if is_video:
            try:
                vid = await compose_video(prompt, req.name, req.style, req.size, category)
            except Exception as e:
                logger.error(f"视频生成失败: {e}")
                return JSONResponse({"error": f"视频生成失败: {e}"}, status_code=502)
            data = base64.b64decode(vid["media_b64"])
            rec = store_work(category, prompt, req.style, req.size, LAST_PROVIDER, vid["media_type"], data)
            return {
                "category": category, "style": req.style, "size": req.size,
                "media_type": vid["media_type"], "media_b64": vid["media_b64"],
                "scenes": vid["scenes"], "frames": vid["frames"],
                "work_id": rec["id"], "created_at": rec["created_at"],
            }

        images = []
        for _ in range(variants):
            seed = random.randint(1, 999999)
            flx_prompt = cat["prompt"](prompt, req.style)
            art = await gen_artwork(flx_prompt, req.size, seed)
            if art is None:
                if not images:
                    return JSONResponse({"error": "AI 画面生成失败，请检查网络 (见 logs/app.log)"}, status_code=502)
                break
            try:
                png = COMPOSERS[category](art, prompt, req.name.strip(), req.style, req.size)
            except Exception as e:
                logger.error(f"合成失败: {e}")
                if not images:
                    return JSONResponse({"error": f"合成失败: {e}"}, status_code=500)
                break
            rec = store_work(category, prompt, req.style, req.size, f"{LAST_PROVIDER}+PIL", "png", png)
            images.append({
                "image_b64": base64.b64encode(png).decode(),
                "provider": f"{LAST_PROVIDER}+PIL",
                "work_id": rec["id"],
                "created_at": rec["created_at"],
            })
        if not images:
            return JSONResponse({"error": "AI 画面生成失败，请检查网络 (见 logs/app.log)"}, status_code=502)
        return {
            "category": category, "style": req.style, "size": req.size,
            "images": images, "count": len(images),
        }

    @app.post("/api/generate_stream")
    async def generate_stream(req: GenRequest):
        """视频站点专用: SSE 流式上报生成进度, 避免几分钟空白等待。非视频站点回退同步接口。"""
        if not is_video:
            return await generate(req)
        prompt = req.prompt.strip()
        if not prompt:
            return JSONResponse({"error": "描述不能为空"}, status_code=422)
        if req.style not in STYLES:
            return JSONResponse({"error": f"未知风格 {req.style}"}, status_code=422)
        if req.size not in cat["sizes"]:
            return JSONResponse({"error": f"品类 {category} 不支持尺寸 {req.size}"}, status_code=422)

        queue = asyncio.Queue()

        async def progress(stage, msg, **kw):
            await queue.put({"type": "stage", "stage": stage, "msg": msg, **kw})

        async def worker():
            try:
                vid = await compose_video(prompt, req.name, req.style, req.size, category, progress=progress)
                await queue.put({"type": "done", "result": vid})
            except Exception as e:
                logger.error(f"视频生成失败: {e}")
                await queue.put({"type": "error", "msg": str(e)})

        async def heartbeat():
            # 周期性心跳, 防止公网隧道 / 反向代理在长生成期间断开 SSE 连接
            while True:
                await asyncio.sleep(15)
                await queue.put({"type": "heartbeat"})

        async def event_stream():
            hb = asyncio.create_task(heartbeat())
            task = asyncio.create_task(worker())
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=330)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"   # 心跳, 防止连接被中间层断开
                        continue
                    if ev["type"] == "heartbeat":
                        yield ": keep-alive\n\n"
                        continue
                    if ev["type"] == "done":
                        res = ev["result"]
                        data = base64.b64decode(res["media_b64"])
                        rec = store_work(category, prompt, req.style, req.size,
                                         res.get("provider") or LAST_PROVIDER, res["media_type"], data)
                        yield _sse({"type": "done", "media_type": res["media_type"],
                                    "media_b64": res["media_b64"], "scenes": res.get("scenes"),
                                    "frames": res.get("frames"),
                                    "provider": res.get("provider") or LAST_PROVIDER,
                                    "work_id": rec["id"], "created_at": rec["created_at"]})
                        break
                    elif ev["type"] == "error":
                        yield _sse({"type": "error", "msg": ev["msg"]})
                        break
                    else:
                        yield _sse(ev)
            finally:
                hb.cancel()
                try:
                    await task
                except Exception:
                    pass

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no",
                                          "Connection": "keep-alive"})

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app("poster", BASE_DIR.parent / "frontend"), host="127.0.0.1", port=8000)
