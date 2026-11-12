// "Sentinel" panel on the computer: switch JARVIS's scheduled background checks on or off, see
// the schedule, the next and the last check, and run one now. Only this computer's own window can.

const $ = s => document.querySelector(s);
const KIND = { text: "Status check", screen: "Screen check" };
const clock = ms => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
let send = () => {};

export function updateSentinel(s) {
  if (!s) return;
  const btn = $("#btn-sentinel");
  btn.classList.toggle("on", s.enabled);
  btn.classList.toggle("active", s.enabled && s.active && !s.offline);
  btn.classList.toggle("checking", !!s.checking);

  const status = !s.enabled ? "OFF"
    : s.offline ? "ON · CAN'T RUN"
    : s.checking ? `${KIND[s.checking].toUpperCase()} RUNNING…`
    : s.active ? "ON · WATCHING" : "ON · OUTSIDE ITS HOURS";
  $("#sn-status").textContent = status;
  $("#sn-toggle").textContent = s.enabled ? "TURN OFF" : "TURN ON";
  $("#sn-toggle").classList.toggle("on", s.enabled);

  const parts = [];
  if (s.text_minutes > 0) parts.push(`a status check every ${+s.text_minutes} minutes`);
  if (s.screen_minutes > 0) parts.push(`a screen check every ${+s.screen_minutes} minutes`);
  $("#sn-schedule").textContent = `${s.window} every day: ${parts.join(" and ") || "no checks scheduled"}.`;
  $("#sn-model").textContent = s.model;

  $("#sn-next").textContent = s.offline ? `Can't run: ${s.offline}.`
    : !s.enabled ? "Sentinel is off."
    : s.next ? `${KIND[s.next.kind]} at ${clock(s.next.at)}${new Date(s.next.at).toDateString() === new Date().toDateString() ? "" : " tomorrow"}`
    : "Nothing scheduled.";

  const l = s.last;
  $("#sn-last").textContent = !l ? "None yet."
    : `${KIND[l.kind]} at ${clock(l.at)}: ` + (l.error ? `failed (${l.error})` : l.said ? `said “${l.said}”` : "nothing to report.");
  $("#sn-count").textContent = s.counts.checks
    ? `${s.counts.checks} check${s.counts.checks > 1 ? "s" : ""} since JARVIS started · spoke ${s.counts.spoke} time${s.counts.spoke === 1 ? "" : "s"}`
      + (s.counts.failed ? ` · ${s.counts.failed} failed` : "")
    : "";
  for (const id of ["#sn-text", "#sn-screen"]) $(id).disabled = !!s.offline || !!s.checking;
  btn.title = `Sentinel: ${status.toLowerCase()} · ${s.window}` + (s.enabled && s.next && !s.offline ? ` · next check ${clock(s.next.at)}` : "");
}

export function initSentinel(sendFn) {
  send = sendFn;
  $("#btn-sentinel").hidden = false;
  $("#btn-sentinel").onclick = () => { $("#sentinel").hidden = false; send({ type: "sentinel", action: "status" }); };
  $("#sn-close").onclick = () => { $("#sentinel").hidden = true; };
  $("#sentinel").onclick = e => { if (e.target.id === "sentinel") $("#sentinel").hidden = true; };
  $("#sn-toggle").onclick = () => send({ type: "sentinel", action: $("#sn-toggle").classList.contains("on") ? "off" : "on" });
  $("#sn-text").onclick = () => send({ type: "sentinel", action: "check", kind: "text" });
  $("#sn-screen").onclick = () => send({ type: "sentinel", action: "check", kind: "screen" });
}
