import test from "node:test";
import assert from "node:assert/strict";
import { buildHeatmap, intensity } from "../public/heatmap-data.mjs";

test("uses UTC date rows, aggregates equal hours, and leaves future hours empty", () => {
  const now = new Date("2026-09-06T10:30:00Z");
  const at = Date.parse("2026-09-05T23:00:00Z") / 1000;
  const result = buildHeatmap(
    [
      { at, tokens: 100, records: 1 },
      { at: at + 30, tokens: 50, records: 2 },
      { at: at + 86400, tokens: 999, records: 1 },
    ],
    2,
    now,
  );
  assert.equal(result.rows.length, 2);
  assert.equal(result.rows[0].date, "2026-09-05");
  assert.equal(result.rows[0].cells.length, 24);
  assert.equal(result.rows[0].cells[23].tokens, 150);
  assert.equal(result.rows[1].cells[11].future, true);
  assert.equal(result.rows[1].cells[10].current, true);
  assert.equal(result.sum, 150);
  assert.equal(result.records, 3);
});

test("excludes records outside range and handles empty and invalid data", () => {
  const result = buildHeatmap(
    [
      { at: 1, tokens: 1 },
      { at: 1, tokens: NaN },
      { at: 1, tokens: -20 },
    ],
    7,
    new Date("2026-09-06T10:00Z"),
  );
  assert.equal(result.sum, 0);
  assert.equal(result.peak, 0);
  assert.equal(result.rows.length, 7);
  assert.equal(intensity(0, 0), 0);
  assert.equal(intensity(1, 10000), 1);
  assert.equal(intensity(10000, 10000), 5);
});
