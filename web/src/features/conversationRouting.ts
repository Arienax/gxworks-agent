// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { components } from "../api/generated";

export type TaskPurpose = "create" | "edit" | "question";
export type CreationWorkflow = "direct" | "review";
export type GenerationAction = NonNullable<components["schemas"]["JobCreate"]["generation_action"]>;

export function conversationRoute(input: {
  task: TaskPurpose;
  workflow: CreationWorkflow;
  editAction: GenerationAction;
  targetMode: string;
  hasVersions: boolean;
  hasSpec: boolean;
  reviewRequested: boolean;
  continuingDirect: boolean;
}) {
  if (input.task === "question") return {
    kind: "agent" as const, available: true, requiresText: true,
    generationAction: undefined, submitLabel: "提问", placeholder: "输入你的问题…",
  };
  if (input.task === "edit" && input.editAction === "edit") return {
    kind: "generation" as const, available: input.hasVersions, requiresText: true,
    generationAction: "edit" as const, submitLabel: "修改当前程序", placeholder: "描述你想修改的功能…",
  };
  const regenerating = input.task === "edit";
  const available = regenerating ? input.hasVersions && input.targetMode === "ladder" : !input.hasVersions;
  if (!regenerating && input.workflow === "direct" && input.targetMode === "ladder") return {
    kind: "direct_generation" as const, available, requiresText: true,
    generationAction: undefined,
    submitLabel: input.continuingDirect ? "补充并继续" : "生成程序",
    placeholder: input.continuingDirect ? "补充上面的问题…" : "描述你希望设备如何工作…",
  };
  if (!input.hasSpec || input.reviewRequested) return {
    kind: "analysis" as const, available, requiresText: true,
    generationAction: undefined, submitLabel: "整理并确认需求", placeholder: "描述你希望设备如何工作…",
  };
  return {
    kind: "generation" as const, available, requiresText: false,
    generationAction: regenerating ? "regenerate" as const : undefined,
    submitLabel: regenerating ? "按需求重新生成" : "按需求生成",
    placeholder: "需求已确认",
  };
}
