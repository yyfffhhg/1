# -*- coding: utf-8 -*-
"""为每个站点生成 6 张风格灵感展示图 -> sites/<id>/frontend/showcase/1..6.png。
展示图用于首页"风格灵感"区块 (前端 loadShowcase 自动加载, 缺失则隐藏)。"""
import asyncio
import re
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDS = ["ecom", "portrait", "packaging", "poster", "home", "food", "fitting", "video", "comic"]


def get_examples(fe: Path):
    txt = (fe / "config.js").read_text(encoding="utf-8")
    m = re.search(r'"examples"\s*:\s*\[(.*?)\]', txt, re.S)
    if not m:
        return ["AI 生成示例"]
    found = re.findall(r'"([^"]*)"', m.group(1))
    return found or ["AI 生成示例"]


def load_engine(sid: str):
    be = ROOT / "sites" / sid / "backend"
    spec = importlib.util.spec_from_file_location("eng_" + sid, str(be / "engine.py"))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    return eng


for sid in IDS:
    eng = load_engine(sid)
    fe = ROOT / "sites" / sid / "frontend"
    showcase = fe / "showcase"
    showcase.mkdir(exist_ok=True)
    examples = get_examples(fe)
    styles = eng.CATEGORY_STYLES.get(sid, list(eng.STYLES.keys()))
    cat = eng.CATEGORIES[sid]
    size = cat["sizes"][0]
    for i in range(6):
        prompt_cn = examples[i % len(examples)]
        style = styles[i % len(styles)]
        try:
            if sid == "video":
                flx = f"cinematic e-commerce product shot of {prompt_cn}, dramatic lighting, premium render, no text"
                art = asyncio.run(eng.gen_artwork(flx, size))
                (showcase / f"{i+1}.png").write_bytes(art)
            else:
                flx = cat["prompt"](prompt_cn, style)
                art = asyncio.run(eng.gen_artwork(flx, size))
                png = eng.COMPOSERS[sid](art, prompt_cn, prompt_cn, style, size)
                (showcase / f"{i+1}.png").write_bytes(png)
            print(f"  ✓ {sid} showcase {i+1} ({style})")
        except Exception as e:
            print(f"  ✗ {sid} showcase {i+1} 失败: {e}")
    print(f"完成 {sid} 展示图 -> {showcase}")
print("全部展示图生成完毕")
