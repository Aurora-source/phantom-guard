import { finite } from "./model.mjs";
import { palette } from "./theme.mjs";

// Completed-result presentation only; no inference, labels or interpolation.
export function trackSeries(index, key) {
  if (
    !index ||
    !key ||
    !finite(index.result.tick_seconds) ||
    index.result.tick_seconds <= 0
  )
    return [];
  const first = index.views[0].header_timestamp_ticks;
  if (!finite(first)) return [];
  return (index.histories.get(key) || []).map((o) => ({
    cursor: o.cursor,
    t: finite(index.views[o.cursor].header_timestamp_ticks)
      ? (index.views[o.cursor].header_timestamp_ticks - first) *
        index.result.tick_seconds
      : null,
    speed: finite(o.vx) && finite(o.vy) ? Math.hypot(o.vx, o.vy) : null,
  }));
}
export function trackChart(canvas, note, index, key, cursor) {
  const series = trackSeries(index, key),
    valid = series.filter((p) => finite(p.t) && finite(p.speed));
  const w = canvas.clientWidth || 500,
    h = 160,
    dpr = Math.min(devicePixelRatio || 1, 1.5),
    p = palette();
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== h * dpr) {
    canvas.width = Math.round(w * dpr);
    canvas.height = h * dpr;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = p.radar;
  ctx.fillRect(0, 0, w, h);
  if (!valid.length) {
    note.textContent = key
      ? "Speed or configured timestamps unavailable for this track."
      : "Select an object to plot its reported velocity magnitude over the completed track lifetime.";
    ctx.fillStyle = p["radar-text"];
    ctx.font = "12px Segoe UI, sans-serif";
    ctx.fillText("Selected-track speed · Awaiting selection", 18, 80);
    return;
  }
  const lo = Math.min(...valid.map((v) => v.t)),
    hi = Math.max(...valid.map((v) => v.t)),
    max = Math.max(...valid.map((v) => v.speed));
  const x = (t) => 48 + ((t - lo) / (hi - lo || 1)) * (w - 68),
    y = (v) => 126 - (v / (max || 1)) * 102;
  ctx.strokeStyle = p.grid;
  ctx.fillStyle = p["radar-text"];
  ctx.font = "10px Consolas, monospace";
  for (let i = 0; i < 3; i++) {
    const v = (max * i) / 2;
    ctx.beginPath();
    ctx.moveTo(48, y(v));
    ctx.lineTo(w - 20, y(v));
    ctx.stroke();
    ctx.fillText(v.toFixed(2), 7, y(v) + 3);
  }
  ctx.fillText(`${lo.toFixed(3)} s`, 48, 149);
  ctx.fillText(`${hi.toFixed(3)} s`, Math.max(48, w - 85), 149);
  ctx.strokeStyle = p.selection;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let last = null;
  for (const v of series) {
    if (!finite(v.t) || !finite(v.speed)) {
      last = null;
      continue;
    }
    if (!last || v.cursor !== last.cursor + 1) ctx.moveTo(x(v.t), y(v.speed));
    else ctx.lineTo(x(v.t), y(v.speed));
    last = v;
  }
  ctx.stroke();
  const at = valid.find((v) => v.cursor === cursor);
  if (at) {
    ctx.strokeStyle = p.orange;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x(at.t), 18);
    ctx.lineTo(x(at.t), 130);
    ctx.stroke();
    ctx.fillStyle = p.selection;
    ctx.beginPath();
    ctx.arc(x(at.t), y(at.speed), 3, 0, Math.PI * 2);
    ctx.fill();
  }
  note.textContent = `Reported √(vx² + vy²), provisional m/s. Time from supplied header ticks × configured ${index.result.tick_seconds} s/tick; bounds ${lo.toFixed(3)}–${hi.toFixed(3)} s, 0–${max.toFixed(3)} m/s. ${at ? `Cursor ${at.t.toFixed(3)} s · ${at.speed.toFixed(3)} m/s.` : "Selected track absent at this cycle."} Completed history; gaps stay disconnected. No confidence or verdict inferred.`;
}
