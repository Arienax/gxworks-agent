// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { Step, Suite, TestCase } from "./simulationTypes";

/** Copy only the edited branch; saved drafts and other test cases stay immutable. */
export function updateTest(
  suite: Suite,
  index: number,
  change: (test: TestCase) => TestCase,
): Suite {
  return { ...suite, tests: suite.tests.map((test, i) => (i === index ? change(test) : test)) };
}

export function updateStep(test: TestCase, index: number, change: (step: Step) => Step): TestCase {
  return { ...test, steps: test.steps.map((step, i) => (i === index ? change(step) : step)) };
}

export function reorderStep(test: TestCase, index: number, offset: number): TestCase {
  const target = index + offset;
  if (!test.steps[index] || !test.steps[target]) return test;
  const steps = [...test.steps];
  [steps[index], steps[target]] = [steps[target], steps[index]];
  return { ...test, steps };
}
