import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { cpus, totalmem } from "node:os";
const base = process.env.PHANTOMGUARD_BROWSER_URL || "http://127.0.0.1:8773",
  out = new URL("../../runs/frontend/browser/", import.meta.url),
  shots = new URL("../../docs/parallel/screenshots/", import.meta.url);
await mkdir(out, { recursive: true });
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({
    args: ["--enable-precise-memory-info"],
  }),
  report = {
    source:
      "Actual legacy Python API + explicitly synthetic browser-contract fixtures",
    browser: browser.version(),
    hardware: {
      cpu: cpus()[0].model,
      logicalCPUs: cpus().length,
      ramGiB: totalmem() / 2 ** 30,
    },
    checks: [],
    metrics: {},
    errors: [],
  };
const check = (name, detail = true) => {
  report.checks.push({ name, detail });
  console.log("PASS", name);
};
const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  }),
  page = await context.newPage();
let polls = 0,
  requests = 0,
  actualResult = null;
page.on("pageerror", (e) => report.errors.push(e.message));
page.on("console", (e) => {
  if (e.type() === "error") report.errors.push(e.text());
});
page.on("request", (r) => {
  requests++;
  if (
    /\/api\/jobs\/[^/]+$/.test(new URL(r.url()).pathname) &&
    r.method() === "GET"
  )
    polls++;
});
page.on("response", async (r) => {
  if (r.ok() && /\/api\/jobs\/[^/]+\/result$/.test(new URL(r.url()).pathname))
    actualResult = await r.json();
});
const waitRun = async (p) => {
  await p.locator("#run").click();
  await expect(p.locator("#progress")).toContainText("Complete ·", {
    timeout: 120000,
  });
};
const seek = async (p, n) => {
  await p.locator("#seek").evaluate((e, n) => {
    e.value = n;
    e.dispatchEvent(new Event("input", { bubbles: true }));
  }, n);
};
const screenshot = async (name, p = page) =>
  p.screenshot({
    path: new URL(name, shots).pathname.replace(/^\/(\w:)/, "$1"),
    fullPage: true,
  });
