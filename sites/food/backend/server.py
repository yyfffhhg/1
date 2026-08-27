# -*- coding: utf-8 -*-
"""单品类应用入口 —— 自动生成, 请勿手改引擎 (engine.py 为共享副本)。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # 让 engine 可导入

from engine import build_app

CATEGORY = "food"
PORT = 8016
FE = HERE.parent / "frontend"

app = build_app(CATEGORY, FE)

if __name__ == "__main__":
    import uvicorn
    print(f"[{CATEGORY}] 启动 {CATEGORY} -> http://0.0.0.0:{PORT}/")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
