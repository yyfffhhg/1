# -*- coding: utf-8 -*-
"""把最新 engine/engine.py 同步到 9 个站点的 backend/engine.py 副本。"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "engine" / "engine.py"
IDS = ["ecom", "portrait", "packaging", "poster", "home", "food", "fitting", "video", "comic"]
for sid in IDS:
    dst = ROOT / "sites" / sid / "backend" / "engine.py"
    shutil.copy(SRC, dst)
    print(f"  synced -> sites/{sid}/backend/engine.py")
print("engine 同步完成")
