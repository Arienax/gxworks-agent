// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import type { SetStateAction } from "react";
import { useEffect, useRef, useState } from "react";
import type { Project } from "../api/client";
import { api } from "../api/client";
import type { AnalysisMode } from "../features/AnalysisModePicker";
import type {
  CreationWorkflow,
  GenerationAction,
  TaskPurpose,
} from "../features/conversationRouting";

export function useComposer(pid: string, vid: string, workspacePath: string | null | undefined, t: (value: string) => string = (value) => value) {
  const [directAnswers, setDirectAnswers] = useState<Record<string, Record<string, string>>>({});
  const [ignoredClarification, setIgnoredClarification] = useState("");
  const scope = JSON.stringify([workspacePath, pid]);
  const activeProjectRef = useRef(scope);
  activeProjectRef.current = scope;
  const [scopeEnabled, setScopeEnabled] = useState(false);
  const [scopeNetworks, setScopeNetworks] = useState("");
  const [scopeAddresses, setScopeAddresses] = useState("");
  const [task, setTask] = useState<TaskPurpose>("create");
  const [workflow, setWorkflow] = useState<CreationWorkflow>("review");
  const [editAction, setEditAction] = useState<GenerationAction>("edit");
  const [reviewRequested, setReviewRequested] = useState(false);
  const [drafts, setDrafts] = useState<Record<TaskPurpose, string>>({
    create: "",
    edit: "",
    question: "",
  });
  const text = drafts[task];
  const setText = (value: SetStateAction<string>) =>
    setDrafts((before) => ({
      ...before,
      [task]: typeof value === "function" ? value(before[task]) : value,
    }));
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("direct");
  useEffect(() => {
    setAnalysisMode("direct");
  }, [scope]);
  const [attachments, setAttachments] = useState<{ attachment_id: string; filename: string }[]>([]);
  const composerProjectRef = useRef("");
  function syncComposerRoute(value: Project) {
    const editRegenerate = (value.version_count || 0) > 0 || (value.versions?.length || 0) > 0;
    const routeKey = `${scope}:${editRegenerate ? "edit-regenerate" : value.confirmed_spec ? "confirmed" : "new-requirement"}`;
    if (composerProjectRef.current === routeKey) return;
    composerProjectRef.current = routeKey;
    setTask(editRegenerate ? "edit" : "create");
    setWorkflow("review");
    setEditAction("edit");
    setReviewRequested(false);
  }
  async function upload(files: FileList | null) {
    if (!files) return;
    if (Array.from(files).some((file) => /\.gxw$/i.test(file.name)))
      throw new Error(t("GXW 工程请使用“导入 GXW”，不能作为图片附件发送。"));
    for (const file of Array.from(files)) {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      if (activeProjectRef.current !== scope) return;
      const added = await api<{ attachment_id: string; filename: string }>(
        `/projects/${pid}/attachments`,
        "POST",
        { filename: file.name, data_base64: encoded },
      );
      if (activeProjectRef.current === scope) setAttachments((old) => [...old, added]);
    }
  }

  useEffect(() => {
    setDirectAnswers({});
    setIgnoredClarification("");
    setDrafts({ create: "", edit: "", question: "" });
    setAttachments([]);
  }, [scope]);
  useEffect(() => {
    setScopeEnabled(false);
    setScopeNetworks("");
    setScopeAddresses("");
  }, [scope, vid]);
  return {
    directAnswers, setDirectAnswers, ignoredClarification, setIgnoredClarification,
    scopeEnabled,
    setScopeEnabled,
    scopeNetworks,
    setScopeNetworks,
    scopeAddresses,
    setScopeAddresses,
    task,
    setTask,
    workflow,
    setWorkflow,
    editAction,
    setEditAction,
    reviewRequested,
    setReviewRequested,
    drafts,
    setDrafts,
    text,
    setText,
    analysisMode,
    setAnalysisMode,
    attachments,
    setAttachments,
    upload,
    syncComposerRoute,
  };
}
