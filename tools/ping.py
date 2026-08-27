# -*- coding: utf-8 -*-
import urllib.request, json

ports = {8011: "ecom", 8012: "portrait", 8013: "packaging", 8014: "poster",
         8015: "home", 8016: "food", 8017: "fitting", 8018: "video", 8019: "comic"}

ok = 0
for p, name in ports.items():
    try:
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{p}/api/health", timeout=8).read())
        print(f"  {name:10s} :{p}  OK   {d}")
        ok += 1
    except Exception as e:
        print(f"  {name:10s} :{p}  FAIL {e}")
print(f"\n{ok}/9 站点健康")
