# -*- coding: utf-8 -*-
"""为 8 个图像站点各生成一张验证成品, 保存到 sites/<id>/sample.png。"""
import urllib.request, json, base64, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITES = [
    ("ecom", 8011, "北欧风陶瓷餐具套装", "gradient"),
    ("portrait", 8012, "温柔知性女生", "minimal"),
    ("packaging", 8013, "国潮风茶叶礼盒", "retro"),
    ("poster", 8014, "新品首发促销海报", "gradient"),
    ("home", 8015, "现代简约客厅", "nature"),
    ("food", 8016, "日式拉面特写", "gradient"),
    ("fitting", 8017, "春季风衣穿搭", "gradient"),
    ("comic", 8019, "都市异能题材", "cyber"),
]

for cat, port, prompt, style in SITES:
    body = json.dumps({"prompt": prompt, "style": style, "name": cat}).encode()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/generate",
                                     data=body, headers={"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=200).read())
        if "error" in d:
            print(f"  {cat:10s} ERROR {d['error']}")
            continue
        png = base64.b64decode(d["image_b64"])
        out = ROOT / "sites" / cat / "sample.png"
        out.write_bytes(png)
        print(f"  {cat:10s} OK  {out.name} ({len(png)//1024} KB)")
    except Exception as e:
        print(f"  {cat:10s} FAIL {e}")
print("图像样本生成完毕")

# ---- 视频站点 ----
try:
    body = json.dumps({"prompt": "智能手表宣传短片", "style": "gradient", "name": "video"}).encode()
    req = urllib.request.Request("http://127.0.0.1:8018/api/generate",
                                 data=body, headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=900).read())
    if "error" in d:
        print(f"  video      ERROR {d['error']}")
    else:
        data = base64.b64decode(d["media_b64"])
        ext = d["media_type"]
        out = ROOT / "sites" / "video" / f"sample.{ext}"
        out.write_bytes(data)
        print(f"  video      OK  sample.{ext} ({len(data)//1024} KB, {d['frames']} 帧)")
except Exception as e:
    print(f"  video      FAIL {e}")
print("全部样本生成完毕")
