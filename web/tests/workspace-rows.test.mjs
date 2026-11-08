// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  flattenProjects,
  matchesProject,
  otherWorkspaces,
  workspaceExpanded,
} from "../src/workbench/workspaceRows.ts";

const groups = [
  {
    id: "a", path: "C:/line-a", label: "line-a", active: true,
    projects: [{ id: "p1", name: "分拣线", plc_model: "FX3U", target_mode: "ladder" },
               { id: "p2", name: "码垛", plc_model: "FX3U", target_mode: "fbd" }],
  },
  {
    id: "b", path: "C:/line-b", label: "line-b", active: false,
    projects: [{ id: "q1", name: "装箱线", plc_model: "FX5U", target_mode: "ladder" }],
  },
];

test("a flat list covers every workspace and keeps the owner on each row", () => {
  const rows = flattenProjects(groups);
  assert.deepEqual(rows.map((row) => row.project.name), ["分拣线", "码垛", "装箱线"]);
  assert.deepEqual(rows.map((row) => row.group.label), ["line-a", "line-a", "line-b"]);
});

test("row keys stay unique when two workspaces reuse a project id", () => {
  const twin = [
    { id: "a", path: "C:/a", label: "a", active: true, projects: [{ id: "same", name: "one" }] },
    { id: "b", path: "C:/b", label: "b", active: false, projects: [{ id: "same", name: "two" }] },
  ];
  const keys = flattenProjects(twin).map((row) => row.key);
  assert.equal(new Set(keys).size, 2);
});

test("only the active workspace's shown project is current", () => {
  const rows = flattenProjects(groups, "", "p1");
  assert.deepEqual(rows.map((row) => row.current), [true, false, false]);
  // A project id from an inactive workspace never becomes current by accident.
  assert.deepEqual(flattenProjects(groups, "", "q1").map((row) => row.current),
    [false, false, false]);
});

test("search filters by project name across workspaces, ignoring case", () => {
  assert.deepEqual(flattenProjects(groups, "线").map((row) => row.project.name),
    ["分拣线", "装箱线"]);
  assert.deepEqual(flattenProjects(groups, "  码垛 ").map((row) => row.project.name), ["码垛"]);
  assert.deepEqual(flattenProjects(groups, "nothing").map((row) => row.project.name), []);
  assert.deepEqual(flattenProjects(groups, "").length, 3);
});

test("a nameless project is kept by an empty query and dropped by a real one", () => {
  assert.equal(matchesProject({ name: null }, ""), true);
  assert.equal(matchesProject({ name: null }, "x"), false);
});

test("the active workspace is always open and others follow the fold state", () => {
  assert.equal(workspaceExpanded(groups[0], {}), true);
  assert.equal(workspaceExpanded(groups[1], {}), false);
  assert.equal(workspaceExpanded(groups[1], { b: true }), true);
});

test("other workspaces are listed above the active project tree", () => {
  assert.deepEqual(otherWorkspaces(groups).map((group) => group.label), ["line-b"]);
  assert.deepEqual(otherWorkspaces([groups[1]]).map((group) => group.label), ["line-b"]);
});
