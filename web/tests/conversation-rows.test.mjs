// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  conversationTitle,
  filterConversations,
  orderByProject,
} from "../src/workbench/conversationRows.ts";

const labelled = (kind) => `kind:${kind}`;

const jobs = [
  { id: "j1", kind: "direct_generation", title: "新建分拣线程序", created_at: "2026-11-09T07:00:00Z", project_id: "p1" },
  { id: "j2", kind: "analysis", title: "  分析一下为什么这个会话中LLM没有选择  ", created_at: "2026-11-09T06:00:00Z", project_id: "p1" },
  { id: "j3", kind: "gx_inspect", title: "", created_at: "2026-11-08T06:00:00Z", project_id: "p2" },
  { id: "j4", kind: "generation", title: null, created_at: "2026-11-08T05:00:00Z", project_id: "p2" },
];

test("the operator's sentence is the row title", () => {
  assert.equal(conversationTitle(jobs[1], labelled),
    "分析一下为什么这个会话中LLM没有选择");
});

test("a job without text falls back to the action it performed", () => {
  assert.equal(conversationTitle(jobs[2], labelled), "kind:gx_inspect");
  assert.equal(conversationTitle(jobs[3], labelled), "kind:generation");
});

test("search matches titles across projects, ignoring case and padding", () => {
  assert.deepEqual(filterConversations(jobs, "分拣线", (job) => conversationTitle(job, labelled))
    .map((job) => job.id), ["j1"]);
  assert.deepEqual(filterConversations(jobs, "  分析 ", (job) => conversationTitle(job, labelled))
    .map((job) => job.id), ["j2"]);
  assert.deepEqual(filterConversations(jobs, "nothing", (job) => conversationTitle(job, labelled)),
    []);
  assert.equal(filterConversations(jobs, "", (job) => conversationTitle(job, labelled)).length, 4);
});

test("search also finds the fallback label of an action", () => {
  assert.deepEqual(filterConversations(jobs, "inspect", (job) => conversationTitle(job, labelled))
    .map((job) => job.id), ["j3"]);
});

test("ordering by project keeps each project together, newest first inside it", () => {
  const ordered = orderByProject(jobs);
  assert.deepEqual(ordered.map((job) => job.id), ["j1", "j2", "j3", "j4"]);
  // The input order must not be mutated for the caller.
  assert.deepEqual(jobs.map((job) => job.id), ["j1", "j2", "j3", "j4"]);
});

test("ordering by project groups a project that appears later in time", () => {
  const interleaved = [
    { id: "a", kind: "analysis", created_at: "2026-11-09T07:00:00Z", project_id: "p1" },
    { id: "b", kind: "analysis", created_at: "2026-11-09T06:00:00Z", project_id: "p2" },
    { id: "c", kind: "analysis", created_at: "2026-11-09T05:00:00Z", project_id: "p1" },
  ];
  assert.deepEqual(orderByProject(interleaved).map((job) => job.id), ["a", "c", "b"]);
});

test("a job with no project still gets a stable bucket", () => {
  const loose = [
    { id: "x", kind: "analysis", created_at: "2026-11-09T07:00:00Z", project_id: null },
    { id: "y", kind: "analysis", created_at: "2026-11-09T08:00:00Z" },
  ];
  assert.deepEqual(orderByProject(loose).map((job) => job.id), ["y", "x"]);
});
