import { HoloScene } from "./scene.js";
import { RemoteMic, SpeechPlayer } from "./remote.js";
import { initDevices, updateDevices } from "./devices.js";
import { initSentinel, updateSentinel } from "./sentinel.js";

const $ = s => document.querySelector(s);
const body = document.body;
const scene = new HoloScene($("#scene"));
let vision = null;
let ws = null;
let jarvisMsg = null;
let micOn = true;
let micMode = "push";   // push: ⌘H to talk · wake: always listening for "Jarvis" · off
let hotkeyOk = false;
// Push-to-talk keys: ⌘H / ⌘S on a Mac, Ctrl+Alt+J / Ctrl+Alt+S on Windows and Linux (sent in "hello").
let KEYS = { listen: "⌘H", send: "⌘S" }, HOST_OS = "mac";
let online = false;
const IN_APP = navigator.userAgent.includes("JARVISApp");  // JARVIS.app handles ⌘H in its menu
// Opened from a phone, tablet or PC (remote access, served over HTTPS): this device has its
// own mic and speaker; the computer runs everything else.
// Pocket: no JARVIS server at all. The page runs the assistant itself (pocket.js) with the user's
// own API key: the GitHub Pages build (it carries <meta name="jarvis-pocket">) or any URL with ?pocket.
const POCKET = !!document.querySelector('meta[name="jarvis-pocket"]') || new URLSearchParams(location.search).has("pocket");
let pocket = null;
const REMOTE = !POCKET && location.protocol === "https:";
const TOUCH = matchMedia("(pointer: coarse)").matches;
const MAX_REC = 300;  // seconds, like push-to-talk on the computer
const remoteMic = REMOTE ? new RemoteMic(l => scene.setMic(l)) : null;
const player = REMOTE ? new SpeechPlayer() : null;
if (REMOTE) body.classList.add("remote");
if (POCKET) body.classList.add("pocket");
if (TOUCH) body.classList.add("touch");

