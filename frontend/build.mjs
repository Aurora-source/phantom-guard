import { build } from "esbuild";
import { copyFile, stat, readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
const out = new URL("../src/phantomguard/web/static/", import.meta.url);
const license = await readFile(
  new URL("node_modules/three/LICENSE", import.meta.url),
  "utf8",
);
await build({
  entryPoints: ["src/app.mjs"],
  bundle: true,
  format: "iife",
  target: ["es2020"],
  minify: true,
  legalComments: "inline",
  outfile: new URL("app.js", out).pathname.replace(/^\/(\w:)/, "$1"),
  banner: {
    js: `/*! Bundled Three.js 0.180.0\n${license.replaceAll("*/", "* /")}*/`,
  },
  sourcemap: false,
  logLevel: "info",
});
for (const name of ["index.html", "style.css"])
  await copyFile(new URL(`src/${name}`, import.meta.url), new URL(name, out));
for (const name of ["index.html", "app.js", "style.css"]) {
  const data = await readFile(new URL(name, out));
  console.log(
    `${name}: ${(await stat(new URL(name, out))).size} bytes; gzip ${gzipSync(data).length} bytes`,
  );
}
