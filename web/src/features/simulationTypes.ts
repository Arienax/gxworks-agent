import type { TraceState } from "./simulatorTimelineData";

export type Value = number | string | boolean | null;
export type Expectation = {
  address: string;
  operator: string;
  value: Value | Value[];
  tolerance?: number;
};
export type Step = {
  id: string;
  at_ms: number;
  set: Record<string, number>;
  expect: Expectation[];
  wait_for: Expectation[];
  timeout_ms?: number;
  poll_ms?: number;
};
export type TestCase = {
  name: string;
  description?: string;
  plc_model: string;
  initial: Record<string, number>;
  steps: Step[];
  sample_ms: number;
  timeout_ms: number;
  trace_devices: string[];
  invariants?: unknown[];
  fault_injections?: unknown[];
  metadata?: Record<string, unknown>;
};
export type Suite = { name: string; plc_model: string; tests: TestCase[] };
export type Plan = {
  binding: { plan_id: string; version_id: string };
  source: string;
  suite: Suite;
  requirement_links: Record<string, string[]>;
  issue_ids: string[];
};
export type RequirementRun = {
  run_id: string;
  test_name: string;
  created_at: string;
  backend_kind: string | null;
  status: string;
  recorded_status: string;
  verification: { status: string; category?: string };
};
export type Requirement = {
  id: string;
  text: string;
  devices: string[];
  status: string;
  latest_run: RequirementRun | null;
  tests: { plan_id: string; test_name: string; latest_run: RequirementRun | null }[];
};
export type EditorMetadata = {
  empty_suite: Suite;
  default_expectation: Expectation;
  default_input_value: number;
  wait_timeout_ms: number;
  poll_ms: number;
  minimum_interval_ms: number;
  maximum_duration_ms: number;
  operators: { value: string; label: string; initial_value: Value; placeholder: string }[];
};
export type Workbench = {
  editor: EditorMetadata;
  version_id: string;
  ir_sha256: string;
  plc_model: string;
  devices: { address: string; label: string; writable: boolean }[];
  plans: Plan[];
  unavailable_plans: { plan_id: string; message: string }[];
  unavailable_runs: string[];
  requirements: Requirement[];
  runs: { run_id: string; suite_name: string; status: string; created_at: string }[];
};
export type Observation = { at_ms: number; event: string; values: Record<string, Value> };
export type ReplayCase = {
  name: string;
  status: string;
  backend_kind: string;
  observations: Observation[];
  duration_ms: number;
  sample_ms: number;
  assertions: {
    passed?: boolean;
    at_ms?: number;
    step_id?: string;
    address?: string;
    actual?: Value;
    expected?: unknown;
    message?: string;
  }[];
  invariant_violations: unknown[];
  requirement_ids: string[];
  issue_ids: string[];
  steps: Step[];
};
export type Replay = {
  run_id: string;
  version_id: string;
  status: string;
  verification: { status: string; category?: string; program_repair_allowed?: boolean };
  cases: ReplayCase[];
  timeline?: TraceState;
};
export type SimulationIssueContext = { id: string; title: string; addresses: string[] };
