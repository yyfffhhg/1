import urllib.request, time, sys

def probe(host):
    t = time.time()
    try:
        req = urllib.request.Request(host, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=15)
        print(f"OK  {host} {r.status} {round(time.time()-t,1)}s", flush=True)
    except Exception as e:
        print(f"ERR {host} {type(e).__name__} {str(e)[:120]} {round(time.time()-t,1)}s", flush=True)

print("probe cloudflare", flush=True)
probe("https://api.cloudflare.com")
print("done", flush=True)
