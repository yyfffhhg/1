# -*- coding: utf-8 -*-
"""
gen_sites.py —— 一键生成 9 个独立的 AI 生图网站
===============================================
每个站点:
  sites/<id>/backend/engine.py   (共享引擎副本, 自包含)
  sites/<id>/backend/server.py   (单品类应用, 独立端口)
  sites/<id>/frontend/{index.html, style.css, app.js, config.js}
  sites/<id>/start.bat
  sites/<id>/README.md
并生成 sites/index.html 门户导航页 (汇总 9 个站点)。
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ai-visual-studio
ENGINE = ROOT / "engine" / "engine.py"
TEMPLATE = ROOT / "template"
SITES_DIR = ROOT / "sites"

# ---------------------------------------------------------------------------
# 9 个站点配置 (品牌 / slogan / 配色 / 示例 / 端口)
# ---------------------------------------------------------------------------
SITES = [
    dict(id="ecom", brand="电商生图", port=8011,
         slogan="商品主图 / 详情图 / 白底图，一句话出片",
         heroTitle="电商商品图，一句话生成",
         accent="#ff7a18", accent2="#ffb347",
         placeholder="描述商品，如：北欧风陶瓷餐具套装，白色系",
         examples=["北欧风陶瓷餐具套装", "夏季真丝连衣裙", "无线蓝牙耳机礼盒"]),
    dict(id="portrait", brand="AI写真馆", port=8012,
         slogan="高定风格人像写真，一键出片",
         heroTitle="专属 AI 写真，即刻拥有",
         accent="#7c7bff", accent2="#ff6b9d",
         placeholder="描述人物，如：温柔知性女生，暖阳窗边",
         examples=["温柔知性女生", "复古港风男士", "清新日系少女"]),
    dict(id="packaging", brand="AI包装设计", port=8013,
         slogan="产品包装 / 礼盒效果图，一键渲染",
         heroTitle="包装设计，所见即所得",
         accent="#2a9d5c", accent2="#d4af37",
         placeholder="描述包装，如：国潮风茶叶礼盒，红色主调",
         examples=["国潮风茶叶礼盒", "极简护肤品瓶身", "儿童零食包装"]),
    dict(id="poster", brand="AI海报工厂", port=8014,
         slogan="营销海报，一句话秒出",
         heroTitle="营销海报，一句话搞定",
         accent="#3b6cff", accent2="#7c3aed",
         placeholder="描述主题，如：新品首发促销海报，科技感",
         examples=["新品首发促销海报", "618大促主视觉", "知识付费课程海报"]),
    dict(id="home", brand="AI家装设计", port=8015,
         slogan="室内空间效果图，所见即所得",
         heroTitle="家装灵感，AI 一键呈现",
         accent="#0ea5a4", accent2="#22c55e",
         placeholder="描述空间，如：现代简约客厅，原木地板",
         examples=["现代简约客厅", "北欧风卧室", "日式茶室"]),
    dict(id="food", brand="AI餐饮视觉", port=8016,
         slogan="美食菜品视觉，勾起食欲",
         heroTitle="餐饮视觉，勾起食欲",
         accent="#ff5e3a", accent2="#ff9500",
         placeholder="描述菜品，如：日式拉面特写，热气腾腾",
         examples=["日式拉面特写", "网红甜品摆盘", "烧烤夜市烟火气"]),
    dict(id="fitting", brand="AI试衣间", port=10817,
         slogan="模特上身 / 穿搭效果，先试再买",
         heroTitle="AI 试衣，先试再买",
         accent="#ec4899", accent2="#8b5cf6",
         placeholder="描述穿搭，如：春季风衣穿搭，米色长款",
         examples=["春季风衣穿搭", "运动休闲套装", "晚礼服造型"]),
    dict(id="video", brand="电商一键生视频", port=8018,
         slogan="多镜头运镜，商品自动成片",
         heroTitle="商品短视频，一键成片",
         accent="#06b6d4", accent2="#3b82f6",
         placeholder="描述商品，如：智能手表宣传短片，科技感",
         examples=["智能手表宣传短片", "护肤品使用场景", "咖啡冲煮过程"]),
    dict(id="comic", brand="AI漫剧", port=8019,
         slogan="漫画分镜 / 同款二创，一键生成",
         heroTitle="AI 漫剧，一键出片",
         accent="#a21caf", accent2="#22d3ee",
         placeholder="描述剧情，如：都市异能题材，雨夜街头对决",
         examples=["都市异能题材", "古风仙侠场景", "校园逆袭剧情"]),
]

SERVER_TPL = '''# -*- coding: utf-8 -*-
"""单品类应用入口 —— 自动生成, 请勿手改引擎 (engine.py 为共享副本)。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # 让 engine 可导入

