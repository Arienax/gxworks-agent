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
    generationAction: undefined, submitLabel: "提问", placeholder: "询问程序行为、工程状态或手册用法…",
    hint: "只查询工程、程序和手册，不修改程序或执行外部操作。",
  };
  if (input.task === "edit" && input.editAction === "edit") return {
    kind: "generation" as const, available: input.hasVersions, requiresText: true,
    generationAction: "edit" as const, submitLabel: "修改当前程序", placeholder: "描述希望修改的行为和需要保留的内容…",
    hint: "修改基于当前选定版本；通过检查后保存新版本，可查看差异和回退。",
  };
  const regenerating = input.task === "edit";
  const available = regenerating ? input.hasVersions && input.targetMode === "ladder" : !input.hasVersions;
  if (!regenerating && input.workflow === "direct" && input.targetMode === "ladder") return {
    kind: "direct_generation" as const, available, requiresText: true,
    generationAction: undefined,
    submitLabel: input.continuingDirect ? "补充并继续" : "生成程序",
    placeholder: input.continuingDirect ? "补充上面的问题…" : "描述输入、输出和控制行为…",
    hint: "信息充分时直接生成程序；缺少必要事实时先提问。",
  };
  if (!input.hasSpec || input.reviewRequested) return {
    kind: "analysis" as const, available, requiresText: true,
    generationAction: undefined, submitLabel: "分析并核对规格", placeholder: "描述控制需求，下一步核对方案、I/O 和参数…",
    hint: "分析需求 → 核对并确认规格 → 生成程序。",
  };
  return {
    kind: "generation" as const, available, requiresText: false,
    generationAction: regenerating ? "regenerate" as const : undefined,
    submitLabel: regenerating ? "按规格重新生成" : "按规格生成",
    placeholder: "规格已确认，可直接生成；需要改变行为时先重新核对规格。",
    hint: regenerating ? "按已确认规格生成完整新版本，保留历史版本。" : "规格已确认，生成将使用已保存的规格。",
  };
}
