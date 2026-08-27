# AI 视觉工坊 —— 单容器部署 (9 个生图站 + 视频站 + 统一网关 8080)
# 适合 Railway / Render / Fly.io / 任意 Docker 主机
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GATEWAY_PORT=8080

WORKDIR /app

# 依赖安装 (ffmpeg 由 imageio-ffmpeg 自带, 无需系统安装)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 用安全默认 .env.example 生成 .env (不含真实 Key);
# 云平台注入的环境变量会经 engine 的 os.environ.setdefault 自动覆盖它们。
RUN for d in sites/*/backend; do \
      if [ -f "$d/.env.example" ] && [ ! -f "$d/.env" ]; then \
        cp "$d/.env.example" "$d/.env"; \
      fi; \
    done

# 仅暴露网关端口; 9 个站点端口只在容器内网互通
EXPOSE 8080

# 一键拉起 9 站 + 网关 (网关为阻塞主进程, 容器会随它保持运行)
CMD ["python", "start_all.py"]
