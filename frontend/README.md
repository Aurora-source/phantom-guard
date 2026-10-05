# Phantom Guard browser

Edit `src/index.html`, `src/style.css` and the JavaScript modules in `src/`.
Build-time dependencies are isolated in this directory. Production requires
neither Node nor a graphics server. The Python package serves the compiled
`../src/phantomguard/web/static/{index.html,app.js,style.css}`.

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
and Playwright 1.58.0 (Apache-2.0) are development-only. `package-lock.json` pins
transitive dependencies and platform-specific build binaries. The original
licenses remain in their installed npm packages; notices are recorded in
[third-party-notices.md](third-party-notices.md).

No fonts, textures, raster illustrations, third-party branded assets, CDN or
dynamic imports are used. SVG motifs, symbolic markers and teaching paths are
original. The 3D height/dimensions/lights/sensor are explicitly illustrative.
