// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import assert from "node:assert/strict";
import test from "node:test";
import {
  childOf, isRootCrumb, jumpTarget, pickTarget, visibleEntries,
} from "../src/features/workspaceBrowse.ts";

const listing = {
  path: "C:\\work",
  home: "C:\\Users\\me",
  crumbs: [{ name: "C:\\", path: "C:\\" }, { name: "work", path: "C:\\work" }],
  entries: [
    { name: "alpha", path: "C:\\work\\alpha", hidden: false },
    { name: "Beta", path: "C:\\work\\Beta", hidden: false },
    { name: ".cache", path: "C:\\work\\.cache", hidden: true },
  ],
  truncated: false,
};

test("hidden entries stay listed by the service and are filtered by the switch", () => {
  assert.deepEqual(visibleEntries(listing).map((row) => row.name), ["alpha", "Beta"]);
  assert.deepEqual(visibleEntries(listing, { showHidden: true }).map((row) => row.name),
    ["alpha", "Beta", ".cache"]);
});

test("the filter matches the start of the name, ignoring case", () => {
  assert.deepEqual(visibleEntries(listing, { query: "be" }).map((row) => row.name), ["Beta"]);
  assert.deepEqual(visibleEntries(listing, { query: "  " }).map((row) => row.name), ["alpha", "Beta"]);
  assert.deepEqual(visibleEntries(listing, { query: "zzz" }), []);
});

test("an empty listing yields no rows instead of throwing", () => {
  assert.deepEqual(visibleEntries(undefined), []);
  assert.deepEqual(visibleEntries({ ...listing, entries: [] }), []);
});

test("open adopts the selection, and the listed level when nothing is selected", () => {
  assert.equal(pickTarget(listing, "C:\\work\\alpha"), "C:\\work\\alpha");
  assert.equal(pickTarget(listing, null), "C:\\work");
  assert.equal(pickTarget(listing, undefined), "C:\\work");
});

test("a selection from another level is ignored rather than adopted", () => {
  assert.equal(pickTarget(listing, "C:\\elsewhere"), "C:\\work");
});

test("open with no listing adopts nothing", () => {
  assert.equal(pickTarget(undefined, "C:\\work"), "");
});

test("the root crumb is labelled by its own path", () => {
  assert.equal(isRootCrumb(listing.crumbs[0]), true);
  assert.equal(isRootCrumb(listing.crumbs[1]), false);
});

test("a typed path drops trailing separators and empty drafts mean home", () => {
  assert.equal(jumpTarget("C:\\work\\"), "C:\\work");
  assert.equal(jumpTarget("  C:\\work  "), "C:\\work");
  assert.equal(jumpTarget("/home/me/"), "/home/me");
  assert.equal(jumpTarget(""), undefined);
  assert.equal(jumpTarget("   "), undefined);
  assert.equal(jumpTarget("\\\\server\\share\\"), "\\\\server\\share");
});

test("a path counts as a child only while it belongs to the listed level", () => {
  assert.equal(childOf(listing, "C:\\work\\alpha"), true);
  assert.equal(childOf(listing, "C:\\work\\nope"), false);
  assert.equal(childOf(undefined, "C:\\work\\alpha"), false);
});
