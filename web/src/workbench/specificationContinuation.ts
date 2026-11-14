export type AnalysisRequest = { versionId: string | null; program?: string; text: string; generationAction?: "edit" | "regenerate"; conversationId?: string | null };

/** Continue the approved analysis against its original version and program. */
export function specificationContinuation(request: AnalysisRequest) {
  const conversation = request.conversationId ? { conversation_id: request.conversationId } : {};
  if (!request.versionId) return conversation;
  return {
    ...conversation,
    generation_action: request.generationAction === "regenerate" ? "regenerate" : "edit",
    version_id: request.versionId,
    ...(request.program ? { program: request.program } : {}),
    ...(request.generationAction !== "regenerate" ? { text: request.text || "请按本次已确认的需求变更修改选中的原程序。" } : {}),
  };
}
