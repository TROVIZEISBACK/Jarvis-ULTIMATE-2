// Holographic 3D core for JARVIS: arc-reactor core inside a geodesic cage, energy beams,
// gyroscopic rings, particle streams, a voice-reactive particle shell, holo-globe, floor grid,
// and a 3D mirror of the user's hands.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const PALETTE = {
  idle: 0x2fd8ff, thinking: 0xffb547, working: 0xffa13a, speaking: 0x5fe6ff,
  alert: 0xff3b4e, scan: 0x3dffc4, party: 0xff4fd8, briefing: 0x63c8ff, diagnostics: 0x63c8ff, globe: 0x2fd8ff,
};
// Mode themes: the hologram's resting colour, and an energy multiplier at full power.
const THEME = { standard: 0x2fd8ff, math: 0xb08cff, science: 0x4dffa0, full: 0xffb21f };
const HAND_BONES = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],
  [9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];

const TIPS = new Set([4, 8, 12, 16, 20]);
const TAU = Math.PI * 2, GOLDEN = Math.PI * (3 - Math.sqrt(5));
// Round additive sprite shared by every particle layer.
const DISC_FRAG = `uniform vec3 uColor; varying float vA;
  void main(){ float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
    gl_FragColor = vec4(uColor, vA * smoothstep(0.5, 0.0, d)); }`;

/** Flat arc bands (ring sectors) merged into one geometry: one draw call however many bands.
 *  uv.x counts blocks along each band, uv.y runs across it, `end` is 0..1 along it (to fade the tips). */
