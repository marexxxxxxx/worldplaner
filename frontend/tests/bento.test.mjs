/**
 * Unit-Tests der Bento-Grid-Engine (Spezifikation E.6).
 * Ohne DOM, ohne Abhängigkeiten: `npm test` im Ordner `frontend/`.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { gridPlacement, rectsOverlap } from "../js/bento.js";
import { isTapGesture, isFocusExcludedTarget, enlargeRowSpan, TAP_MAX_MS } from "../js/bento.js";

test("gridPlacement übersetzt Zellen in CSS-Grid-Angaben (0-basiert -> 1-basiert)", () => {
  assert.deepEqual(gridPlacement({ x: 0, y: 0, w: 12, h: 6 }), {
    gridColumn: "1 / span 12",
    gridRow: "1 / span 6",
  });
  assert.deepEqual(gridPlacement({ x: 6, y: 2, w: 3, h: 2 }), {
    gridColumn: "7 / span 3",
    gridRow: "3 / span 2",
  });
});

test("rectsOverlap erkennt Überlappung, Berührung zählt nicht", () => {
  assert.equal(rectsOverlap({ x: 0, y: 0, w: 2, h: 2 }, { x: 1, y: 1, w: 2, h: 2 }), true);
  assert.equal(rectsOverlap({ x: 0, y: 0, w: 2, h: 2 }, { x: 2, y: 0, w: 2, h: 2 }), false);
  assert.equal(rectsOverlap({ x: 0, y: 0, w: 2, h: 2 }, { x: 0, y: 2, w: 2, h: 2 }), false);
  assert.equal(rectsOverlap({ x: 0, y: 0, w: 4, h: 4 }, { x: 1, y: 1, w: 1, h: 1 }), true);
});

// --- Fokusmodus: Tap-Erkennung (spec-focus F, Akzeptanz 1/4) ---------------

test("isTapGesture: <= 250 ms und < 10 px ist ein Tap", () => {
  assert.equal(TAP_MAX_MS, 250);
  assert.equal(isTapGesture({ t: 0, x: 0, y: 0 }, { t: 200, x: 3, y: 4 }), true);
  // Grenze: genau 250 ms zählt noch, 251 ms nicht mehr (Langdruck bleibt Drag).
  assert.equal(isTapGesture({ t: 100, x: 10, y: 10 }, { t: 350, x: 10, y: 10 }), true);
  assert.equal(isTapGesture({ t: 100, x: 10, y: 10 }, { t: 351, x: 10, y: 10 }), false);
});

test("isTapGesture: >= 10 px Bewegung bricht den Tap ab", () => {
  assert.equal(isTapGesture({ t: 0, x: 0, y: 0 }, { t: 100, x: 9.9, y: 0 }), true);
  assert.equal(isTapGesture({ t: 0, x: 0, y: 0 }, { t: 100, x: 10, y: 0 }), false);
  assert.equal(isTapGesture({ t: 0, x: 0, y: 0 }, { t: 100, x: 6, y: 9 }), false);
  assert.equal(isTapGesture(null, { t: 100, x: 0, y: 0 }), false);
});

test("isFocusExcludedTarget: Kopfzeile, Griff, data-no-focus und Interaktives", () => {
  const target = (markers) => ({
    closest: (selector) =>
      markers.some((marker) => selector.includes(marker)) ? {} : null,
  });
  assert.equal(isFocusExcludedTarget(target([])), false);
  assert.equal(isFocusExcludedTarget(target([".bento-item__header"])), true);
  assert.equal(isFocusExcludedTarget(target([".bento-item__handle"])), true);
  assert.equal(isFocusExcludedTarget(target(["[data-no-focus]"])), true);
  assert.equal(isFocusExcludedTarget(target(["button"])), true);
  assert.equal(isFocusExcludedTarget(null), true);
});

test("enlargeRowSpan: span max(3, aktuelle Höhe)", () => {
  assert.equal(enlargeRowSpan(1), 3);
  assert.equal(enlargeRowSpan(2), 3);
  assert.equal(enlargeRowSpan(3), 3);
  assert.equal(enlargeRowSpan(5), 5);
  assert.equal(enlargeRowSpan("4"), 4);
  assert.equal(enlargeRowSpan(undefined), 3);
});
