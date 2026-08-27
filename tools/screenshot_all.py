# -*- coding: utf-8 -*-
"""用 Edge headless 截取全部 9 个站点首页 UI。"""
import subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "research" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")

SITES = [
    (8011, "ecom"), (8012, "portrait"), (8013, "packaging"),
    (8014, "poster"), (8015, "home"), (8016, "food"),
    (8017, "fitting"), (8018, "video"), (8019, "comic"),
]

for port, name in SITES:
    out = SHOTS / f"ui_{name}.png"
    cmd = [
        str(EDGE), "--headless", "--disable-gpu",
        "--virtual-time-budget=8000", "--window-size=1400,900",
        f"--screenshot={out}",
        f"http://127.0.0.1:{port}/",
    ]
    subprocess.run(cmd, check=False, capture_output=True)
    print(f"  {name:10s} {out.stat().st_size/1024:6.1f} KB")
    time.sleep(0.5)
print("UI 截图完成")
