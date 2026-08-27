# -*- coding: utf-8 -*-
import urllib.request, json, base64, os, time

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(__file__), "..", "research", "samples2")
os.makedirs(OUT, exist_ok=True)

cases = [
    ("poster",    "清新草本助眠茶饮，熬夜上班族", "nature", "xhs", "茶里"),
    ("portrait",  "职场女性商务写真，干练短发", "minimal", "xhs", "林清"),
    ("food",      "日式照烧鸡腿饭，热气腾腾", "gradient", "square", "和食"),
    ("fitting",   "法式碎花连衣裙，初恋感", "cyber", "xhs", "初晴"),
    ("comic",     "都市异能少女觉醒的瞬间", "cyber", "xhs", "第03幕"),
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

# quick video
t0 = time.time()
try:
    r = post({"category":"video","prompt":"新款蓝牙耳机开箱种草","style":"gradient","size":"story"})
    d = json.loads(r.read())
    ext = d.get("media_type")
    p = os.path.join(OUT, f"video.{ext}")
    open(p,"wb").write(base64.b64decode(d["media_b64"]))
    print(f"[OK] video -> {os.path.getsize(p)//1024}KB  {time.time()-t0:.1f}s frames={d.get('frames')} {p}")
except Exception as e:
    print(f"[FAIL] video: {e}")
