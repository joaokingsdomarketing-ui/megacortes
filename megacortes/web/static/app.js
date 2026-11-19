"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

async function api(path, { method = "GET", body } = {}) {
  const opt = { method, headers: { "X-Requested-With": "megacortes" } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error((data && data.error) || `Erro ${r.status}`);
  return data;
}

const STATUS = {
  queued: "Na fila", expanding: "Listando vídeos", downloading: "Baixando", transcribing: "Transcrevendo",
  planning: "Escolhendo momentos", rendering: "Renderizando", done: "Pronto", error: "Falhou",
  canceled: "Cancelado", running: "Em andamento",
};
const ACTIVE = ["expanding", "downloading", "transcribing", "planning", "rendering"];
const pillClass = s => (s === "done" ? "done" : s === "error" ? "error" : ACTIVE.includes(s) || s === "running" ? "run" : "");
const fmtT = s => { s = Math.max(0, s); return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`; };

let CONFIG = null;
const S = { preset: null };

/* ------------------------------------------------------------------ navegação */
function showView(v) {
  for (const b of $$(".top nav button")) b.setAttribute("aria-current", b.dataset.view === v ? "page" : "false");
  $("#view-create").hidden = v !== "create";
  $("#view-projects").hidden = v !== "projects";
  if (v === "projects") poll(true);
}
$$(".top nav button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));

/* ------------------------------------------------------------------ formulário */
const COLORS = ["highlight", "primary", "outline", "hook_box", "hook_text"];

function setColors(c) { for (const k of COLORS) if (c[k]) $("#c_" + k).value = c[k].toLowerCase(); }

function loadProfile(pid) {
  const p = CONFIG.profiles.find(x => x.id === pid) || CONFIG.profiles[0];
  const c = p.caption;
  setColors(c);
  $("#size").value = c.size; $("#wpl").value = c.words_per_line; $("#mv").value = c.margin_v_blur;
  $("#hs").value = c.hook_seconds; $("#upper").checked = c.uppercase;
  $("#clips").value = p.clips; $("#minS").value = p.min_seconds; $("#maxS").value = p.max_seconds;
  $("#reframe").value = p.reframe; $("#niche").value = p.niche_prompt.trim();
  $("#keywords").value = p.keywords; $("#hashtags").value = (p.hashtags || []).join(" ");
  store.set("mc_profile", p.id);
  markPreset(); paint();
}

function applyPreset(pr) {
  setColors(pr.caption);
  if ("uppercase" in pr.caption) $("#upper").checked = pr.caption.uppercase;
  markPreset(); paint();
}

function markPreset() {
  const cur = Object.fromEntries(COLORS.map(k => [k, $("#c_" + k).value.toLowerCase()]));
  S.preset = null;
  for (const pr of CONFIG.presets) {
    const same = COLORS.every(k => (pr.caption[k] || "").toLowerCase() === cur[k]);
    if (same) S.preset = pr.id;
  }
  $$(".preset").forEach(b => b.setAttribute("aria-checked", b.dataset.id === S.preset ? "true" : "false"));
}

function buildPresets() {
  const box = $("#presets");
  for (const pr of CONFIG.presets) {
    const c = pr.caption;
    box.append(h("button", { type: "button", class: "preset", role: "radio", "aria-checked": "false", "data-id": pr.id, onclick: () => applyPreset(pr) },
      h("div", { class: "sw" }, [c.hook_box, c.highlight, c.primary, c.outline].map(col => h("i", { style: `background:${col}` }))),
      pr.name));
  }
}

function collect() {
  const mv = +$("#mv").value;
  return {
    profile: $("#profile").value, clips: +$("#clips").value, min_seconds: +$("#minS").value, max_seconds: +$("#maxS").value,
    min_score: +$("#minScore").value, reframe: $("#reframe").value, niche_prompt: $("#niche").value,
    keywords: $("#keywords").value, hashtags: $("#hashtags").value, whisper_model: $("#whisper").value,
    language: $("#language").value, device: $("#device").value, llm_provider: $("#llm").value, hook: $("#hookOn").checked,
    caption: {
      size: +$("#size").value, words_per_line: +$("#wpl").value, uppercase: $("#upper").checked, hook_seconds: +$("#hs").value,
      margin_v_blur: mv, margin_v_full: Math.max(0, mv - 50), hook_font_size: Math.round(+$("#size").value * 0.875),
      primary: $("#c_primary").value, highlight: $("#c_highlight").value, outline: $("#c_outline").value,
      hook_box: $("#c_hook_box").value, hook_text: $("#c_hook_text").value,
    },
  };
}

/* prévia do celular: palavra falada em destaque, como no vídeo final */
const SAMPLE = "ESSE DETALHE NINGUÉM VIU NO TRAILER".split(" ");
let wordIdx = 0;
function paint() {
  const o = collect(), c = o.caption, st = $("#screen").style;
  st.setProperty("--hbox", c.hook_box); st.setProperty("--htext", c.hook_text); st.setProperty("--hfs", c.hook_font_size);
  st.setProperty("--prim", c.primary); st.setProperty("--hl", c.highlight); st.setProperty("--outl", c.outline);
  st.setProperty("--size", c.size); st.setProperty("--mv", c.margin_v_blur); st.setProperty("--ow", 7);
  $("#pvHook").style.opacity = o.hook && c.hook_seconds > 0 ? 1 : 0;
  const n = c.words_per_line, g = Math.floor(wordIdx / n) * n;
  const cap = $("#pvCap"); cap.replaceChildren();
  SAMPLE.slice(g, g + n).forEach((w, i) => {
    const t = c.uppercase ? w : w.toLowerCase();
    cap.append(i ? " " : "", h("span", { class: g + i === wordIdx ? "hl" : "" }, t));
  });
  $("#o_size").textContent = c.size; $("#o_wpl").textContent = n; $("#o_mv").textContent = c.margin_v_blur;
  $("#o_hs").textContent = c.hook_seconds + "s"; $("#o_score").textContent = o.min_score ? "≥ " + o.min_score : "todas";
  estimate();
}
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
setInterval(() => { if (reduceMotion || document.hidden) return; wordIdx = (wordIdx + 1) % SAMPLE.length; paint(); }, 480);

function sources() {
  return $("#links").value.split("\n").map(s => s.trim()).filter(s => s && !s.startsWith("#"));
}
function estimate() {
  const n = sources().length, c = +$("#clips").value || 0;
  let t = "";
  if (n) {
    const vids = $("#expand").checked ? `até ${n * (+$("#maxVideos").value || 1)}` : n;
    const tot = $("#expand").checked ? n * (+$("#maxVideos").value || 1) * c : n * c;
    t = `${vids} vídeo${vids === 1 ? "" : "s"} × ${c} cortes = até ${tot.toLocaleString("pt-BR")} cortes`;
  }
  $("#estimate").textContent = t;
}

$("#form").addEventListener("input", paint);
$("#profile").addEventListener("change", e => loadProfile(e.target.value));
for (const k of COLORS) $("#c_" + k).addEventListener("input", markPreset);

$("#btnUpload").addEventListener("click", () => $("#fileInput").click());
$("#fileInput").addEventListener("change", async e => {
  const st = $("#uploadStatus");
  for (const f of e.target.files) {
    try {
      const path = await new Promise((res, rej) => {
        const x = new XMLHttpRequest();
        x.open("POST", "/api/upload?name=" + encodeURIComponent(f.name));
        x.setRequestHeader("X-Requested-With", "megacortes");
        x.upload.onprogress = ev => { st.textContent = `Enviando ${f.name}: ${Math.round(100 * ev.loaded / ev.total)}%`; };
        x.onload = () => { try { const d = JSON.parse(x.responseText); x.status < 300 ? res(d.path) : rej(new Error(d.error)); } catch { rej(new Error("Falha no envio")); } };
        x.onerror = () => rej(new Error("Falha no envio"));
        x.send(f);
      });
      const ta = $("#links"); ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + path;
      st.textContent = `${f.name} enviado.`; paint();
    } catch (err) { st.textContent = `Não foi possível enviar ${f.name}: ${err.message}`; }
  }
  e.target.value = "";
});

$("#rights").checked = store.get("mc_rights") === "1";
$("#rights").addEventListener("change", e => store.set("mc_rights", e.target.checked ? "1" : "0"));

$("#form").addEventListener("submit", async e => {
  e.preventDefault();
  const err = $("#formError"); err.hidden = true;
  const fail = m => { err.textContent = m; err.hidden = false; };
  if (!sources().length) return fail("Cole pelo menos um link de vídeo ou envie um arquivo.");
  if (!$("#rights").checked) return fail("Confirme que você tem os direitos ou a autorização para usar estes vídeos.");
  const btn = $("#btnGo"); btn.disabled = true; btn.textContent = "Enviando…";
  try {
    const job = await api("/api/jobs", { method: "POST", body: {
      sources: sources(), options: collect(), expand: $("#expand").checked,
      max_videos: +$("#maxVideos").value || 20, name: $("#name").value } });
    $("#links").value = ""; paint();
    P.current = job.id; showView("projects");
  } catch (ex) { fail(ex.message); }
  finally { btn.disabled = false; btn.textContent = "Gerar cortes"; }
});

/* ------------------------------------------------------------------ projetos */
const P = { current: null, jobs: [], sort: "score", min: 0, detailFor: null, items: new Map(), open: new Set() };
let timer = null;

async function poll(force) {
  clearTimeout(timer);
  try {
    P.jobs = await api("/api/jobs");
    const running = P.jobs.filter(j => j.status === "running" || j.status === "queued").length;
    const b = $("#badge"); b.hidden = !running; b.textContent = running;
    if (!$("#view-projects").hidden) {
      renderList();
      if (P.current && P.jobs.some(j => j.id === P.current)) renderDetail(await api("/api/jobs/" + P.current));
      else if (!P.current && P.jobs.length) { P.current = P.jobs[0].id; renderList(); renderDetail(await api("/api/jobs/" + P.current)); }
      else if (!P.jobs.length) { $("#pdetail").replaceChildren(h("div", { class: "empty" }, "Nenhum projeto ainda. Volte em Criar e cole um link.")); }
    }
  } catch (e) { /* servidor reiniciando: tenta de novo */ }
  timer = setTimeout(poll, running() ? 2000 : 6000);
}
const running = () => P.jobs.some(j => j.status === "running" || j.status === "queued");

function jobTitle(j) { return j.name || "Projeto " + new Date(j.created * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); }

function renderList() {
  const box = $("#plist"); box.replaceChildren();
  for (const j of P.jobs) {
    const done = j.status === "done";
    box.append(h("button", { class: "pcard", "aria-current": j.id === P.current ? "true" : "false", onclick: () => { P.current = j.id; P.detailFor = null; poll(); } },
      h("h3", {}, jobTitle(j)),
      h("div", { class: "bar" + (done ? " done" : "") }, h("i", { style: `width:${Math.round(j.progress * 100)}%` })),
      h("div", { class: "meta" }, `${STATUS[j.status] || j.status} · ${j.videos} vídeo${j.videos === 1 ? "" : "s"} · ${j.clips_total} ${j.clips_total === 1 ? "corte" : "cortes"}`)));
  }
}

function renderDetail(job) {
  const box = $("#pdetail");
  if (P.detailFor !== job.id) {
    P.detailFor = job.id; P.items = new Map();
    box.replaceChildren(h("div", { class: "phead", id: "phead" }), h("div", { class: "tools", id: "ptools" }), h("div", { id: "pitems" }));
    buildTools();
  }
  const head = $("#phead"); head.replaceChildren(...[
    h("h2", {}, jobTitle(job)), h("span", { class: "pill " + pillClass(job.status) }, STATUS[job.status] || job.status),
    job.status === "running" || job.status === "queued" ? h("button", { class: "ghost", onclick: () => act(job.id, "cancel") }, "Cancelar") : null,
    job.items.some(i => i.status === "error" || i.status === "canceled") ? h("button", { class: "ghost", onclick: () => act(job.id, "retry") }, "Tentar de novo os que falharam") : null,
    job.items.some(i => i.status === "done") ? h("a", { class: "ghost", style: "text-decoration:none", href: `/api/jobs/${job.id}/download.zip` }, "Baixar tudo (ZIP)") : null,
    job.status !== "running" ? h("button", { class: "ghost danger", onclick: () => del(job.id) }, "Excluir") : null].filter(Boolean));

  const list = $("#pitems");
  const seen = new Set();
  for (const it of job.items) {
    seen.add(it.id);
    let el = P.items.get(it.id);
    if (!el) { el = makeItem(job, it); P.items.set(it.id, el); list.append(el.root); }
    updateItem(el, job, it);
  }
  for (const [id, el] of P.items) if (!seen.has(id)) { el.root.remove(); P.items.delete(id); }
}

async function act(id, what) { try { await api(`/api/jobs/${id}/${what}`, { method: "POST", body: {} }); } catch (e) { alert(e.message); } poll(); }
async function del(id) {
  if (!confirm("Excluir este projeto e todos os cortes gerados?")) return;
  try { await api("/api/jobs/" + id, { method: "DELETE" }); P.current = null; P.detailFor = null; } catch (e) { alert(e.message); }
  poll();
}

function buildTools() {
  $("#ptools").append(
    h("label", {}, "Ordenar ", h("select", { onchange: e => { P.sort = e.target.value; applyView(); } },
      h("option", { value: "score" }, "Maior nota"), h("option", { value: "time" }, "Ordem no vídeo"))),
    h("label", {}, "Nota mínima ", h("input", { type: "range", min: 0, max: 95, step: 5, value: P.min, oninput: e => { P.min = +e.target.value; e.target.nextSibling.textContent = " " + (P.min || "todas"); applyView(); } }), h("span", {}, " " + (P.min || "todas"))));
}
function applyView() {
  for (const el of P.items.values()) for (const c of el.cards.values()) {
    c.root.hidden = c.data.score < P.min;
    c.root.style.order = P.sort === "score" ? Math.round(-c.data.score * 10) : Math.round(c.data.start);
  }
}

function makeItem(job, it) {
  const bar = h("i"), pill = h("span", { class: "pill" }), title = h("span", { class: "t" }), meta = h("div", { class: "meta" });
  const gallery = h("div", { class: "gallery" }), err = h("div", { class: "error", hidden: true });
  const body = h("div", { class: "ibody" }, err, meta, gallery);
  const root = h("details", { class: "item" },
    h("summary", {}, h("div", {}, title, h("div", { class: "meta" })), pill, h("div", { class: "bar" }, bar)), body);
  const el = { root, bar, pill, title, sub: $(".meta", root), meta, gallery, err, body, cards: new Map(), last: "" };
  if (P.open.has(`${job.id}/${it.id}`)) root.open = true;
  root.addEventListener("toggle", () => {
    const k = `${job.id}/${it.id}`; root.open ? P.open.add(k) : P.open.delete(k);
    if (root.open) refreshClips(el, job.id, it.id);
  });
  return el;
}

function updateItem(el, job, it) {
  el.title.textContent = it.title || it.source;
  el.pill.textContent = STATUS[it.status] || it.status; el.pill.className = "pill " + pillClass(it.status);
  el.bar.style.width = Math.round(it.progress * 100) + "%";
  el.bar.parentElement.className = "bar" + (it.status === "done" ? " done" : "");
  el.sub.textContent = it.status === "done" ? `${it.clips_total} ${it.clips_total === 1 ? "corte" : "cortes"} · ${Math.round(it.seconds)}s de processamento` : (it.message || "");
  el.err.hidden = !it.error; el.err.textContent = it.error;
  if (it.status === "done") {
    el.meta.replaceChildren(
      h("a", { href: `/api/jobs/${job.id}/items/${it.id}/download.zip`, class: "hint", style: "margin-right:14px" }, "Baixar cortes (ZIP)"),
      h("a", { href: `/api/jobs/${job.id}/items/${it.id}/files/compilado_10min.mp4`, class: "hint", target: "_blank", download: "compilado_10min.mp4", style: "font-weight:600; color:var(--accent,#f0301e)" }, "🎬 Baixar Compilado (10 min)")
    );
  } else {
    el.meta.replaceChildren("");
  }
  const key = it.status + (it.status === "rendering" ? it.progress : "");
  if (el.root.open && (ACTIVE.includes(it.status) && it.status === "rendering" || el.last !== key)) refreshClips(el, job.id, it.id);
  el.last = key;
}

async function refreshClips(el, jid, iid) {
  let clips;
  try { clips = await api(`/api/jobs/${jid}/items/${iid}/clips`); } catch { return; }
  for (const c of clips) {
    let card = el.cards.get(c.index);
    if (!card) { card = makeCard(jid, iid, c); el.cards.set(c.index, card); el.gallery.append(card.root); }
    updateCard(card, jid, iid, c);
  }
  applyView();
}

function makeCard(jid, iid, c) {
  const thumb = h("button", { class: "thumb", type: "button", "aria-label": "Assistir corte" });
  const score = h("span", { class: "score" }), ttl = h("div", { class: "ttl" }), hk = h("div", { class: "hk" }), tags = h("div", { class: "tags" }), risk = h("div", { class: "risk", hidden: true });
  const act = h("div", { class: "cact" });
  const root = h("article", { class: "clip" }, thumb, h("div", { class: "cbody" }, ttl, hk, risk, tags, act));
  return { root, thumb, score, ttl, hk, tags, risk, act, data: c, v: -1, busy: null };
}

function updateCard(card, jid, iid, c) {
  card.data = c;
  card.ttl.textContent = c.title; card.hk.textContent = `Gancho: ${c.hook}  ·  ${fmtT(c.start)}–${fmtT(c.end)} (${Math.round(c.duration)}s)`;
  card.tags.textContent = (c.post || "").slice(c.title.length).trim();
  card.risk.hidden = !(c.risk && c.risk.length); card.risk.textContent = "Revisar antes de postar: " + (c.risk || []).join(", ");
  if (card.v === c.v && card.busy === c.busy) return;
  card.v = c.v; card.busy = c.busy;
  const nn = String(c.index).padStart(2, "0"), base = `/files/${jid}/${iid}/clips/clip_${nn}`;
  card.thumb.replaceChildren();
  const sc = Math.round(c.score);
  card.thumb.append(h("span", { class: "score " + (sc >= 75 ? "hi" : sc >= 55 ? "mid" : "") }, `Nota ${sc}`));
  if (c.ready) {
    card.thumb.append(h("img", { src: `${base}.jpg?v=${c.v}`, loading: "lazy", alt: c.title }), h("span", { class: "play" }, "▶"));
    card.thumb.onclick = () => {
      const v = h("video", { src: `${base}.mp4?v=${c.v}`, controls: true, autoplay: true, playsinline: true, preload: "auto" });
      card.thumb.replaceChildren(v); card.thumb.onclick = null; card.thumb.style.cursor = "default";
    };
  }
  if (!c.ready || c.busy) card.thumb.append(h("div", { class: "busy" }, c.busy ? "Renderizando de novo…" : "Renderizando…"));
  card.act.replaceChildren(
    c.ready ? h("a", { href: `${base}.mp4?dl=${encodeURIComponent(slug(c.title))}.mp4`, download: "" }, "Baixar") : null,
    h("button", { type: "button", onclick: ev => copy(c.post, ev.target) }, "Copiar legenda"),
    h("button", { type: "button", onclick: () => editClip(jid, iid, c) }, "Editar"));
}
const slug = s => s.normalize("NFKD").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-").toLowerCase().slice(0, 60) || "corte";
async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); const t = btn.textContent; btn.textContent = "Copiado"; setTimeout(() => btn.textContent = t, 1200); }
  catch { prompt("Copie a legenda:", text); }
}

/* editar um corte */
function editClip(jid, iid, c) {
  const d = $("#dlgEdit"); $("#e_err").hidden = true;
  $("#e_title").value = c.title; $("#e_hook").value = c.hook; $("#e_start").value = c.start; $("#e_end").value = c.end;
  d.returnValue = ""; d.showModal();
  $("#e_save").onclick = async ev => {
    ev.preventDefault();
    try {
      await api(`/api/jobs/${jid}/items/${iid}/clips/${c.index}`, { method: "PATCH", body: {
        title: $("#e_title").value, hook: $("#e_hook").value, start: +$("#e_start").value, end: +$("#e_end").value } });
      d.close(); const el = P.items.get(iid); if (el) refreshClips(el, jid, iid);
      const poller = setInterval(async () => { const e2 = P.items.get(iid); if (!e2) return clearInterval(poller); await refreshClips(e2, jid, iid); if (![...e2.cards.values()].some(x => x.data.busy)) clearInterval(poller); }, 2000);
    } catch (ex) { $("#e_err").textContent = ex.message; $("#e_err").hidden = false; }
  };
}

/* ------------------------------------------------------------------ configurações */
async function openSettings() {
  const s = await api("/api/settings");
  const sel = $("#s_provider"); sel.replaceChildren(h("option", { value: "" }, "Automático (usa a primeira chave salva)"), h("option", { value: "none" }, "Sem IA (só heurística)"));
  for (const p of CONFIG.llm_providers) sel.append(h("option", { value: p.id }, p.id));
  sel.value = s.provider; $("#s_model").value = s.model;
  $("#s_model").placeholder = s.default_models[s.provider] || "padrão do provedor";
  const keys = $("#s_keys"); keys.replaceChildren();
  for (const p of CONFIG.llm_providers.filter(p => p.needs_key)) {
    const k = s.keys[p.id];
    keys.append(h("label", { class: "field" }, h("span", {}, `Chave ${p.id}`, k.set ? h("button", { type: "button", class: "ghost", onclick: async () => { await api("/api/settings", { method: "POST", body: { keys: { [p.id]: "" } } }); openSettings(); } }, "Remover") : null),
      h("input", { type: "password", "data-prov": p.id, placeholder: k.set ? `${k.hint} (salva)` : "Cole a chave aqui", autocomplete: "off" })));
  }
  const c = CONFIG.capabilities, ok = v => v ? "ok" : "não encontrado";
  $("#s_caps").textContent = `FFmpeg: ${ok(c.ffmpeg)} · Whisper: ${ok(c.whisper)} · yt-dlp: ${ok(c.yt_dlp)} · OpenCV (seguir rosto): ${ok(c.opencv)}. Arquivos locais: ${CONFIG.videos_dir}`;
  $("#dlgSettings").showModal();
}
$("#btnSettings").addEventListener("click", openSettings);
$("#s_save").addEventListener("click", async ev => {
  ev.preventDefault();
  const keys = {}; for (const i of $$("#s_keys input")) if (i.value.trim()) keys[i.dataset.prov] = i.value.trim();
  try { await api("/api/settings", { method: "POST", body: { provider: $("#s_provider").value, model: $("#s_model").value, keys } }); $("#dlgSettings").close(); await refreshLlm(); }
  catch (e) { alert(e.message); }
});

async function refreshLlm() {
  const s = await api("/api/settings"), sel = $("#llm"), cur = sel.value;
  const any = Object.values(s.keys).some(k => k.set);
  sel.replaceChildren(h("option", { value: "auto" }, any ? "Automático (usar a IA configurada)" : "Automático (sem chave: só heurística)"), h("option", { value: "none" }, "Sem IA"));
  for (const p of CONFIG.llm_providers) sel.append(h("option", { value: p.id }, p.id));
  sel.value = cur || "auto";
}

/* ------------------------------------------------------------------ início */
(async function init() {
  try { CONFIG = await api("/api/config"); }
  catch (e) { $("#warn").textContent = "Não consegui falar com o servidor: " + e.message; return; }
  const c = CONFIG.capabilities, w = [];
  if (!c.ffmpeg) w.push("FFmpeg não encontrado: nada será renderizado.");
  if (!c.whisper) w.push("faster-whisper não instalado: a transcrição não vai funcionar.");
  if (!c.yt_dlp) w.push("yt-dlp não instalado: links não baixam; só arquivos enviados.");
  $("#warn").textContent = w.join(" ");
  const pf = $("#profile"); for (const p of CONFIG.profiles) pf.append(h("option", { value: p.id }, p.name));
  const w2 = $("#whisper"); for (const m of CONFIG.whisper_models) w2.append(h("option", { value: m }, m + (m === "small" ? " (equilibrado)" : m === "tiny" ? " (mais rápido)" : m === "large-v3" ? " (mais preciso)" : "")));
  w2.value = "small"; $("#hookOn").checked = true;
  buildPresets();
  const saved = store.get("mc_profile");
  pf.value = CONFIG.profiles.some(p => p.id === saved) ? saved : (CONFIG.profiles.find(p => p.id === "gta6") || CONFIG.profiles[0]).id;
  await refreshLlm();
  loadProfile(pf.value);
  poll();
})();

// PWA Service Worker Registration
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/sw.js')
      .then((reg) => console.log('MegaCortes PWA registrado:', reg.scope))
      .catch((err) => console.log('MegaCortes PWA falhou:', err));
  });
}
