# 部署指南 · AI 视觉工坊（9 站 + 视频 + 统一网关）

一个单端口（默认 `8080`）即可对外提供全部 9 个生图站 + 视频站的网站。
架构：9 个独立站点（各自端口）经 `portal/gateway.py` 反向代理收拢到 `http://<host>:8080`，
公网隧道 / 云部署只需暴露 `8080` 一个端口。

---

## 1. 本地一键运行

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows；Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python start_all.py
```

浏览器打开 `http://127.0.0.1:8080/` 即门户，点卡片进入各站。
（`.env` 已配好免费生图源，开箱即用；视频默认 `kenburns` 静态兜底，即时出片。）

---

## 2. Docker 本地运行

```bash
docker build -t ai-visual-studio .
docker run -p 8080:8080 ai-visual-studio
```

镜像内用 `sites/*/backend/.env.example` 生成安全默认 `.env`（不含真实 Key）。
要启用真实视频模型，运行时注入环境变量即可（见第 5 节）。

---

## 3. 部署到免费云平台（拿到永久公网域名）

三个平台都支持「连 Git 仓库 → 自动构建 → 给一个永久 https 域名」，比 SSH 隧道更稳。

### 3.1 Railway
1. 登录 railway.app → New Project → Deploy from GitHub repo。
2. 构建命令留空（Dockerfile 自动识别），启动命令自动用 Dockerfile 的 `CMD`。
3. 在 Variables 里加 `GATEWAY_PORT=8080`（Railway 会同时提供自己的 `PORT`，两者都设为 8080）。
4. 需要真实视频/生图 Key 时，在 Variables 添加对应变量（见第 5 节）。
5. Deploy → 得到 `https://<项目>.up.railway.app` 永久域名。

### 3.2 Render
1. 登录 render.com → New → Web Service → 关联仓库。
2. Runtime 选 `Docker`，其余默认。
3. 在 Environment 里加 `GATEWAY_PORT=8080`（Render 注入 `PORT`，把两者都设 8080）。
4. 添加视频/生图 Key 变量。
5. Create Web Service → 得到 `https://<服务>.onrender.com` 永久域名。

### 3.3 Fly.io
```bash
fly launch        # 选 Dockerfile，端口填 8080
fly deploy
fly scale count 1
```
在 `fly.toml` 已自动监听 8080；如需变量：`fly secrets set VIDEO_PROVIDER=auto ZHIPU_API_KEY=xxx`。

> 提示：云平台的 `PORT` 环境变量若不是 8080，把 `GATEWAY_PORT` 同步设成平台给的端口即可（网关读取 `GATEWAY_PORT`）。

---

## 4. 临时公网（免账号，立即分享）

不部署也能让所有人打开——用 SSH 反向隧道（需本机保持运行）：

```bash
# serveo.net（免费，无需下载客户端；自定义子域名需先注册 SSH Key）
ssh -R 80:localhost:8080 serveo.net
# 终端会回显一个 https 公网地址，转发到本机 8080
```

> 注意：免费隧道地址在每次重连时会变化；自定义易记子域名需先 `ssh-keygen` 并向 serveo 注册 Key。
> 若 serveo 不可达，可换 `ssh -R 80:localhost:8080 localhost.run`（localhost.run 现也要求 SSH Key）。

---

## 5. 视频模型配置（核心：接入多家视频大模型）

视频站 `sites/video/backend/.env` 的 `VIDEO_PROVIDER` 控制走哪条链路：

| 取值 | 行为 |
|------|------|
| `kenburns` | 免 Key 静态分镜兜底（真实 AI 图 + 缓推镜头），**即时出片**，公网演示推荐 |
| `auto` | 自动串联**所有已填 Key** 的视频模型（火山Seedance/智谱/万相/可灵/海螺/混元/Pollinations），全失败回退 kenburns |
| `zhipu` / `dashscope` / `kling` / `minimax` / `hunyuan` / `pollinations` / `volcengine` | 该模型优先，其余已填 Key 的模型兜底 |

在云平台「环境变量」或本地 `.env` 中填入对应 Key（只填想用的；引擎按可用 Key 自动编排）：

```
VIDEO_PROVIDER=auto
VOLC_API_KEY=xxx             # 火山引擎 Seedance (doubao-seedance-1-0-pro-fast-251015)
ZHIPU_API_KEY=xxx            # 智谱清影 CogVideoX
DASHSCOPE_API_KEY=xxx        # 阿里通义万相
KLING_API_KEY=xxx            # 可灵 Kling
MINIMAX_API_KEY=xxx          # 海螺 Minimax
HUNYUAN_API_KEY=xxx          # 腾讯混元
POLLINATIONS_API_KEY=xxx     # Pollinations 视频
```

生图源同理：`IMAGE_PROVIDER` 默认 `siliconflow`（填 `SF_API_KEY` 升级为硅基流动免费生图；
不填则自动回退免费 Pollinations FLUX）。

---

## 6. 端口与结构

| 端口 | 服务 |
|------|------|
| 8080 | 统一网关（对外唯一端口） |
| 8011–8019 | ecom / portrait / packaging / poster / home / food / video / comic |
| 10817 | fitting（原 8017 与 Doubao 桌面端端口冲突，已迁移） |

网关 `/s/<key>/...` 反向代理到各站；`/api/health` 返回站点清单。
