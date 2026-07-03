// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"use strict";
/*
 * Node test for the panel's pure form logic (static/panel.schema.js).
 * No DOM, no network — asserts schema→descriptor generation and the
 * change-collection semantics the design leans on (schema-driven forms,
 * secret-left-blank = no change, type coercion, unchanged = dropped).
 *
 * Run standalone: `node packages/rtime-admin-api/tests/panel_schema.test.js`
 * Also driven by pytest (tests/test_panel_schema_logic.py) so it joins the gate.
 */
const assert = require("assert");
const path = require("path");

const S = require(path.join(
  __dirname, "..", "src", "rtime_admin_api", "static", "panel.schema.js"
));

// A schema shaped like GET /v1/schema returns (modules -> properties w/ x-* meta).
const SCHEMA = {
  modules: {
    models: {
      properties: {
        default_model: { type: "string", enum: ["claude", "ds", "kimi"], "x-scope": "write:models", "x-reload": "hot" },
        ustc_api_key: { anyOf: [{ type: "string" }, { type: "null" }], "x-secret": true, "x-reload": "hot" },
        max_turns: { type: "integer", "x-reload": "restart" },
      },
    },
    "channel-common": {
      properties: {
        read_only: { type: "boolean", "x-reload": "restart" },
        allowed: { type: "array", "x-reload": "hot" },
      },
    },
  },
};

// provenance=1 shape: {path: {value, provenance}}
const VALUES = {
  "models.default_model": { value: "claude", provenance: "default" },
  "models.ustc_api_key": { value: "***", provenance: "store" },
  "models.max_turns": { value: 5, provenance: "env" },
  "channel-common.read_only": { value: true, provenance: "profile" },
  "channel-common.allowed": { value: ["12345"], provenance: "store" },
};

let passed = 0;
function ok(name) { passed++; }

// --- fieldType ------------------------------------------------------------
assert.strictEqual(S.fieldType({ type: "integer" }), "integer");
assert.strictEqual(S.fieldType({ enum: ["a"] }), "enum");
assert.strictEqual(S.fieldType({ anyOf: [{ type: "string" }, { type: "null" }] }), "string");
assert.strictEqual(S.fieldType({}), "string");
ok("fieldType");

// --- fieldMeta ------------------------------------------------------------
const meta = S.fieldMeta(SCHEMA, "models.ustc_api_key");
assert.strictEqual(meta.secret, true);
assert.strictEqual(meta.reload, "hot");
const meta2 = S.fieldMeta(SCHEMA, "models.default_model");
assert.strictEqual(meta2.scope, "write:models");
assert.strictEqual(meta2.secret, false);
ok("fieldMeta");

// --- fieldDescriptors: 100% from schema, grouped, secret has null orig ----
const groups = S.fieldDescriptors(SCHEMA, VALUES);
assert.strictEqual(groups.length, 2);
// sorted module order
assert.strictEqual(groups[0].module, "channel-common");
assert.strictEqual(groups[1].module, "models");
const byPath = {};
groups.forEach((g) => g.fields.forEach((f) => { byPath[f.path] = f; }));
// every schema field became a descriptor (no hand-coding)
assert.deepStrictEqual(
  Object.keys(byPath).sort(),
  ["channel-common.allowed", "channel-common.read_only",
   "models.default_model", "models.max_turns", "models.ustc_api_key"]
);
// secret never exposes its current value as orig (form shows ***, blank = no change)
assert.strictEqual(byPath["models.ustc_api_key"].secret, true);
assert.strictEqual(byPath["models.ustc_api_key"].orig, null);
// non-secret carries its current value as orig for change detection
assert.strictEqual(byPath["models.max_turns"].orig, 5);
assert.strictEqual(byPath["models.default_model"].enum.length, 3);
ok("fieldDescriptors");

// --- coerce ---------------------------------------------------------------
assert.strictEqual(S.coerce("", "string"), null);
assert.strictEqual(S.coerce("5", "integer"), 5);
assert.strictEqual(S.coerce("2.5", "number"), 2.5);
assert.strictEqual(S.coerce("true", "boolean"), true);
assert.deepStrictEqual(S.coerce('["a"]', "array"), ["a"]);
ok("coerce");

// --- collectChangesFrom: the heart of diff/apply --------------------------
// unchanged non-secret dropped; changed kept; secret blank dropped; secret set kept
const readings = [
  { path: "models.default_model", type: "enum", secret: false, raw: "ds", orig: "claude" },   // changed
  { path: "models.max_turns", type: "integer", secret: false, raw: "5", orig: 5 },             // unchanged (str vs int)
  { path: "channel-common.read_only", type: "boolean", secret: false, raw: "true", orig: true },// unchanged
  { path: "channel-common.allowed", type: "array", secret: false, raw: '["9"]', orig: ["12345"] }, // changed
  { path: "models.ustc_api_key", type: "string", secret: true, raw: "", orig: null },           // secret blank -> skip
];
const changes = S.collectChangesFrom(readings);
assert.deepStrictEqual(changes, { "models.default_model": "ds", "channel-common.allowed": ["9"] });
ok("collectChangesFrom drops unchanged + blank secret");

// secret set -> submitted as raw string (backend validates)
const withSecret = S.collectChangesFrom([
  { path: "models.ustc_api_key", type: "string", secret: true, raw: "sk-new", orig: null },
]);
assert.deepStrictEqual(withSecret, { "models.ustc_api_key": "sk-new" });
ok("collectChangesFrom keeps set secret");

console.log(`panel_schema.test.js: ${passed} assertions groups passed`);
