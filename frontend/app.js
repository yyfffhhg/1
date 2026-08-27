// 电商生图 Studio - 前端逻辑
const API = "";
let META = { categories: [], styles: [], sizes: [] };
let state = { category: "poster", style: "gradient", size: "xhs" };

const HINTS = {
  poster:   "例如：清新草本助眠茶饮，熬夜上班族",
  portrait: "例如：职场女性商务写真，干练短发",
  packaging:"例如：高端每日坚果礼盒",
  home:     "例如：现代极简风客厅，原木色调",
  food:     "例如：日式照烧鸡腿饭，热气腾腾",
  fitting:  "例如：法式碎花连衣裙，初恋感",
  comic:    "例如：都市异能少女觉醒的瞬间",
  video:    "例如：新款蓝牙耳机开箱种草短片",
};

async function loadMeta() {
  const r = await fetch(API + "/api/meta");
  META = await r.json();
  renderCats();
  selectCat("poster");
}

function renderCats() {
  const nav = document.getElementById("catnav");
  nav.innerHTML = "";
  META.categories.forEach((c) => {
    const el = document.createElement("div");
    el.className = "cat" + (c.id === state.category ? " active" : "");
    el.innerHTML = `<span class="ci">${c.icon}</span>${c.name}`;
    el.onclick = () => selectCat(c.id);
    nav.appendChild(el);
  });
}

function catById(id) { return META.categories.find((c) => c.id === id); }

function selectCat(id) {
  state.category = id;
  const c = catById(id);
  state.style = "gradient";
  state.size = c.sizes[0];
  document.getElementById("catIcon").textContent = c.icon;
  document.getElementById("catName").textContent = c.name;
  document.getElementById("catDesc").textContent = c.desc;
  document.getElementById("prompt").placeholder = HINTS[id] || "输入描述…";
  document.getElementById("hint").textContent = c.video
    ? "提示：视频品类将生成多镜头运镜短片（约 20–40 秒），请耐心等待。"
    : "提示：描述越具体，画面越精准。可填品牌/名称做署名。";
  // 风格
  const sc = document.getElementById("styleChips");
  sc.innerHTML = "";
  META.styles.forEach((s) => {
    const el = document.createElement("div");
    el.className = "chip" + (s.id === state.style ? " active" : "");
    el.textContent = s.name;
    el.onclick = () => { state.style = s.id; renderChips(); };
    sc.appendChild(el);
  });
  // 尺寸
  const zc = document.getElementById("sizeChips");
  zc.innerHTML = "";
  META.sizes.filter((s) => c.sizes.includes(s.id)).forEach((s) => {
    const el = document.createElement("div");
    el.className = "chip" + (s.id === state.size ? " active" : "");
    el.textContent = `${s.name} · ${s.w}×${s.h}`;
    el.onclick = () => { state.size = s.id; renderChips(); };
    zc.appendChild(el);
  });
  // 高亮导航
  [...document.querySelectorAll(".cat")].forEach((e, i) =>
    e.classList.toggle("active", META.categories[i].id === id));
}

function renderChips() {
  [...document.querySelectorAll("#styleChips .chip")].forEach((e, i) =>
    e.classList.toggle("active", META.styles[i].id === state.style));
  const c = catById(state.category);
  const zlist = META.sizes.filter((s) => c.sizes.includes(s.id));
  [...document.querySelectorAll("#sizeChips .chip")].forEach((e, i) =>
    e.classList.toggle("active", zlist[i].id === state.size));
}

function b64toBlob(b64, type) {
  const bin = atob(b64);
  const len = bin.length;
  const arr = new Uint8Array(len);
  for (let i = 0; i < len; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type });
}

function buildMedia(res, c) {
  // 返回 {src, isVideo, type, blobUrl}
  if (res.media_type) {
    const type = res.media_type === "mp4" ? "video/mp4" : "image/gif";
    const blob = b64toBlob(res.media_b64, type);
    const url = URL.createObjectURL(blob);
    return { src: url, isVideo: res.media_type === "mp4", type, blobUrl: url };
  }
  const src = "data:image/png;base64," + res.image_b64;
  return { src, isVideo: false, type: "image/png", dataUrl: src };
}