// ---------------- socket ----------------
async function connect() {
  if (POCKET) {
    const mod = await import("./pocket.js");
    ws = pocket = new mod.PocketCore(handle);
    initPocketSettings(mod);
    bootLog("› pocket core online · running on this device");
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
    return;
  }
  ws = new WebSocket(`${REMOTE ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { bootLog("› uplink to core established"); sendVisibility(); };
  ws.onclose = () => { setStatus("RECONNECTING", "offline"); setTimeout(connect, 1500); };
  ws.onmessage = e => handle(JSON.parse(e.data));
}
const send = msg => ws?.readyState === 1 && ws.send(JSON.stringify(msg));
// The core only polls volume, now-playing and the process list while someone can see them.
const sendVisibility = () => send({ type: "visibility", visible: !document.hidden });
document.addEventListener("visibilitychange", sendVisibility);

const STATE_TEXT = {
  idle: ["STANDBY", "AWAITING INSTRUCTION"], listening: ["LISTENING", "SPEAK NOW"],
  hearing: ["LISTENING", "AUDIO INPUT DETECTED"],
  recognizing: ["PROCESSING", "DECODING SPEECH"], thinking: ["ANALYSING", "NEURAL CORE ENGAGED"],
  working: ["EXECUTING", "OPERATING SYSTEMS"], speaking: ["RESPONDING", "VOICE SYNTHESIS"],
};
let brainState = "idle", voiceState = "idle", attentive = false, recStart = 0;
// recording clock in the status line
setInterval(() => {
  if (["listening", "hearing"].includes(voiceState) || remoteMic?.active) refreshState();
  if (remoteMic?.active && (performance.now() - recStart) / 1000 >= MAX_REC) remoteSend();
}, 1000);

function refreshState() {
  const s = brainState !== "idle" && voiceState !== "speaking" ? brainState : voiceState;
  body.dataset.state = s;
  scene.setState(s === "listening" ? "hearing" : s);
  $("#btn-mic").classList.toggle("on", POCKET ? pocket?.micActive : REMOTE ? remoteMic.active : micMode === "push" ? s === "listening" || s === "hearing" : micOn);
  let [title, sub] = STATE_TEXT[s] || STATE_TEXT.idle;
  if (s === "idle" && attentive) [title, sub] = ["LISTENING", "GO AHEAD"];
  if (s === "idle" && micMode === "push" && !attentive) sub = REMOTE || POCKET ? "TAP THE MIC TO SPEAK" : hotkeyOk ? `PRESS ${KEYS.listen.toUpperCase()} TO SPEAK` : "AWAITING INSTRUCTION";
  if (REMOTE && remoteMic.active) [title, sub] = ["RECORDING", `${mmss((performance.now() - recStart) / 1000)} · TAP THE MIC TO SEND`];
  else if (POCKET && pocket?.micActive) [title, sub] = ["LISTENING", "TAP THE MIC WHEN YOU'RE DONE"];
  else if ((s === "listening" || s === "hearing") && micMode === "push") {
    title = "RECORDING";
    sub = `${mmss((performance.now() - recStart) / 1000)} · ${KEYS.send.toUpperCase()} TO SEND`;
  }
  if (!online && s === "idle") sub = POCKET ? "TAP ⚙ TO ADD YOUR API KEY" : "BRAIN OFFLINE · SEE CONSOLE";
  $("#core-state").textContent = title;
  $("#core-sub").textContent = sub;
  setStatus(title);
}
function setStatus(text, cls) {
  $("#status-text").textContent = text;
  body.classList.toggle("offline", cls === "offline" || !online);
}

function handle(m) {
  switch (m.type) {
    case "hello":
      online = m.online; micOn = m.mic; micMode = m.mic_mode || "push"; hotkeyOk = !!m.hotkey;
      if (m.keys) KEYS = m.keys;
      if (m.os) { HOST_OS = m.os; body.dataset.os = m.os; }
      $("#model").textContent = `${m.model.toUpperCase()} · ${m.brain}`;
      applyMicMode();
      renderTray(m.tray);
      if (m.weather) showWeather(m.weather);
      showMedia(m.media); if (m.agenda) { agenda = m.agenda; renderAgenda(); }
      bootLog(`› cognitive core: ${m.model} via ${POCKET ? "Anthropic API, on this device" : m.brain === "API" ? "Anthropic API" : "Claude plan"}${online ? "" : "  [OFFLINE]"}`);
      if (m.modes) applyModes(m.modes);
      if (m.remote) updateDevices(m.remote);
      if (m.sentinel) updateSentinel(m.sentinel);
      if (m.memory) showMemory(m.memory);
      if (REMOTE) micMode = "push";
      refreshState();
      break;
    case "state": brainState = m.state; refreshState(); break;
    case "hotkey": hotkeyOk = !!m.ok; applyMicMode(); addMsg("progress", `${KEYS.listen} push-to-talk is ready.`); break;
    case "voice":
      if (m.state === "listening" && !["listening", "hearing"].includes(voiceState)) recStart = performance.now();
      voiceState = m.state.startsWith("mic-error") ? "idle" : m.state;
      attentive = !!m.attentive && micOn;
      clearTimeout(handle.at);
      if (attentive) handle.at = setTimeout(() => { attentive = false; refreshState(); }, (m.window || 8) * 1000);
      if (m.state.startsWith("mic-error")) addMsg("progress", "Microphone unavailable: " + m.state.slice(11));
      refreshState(); break;
    case "level": scene.setLevel(m.level); break;
    case "mic": scene.setMic(m.level); break;
    case "heard": $("#heard").textContent = `“${m.text}”`; clearTimeout(handle.ht); handle.ht = setTimeout(() => $("#heard").textContent = "", 5000); break;
    case "user": addUserMsg(m.text, m.images); jarvisMsg = null; break;
    case "tray": renderTray(m); break;
    case "assistant_delta":
      if (!jarvisMsg) jarvisMsg = addMsg("jarvis", "");
      jarvisMsg.textContent += m.text; scrollFeed(); break;
    case "assistant_done": jarvisMsg = null; break;
    case "progress": addMsg("progress", m.text); break;
    case "action": { const d = describeAction(m); if (d) addMsg("action", d); break; }
    case "hud": hud(m); break;
    case "telemetry": telemetry(m); break;
    case "weather": showWeather(m); break;
    case "protocol": protocol(m); break;
    case "modes": applyModes(m); break;
    case "remote": updateDevices(m); break;
    case "sentinel": if (!REMOTE && !POCKET) updateSentinel(m); break;
    case "memory": showMemory(m); break;
    case "speech_audio": player?.play(m.data); break;
    case "speech_stop": player?.stop(); break;
    case "timer": showAlert("TIMER SET", `${m.message} · ${m.due}`, "info");
      timers.push({ message: m.message, at: m.at || Date.now() }); renderAgenda(); break;
    case "media": showMedia(m.media); break;
    case "agenda": agenda = m; renderAgenda(); break;
    case "camera_request": cameraRequest(m.id); break;
    case "confirm": openConfirm(m); break;
    case "confirm_done": $("#confirm").hidden = true; break;
    case "pocket_setup": openPocketSettings(); break;
  }
}

// ---------------- feed ----------------
function addMsg(kind, text) {
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  el.textContent = text;
  const feed = $("#feed");
  feed.append(el);
  while (feed.children.length > 60) feed.firstChild.remove();
  scrollFeed();
  return el;
}
function addUserMsg(text, images = []) {
  const el = addMsg("user", text || "");
  const safe = (images || []).filter(u => /^\/uploads\/[\w.-]+$/.test(u) || (POCKET && /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(u)));
  if (safe.length) {
    const row = document.createElement("div");
    row.className = "msg-images";
    for (const u of safe) {
      const img = document.createElement("img");
      img.src = u; img.alt = ""; img.onclick = () => showLightbox(u);
      img.onload = scrollFeed;
      row.append(img);
    }
    el.append(row);
  }
  return el;
}
const scrollFeed = () => { const f = $("#feed"); f.scrollTop = f.scrollHeight; };
function describeAction(m) {
  const i = m.input || {};
  if (m.tool === "computer") {
    const c = i.coordinate ? ` @ ${i.coordinate.join(",")}` : "";
    const t = i.text ? ` “${String(i.text).slice(0, 40)}”` : "";
    return `computer.${m.name}${c}${t}`;
  }
  if (m.tool === "bash") return `shell $ ${(i.command || "restart").slice(0, 120)}`;
  if (m.tool === "applescript") return `applescript ${i.script?.split("\n")[0].slice(0, 100)}`;
  if (m.tool === "hud") return null;
  return `${m.tool} ${Object.values(i).filter(v => typeof v === "string").join(" ").slice(0, 80)}`;
}

// ---------------- HUD panels & effects ----------------
function tex(src, display) {
  try {
    if (window.katex) return katex.renderToString(src, { displayMode: display, throwOnError: false, output: "html" });
  } catch {}
  const e = src.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  return display ? `<pre>${e}</pre>` : `<code>${e}</code>`;
}
function md(src = "") {
  const esc = s => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const blocks = [];
  const keep = html => { blocks.push(html); return `\u0000${blocks.length - 1}\u0000`; };
  let text = src.replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, code) => keep(`<pre>${esc(code)}</pre>`));
  // LaTeX: $$display$$, \[display\], \(inline\), $inline$ (no space just inside the $ so "$5 and $10" stays text)
  text = text.replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (_, a, b) => keep(`<div class="math-d">${tex(a ?? b, true)}</div>`))
    .replace(/\\\((.+?)\\\)|\$([^\s$](?:[^$\n]*?[^\s$])?)\$/g, (_, a, b) => keep(tex(a ?? b, false)));
  text = esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines = text.split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    const li = line.match(/^\s*[-*•]\s+(.*)/);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${li[1]}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    html += line.trim() ? `<div>${line}</div>` : "";
  }
  if (inList) html += "</ul>";
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

function hud(m) {
  if (m.action === "panel") {
    const panel = document.createElement("section");
    panel.className = `hud-card panel lvl-${m.level || "info"}`;
    panel.innerHTML = `<h3><b>◆</b> ${md(m.title || "INFORMATION").replace(/<\/?div>/g, "")}</h3><button class="panel-close" title="Close">✕</button><div class="panel-body">${md(m.body || "")}</div>`;
    panel.querySelector(".panel-close").onclick = () => closePanel(panel);
    const host = $("#panels");
    host.prepend(panel);
    while (host.children.length > 4) host.lastChild.remove();
    scene.pulse();
  } else if (m.action === "image") {
    // a generated picture (or several): tap one to view it large
    const safe = (m.images || []).filter(u => /^\/generated\/[\w.-]+$/.test(u));
    if (!safe.length) return;
    const panel = document.createElement("section");
    panel.className = "hud-card panel panel-image";
    panel.innerHTML = `<h3><b>◆</b> IMAGE</h3><button class="panel-close" title="Close">✕</button>
      <div class="gen-grid n${safe.length}">${safe.map(u => `<img src="${u}" alt="" loading="lazy">`).join("")}</div>
      <div class="gen-caption"></div>`;
    panel.querySelector(".gen-caption").textContent = m.caption || "";
    panel.querySelector(".panel-close").onclick = () => closePanel(panel);
    panel.querySelectorAll("img").forEach(img => { img.onclick = () => showLightbox(img.src); });
    const host = $("#panels");
    host.prepend(panel);
    while (host.children.length > 4) host.lastChild.remove();
    scene.pulse();
  } else if (m.action === "device_screen") {
    // live view of a wired phone: one panel, refreshed with every screenshot JARVIS takes
    if (!/^data:image\/(jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(m.image || "")) return;
    let panel = $("#device-screen");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "device-screen";
      panel.className = "hud-card panel panel-image";
      panel.innerHTML = `<h3><b>◆</b> <span></span></h3><button class="panel-close" title="Close">✕</button>
        <div class="gen-grid device"><img alt=""></div>`;
      panel.querySelector(".panel-close").onclick = () => closePanel(panel);
      panel.querySelector("img").onclick = e => showLightbox(e.target.src);
      $("#panels").prepend(panel);
      scene.pulse();
    }
    panel.querySelector("h3 span").textContent = m.title || "DEVICE";
    panel.querySelector("img").src = m.image;
  } else if (m.action === "alert") {
    showAlert(m.title || "ALERT", m.body || "", m.level || "info");
  } else if (m.action === "mode") {
    setMode(m.mode);
    if (m.mode === "scan") { body.classList.add("scanning"); setTimeout(() => body.classList.remove("scanning"), 4000); }
  } else if (m.action === "locate") {
    scene.locate(m.lat, m.lon);
    body.dataset.mode = "globe";
    geoLabel = m.label || `${m.lat?.toFixed(2)}, ${m.lon?.toFixed(2)}`;
  } else if (m.action === "clear") {
    [...$("#panels").children].forEach(closePanel);
    setMode("idle");
  }
}
function showLightbox(src) {
  let box = $("#lightbox");
  if (!box) {
    box = document.createElement("div");
    box.id = "lightbox";
    box.innerHTML = "<img alt=''>";
    box.onclick = () => { box.hidden = true; };
    document.body.append(box);
  }
  box.querySelector("img").src = src;
  box.hidden = false;
}
// ---------------- modes ----------------
let modes = { focus: "standard", power: "normal" };
function applyModes(m) {
  modes = m;
  body.dataset.focus = m.focus;
  body.dataset.power = m.power;
  document.querySelectorAll("#modebar [data-focus]").forEach(b => b.classList.toggle("on", b.dataset.focus === m.focus));
  $("#mb-power").classList.toggle("on", m.power === "full");
  $("#modebar").title = `${m.focus_label} · ${m.power_label} · effort ${m.effort}`;
  scene.setTheme(m.focus, m.power === "full");
}
document.querySelectorAll("#modebar [data-focus]").forEach(b =>
  b.onclick = () => send({ type: "set_mode", kind: "focus", value: b.dataset.focus }));
$("#mb-power").onclick = () => send({ type: "set_mode", kind: "power", value: modes.power === "full" ? "normal" : "full" });

function closePanel(p) { p.classList.add("out"); setTimeout(() => p.remove(), 350); }
function setMode(mode) {
  body.dataset.mode = mode || "idle";
  scene.setMode(mode);
  if (mode !== "globe") geoLabel = null;
  clearTimeout(setMode.t);
  if (mode && !["idle", "globe", "party"].includes(mode)) setMode.t = setTimeout(() => { if (body.dataset.mode === mode) setMode("idle"); }, 20000);
}
let geoLabel = null;
const geoEl = $("#geo-label");
scene.onFrame = () => {
  if (!geoLabel && !geoEl.classList.contains("on")) return;  // nothing pinned: skip per-frame DOM work
  const el = geoEl, p = geoLabel && scene.pinScreen();
  el.classList.toggle("on", !!(p && p.front));
  if (p) { el.style.left = p.x + 14 + "px"; el.style.top = p.y - 30 + "px"; el.textContent = "◎ " + geoLabel; }
};

function showAlert(title, text, level = "info") {
  const el = $("#alert");
  el.className = `on ${level}`;
  el.innerHTML = `${title}${text ? `<small>${md(text).replace(/<\/?div>/g, " ")}</small>` : ""}`;
  scene.pulse();
  clearTimeout(showAlert.t);
  showAlert.t = setTimeout(() => el.className = level, 6000);
}

function protocol(m) {
  $("#ps-name").textContent = m.name;
  const s = $("#protocol-splash");
  s.classList.remove("on"); void s.offsetWidth; s.classList.add("on");
  setMode(m.mode);
  for (let i = 0; i < 4; i++) setTimeout(() => scene.pulse(), i * 250);
}

// ---------------- telemetry ----------------
function gauge(id, value) {
  const el = $(id);
  el.querySelector(".g-val").style.strokeDashoffset = 314 * (1 - Math.min(100, value) / 100);
  el.querySelector(".g-num").textContent = Math.round(value);
}
function telemetry(t) {
  gauge("#g-cpu", t.cpu); gauge("#g-mem", t.mem);
  const cores = $("#cores");
  if (cores.children.length !== t.cores.length) cores.innerHTML = t.cores.map(() => "<i></i>").join("");
  t.cores.forEach((c, i) => cores.children[i].style.height = Math.max(4, c) + "%");
  $("#b-disk").style.width = t.disk + "%";
  $("#t-disk").textContent = `${t.disk_free_gb} GB FREE`;
  if (t.battery != null) {
    $("#b-bat").style.width = t.battery + "%";
    $("#b-bat").classList.toggle("low", t.battery < 20 && !t.charging);
    $("#t-bat").textContent = `${t.battery}%${t.charging ? " ⚡" : ""}`;
  } else $("#t-bat").textContent = "AC";
  $("#t-up").textContent = `${t.uptime_h} H · ${t.mem_used_gb}/${t.mem_total_gb} GB RAM`;
  if (t.procs) {
    $("#procs").innerHTML = `<div class="hdr"><span>PROCESS</span><span>CPU</span><span></span></div>` +
      t.procs.map(p => `<div><span>${esc(p.name)}</span><span>${p.cpu.toFixed(0)}%</span><i style="width:${Math.min(100, p.cpu / 2)}%"></i></div>`).join("");
  }
  sysbar(t);
  scene.setLoad?.(t.cpu / 100);

}
// ---------------- status bar, media, schedule ----------------
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// Conversation context in use. JARVIS compacts its memory when this reaches `compact_at` percent.
function showMemory(m) {
  const pct = m.percent < 10 ? m.percent.toFixed(1) : Math.round(m.percent);
  chip("#sb-ctx", m.compacting ? "COMPACTING" : `${pct}%`, m.percent >= m.compact_at * 0.9);
  $("#sb-ctx").title = `Conversation context: ${m.tokens.toLocaleString()} of ${m.window.toLocaleString()} tokens. `
    + `JARVIS compacts its memory at ${m.compact_at}% (done ${m.compactions} time${m.compactions === 1 ? "" : "s"} so far); nothing is forgotten.`;
}
function chip(id, text, warn) { const el = $(id); el.querySelector("em").textContent = text; el.classList.toggle("warn", !!warn); }
function sysbar(t) {
  if (t.battery != null) chip("#sb-power", `${t.battery}%${t.charging ? " ⚡" : ""}`, t.battery < 20 && !t.charging);
  else chip("#sb-power", "AC");
  if (t.volume != null) chip("#sb-vol", t.muted ? "MUTED" : `${t.volume}`);
  const kb = t.net_up_kbs + t.net_down_kbs;
  chip("#sb-net", navigator.onLine ? (kb > 1024 ? `${(kb / 1024).toFixed(1)} MB/s` : `${kb.toFixed(0)} KB/s`) : "OFFLINE", !navigator.onLine);
  chip("#sb-load", `${t.cpu.toFixed(0)}%`, t.cpu > 85);
}

let media = null, mediaAt = 0;
const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function showMedia(m) {
  media = m; mediaAt = performance.now();
  $("#card-media").hidden = !m;
  if (!m) return;
  $("#mp-player").textContent = `${m.player.toUpperCase()} · ${m.state.toUpperCase()}`;
  $("#mp-title").textContent = m.title || "—";
  $("#mp-artist").textContent = [m.artist, m.album].filter(Boolean).join(" · ") || "—";
  $("#mp-toggle").textContent = m.state === "playing" ? "⏸" : "▶";
  mediaProgress();
}
function mediaProgress() {
  if (!media) return;
  const pos = Math.min(media.duration || 0, media.position + (media.state === "playing" ? (performance.now() - mediaAt) / 1000 : 0));
  $("#mp-pos").textContent = mmss(pos); $("#mp-dur").textContent = mmss(media.duration || 0);
  $("#mp-bar").style.width = media.duration ? (pos / media.duration * 100) + "%" : "0";
}
document.querySelectorAll("[data-media]").forEach(b => b.onclick = () => send({ type: "media", action: b.dataset.media }));

let agenda = { events: [], authorized: false };
const timers = [];
function renderAgenda() {
  const now = Date.now();
  $("#ag-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }).toUpperCase();
  for (let i = timers.length - 1; i >= 0; i--) if (timers[i].at < now - 5000) timers.splice(i, 1);
  const rows = timers.map(t => {
    const left = Math.max(0, (t.at - now) / 1000);
    return `<div class="ag-item timer"><time>${mmss(left)}</time><div>⏱ ${esc(t.message)}</div></div>`;
  });
  const hm = d => d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  for (const e of agenda.events || []) {
    const s = new Date(e.start), en = new Date(e.end);
    const cls = en < now ? "past" : s <= now ? "now" : "";
    const sub = [e.location, e.calendar].filter(Boolean).join(" · ");
    rows.push(`<div class="ag-item ${cls}"><time>${e.all_day ? "ALL DAY" : hm(s)}</time><div>${esc(e.title)}${sub ? `<small>${esc(sub)}</small>` : ""}</div></div>`);
  }
  if (!agenda.authorized && !rows.length) {
    $("#agenda").innerHTML = `<div class="ag-empty">Calendar not connected.<button type="button" id="ag-connect">CONNECT CALENDAR</button></div>`;
    $("#ag-connect").onclick = () => send({ type: "calendar_connect" });
  } else {
    $("#agenda").innerHTML = rows.join("") || `<div class="ag-empty">Nothing scheduled for the next day.</div>`;
  }
}
setInterval(() => { mediaProgress(); if (timers.length) renderAgenda(); }, 1000);

function showWeather(w) {
  $("#wx-temp").textContent = `${Math.round(w.temp_c)}°`;
  $("#wx-cond").textContent = w.condition;
  $("#wx-place").textContent = (w.place || "").toUpperCase();
  $("#wx-extra").textContent = `H ${Math.round(w.high_c)}° · L ${Math.round(w.low_c)}° · ${w.humidity}% RH · ${Math.round(w.wind_kmh)} KM/H`;
}

// ---------------- clock ----------------
function tick() {
  const d = new Date();
  $("#clock-time").textContent = d.toLocaleTimeString([], { hour12: false });
  $("#clock-date").textContent = d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short", year: "numeric" });
}
setInterval(tick, 1000); tick();

// ---------------- camera, confirm ----------------
function cameraRequest(id) {
  if (REMOTE && !vision?.running) return;  // the computer's camera answers
  try {
    if (!vision?.running) throw new Error("camera offline");
    body.classList.add("scanning");
    setTimeout(() => body.classList.remove("scanning"), 2500);
    send({ type: "camera_frame", id, data: vision.snapshot(), media: "image/jpeg" });
  } catch (e) {
    addMsg("progress", "Camera unavailable — click INITIALISE and allow camera access.");
  }
}
function openConfirm(m) {
  $("#confirm-kind").textContent = { bash: "SHELL COMMAND", adb: "PHONE COMMAND", python: "PYTHON CODE" }[m.kind] || "APPLESCRIPT";
  $("#confirm-detail").textContent = m.detail;
  $("#confirm").hidden = false;
}
$("#confirm-yes").onclick = () => send({ type: "confirm_answer", value: true });
$("#confirm-no").onclick = () => send({ type: "confirm_answer", value: false });

// ---------------- controls ----------------
$("#cmd").addEventListener("submit", e => {
  e.preventDefault();
  const v = $("#cmd-input").value.trim();
  if (v || trayCount) send({ type: "command", text: v });
  $("#cmd-input").value = "";
});

// ---------------- attached images ----------------
// Pictures are shrunk here (the model sees up to ~1568 px anyway), sent to the core, and wait in the
// tray until the next typed or spoken command. The core strips location data and keeps them local.
let trayCount = 0, trayMax = 4;
function renderTray(t) {
  if (!t) return;
  const thumbs = (t.thumbs || []).filter(u => /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(u));
  trayCount = thumbs.length; trayMax = t.max || 4;
  const tray = $("#tray");
  tray.hidden = !trayCount;
  tray.innerHTML = thumbs.map((u, i) => `<div class="tray-item"><img src="${u}" alt=""><button type="button" data-i="${i}" title="Remove">✕</button></div>`).join("");
  $("#btn-attach").classList.toggle("on", !!trayCount);
  applyMicMode();
}
$("#tray").onclick = e => {
  const b = e.target.closest("button[data-i]");
  if (b) send({ type: "detach", index: +b.dataset.i });
};
async function shrink(file) {
  const MAX = 1568;
  let src;
  try { src = await createImageBitmap(file, { imageOrientation: "from-image" }); }
  catch {
    src = await new Promise((ok, fail) => {
      const img = new Image(); img.onload = () => ok(img); img.onerror = () => fail(new Error("unreadable"));
      img.src = URL.createObjectURL(file);
    });
  }
  const w = src.width, h = src.height, k = Math.min(1, MAX / Math.max(w, h));
  const c = document.createElement("canvas");
  c.width = Math.round(w * k); c.height = Math.round(h * k);
  c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
  const png = file.type === "image/png" || file.type === "image/gif";
  return c.toDataURL(png ? "image/png" : "image/jpeg", 0.88);
}
async function attachFiles(files) {
  const imgs = [...files].filter(f => f.type.startsWith("image/") || /\.(heic|heif|jpe?g|png|webp|gif)$/i.test(f.name || ""));
  if (!imgs.length) { if (files.length) addMsg("progress", "Only images can be attached."); return; }
  const room = trayMax - trayCount;
  if (room <= 0) { addMsg("progress", `Up to ${trayMax} images at a time.`); return; }
  if (imgs.length > room) addMsg("progress", `Only the first ${room} image${room > 1 ? "s were" : " was"} attached (limit ${trayMax}).`);
  const out = [];
  for (const f of imgs.slice(0, room)) {
    try { out.push(await shrink(f)); }
    catch { addMsg("progress", `Couldn't read ${f.name || "that image"}${/heic|heif/i.test(f.name || f.type) ? " (HEIC photos: export as JPEG first)" : ""}.`); }
  }
  if (out.length) send({ type: "attach", images: out });
}
$("#btn-attach").onclick = () => $("#attach-input").click();
$("#attach-input").onchange = e => { attachFiles(e.target.files); e.target.value = ""; };
document.addEventListener("paste", e => {
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
  if (files.length) { e.preventDefault(); attachFiles(files); }
});
let dragDepth = 0;
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
addEventListener("dragenter", e => { if (!hasFiles(e)) return; e.preventDefault(); if (++dragDepth === 1) body.classList.add("dropping"); });
addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
addEventListener("dragleave", e => { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; body.classList.remove("dropping"); } });
addEventListener("drop", e => {
  if (!hasFiles(e)) return;
  e.preventDefault(); dragDepth = 0; body.classList.remove("dropping");
  attachFiles(e.dataTransfer.files);
});
$("#btn-stop").onclick = () => send({ type: "interrupt" });
function applyMicMode() {
  const push = micMode === "push";
  $("#cmd-input").placeholder = trayCount ? (REMOTE || POCKET ? "Ask about the image, or tap the mic and say it"
      : `Ask about the image${trayCount > 1 ? "s" : ""}${push ? `, or press ${KEYS.listen} and say it` : ""}`)
    : REMOTE || POCKET ? "Tap the mic and speak, or type" : push ? `Press ${KEYS.listen} and speak, or type a command`
    : micMode === "wake" ? "Say “Jarvis…” or type a command" : "Type a command";
  $("#btn-mic").title = REMOTE || POCKET ? "Tap to talk, tap again to send" : push ? `Talk (${KEYS.listen})` : "Microphone";
  $("#btn-mic").hidden = micMode === "off";
  $("#legend-wake").textContent = push ? "✋ clear" : "✋ wake";
  if (push && !hotkeyOk && !REMOTE && !POCKET && !applyMicMode.warned) {
    applyMicMode.warned = true;
    addMsg("progress", HOST_OS === "mac"
      ? "⌘H is waiting for Accessibility permission: System Settings → Privacy & Security → Accessibility → turn on JARVIS. Until then, click the mic button."
      : `${KEYS.listen} isn't available in this desktop session (on Wayland, apps can't watch global keys); press it inside this window, or click the mic button.`);
  }
  refreshState();
}
// ---------------- remote device mic ----------------
async function remoteRecord() {
  player.unlock();
  player.stop();
  send({ type: "barge_in" });  // JARVIS stops talking
  try {
    await remoteMic.start();
    recStart = performance.now();
    scene.setState("hearing");
    refreshState();
  } catch (e) {
    addMsg("progress", e.name === "NotAllowedError" ? "Microphone access was blocked. Allow it for this page in the browser's settings."
      : `Microphone unavailable: ${e.message}`);
  }
}
function remoteSend() {
  const { b64, seconds } = remoteMic.stop();
  refreshState();
  if (seconds < 0.4) return addMsg("progress", "That was too short to hear anything.");
  voiceState = "recognizing"; refreshState();
  send({ type: "voice_audio", data: b64 });
}
$("#btn-mic").onclick = () => {
  if (POCKET) { pocket?.toggleMic(); return refreshState(); }
  if (REMOTE) return remoteMic.active ? remoteSend() : remoteRecord();
  if (micMode === "push")  // click to start recording, click again to send
    return send({ type: ["listening", "hearing"].includes(voiceState) ? "stop_listening" : "listen" });
  micOn = !micOn; send({ type: "mic", enabled: micOn }); refreshState();
};
addEventListener("keydown", e => {
  // Fallback while the global hotkey isn't available: ⌘H (Mac) / Ctrl+Alt+J inside this window
  const mod = HOST_OS === "mac" ? e.metaKey && !e.ctrlKey : e.ctrlKey && e.altKey;
  const k = e.key.toLowerCase();
  if (micMode === "push" && !hotkeyOk && !IN_APP && !REMOTE && mod && k === (HOST_OS === "mac" ? "h" : "j")) { e.preventDefault(); send({ type: "listen" }); }
  // ⌘S / Ctrl+Alt+S sends the recording (in the macOS app the JARVIS menu handles it; this covers a browser tab)
  if (micMode === "push" && !IN_APP && !REMOTE && mod && !e.shiftKey && k === "s" &&
      ["listening", "hearing", "recognizing"].includes(voiceState)) { e.preventDefault(); send({ type: "stop_listening" }); }
  if (e.key === "Escape") { pocket?.cancelMic(); if (remoteMic?.active) { remoteMic.cancel(); refreshState(); } send({ type: "interrupt" }); }
  if (e.key === "/" && document.activeElement !== $("#cmd-input")) { e.preventDefault(); $("#cmd-input").focus(); }
});