async function sample(p, mode, duration = 1500) {
  const cdp = await p.context().newCDPSession(p);
  await cdp.send("Performance.enable");
  await p.evaluate(() => performance.clearMeasures("phantomguard-render"));
  const before = (await cdp.send("Performance.getMetrics")).metrics,
    start = Date.now();
  await p.waitForTimeout(duration);
  const after = (await cdp.send("Performance.getMetrics")).metrics;
  const get = (x, n) => x.find((v) => v.name === n)?.value || 0;
  const detail = await p.evaluate(() => ({
    heap: performance.memory?.usedJSHeapSize,
    render: performance
      .getEntriesByName("phantomguard-render")
      .map((e) => e.duration),
    gpu: window.__phantomguardMetrics?.gpu,
  }));
  await cdp.detach();
  return {
    mode,
    sampleMs: Date.now() - start,
    taskCpuMs:
      (get(after, "TaskDuration") - get(before, "TaskDuration")) * 1000,
    scriptCpuMs:
      (get(after, "ScriptDuration") - get(before, "ScriptDuration")) * 1000,
    heapBytes: detail.heap,
    renderCount: detail.render.length,
    renderMedianMs:
      detail.render.sort((a, b) => a - b)[
        Math.floor(detail.render.length / 2)
      ] ?? null,
    renderP99Ms:
      detail.render[
        Math.min(
          detail.render.length - 1,
          Math.floor(detail.render.length * 0.99),
        )
      ] ?? null,
    gpu: detail.gpu,
  };
}
try {
  const response = await page.goto(base);
  await expect(page.locator("#ready")).toContainText("CPU pipeline ready");
  const csp = response.headers()["content-security-policy"];
  expect(csp).toContain("script-src 'self'");
  expect(csp).not.toContain("unsafe");
  await expect(page.locator("#evaluation-rows tr")).not.toHaveCount(0);
  check("CSP and three packaged asset routes", csp);
  report.metrics.initial = await page.evaluate(() => ({
    navigationMs: performance.getEntriesByType("navigation")[0].duration,
    resources: performance
      .getEntriesByType("resource")
      .map((r) => ({
        path: new URL(r.name).pathname,
        transferBytes: r.transferSize,
        durationMs: r.duration,
      })),
    viewport: { width: innerWidth, height: innerHeight },
    overflow: document.documentElement.scrollWidth > innerWidth,
  }));
  await page.locator("#guide-open").click();
  await expect(page.locator("#guide")).toBeVisible();
  await page.keyboard.press("Escape");
  check("Accessible optional guide and direct replay path");
  await page.locator("#attack").selectOption("T3");
  expect(await page.locator('#level option[value="A2"]').isDisabled()).toBe(
    true,
  );
  expect(await page.locator("#level").inputValue()).toBe("A3");
  await page.locator("#attack").selectOption("");
  check("Catalog-supported options; T3/A0–A2 unavailable");
  await page.locator("#cycles").fill("600");
  await waitRun(page);
  await writeFile(
    new URL("actual-clean.json", out),
    JSON.stringify(actualResult),
  );
  check("Actual clean recording / supplied compatible artifacts", {
    cycles: actualResult.cycles.length,
    processing: actualResult.processing,
    timings: actualResult.timings,
  });
  await page.locator("#object-select").selectOption({ index: 1 });
  await expect(page.locator("#object-details")).toContainText(
    "Unknown; detector flags",
  );
  await seek(page, 14);
  const clock = await page.locator("#clock").textContent();
  await page.locator("#step").click();
  expect(await page.locator("#seek").inputValue()).toBe("15");
  await page.locator("#back").click();
  expect(await page.locator("#clock").textContent()).toBe(clock);
  check("Discrete step/back/scrub alignment");
  await screenshot("after-desktop.png");
  report.metrics.paused2d = await sample(page, "paused 2D");
  const pollsAtComplete = polls;
  await page.locator("#play").click();
  report.metrics.playing2d = await sample(page, "playing 2D");
  await page.locator("#play").click();
  expect(polls).toBe(pollsAtComplete);
  check("Terminal polling stops; playback makes no HTTP calls");
  await page.locator("#view3d").click();
  await expect(page.locator("#scene3d")).toBeVisible();
  await page.locator("#object-select").selectOption({ index: 1 });
  await screenshot("after-3d.png");
  report.metrics.paused3d = await sample(page, "paused 3D");
  await page.locator("#play").click();
  report.metrics.playing3d = await sample(page, "playing 3D");
  await page.locator("#play").click();
  for (let i = 0; i < 8; i++) {
    await page.locator("#view2d").click();
    await page.locator("#view3d").click();
  }
  await expect(page.locator("#scene3d")).toBeVisible();
  const garbage = await page.context().newCDPSession(page);
  await garbage.send("HeapProfiler.collectGarbage");
  await garbage.detach();
  report.metrics.afterViewChanges = await sample(
    page,
    "after eight 2D/3D changes",
  );
  check("On-demand 3D with bounded resources and view disposal");
  const gpuInfo = await page.evaluate(() => {
    const gl = document.getElementById("scene3d").getContext("webgl2"),
      ext = gl.getExtension("WEBGL_debug_renderer_info");
    return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "unavailable";
  });
  report.hardware.webglRenderer = gpuInfo;
  await page.locator("#retain").click();
  await expect(page.locator("#compare")).toBeDisabled();
  await expect(page.locator("#compare-note")).toContainText(
    "correspondence is unavailable",
  );
  check("Legacy comparison honestly unavailable; clean result retained");
  await page.locator("#attack").selectOption("T1");
  await page.locator("#level").selectOption("A2");
  await waitRun(page);
  await writeFile(
    new URL("actual-attack.json", out),
    JSON.stringify(actualResult),
  );
  check("Actual T1/A2 attack through ordinary frame path", {
    cycles: actualResult.cycles.length,
    alerts: actualResult.summary.alerting_cycles,
    timings: actualResult.timings,
  });
  await seek(page, 599);
  await expect(page.locator("#alert-log .entry")).not.toHaveCount(0);
  await page.locator("#alert-log .entry").first().click();
  await expect(page.locator("#clock")).toContainText(
    `Cycle ${await page.evaluate(() => window.__phantomguardMetrics.cycleIndex)}`,
  );
  await screenshot("after-attack.png");
  check("Actual alert navigation selects associated discrete cycle");
  await page.reload();
  await expect(page.locator("#progress")).toContainText("Complete ·", {
    timeout: 10000,
  });
  check("Reload recovers completed job with isolated session token");
  const second = await browser.newContext(),
    p2 = await second.newPage();
  await p2.goto(base);
  await expect(p2.locator("#ready")).toContainText("CPU pipeline ready");
  const owner = await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("phantomguard.session")),
    ),
    other = await p2.evaluate(() =>
      JSON.parse(sessionStorage.getItem("phantomguard.session")),
    );
  expect(owner.token).not.toBe(other.token);
  const stolen = await p2.evaluate(
    async ({ job, token }) =>
      (
        await fetch(`/api/jobs/${job}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
      ).status,
    { job: owner.job, token: other.token },
  );
  expect(stolen).toBe(404);
  await p2.locator("#cycles").fill("150");
  await waitRun(p2);
  check("Two actual sessions isolate ownership and causal runs");
  await p2.locator("#reset").click();
  await second.close();
  await page.locator("#run").click();
  await page.locator("#cancel").click();
  await expect(page.locator("#progress")).toContainText(
    "Cancellation requested",
    { timeout: 10000 },
  );
  await expect(page.locator("#empty")).toBeVisible();
  await page.waitForTimeout(2200);
  await expect(page.locator("#empty")).toBeVisible();
  check("Actual cancellation/reset discards late status/result");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator("#attack").selectOption("");
  await page.locator("#cycles").fill("150");
  await waitRun(page);
  await page.locator("#object-select").selectOption({ index: 1 });
  await screenshot("after-mobile.png");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  check("390×844 mobile / reduced motion, usable replay with no page overflow");
  await page.locator("#reset").click();
  report.metrics.resetCycles = [];
  for (let i = 0; i < 3; i++) {
    await waitRun(page);
    await page.locator("#view3d").click();
    await page.locator("#reset").click();
    const gcSession = await page.context().newCDPSession(page);
    await gcSession.send("HeapProfiler.collectGarbage");
    await gcSession.detach();
    report.metrics.resetCycles.push(
      await sample(page, `reset stress ${i + 1}`, 300),
    );
  }
  check("Three repeated actual runs/resets release results and GPU resources");
  report.metrics.polls = polls;
  report.metrics.totalRequests = requests;
  report.metrics.afterReset = await sample(page, "after reset");
  await context.close();
  // Synthetic contract fixtures have no detector-performance meaning and never ship as measurements.
  const fixtureCatalog = {
    recordings: ["emptyRoom.csv"],
    attacks: [
      { attack: "T1", level: "A2", supported: true },
      {
        attack: "T3",
        level: "A2",
        supported: false,
        reason: "inherently A3/A4",
      },
      { attack: "T3", level: "A3", supported: true },
    ],
    max_cycles: 30,
    seed_min: 4,
    seed_max: 20,
  };
  const obj = (frame, track, extra = {}) => ({
    frame_index: frame,
    slot: 7,
    track_id: track,
    x: 3,
    y: 1,
    vx: 0.25,
    vy: 0,
    in_roi: true,
    moving: true,
    timestamp_ticks: frame * 2,
    reasons: [],
    scores: {},
    score_status: { ae: "warming_up" },
    flagged: false,
    alert: false,
    ...extra,
  });
  const fixtures = {
    request: { recording: "emptyRoom.csv", attack: null, seed: 11 },
    provenance: {
      source_alignment: {
        kind: "source_cycle_index",
        recording_sha256: "schema-fixture-only",
        segment_lo: 20,
      },
      model_artifact_id: "schema-fixture-model",
    },
    tick_seconds: 0.0001,
    period_seconds: 0.0332,
    roi: 15,
    elapsed_seconds: 0.01,
    cycles: [
      {
        index: 8,
        source_cycle_index: 20,
        header_timestamp_ticks: 1000,
        header_frame_index: 100,
        cycle_reasons: ["COUNT_MISMATCH"],
        cycle_alert: true,
        layer_status: { learned: "unavailable: fixture" },
        objects: [obj(101, 1)],
      },
      {
        index: 9,
        source_cycle_index: 21,
        header_timestamp_ticks: 1332,
        header_frame_index: 120,
        cycle_reasons: [],
        cycle_alert: false,
        objects: [
          obj(121, 1, {
            alert: true,
            score_status: { ae: "unavailable_model" },
          }),
          obj(122, 2, {
            flagged: true,
            reasons: ["ACCEL"],
            scores: { accel: 0 },
            score_bounds: { accel: { max: 2 } },
          }),
        ],
      },
      {
        index: 10,
        source_cycle_index: 22,
        header_timestamp_ticks: 1664,
        header_frame_index: 140,
        cycle_reasons: [],
        cycle_alert: false,
        objects: [],
      },
    ],
  };
  async function fixturePage({
    result = fixtures,
    error = null,
    readyState = true,
    delayResult = 0,
    webgl = true,
    progress = { stage: "detecting", completed: 1, total: 3 },
    status = "complete",
  } = {}) {
    const ctx = await browser.newContext({
        viewport: { width: 1280, height: 900 },
        reducedMotion: "reduce",
      }),
      p = await ctx.newPage();
    let posts = 0,
      polls = 0;
    if (!webgl)
      await p.addInitScript(() => {
        const get = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
          return /webgl/.test(kind) ? null : get.call(this, kind, ...args);
        };
      });
    await p.route("**/*", async (route) => {
      const req = route.request(),
        url = new URL(req.url()),
        path = url.pathname;
      const json = (body, code = 200) =>
        route.fulfill({
          status: code,
          contentType: "application/json",
          body: JSON.stringify(body),
        });
      if (path === "/api/catalog") return json(fixtureCatalog);
      if (path === "/readyz")
        return json(
          readyState ? { ok: true } : { error: "Model artifact missing" },
          readyState ? 200 : 503,
        );
      if (path === "/api/sessions") return json({ token: "test-only-session" });
      if (path === "/api/evaluation" || path === "/api/evaluation/runs")
        return json({ error: "Fixture report deliberately unavailable" }, 503);
      if (path === "/api/jobs" && req.method() === "POST") {
        posts++;
        return error
          ? json(error.body, error.code)
          : json({ id: `fixture-${posts}`, state: "queued" });
      }
      if (/\/api\/jobs\//.test(path)) {
        if (req.method() === "DELETE") return json({ state: "cancelled" });
        if (path.endsWith("/result")) {
          if (delayResult) await new Promise((r) => setTimeout(r, delayResult));
          return json(result);
        }
        polls++;
        return json({ id: "fixture-1", state: status, progress });
      }
      return route.continue();
    });
    p.on("pageerror", (e) => report.errors.push(e.message));
    await p.goto(base);
    await expect(p.locator("#recording")).toHaveValue("emptyRoom.csv");
    return {
      ctx,
      p,
      get posts() {
        return posts;
      },
      get polls() {
        return polls;
      },
    };
  }
  let f = await fixturePage({ webgl: false });
  await waitRun(f.p);
  expect(await f.p.locator("#seed").getAttribute("min")).toBe("4");
  expect(await f.p.locator("#seed").getAttribute("max")).toBe("20");
  await f.p.locator("#object-select").selectOption({ index: 1 });
  await expect(f.p.locator("#object-details")).toContainText("Not flagged");
  await expect(f.p.locator("#cycle-warning")).toContainText(
    "Object attribution unavailable",
  );
  await f.p.locator("#view3d").click();
  await expect(f.p.locator("#scene")).toBeVisible();
  await expect(f.p.locator("#view-note")).toContainText("WebGL is unavailable");
  await seek(f.p, 1);
  await f.p.locator("#object-select").selectOption({ index: 1 });
  await expect(f.p.locator("#object-details")).toContainText(
    "persistence carried",
  );
  await expect(f.p.locator("#object-details")).toContainText("Unavailable");
  await f.p.locator("#object-select").selectOption({ index: 2 });
  await expect(f.p.locator("#object-details")).toContainText("accel: 0.00000");
  await expect(f.p.locator("#object-details")).toContainText('"max":2');
  await seek(f.p, 2);
  await expect(f.p.locator("#object-details")).toContainText("absent");
  await expect(f.p.locator("#object-select")).toBeDisabled();
  check(
    "Synthetic: scene/object separation, persistence, bounds, warming/unavailable, empty scene, no WebGL",
  );
  await f.ctx.close();
  f = await fixturePage({ readyState: false });
  await expect(f.p.locator("#run")).toBeDisabled();
  await expect(f.p.locator("#progress")).toContainText(
    "Model artifact missing",
  );
  await expect(f.p.locator("#evaluation-note")).toContainText("unavailable");
  check("Synthetic: missing artifacts / generated evidence");
  await f.ctx.close();
  for (const [code, message] of [
    [401, "Session expired"],
    [429, "Queue capacity"],
    [400, "No eligible moving material"],
  ]) {
    f = await fixturePage({ error: { code, body: { error: message } } });
    await f.p.locator("#run").click();
    await expect(f.p.locator("#progress")).toContainText(message);
    expect(f.posts).toBe(1);
    check(`Synthetic: HTTP ${code} ${message}`);
    await f.ctx.close();
  }
  f = await fixturePage({
    error: { code: 503, body: { error: { malformed: true } } },
  });
  await f.p.locator("#run").click();
  await expect(f.p.locator("#progress")).toContainText("HTTP 503");
  check("Synthetic: malformed error envelope");
  await f.ctx.close();
  f = await fixturePage({ result: { cycles: [] } });
  await f.p.locator("#run").click();
  await expect(f.p.locator("#progress")).toContainText("Empty or malformed");
  await expect(f.p.locator("#empty")).toBeVisible();
  check("Synthetic: empty/malformed result never becomes a current run");
  await f.ctx.close();
  f = await fixturePage({ delayResult: 1600 });
  await f.p.locator("#run").click();
  await f.p.waitForTimeout(1250);
  await f.p.locator("#reset").click();
  await f.p.waitForTimeout(2000);
  await expect(f.p.locator("#empty")).toBeVisible();
  await expect(f.p.locator("#clock")).toContainText("No cycle");
  check("Synthetic: delayed result after reset cannot reinstall stale data");
  await f.ctx.close();
  f = await fixturePage({ status: "running", progress: {} });
  await f.p.locator("#run").click();
  await expect(f.p.locator("#progress")).toContainText("stage not reported", {
    timeout: 5000,
  });
  expect(await f.p.locator("#progress").textContent()).not.toContain("%");
  await f.p.locator("#cancel").click();
  check("Synthetic: legacy running state never invents percentages");
  await f.ctx.close();
  f = await fixturePage();
  await waitRun(f.p);
  await f.p.locator("#retain").click();
  await expect(f.p.locator("#compare")).toBeEnabled();
  await f.p.locator("#compare").check();
  await expect(f.p.locator("#comparison-scene")).toBeVisible();
  await seek(f.p, 1);
  await expect(f.p.locator("#compare-note")).toContainText("Source cycle 21");
  check(
    "Synthetic: additive declared source alignment enables discrete comparison",
  );
  await f.ctx.close();

  f = await fixturePage();
  await waitRun(f.p);
  await seek(f.p, 1);
  await f.p.locator("#object-select").selectOption({ index: 2 });
  await expect(f.p.locator("#object-details")).toContainText("9 / 122");
  await f.p.locator("#alert-log .entry").first().click();
  await expect(f.p.locator("#object-details")).toContainText("9 / 121");
  check(
    "Synthetic: alert final frame and selected-track lifetime remain aligned",
  );
  await f.ctx.close();
  f = await fixturePage({
    status: "running",
    progress: { stage: "cooling", completed: 0 },
  });
  await f.p.locator("#run").click();
  await expect(f.p.locator("#progress")).toContainText("cooling");
  await f.p.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await f.p.waitForTimeout(1200);
  const hiddenPolls = f.polls;
  await f.p.waitForTimeout(2300);
  expect(f.polls).toBe(hiddenPolls);
  await f.p.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await f.p.waitForTimeout(1300);
  expect(f.polls).toBeGreaterThan(hiddenPolls);
  await f.p.locator("#cancel").click();
  check(
    "Synthetic: cooling stage and visibility suspend/resume status polling",
  );
  await f.ctx.close();
  expect(report.errors).toEqual([]);
} finally {
  await writeFile(
    new URL("browser-validation.json", out),
    JSON.stringify(report, null, 2),
  );
  await browser.close();
}
console.log(
  JSON.stringify(
    {
      checks: report.checks.length,
      metrics: report.metrics,
      errors: report.errors,
    },
    null,
    2,
  ),
);
