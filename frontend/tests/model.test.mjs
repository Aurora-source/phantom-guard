import test from "node:test";
import assert from "node:assert/strict";
import {
  indexResult,
  trail,
  alertEnd,
  status,
  correspondence,
  metric,
  percent,
} from "../src/model.mjs";
// Small hand-authored schema fixtures test browser semantics, not detector performance.
const object = (frame, track, extra = {}) => ({
  frame_index: frame,
  track_id: track,
  slot: 7,
  x: frame,
  y: 0,
  vx: 1,
  vy: 0,
  reasons: [],
  scores: {},
  score_status: {},
  flagged: false,
  alert: false,
  ...extra,
});
const cycle = (index, objects, extra = {}) => ({
  index,
  header_frame_index: index * 10,
  header_timestamp_ticks: index * 332,
  cycle_reasons: [],
  cycle_alert: false,
  objects,
  ...extra,
});
const result = (cycles) => ({
  cycles,
  provenance: {},
  request: {},
  tick_seconds: 0.0001,
});
test("trails use track lifetimes; reused numeric slots and unknown tracks never join", () => {
  const a = indexResult(
    result([
      cycle(0, [object(1, 1), object(2, null)]),
      cycle(1, [object(3, 2), object(4, null)]),
      cycle(2, []),
      cycle(3, []),
      cycle(4, [object(9, 1)]),
    ]),
  );
  assert.equal(trail(a, a.views[1].objects[0]).length, 1);
  assert.equal(trail(a, a.views[1].objects[1]).length, 1);
  assert.equal(trail(a, a.views[4].objects[0]).length, 1);
  assert.notEqual(a.views[0].objects[0].key, a.views[4].objects[0].key);
});
test("preindexed history is bounded and never includes a future position", () => {
  const a = indexResult(
    result(Array.from({ length: 50 }, (_, i) => cycle(i, [object(i, 1)]))),
  );
  const t = trail(a, a.views[30].objects[0]);
  assert.equal(t.length, 20);
  assert.equal(t.at(-1).frame_index, 30);
  assert.equal(t[0].frame_index, 11);
});
test("scene warning stays separate; carried alert retains its exact frame", () => {
  const input = result([
    cycle(9, [object(91, 1)], {
      cycle_alert: true,
      cycle_reasons: ["COUNT_MISMATCH"],
    }),
    cycle(10, [object(101, 1, { alert: true })]),
  ]);
  const a = indexResult(input);
  assert.equal(a.alerts[0].object, null);
  assert.equal(a.views[0].objects[0].flagged, false);
  assert.match(status(a.views[1].objects[0]), /persistence carried/);
  assert.equal(a.alerts[1].frame, 101);
  assert.equal(alertEnd(a.alerts, 0), 1);
  assert.equal(input.cycles[1].objects[0].key, undefined);
});
test("synchronization requires declared source correspondence, not emitted frame index", () => {
  const a = indexResult(
      result([
        cycle(0, [], { source_cycle_index: 23 }),
        cycle(1, [], { source_cycle_index: 24 }),
      ]),
    ),
    b = indexResult(
      result([
        cycle(200, [], { source_cycle_index: 24 }),
        cycle(201, [], { source_cycle_index: 23 }),
      ]),
    );
  assert.equal(correspondence(a, b), null);
  for (const x of [a, b])
    x.result.provenance.source_alignment = {
      kind: "source_cycle_index",
      recording_sha256: "test-only-recording",
      segment_lo: 20,
    };
  assert.deepEqual(correspondence(a, b), [1, 0]);
  b.result.provenance.source_alignment.recording_sha256 = "different";
  assert.equal(correspondence(a, b), null);
});
test("zero is a real measurement; unavailable is never zero or a probability", () => {
  assert.equal(metric(""), null);
  assert.equal(metric(null), null);
  assert.equal(metric("0"), 0);
  assert.equal(percent(""), "Unavailable");
  assert.equal(percent("0"), "0.0%");
});
test("malformed/empty results fail explicitly", () => {
  assert.throws(() => indexResult(result([])), /Empty/);
  assert.throws(() => indexResult(result([{ index: 0 }])), /Malformed/);
});
test("final identities distinguish two frames associated with one track in a cycle", () => {
  const a = indexResult(result([cycle(0, [object(1, 1), object(2, 1)])]));
  assert.equal(a.views[0].objects[0].key, a.views[0].objects[1].key);
  assert.notEqual(
    a.views[0].objects[0].identity,
    a.views[0].objects[1].identity,
  );
});
