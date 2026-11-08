// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import assert from "node:assert/strict";
import test from "node:test";
import { deviceFamily, groupDeviceComments } from "../src/features/deviceGroups.ts";

const comments = (pairs) => pairs.map(([address, comment]) => [address, { comment }]);

test("the device family is the leading letters, uppercased", () => {
  assert.equal(deviceFamily("X12"), "X");
  assert.equal(deviceFamily("y1f"), "Y");
  assert.equal(deviceFamily("M8000"), "M");
  assert.equal(deviceFamily("D100"), "D");
  assert.equal(deviceFamily("T7"), "T");
  assert.equal(deviceFamily("SM400"), "SM");
  assert.equal(deviceFamily("SD210"), "SD");
  assert.equal(deviceFamily("ZR0"), "ZR");
});

test("an address without letters has no family", () => {
  assert.equal(deviceFamily(""), "");
  assert.equal(deviceFamily("12"), "");
  assert.equal(deviceFamily(undefined), "");
});

test("families follow the reading order, not the insertion order", () => {
  const groups = groupDeviceComments(comments([["D10", "参数"], ["M0", "启动"],
    ["Y0", "输送带"], ["X1", "按钮"], ["T0", "延时"]]));
  assert.deepEqual(groups.map((group) => group.family), ["X", "Y", "M", "T", "D"]);
  assert.deepEqual(groups.map((group) => group.count), [1, 1, 1, 1, 1]);
});

test("a family keeps the caller's address order and its own label", () => {
  const groups = groupDeviceComments(comments([["M10", "c"], ["M2", "a"], ["M7", "b"]]));
  assert.equal(groups.length, 1);
  assert.equal(groups[0].family, "M");
  assert.equal(groups[0].label, "内部继电器");
  assert.deepEqual(groups[0].entries.map(([address]) => address), ["M10", "M2", "M7"]);
});

test("an unknown prefix stays visible under its own name", () => {
  const groups = groupDeviceComments(comments([["QQ1", "扩展"], ["X0", "输入"]]));
  assert.deepEqual(groups.map((group) => group.family), ["X", "QQ"]);
  assert.equal(groups[1].label, "QQ");
  assert.equal(groups[1].count, 1);
});

test("known families come before unknown ones even when unknown sorts earlier", () => {
  const groups = groupDeviceComments(comments([["AA0", "x"], ["X0", "y"]]));
  assert.deepEqual(groups.map((group) => group.family), ["X", "AA"]);
});

test("empty input produces no groups", () => {
  assert.deepEqual(groupDeviceComments([]), []);
});

test("malformed entries are skipped instead of crashing the panel", () => {
  const groups = groupDeviceComments([["M0", { comment: "ok" }], null, ["", { comment: "x" }]]);
  assert.deepEqual(groups.map((group) => group.family), ["M", ""]);
  assert.equal(groups[0].count, 1);
});
