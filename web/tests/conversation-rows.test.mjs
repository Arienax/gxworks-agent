// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import assert from "node:assert/strict";
import { test } from "node:test";

import { conversationTokenUsage } from "../src/features/conversationUsage.ts";
import {
  conversationRows,
  conversationTitle,
  filterConversations,
  orderByProject,
  workspaceRows,
} from "../src/workbench/conversationRows.ts";

const labelled = (kind) => `kind:${kind}`;

test("a generated version stays in the originating conversation and GX reads stay in jobs", () => {
  const root = { id: "analysis-1", kind: "analysis", title: "分拣需求", conversation_id: "analysis-1" };
  const generated = { id: "generated-1", kind: "generation", conversation_id: root.id, title: "生成候选" };
  const read = { id: "read-1", kind: "gx_read" };
  const standaloneGeneration = { id: "generated-2", kind: "generation", conversation_id: null };
  const opened = { id: "manual-1", kind: "agent", title: "新对话", conversation_id: "manual-1" };
  assert.deepEqual(conversationRows([read, standaloneGeneration, generated, opened, root]), [
    generated, opened,
  ]);
});

test("a saved project shows before its first conversation and keeps one row after it starts", () => {
  const fresh = { id: "p2", name: "New project", created_at: "2026-11-09T07:00:00Z" };
  const projects = [fresh, { id: "p1", name: "Existing project" }];
  const existing = { id: "c1", kind: "agent", project_id: "p1", conversation_id: "c1", created_at: "2026-11-09T06:00:00Z" };
  const read = { id: "read", kind: "gx_read", project_id: "p2" };
  const first = { id: "c2", kind: "agent", project_id: "p2", conversation_id: "c2", created_at: "2026-11-09T08:00:00Z" };

  assert.deepEqual(workspaceRows([read, existing], projects), [{ ...fresh, project_id: "p2" }, existing]);
  assert.deepEqual(workspaceRows([first, read, existing], projects), [first, existing]);
  assert.deepEqual(workspaceRows([first, read, existing], [projects[1]]), [existing]);
});

const meteredJob = (input, output, cached, fields = {}) => ({
  project_id: "p1",
  conversation_id: "c1",
  model_usage: { tokens: { input_tokens: input, output_tokens: output, cached_input_tokens: cached } },
  ...fields,
});

test("conversation usage includes all metered jobs across versions, including failed validation", () => {
  const jobs = [
    meteredJob(400, 40, 320, { kind: "generation", version_id: "v0002", status: "failed" }),
    meteredJob(200, 20, 100, { kind: "generation", version_id: "v0001" }),
    meteredJob(100, 10, 0, { kind: "analysis" }),
    meteredJob(800, 80, 800, { conversation_id: "c2" }),
    meteredJob(900, 90, 900, { project_id: "p2" }),
    { project_id: "p1", conversation_id: "c1", kind: "execution" },
  ];
  assert.deepEqual(conversationTokenUsage(jobs, "p1", "c1"), { input: 700, output: 70, cached: 420 });
  assert.equal(conversationTokenUsage(jobs, "p1", ""), null);
});

test("unreported cache counts leave the rate unknown without losing input or output totals", () => {
  const jobs = [meteredJob(100, 10, 50), meteredJob(50, 5)];
  assert.deepEqual(conversationTokenUsage(jobs, "p1", "c1"), { input: 150, output: 15, cached: undefined });
  assert.equal(conversationTokenUsage([meteredJob(0, 0, 0)], "p1", "c1"), null);
});

const jobs = [
  { id: "j1", kind: "direct_generation", title: "新建分拣线程序", created_at: "2026-11-09T07:00:00Z", project_id: "p1" },
  { id: "j2", kind: "analysis", title: "  分析一下为什么这个会话中LLM没有选择  ", created_at: "2026-11-09T06:00:00Z", project_id: "p1" },
  { id: "j3", kind: "gx_inspect", title: "", created_at: "2026-11-08T06:00:00Z", project_id: "p2" },
  { id: "j4", kind: "generation", title: null, created_at: "2026-11-08T05:00:00Z", project_id: "p2" },
];

const projectNames = new Map([["p1", "分拣线 A"], ["p2", "QC 测试台"]]);
const titleOf = (job) => conversationTitle(job, projectNames, labelled);

test("the project name is the row title for every conversation of that project", () => {
  assert.equal(titleOf(jobs[0]), "分拣线 A");
  assert.equal(titleOf(jobs[1]), "分拣线 A");
  assert.equal(titleOf(jobs[2]), "QC 测试台");
});

test("a conversation without a project name falls back to its job kind", () => {
  assert.equal(conversationTitle(jobs[1], new Map(), labelled), "kind:analysis");
});

test("search matches project names, ignoring case and padding", () => {
  assert.deepEqual(filterConversations(jobs, "分拣线", titleOf).map((job) => job.id), ["j1", "j2"]);
  assert.deepEqual(filterConversations(jobs, "  qc ", titleOf).map((job) => job.id), ["j3", "j4"]);
  assert.deepEqual(filterConversations(jobs, "分析一下", titleOf), []);
  assert.equal(filterConversations(jobs, "", titleOf).length, 4);
});

test("search also finds the fallback label of an action", () => {
  assert.deepEqual(filterConversations(jobs, "inspect", (job) => conversationTitle(job, new Map(), labelled))
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