function renderCard(res, c, isSkeleton) {
  const gallery = document.getElementById("gallery");
  const empty = document.getElementById("empty");
  if (empty) empty.remove();
  const card = document.createElement("div");
  card.className = "card" + (res.media_type ? " video-card" : "");
  if (isSkeleton) {
    card.className = "skeleton" + (c.video ? " video-card" : "");
    gallery.prepend(card);
    return card;
  }
  const m = buildMedia(res, c);
  let inner;
  if (m.isVideo) {
    inner = `<video src="${m.src}" autoplay loop muted playsinline></video>`;
  } else {
    inner = `<img src="${m.src}" alt="result" />`;
  }
  const label = c.video ? `🎞️ ${c.name}` : c.name;
  card.innerHTML = inner +
    `<span class="tag">${label} · ${styleName(state.style)}</span>` +
    `<span class="dl">⬇ 下载</span>`;
  card.onclick = (e) => {
    if (e.target.classList.contains("dl")) return;
    openModal(res, c, m);
  };
  card.querySelector(".dl").onclick = (e) => { e.stopPropagation(); downloadRes(res, c, m); };
  gallery.prepend(card);
  return card;
}

function styleName(id) { const s = META.styles.find((x) => x.id === id); return s ? s.name : id; }

function openModal(res, c, m) {
  const box = document.getElementById("modalMedia");
  if (m.isVideo) {
    box.innerHTML = `<video src="${m.src}" controls autoplay loop playsinline></video>`;
  } else {
    box.innerHTML = `<img src="${m.src}" />`;
  }
  const meta = c.video
    ? `${c.name} · ${styleName(state.style)} · ${res.frames} 帧 / ${res.scenes} 镜`
    : `${c.name} · ${styleName(state.style)} · ${sizeName(state.size)}`;
  document.getElementById("modalMeta").textContent = meta;
  const dl = document.getElementById("modalDownload");
  if (m.isVideo) { dl.href = m.src; dl.download = `${c.id}_${state.style}.${res.media_type}`; }
  else { dl.href = m.dataUrl; dl.download = `${c.id}_${state.style}_${state.size}.png`; }
  document.getElementById("modal").classList.add("open");
}
function downloadRes(res, c, m) {
  const a = document.createElement("a");
  if (m.isVideo) { a.href = m.src; a.download = `${c.id}_${state.style}.${res.media_type}`; }
  else { a.href = m.dataUrl; a.download = `${c.id}_${state.style}_${state.size}.png`; }
  a.click();
}
function sizeName(id) { const s = META.sizes.find((x) => x.id === id); return s ? s.name : id; }

async function generate() {
  const btn = document.getElementById("genBtn");
  const prompt = document.getElementById("prompt").value.trim();
  const name = document.getElementById("name").value.trim();
  if (!prompt) { alert("请先输入描述 / 主题"); return; }
  const c = catById(state.category);
  btn.disabled = true;
  document.getElementById("genBtnTxt").textContent = c.video ? "生成视频中…" : "生成中…";
  const sk = renderCard(null, c, true);
  try {
    const r = await fetch(API + "/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: state.category, prompt, style: state.style, size: state.size, name }),
    });
    const res = await r.json();
    sk.remove();
    if (!r.ok) { alert(res.error || "生成失败"); return; }
    renderCard(res, c, false);
    updateCount();
  } catch (e) {
    sk.remove();
    alert("请求异常：" + e.message);
  } finally {
    btn.disabled = false;
    document.getElementById("genBtnTxt").textContent = "立即生成";
  }
}

function updateCount() {
  const n = document.querySelectorAll(".gallery .card").length;
  document.getElementById("count").textContent = n + " 张";
}

// 事件
document.getElementById("genBtn").onclick = generate;
document.getElementById("modalClose").onclick = () => document.getElementById("modal").classList.remove("open");
document.getElementById("modal").onclick = (e) => { if (e.target.id === "modal") e.target.classList.remove("open"); };
document.getElementById("prompt").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") generate();
});

loadMeta();
