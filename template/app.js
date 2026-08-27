/* 对话式前端 —— 像聊天一样创作，生成的照片以气泡展示 */
(function () {
  "use strict";
  const SITE = window.SITE || {};
  const $ = (s) => document.querySelector(s);

  // ---- 注入品牌与配色 ----
  document.documentElement.style.setProperty("--accent", SITE.accent || "#7c7bff");
  document.documentElement.style.setProperty("--accent2", SITE.accent2 || "#ff6b9d");
  document.title = SITE.brand || "AI 创作";
  $("#brand").textContent = SITE.brand || "AI";
  $("#chatTitle").textContent = (SITE.brand || "AI") + " · 创作助手";
  $("#prompt").placeholder = SITE.placeholder || "和 AI 说说你的需求，例如：清新草本助眠茶饮，熬夜上班族";
  $("#footBrand").textContent = SITE.brand || "AI 创作";

  // ---- 顶部跨站导航 (兼容网关 /s/<key>/ 与直连两种部署) ----
  const UNDER_GATEWAY = location.pathname.startsWith("/s/");
  (SITE.siblings || []).forEach((s) => {
    const a = document.createElement("a");
    if (UNDER_GATEWAY) {
      a.href = "/s/" + (s.key || s.name) + "/";
    } else {
      a.href = location.protocol + "//" + location.hostname + ":" + s.port + "/";
    }
    a.textContent = s.name;
    if (s.name === SITE.brand) a.classList.add("active");
    $("#navLinks").appendChild(a);
  });

  // ---- 示例 chips ----
  (SITE.examples || []).forEach((ex) => {
    const b = document.createElement("span");
    b.className = "ex";
    b.textContent = ex;
    b.onclick = () => { $("#prompt").value = ex; autoGrow(); $("#prompt").focus(); };
    $("#examples").appendChild(b);
  });

  // ---- 拉取 meta: 风格 / 尺寸 ----
  let state = { style: "gradient", size: null, variants: 1, category: SITE.category };
  fetch("/api/meta").then((r) => r.json()).then((meta) => {
    state.size = meta.category.sizes[0].id;
    state.isVideo = !!(meta.category && meta.category.video);
    $("#videoRow").hidden = !meta.category.video;

    const sc = $("#styleChips");
    meta.styles.forEach((st, i) => {
      const c = document.createElement("div");
      c.className = "chip" + (i === 0 ? " active" : "");
      c.textContent = st.name;
      c.onclick = () => { state.style = st.id; setActive(sc, c); };
      sc.appendChild(c);
    });
    const zc = $("#sizeChips");
    meta.category.sizes.forEach((sz, i) => {
      const c = document.createElement("div");
      c.className = "chip" + (i === 0 ? " active" : "");
      c.textContent = sz.name;
      c.onclick = () => { state.size = sz.id; setActive(zc, c); };
      zc.appendChild(c);
    });
  });

  const vc = $("#variantChips");
  vc.querySelectorAll(".chip").forEach((c) => {
    c.onclick = () => { state.variants = parseInt(c.dataset.n, 10); setActive(vc, c); };
  });

  function setActive(parent, el) {
    parent.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
    el.classList.add("active");
  }

  // ---- 设置面板开关 ----
  $("#settingsToggle").onclick = () => {
    const el = $("#chatSettings");
    el.hidden = !el.hidden;
  };

  // ---- 聊天基础 ----
  const log = $("#chatLog");
  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function addUserMsg(text) {
    const m = document.createElement("div");
    m.className = "msg user";
    m.innerHTML = `<div class="avatar">🙂</div><div class="bubble"></div>`;
    m.querySelector(".bubble").textContent = text;
    log.appendChild(m); scrollDown();
    return m;
  }

  function addAssistantLoading(text) {
    const m = document.createElement("div");
    m.className = "msg assistant";
    m.innerHTML = `<div class="avatar">✨</div><div class="bubble loading">
        <div class="spin"></div><span>${escapeHtml(text || "正在为你生成…")}</span></div>`;
    log.appendChild(m); scrollDown();
    return m;
  }

  // 把作品列表渲染进一个助手气泡 (图片网格)
  function fillAssistantImages(bubble, works, prompt, meta) {
    const n = Math.min(works.length, 4);
    let html = "";
    if (prompt) html += `<div>${escapeHtml(prompt)}</div>`;
    html += `<div class="media-grid n${n}">`;
    works.forEach((w) => {
      const src = w.src || ("data:" + (w.isVideo ? "video/mp4" : "image/png") + ";base64," + w.b64);
      const media = w.isVideo
        ? `<video src="${src}" muted loop playsinline></video>`
        : `<img src="${src}" alt="${escapeHtml(w.prompt)}" loading="lazy" />`;
      html += `<div class="shot" data-id="${w.workId || ""}" data-src="${src}" data-prompt="${escapeHtml(w.prompt)}" data-meta="${escapeHtml(w.meta)}">
          ${media}
          <div class="shot-bar">
            <button class="dl" title="下载">⬇</button>
            <button class="del" title="删除">🗑</button>
          </div>
        </div>`;
    });
    html += `</div><div class="media-meta">${escapeHtml(meta || "")}</div>`;
    bubble.classList.remove("loading");
    bubble.innerHTML = html;
    bubble.querySelectorAll(".shot").forEach((shot) => bindShot(shot));
    scrollDown();
  }

  function bindShot(shot) {
    const id = shot.dataset.id;
    const src = shot.dataset.src;
    const prompt = shot.dataset.prompt;
    const meta = shot.dataset.meta;
    shot.querySelector("img, video").onclick = () => openModal({ src, isVideo: src.startsWith("data:video") || src.endsWith(".mp4"), prompt, meta, workId: id });
    shot.querySelector(".dl").onclick = (e) => {
      e.stopPropagation();
      const a = document.createElement("a");
      a.href = src.startsWith("data:") ? src : "api/works/" + id;
      a.download = (SITE.category || "ai") + (src.indexOf("video") >= 0 ? ".mp4" : ".png");
      a.click();
    };
    shot.querySelector(".del").onclick = (e) => {
      e.stopPropagation();
      const msg = shot.closest(".msg");
      deleteWork(id, () => { if (msg) msg.remove(); });
    };
  }

  // ---- 发送 / 生成 ----
  const btn = $("#genBtn");
  let busy = false;
  async function send() {
    if (busy) return;
    const prompt = $("#prompt").value.trim();
    if (!prompt) { $("#prompt").focus(); return; }
    addUserMsg(prompt);
    $("#prompt").value = ""; autoGrow();
    const loadingMsg = addAssistantLoading("正在为你生成…");
    const bubble = loadingMsg.querySelector(".bubble");

    busy = true; btn.disabled = true; btn.textContent = "生成中…";
    try {
      if (state.isVideo) {
        await streamVideo(prompt, bubble);
      } else {
        await generateImage(prompt, bubble);
      }
    } catch (e) {
      bubble.classList.remove("loading");
      bubble.innerHTML = `😢 生成失败：${escapeHtml(e.message)}<br><span class="media-meta">请稍后重试，或检查网络</span>`;
    } finally {
      busy = false; btn.disabled = false; btn.textContent = "发送 ✈";
    }
  }

  // ---- 图片生成 (同步接口, 出图快) ----
  async function generateImage(prompt, bubble) {
    const res = await fetch("api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt, style: state.style, size: state.size,
        name: SITE.brand, variants: state.variants,
      }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const works = (data.images || []).map((im) => ({
      b64: im.image_b64, isVideo: false, prompt,
      meta: "图片 · " + (im.provider || "AI"), workId: im.work_id,
    }));
    if (data.media_b64) {
      works.push({ b64: data.media_b64, isVideo: true, prompt, meta: "视频 · " + (data.provider || "AI"), workId: data.work_id });
    }
    if (!works.length) throw new Error("未返回任何作品");
    fillAssistantImages(bubble, works, prompt, works.length + " 张作品 · " + works[0].meta);
  }

  // ---- 视频生成 (SSE 流式进度, 避免几分钟空白等待) ----
  async function streamVideo(prompt, bubble) {
    bubble.classList.remove("loading");
    bubble.innerHTML = videoProgressHTML();
    const fill = bubble.querySelector(".vp-fill");
    const stageEl = bubble.querySelector(".vp-stage");
    const titleEl = bubble.querySelector(".vp-title");
    titleEl.textContent = "视频生成中…";

    try {
      const res = await fetch("api/generate_stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt, style: state.style, size: state.size,
          name: SITE.brand, variants: 1,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error || ("HTTP " + res.status));
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
          if (ev.type === "stage") {
            stageEl.textContent = ev.msg || "生成中…";
            if (typeof ev.pct === "number") {
              fill.style.width = Math.max(3, Math.min(100, ev.pct)) + "%";
              fill.classList.remove("indeterminate");
            } else {
              fill.classList.add("indeterminate");
            }
          } else if (ev.type === "done") {
            fill.style.width = "100%";
            fill.classList.remove("indeterminate");
            renderVideoResult(bubble, ev, prompt);
            return;
          } else if (ev.type === "error") {
            throw new Error(ev.msg || "生成失败");
          }
        }
      }
      throw new Error("连接中断，未收到完成信号");
    } catch (e) {
      bubble.innerHTML = `😢 视频生成失败：${escapeHtml(e.message)}<br><span class="media-meta">请稍后重试，或检查网络</span>`;
    }
  }

  function videoProgressHTML() {
    return `<div class="video-progress">
      <div class="vp-head">
        <div class="vp-ring"></div>
        <div class="vp-title">视频生成中…</div>
      </div>
      <div class="vp-bar"><div class="vp-fill"></div></div>
      <div class="vp-stage">正在准备…</div>
      <div class="vp-sub">多镜头视频生成较慢，通常需要 1–3 分钟，请稍候 ✨</div>
    </div>`;
  }

  function renderVideoResult(bubble, ev, prompt) {
    bubble.classList.remove("loading");
    const src = "data:video/mp4;base64," + ev.media_b64;
    const isFallback = /兜底/.test(ev.provider || "");
    const meta = "视频 · " + (ev.provider || "AI");
    bubble.innerHTML = `<div class="vp-result">为你生成的视频</div>
      <div class="media-grid n1">
        <div class="shot" data-id="${ev.work_id || ""}" data-src="${src}" data-prompt="${escapeHtml(prompt)}" data-meta="${escapeHtml(meta)}">
          <video src="${src}" muted loop playsinline autoplay></video>
          <div class="shot-bar"><button class="dl" title="下载">⬇</button><button class="del" title="删除">🗑</button></div>
        </div>
      </div>
      <div class="media-meta">${escapeHtml(meta)} · ${ev.scenes || ""} 镜头${isFallback ? " · 免费模型繁忙，已用静态分镜兜底成片" : ""}</div>`;
    const shot = bubble.querySelector(".shot");
    if (shot) bindShot(shot);
    scrollDown();
  }

  btn.onclick = send;
  const ta = $("#prompt");
  ta.addEventListener("input", autoGrow);
  function autoGrow() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; }
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  // ---- 历史作品 (首屏以助手气泡形式展示已有创作) ----
  async function loadWorks() {
    try {
      const r = await fetch("api/works");
      const d = await r.json();
      const works = d.works || [];
      if (works.length === 0) {
        welcome();
        return;
      }
      works.forEach((w) => {
        const m = document.createElement("div");
        m.className = "msg assistant";
        m.innerHTML = `<div class="avatar">✨</div><div class="bubble"></div>`;
        log.appendChild(m);
        const isVideo = w.media_type && w.media_type !== "png";
        fillAssistantImages(m.querySelector(".bubble"), [{
          src: "/api/works/" + w.id, isVideo, prompt: w.prompt,
          meta: (isVideo ? "视频 · " : "图片 · ") + (w.provider || "AI"), workId: w.id,
        }], w.prompt, (isVideo ? "视频 · " : "图片 · ") + (w.provider || "AI"));
      });
      scrollDown();
    } catch (e) {
      console.warn("加载作品失败", e);
      welcome();
    }
  }

  function welcome() {
    const m = document.createElement("div");
    m.className = "msg assistant";
    m.innerHTML = `<div class="avatar">✨</div><div class="bubble">
      你好，我是你的 AI 创作助手 ✨<br>描述一句话需求，我就能帮你生成${SITE.brand || ""}作品。
      试试下方的示例，或直接在下面输入框告诉我你想要什么～
    </div>`;
    log.appendChild(m); scrollDown();
  }

  async function deleteWork(id, after) {
    if (!confirm("确定删除这张作品？")) return;
    try {
      await fetch("api/works/" + id, { method: "DELETE" });
      if (after) after();
    } catch (e) {
      alert("删除失败：" + e.message);
    }
  }

  // ---- 模态 ----
  function openModal(o) {
    const m = $("#modalMedia");
    m.innerHTML = o.isVideo
      ? `<video src="${o.src}" controls autoplay muted loop playsinline></video>`
      : `<img src="${o.src}" alt="${escapeHtml(o.prompt)}" />`;
    const dl = $("#modalDownload");
    dl.href = o.src.startsWith("data:") ? o.src : "api/works/" + o.workId;
    dl.download = (SITE.category || "ai") + (o.isVideo ? ".mp4" : ".png");
    $("#modalTitle").textContent = o.prompt;
    $("#modalMeta").textContent = o.meta;
    $("#modalDelete").onclick = () => { $("#modal").hidden = true; if (o.workId) deleteWork(o.workId, null); };
    $("#modal").hidden = false;
  }
  document.querySelectorAll("[data-close]").forEach((el) => el.onclick = () => { $("#modal").hidden = true; });

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---- 初始化 ----
  loadWorks();
  autoGrow();
})();
