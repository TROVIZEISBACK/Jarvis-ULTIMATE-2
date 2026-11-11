// Webcam vision: MediaPipe hand gestures + face targeting, running locally in the browser.
import { FilesetResolver, GestureRecognizer, FaceDetector } from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";

const WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const GESTURE_MODEL = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task";
const FACE_MODEL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite";
const BONES = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];

// Gesture -> intent, with how long it must be held (ms).
const INTENTS = {
  Open_Palm: { name: "wake", hold: 700 },
  Closed_Fist: { name: "stop", hold: 450 },
  Thumb_Up: { name: "confirm", hold: 450 },
  Thumb_Down: { name: "cancel", hold: 450 },
  Victory: { name: "scan", hold: 800 },
};
const COOLDOWN = 2200;

export class Vision {
  constructor({ video, overlay, onIntent, onHands, onCursor, onStatus, onGestureLabel }) {
    Object.assign(this, { video, overlay, onIntent, onHands, onCursor, onStatus, onGestureLabel });
    this.ctx = overlay.getContext("2d");
    this.held = { name: null, since: 0 };
    this.lastFired = {};
    this.faces = [];
    this.pinch = [false, false];
    this.prevPinchPt = null;
    this.prevTwoDist = null;
    this.running = false;
    this.interval = 50;
    this.accent = "#2fd8ff";
    this.accentAt = 0;
  }

  async start() {
    this.onStatus?.("REQUESTING");
    const base = { width: { ideal: 960 }, height: { ideal: 540 }, frameRate: { ideal: 20 } };
    let stream = await navigator.mediaDevices.getUserMedia({ video: { ...base, facingMode: "user" }, audio: false });
    // Prefer an external webcam (e.g. USB) over the built-in one when available.
    try {
      const cams = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput");
      const builtIn = /macbook|facetime|built-?in|integrated/i;
      const ext = cams.find(d => d.label && !builtIn.test(d.label));
      const curId = stream.getVideoTracks()[0]?.getSettings().deviceId;
      if (ext && ext.deviceId !== curId) {
        stream.getTracks().forEach(t => t.stop());
        stream = await navigator.mediaDevices.getUserMedia({ video: { ...base, deviceId: { exact: ext.deviceId } }, audio: false });
      }
    } catch (e) { console.warn("external camera selection failed", e); }
    this.video.srcObject = stream;
    await this.video.play();
    this.onStatus?.("LOADING MODELS");
    const files = await FilesetResolver.forVisionTasks(WASM);
    this.gestures = await GestureRecognizer.createFromOptions(files, {
      baseOptions: { modelAssetPath: GESTURE_MODEL, delegate: "GPU" }, runningMode: "VIDEO", numHands: 2,
      minHandDetectionConfidence: 0.6, minTrackingConfidence: 0.5,
    });
    try {
      this.faceDet = await FaceDetector.createFromOptions(files, {
        baseOptions: { modelAssetPath: FACE_MODEL, delegate: "GPU" }, runningMode: "VIDEO", minDetectionConfidence: 0.6,
      });
    } catch (e) { console.warn("face detector unavailable", e); }
    this.running = true;
    this.onStatus?.("ONLINE");
    this.loop();
  }

