// "Devices" panel on the computer: switch remote access on/off, pair a phone/tablet/PC with a
// code or QR code, and remove paired devices. Only this computer's own window can do this.

const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let state = { enabled: false, addresses: [], code: null, code_left: 0, devices: [] };
let send = () => {};
let countdown = null;
let qrReady = null;

function loadQR() {
  qrReady ??= new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js";
    s.onload = () => ok(window.qrcode);
    s.onerror = () => { qrReady = null; fail(new Error("QR code library unavailable")); };
    document.head.append(s);
  });
  return qrReady;
}

async function drawQR(url) {
  const box = $("#dv-qr");
  try {
    const qrcode = await loadQR();
    const q = qrcode(0, "M");
    q.addData(url);
    q.make();
    box.innerHTML = q.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  } catch { box.innerHTML = ""; }
}

const ago = t => {
  const s = Date.now() / 1000 - t;
  return s < 3600 ? "this hour" : s < 86400 ? "today" : `${Math.floor(s / 86400)} d ago`;
};

function render() {
  const st = state, on = st.enabled;
  $("#dv-toggle").textContent = on ? "TURN OFF" : "TURN ON";
  $("#dv-toggle").classList.toggle("on", on);
  $("#dv-status").textContent = on ? "ON · devices on your Wi-Fi can connect" : "OFF · only this computer can use JARVIS";
  $("#dv-on").hidden = !on;
  $("#btn-devices").classList.toggle("on", on);
  if (!on) return;
  $("#dv-addr").innerHTML = st.addresses.map(a => `<code>${esc(a)}</code>`).join("") || "<em>No network connection</em>";
  const pairing = !!st.code;
  $("#dv-pairing").hidden = !pairing;
  $("#dv-pair").hidden = pairing;
  clearInterval(countdown);
  if (pairing) {
    $("#dv-code").textContent = st.code.replace(/(\d{3})(\d{3})/, "$1 $2");
    const url = `${st.addresses[0]}/pair#code=${st.code}`;
    drawQR(url);
    let left = st.code_left;
    const tick = () => {
      $("#dv-left").textContent = left > 0 ? `expires in ${left}s` : "expired";
      if (left-- <= 0) { clearInterval(countdown); send({ type: "remote", action: "status" }); }
    };
    tick();
    countdown = setInterval(tick, 1000);
  }
  $("#dv-list").innerHTML = st.devices.length ? st.devices.map(d =>
    `<li><span><b>${esc(d.name)}</b><small>paired ${new Date(d.paired * 1000).toLocaleDateString()} · used ${ago(d.seen)}</small></span>
     <button type="button" data-revoke="${esc(d.id)}">REMOVE</button></li>`).join("") : "<li class='none'>No devices paired yet.</li>";
}

export function initDevices(sendFn) {
  send = sendFn;
  $("#btn-devices").hidden = false;
  $("#btn-devices").onclick = () => { $("#devices").hidden = false; send({ type: "remote", action: "status" }); };
  $("#dv-close").onclick = () => { $("#devices").hidden = true; };
  $("#devices").onclick = e => { if (e.target.id === "devices") $("#devices").hidden = true; };
  $("#dv-toggle").onclick = () => send({ type: "remote", action: state.enabled ? "disable" : "enable" });
  $("#dv-pair").onclick = () => send({ type: "remote", action: "pair" });
  $("#dv-list").onclick = e => {
    const b = e.target.closest("[data-revoke]");
    if (!b) return;
    if (b.dataset.sure) return send({ type: "remote", action: "revoke", id: b.dataset.revoke });
    b.dataset.sure = "1"; b.textContent = "SURE?";  // second tap removes (it'll need a new code)
    setTimeout(() => { delete b.dataset.sure; b.textContent = "REMOVE"; }, 3000);
  };
}

export function updateDevices(st) {
  state = st;
  render();
}