// ---------------- gestures ----------------
const cursor = $("#hand-cursor");
let hot = null, wasPinch = false;
const VIEWS = ["idle", "globe", "diagnostics"];
function onIntent(name, arg) {
  if (name === "rotate") return scene.grab(arg.dx * 1.4, arg.dy * 1.4);
  if (name === "zoom") return scene.zoomBy(arg);
  if (name === "swipe_left" || name === "swipe_right") {
    const i = (VIEWS.indexOf(body.dataset.mode) + (name === "swipe_right" ? 1 : VIEWS.length - 1)) % VIEWS.length;
    setMode(VIEWS[i]);
    return;
  }
  const labels = { wake: micMode === "push" ? "✋ CLEAR" : "✋ LISTENING", stop: "✊ HALT", confirm: "👍 CONFIRMED", cancel: "👎 CANCELLED", scan: "✌ SCAN INITIATED" };
  $("#cam-gesture").textContent = labels[name] || name;
  if (name === "confirm" && !$("#confirm").hidden) send({ type: "confirm_answer", value: true });
  else if (name === "cancel" && !$("#confirm").hidden) send({ type: "confirm_answer", value: false });
  else send({ type: "gesture", name });
  scene.pulse();
}
function onCursor(c) {
  cursor.classList.toggle("on", !!c.active);
  if (!c.active) { hot?.classList.remove("hot"); hot = null; wasPinch = false; return; }
  cursor.style.transform = `translate(${c.x}px, ${c.y}px)`;
  cursor.classList.toggle("pinch", c.pinch);
  // "Touch" holographic UI: pinch over a button/link to press it
  const el = document.elementFromPoint(c.x, c.y)?.closest("button, a, .panel-close");
  if (el !== hot) { hot?.classList.remove("hot"); hot = el; hot?.classList.add("hot"); }
  if (c.pinch && !wasPinch && hot) { hot.click(); scene.pulse(); }
  wasPinch = c.pinch;
}

