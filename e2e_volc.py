import httpx, json, time, base64

GATEWAY = "http://127.0.0.1:8080"
URL = f"{GATEWAY}/s/video/api/generate_stream"
BODY = {"prompt": "国潮风茶饮品牌宣传短片，产品特写，暖光", "style": "gradient", "size": "wide", "name": "", "variants": 1}

t0 = time.time()
provider = None
stages = []
with httpx.Client(timeout=300, verify=False) as c:
    with c.stream("POST", URL, json=BODY) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                t = ev.get("type")
                if t == "provider":
                    print("PROVIDER:", ev.get("msg"))
                elif t == "stage":
                    stages.append(ev.get("stage"))
                    print("stage:", ev.get("stage"), "-", (ev.get("msg") or "")[:50])
                elif t == "done":
                    provider = ev.get("provider")
                    b = len(base64.b64decode(ev["media_b64"]))
                    print(f"DONE provider={provider} media={ev.get('media_type')} bytes={b} scenes={ev.get('scenes')}")
                    with open("volc_site_out.mp4", "wb") as f:
                        f.write(base64.b64decode(ev["media_b64"]))
                elif t == "error":
                    print("ERROR:", ev.get("msg"))
print("elapsed=%.1fs final_provider=%s" % (time.time() - t0, provider))
