import urllib.request, time
for host in ["https://generativelanguage.googleapis.com",
             "https://image.pollinations.ai"]:
    t = time.time()
    try:
        req = urllib.request.Request(host, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=20)
        print("OK ", host, r.status, round(time.time()-t, 1), "s")
    except Exception as e:
        print("ERR", host, type(e).__name__, str(e)[:140], round(time.time()-t, 1), "s")
