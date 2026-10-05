import { chromium, expect } from "@playwright/test";
import { writeFile, mkdir } from "node:fs/promises";
const url = process.env.PHANTOMGUARD_BROWSER_URL || "http://127.0.0.1:8773";
const browser = await chromium.launch(),
  page = await browser.newPage();
const rows = [];
let result = null;
page.on("response", async (r) => {
  if (r.ok() && r.url().endsWith("/result")) result = await r.json();
});
try {
  await page.goto(url);
  await expect(page.locator("#ready")).toContainText("CPU pipeline ready");
  await page.locator("#cycles").fill("150");
  for (const [attack, level] of [
    ["T2", "A2"],
    ["T3", "A3"],
    ["T4", "A3"],
  ]) {
    await page.locator("#attack").selectOption(attack);
    await page.locator("#level").selectOption(level);
    await page.locator("#run").click();
    await expect(page.locator("#progress")).toContainText("Complete ·", {
      timeout: 120000,
    });
    expect(result.request.attack).toBe(attack);
    expect(result.request.level).toBe(level);
    expect(result.cycles.length).toBe(150);
    rows.push({
      attack,
      level,
      request: result.request,
      provenance: result.provenance,
      summary: result.summary,
      timings: result.timings,
    });
    console.log("PASS actual", attack, level, JSON.stringify(result.summary));
  }
  await page.locator("#reset").click();
  await expect(page.locator("#empty")).toBeVisible();
  await mkdir("../runs/frontend/browser", { recursive: true });
  await writeFile(
    "../runs/frontend/browser/attack-options.json",
    JSON.stringify(rows, null, 2),
  );
} finally {
  await browser.close();
}
