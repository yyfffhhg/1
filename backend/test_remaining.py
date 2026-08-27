# -*- coding: utf-8 -*-
import urllib.request, json, base64, os, time

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(__file__), "..", "research", "samples2")
os.makedirs(OUT, exist_ok=True)

cases = [
    ("packaging","高端每日坚果礼盒", "retro", "square", "每日优"),
    ("home",     "现代极简风客厅，原木色调", "nature", "wide", "栖屋"),
]

def post(payload):
    req = urllib.request.Request(BASE + "/api/generate",
        data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
    return urllib.request.urlopen(req, timeout=200)

for cat, prompt, style, size, name in cases:
    t0 = time.time()
    try:
        r = post({"category":cat,"prompt":prompt,"style":style,"size":size,"name":name})
        d = json.loads(r.read())
        p = os.path.join(OUT, f"{cat}_{style}_{size}.png")
        open(p,"wb").write(base64.b64decode(d["image_b64"]))
        print(f"[OK] {cat:10s} -> {os.path.getsize(p)//1024}KB  {time.time()-t0:.1f}s  {p}")
    except Exception as e:
        print(f"[FAIL] {cat}: {e}")