  /** JPEG snapshot of the current camera frame for the brain. Returns base64 (no prefix). */
  snapshot(maxW = 1280) {
    const v = this.video;
    if (!v.videoWidth) throw new Error("camera not ready");
    const scale = Math.min(1, maxW / v.videoWidth);
    const c = document.createElement("canvas");
    c.width = Math.round(v.videoWidth * scale); c.height = Math.round(v.videoHeight * scale);
    const g = c.getContext("2d");
    g.translate(c.width, 0); g.scale(-1, 1); // mirror like the preview
    g.drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85).split(",")[1];
  }

  // Recognition runs on the same thread as the 3D scene, so it's paced: ~20 Hz for hands
  // (slower if a pass is expensive on this computer) and ~3 Hz for faces; with no hand in view
  // for a few seconds it drops to ~6 Hz / 1 Hz until one appears.
  loop() {
    if (!this.running) return;
    const now = performance.now();
    const due = now - (this.lastRun || 0) >= this.interval;
    if (due && !document.hidden && this.video.readyState >= 2 && this.video.currentTime !== this.lastVideoTime) {
      this.lastVideoTime = this.video.currentTime;
      this.lastRun = now;
      const res = this.gestures.recognizeForVideo(this.video, now);
      if (res.landmarks?.length) this.lastSeen = now;
      const resting = now - (this.lastSeen || 0) > 2500;  // no hands for a while: look less often
      if (this.faceDet && now - (this.lastFace || 0) > (resting ? 1000 : 350)) {
        this.lastFace = now;
        this.faces = this.faceDet.detectForVideo(this.video, now).detections || [];
      }
      const cost = performance.now() - now;
      this.interval = resting ? 160 : Math.min(110, Math.max(50, cost * 3));
      this.process(res, now);
    }
    requestAnimationFrame(() => this.loop());
  }

  process(res, now) {
    const hands = (res.landmarks || []).map((lm, i) => {
      const mirrored = lm.map(p => ({ x: 1 - p.x, y: p.y, z: p.z }));
      const top = res.gestures?.[i]?.[0];
      const d = Math.hypot(lm[4].x - lm[8].x, lm[4].y - lm[8].y, (lm[4].z - lm[8].z));
      const span = Math.hypot(lm[0].x - lm[9].x, lm[0].y - lm[9].y) || 0.1;
      const pinch = d / span < (this.pinch[i] ? 0.42 : 0.3); // hysteresis
      this.pinch[i] = pinch;
      return { landmarks: mirrored, gesture: top?.categoryName, score: top?.score ?? 0, pinch };
    });
    this.onHands?.(hands);
    this.draw(hands);

    // Cursor from the first hand's index fingertip (between thumb & index when pinching)
    if (hands.length) {
      const h = hands[0], a = h.landmarks[8], b = h.landmarks[4];
      const pt = h.pinch ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : a;
      // Map the central 70% of the camera to the full screen for comfortable reach
      const sx = Math.min(1, Math.max(0, (pt.x - 0.15) / 0.7)), sy = Math.min(1, Math.max(0, (pt.y - 0.15) / 0.7));
      this.onCursor?.({ x: sx * innerWidth, y: sy * innerHeight, pinch: h.pinch, active: true });

      // Pinch-drag rotates; two-hand pinch zooms
      if (hands.length === 2 && hands[0].pinch && hands[1].pinch) {
        const p0 = hands[0].landmarks[8], p1 = hands[1].landmarks[8];
        const dist = Math.hypot(p0.x - p1.x, p0.y - p1.y);
        if (this.prevTwoDist) this.onIntent?.("zoom", dist / this.prevTwoDist);
        this.prevTwoDist = dist; this.prevPinchPt = null;
      } else if (h.pinch) {
        if (this.prevPinchPt) this.onIntent?.("rotate", { dx: pt.x - this.prevPinchPt.x, dy: pt.y - this.prevPinchPt.y });
        this.prevPinchPt = pt; this.prevTwoDist = null;
      } else { this.prevPinchPt = null; this.prevTwoDist = null; }

      // Swipe with an open palm switches hologram views
      const wrist = h.landmarks[0];
      if (this.lastWrist && h.gesture === "Open_Palm") {
        const vx = (wrist.x - this.lastWrist.x) / Math.max(1, now - this.lastWrist.t) * 1000;
        if (Math.abs(vx) > 2.2) this.fire(vx > 0 ? "swipe_right" : "swipe_left", now);
      }
      this.lastWrist = { x: wrist.x, t: now };
    } else {
      this.onCursor?.({ active: false });
      this.prevPinchPt = null; this.prevTwoDist = null; this.lastWrist = null;
    }

    // Held gestures -> intents
    const g = hands.find(h => INTENTS[h.gesture] && h.score > 0.6 && !h.pinch)?.gesture || null;
    if (g !== this.held.name) this.held = { name: g, since: now };
    this.onGestureLabel?.(hands.map(h => h.pinch ? "PINCH" : (h.gesture || "—").replace("_", " ")).join(" · ") || "NO HANDS");
    if (g && now - this.held.since > INTENTS[g].hold) {
      this.fire(INTENTS[g].name, now);
      this.held.since = now + 10_000; // require release before repeating
    }
  }

  fire(name, now) {
    if (now - (this.lastFired[name] || 0) < COOLDOWN) return;
    this.lastFired[name] = now;
    this.onIntent?.(name);
  }

  draw(hands) {
    const c = this.overlay, g = this.ctx;
    const w = c.clientWidth, h = c.clientHeight, dpr = Math.min(devicePixelRatio, 1.5);
    if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    // Overlay canvas is CSS-mirrored, so draw in raw (unmirrored) coordinates.
    const vw = this.video.videoWidth || 1, vh = this.video.videoHeight || 1;
    const cover = Math.max(w / vw, h / vh), ox = (w - vw * cover) / 2, oy = (h - vh * cover) / 2;
    const X = x => ox + x * vw * cover, Y = y => oy + y * vh * cover;
    if (performance.now() - this.accentAt > 1000) {  // getComputedStyle forces a style pass; don't do it every frame
      this.accentAt = performance.now();
      this.accent = getComputedStyle(document.body).getPropertyValue("--c").trim() || "#2fd8ff";
    }
    const accent = this.accent;

    // face targeting reticles
    for (const f of this.faces) {
      const bb = f.boundingBox;
      const x = ox + bb.originX * cover, y = oy + bb.originY * cover, bw = bb.width * cover, bh = bb.height * cover;
      g.strokeStyle = "#ffb547"; g.lineWidth = 1.5;
      const L = Math.min(bw, bh) * 0.25;
      for (const [cx, cy, sx, sy] of [[x, y, 1, 1], [x + bw, y, -1, 1], [x, y + bh, 1, -1], [x + bw, y + bh, -1, -1]]) {
        g.beginPath(); g.moveTo(cx, cy + sy * L); g.lineTo(cx, cy); g.lineTo(cx + sx * L, cy); g.stroke();
      }
      g.beginPath(); g.arc(x + bw / 2, y + bh / 2, Math.max(bw, bh) * 0.62, performance.now() / 600, performance.now() / 600 + 1.2); g.stroke();
      g.save(); g.scale(-1, 1); g.font = "10px 'Share Tech Mono'"; g.fillStyle = "#ffb547";
      g.fillText(`SUBJECT · ${(f.categories?.[0]?.score * 100 || 0).toFixed(0)}%`, -(x + bw), y - 6); g.restore();
    }
    // hand skeletons
    for (const hand of hands) {
      const raw = hand.landmarks.map(p => ({ x: 1 - p.x, y: p.y }));
      g.beginPath();
      for (const [a, b] of BONES) { g.moveTo(X(raw[a].x), Y(raw[a].y)); g.lineTo(X(raw[b].x), Y(raw[b].y)); }
      g.strokeStyle = accent; g.globalAlpha = 0.25; g.lineWidth = 7; g.stroke();  // glow
      g.globalAlpha = 1; g.lineWidth = 2; g.stroke();
      g.fillStyle = hand.pinch ? "#ffc96b" : "#e6fbff";
      for (const p of raw) { g.beginPath(); g.arc(X(p.x), Y(p.y), 2.6, 0, Math.PI * 2); g.fill(); }
    }
  }
}
