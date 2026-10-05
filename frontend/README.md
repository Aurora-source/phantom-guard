# Phantom Guard browser

Edit `src/index.html`, `src/style.css` and the JavaScript modules in `src/`.
Build-time dependencies are isolated in this directory. Production requires
neither Node nor a graphics server. The Python package serves the compiled
`../src/phantomguard/web/static/{index.html,app.js,style.css}` plus
`assets/theme.js`, a synchronous same-origin theme bootstrap before styles.
Keep source and compiled assets together; the existing safe `/assets/` route and
package-data rules include this file without any CSP exception.

From this directory on Windows or Linux (Node 22 tested):

```text
npm ci
npm run build
npm test
npx playwright install chromium
npm run test:browser
```

The browser test expects an actual Python service at `http://127.0.0.1:8773`
with compatible trusted artifacts and recordings. Set `PHANTOMGUARD_BROWSER_URL`
to another local service URL. It runs actual clean/attacked jobs, then explicit
schema fixtures for unavailable/error/additive contracts. Fixture values never
ship as measurements. Outputs go to ignored `runs/frontend/browser`; reviewed
screenshots go to `docs/parallel/screenshots`.

See [the integration handoff](../docs/parallel/frontend-handoff.md) for exact
isolated Python preview commands, evidence limitations, measurements and routes.

## Dependency notices

Three.js 0.180.0 is the only deployed third-party dependency (MIT). Its complete
license is included in the compiled JavaScript banner. esbuild 0.25.10 (MIT)
and Playwright 1.58.0 (Apache-2.0) are development-only. Prettier 3.6.2 (MIT)
formats sources during development. `package-lock.json` pins
transitive dependencies and platform-specific build binaries. The original
licenses remain in their installed npm packages; notices are recorded in
[third-party-notices.md](third-party-notices.md).

No fonts, textures, raster illustrations, third-party branded assets, CDN or
dynamic imports are used. SVG motifs, symbolic markers and teaching paths are
original. The 3D height/dimensions/lights/sensor are explicitly illustrative.

## Focused polish checks

`npm run test:polish` runs two pure progress/chart tests and a small local browser
fixture on loopback 8774 with the production CSP. It never runs inference.
Use `PHANTOMGUARD_CHROMIUM=/absolute/path/to/chromium` when reusing an installed
browser; otherwise Playwright uses its standard browser installation. Screenshots
and fixture reports are saved under ignored `runs/frontend-polish/`.

Theme precedence: explicit localStorage Light/Dark selection, otherwise System,
including live system changes. Denied storage is handled. Illustrations pause
when hidden/offscreen or a central wait is shown, with reduced motion using a
static illustration and manual lesson stepping. Hero perspective uses Canvas;
only the recorded replay uses WebGL. Playback also stops advancing offscreen.

The delayed foreground panel reuses existing status polling (1 s active / 2 s
queued) and generation guards. It reports worker `progress` counts only when
present, supports runtime queue/cooldown fields, and separates result retrieval.
Minimize keeps the same job and a persistent Cancel. Error/reset/cancel stop the
clock and graphics; stale poll/result responses cannot reopen it. Stage changes
are announced, each cycle count update is not. Evidence uses an inline delayed
indicator. The chart reads completed track velocities and supplied header ticks;
its displayed m/s and time retain the configured unit/tick assumptions.
