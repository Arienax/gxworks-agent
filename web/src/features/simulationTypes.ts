// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { ApiSchema } from "../api/client";
export type Value = number | string | boolean | null;
export type Expectation = ApiSchema["Expectation"];
export type Step = ApiSchema["SimulationStep"];
export type TestCase = ApiSchema["SimulationTest"];
export type Suite = ApiSchema["SimulationSuite"];
export type Plan = ApiSchema["SimulationPlan"];
export type RequirementRun = ApiSchema["RequirementRun"];
export type Requirement = ApiSchema["Requirement"];
export type EditorMetadata = ApiSchema["SimulationEditor"];
export type Workbench = ApiSchema["SimulationWorkbench"];
export type Observation = ApiSchema["Observation"];
export type ReplayCase = ApiSchema["ReplayCase"];
export type Replay = ApiSchema["SimulationReplay"];
export type SimulationIssueContext = { id: string; title: string; addresses: string[] };
