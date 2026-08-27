# -*- coding: utf-8 -*-
"""用真实 Gemini 实测若干品类, 校验 Key 是否生效并保存成品."""
import urllib.request, json, base64, pathlib, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = [
    (8014, "poster",   "清新草本助眠茶饮，熬夜上班族", "gradient", "xhs"),
    (8012, "portrait", "清冷高级感都市女性写真",       "minimal",  "xhs"),
    (8011, "ecom",     "北欧极简陶瓷香薰蜡烛礼盒",     "gradient", "square"),
    (8019, "comic",    "赛博朋克少女拯救城市",         "cyber",    "xhs"),
]

for port, cat, prompt, style, size in CASES:
    body = json.dumps({"prompt": prompt, "style": style, "size": size}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/generate",
        data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=240).read())
        dt = round(time.time() - t0, 1)
        if "error" in d:
            print(f"  {cat:10s} ERROR {d['error']} ({dt}s)")
            continue
        data = base64.b64decode(d["image_b64"])
        out = ROOT / f"research/samples_gemini/{cat}_{style}_{size}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        print(f"  {cat:10s} OK size={len(data)//1024}KB provider={d.get('provider')} ({dt}s) -> {out.name}")
    except Exception as e:
        print(f"  {cat:10s} FAIL {e}")
print("GEMINI_TEST_DONE")
