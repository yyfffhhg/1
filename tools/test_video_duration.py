# -*- coding: utf-8 -*-
"""端到端验证视频站: 通过 SSE 流式接口生成视频, 校验真实时长≈10秒。"""
import urllib.request, json, time, base64, subprocess, re, sys
import imageio_ffmpeg

PORT = sys.argv[1] if len(sys.argv) > 1 else "8018"
URL = f"http://127.0.0.1:{PORT}/api/generate_stream"

body = json.dumps({
    "prompt": "无线蓝牙耳机 数码产品 多镜头展示",
    "style": "gradient", "size": "story",
    "name": "时长验证", "variants": 1,
}).encode()

req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
t0 = time.time()
mp4_b64 = None
stages = 0
print(f"[start] POST {URL}")
try:
    with urllib.request.urlopen(req, timeout=400) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                d = json.loads(payload)
            except Exception:
                continue
            if d.get("type") == "stage":
                stages += 1
                msg = d.get("msg", "")
                pct = d.get("pct")
                print(f"  [stage {stages}] {msg}" + (f" ({pct}%)" if pct else ""))
            elif d.get("type") == "done":
                mp4_b64 = d.get("media_b64")
                print(f"  [done] media_type={d.get('media_type')} provider={d.get('provider')} frames={d.get('frames')}")
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}")
    sys.exit(1)

elapsed = time.time() - t0
if not mp4_b64:
    print(f"[FAIL] 无 media_b64, 用时 {elapsed:.1f}s")
    sys.exit(1)

data = base64.b64decode(mp4_b64)
out = "C:/Users/Administrator/WorkBuddy/2026-08-17-06-41-29/ai-visual-studio/tools/_test_video.mp4"
with open(out, "wb") as f:
    f.write(data)
print(f"[saved] {out} 大小={len(data)/1024/1024:.2f}MB 用时={elapsed:.1f}s")

# 用 ffmpeg 探针时长
ff = imageio_ffmpeg.get_ffmpeg_exe()
try:
    p = subprocess.run([ff, "-i", out], capture_output=True, text=True, timeout=60)
    txt = p.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", txt)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        dur = h * 3600 + mi * 60 + s
        print(f"[duration] {dur:.2f}s  (期望≈10s)")
    else:
        # 兜底: 用帧数/帧率估算
        mf = re.search(r", (\d+) fps", txt)
        print(f"[duration] 未解析到 Duration, ffmpeg输出尾部:\n" + txt[-400:])
except Exception as e:
    print(f"[probe error] {e}")