// ---------------- boot ----------------
function bootLog(line) {
  const el = $("#boot-log");
  if (el) el.textContent += line + "\n";
}
const BOOT_LINES = ["› initialising holographic projection matrix", "› calibrating gyroscopic rings", "› loading voice synthesis module", "› arc reactor output nominal"];
BOOT_LINES.forEach((l, i) => setTimeout(() => bootLog(l), 250 + i * 320));

if (POCKET) {
  $("#boot-btn").textContent = "START";
  $(".boot-note").textContent = "JARVIS Pocket runs on this device · tap the mic to talk · ⚙ for settings";
} else if (REMOTE) {
  $("#boot-btn").textContent = "CONNECT";
  $(".boot-note").textContent = "Tap the mic to talk to JARVIS on your computer";
} else {
  initDevices(send);
  initSentinel(send);
}
$("#boot-btn").onclick = async () => {
  $("#boot").classList.add("gone");
  scene.pulse(); setTimeout(() => scene.pulse(), 300);
  if (POCKET) {  // speech output must be started by a tap once (iOS)
    pocket?.voice.unlock();
    if (!pocket?.client) openPocketSettings();
    return;
  }
  if (REMOTE) {  // phones and tablets: no hand tracking (it would drain the battery); sound needs this tap
    player.unlock();
    if (!TOUCH) document.documentElement.requestFullscreen?.().catch(() => {});
    return;
  }
  document.documentElement.requestFullscreen?.().catch(() => {});
  try {
    const { Vision } = await import("./hands.js");
    vision = new Vision({
      video: $("#cam"), overlay: $("#cam-overlay"), onIntent, onCursor,
      onHands: hands => scene.setHands(hands),
      onGestureLabel: t => { if (!onIntent.lock) $("#cam-gesture").textContent = t; },
      onStatus: s => { $("#cam-status").textContent = s; $("#cam-status").classList.toggle("on", s === "ONLINE"); },
    });
    await vision.start();
  } catch (e) {
    console.error(e);
    $("#cam-status").textContent = "DENIED";
    addMsg("progress", `Optical sensor offline: ${e.message}`);
  }
};