from engine import build_app

CATEGORY = "{category}"
PORT = {port}
FE = HERE.parent / "frontend"

app = build_app(CATEGORY, FE)

if __name__ == "__main__":
    import uvicorn
    print(f"[{{CATEGORY}}] 启动 {{CATEGORY}} -> http://0.0.0.0:{{PORT}}/")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
'''

START_BAT = '''@echo off
cd /d "%~dp0"
"%~dp0..\\..\\.venv\\Scripts\\python.exe" backend\\server.py
pause
'''


def write_config(site, siblings):
    import json
    cfg = {
        "category": site["id"],
        "brand": site["brand"],
        "badge": "AI 智能生成",
        "heroTitle": site["heroTitle"],
        "slogan": site["slogan"],
        "placeholder": site["placeholder"],
        "accent": site["accent"],
        "accent2": site["accent2"],
        "examples": site["examples"],
        "siblings": siblings,
    }
    return "window.SITE = " + json.dumps(cfg, ensure_ascii=False, indent=2) + ";\n"


def gen_one(site, siblings):
    base = SITES_DIR / site["id"]
    be = base / "backend"
    fe = base / "frontend"
    be.mkdir(parents=True, exist_ok=True)
    fe.mkdir(parents=True, exist_ok=True)

    # 引擎副本 (自包含)
    shutil.copy(ENGINE, be / "engine.py")
    # server 入口
    (be / "server.py").write_text(
        SERVER_TPL.format(category=site["id"], port=site["port"]), encoding="utf-8")
    # 前端模板
    shutil.copy(TEMPLATE / "index.html", fe / "index.html")
    shutil.copy(TEMPLATE / "style.css", fe / "style.css")
    shutil.copy(TEMPLATE / "app.js", fe / "app.js")
    # 站点专属 config
    (fe / "config.js").write_text(write_config(site, siblings), encoding="utf-8")
    # 启动脚本
    (base / "start.bat").write_text(START_BAT, encoding="utf-8")
    # README
    (base / "README.md").write_text(
        f"# {site['brand']}\n\n"
        f"- 描述：{site['slogan']}\n"
        f"- 访问：http://127.0.0.1:{site['port']}/\n"
        f"- 启动：双击 `start.bat`\n"
        f"- 引擎：FLUX 免Key生图 + PIL 服务端中文排版（engine.py 副本）\n",
        encoding="utf-8")
    print(f"  ✓ {site['brand']:8s} -> sites/{site['id']}  (port {site['port']})")


ICONS = {
    "ecom": "🛍️", "portrait": "📸", "packaging": "📦", "poster": "🖼️", "home": "🛋️",
    "food": "🍜", "fitting": "👗", "video": "🎞️", "comic": "🎬",
}


def gen_portal():
    cards = ""
    for s in SITES:
        icon = ICONS.get(s["id"], "✨")
        cards += (
            f'    <a class="scard" href="http://127.0.0.1:{s["port"]}/" '
            f'style="--accent:{s["accent"]};--accent2:{s["accent2"]}">\n'
            f'      <div class="scover"><div class="sicon">{icon}</div>'
            f'<img src="http://127.0.0.1:{s["port"]}/static/showcase/1.png" '
            f'onerror="this.style.display=\'none\'"></div>\n'
            f'      <div class="sbody"><div class="sname">{s["brand"]}</div>'
            f'<div class="sdesc">{s["slogan"]}</div>\n'
            f'        <div class="sfoot"><span class="slink">{s["port"]}</span>'
            f'<span class="sgo">进入 →</span></div></div>\n'
            f'    </a>\n'
        )
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 生图系列 · 9 站门户</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#f3f4ff;min-height:100vh;
    background:radial-gradient(1200px 700px at 15% -10%,rgba(124,123,255,.25),transparent 60%),
               radial-gradient(1000px 600px at 90% 0%,rgba(255,107,157,.2),transparent 60%),
               linear-gradient(160deg,#0b0d1f,#141637)}}
  .wrap{{max-width:1180px;margin:0 auto;padding:56px 24px}}
  .head{{text-align:center;margin-bottom:14px}}
  h1{{font-size:38px;font-weight:800;background:linear-gradient(90deg,#7c7bff,#ff6b9d);-webkit-background-clip:text;background-clip:text;color:transparent}}
  .sub{{color:#9aa0c0;margin:12px 0 6px;font-size:15px}}
  .tip{{color:#9aa0c0;font-size:13px;margin-bottom:34px;text-align:center}}
  .tip code{{background:rgba(255,255,255,.08);padding:2px 8px;border-radius:6px;color:#ffd479}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:18px}}
  .scard{{display:block;text-decoration:none;color:inherit;background:rgba(255,255,255,.06);
    border:1px solid rgba(255,255,255,.12);border-radius:18px;overflow:hidden;transition:.22s}}
  .scard:hover{{transform:translateY(-5px);border-color:var(--accent)}}
  .scover{{position:relative;aspect-ratio:16/10;background:linear-gradient(135deg,#1a1d3a,#23264f);overflow:hidden}}
  .scover img{{width:100%;height:100%;object-fit:cover;display:block}}
  .sicon{{position:absolute;top:12px;left:12px;width:42px;height:42px;border-radius:12px;
    display:grid;place-items:center;font-size:20px;background:linear-gradient(100deg,var(--accent),var(--accent2));box-shadow:0 6px 18px rgba(0,0,0,.4)}}
  .sbody{{padding:16px 18px 18px}}
  .sname{{font-size:18px;font-weight:700;margin-bottom:6px}}
  .sdesc{{font-size:13px;color:#9aa0c0;line-height:1.55;margin-bottom:12px;min-height:38px}}
  .sfoot{{display:flex;align-items:center;justify-content:space-between}}
  .slink{{font-size:12px;color:var(--accent)}}
  .sgo{{font-size:12.5px;font-weight:600;color:#fff;background:linear-gradient(100deg,var(--accent),var(--accent2));padding:7px 14px;border-radius:10px}}
  .foot{{text-align:center;color:#9aa0c0;font-size:12.5px;margin-top:46px}}
</style></head>
<body><div class="wrap">
  <div class="head"><h1>AI 生图系列 · 9 站门户</h1>
    <div class="sub">每个品类一个独立网站，对标 AI 海报工厂质量 · 由硅基流动免费生图 + PIL 本地排版引擎驱动</div></div>
  <div class="tip">全部站点本地运行，浏览器访问对应端口即可使用（如 <code>http://127.0.0.1:8014/</code>）</div>
  <div class="grid">
{cards}  </div>
  <div class="foot">本地系列站点 · 由 FLUX + 硅基流动 + PIL 本地引擎驱动</div>
</div></body></html>'''
    (SITES_DIR / "index.html").write_text(html, encoding="utf-8")
    print("  ✓ 门户 sites/index.html")


def main():
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    siblings = [{"name": s["brand"], "port": s["port"], "key": s["id"]} for s in SITES]
    print("生成 9 个独立站点:")
    for s in SITES:
        gen_one(s, siblings)
    gen_portal()
    print("完成。门户: sites/index.html")


if __name__ == "__main__":
    main()