function bandGeometry(bands) {
  const pos = [], uv = [], end = [], idx = [];
  for (const { r0, r1, a0 = 0, span, blocks = 1, steps = Math.max(3, Math.ceil(span / 0.07)) } of bands) {
    const base = pos.length / 3;
    for (let i = 0; i <= steps; i++) {
      const f = i / steps, a = a0 + span * f, c = Math.cos(a), sn = Math.sin(a);
      pos.push(c * r0, sn * r0, 0, c * r1, sn * r1, 0);
      uv.push(f * blocks, 0, f * blocks, 1);
      end.push(f, f);
    }
    for (let i = 0; i < steps; i++) { const k = base + i * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setAttribute("end", new THREE.Float32BufferAttribute(end, 1));
  g.setIndex(idx);
  return g;
}

export class HoloScene {
  constructor(canvas) {
    this.canvas = canvas;
    // Performance: no MSAA (the bloom composer bypasses it anyway) and a capped, adaptive
    // render scale. Retina at 2x with full-res bloom was the main source of lag.
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance" });
    this.maxRatio = Math.min(devicePixelRatio, 1.25);
    this.ratio = this.maxRatio;
    this.perf = { frames: 0, time: 0, lastDrop: -1e9 };
    this._s = new THREE.Vector3();
    this.renderer.setPixelRatio(this.ratio);
    this.renderer.setClearColor(0x01050b, 1);
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x01050b, 0.06);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);
    this.camDist = 7.2;
    this.camera.position.set(0, 0.4, this.camDist);

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 1.0, 0.5, 0.12);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.color = new THREE.Color(PALETTE.idle);
    this.targetColor = new THREE.Color(PALETTE.idle);
    this.accent = new THREE.Color(0xffb547);
    this.colorMats = [];
    this.state = "idle";
    this.mode = "idle";
    this.level = 0; this.levelTarget = 0;
    this.mic = 0;
    this.energy = 1; this.energyTarget = 1;
    this.base = PALETTE.idle; this.boost = 1; this.bloomBase = 1.0;
    // Uniforms shared by the detail layers. Everything they animate runs in shaders on the GPU,
    // so the extra detail costs no JavaScript per frame beyond updating these numbers.
    this.fx = { uTime: { value: 0 }, uFlow: { value: 0 }, uLevel: { value: 0 }, uEnergy: { value: 1 }, uColor: { value: this.color } };
    this.rot = new THREE.Euler(0.15, 0, 0);
    this.rotVel = new THREE.Vector2(0, 0.0015);
    this.zoom = 1; this.zoomTarget = 1;
    this.clock = new THREE.Clock();
    this.pulses = [];
    this.mouse = new THREE.Vector2();

    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.buildCore();
    this.buildCage();
    this.buildBeams();
    this.buildRings();
    this.buildStreams();
    this.buildLoadRing();
    this.buildSatellites();
    this.buildShell();
    this.buildGlobe();
    this.buildFloor();
    this.buildDust();
    this.buildHands();
    this.bindInput();
    this.resize();
    addEventListener("resize", () => this.resize());
    this.renderer.setAnimationLoop(() => this.frame());
  }

  mat(opts = {}, colored = true) {
    const m = new THREE.LineBasicMaterial({ color: this.color.clone(), transparent: true, opacity: 0.8,
      blending: THREE.AdditiveBlending, depthWrite: false, ...opts });
    if (colored) this.colorMats.push(m);
    return m;
  }

  // ---------- builders ----------
  buildCore() {
    this.core = new THREE.Group();
    this.root.add(this.core);
    // Fresnel glow sphere
    this.coreUniforms = { uColor: { value: this.color }, uLevel: { value: 0 }, uTime: { value: 0 } };
    const glow = new THREE.Mesh(new THREE.SphereGeometry(0.62, 40, 28), new THREE.ShaderMaterial({
      uniforms: this.coreUniforms, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
      vertexShader: `varying vec3 vN; varying vec3 vV; uniform float uLevel; uniform float uTime;
        void main(){ vec3 p = position * (1.0 + 0.06*sin(uTime*3.0 + position.y*8.0)*uLevel);
          vec4 mv = modelViewMatrix * vec4(p,1.0); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz);
          gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `varying vec3 vN; varying vec3 vV; uniform vec3 uColor; uniform float uLevel;
        void main(){ float f = pow(1.0 - abs(dot(vN, vV)), 2.2); float c = 0.1 + 0.28*uLevel;
          gl_FragColor = vec4(uColor * (f*1.25 + c), f*0.85 + c*0.5); }`,
    }));
    this.core.add(glow);
    // Bright nucleus
    const nucleus = new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    this.nucleus = nucleus;
    this.core.add(nucleus);
    // Wireframe lattice
    this.lattice = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.44, 1)), this.mat({ opacity: 0.55 }));
    this.core.add(this.lattice);
    this.lattice2 = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(0.3, 0)), this.mat({ opacity: 0.9 }));
    this.core.add(this.lattice2);
    // Arc-reactor segments (one merged mesh)
    const rm = new THREE.MeshBasicMaterial({ color: this.color.clone(), transparent: true, opacity: 0.35,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
    this.colorMats.push(rm);
    this.reactor = new THREE.Mesh(bandGeometry(Array.from({ length: 10 }, (_, i) =>
      ({ r0: 0.72, r1: 0.82, a0: i / 10 * TAU, span: TAU / 10 * 0.72 }))), rm);
    this.core.add(this.reactor);
    // Three orbit rings close around the nucleus, like an atom
    const R = 0.56, pts = [];
    for (let k = 0; k < 3; k++) for (let i = 0; i < 48; i++) {
      if (i % 12 === 11) continue;  // small gaps
      for (const a of [i / 48 * TAU, (i + 1) / 48 * TAU]) {
        const c = Math.cos(a) * R, sn = Math.sin(a) * R;
        pts.push(...(k === 0 ? [c, sn, 0] : k === 1 ? [c, 0, sn] : [0, c, sn]));
      }
    }
    const ag = new THREE.BufferGeometry();
    ag.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.atom = new THREE.LineSegments(ag, this.mat({ opacity: 0.5 }));
    this.core.add(this.atom);
  }

  /** Geodesic cage around the core with a glowing node at every joint. */
  buildCage() {
    const R = 1.16, ico = new THREE.IcosahedronGeometry(R, 2);
    this.cage = new THREE.Group();
    this.cage.add(new THREE.LineSegments(new THREE.EdgesGeometry(ico), this.mat({ opacity: 0.5 })));
    const p = ico.attributes.position, seen = new Set(), pts = [], seeds = [];
    for (let i = 0; i < p.count; i++) {
      const key = `${p.getX(i).toFixed(3)},${p.getY(i).toFixed(3)},${p.getZ(i).toFixed(3)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      pts.push(p.getX(i), p.getY(i), p.getZ(i));
      seeds.push(Math.random());
    }
    ico.dispose();
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    g.setAttribute("seed", new THREE.Float32BufferAttribute(seeds, 1));
    this.cage.add(new THREE.Points(g, new THREE.ShaderMaterial({
      uniforms: this.fx, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      // nodes twinkle, and a band of light climbs the cage every few seconds
      vertexShader: `attribute float seed; uniform float uTime; uniform float uLevel; varying float vA;
        void main(){ vec4 mv = modelViewMatrix * vec4(position, 1.0);
          float sweep = smoothstep(0.3, 0.0, abs(position.y / ${R.toFixed(2)} - (fract(uTime*0.16)*2.8 - 1.4)));
          float tw = 0.5 + 0.5*sin(uTime*(1.5 + seed*3.0) + seed*40.0);
          vA = 0.3 + 0.35*tw + sweep + uLevel*0.6;
          gl_PointSize = (2.0 + sweep*3.0 + uLevel*3.0) * (7.0 / -mv.z);
          gl_Position = projectionMatrix * mv; }`,
      fragmentShader: DISC_FRAG,
    })));
    this.core.add(this.cage);
  }

  /** Energy beams radiating from the nucleus: 16 beams, each two crossed tapered blades, in one mesh.
   *  They lengthen, flicker and send light packets outward with voice level and activity. */
  buildBeams() {
    const N = 16, pos = [], tt = [], side = [], seed = [], idx = [];
    const dir = new THREE.Vector3(), up = new THREE.Vector3(), u = new THREE.Vector3(), v = new THREE.Vector3();
    for (let b = 0; b < N; b++) {
      const y = 1 - (b + 0.5) / N * 2, r = Math.sqrt(1 - y * y), th = GOLDEN * b + Math.random() * 0.8;
      dir.set(Math.cos(th) * r, y, Math.sin(th) * r).normalize();
      up.set(0, 1, 0); if (Math.abs(dir.y) > 0.9) up.set(1, 0, 0);
      u.crossVectors(dir, up).normalize(); v.crossVectors(dir, u);
      const len = 1.7 + Math.random() * 1.6, w = 0.02 + Math.random() * 0.024, sd = Math.random();
      for (const axis of [u, v]) {
        const base = pos.length / 3;
        for (const [t, sgn] of [[0, -1], [0, 1], [1, -1], [1, 1]]) {
          const ww = w * (t ? 0.15 : 1) * sgn;
          pos.push(dir.x * len * t + axis.x * ww, dir.y * len * t + axis.y * ww, dir.z * len * t + axis.z * ww);
          tt.push(t); side.push(sgn); seed.push(sd);
        }
        idx.push(base, base + 1, base + 2, base + 1, base + 3, base + 2);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("aT", new THREE.Float32BufferAttribute(tt, 1));
    g.setAttribute("aSide", new THREE.Float32BufferAttribute(side, 1));
    g.setAttribute("aSeed", new THREE.Float32BufferAttribute(seed, 1));
    g.setIndex(idx);
    this.beams = new THREE.Mesh(g, new THREE.ShaderMaterial({
      uniforms: this.fx, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      vertexShader: `attribute float aT; attribute float aSide; attribute float aSeed;
        uniform float uTime; uniform float uLevel; uniform float uEnergy; varying vec4 vB;
        void main(){ float flick = 0.5 + 0.5*sin(uTime*(1.3 + aSeed*2.4) + aSeed*50.0);
          vB = vec4(aT, aSide, aSeed, flick);
          float reach = 0.6 + 0.25*flick + uLevel*0.3 + (uEnergy - 1.0)*0.06;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position * reach, 1.0); }`,
      fragmentShader: `uniform vec3 uColor; uniform float uTime; uniform float uLevel; uniform float uEnergy; varying vec4 vB;
        void main(){ float across = 1.0 - vB.y*vB.y, along = pow(1.0 - vB.x, 1.6);
          float packet = smoothstep(0.09, 0.0, abs(vB.x - fract(uTime*(0.3 + vB.z*0.3) + vB.z*7.0))) * (1.0 - vB.x);
          float power = 0.3 + 0.25*vB.w + uLevel*0.4 + (uEnergy - 1.0)*0.14;
          float root = smoothstep(0.02, 0.16, vB.x);  // start outside the nucleus, so 32 blades don't pile up into a white blob
          gl_FragColor = vec4(mix(uColor, vec3(1.0), along*0.3), across * root * (along*power + packet*0.55)); }`,
    }));
    this.beams.frustumCulled = false;
    this.core.add(this.beams);
  }

  arcRing(radius, arcs, fill, opacity = 0.8) {
    const pts = [];
    // one segment per ~3.5 degrees: smooth at this size, and short arcs no longer cost 64 segments each
    const segs = Math.max(2, Math.ceil((TAU / arcs) * fill / 0.06));
    for (let i = 0; i < arcs; i++) {
      const a0 = (i / arcs) * Math.PI * 2, span = (Math.PI * 2 / arcs) * fill;
      for (let s = 0; s < segs; s++) {
        const t0 = a0 + span * (s / segs), t1 = a0 + span * ((s + 1) / segs);
        pts.push(Math.cos(t0) * radius, Math.sin(t0) * radius, 0, Math.cos(t1) * radius, Math.sin(t1) * radius, 0);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return new THREE.LineSegments(g, this.mat({ opacity }));
  }

  tickRing(radius, count, len, opacity = 0.7, every = 5) {
    const pts = [];
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2, l = i % every === 0 ? len * 2.2 : len;
      pts.push(Math.cos(a) * radius, Math.sin(a) * radius, 0, Math.cos(a) * (radius + l), Math.sin(a) * (radius + l), 0);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return new THREE.LineSegments(g, this.mat({ opacity }));
  }

  /** Barcode-like ring: ticks of varied length in irregular groups. */
  codeRing(radius, count, opacity = 0.4) {
    const pts = [];
    for (let i = 0; i < count; i++) {
      if (Math.sin(i * 12.9898) * 43758.5453 % 1 < -0.25 || Math.floor(i / 9) % 4 === 3) continue;
      const a = (i / count) * TAU, l = 0.02 + Math.abs(Math.sin(i * 78.233) * 43758.5453 % 1) * 0.07;
      pts.push(Math.cos(a) * radius, Math.sin(a) * radius, 0, Math.cos(a) * (radius + l), Math.sin(a) * (radius + l), 0);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return new THREE.LineSegments(g, this.mat({ opacity }));
  }

  /** Thick arc made of lit blocks, brighter on the outer rim, with light running along it. */
  band(bands) {
    this.bandMat ??= new THREE.ShaderMaterial({
      uniforms: this.fx, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      vertexShader: `attribute float end; varying vec3 vU;
        void main(){ vU = vec3(uv, end); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `uniform vec3 uColor; uniform float uTime; varying vec3 vU;
        void main(){ float block = step(0.16, fract(vU.x));
          float tips = smoothstep(0.0, 0.12, vU.z) * smoothstep(1.0, 0.88, vU.z);
          float rim = 0.4 + 0.6*step(0.76, vU.y);
          float run = 0.55 + 0.45*sin(vU.x*0.9 - uTime*2.0);
          gl_FragColor = vec4(uColor, 0.4 * block * tips * rim * run); }`,
    });
    return new THREE.Mesh(bandGeometry(bands), this.bandMat);
  }

  buildRings() {
    this.rings = [];
    const add = (obj, speed, tilt = [0, 0, 0]) => {
      const holder = new THREE.Group();
      holder.rotation.set(...tilt);
      holder.add(obj);
      this.root.add(holder);
      this.rings.push({ obj, speed, holder });
    };
    add(this.arcRing(1.0, 3, 0.8, 0.9), 0.35);
    add(this.tickRing(1.08, 120, 0.035, 0.6, 10), -0.08);
    add(this.arcRing(1.28, 6, 0.55, 0.6), -0.22);
    add(this.arcRing(1.36, 1, 0.33, 0.95), 0.6);
    add(this.tickRing(1.5, 72, 0.05, 0.45, 6), 0.05);
    add(this.arcRing(1.72, 24, 0.5, 0.35), -0.04);
    add(this.arcRing(1.9, 2, 0.22, 0.9), 0.28);
    // Gyroscope rings on tilted axes
    add(this.arcRing(1.6, 4, 0.7, 0.5), 0.5, [Math.PI / 2.3, 0.4, 0]);
    add(this.arcRing(1.75, 3, 0.6, 0.4), -0.42, [Math.PI / 1.8, -0.7, 0.3]);
    add(this.arcRing(2.15, 1, 0.12, 0.8), 0.9, [Math.PI / 2, 0, 0]);
    // Block bands and a code ring
    add(this.band([{ r0: 1.405, r1: 1.48, span: 1.25, blocks: 15 }]), 0.3);
    add(this.band([{ r0: 1.93, r1: 2.03, span: 0.85, blocks: 11 }, { r0: 1.93, r1: 2.03, a0: Math.PI, span: 0.5, blocks: 6 }]),
      -0.2, [Math.PI / 1.8, -0.7, 0.3]);
    add(this.band([{ r0: 2.2, r1: 2.25, span: 1.9, blocks: 34 }]), 0.12);
    add(this.codeRing(2.29, 300), -0.06);
    // Amber accent arc (thinking indicator)
    this.amber = this.arcRing(1.18, 2, 0.18, 0);
    this.amber.material = new THREE.LineBasicMaterial({ color: 0xffb547, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
    add(this.amber, 1.4);
  }

  buildShell() {
    const N = 2600, pos = new Float32Array(N * 3), seed = new Float32Array(N);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = golden * i;
      pos.set([Math.cos(th) * r, y, Math.sin(th) * r], i * 3);
      seed[i] = Math.random();
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
    this.shellUniforms = { uTime: { value: 0 }, uLevel: { value: 0 }, uColor: { value: this.color }, uRadius: { value: 2.45 }, uMic: { value: 0 } };
    this.shell = new THREE.Points(g, new THREE.ShaderMaterial({
      uniforms: this.shellUniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: `attribute float seed; uniform float uTime; uniform float uLevel; uniform float uRadius; uniform float uMic; varying float vA;
        void main(){
          vec3 n = normalize(position);
          float wave = sin(n.y*9.0 + uTime*2.2) * cos(n.x*7.0 - uTime*1.7) * sin(n.z*8.0 + uTime*1.3);
          float r = uRadius * (1.0 + wave*(0.04 + uLevel*0.32) + uMic*0.06*seed);
          vec4 mv = modelViewMatrix * vec4(n*r, 1.0);
          vA = 0.25 + 0.75*smoothstep(-0.3, 1.0, wave) * (0.4 + uLevel);
          gl_PointSize = (1.6 + seed*1.8 + uLevel*3.0) * (7.0 / -mv.z);
          gl_Position = projectionMatrix * mv; }`,
      fragmentShader: DISC_FRAG,
    }));
    this.root.add(this.shell);
  }

  /** Rivers of particles orbiting the core on three tilted rings. All three are one draw call and
   *  move entirely on the GPU: each particle carries its own angle, radius, speed and ring tilt. */
  buildStreams() {
    const specs = [
      { r: 1.55, n: 520, spread: 0.06, speed: 0.42, tilt: [Math.PI / 2.3, 0.4, 0] },
      { r: 2.05, n: 640, spread: 0.1, speed: -0.26, tilt: [1.1, 0.3, 0] },
      { r: 2.6, n: 760, spread: 0.15, speed: 0.17, tilt: [0.5, -0.8, 0.2] },
    ];
    const pos = [], par = [], quat = [], q = new THREE.Quaternion(), e = new THREE.Euler();
    const gauss = () => (Math.random() + Math.random() + Math.random() - 1.5) / 1.5;
    specs.forEach((sp, band) => {
      q.setFromEuler(e.set(...sp.tilt));
      for (let i = 0; i < sp.n; i++) {
        pos.push(Math.random() * TAU, sp.r + gauss() * sp.spread, gauss() * sp.spread * 0.6);
        par.push(Math.random(), sp.speed, band * 2.1);
        quat.push(q.x, q.y, q.z, q.w);
      }
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));  // (angle, radius, height), unpacked in the shader
    g.setAttribute("aPar", new THREE.Float32BufferAttribute(par, 3));      // (seed, speed, phase)
    g.setAttribute("aQuat", new THREE.Float32BufferAttribute(quat, 4));
    this.streams = new THREE.Points(g, new THREE.ShaderMaterial({
      uniforms: this.fx, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: `attribute vec3 aPar; attribute vec4 aQuat; uniform float uFlow; uniform float uLevel; varying float vA;
        void main(){
          float a = position.x + uFlow * aPar.y * (0.75 + 0.5*aPar.x);
          float r = position.y + uLevel * 0.25 * (aPar.x - 0.5);
          vec3 p = vec3(cos(a)*r, sin(a)*r, position.z * (1.0 + uLevel*1.5));
          p += 2.0 * cross(aQuat.xyz, cross(aQuat.xyz, p) + aQuat.w * p);   // tilt onto this particle's ring
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          float arc = 0.5 + 0.5*sin(a*2.0 - uFlow*aPar.y*2.6 + aPar.z);       // brighter arcs drift round each ring
          vA = (0.12 + 0.88*arc*arc) * (0.45 + 0.55*aPar.x) * (1.0 + uLevel);
          gl_PointSize = (1.1 + aPar.x*2.0 + uLevel*2.0) * (7.0 / -mv.z);
          gl_Position = projectionMatrix * mv; }`,
      fragmentShader: DISC_FRAG,
    }));
    this.streams.frustumCulled = false;  // positions are packed, so the automatic bounds would be wrong
    this.root.add(this.streams);
  }

  buildGlobe() {
    this.globe = new THREE.Group();
    this.globe.visible = false;
    this.root.add(this.globe);
    const R = 1.35, pts = [];
    for (let lat = -75; lat <= 75; lat += 15) {
      const y = Math.sin(lat * Math.PI / 180) * R, r = Math.cos(lat * Math.PI / 180) * R;
      for (let i = 0; i < 96; i++) {
        const a0 = i / 96 * Math.PI * 2, a1 = (i + 1) / 96 * Math.PI * 2;
        pts.push(Math.cos(a0) * r, y, Math.sin(a0) * r, Math.cos(a1) * r, y, Math.sin(a1) * r);
      }
    }
    for (let lon = 0; lon < 360; lon += 15) {
      const a = lon * Math.PI / 180;
      for (let i = 0; i < 64; i++) {
        const p0 = -Math.PI / 2 + i / 64 * Math.PI, p1 = -Math.PI / 2 + (i + 1) / 64 * Math.PI;
        pts.push(Math.cos(p0) * Math.cos(a) * R, Math.sin(p0) * R, Math.cos(p0) * Math.sin(a) * R,
          Math.cos(p1) * Math.cos(a) * R, Math.sin(p1) * R, Math.cos(p1) * Math.sin(a) * R);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.globe.add(new THREE.LineSegments(g, this.mat({ opacity: 0.5 })));
    this.globeR = R;
    // dotted surface between the grid lines
    const dots = new Float32Array(1400 * 3);
    for (let i = 0; i < 1400; i++) {
      const y = 1 - (i / 1399) * 2, r = Math.sqrt(1 - y * y), th = GOLDEN * i;
      dots.set([Math.cos(th) * r * R, y * R, Math.sin(th) * r * R], i * 3);
    }
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.BufferAttribute(dots, 3));
    const dm = new THREE.PointsMaterial({ color: this.color.clone(), size: 0.022, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false });
    this.colorMats.push(dm);
    this.globe.add(new THREE.Points(dg, dm));
    this.globe.add(this.arcRing(R * 1.12, 8, 0.7, 0.45), this.tickRing(R * 1.16, 90, 0.03, 0.4, 5));
    // target pin
    this.pin = new THREE.Group();
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.6, 6),
      new THREE.MeshBasicMaterial({ color: 0xffb547, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending }));
    beam.position.y = 0.3;
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffd28a }));
    this.pinRing = new THREE.Mesh(new THREE.RingGeometry(0.06, 0.075, 32),
      new THREE.MeshBasicMaterial({ color: 0xffb547, transparent: true, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
    this.pinRing.rotation.x = -Math.PI / 2;
    this.pin.add(beam, dot, this.pinRing);
    this.pin.visible = false;
    this.globe.add(this.pin);
  }

  buildFloor() {
    const grid = new THREE.PolarGridHelper(9, 16, 12, 96, 0x2fd8ff, 0x2fd8ff);
    grid.material.transparent = true; grid.material.opacity = 0.12; grid.material.depthWrite = false;
    grid.position.y = -2.6;
    this.floor = grid;
    this.colorMats.push(grid.material);
    this.scene.add(grid);
    this.scanRing = new THREE.Mesh(new THREE.RingGeometry(0.98, 1, 128),
      new THREE.MeshBasicMaterial({ color: this.color.clone(), transparent: true, opacity: 0.5, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.colorMats.push(this.scanRing.material);
    this.scanRing.rotation.x = -Math.PI / 2;
    this.scanRing.position.y = -2.59;
    this.scene.add(this.scanRing);
  }

  buildDust() {
    const N = 900, pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) pos.set([(Math.random() - .5) * 30, (Math.random() - .5) * 16, (Math.random() - .5) * 30 - 4], i * 3);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const m = new THREE.PointsMaterial({ color: this.color.clone(), size: 0.035, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false });
    this.colorMats.push(m);
    this.dust = new THREE.Points(g, m);
    this.scene.add(this.dust);
  }

  buildHands() {
    this.hands = [];
    for (let h = 0; h < 2; h++) {
      const group = new THREE.Group();
      const joints = new THREE.InstancedMesh(new THREE.SphereGeometry(0.022, 12, 12),
        new THREE.MeshBasicMaterial({ color: 0xbff4ff, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending }), 21);
      const bonePos = new Float32Array(HAND_BONES.length * 6);
      const bg = new THREE.BufferGeometry();
      bg.setAttribute("position", new THREE.BufferAttribute(bonePos, 3));
      const bones = new THREE.LineSegments(bg, this.mat({ opacity: 0.85 }));
      group.add(joints, bones);
      group.visible = false;
      this.scene.add(group);
      this.hands.push({ group, joints, bones, bonePos, alpha: 0 });
    }
    this._m = new THREE.Matrix4();
  }

  // ---------- input ----------
  bindInput() {
    let drag = null;
    this.canvas.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY }; this.canvas.setPointerCapture(e.pointerId); });
    this.canvas.addEventListener("pointermove", e => {
      this.mouse.set(e.clientX / innerWidth - .5, e.clientY / innerHeight - .5);
      if (!drag) return;
      this.grab((e.clientX - drag.x) / innerWidth, (e.clientY - drag.y) / innerHeight);
      drag = { x: e.clientX, y: e.clientY };
    });
    this.canvas.addEventListener("pointerup", () => drag = null);
    this.canvas.addEventListener("wheel", e => this.zoomBy(e.deltaY > 0 ? 0.92 : 1.08), { passive: true });
  }

  /** Rotate the hologram; dx, dy are fractions of the screen. */
  grab(dx, dy) {
    this.rotVel.set(dy * 2.5, dx * 4.0);
    this.rot.x += dy * 3.0;
    this.rot.y += dx * 5.0;
    this.rot.x = Math.max(-1.2, Math.min(1.2, this.rot.x));
  }

  zoomBy(f) { this.zoomTarget = Math.max(0.55, Math.min(1.9, this.zoomTarget * f)); }

  // ---------- state API ----------
  setState(state) {
    this.state = state;
    const c = state === "idle" || state === "speaking" ? this.base : PALETTE[state] ?? this.base;
    if (this.mode === "idle" || this.mode === "globe") this.targetColor.set(c);
    this.energyTarget = ({ thinking: 3.2, working: 2.4, speaking: 1.5, hearing: 1.4 }[state] ?? 1) * this.boost;
  }

  setMode(mode) {
    this.mode = mode || "idle";
    this.globe.visible = this.mode === "globe";
    this.shell.visible = this.mode !== "globe";
    this.targetColor.set(PALETTE[this.mode] ?? (this.state === "idle" ? this.base : PALETTE[this.state]) ?? this.base);
    if (this.mode === "idle") this.setState(this.state);
    this.pulse();
  }

  /** Mode theme: focus colours the core; full power turns it gold, spins it faster and brightens the bloom. */
  setTheme(focus, full) {
    this.base = full ? THEME.full : THEME[focus] ?? THEME.standard;
    this.boost = full ? 1.7 : 1;
    this.bloomBase = full ? 1.12 : 1.0;
    this.setState(this.state);
    this.pulse();
  }

  setLevel(level) { this.levelTarget = level; }
  /** System load 0..1 lights up segments of the outer load ring. */
  setLoad(load) { this.loadTarget = Math.max(0, Math.min(1, load || 0)); }

  // ---------- extra detail: load ring + orbiting satellites ----------
  buildLoadRing() {
    const SEG = 60, R = 2.42, gap = 0.012, steps = 4, pts = [];
    for (let i = 0; i < SEG; i++) {
      const a0 = (i / SEG) * Math.PI * 2 + gap, a1 = ((i + 1) / SEG) * Math.PI * 2 - gap;
      for (let k = 0; k < steps; k++) {
        const t0 = a0 + (a1 - a0) * k / steps, t1 = a0 + (a1 - a0) * (k + 1) / steps;
        pts.push(Math.cos(t0) * R, Math.sin(t0) * R, 0, Math.cos(t1) * R, Math.sin(t1) * R, 0);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.loadSeg = { SEG, verts: steps * 2 };
    const dim = new THREE.LineSegments(geo, this.mat({ opacity: 0.12 }));
    this.loadLit = new THREE.LineSegments(geo, this.mat({ opacity: 0.9 }));
    this.loadHot = new THREE.LineSegments(geo.clone(), new THREE.LineBasicMaterial({ color: 0xffb547, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false }));
    this.loadRing = new THREE.Group();
    this.loadRing.add(dim, this.loadLit, this.loadHot);
    this.loadRing.rotation.x = Math.PI / 2.15;  // lies almost flat, like a dais around the core
    this.loadRing.position.y = -0.15;
    this.root.add(this.loadRing);
    this.load = 0; this.loadTarget = 0;
  }

  buildSatellites() {
    this.satellites = [];
    const TRAIL = 48;
    const specs = [
      { r: 2.05, speed: 0.55, tilt: [1.1, 0.3, 0], color: 0xbff4ff, size: 0.035 },
      { r: 2.6, speed: -0.32, tilt: [0.5, -0.8, 0.2], color: 0xffc96b, size: 0.03 },
      { r: 3.05, speed: 0.22, tilt: [1.9, 0.6, -0.3], color: 0x8fe9ff, size: 0.028 },
    ];
    for (const sp of specs) {
      const holder = new THREE.Group();
      holder.rotation.set(...sp.tilt);
      const dot = new THREE.Mesh(new THREE.SphereGeometry(sp.size, 10, 10),
        new THREE.MeshBasicMaterial({ color: sp.color, transparent: true, blending: THREE.AdditiveBlending }));
      const pos = new Float32Array(TRAIL * 3), col = new Float32Array(TRAIL * 3);
      const c = new THREE.Color(sp.color);
      for (let i = 0; i < TRAIL; i++) { const f = 1 - i / TRAIL; col.set([c.r * f, c.g * f, c.b * f], i * 3); }
      const tg = new THREE.BufferGeometry();
      tg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      tg.setAttribute("color", new THREE.BufferAttribute(col, 3));
      const trail = new THREE.Line(tg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.8,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      const orbit = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 96 }, (_, i) => new THREE.Vector3(Math.cos(i / 96 * Math.PI * 2) * sp.r, Math.sin(i / 96 * Math.PI * 2) * sp.r, 0))),
        this.mat({ opacity: 0.07 }));
      holder.add(dot, trail, orbit);
      this.root.add(holder);
      this.satellites.push({ ...sp, dot, trail, pos, phase: Math.random() * Math.PI * 2, TRAIL });
    }
  }

  updateDetail(dt, t) {
    // load ring: lit segments follow CPU load, the top 15% glows amber
    this.load += (this.loadTarget - this.load) * Math.min(1, dt * 2);
    const { SEG, verts } = this.loadSeg;
    const lit = Math.round(this.load * SEG);
    const hotFrom = Math.round(SEG * 0.85);
    this.loadLit.geometry.setDrawRange(0, Math.min(lit, hotFrom) * verts);
    this.loadHot.visible = lit > hotFrom;
    if (this.loadHot.visible) this.loadHot.geometry.setDrawRange(hotFrom * verts, (lit - hotFrom) * verts);
    if (this.loadHot.visible) this.loadHot.material.opacity = 0.6 + Math.sin(t * 8) * 0.35;
    this.loadRing.rotation.z -= dt * 0.03;
    // satellites + fading trails
    for (const s of this.satellites) {
      for (let i = 0; i < s.TRAIL; i++) {
        const a = s.phase + t * s.speed - i * 0.025 * Math.sign(s.speed);
        s.pos[i * 3] = Math.cos(a) * s.r; s.pos[i * 3 + 1] = Math.sin(a) * s.r; s.pos[i * 3 + 2] = 0;
      }
      s.dot.position.set(s.pos[0], s.pos[1], 0);
      s.trail.geometry.attributes.position.needsUpdate = true;
    }
  }
  setMic(level) { this.mic = level; }
  pulse() { this.pulses.push(0); }

  locate(lat, lon) {
    this.setMode("globe");
    const la = lat * Math.PI / 180, lo = -lon * Math.PI / 180, R = this.globeR;
    this.pin.position.set(Math.cos(la) * Math.cos(lo) * R, Math.sin(la) * R, Math.cos(la) * Math.sin(lo) * R);
    this.pin.lookAt(this.pin.position.clone().multiplyScalar(2));
    this.pin.rotateX(Math.PI / 2);
    this.pin.visible = true;
    // Spin so the pin faces the camera
    this.globeTarget = { x: la * 0.8, y: Math.PI / 2 + lon * Math.PI / 180 - this.root.rotation.y };
  }

  /** Project the pin to screen coords for the DOM label. */
  pinScreen() {
    if (!this.pin.visible || !this.globe.visible) return null;
    const v = new THREE.Vector3();
    this.pin.getWorldPosition(v);
    v.project(this.camera);
    return { x: (v.x + 1) / 2 * innerWidth, y: (1 - v.y) / 2 * innerHeight, front: v.z < 1 };
  }

  /** hands: array of { landmarks: [{x,y,z}...21] } in normalized image coords (mirrored already). */
  setHands(hands) {
    this.handData = hands;
  }

  // ---------- loop ----------
  resize() {
    const w = innerWidth, h = innerHeight;
    this.renderer.setPixelRatio(this.ratio);
    this.composer.setPixelRatio(this.ratio);
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    // Bloom is a blur, so half resolution looks the same and costs about a quarter as much.
    this.bloom.setSize(Math.round(w * this.ratio / 2), Math.round(h * this.ratio / 2));
    this.camera.aspect = w / h;
    this.camera.fov = w / h < 1 ? 60 : 42;
    this.camera.updateProjectionMatrix();
  }

  /** Lower the render scale when frames run slow, raise it again when there's headroom. */
  adapt(rawDt, t, target) {
    const p = this.perf;
    p.frames++; p.time += rawDt;
    if (p.time < 2) return;
    const fps = p.frames / p.time;
    p.frames = 0; p.time = 0;
    if (fps < target * 0.75 && this.ratio > 0.6) {
      this.ratio = Math.max(0.6, this.ratio - 0.2); p.lastDrop = t; this.resize();
    } else if (fps > target * 0.95 && this.ratio < this.maxRatio && t - p.lastDrop > 20) {
      this.ratio = Math.min(this.maxRatio, this.ratio + 0.1); this.resize();
    }
  }

  frame() {
    if (document.hidden) { this.clock.getDelta(); return; }  // don't render while the window is hidden
    // Frame budget: 30 fps while nothing is happening (the slow drift looks the same and it
    // halves GPU and window-compositing work), 60 fps when talking, working or tracking hands
    // (120 Hz displays are held to 60 too).
    const now = performance.now();
    const calm = this.state === "idle" && !this.handData?.length && this.level < 0.02 &&
      (this.mode === "idle" || this.mode === "globe") && this.energy < 1.3 * this.boost;
    if (calm !== this._calm) { this._calm = calm; this.perf.frames = 0; this.perf.time = 0; }
    if (now - (this._lastFrame || 0) < (calm ? 31 : 15)) return;
    this._lastFrame = now;
    const rawDt = this.clock.getDelta(), t = this.clock.elapsedTime;
    if (rawDt < 0.25) this.adapt(rawDt, t, calm ? 30 : 60);
    const dt = Math.min(rawDt, 0.05);
    this.level += (this.levelTarget - this.level) * Math.min(1, dt * 14);
    this.energy += (this.energyTarget - this.energy) * Math.min(1, dt * 3);
    this.zoom += (this.zoomTarget - this.zoom) * Math.min(1, dt * 6);

    // colour
    if (this.mode === "party") this.targetColor.setHSL((t * 0.25) % 1, 0.9, 0.6);
    const c = this.color, tc = this.targetColor, dc = Math.abs(c.r - tc.r) + Math.abs(c.g - tc.g) + Math.abs(c.b - tc.b);
    if (dc > 0) {  // only touch the materials while the colour is actually changing
      if (dc < 0.002) c.copy(tc); else c.lerp(tc, Math.min(1, dt * 4));
      for (const m of this.colorMats) m.color.copy(c);
    }

    // root rotation with inertia
    this.rot.y += this.rotVel.y; this.rot.x += this.rotVel.x;
    this.rotVel.multiplyScalar(0.94);
    if (Math.abs(this.rotVel.y) < 0.0015) this.rotVel.y = 0.0015;
    if (this.globeTarget && this.globe.visible) {
      this.rot.y += (this.globeTarget.y - this.rot.y) * dt * 2.5;
      this.rot.x += (this.globeTarget.x - this.rot.x) * dt * 2.5;
      this.rotVel.set(0, 0);
    } else {
      this.rot.x += (0.15 - this.rot.x) * dt * 0.3;
    }
    this.root.rotation.set(this.rot.x, this.rot.y, 0);

    // rings
    for (const r of this.rings) r.obj.rotation.z += r.speed * dt * this.energy;
    this.amber.material.opacity += (((this.state === "thinking" || this.state === "working") ? 0.95 : 0) - this.amber.material.opacity) * dt * 4;

    // core
    this.coreBase = (this.coreBase ?? 1) + ((this.mode === "globe" ? 0.32 : 1) - (this.coreBase ?? 1)) * Math.min(1, dt * 4);
    const s = this.coreBase * (1 + this.level * 0.22 + Math.sin(t * 2) * 0.02);
    this.core.scale.setScalar(s);
    this.lattice.rotation.x += dt * 0.4 * this.energy; this.lattice.rotation.y += dt * 0.55 * this.energy;
    this.lattice2.rotation.y -= dt * 0.9 * this.energy;
    this.reactor.rotation.z -= dt * 0.25 * this.energy;
    this.atom.rotation.x += dt * 0.7 * this.energy; this.atom.rotation.y += dt * 0.45 * this.energy;
    this.cage.rotation.y -= dt * 0.12 * this.energy; this.cage.rotation.x += dt * 0.05 * this.energy;
    this.beams.rotation.y += dt * 0.1 * this.energy; this.beams.rotation.z += dt * 0.04 * this.energy;
    this.fx.uTime.value = t; this.fx.uLevel.value = this.level; this.fx.uEnergy.value = this.energy;
    this.fx.uFlow.value += dt * (0.6 + this.energy * 0.4);
    this.nucleus.scale.setScalar(1 + this.level * 0.45 + Math.sin(t * 5) * 0.05);
    this.coreUniforms.uLevel.value = this.level;
    this.coreUniforms.uTime.value = t;
    this.shellUniforms.uLevel.value = this.level;
    this.shellUniforms.uMic.value += (this.mic - this.shellUniforms.uMic.value) * 0.2;
    this.shellUniforms.uTime.value = t * (0.6 + this.energy * 0.4);
    this.shell.rotation.y += dt * 0.05 * this.energy;

    // floor scan pulses
    if (Math.floor(t / 3.2) !== this._lastAutoPulse) { this._lastAutoPulse = Math.floor(t / 3.2); this.pulses.push(0); }
    let p = this.pulses.length ? this.pulses[0] : 0;
    this.pulses = this.pulses.map(v => v + dt * 0.45).filter(v => v < 1);
    p = this.pulses.length ? this.pulses[this.pulses.length - 1] : 1;
    this.scanRing.scale.setScalar(0.3 + p * 8.5);
    this.scanRing.material.opacity = 0.55 * (1 - p);
    this.dust.rotation.y += dt * 0.01;

    // pin ring pulse
    if (this.pin.visible) { const k = (t * 1.2) % 1; this.pinRing.scale.setScalar(1 + k * 3); this.pinRing.material.opacity = 1 - k; }

    this.updateDetail(dt, t);

    // hands
    this.updateHands(dt);

    // camera
    const d = this.camDist / this.zoom;
    this.camera.position.x += (this.mouse.x * 0.8 - this.camera.position.x) * dt * 2;
    this.camera.position.y += (0.4 - this.mouse.y * 0.5 - this.camera.position.y) * dt * 2;
    this.camera.position.z += (d - this.camera.position.z) * dt * 4;
    this.camera.lookAt(0, 0, 0);

    this.bloom.strength = this.bloomBase - 0.05 + this.level * 0.45 + (this.mode === "party" ? Math.abs(Math.sin(t * 4)) * 0.8 : 0);
    this.composer.render();
    this.onFrame?.(t);
  }

  updateHands(dt) {
    const data = this.handData || [];
    for (let h = 0; h < 2; h++) {
      const hand = this.hands[h], lm = data[h]?.landmarks;
      hand.alpha += ((lm ? 1 : 0) - hand.alpha) * Math.min(1, dt * 8);
      hand.group.visible = hand.alpha > 0.02;
      if (!lm) { hand.P = null; continue; }
      // Tracking updates ~20x a second; ease toward it every frame so the hologram stays smooth.
      if (!hand.P) hand.P = Array.from({ length: 21 }, () => new THREE.Vector3());
      const P = hand.P, k = hand.alpha < 0.05 ? 1 : Math.min(1, dt * 18);
      for (let i = 0; i < 21; i++) {
        const p = lm[i];
        P[i].x += ((p.x - 0.5) * 7.5 - P[i].x) * k;
        P[i].y += ((0.5 - p.y) * 4.6 - P[i].y) * k;
        P[i].z += ((-p.z * 9 + 1.6) - P[i].z) * k;
      }
      for (let i = 0; i < 21; i++) {
        this._m.makeTranslation(P[i].x, P[i].y, P[i].z);
        const sc = TIPS.has(i) ? 1.6 : 1;
        this._m.scale(this._s.set(sc, sc, sc));
        hand.joints.setMatrixAt(i, this._m);
      }
      hand.joints.instanceMatrix.needsUpdate = true;
      HAND_BONES.forEach(([a, b], k) => {
        hand.bonePos.set([P[a].x, P[a].y, P[a].z, P[b].x, P[b].y, P[b].z], k * 6);
      });
      hand.bones.geometry.attributes.position.needsUpdate = true;
      hand.joints.material.opacity = 0.95 * hand.alpha;
      hand.joints.material.color.set(data[h].pinch ? 0xffc96b : 0xbff4ff);
    }
  }
}
