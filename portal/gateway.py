# -*- coding: utf-8 -*-
"""
统一网关 (单端口反向代理)
=================================
把 9 个独立站点 + 视频站 全部收拢到 8080 这一个端口:
  - GET  /                       -> 门户 portal/index.html
  - GET/POST ... /s/<key>/...    -> 反向代理到各站点 127.0.0.1:<port>
  - 支持 SSE 流式 (视频进度) 与大文件透传

这样做的好处: 公网隧道 / 云部署只需暴露 8080 一个端口, 其余站点对内网隐藏。
"""
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import httpx

ROOT = Path(__file__).resolve().parent.parent
PORTAL_DIR = ROOT / "portal"

# key -> 端口, 必须与 tools/gen_sites.py 中 SITES 完全一致
SITES = {
    "ecom": 8011, "portrait": 8012, "packaging": 8013, "poster": 8014,
    "home": 8015, "food": 8016, "fitting": 10817, "video": 8018, "comic": 8019,
}

UPSTREAM = {k: f"http://127.0.0.1:{v}" for k, v in SITES.items()}

app = FastAPI(title="AI 创意工坊网关")

# 复用连接池, 避免每次请求新建 TCP
_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0), follow_redirects=False)


@app.get("/")
async def index():
    html = (PORTAL_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/health")
async def health():
    return {"ok": True, "gateway": True, "sites": list(SITES.keys())}


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(status_code=204, content={})


@app.api_route(
    "/s/{key:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy(key: str, request: Request):
    parts = key.split("/", 1)
    site = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if site not in UPSTREAM:
        return JSONResponse({"error": f"未知模块: {site}"}, status_code=404)

    upstream = UPSTREAM[site]
    path = "/" + rest if rest else "/"
    url = upstream + path
    if request.url.query:
        url += "?" + request.url.query

    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "connection", "transfer-encoding", "content-length")
    }

    try:
        # 注意: 不能用 `async with client.stream() as resp` 后直接返回 StreamingResponse,
        # 否则 with 块退出会先关闭上游响应, 导致流式主体被截断。
        # 这里改用 send(stream=True) 拿到响应对象, 在生成器内部逐块吐出并最后关闭。
        upstream_req = _CLIENT.build_request(
            request.method, url, headers=headers, content=body
        )
        upstream_resp = await _CLIENT.send(upstream_req, stream=True)
        excluded = {"transfer-encoding", "connection", "keep-alive", "content-length"}
        resp_headers = {
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in excluded
        }

        async def _stream():
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type=upstream_resp.headers.get("content-type"),
        )
    except httpx.HTTPError as e:
        return JSONResponse(
            {"error": f"上游「{site}」未运行或不可达: {e}"}, status_code=502
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("GATEWAY_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
