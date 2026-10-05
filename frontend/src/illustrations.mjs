import { palette } from "./theme.mjs";

// Local illustration clocks only. No requests and no WebGL context.
function clock(root, button, draw, fps) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  let active = !reduced.matches,
    visible = false,
    timer = null,
    tick = 0;
  const paint = () => {
    draw(tick);
    button.disabled = reduced.matches;
    button.textContent = reduced.matches
      ? "Motion reduced"
      : active
        ? "Pause illustration"
        : "Play illustration";
    button.setAttribute("aria-pressed", String(active && !reduced.matches));
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = null;
    if (
      !active ||
      !visible ||
      document.hidden ||
      reduced.matches ||
      document.documentElement.classList.contains("foreground-wait")
    )
      return;
    timer = setTimeout(() => {
      tick++;
      paint();
      schedule();
    }, 1000 / fps);
  };
  const observer = new IntersectionObserver(
    (entries) => {
      visible = entries[0].isIntersecting;
      schedule();
    },
    { threshold: 0.05 },
  );
  observer.observe(root);
  button.onclick = () => {
    active = !active;
    paint();
    schedule();
  };
  const visibility = () => {
    schedule();
    if (!document.hidden) paint();
  };
  document.addEventListener("visibilitychange", visibility);
  document.addEventListener("foreground-wait-change", schedule);
  reduced.addEventListener("change", () => {
    if (reduced.matches) active = false;
    paint();
    schedule();
  });
  const api = {
    paint,
    step() {
      active = false;
      tick++;
      paint();
      schedule();
    },
    reset() {
      tick = 0;
      paint();
      schedule();
    },
    dispose() {
      clearTimeout(timer);
      observer.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      document.removeEventListener("foreground-wait-change", schedule);
    },
  };
  paint();
  return api;
}
export function initIllustrations(advance, restart) {
  const $ = (id) => document.getElementById(id),
    canvas = $("hero-radar");
  let depth = false,
    bearing = 0;
  const hero = clock(
    $("hero-visual"),
    $("hero-pause"),
    (tick) => {
      const w = canvas.clientWidth || 400,
        h = 280,
        dpr = Math.min(devicePixelRatio || 1, 1.5),
        p = palette();
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== h * dpr) {
        canvas.width = Math.round(w * dpr);
        canvas.height = h * dpr;
      }
      const c = canvas.getContext("2d");
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.fillStyle = p.radar;
      c.fillRect(0, 0, w, h);
      c.save();
      c.translate(w / 2, h / 2);
      c.scale(Math.min(w / 360, 1), depth ? 0.57 : 1);
      c.rotate(depth ? -0.35 + bearing : bearing);
      c.strokeStyle = p.grid;
      c.lineWidth = 1;
      for (let r = 30; r <= 120; r += 30) {
        c.beginPath();
        c.arc(0, 0, r, 0, Math.PI * 2);
        c.stroke();
      }
      c.beginPath();
      c.moveTo(-135, 0);
      c.lineTo(135, 0);
      c.moveTo(0, -135);
      c.lineTo(0, 135);
      c.stroke();
      const angle = tick * 0.025;
      c.fillStyle = p.green + "20";
      c.beginPath();
      c.moveTo(0, 0);
      c.arc(0, 0, 120, angle - 0.35, angle);
      c.closePath();
      c.fill();
      c.strokeStyle = p.green;
      c.beginPath();
      c.moveTo(0, 0);
      c.lineTo(120 * Math.cos(angle), 120 * Math.sin(angle));
      c.stroke();
      for (let i = 0; i < 6; i++) {
        const position = (n) => [
          Math.cos(n * 0.014 + i) * (42 + i * 11),
          Math.sin(n * 0.011 + i * 1.6) * (35 + i * 9),
        ];
        c.strokeStyle = p.selection + "70";
        c.beginPath();
        for (let j = 15; j >= 0; j--) {
          const [x, y] = position(tick - j * 3);
          j === 15 ? c.moveTo(x, y) : c.lineTo(x, y);
        }
        c.stroke();
        const [x, y] = position(tick),
          [qx, qy] = position(tick + 12);
        c.fillStyle = p.selection;
        c.beginPath();
        c.arc(x, y, 3, 0, Math.PI * 2);
        c.fill();
        c.strokeStyle = p.selection;
        c.beginPath();
        c.moveTo(x, y);
        c.lineTo(qx, qy);
        const a = Math.atan2(qy - y, qx - x);
        c.lineTo(qx - 4 * Math.cos(a - 0.5), qy - 4 * Math.sin(a - 0.5));
        c.moveTo(qx, qy);
        c.lineTo(qx - 4 * Math.cos(a + 0.5), qy - 4 * Math.sin(a + 0.5));
        c.stroke();
      }
      c.restore();
      c.fillStyle = p["radar-text"];
      c.font = "10px Consolas,monospace";
      c.fillText(
        depth
          ? "ILLUSTRATIVE PERSPECTIVE · PLANAR PATHS"
          : "ILLUSTRATIVE PLAN · SYMBOLIC PATHS",
        14,
        260,
      );
    },
    20,
  );
  for (const b of document.querySelectorAll("[data-hero-view]"))
    b.onclick = () => {
      depth = b.dataset.heroView === "3d";
      for (const a of document.querySelectorAll("[data-hero-view]"))
        a.setAttribute("aria-pressed", String(a === b));
      hero.paint();
    };
  canvas.addEventListener("pointermove", (e) => {
    if (e.buttons) {
      bearing += e.movementX * 0.005;
      hero.paint();
    }
  });
  const resize = new ResizeObserver(() => hero.paint());
  resize.observe(canvas);
  const teaching = clock(
    $("teaching"),
    $("lesson-play"),
    (tick) => advance(tick),
    1.5,
  );
  $("lesson-step").onclick = () => teaching.step();
  $("lesson-replay").onclick = () => {
    restart();
    teaching.reset();
  };
  window.addEventListener("pagehide", () => {
    hero.dispose();
    teaching.dispose();
    resize.disconnect();
  });
  return {
    paint() {
      hero.paint();
      teaching.paint();
    },
    resetLesson() {
      teaching.reset();
    },
  };
}
