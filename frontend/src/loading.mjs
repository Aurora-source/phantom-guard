// Presentation of reported scheduler state, never a second polling loop.
export function describeProgress(state) {
  const p = state.progress || {},
    stage = p.stage || state.state || "waiting";
  const titles = {
    queued: "Waiting for the replay worker",
    cooling: "Waiting for server cooling",
    verifying: "Verifying installed inputs",
    loading: "Loading immutable inputs",
    loading_artifacts: "Loading detector artifacts",
    loading_data: "Loading the recording",
    preparing_pools: "Preparing attack material",
    planning: "Planning the supported attack",
    detecting: "Processing recorded cycles",
    complete: "Processing finished",
  };
  const cooling =
    state.runtime?.cooldown_remaining_seconds ?? state.cooling_seconds;
  const queue = state.runtime?.queue_seconds;
  const title =
    state.state === "queued" && Number.isFinite(cooling) && cooling > 0
      ? "Waiting for server cooling"
      : titles[stage] || stage.replaceAll("_", " ");
  const completed = p.completed,
    total = p.total;
  const count = Number.isFinite(completed) && completed >= 0;
  const determinate =
    count && Number.isFinite(total) && total > 0 && completed <= total;
  const unit =
    p.unit || p.units || (stage === "detecting" ? "cycles" : "items");
  let detail = count
    ? `${completed}${determinate ? ` / ${total}` : ""} ${unit} reported by the worker.`
    : "This stage has no reported total; the indicator is indeterminate.";
  if (state.state === "queued" || stage === "cooling")
    detail +=
      " One replay worker serves jobs in order; cooling pauses protect the home server.";
  if (state.queue_position != null)
    detail += ` Queue position: ${state.queue_position}.`;
  if (Number.isFinite(queue))
    detail += ` Scheduler queue wait: ${queue.toFixed(1)} s, including cooling.`;
  if (Number.isFinite(cooling) && cooling > 0)
    detail += ` Server reports ${cooling.toFixed(1)} s cooling remaining; this is not a browser countdown.`;
  return { stage, title, detail, completed, total, determinate };
}
export function initLoading(cancel) {
  const $ = (id) => document.getElementById(id),
    panel = $("loading-panel"),
    mini = $("loading-mini"),
    live = $("loading-announcement");
  let key = null,
    timer = null,
    elapsedTimer = null,
    start = 0,
    minimized = false,
    shown = false,
    phase = null,
    cancellable = false;
  const elapsed = () => {
    const value = `Elapsed ${Math.floor((performance.now() - start) / 1000)} s since this operation started`;
    $("loading-elapsed").textContent = value;
    $("mini-elapsed").textContent = value;
  };
  const visibility = () => {
    panel.hidden = !shown || minimized;
    mini.hidden = !shown || !minimized;
    document.documentElement.classList.toggle(
      "foreground-wait",
      shown && !minimized,
    );
    document.dispatchEvent(new Event("foreground-wait-change"));
    $("loading-cancel").hidden = $("mini-cancel").hidden = !cancellable;
    elapsed();
  };
  const set = (data) => {
    $("loading-title").textContent = $("mini-title").textContent = data.title;
    $("loading-detail").textContent = data.detail;
    const progress = $("loading-meter");
    if (data.determinate) {
      progress.max = data.total;
      progress.value = data.completed;
    } else progress.removeAttribute("value");
    if (phase !== data.stage) {
      phase = data.stage;
      live.textContent = data.title;
    }
  };
  const end = (owner, message) => {
    if (owner !== key) return;
    const focus =
      panel.contains(document.activeElement) ||
      mini.contains(document.activeElement);
    clearTimeout(timer);
    clearInterval(elapsedTimer);
    key = null;
    shown = false;
    visibility();
    if (message) live.textContent = message;
    if (focus)
      queueMicrotask(() => {
        if (key == null)
          (message?.startsWith("Replay ready") ? $("play") : $("run")).focus({
            preventScroll: true,
          });
      });
  };
  $("loading-minimize").onclick = () => {
    minimized = true;
    visibility();
    $("loading-expand").focus({ preventScroll: true });
  };
  $("loading-expand").onclick = () => {
    minimized = false;
    visibility();
    panel.scrollIntoView({ block: "center", behavior: "instant" });
    $("loading-minimize").focus({ preventScroll: true });
  };
  $("loading-cancel").onclick = $("mini-cancel").onclick = cancel;
  const observer = new IntersectionObserver((entries) =>
    panel.classList.toggle("offscreen", !entries[0].isIntersecting),
  );
  observer.observe(panel);
  document.addEventListener("visibilitychange", () =>
    document.documentElement.classList.toggle("page-hidden", document.hidden),
  );
  return {
    begin(owner, title, detail, allowCancel = false) {
      if (key != null) end(key);
      key = owner;
      minimized = false;
      start = performance.now();
      phase = null;
      cancellable = allowCancel;
      set({ stage: "starting", title, detail, determinate: false });
      timer = setTimeout(() => {
        if (key !== owner) return;
        shown = true;
        visibility();
        elapsedTimer = setInterval(elapsed, 1000);
        if (allowCancel)
          panel.scrollIntoView({ block: "center", behavior: "instant" });
      }, 300);
    },
    update(owner, state) {
      if (owner === key) set(describeProgress(state));
    },
    result(owner) {
      if (owner === key)
        set({
          stage: "result",
          title: "Loading the usable replay result",
          detail:
            "Detector processing has finished. Retrieving completed cycles and preparing browser playback; this is not another detector run.",
          determinate: false,
        });
    },
    end,
    clear(message) {
      if (key != null) end(key, message);
      else if (message) live.textContent = message;
    },
  };
}

// The same delayed, cancellable ownership rule for small report regions.
export function inlineWait(element) {
  let key = null,
    timer = null;
  return {
    begin(owner) {
      clearTimeout(timer);
      key = owner;
      timer = setTimeout(() => {
        if (key === owner) {
          element.classList.add("inline-wait");
          element.setAttribute("aria-busy", "true");
        }
      }, 300);
    },
    end(owner) {
      if (key !== owner) return;
      clearTimeout(timer);
      key = null;
      element.classList.remove("inline-wait");
      element.removeAttribute("aria-busy");
    },
  };
}
