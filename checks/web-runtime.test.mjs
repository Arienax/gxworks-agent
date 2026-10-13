import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { defaultWorkspace, safeEntry } from "../tools/web-runtime.mjs";

test("default projects stay in user AppData, independent of the checkout", () => {
  const local = path.resolve("test-local-appdata");
  assert.equal(defaultWorkspace({ LOCALAPPDATA: local }), path.join(local, "GXWorks Agent", "web-workspace"));
});

test("archive extraction rejects escapes, alternate streams and symlinks", () => {
  const base = path.resolve("temporary-stage");
  for (const entry of ["../secret", "/absolute", "GXWorks-Agent-Web/../secret", "GXWorks-Agent-Web/file:stream",
    "GXWorks-Agent-Web/dir\\escape", "GXWorks-Agent-Web/CON.txt", "GXWorks-Agent-Web/name."]) {
    assert.throws(() => safeEntry(base, entry), /Unsafe/);
  }
  assert.throws(() => safeEntry(base, "GXWorks-Agent-Web/link", 0o120777 << 16), /Unsafe/);
  assert.equal(safeEntry(base, "GXWorks-Agent-Web/GXWorks-Agent-Web.exe"), path.join(base, "GXWorks-Agent-Web/GXWorks-Agent-Web.exe"));
});