// ---------------- pocket settings (API key, name, model) ----------------
function openPocketSettings() {
  const s = pocket?.settings || {};
  $("#ps-key").value = "";
  $("#ps-key").placeholder = s.apiKey ? `saved (…${s.apiKey.slice(-4)}) · paste a new key to replace it` : "sk-ant-…";
  $("#ps-owner").value = s.owner || "sir";
  $("#ps-model").value = s.model || "claude-opus-5-5";
  $("#ps-forget").hidden = !s.apiKey;
  $("#pocket-setup").hidden = false;
}
function initPocketSettings(mod) {
  $("#btn-pocket").hidden = false;
  $("#btn-pocket").onclick = openPocketSettings;
  $("#ps-close").onclick = () => { $("#pocket-setup").hidden = true; };
  $("#ps-forget").onclick = () => {
    pocket.applySettings({ apiKey: "" });
    $("#pocket-setup").hidden = true;
    addMsg("progress", "API key removed from this device.");
  };
  $("#ps-form").onsubmit = e => {
    e.preventDefault();
    const key = $("#ps-key").value.trim();
    if (key && !/^sk-ant-[\w-]{20,}$/.test(key)) { $("#ps-key").setCustomValidity("That doesn't look like an Anthropic API key (sk-ant-…)"); $("#ps-key").reportValidity(); return; }
    $("#ps-key").setCustomValidity("");
    pocket.applySettings({ ...(key ? { apiKey: key } : {}), owner: $("#ps-owner").value.trim() || "sir", model: $("#ps-model").value });
    $("#pocket-setup").hidden = true;
    addMsg("progress", pocket.client ? "Settings saved on this device." : "Saved. Add an API key to switch the brain on.");
  };
  $("#ps-key").oninput = () => $("#ps-key").setCustomValidity("");
}

window.jarvis = { handle, scene, listen: () => send({ type: "listen" }), stopListening: () => send({ type: "stop_listening" }) }; // console debugging: jarvis.handle({type: "hud", action: "panel", ...})
connect();
