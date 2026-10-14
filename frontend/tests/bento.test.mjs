/**
 * Unit-Tests der Bento-Grid-Engine (Spezifikation E.6).
 * Ohne DOM, ohne Abhängigkeiten: `npm test` im Ordner `frontend/`.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { gridPlacement, rectsOverlap } from "../js/bento.js";

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
