/**
 * Unit-Test der Install-/Deinstall-Aufrufe (WP-P2-4, MARAAA-36).
 *
 * Prueft, dass `api.installWidget` ein `FormData` multipart sendet (kein
 * JSON-Content-Type), `api.installWidgetGit` JSON mit `{url, ref}` und
 * `api.uninstallWidget` ein `DELETE` auf `/api/widgets/{id}` ausloest.
 * Ohne Abhängigkeit: `fetch` wird gestubbt.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { api } from "../js/api.js";

function stubFetch(t, handler) {
  const original = globalThis.fetch;
  globalThis.fetch = handler;
  t.after(() => {
    globalThis.fetch = original;
  });
}

test("installWidget sendet multipart-FormData ohne JSON-Content-Type", async (t) => {
  let seen = null;
  stubFetch(t, async (url, init) => {
    seen = { url, init };
    return new Response(JSON.stringify({ widget: { id: "demo" } }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
  });

  const result = await api.installWidget(new Blob(["x"], { type: "application/zip" }));
  assert.equal(result.widget.id, "demo");
  assert.equal(seen.url, "/api/widgets/install");
  assert.equal(seen.init.method, "POST");
  assert.ok(seen.init.body instanceof FormData);
  assert.ok(seen.init.body.get("file"));
  assert.equal(seen.init.headers["Content-Type"], undefined);
});

test("installWidgetGit sendet JSON mit url und Default-ref main", async (t) => {
  let seen = null;
  stubFetch(t, async (url, init) => {
    seen = { url, init };
    return new Response(JSON.stringify({ widget: { id: "git" } }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
  });

  await api.installWidgetGit("https://example.test/widget.git");
  assert.equal(seen.url, "/api/widgets/install/git");
  assert.equal(seen.init.method, "POST");
  assert.equal(seen.init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(seen.init.body), {
    url: "https://example.test/widget.git",
    ref: "main",
  });
});

test("installWidgetGit respektiert einen abweichenden ref", async (t) => {
  let body = null;
  stubFetch(t, async (_url, init) => {
    body = init.body;
    return new Response(JSON.stringify({ widget: { id: "git" } }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
  });

  await api.installWidgetGit("https://example.test/widget.git", "v2");
  assert.deepEqual(JSON.parse(body), { url: "https://example.test/widget.git", ref: "v2" });
});

test("uninstallWidget ruft DELETE /api/widgets/{id} auf", async (t) => {
  let seen = null;
  stubFetch(t, async (url, init) => {
    seen = { url, init };
    return new Response(null, { status: 204 });
  });

  const result = await api.uninstallWidget("demo widget");
  assert.equal(result, null);
  assert.equal(seen.url, "/api/widgets/demo%20widget");
  assert.equal(seen.init.method, "DELETE");
});
