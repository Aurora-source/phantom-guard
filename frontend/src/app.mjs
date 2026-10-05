import {
  indexResult,
  alertEnd,
  status,
  definitions,
  finite,
  number,
  metric,
  percent,
  correspondence,
} from "./model.mjs";
import { plan, extent, OrbitView } from "./render.mjs";
import { initLoading, inlineWait } from "./loading.mjs";
import { initTheme } from "./theme.mjs";
import { trackChart } from "./chart.mjs";
import { initIllustrations } from "./illustrations.mjs";
const $ = (id) => document.getElementById(id),
  text = (id, v) => {
    $(id).textContent = String(v);
  },
  node = (tag, value, className) => {
    const e = document.createElement(tag);
    if (value != null) e.textContent = String(value);
    if (className) e.className = className;
    return e;
  };
let token = null,
  job = null,
  catalog = null,
  current = null,
  retained = null,
  cursor = 0,
  selected = null,
  selectedFrame = null,
  replayVisible = true,
  playing = false,
  playTimer = null,
  pollController = null,
  generation = 0,
  busy = false,
  ready = false,
  orbit = null,
  view = "2d",
  hits = [],
  bounds = extent(null),
  logPage = 0,
  evidence = null,
  evaluationGeneration = 0,
  individual = false,
  comparisonCache = null;
