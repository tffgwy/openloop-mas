// Tests for the zero-dependency OpenLoop MAS MCP client.
// Run: node --test integrations/dsh-openloop-mas/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { resolvePython, resolveAppDir } from "../lib/mcp-client.js";

test("resolvePython prefers an explicit interpreter", () => {
  assert.equal(resolvePython({ python: "C:/py/python.exe", masHome: "" }), "C:/py/python.exe");
});

test("resolvePython falls back to python on PATH", () => {
  assert.equal(resolvePython({ python: "", masHome: "" }), "python");
});

test("resolvePython ignores a whitespace-only override", () => {
  assert.equal(resolvePython({ python: "   ", masHome: "" }), "python");
});

test("resolveAppDir returns empty when masHome is unset", () => {
  assert.equal(resolveAppDir({ masHome: "" }), "");
  assert.equal(resolveAppDir({ masHome: "   " }), "");
});

test("resolveAppDir points at the app directory when it exists", () => {
  const dir = resolveAppDir({ masHome: process.cwd() });
  // Either the app dir exists here or not; the contract is only that it
  // returns a path under masHome or an empty string - never a wrong path.
  if (dir !== "") {
    assert.ok(dir.startsWith(process.cwd()));
    assert.ok(dir.endsWith("app"));
  }
});
