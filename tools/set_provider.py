# -*- coding: utf-8 -*-
"""把 9 个站点的 backend/.env 预置为指定生图源 (默认 gemini)。
若 .env 不存在则从 .env.example 生成; 仅改写 IMAGE_PROVIDER 一行。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = ["ecom", "portrait", "packaging", "poster", "home", "food", "fitting", "video", "comic"]
PROVIDER = os.getenv("TARGET_PROVIDER", "gemini")

for sid in SITES:
    bdir = os.path.join(ROOT, "sites", sid, "backend")
    example = os.path.join(bdir, ".env.example")
    target = os.path.join(bdir, ".env")
    src = open(example, encoding="utf-8").read() if os.path.exists(example) else ""
    lines = []
    replaced = False
    for ln in src.splitlines():
        if ln.startswith("IMAGE_PROVIDER="):
            lines.append(f"IMAGE_PROVIDER={PROVIDER}")
            replaced = True
        else:
            lines.append(ln)
    if not replaced:
        lines.insert(0, f"IMAGE_PROVIDER={PROVIDER}")
    open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"  {sid:10s} -> IMAGE_PROVIDER={PROVIDER}  ({target})")
print("done")
