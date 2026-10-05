import test from "node:test";
import assert from "node:assert/strict";
import { describeProgress } from "../src/loading.mjs";
import { trackSeries } from "../src/chart.mjs";
import { indexResult } from "../src/model.mjs";

test("progress totals and units come only from the worker", () => {
  assert.equal(describeProgress({ state: "queued" }).determinate, false);
  assert.match(
    describeProgress({ state: "queued", cooling_seconds: 7, queue_position: 2 })
      .detail,
    /Queue position: 2/,
  );
  assert.match(
    describeProgress({
      state: "queued",
      runtime: { queue_seconds: 12, cooldown_remaining_seconds: 7 },
    }).detail,
    /Scheduler queue wait: 12.0 s/,
  );
  const p = describeProgress({
    state: "running",
    progress: {
      stage: "detecting",
      completed: 240,
      total: 600,
      unit: "cycles",
    },
  });
  assert.equal(p.determinate, true);
  assert.match(p.detail, /240 \/ 600 cycles/);
  assert.equal(
    describeProgress({ progress: { stage: "planning" } }).determinate,
    false,
  );
  assert.equal(
    describeProgress({ progress: { completed: 1, total: 0 } }).determinate,
    false,
  );
  assert.equal(
    describeProgress({ progress: { completed: 7, total: 6 } }).determinate,
    false,
  );
});
test("track chart uses supplied ticks and velocity, retaining missing values", () => {
  const input = {
    tick_seconds: 0.001,
    cycles: [0, 1, 2].map((i) => ({
      index: i,
      header_timestamp_ticks: 1000 + i * 32,
      objects: [{ frame_index: i, track_id: 1, vx: i === 1 ? null : 3, vy: 4 }],
    })),
  };
  const index = indexResult(input),
    series = trackSeries(index, index.views[0].objects[0].key);
  assert.deepEqual(
    series.map((s) => s.t),
    [0, 0.032, 0.064],
  );
  assert.deepEqual(
    series.map((s) => s.speed),
    [5, null, 5],
  );
  assert.equal(input.cycles[0].objects[0].key, undefined);
  delete index.result.tick_seconds;
  assert.deepEqual(trackSeries(index, index.views[0].objects[0].key), []);
});