const reduce = matchMedia("(prefers-reduced-motion: reduce)");
const storage = {
  read() {
    try {
      return JSON.parse(
        sessionStorage.getItem("phantomguard.session") || "null",
      );
    } catch {
      return null;
    }
  },
  write() {
    try {
      sessionStorage.setItem(
        "phantomguard.session",
        JSON.stringify({ token, job }),
      );
    } catch {}
  },
  clear() {
    try {
      sessionStorage.removeItem("phantomguard.session");
    } catch {}
  },
};
class ApiError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}
async function api(path, { method = "GET", body, signal, owner = token } = {}) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 30000),
    abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(path, {
      method,
      headers: {
        ...(owner ? { Authorization: `Bearer ${owner}` } : {}),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError(
        `Service returned an unreadable response (HTTP ${response.status}). Reconnect or retry.`,
        response.status,
      );
    }
    if (!response.ok) {
      const msg =
        typeof data.error === "string"
          ? data.error
          : Array.isArray(data.errors)
            ? data.errors.map(String).join("; ")
            : `HTTP ${response.status}`;
      throw new ApiError(msg, response.status);
    }
    return data;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
function showError(e) {
  if (e.name === "AbortError") return;
  let message = e.message || "The service could not complete this request.";
  if (e.code === 401) {
    message =
      "Session expired. Reconnect to create a new isolated session, then run again. " +
      message;
    token = null;
    job = null;
    storage.clear();
    $("reconnect").hidden = false;
  } else if (e.code === 404 && job) {
    message =
      "This job expired or is no longer available. Reconnect or start a fresh run. " +
      message;
    job = null;
    storage.write();
    $("reconnect").hidden = false;
  } else if (e.code === 429 || e.code === 409)
    message =
      "The request was rejected by the queue/session limit. Retry when capacity is available. " +
      message;
  text("progress", message);
  loading.clear(message);
}
const recordingCopy = {
  emptyRoom: {
    title: "Background / empty room",
    text: "No test subject; one person working in the background. Genuine sensor clutter and ghosts remain in this clean recording.",
  },
  onePersonMovingFrontAndBack: {
    title: "Toward & away",
    text: "One person walking toward and away from the radar. Planar observations are shown as symbolic points, without inferred object classes.",
  },
  onePersonMovingSideToSide: {
    title: "Across the field of view",
    text: "One person moving from side to side. Position continuity matters even when radial velocity is small.",
  },
  multiplePeopleChaotic: {
    title: "Unscripted motion",
    text: "Several people moving without a script. Complex clean motion can trigger false alerts.",
  },
};
const attackCopy = {
  T1: [
    "T1 · Phantom",
    "Injects additional static or moving objects into ordinary CAN frames.",
  ],
  T2: [
    "T2 · Flood",
    "Adds a short-lived burst of objects. Support and slot capacity determine whether it can be emitted.",
  ],
  T3: [
    "T3 · Recorded replay",
    "Copies a recorded moving trajectory, exact or translated. Inherently A3/A4; browser source material comes from permitted training segments.",
  ],
  T4: [
    "T4 · Shift / overwrite",
    "Adds a growing position offset to a moving track. It needs eligible moving material.",
  ],
};
function updateOptions() {
  if (!catalog) return;
  const attack = $("attack").value,
    choices = catalog.attacks.filter((c) => c.attack === attack);
  for (const option of $("level").options) {
    const choice = choices.find((c) => c.level === option.value);
    option.disabled = Boolean(attack && (!choice || choice.supported !== true));
    option.textContent =
      option.value + (option.disabled ? " · unsupported" : "");
    option.title = choice?.reason || "";
  }
  if (attack && $("level").selectedOptions[0]?.disabled)
    $("level").value = choices.find((c) => c.supported)?.level || "";
  const choice = choices.find((c) => c.level === $("level").value),
    motions =
      choice?.motions ||
      (["T3", "T4"].includes(attack) ? ["moving"] : ["moving", "static"]);
  for (const o of $("motion").options) o.disabled = !motions.includes(o.value);
  if (!motions.includes($("motion").value))
    $("motion").value = motions[0] || "moving";
  $("level").disabled = busy || !attack;
  $("motion").disabled = busy || !attack || motions.length === 1;
  $("variant").disabled = busy || attack !== "T3";
  const rec = catalog.recordings.find(
      (r) => (typeof r === "string" ? r : r.id) === $("recording").value,
    ),
    meta = recordingCopy[$("recording").value.replace(/\.csv$/, "")];
  text(
    "recording-description",
    (typeof rec === "object" ? rec.description : null) ||
      meta?.text ||
      "Server-approved recorded scene. No object class labels are inferred.",
  );
  text(
    "attack-description",
    attackCopy[attack]?.[1] ||
      "No attack is injected. Any alert on this clean clip is a false positive.",
  );
  text(
    "eligibility",
    attack
      ? choice?.reason ||
          choice?.eligibility?.reason ||
          "Supported by this catalog. Material/slot eligibility is checked during preparation; an emitted attack may escape."
      : "Clean replay of the test segment, starting with fresh causal history.",
  );
  $("run").disabled =
    busy || !ready || !token || Boolean(attack && choice?.supported !== true);
}
function setBusy(value) {
  busy = value;
  for (const id of ["recording", "attack", "seed", "cycles"])
    $(id).disabled = value;
  $("cancel").disabled = !value;
  updateOptions();
}
function pause() {
  playing = false;
  clearTimeout(playTimer);
  text("play", "Play replay");
}
function schedule() {
  clearTimeout(playTimer);
  if (!playing || !current || document.hidden || !replayVisible) return;
  playTimer = setTimeout(
    () => {
      if (cursor >= current.views.length - 1) {
        pause();
        return;
      }
      cursor++;
      logPage = 0;
      render();
      schedule();
    },
    Math.max(
      16,
      ((current.result.period_seconds || 0.0332) * 1000) /
        Number($("speed").value),
    ),
  );
}
function destroyOrbit() {
  if (orbit) {
    orbit.dispose();
    orbit = null;
  }
}
function switchView(next) {
  destroyOrbit();
  view = next;
  $("scene").hidden = next === "3d";
  $("scene3d").hidden = next !== "3d";
  $("view2d").setAttribute("aria-pressed", String(next === "2d"));
  $("view3d").setAttribute("aria-pressed", String(next === "3d"));
  if (next === "3d") {
    try {
      orbit = new OrbitView($("scene3d"), selectObject);
      orbit.failed = () => {
        switchView("2d");
        text(
          "view-note",
          "WebGL context unavailable. The fully functional 2D view is active.",
        );
      };
      orbit.setup(bounds);
      text(
        "view-note",
        "Recorded x/y and vx/vy are mapped to a ground plane. Height, dimensions, sensor casing and light are illustrative. Drag to orbit; scroll/pinch to zoom.",
      );
    } catch {
      view = "2d";
      $("scene").hidden = false;
      $("scene3d").hidden = true;
      $("view2d").setAttribute("aria-pressed", "true");
      $("view3d").setAttribute("aria-pressed", "false");
      text(
        "view-note",
        "WebGL is unavailable. The fully functional 2D view is active.",
      );
    }
  } else
    text(
      "view-note",
      "x/y are recorded planar coordinates. Tick duration and distance scales are configurable assumptions. No measured elevation or object classes.",
    );
  text(
    "scene-label",
    view === "3d"
      ? "ILLUSTRATIVE HEIGHT / RECORDED PLANAR COORDINATES"
      : "PLAN VIEW / PROVISIONAL COORDINATE UNITS",
  );
  text(
    "active-view",
    `Selected view: ${view === "3d" ? "3D orbit" : "2D plan"}${current ? "" : " · Awaiting replay"}`,
  );
  render();
}
function selectObject(o) {
  selected = o?.key || null;
  selectedFrame = o?.frame_index ?? null;
  render();
}
function addPairs(target, entries) {
  const dl = node("dl");
  for (const [key, value] of entries) {
    dl.append(
      node("dt", key),
      node("dd", value == null ? "Unavailable" : value),
    );
  }
  target.append(dl);
}
function inspector(c) {
  const pick = $("object-select");
  pick.replaceChildren(new Option("Select an object in this cycle", ""));
  for (const o of c.objects) {
    const label = `${o.slot == null ? "Malformed" : `Slot ${o.slot.toString(16).padStart(2, "0")}`} · frame ${o.frame_index} · ${status(o)}`;
    pick.append(new Option(label, o.identity));
  }
  pick.disabled = !c.objects.length;
  const o =
    c.objects.find(
      (o) => o.key === selected && o.frame_index === selectedFrame,
    ) || c.objects.find((o) => o.key === selected);
  pick.value = o?.identity || "";
  text("object-count", `${c.objects.length} IN FRAME`);
  const details = $("object-details");
  details.replaceChildren();
  if (o) {
    details.append(
      node(
        "span",
        status(o),
        "verdict-status" + (o.alert ? " alert" : o.flagged ? " flagged" : ""),
      ),
    );
    addPairs(details, [
      ["Cycle / final frame", `${c.index} / ${o.frame_index}`],
      ["Slot / track", `${o.slot ?? "Unknown"} / ${o.track_id ?? "Untracked"}`],
      [
        "Track lifetime",
        o.key.split(":birth:")[1] != null
          ? `begins at displayed cycle ${Number(o.key.split(":birth:")[1]) + 1}`
          : "Single untracked frame",
      ],
      ["Timestamp / ticks", o.timestamp_ticks],
      ["x / y", `${number(o.x, 2)} / ${number(o.y, 2)}`],
      ["vx / vy", `${number(o.vx, 2)} / ${number(o.vy, 2)}`],
      [
        "Region of interest",
        o.in_roi ? "Inside configured ROI" : "Outside ROI / unavailable",
      ],
      [
        "Attribution",
        "Unknown; detector flags do not identify a forged object",
      ],
    ]);
    details.append(node("h4", "Current reported reasons"));
    details.append(
      node(
        "p",
        "Fusion can exclude learned-only evidence; the flag/alert status above is the actual detector decision.",
        "hint",
      ),
    );
    if (o.reasons.length) {
      for (const r of o.reasons) {
        const p = node("p", null, "reason");
        p.append(
          node("b", r),
          document.createTextNode(
            definitions[r] ||
              "Definition unavailable in this schema; inspect the generated evidence.",
          ),
        );
        details.append(p);
      }
    } else
      details.append(
        node(
          "p",
          o.alert
            ? "No current reason. This alert is carried by persistence from earlier flags."
            : "No current triggering reason.",
          "hint",
        ),
      );
    details.append(node("h4", "Scores & calibrated bounds"));
    const keys = new Set([
      ...Object.keys(o.scores),
      ...Object.keys(o.score_status),
    ]);
    if (!keys.size)
      details.append(node("p", "Scores unavailable for this frame.", "hint"));
    for (const k of keys) {
      const p = node("div", `${k}: ${number(o.scores[k], 5)}`, "score-row");
      const bound = o.score_bounds?.[k] ?? current.result.score_bounds?.[k],
        s = o.score_status[k];
      p.append(
        node(
          "span",
          `Status: ${s || (finite(o.scores[k]) ? "reported" : "unavailable")}`,
        ),
        node(
          "span",
          `Bound: ${bound != null ? JSON.stringify(bound) : "Unavailable in this result"}`,
        ),
      );
      details.append(p);
    }
    const jump = node("a", "Inspect selected clip provenance ↓");
    jump.href = "#provenance";
    details.append(jump);
  } else
    details.append(
      node(
        "p",
        selected
          ? "Selected track is absent in this cycle. No new object is selected by reused slot."
          : "Select an object to inspect its actual verdict.",
        "muted",
      ),
    );
  const layer = $("layer-status");
  layer.replaceChildren();
  for (const name of ["protocol", "kinematic", "replay", "learned"])
    layer.append(
      node(
        "div",
        `${name} · ${c.layer_status?.[name] || "Unavailable / not reported"}`,
      ),
    );
}
function logs() {
  if (!current) return;
  const events = $("alerts-only").checked
      ? current.alertEvents
      : current.alerts,
    end = alertEnd(events, cursor),
    stop = Math.max(0, end - logPage * 30),
    start = Math.max(0, stop - 30),
    log = $("alert-log");
  log.replaceChildren();
  for (let i = stop - 1; i >= start; i--) {
    const e = events[i],
      row = node("button", null, "entry");
    row.type = "button";
    const who = e.object
      ? `Object frame ${e.frame} · slot ${e.object.slot ?? "?"}`
      : `Scene · header frame ${e.frame ?? "unknown"}`;
    row.append(node("span", `cycle ${current.views[e.cursor].index}`, "time"));
    const description = node("span", null, "codes");
    description.append(
      node("b", who),
      document.createTextNode(
        e.object
          ? status(e.object)
          : e.alert
            ? "Scene alert · object attribution unavailable"
            : "Scene warning",
      ),
    );
    description.append(
      node(
        "div",
        e.reasons.length
          ? e.reasons.join(" · ")
          : "Persistence carried · no current trigger",
      ),
    );
    row.append(description);
    row.onclick = () => {
      pause();
      cursor = e.cursor;
      selected = e.object?.key || null;
      selectedFrame = e.object?.frame_index ?? null;
      logPage = 0;
      render();
    };
    log.append(row);
  }
  if (!stop) log.append(node("p", "No detector events through this cycle."));
  text(
    "log-count",
    `${end} events · showing ${start + Number(stop > 0)}–${stop}`,
  );
  $("log-older").disabled = start === 0;
  $("log-newer").disabled = logPage === 0;
}
function render() {
  if (document.hidden) return;
  const start = performance.now();
  if (!current) {
    if (view === "3d" && orbit) orbit.update(null, 0, null);
    else hits = plan($("scene"), null, 0, null, bounds);
    trackChart($("track-chart"), $("track-chart-note"), null, null, 0);
    return;
  }
  const c = current.views[cursor],
    selection =
      c.objects.find(
        (o) => o.key === selected && o.frame_index === selectedFrame,
      ) || c.objects.find((o) => o.key === selected);
  if (view === "3d" && orbit)
    orbit.update(current, cursor, selection?.identity);
  else hits = plan($("scene"), current, cursor, selection?.identity, bounds);
  const first = current.views[0].header_timestamp_ticks,
    time =
      finite(c.header_timestamp_ticks) &&
      finite(first) &&
      finite(current.result.tick_seconds)
        ? (
            (c.header_timestamp_ticks - first) *
            current.result.tick_seconds
          ).toFixed(3) + " s"
        : "time unavailable";
  $("seek").value = cursor;
  text(
    "clock",
    `Cycle ${c.index} · ${cursor + 1}/${current.views.length} · ${time} · header frame ${c.header_frame_index ?? "?"}`,
  );
  const warning = $("cycle-warning");
  warning.hidden = !c.cycle_alert && !c.cycle_reasons.length;
  warning.textContent = `${c.cycle_alert ? "! Scene alert" : "Scene warning"} · ${c.cycle_reasons.join(" · ") || "Persistent scene finding"} · Object attribution unavailable`;
  inspector(c);
  trackChart(
    $("track-chart"),
    $("track-chart-note"),
    current,
    selected,
    cursor,
  );
  logs();
  renderComparison();
  const elapsed = performance.now() - start;
  performance.measure("phantomguard-render", { start, end: performance.now() });
  if (performance.getEntriesByName("phantomguard-render").length > 500)
    performance.clearMeasures("phantomguard-render");
  window.__phantomguardMetrics = {
    lastRenderMs: elapsed,
    cursor,
    cycleIndex: c.index,
    frame: c.header_frame_index,
    view,
    objects: c.objects.length,
    playing,
    generation,
    gpu: orbit
      ? {
          geometries: orbit.renderer.info.memory.geometries,
          textures: orbit.renderer.info.memory.textures,
        }
      : null,
  };
}
function renderComparison() {
  if (
    comparisonCache?.current !== current ||
    comparisonCache?.retained !== retained
  )
    comparisonCache = {
      current,
      retained,
      mapping: correspondence(current, retained),
    };
  const mapping = comparisonCache.mapping,
    available = Boolean(mapping?.some((v) => v != null));
  $("compare").disabled = !available;
  $("retain").disabled = !current || Boolean(current.result.request?.attack);
  const canvas = $("comparison-scene");
  canvas.hidden = !$("compare").checked || !available;
  if (!retained)
    text(
      "compare-note",
      "Retain a completed clean run. Alignment requires an explicit source-cycle contract; emitted frame indices are not alignment keys.",
    );
  else if (!available) {
    $("compare").checked = false;
    canvas.hidden = true;
    text(
      "compare-note",
      `Clean run ${retained.id.slice(0, 8)} retained in this tab. Source-cycle correspondence is unavailable in this backend; synchronized comparison cannot be established.`,
    );
  } else {
    const i = mapping[cursor];
    text(
      "compare-note",
      `Separate runs ${current.id.slice(0, 8)} / ${retained.id.slice(0, 8)} · aligned by declared source cycle. ${i == null ? "No corresponding clean cycle." : `Source cycle ${current.views[cursor].source_cycle_index}.`} Artifact IDs: ${current.result.provenance?.model_artifact_id || "unavailable"} / ${retained.result.provenance?.model_artifact_id || "unavailable"}.`,
    );
    canvas.hidden = canvas.hidden || i == null;
    if (!canvas.hidden) plan(canvas, retained, i, null, bounds);
  }
}
function clearDisplay(keepRetained = false) {
  pause();
  destroyOrbit();
  current = null;
  comparisonCache = null;
  cursor = 0;
  selected = null;
  selectedFrame = null;
  logPage = 0;
  bounds = extent(null);
  if (!keepRetained) retained = null;
  $("compare").checked = false;
  $("compare").disabled = true;
  $("comparison-scene").hidden = true;
  $("retain").disabled = true;
  $("empty").hidden = false;
  $("cycle-warning").hidden = true;
  for (const id of ["play", "back", "step", "seek", "object-select"])
    $(id).disabled = true;
  $("seek").max = 0;
  $("seek").value = 0;
  for (const id of ["objects", "alerts", "latency", "assembly"]) text(id, "—");
  text("clock", "No cycle selected");
  text("run-kind", "AWAITING A RECORDED RUN");
  text("scene-label", "PLAN VIEW / PROVISIONAL COORDINATE UNITS");
  text("alert-log", "No replay processed yet.");
  text("log-count", "0 events");
  $("log-older").disabled = true;
  $("log-newer").disabled = true;
  text("provenance", "No run selected.");
  text("raw-provenance", "");
  text(
    "object-details",
    "Frame identity, track lifetime, reasons and actual scores appear after selection.",
  );
  text("layer-status", "Available after a run.");
  text("object-count", "0 IN FRAME");
  text(
    "view-note",
    "x/y are recorded planar coordinates. Tick duration and distance scales are configurable assumptions. No measured elevation or object classes.",
  );
  text("compare-note", "No aligned comparison selected.");
  $("object-select").replaceChildren(new Option("No object selected", ""));
  switchView(view);
}
async function reset(keepRetained = false) {
  loading.clear();
  const operation = ++generation,
    oldJob = job,
    oldToken = token;
  pollController?.abort();
  job = null;
  storage.write();
  clearDisplay(keepRetained);
  window.__phantomguardMetrics = null;
  performance.clearMeasures("phantomguard-render");
  setBusy(false);
  text(
    "progress",
    "Reset complete. A new run starts with fresh causal history.",
  );
  if (oldJob) {
    try {
      await api(`/api/jobs/${oldJob}`, { method: "DELETE", owner: oldToken });
    } catch (e) {
      if (operation === generation && e.code !== 404 && e.code !== 401)
        showError(e);
    }
  }
  return operation;
}
const delay = (ms, signal) =>
  new Promise((resolve, reject) => {
    const onAbort = () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      },
      t = setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
const visible = (signal) =>
  document.hidden
    ? new Promise((resolve, reject) => {
        const cleanup = () => {
            document.removeEventListener("visibilitychange", change);
            signal.removeEventListener("abort", abort);
          },
          change = () => {
            if (!document.hidden) {
              cleanup();
              resolve();
            }
          },
          abort = () => {
            cleanup();
            reject(new DOMException("Aborted", "AbortError"));
          };
        document.addEventListener("visibilitychange", change);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      })
    : Promise.resolve();
function installResult(data, id) {
  try {
    current = indexResult(data, id);
  } catch (e) {
    throw new ApiError(e.message, 422);
  }
  current.alertEvents = current.alerts.filter((e) => e.alert);
  bounds = extent(current);
  cursor = 0;
  selected = null;
  selectedFrame = null;
  destroyOrbit();
  switchView(view);
  $("empty").hidden = true;
  $("seek").max = current.views.length - 1;
  for (const id of ["play", "back", "step", "seek"]) $(id).disabled = false;
  text(
    "run-kind",
    data.request?.attack
      ? `SIMULATED ${data.request.attack} / ${data.request.level}`
      : "RECORDED / CLEAN · NO INJECTION",
  );
  text("objects", data.summary?.object_cycles ?? "Unavailable");
  text("alerts", data.summary?.alerting_cycles ?? "Unavailable");
  text(
    "latency",
    finite(data.processing?.p99_ms)
      ? number(data.processing.p99_ms, 2) + " ms"
      : "Unavailable",
  );
  text(
    "assembly",
    finite(data.assembly?.assembly_p99_ms)
      ? number(data.assembly.assembly_p99_ms, 1) + " ms"
      : "Unmeasured",
  );
  const p = data.provenance || {},
    target = $("provenance");
  target.replaceChildren();
  addPairs(target, [
    ["Run ID", id],
    [
      "Recording / split",
      `${data.request?.recording || "Unavailable"} / ${p.part || "Unavailable"}`,
    ],
    ["Source segment", `${p.segment_lo ?? "?"}–${p.segment_hi ?? "?"}`],
    [
      "Attack / seed",
      `${data.request?.attack || "Clean"} / ${data.request?.seed ?? "?"}`,
    ],
    [
      "Source / run commit",
      p.source_commit || p.git_head || "Not reported by legacy result",
    ],
    ["Recording SHA-256", p.recording_sha256],
    ["Model artifact ID", p.model_artifact_id],
    ["Baseline SHA-256", p.baseline_sha256],
    [
      "Configuration ID",
      p.configuration_sha256 ||
        "Configuration embedded below; hash unavailable",
    ],
    [
      "Tick assumption",
      finite(data.tick_seconds)
        ? `${data.tick_seconds} s / tick`
        : "Unavailable",
    ],
    [
      "Job elapsed",
      finite(data.elapsed_seconds)
        ? number(data.elapsed_seconds, 2) + " s"
        : "Unavailable",
    ],
    [
      "Detector interval total (legacy wall)",
      finite(data.timings?.detector_cpu_seconds)
        ? number(data.timings.detector_cpu_seconds, 3) + " s"
        : "Unavailable",
    ],
    [
      "Preparation",
      data.timings
        ? `${number(data.timings.artifact_loading_seconds, 2)} s artifacts; ${number(data.timings.data_loading_seconds, 2)} s data; ${number(data.timings.attacker_planning_seconds, 2)} s attacker`
        : "Unavailable",
    ],
  ]);
  text(
    "raw-provenance",
    JSON.stringify(
      {
        request: data.request,
        provenance: p,
        timings: data.timings,
        processing: data.processing,
        assembly: data.assembly,
      },
      null,
      2,
    ),
  );
  text(
    "progress",
    `Complete · ${current.views.length} discrete cycles · ${number(data.elapsed_seconds, 1)} s job time. Playback speed is independent of detection.`,
  );
  render();
}
async function poll(id, operation, initial) {
  pollController?.abort();
  const controller = new AbortController();
  pollController = controller;
  let state = initial,
    pollMs = 1000,
    failures = 0;
  while (operation === generation && job === id) {
    try {
      await visible(controller.signal);
      if (!state) {
        await delay(pollMs, controller.signal);
        await visible(controller.signal);
        state = await api(`/api/jobs/${id}`, { signal: controller.signal });
      }
      if (operation !== generation || job !== id) return;
      loading.update(operation, state);
      const progress = state.progress || {},
        stage =
          progress.stage ||
          (state.state === "queued"
            ? "waiting for scheduler (queue/cooling duration not reported)"
            : "running (stage not reported)"),
        counts = Number.isFinite(progress.completed)
          ? ` · ${progress.completed}${Number.isFinite(progress.total) ? ` / ${progress.total}` : ""} ${progress.unit || progress.units || (progress.stage === "detecting" ? "cycles" : "items")}`
          : "";
      text(
        "progress",
        `${state.state} · ${stage}${counts}${state.queue_position != null ? ` · queue position ${state.queue_position}` : ""}${state.cooling_seconds != null ? ` · cooling ${state.cooling_seconds} s` : ""}`,
      );
      if (state.state === "complete") {
        loading.result(operation);
        const data = await api(`/api/jobs/${id}/result`, {
          signal: controller.signal,
        });
        if (operation === generation && job === id) {
          installResult(data, id);
          setBusy(false);
          loading.end(
            operation,
            "Replay ready. Use Play replay to inspect completed cycles.",
          );
        }
        return;
      }
      if (
        ["failed", "timed_out", "cancelled", "error", "canceled"].includes(
          state.state,
        )
      )
        throw new ApiError(state.error || `Job ${state.state}`, 400);
      pollMs = ["queued", "cooling"].includes(state.state) ? 2000 : 1000;
      state = null;
      failures = 0;
    } catch (e) {
      if (operation !== generation || e.name === "AbortError") return;
      if (!e.code && failures < 3) {
        failures++;
        pollMs = Math.min(8000, 1000 * 2 ** failures);
        text(
          "progress",
          `Connection interrupted. Recovering job status in ${pollMs / 1000}s…`,
        );
        state = null;
        continue;
      }
      showError(e);
      setBusy(false);
      $("reconnect").hidden = false;
      return;
    }
  }
}
async function run() {
  if (busy || !ready) return;
  const request = {
    recording: $("recording").value,
    attack: $("attack").value || null,
    level: $("attack").value ? $("level").value : null,
    seed: Number($("seed").value),
    cycles: Number($("cycles").value),
    motion: $("motion").value,
    variant: $("variant").value,
  };
  const resetting = reset(true);
  setBusy(true);
  const resetOperation = await resetting;
  if (resetOperation !== generation) return;
  const operation = ++generation;
  loading.begin(
    operation,
    "Submitting isolated replay job",
    "The recording and requested seed are being admitted to the bounded server queue.",
    true,
  );
  text("progress", "Submitting isolated replay job…");
  const owner = token;
  try {
    const created = await api("/api/jobs", {
      method: "POST",
      body: request,
      owner,
    });
    if (operation !== generation) {
      await api(`/api/jobs/${created.id}`, { method: "DELETE", owner });
      return;
    }
    if (!created.id)
      throw new Error("Malformed job response: identifier unavailable.");
    job = created.id;
    storage.write();
    await poll(job, operation, created);
  } catch (e) {
    if (operation === generation) {
      showError(e);
      setBusy(false);
    }
  }
}
async function boot(recover = true) {
  const operation = ++generation;
  loading.begin(
    operation,
    "Checking the replay service",
    "Loading the recording catalog and verifying installed artifacts. No detector job is running yet.",
  );
  pollController?.abort();
  $("reconnect").hidden = true;
  try {
    catalog = await api("/api/catalog");
    if (operation !== generation) return;
    if (!Array.isArray(catalog.recordings) || !Array.isArray(catalog.attacks))
      throw new Error("Catalog unavailable or malformed.");
    $("recording").replaceChildren();
    for (const r of catalog.recordings) {
      const id = typeof r === "string" ? r : r.id,
        title = typeof r === "object" ? r.title : null;
      $("recording").append(
        new Option(
          title || recordingCopy[id.replace(/\.csv$/, "")]?.title || id,
          id,
        ),
      );
    }
    $("recording").value = catalog.recordings.some(
      (r) =>
        (typeof r === "string" ? r : r.id) ===
        "onePersonMovingFrontAndBack.csv",
    )
      ? "onePersonMovingFrontAndBack.csv"
      : $("recording").options[0]?.value;
    $("attack").replaceChildren(new Option("Clean · no injection", ""));
    for (const t of new Set(catalog.attacks.map((c) => c.attack)))
      $("attack").append(new Option(attackCopy[t]?.[0] || t, t));
    $("level").replaceChildren();
    for (const l of new Set(catalog.attacks.map((c) => c.level)))
      $("level").append(new Option(l, l));
    $("level").value = Array.from($("level").options).some(
      (o) => o.value === "A2",
    )
      ? "A2"
      : $("level").options[0]?.value;
    $("cycles").max = catalog.max_cycles;
    $("cycles").value = Math.min(600, catalog.max_cycles);
    $("seed").min = catalog.seed_min ?? catalog.seed?.min ?? 0;
    $("seed").max = catalog.seed_max ?? catalog.seed?.max ?? 4294967295;
    const saved = recover ? storage.read() : null;
    token = saved?.token || null;
    job = saved?.job || null;
    if (!token) {
      const session = await api("/api/sessions", { method: "POST" });
      if (operation !== generation) return;
      token = session.token;
    }
    storage.write();
    try {
      const health = await api("/readyz");
      if (operation !== generation) return;
      ready = health.ok === true;
      text("ready", ready ? "CPU pipeline ready" : "Service not ready");
      $("ready").classList.toggle("is-ready", ready);
    } catch (e) {
      if (operation !== generation) return;
      ready = false;
      text("ready", "Artifacts / service not ready");
      showError(e);
    }
    if (operation !== generation) return;
    updateOptions();
    loading.end(
      operation,
      ready
        ? "Replay service ready."
        : "Replay service needs attention; see the artifact error.",
    );
    evaluation(false);
    if (job) {
      setBusy(true);
      loading.begin(
        operation,
        "Recovering this tab’s replay",
        "Retrieving the existing job status; no duplicate job is submitted.",
        true,
      );
      text("progress", "Recovering the previous job in this tab…");
      await poll(job, operation);
    } else if (ready)
      text(
        "progress",
        "Ready. Choose a recording and run the causal detector.",
      );
  } catch (e) {
    if (operation === generation) {
      showError(e);
      setBusy(false);
      $("reconnect").hidden = false;
      text("ready", "Service unavailable");
    }
  }
  render();
}
async function evaluation(match) {
  const operation = ++evaluationGeneration;
  evidenceWait.begin(operation);
  individual = match;
  $("matrix-results").setAttribute("aria-pressed", String(!match));
  $("filter-results").setAttribute("aria-pressed", String(match));
  text("evaluation-note", "Loading actual generated report…");
  try {
    const query = new URLSearchParams({
      recording: $("recording").value,
      seed: $("seed").value,
    });
    if ($("attack").value) {
      query.set("attack", $("attack").value);
      query.set("level", $("level").value);
    }
    const data = await api(
      match ? `/api/evaluation/runs?${query}` : "/api/evaluation",
    );
    if (operation !== evaluationGeneration) return;
    if (!Array.isArray(data.rows) || !Array.isArray(data.clean))
      throw new Error("Generated evaluation schema unavailable.");
    evidence = data;
    renderEvidence();
  } catch (e) {
    if (operation === evaluationGeneration) {
      evidence = null;
      text("evaluation-note", `Generated evidence unavailable: ${e.message}`);
      for (const id of [
        "evaluation-rows",
        "denominators",
        "clean-results",
        "evaluation-provenance",
      ])
        $(id).replaceChildren();
    }
  } finally {
    evidenceWait.end(operation);
  }
}
function renderEvidence() {
  if (!evidence) return;
  const choice = $("evidence-status").value,
    sort = $("evidence-sort").value;
  let rows = evidence.rows.filter((r) => {
    const s = r.status || "ok";
    if (!choice) return true;
    if (choice === "miss")
      return (
        metric(r.undetected_instances) > 0 ||
        (metric(r.attack_instance_detection_rate) != null &&
          metric(r.attack_instance_detection_rate) < 1)
      );
    if (choice === "no_material") return /material/.test(s);
    if (choice === "excluded") return /exclu|capacity/.test(s);
    return choice === "ok" ? ["ok", "completed"].includes(s) : s === choice;
  });
  if (sort !== "default") {
    const k =
        sort === "objects-asc"
          ? "object_detection_rate"
          : "attack_instance_detection_rate",
      sign = sort === "scene-desc" ? -1 : 1;
    rows = [...rows].sort((a, b) => {
      const x = metric(a[k]),
        y = metric(b[k]);
      return x == null ? 1 : y == null ? -1 : sign * (x - y);
    });
  }
  text(
    "evaluation-note",
    `${evidence.note || "Actual generated offline measurements."} ${individual ? "Matching runs use configured matrix seeds, which differ from the direct browser seed." : "Pooled matrix; recording detail is available in Matching runs."} ${rows.length}/${evidence.rows.length} supplied rows shown (server limit ${evidence.row_limit ?? "unreported"}).`,
  );
  text("evaluation-provenance", JSON.stringify(evidence.provenance, null, 2));
  const tbody = $("evaluation-rows");
  tbody.replaceChildren();
  for (const r of rows) {
    const tr = node("tr");
    const pair = (a, b) =>
      metric(a) != null && metric(b) != null ? `${a} / ${b}` : "Unavailable";
    for (const v of [
      `${r.split || "?"} / ${r.file || "pooled recordings"}`,
      `${r.attack_type || "?"} / ${r.level || "?"}`,
      r.configured_seed
        ? `${r.configured_seed} / ${r.run}`
        : "all configured runs",
      pair(r.detected_instances, r.attack_instances),
      percent(r.attack_instance_detection_rate),
      pair(r.identified_object_frames, r.forged_object_frames),
      percent(r.object_detection_rate),
      pair(r.undetected_instances, r.right_censored_instances),
      r.status || "completed observed attacks",
    ])
      tr.append(node("td", v));
    if (r.reason) tr.lastChild.append(node("div", r.reason));
    tbody.append(tr);
  }
  $("evidence-empty").hidden = rows.length > 0;
  const den = $("denominators");
  den.replaceChildren();
  for (const [s, n] of Object.entries(
    evidence.provenance?.run_status_counts || {},
  ))
    den.append(
      node("span", `${s}: ${typeof n === "object" ? JSON.stringify(n) : n}`),
    );
  if (!den.children.length)
    den.append(
      node("span", "Attempt / unsupported / excluded counts unavailable"),
    );
  for (const [s, n] of Object.entries(evidence.denominators || {}))
    den.append(node("span", `${s}: ${n}`));
  const clean = $("clean-results");
  clean.replaceChildren();
  for (const split of ["timeblock", "loso"]) {
    const data = evidence.clean.filter(
        (r) => r.split === split && r.part === "test" && r.status === "ok",
      ),
      events = data.reduce((s, r) => s + (metric(r.alert_events) ?? 0), 0),
      minutes = data.reduce((s, r) => s + (metric(r.minutes_exact) ?? 0), 0),
      group = node("div", null, "clean-group");
    group.append(
      node(
        "h3",
        `${split === "timeblock" ? "Time block" : "Leave one scenario out"} · clean false-alert episodes`,
      ),
      node(
        "p",
        minutes
          ? `${(events / minutes).toFixed(2)} / minute · ${events} episodes / ${minutes.toFixed(3)} minutes · target < 1 ${events / minutes < 1 ? "met" : "NOT MET"}`
          : "Clean denominator unavailable",
      ),
    );
    const list = node("ul");
    for (const r of data)
      list.append(
        node(
          "li",
          `${recordingCopy[(r.file || "").replace(/\.csv$/, "")]?.title || r.file} · ${metric(r.alerts_per_minute)?.toFixed(2) ?? "Unavailable"}/min · ${r.alert_events} episodes / ${r.minutes_exact} min`,
        ),
      );
    group.append(list);
    clean.append(group);
  }
  text(
    "precision-note",
    evidence.localization
      ? JSON.stringify(evidence.localization)
      : "Unavailable in the legacy report API. Exact-object recall is not precision.",
  );
  text(
    "ablation-note",
    evidence.ablation
      ? JSON.stringify(evidence.ablation)
      : "No compatible measured ablation is supplied by this endpoint. No benefit is claimed.",
  );
}
const stages = {
  frames: [
    "Recorded CAN frames",
    "The source reconstructs headers and ordinary object bytes from the recordings. A simulated attacker can modify/inject frames before the common decode path. No attack labels are attached to detector inputs.",
  ],
  decode: [
    "Common decoding & cycle assembly",
    "The same byte decoder and cycle assembler process clean and injected frames in arrival order. A cycle is closed before its verdict is computed; capture assembly delay is distinct from CPU inference time.",
  ],
  track: [
    "Causal tracking",
    "Tracks link current observations to their own past. Numeric slots can be reassigned; final frame identities and separate track lifetimes remain the evidence keys. No future frame can influence a past verdict.",
  ],
  checks: [
    "Layered checks",
    "Protocol checks test format, count and timing. Kinematic checks compare motion and radar cross-section continuity. Replay checks search permitted/past motion fingerprints. The learned model reports window reconstruction error with availability/warm-up status.",
  ],
  fusion: [
    "Fusion & persistence",
    "Hard active reasons can alert immediately. Softer evidence votes across calibrated M-of-N track cycles. Learned-only evidence does not independently count toward an alert in the current configuration. Persistence can carry an alert with no current trigger.",
  ],
  verdict: [
    "Verdicts & alerts",
    "Scene findings warn about the cycle; object findings identify final object frames and track context. A detector anomaly is not proof that an object is forged, authentic, or associated with a known person.",
  ],
};
function stage(key) {
  for (const b of document.querySelectorAll("[data-stage]"))
    b.setAttribute("aria-pressed", String(b.dataset.stage === key));
  const d = $("stage-detail");
  d.replaceChildren(
    node("b", stages[key][0]),
    document.createTextNode(stages[key][1]),
  );
}
let lesson = "T1",
  phase = 0;
const lessons = {
  T1: "Phantom: add another encoded object to the stream. Plausible appearance alone does not establish a plausible history.",
  T2: "Flood: add a burst of short-lived objects. Scene count or protocol findings need not localize every forged frame.",
  T3: "Replay: copy a recorded moving trajectory, optionally translated. A3/A4 only. Complex clean repetition can also resemble a replay.",
  T4: "Shift / overwrite: introduce a growing offset on a moving trajectory. Smooth drift can evade checks; this illustration is not a measured detector response.",
};
function illustrate() {
  for (const b of document.querySelectorAll("[data-lesson]"))
    b.setAttribute("aria-pressed", String(b.dataset.lesson === lesson));
  text("lesson-text", lessons[lesson]);
  text("lesson-phase", ` STEP ${phase + 1} / 4`);
  const ns = "http://www.w3.org/2000/svg",
    group = $("lesson-markers");
  group.replaceChildren();
  const paths = {
    T1: [
      "M220 172L235 160",
      "M220 172L270 145",
      "M220 172L320 131",
      "M220 172L360 110",
    ],
    T2: [
      "M220 172L235 160",
      "M200 185L235 160M230 180L285 135",
      "M180 170L235 160M230 180L285 135M270 176L330 115",
      "M180 170L235 160M230 180L285 135M270 176L330 115M320 178L390 115",
    ],
    T3: [
      "M40 182L110 154",
      "M40 182L110 154L190 136",
      "M140 210L210 182L290 164",
      "M140 210L210 182L290 164L375 134",
    ],
    T4: [
      "M40 182L110 154",
      "M40 182L110 154L190 123",
      "M40 182L110 154L190 123L275 74",
      "M40 182L110 154L190 123L275 74L390 31",
    ],
  };
  $("lesson-path").setAttribute("d", paths[lesson][phase]);
  const total = lesson === "T2" ? phase * 3 + 1 : 1;
  for (let i = 0; i < total; i++) {
    const e = document.createElementNS(ns, "circle");
    e.setAttribute("cx", String(220 + phase * 40 + (i % 3) * 18));
    e.setAttribute("cy", String(150 - phase * 22 + Math.floor(i / 3) * 18));
    e.setAttribute("r", "5");
    e.setAttribute("class", "lesson-marker");
    group.append(e);
  }
}
$("run-form").onsubmit = (e) => {
  e.preventDefault();
  run();
};
$("reset").onclick = () => reset();
$("cancel").onclick = async () => {
  const operation = await reset(true);
  if (operation === generation)
    text(
      "progress",
      "Cancellation requested; replay cleared. A new run uses fresh causal history.",
    );
};
$("attack").onchange = updateOptions;
$("level").onchange = updateOptions;
$("recording").onchange = updateOptions;
$("reconnect").onclick = async () => {
  await reset();
  storage.clear();
  token = null;
  job = null;
  ready = false;
  boot(false);
};
$("play").onclick = () => {
  if (!current) return;
  if (playing) {
    pause();
    return;
  }
  if (cursor === current.views.length - 1) cursor = 0;
  playing = true;
  text("play", "Pause");
  render();
  schedule();
};
const step = (d) => {
  pause();
  if (current) {
    cursor = Math.max(0, Math.min(current.views.length - 1, cursor + d));
    logPage = 0;
    render();
  }
};
$("back").onclick = () => step(-1);
$("step").onclick = () => step(1);
$("seek").oninput = () => {
  pause();
  cursor = Number($("seek").value);
  logPage = 0;
  render();
};
$("speed").onchange = schedule;
$("object-select").onchange = () =>
  selectObject(
    current?.views[cursor].objects.find(
      (o) => o.identity === $("object-select").value,
    ),
  );
$("view2d").onclick = () => switchView("2d");
$("view3d").onclick = () => switchView("3d");
$("retain").onclick = () => {
  if (current && !current.result.request?.attack) {
    retained = current;
    renderComparison();
  }
};
$("compare").onchange = renderComparison;
$("scene").onclick = (e) => {
  const r = $("scene").getBoundingClientRect(),
    x = e.clientX - r.left,
    y = e.clientY - r.top;
  let best = null,
    dist = 18;
  for (const h of hits) {
    const d = Math.hypot(h.x - x, h.y - y);
    if (d < dist) {
      best = h.o;
      dist = d;
    }
  }
  selectObject(best);
};
$("alerts-only").onchange = () => {
  logPage = 0;
  logs();
};
$("log-older").onclick = () => {
  logPage++;
  logs();
};
$("log-newer").onclick = () => {
  logPage = Math.max(0, logPage - 1);
  logs();
};
$("matrix-results").onclick = () => evaluation(false);
$("filter-results").onclick = () => evaluation(true);
$("evidence-status").onchange = renderEvidence;
$("evidence-sort").onchange = renderEvidence;
$("guide-open").onclick = () => $("guide-modal").showModal();
$("guide-close").onclick = () => $("guide-modal").close();
$("guide-start").onclick = () => $("guide-modal").close();
for (const b of document.querySelectorAll("[data-stage]"))
  b.onclick = () => stage(b.dataset.stage);
for (const b of document.querySelectorAll("[data-lesson]"))
  b.onclick = () => {
    lesson = b.dataset.lesson;
    phase = 0;
    illustrate();
    illustrations.resetLesson();
  };
document.addEventListener("keydown", (e) => {
  if (
    !current ||
    e.altKey ||
    e.ctrlKey ||
    e.metaKey ||
    e.target.closest("input,select,textarea,button,dialog,a")
  )
    return;
  if (e.code === "Space") {
    e.preventDefault();
    $("play").click();
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    step(1);
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    step(-1);
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearTimeout(playTimer);
  } else {
    render();
    schedule();
  }
});
reduce.addEventListener("change", () => {
  if (reduce.matches) pause();
});
new ResizeObserver(() => render()).observe($("scene").parentElement);
new IntersectionObserver((entries) => {
  replayVisible = entries[0].isIntersecting;
  if (!replayVisible) clearTimeout(playTimer);
  else {
    render();
    schedule();
  }
}).observe($("scene").parentElement);
window.addEventListener("pagehide", () => {
  pause();
  pollController?.abort();
  destroyOrbit();
});
stage("frames");
illustrate();
let lastLessonTick = -1;
const stageKeys = Object.keys(stages);
const illustrations = initIllustrations(
  (tick) => {
    if (tick === lastLessonTick) return;
    lastLessonTick = tick;
    phase = tick % 4;
    illustrate();
    const key = stageKeys[tick % stageKeys.length];
    stage(key);
    $("packet").dataset.stage = String(tick % stageKeys.length);
    text(
      "lesson-phase",
      `STEP ${phase + 1} / 4 · ${lesson} mechanism / no detector verdict`,
    );
  },
  () => {
    lastLessonTick = -1;
  },
);
initTheme(() => {
  orbit?.theme();
  render();
  illustrations.paint();
});
const loading = initLoading(() => $("cancel").click());
const evidenceWait = inlineWait($("evaluation-note"));
boot();
