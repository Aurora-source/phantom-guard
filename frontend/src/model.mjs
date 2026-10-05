// Result normalization has no attacker labels, inference or score calibration.
export const finite = (v) => typeof v === "number" && Number.isFinite(v);
export const number = (v, digits = 3) =>
  finite(v) ? v.toFixed(digits) : "Unavailable";
export const status = (o) =>
  o.alert
    ? o.flagged
      ? "Alert · currently flagged"
      : "Alert · persistence carried"
    : o.flagged
      ? "Flagged · no persistent alert"
      : "Not flagged";
export const color = (o) =>
  o.alert ? "#ff7673" : o.flagged ? "#f9c66b" : "#76d9ae";
export const definitions = {
  COUNT_MISMATCH: "Header object count differs from received frames.",
  COUNT_RANGE: "Scene object count is outside the learned envelope.",
  COUNTER: "Measurement counter continuity broke.",
  CADENCE: "Scan spacing is outside calibrated timing bounds.",
  STATUS: "Unexpected header status.",
  BAD_ID: "Unexpected CAN identifier.",
  SHORT_HEADER: "Header is too short to decode.",
  HEADER_LEN: "Header length differs from the configured format.",
  NO_HEADER: "Frames arrived without a complete header.",
  FRAME_LEN: "Object bytes cannot be decoded as the expected frame.",
  ARRIVAL: "Object arrival is outside the learned timing window.",
  BURST_GAP: "Spacing between object frames breaks the learned burst pattern.",
  RANGE_ORDER: "Object ordering breaks the observed range sequence.",
  DUP_SLOT: "A numeric slot repeats within this scan.",
  SLOT_RANGE: "Slot is outside the observed range.",
  FIXED_FIELD: "A field expected to be fixed changed.",
  RCS_GRID: "Radar cross-section value breaks the encoded grid.",
  RCS_RANGE: "Radar cross-section is outside calibrated bounds.",
  SPEED: "Reported speed exceeds the clean-data envelope.",
  ACCEL: "Change in velocity exceeds the learned acceleration envelope.",
  RR_RESID: "Range change disagrees with reported radial velocity.",
  POS_SPEED: "Position change implies an unusual speed.",
  RCS_STD: "Radar cross-section varies unusually over the track window.",
  RCS_BAND: "Radar cross-section is outside the relevant calibrated band.",
  JUMP: "Position continuity breaks for this track.",
  COLOC: "Tracks are unusually close together.",
  REPLAY:
    "A moving trajectory fingerprint repeats permitted library, past-stream or concurrent motion.",
  LEARNED:
    "Window reconstruction error exceeds the calibrated clean-validation bound.",
};
export function indexResult(result, id = "retained") {
  if (!result || !Array.isArray(result.cycles) || !result.cycles.length)
    throw new Error(
      "Empty or malformed result: no replay cycles are available.",
    );
  const histories = new Map(),
    active = new Map(),
    alerts = [],
    views = [];
  const gap = result.provenance?.configuration?.tracks?.max_gap_cycles ?? 1;
  for (let i = 0; i < result.cycles.length; i++) {
    const c = result.cycles[i];
    if (!Array.isArray(c.objects))
      throw new Error("Malformed result: object list is unavailable.");
    if (c.cycle_alert || c.cycle_reasons?.length)
      alerts.push({
        cursor: i,
        frame: c.header_frame_index,
        object: null,
        reasons: c.cycle_reasons || [],
        alert: Boolean(c.cycle_alert),
      });
    const items = c.objects.map((o, j) => {
      let key = `${id}:frame:${o.frame_index}:${i}:${j}`;
      if (o.track_id != null) {
        let a = active.get(o.track_id);
        if (!a || i - a.last > gap + 1)
          a = { key: `${id}:track:${o.track_id}:birth:${i}`, last: i };
        a.last = i;
        active.set(o.track_id, a);
        key = a.key;
      }
      const item = {
        ...o,
        reasons: o.reasons || [],
        scores: o.scores || {},
        score_status: o.score_status || {},
        key,
        identity: `${id}:cycle:${i}:frame:${o.frame_index}:object:${j}`,
        cursor: i,
      };
      const h = histories.get(key) || [];
      item.trailOffset = h.length;
      h.push(item);
      histories.set(key, h);
      if (o.alert || o.flagged)
        alerts.push({
          cursor: i,
          frame: o.frame_index,
          object: item,
          reasons: item.reasons,
          alert: Boolean(o.alert),
        });
      return item;
    });
    views.push({ ...c, objects: items, cycle_reasons: c.cycle_reasons || [] });
  }
  return { result, id, histories, alerts, views };
}
export const trail = (index, item, limit = 20) =>
  index.histories
    .get(item.key)
    ?.slice(Math.max(0, item.trailOffset - limit + 1), item.trailOffset + 1) ||
  [];
export function alertEnd(events, cursor) {
  let lo = 0,
    hi = events.length;
  while (lo < hi) {
    const m = (lo + hi) >>> 1;
    if (events[m].cursor <= cursor) lo = m + 1;
    else hi = m;
  }
  return lo;
}
export function correspondence(a, b) {
  const x = a?.result.provenance?.source_alignment,
    y = b?.result.provenance?.source_alignment;
  if (
    !x ||
    !y ||
    x.kind !== "source_cycle_index" ||
    y.kind !== x.kind ||
    !x.recording_sha256 ||
    x.recording_sha256 !== y.recording_sha256 ||
    x.segment_lo !== y.segment_lo
  )
    return null;
  const lookup = new Map();
  b.views.forEach((c, i) => {
    if (Number.isInteger(c.source_cycle_index))
      lookup.set(c.source_cycle_index, i);
  });
  return a.views.map((c) =>
    Number.isInteger(c.source_cycle_index)
      ? (lookup.get(c.source_cycle_index) ?? null)
      : null,
  );
}
export function metric(v) {
  return v !== "" && v != null && Number.isFinite(Number(v)) ? Number(v) : null;
}
export const percent = (v) =>
  metric(v) == null ? "Unavailable" : `${(metric(v) * 100).toFixed(1)}%`;
