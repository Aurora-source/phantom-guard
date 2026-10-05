// Targeted UI fixtures only: no detector performance or accuracy claims.
import { chromium, expect } from "@playwright/test";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
const root = new URL("../../src/phantomguard/web/static/", import.meta.url),
  out = new URL("../../runs/frontend-polish/", import.meta.url);
await mkdir(out, { recursive: true });
const csp =
  "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'";
const server = createServer(async (req, res) => {
  const file = {
    "/": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/assets/theme.js": "assets/theme.js",
  }[req.url];
  if (!file) {
    res.writeHead(404);
    res.end();
    return;
  }
  res.writeHead(200, {
    "Content-Type": file.endsWith("html")
      ? "text/html"
      : file.endsWith("css")
        ? "text/css"
        : "application/javascript",
    "Content-Security-Policy": csp,
    "Cache-Control": "no-store",
  });
  res.end(await readFile(new URL(file, root)));
});
await new Promise((resolve) => server.listen(8774, "127.0.0.1", resolve));
const browser = await chromium.launch({
  executablePath: process.env.PHANTOMGUARD_CHROMIUM,
  args: [
    "--enable-webgl",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
const checks = [],
  errors = [];
const fixture = {
  tick_seconds: 0.0001,
  period_seconds: 0.0332,
  request: {
    recording: "onePersonMovingFrontAndBack.csv",
    attack: null,
    seed: 11,
  },
  provenance: { configuration: { tracks: { max_gap_cycles: 1 } } },
  summary: { object_cycles: 50, alerting_cycles: 0 },
  elapsed_seconds: 1,
  cycles: Array.from({ length: 50 }, (_, i) => ({
    index: i,
    header_frame_index: i * 2,
    header_timestamp_ticks: i * 332,
    cycle_alert: false,
    cycle_reasons: [],
    objects: [
      {
        frame_index: i * 2 + 1,
        track_id: 1,
        slot: 1,
        timestamp_ticks: i * 332 + 3,
        x: 3 + i * 0.02,
        y: 1,
        vx: 0.25,
        vy: 0,
        in_roi: true,
        flagged: false,
        alert: false,
        reasons: [],
        scores: {},
        score_status: {},
      },
    ],
  })),
};
let posts = 0,
  polls = 0,
  mode = "normal",
  fixtureStage = "queued";
async function fixtures(context) {
  await context.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname,
      method = route.request().method();
    let data = {};
    if (path === "/api/catalog")
      data = {
        recordings: ["onePersonMovingFrontAndBack.csv"],
        attacks: [{ attack: "T1", level: "A2", supported: true }],
        max_cycles: 1200,
      };
    else if (path === "/api/sessions") data = { token: "test-only-ui-fixture" };
    else if (path === "/api/jobs" && method === "POST") {
      posts++;
      polls = 0;
      data = {
        id: "ui-fixture-" + posts,
        state: "queued",
        queue_position: 1,
        cooling_seconds: 2,
      };
    } else if (path === "/api/evaluation") data = { rows: [], clean: [] };
    else if (method === "DELETE") data = { state: "cancelled" };
    else if (path.endsWith("/result")) {
      await new Promise((r) => setTimeout(r, 1200));
      data = fixture;
    } else {
      polls++;
      data =
        mode === "normal"
          ? fixtureStage === "queued"
            ? { state: "queued", queue_position: 1, cooling_seconds: 2 }
            : fixtureStage === "detecting"
              ? {
                  state: "running",
                  progress: {
                    stage: "detecting",
                    completed: 25,
                    total: 50,
                    unit: "cycles",
                  },
                }
              : { state: "complete" }
          : {
              state: "failed",
              error:
                "UI fixture: required model models/missing.npz is unavailable; upload the compatible bundle.",
            };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
  });
  await context.route("**/readyz", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: '{"ok":true}',
    }),
  );
}
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "light",
  });
  await fixtures(context);
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:8774");
  await expect(page.locator("#run")).toBeEnabled();
  await page.locator("#view3d").click();
  await expect(page.locator("#view3d")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#active-view")).toContainText(
    "3D orbit · Awaiting replay",
  );
  await expect(page.locator("#scene3d")).toBeVisible();
  checks.push("3D before replay has synchronized selection and empty renderer");
  await page.locator("#theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("#theme")).toHaveValue("dark");
  await page.locator("#theme").selectOption("system");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  checks.push("explicit persistence and System theme changes");
  await page.locator("#cycles").fill("50");
  await page.locator("#run").click();
  await expect(page.locator("#loading-panel")).toBeVisible();
  await page.screenshot({
    path: new URL("loading-central-dark.png", out).pathname,
  });
  await page.locator("#loading-minimize").click();
  await expect(page.locator("#loading-mini")).toBeVisible();
  await expect(page.locator("#loading-panel")).toBeHidden();
  await page.locator('nav a[href="#guide"]').click();
  await page.screenshot({
    path: new URL("loading-minimized-dark.png", out).pathname,
  });
  await page.locator("#loading-expand").click();
  await expect(page.locator("#loading-panel")).toBeVisible();
  fixtureStage = "detecting";
  await expect(page.locator("#loading-detail")).toContainText(
    "25 / 50 cycles",
    { timeout: 15000 },
  );
  fixtureStage = "complete";
  await expect(page.locator("#loading-title")).toHaveText(
    "Loading the usable replay result",
    { timeout: 15000 },
  );
  await expect(page.locator("#progress")).toContainText("Complete", {
    timeout: 15000,
  });
  await expect(page.locator("#loading-panel")).toBeHidden();
  checks.push(
    "queued/real totals/result retrieval, minimize/expand and completion",
  );
  await page.locator("#object-select").selectOption({ index: 1 });
  await page.locator("#step").click();
  const selected = await page.locator("#object-select").inputValue();
  const cursor = await page.locator("#seek").inputValue();
  for (const theme of ["light", "dark"]) {
    await page.locator("#theme").selectOption(theme);
    for (const view of ["3d", "2d"]) {
      await page.locator("#view" + view).click();
      await expect(page.locator("#object-select")).toHaveValue(selected);
      await expect(page.locator("#seek")).toHaveValue(cursor);
      await expect(page.locator("#view" + view)).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    }
  }
  await expect(page.locator("#track-chart-note")).toContainText("0.250 m/s");
  assertNoExtra();
  await page.locator("#speed").selectOption("0.5");
  await page.locator("#play").click();
  await page.waitForTimeout(200);
  await page.locator("#view3d").click();
  await expect(page.locator("#play")).toHaveText("Pause");
  await page.locator("#play").click();
  checks.push(
    "paused/playing projection and theme retain object/history/cursor; no new jobs",
  );
  // Deterministic fixture failure; no missing artifacts are synthesized.
  mode = "error";
  await page.locator("#run").click();
  await expect(page.locator("#progress")).toContainText(
    "upload the compatible bundle",
    { timeout: 15000 },
  );
  await expect(page.locator("#loading-panel")).toBeHidden();
  await expect(page.locator("#run")).toBeEnabled();
  checks.push("representative artifact error stops waiting");
  await page.locator("#reset").click();
  await expect(page.locator("#active-view")).toContainText("Awaiting replay");
  await expect(page.locator("#view3d")).toHaveAttribute("aria-pressed", "true");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator("#hero-pause")).toHaveText("Motion reduced");
  await expect(page.locator("#lesson-play")).toHaveText("Motion reduced");
  await page.locator("#lesson-step").click();
  checks.push("reduced motion uses static illustrations and manual step");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.locator("#lesson-play").click();
  await page.locator("#lesson-play").click();
  const phase = await page.locator("#lesson-phase").innerText();
  await page.waitForTimeout(800);
  await expect(page.locator("#lesson-phase")).toHaveText(phase);
  checks.push("illustration pause and step");
  for (const size of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const theme of ["light", "dark"]) {
      await page.locator("#theme").selectOption(theme);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({
        path: new URL(`fixture-${size.width}-${theme}.png`, out).pathname,
        fullPage: true,
      });
      if (
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        )
      )
        throw Error("Page overflow at " + size.width);
    }
  }
  checks.push("desktop/mobile layout and themed guide/navigation");
  const fallback = await browser.newContext();
  await fixtures(fallback);
  await fallback.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      return type.startsWith("webgl")
        ? null
        : original.call(this, type, ...args);
    };
  });
  const fp = await fallback.newPage();
  fp.on("pageerror", (e) => errors.push(e.message));
  await fp.goto("http://127.0.0.1:8774");
  await fp.locator("#view3d").click();
  await expect(fp.locator("#view2d")).toHaveAttribute("aria-pressed", "true");
  await expect(fp.locator("#view-note")).toContainText("WebGL is unavailable");
  checks.push("WebGL failure visibly selects working 2D fallback");
  if (errors.length) throw Error(errors.join("; "));
  console.log(
    JSON.stringify(
      { checks, errors, source: "Synthetic UI fixtures only" },
      null,
      2,
    ),
  );
  function assertNoExtra() {
    if (posts !== 1) throw Error("View/theme submitted another detector job");
  }
} finally {
  await writeFile(
    new URL("fixture-checks.json", out),
    JSON.stringify(
      { checks, errors, source: "Synthetic UI fixtures only" },
      null,
      2,
    ),
  );
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
