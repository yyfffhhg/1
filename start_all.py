# -*- coding: utf-8 -*-
"""一键启动: 拉起 9 个站点 + 统一网关(8080)。

- 启动前清理已占用端口的旧进程, 避免重复实例。
- 网关在 8080 提供单一入口: 内部用反向代理把各站点(各自端口)收拢到 /s/<key>/,
  用户只需记一个地址; 公网隧道 / 云部署也只需暴露 8080 一个端口。
"""
import os
import sys
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable  # 用当前解释器, 兼容 venv 与 Docker/Linux
PORTAL_DIR = ROOT / "portal"
# 云平台(Render/Railway/Fly)会注入 PORT; 没注入时回退 GATEWAY_PORT, 再回退 8080
GATEWAY_PORT = int(os.environ.get("PORT", os.environ.get("GATEWAY_PORT", "8080")))

SITES = [
    ("ecom", 8011), ("portrait", 8012), ("packaging", 8013), ("poster", 8014),
    ("home", 8015), ("food", 8016), ("fitting", 10817), ("video", 8018), ("comic", 8019),
]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def kill_old(port: int):
    """通过 netstat + taskkill 清理占用端口的进程(Windows)。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=20
        ).stdout
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit():
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   capture_output=True, timeout=20)
    except Exception as e:
        print(f"[warn] 清理端口 {port} 失败: {e}")


def start_site(cat: str, port: int):
    if port_in_use(port):
        print(f"[skip] {cat} 端口 {port} 已在运行")
        return
    p = subprocess.Popen(
        [PY, str(ROOT / f"sites/{cat}/backend/server.py")],
        cwd=str(ROOT / f"sites/{cat}"),
    )
    print(f"[start] {cat} -> http://127.0.0.1:{port}/  (pid={p.pid})")


def main():
    print("=== 启动 9 个站点 ===")
    for cat, port in SITES:
        start_site(cat, port)

    print(f"=== 启动统一网关 -> http://0.0.0.0:{GATEWAY_PORT}/ ===")
    if port_in_use(GATEWAY_PORT):
        kill_old(GATEWAY_PORT)
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", str(GATEWAY_PORT)],
        cwd=str(PORTAL_DIR),
    )
    print(f"[ok] 网关已就绪: http://0.0.0.0:{GATEWAY_PORT}/  (pid={proc.pid})")
    print("     局域网 / 公网隧道访问此端口即可。按 Ctrl+C 退出。")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("[stop] 关闭网关")


if __name__ == "__main__":
    main()
