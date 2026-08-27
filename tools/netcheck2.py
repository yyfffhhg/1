import urllib.request, time, sys

def probe(host):
    t = time.time()
    try:
        req = urllib.request.Request(host, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=15)
        msg = f"OK  {host} {r.status} {round(time.time()-t,1)}s"
    except Exception as e:
        msg = f"ERR {host} {type(e).__name__} {str(e)[:120]} {round(time.time()-t,1)}s"
    print(msg, flush=True)

print("start probing", flush=True)
probe("https://image.pollinations.ai")
probe("https://generativelanguage.googleapis.com")
print("done", flush=True)
