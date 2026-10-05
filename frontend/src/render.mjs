import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { finite, trail } from "./model.mjs";
import { palette } from "./theme.mjs";
const color = (o) => palette()[o.alert ? "red" : o.flagged ? "amber" : "green"];
export function extent(index) {
  let xmin = 0,
    xmax = 36,
    ymin = -18,
    ymax = 18;
  for (const c of index?.views || [])
    for (const o of c.objects) {
      if (finite(o.x) && finite(o.y)) {
        xmin = Math.min(xmin, o.x - 2);
        xmax = Math.max(xmax, o.x + 2);
        ymin = Math.min(ymin, o.y - 2);
        ymax = Math.max(ymax, o.y + 2);
      }
    }
  return { xmin, xmax, ymin, ymax };
}
export function plan(canvas, index, cursor, selected, ext) {
  const w = canvas.clientWidth || 640,
    h = canvas.clientHeight || 370,
    dpr = Math.min(devicePixelRatio || 1, 1.5);
  if (
    canvas.width !== Math.round(w * dpr) ||
    canvas.height !== Math.round(h * dpr)
  ) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const b = ext || { xmin: 0, xmax: 36, ymin: -18, ymax: 18 },
    s = Math.min((w - 65) / (b.xmax - b.xmin), (h - 56) / (b.ymax - b.ymin));
  const ox = 32 + (w - 65 - (b.xmax - b.xmin) * s) / 2 - b.xmin * s,
    oy = 22 + (h - 56 - (b.ymax - b.ymin) * s) / 2 + b.ymax * s;
  const point = (x, y) => ({ x: ox + x * s, y: oy - y * s });
  const step = Math.max(3, Math.ceil((b.xmax - b.xmin) / 10 / 3) * 3);
  ctx.lineWidth = 1;
  ctx.font = "9px Consolas,monospace";
  ctx.strokeStyle = palette().grid;
  ctx.fillStyle = palette()["radar-text"];
  for (let x = Math.ceil(b.xmin / step) * step; x <= b.xmax; x += step) {
    const p = point(x, b.ymin),
      q = point(x, b.ymax);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(q.x, q.y);
    ctx.stroke();
    ctx.fillText(String(x), p.x + 3, h - 25);
  }
  for (let y = Math.ceil(b.ymin / step) * step; y <= b.ymax; y += step) {
    const p = point(b.xmin, y),
      q = point(b.xmax, y);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(q.x, q.y);
    ctx.stroke();
    ctx.fillText(String(y), 6, p.y + 3);
  }
  ctx.setLineDash([3, 5]);
  ctx.strokeStyle = palette().grid;
  const roi = index?.result.roi || 15;
  for (let r = roi / 3; r <= roi + 0.001; r += roi / 3) {
    ctx.beginPath();
    ctx.arc(ox, oy, r * s, -Math.PI / 2, Math.PI / 2);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.fillStyle = palette()["radar-text"];
  ctx.beginPath();
  ctx.arc(ox, oy, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillText("ORIGIN", ox + 8, oy + 15);
  const hits = [];
  for (const o of index?.views[cursor]?.objects || []) {
    if (!finite(o.x) || !finite(o.y)) continue;
    const p = point(o.x, o.y),
      col = color(o),
      t = trail(index, o);
    ctx.beginPath();
    let started = false;
    for (const a of t) {
      if (!finite(a.x) || !finite(a.y)) {
        started = false;
        continue;
      }
      const q = point(a.x, a.y);
      if (!started) ctx.moveTo(q.x, q.y);
      else ctx.lineTo(q.x, q.y);
      started = true;
    }
    ctx.strokeStyle = col + "65";
    ctx.lineWidth = 1.4;
    ctx.stroke();
    if (finite(o.vx) && finite(o.vy)) {
      const q = point(o.x + o.vx, o.y + o.vy);
      if (Math.hypot(q.x - p.x, q.y - p.y) > 2) {
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(q.x, q.y);
        ctx.strokeStyle = col;
        ctx.stroke();
        const a = Math.atan2(q.y - p.y, q.x - p.x);
        ctx.beginPath();
        ctx.moveTo(q.x, q.y);
        ctx.lineTo(q.x - 5 * Math.cos(a - 0.5), q.y - 5 * Math.sin(a - 0.5));
        ctx.moveTo(q.x, q.y);
        ctx.lineTo(q.x - 5 * Math.cos(a + 0.5), q.y - 5 * Math.sin(a + 0.5));
        ctx.stroke();
      }
    }
    ctx.fillStyle = col;
    ctx.strokeStyle = col;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (o.alert || o.flagged) {
      ctx.moveTo(p.x, p.y - 5);
      ctx.lineTo(p.x + 5, p.y);
      ctx.lineTo(p.x, p.y + 5);
      ctx.lineTo(p.x - 5, p.y);
      ctx.closePath();
    } else ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    ctx.fill();
    if (o.identity === selected) {
      ctx.strokeStyle = palette().selection;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 10, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (o.alert || o.flagged || o.identity === selected) {
      ctx.font = "10px Consolas,monospace";
      ctx.fillText(
        `${o.alert ? "! " : o.flagged ? "◇ " : ""}${o.slot == null ? "?" : o.slot.toString(16).padStart(2, "0")}`,
        p.x + 8,
        p.y - 6,
      );
    }
    hits.push({ x: p.x, y: p.y, o });
  }
  return hits;
}
export class OrbitView {
  constructor(canvas, onSelect) {
    this.canvas = canvas;
    const context = canvas.getContext("webgl2", {
      antialias: false,
      alpha: false,
      powerPreference: "low-power",
    });
    if (!context) throw new Error("WebGL unavailable");
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      context,
      antialias: false,
      alpha: false,
      powerPreference: "low-power",
    });
    this.renderer.setClearColor(palette().radar);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(43, 1, 0.1, 4000);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = false;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 2000;
    this.scene.add(new THREE.HemisphereLight(0xd5edd3, 0x355944, 2));
    const sun = new THREE.DirectionalLight(0xffefda, 2);
    sun.position.set(-8, 24, 16);
    this.scene.add(sun);
    this.dynamic = new THREE.Group();
    this.scene.add(this.dynamic);
    this.static = new THREE.Group();
    this.scene.add(this.static);
    this.material = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.8,
    });
    this.sphere = new THREE.SphereGeometry(0.22, 8, 6);
    this.diamond = new THREE.OctahedronGeometry(0.34);
    this.alert = new THREE.ConeGeometry(0.32, 0.65, 4);
    this.meshes = [this.sphere, this.diamond, this.alert].map((g) => {
      const m = new THREE.InstancedMesh(g, this.material, 512);
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      m.count = 0;
      m.frustumCulled = false;
      this.dynamic.add(m);
      return m;
    });
    this.lines = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.65,
      }),
    );
    this.dynamic.add(this.lines);
    this.selector = new THREE.Mesh(
      new THREE.TorusGeometry(0.5, 0.025, 4, 24),
      new THREE.MeshBasicMaterial({ color: palette().selection }),
    );
    this.selector.rotation.x = Math.PI / 2;
    this.dynamic.add(this.selector);
    this.selector.visible = false;
    this.onChange = () => this.render();
    this.controls.addEventListener("change", this.onChange);
    this.down = null;
    this.onDown = (e) => {
      this.down = { x: e.clientX, y: e.clientY };
    };
    this.onUp = (e) => {
      if (
        !this.down ||
        Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 5
      )
        return;
      const r = canvas.getBoundingClientRect(),
        ray = new THREE.Raycaster();
      ray.setFromCamera(
        new THREE.Vector2(
          ((e.clientX - r.left) / r.width) * 2 - 1,
          (-(e.clientY - r.top) / r.height) * 2 + 1,
        ),
        this.camera,
      );
      const hit = ray.intersectObjects(this.meshes)[0];
      if (hit) {
        const o = this.items[this.meshes.indexOf(hit.object)]?.[hit.instanceId];
        if (o) onSelect(o);
      }
    };
    canvas.addEventListener("pointerdown", this.onDown);
    canvas.addEventListener("pointerup", this.onUp);
    this.lost = (e) => {
      e.preventDefault();
      this.failed?.();
    };
    canvas.addEventListener("webglcontextlost", this.lost);
  }
  setup(b) {
    const width = b.xmax - b.xmin,
      height = b.ymax - b.ymin,
      cx = (b.xmin + b.xmax) / 2,
      cz = -(b.ymin + b.ymax) / 2,
      size = Math.max(width, height);
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(width, height),
      new THREE.MeshStandardMaterial({ color: palette().radar, roughness: 1 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(cx, -0.035, cz);
    this.static.add(floor);
    const points = [];
    const step = Math.max(3, Math.ceil(size / 15 / 3) * 3);
    for (let x = Math.ceil(b.xmin / step) * step; x <= b.xmax; x += step)
      points.push(x, 0, -b.ymin, x, 0, -b.ymax);
    for (let y = Math.ceil(b.ymin / step) * step; y <= b.ymax; y += step)
      points.push(b.xmin, 0, -y, b.xmax, 0, -y);
    const grid = new THREE.LineSegments(
      new THREE.BufferGeometry().setAttribute(
        "position",
        new THREE.Float32BufferAttribute(points, 3),
      ),
      new THREE.LineBasicMaterial({ color: palette().grid }),
    );
    this.static.add(grid);
    const origin = new THREE.Mesh(
      new THREE.CylinderGeometry(0.45, 0.65, 0.3, 12),
      new THREE.MeshStandardMaterial({ color: palette()["radar-text"] }),
    );
    origin.position.set(0, 0.15, 0);
    this.static.add(origin);
    this.floor = floor;
    this.grid = grid;
    this.origin = origin;
    this.controls.target.set(cx, 0, cz);
    this.camera.position.set(cx - size * 0.6, size * 0.85, cz + size * 0.8);
    this.camera.far = Math.max(4000, size * 10);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }
  update(index, cursor, selected) {
    const positions = [],
      colors = [],
      matrix = new THREE.Matrix4(),
      col = new THREE.Color();
    this.items = [[], [], []];
    this.selector.visible = false;
    const segment = (a, b, c) => {
      positions.push(...a, ...b);
      col.set(c);
      colors.push(col.r, col.g, col.b, col.r, col.g, col.b);
    };
    for (const o of index?.views[cursor]?.objects || []) {
      if (!finite(o.x) || !finite(o.y)) continue;
      const k = o.alert ? 2 : o.flagged ? 1 : 0,
        i = this.items[k].length;
      if (i >= 512) continue;
      this.items[k].push(o);
      matrix.makeTranslation(o.x, 0.36, -o.y);
      this.meshes[k].setMatrixAt(i, matrix);
      col.set(color(o));
      this.meshes[k].setColorAt(i, col);
      if (o.identity === selected) {
        this.selector.position.set(o.x, 0.035, -o.y);
        this.selector.visible = true;
      }
      const history = trail(index, o);
      for (let j = 1; j < history.length; j++) {
        const a = history[j - 1],
          b = history[j];
        if ([a.x, a.y, b.x, b.y].every(finite))
          segment([a.x, 0.025, -a.y], [b.x, 0.025, -b.y], color(o));
      }
      if (finite(o.vx) && finite(o.vy)) {
        const x = o.x + o.vx,
          z = -o.y - o.vy;
        segment([o.x, 0.13, -o.y], [x, 0.13, z], color(o));
        const angle = Math.atan2(-o.vy, o.vx);
        for (const d of [-0.45, 0.45])
          segment(
            [x, 0.13, z],
            [
              x - 0.4 * Math.cos(angle + d),
              0.13,
              z - 0.4 * Math.sin(angle + d),
            ],
            color(o),
          );
      }
    }
    for (let k = 0; k < 3; k++) {
      const m = this.meshes[k];
      m.count = this.items[k].length;
      m.instanceMatrix.needsUpdate = true;
      if (m.instanceColor) m.instanceColor.needsUpdate = true;
    }
    this.lines.geometry.dispose();
    this.lines.geometry = new THREE.BufferGeometry()
      .setAttribute("position", new THREE.Float32BufferAttribute(positions, 3))
      .setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    this.render();
  }
  theme() {
    const p = palette();
    this.renderer.setClearColor(p.radar);
    this.floor?.material.color.set(p.radar);
    this.grid?.material.color.set(p.grid);
    this.origin?.material.color.set(p["radar-text"]);
    this.selector.material.color.set(p.selection);
    this.render();
  }
  render() {
    if (this.canvas.hidden || document.hidden) return;
    const w = this.canvas.clientWidth,
      h = this.canvas.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(devicePixelRatio || 1, 1.5),
      scale = Math.min(dpr, Math.sqrt(1_200_000 / (w * h))),
      bw = Math.round(w * scale),
      bh = Math.round(h * scale);
    if (this.canvas.width !== bw || this.canvas.height !== bh) {
      this.renderer.setSize(bw, bh, false);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
    this.renderer.render(this.scene, this.camera);
  }
  dispose() {
    this.controls.removeEventListener("change", this.onChange);
    this.controls.dispose();
    this.canvas.removeEventListener("pointerdown", this.onDown);
    this.canvas.removeEventListener("pointerup", this.onUp);
    this.canvas.removeEventListener("webglcontextlost", this.lost);
    const geometries = new Set(),
      materials = new Set();
    this.scene.traverse((o) => {
      if (o.geometry) geometries.add(o.geometry);
      if (o.material) materials.add(o.material);
    });
    geometries.forEach((g) => g.dispose());
    materials.forEach((m) => m.dispose());
    this.renderer.dispose();
  }
}
