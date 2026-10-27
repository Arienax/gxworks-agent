// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { Suspense, lazy } from "react";
import { RetainedPanel } from "../components/RetainedPanel";
import { Button, Modal } from "../components/ui";
import { ApprovalSettingsPanel } from "../features/ApprovalSettings";
import { DeveloperSettingsPanel } from "../features/DeveloperSettings";
import type { WorkbenchModel } from "./useWorkbenchController";

const Settings = lazy(() =>
  import("../features/Settings").then((module) => ({ default: module.Settings })),
);
export const settingsDialogFields = [
  "approvalSettings",
  "constructionExamples",
  "freshConfirmedGeneration",
  "locale",
  "modal",
  "session",
  "setApprovalSettings",
  "setConstructionExamples",
  "setFreshConfirmedGeneration",
  "setModal",
  "setSettings",
  "setSettingsRetry",
  "setSettingsTab",
  "settings",
  "settingsError",
  "settingsTab",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof settingsDialogFields[number]>;
export function SettingsDialog(props: Props) {
  const {
    approvalSettings,
    constructionExamples,
    freshConfirmedGeneration,
    locale,
    modal,
    session,
    setApprovalSettings,
    setConstructionExamples,
    setFreshConfirmedGeneration,
    setModal,
    setSettings,
    setSettingsRetry,
    setSettingsTab,
    settings,
    settingsError,
    settingsTab,
    t,
  } = props;

  return (
    <Modal open={modal === "settings"} onOpenChange={(v) => !v && setModal("")} title={t("设置")}>
      <nav className="settings-tabs" aria-label={t("设置分类")}>
        <Button
          variant={settingsTab === "general" ? "primary" : "ghost"}
          onClick={() => setSettingsTab("general")}
        >
          {t("通用与审批")}
        </Button>
        <Button
          variant={settingsTab === "model" ? "primary" : "ghost"}
          onClick={() => setSettingsTab("model")}
        >
          {t("模型")}
        </Button>
        <Button
          variant={settingsTab === "developer" ? "primary" : "ghost"}
          onClick={() => setSettingsTab("developer")}
        >
          {t("开发者")}
        </Button>
      </nav>
      <RetainedPanel active={settingsTab === "general"}>
        {() =>
          approvalSettings ? (
            <ApprovalSettingsPanel
              value={approvalSettings}
              disabled={!!session?.read_only}
              onChange={setApprovalSettings}
              t={t}
            />
          ) : (
            <p>{t("正在读取设置")}</p>
          )
        }
      </RetainedPanel>
      {settingsTab === "general" && settingsError && (
        <p role="alert" className="error-text">
          {settingsError}
          <Button onClick={() => setSettingsRetry((n) => n + 1)}>{t("重试")}</Button>
        </p>
      )}
      <RetainedPanel active={settingsTab === "model"}>
        {() =>
          settings && (
            <Suspense fallback={<p role="status">{t("正在读取设置")}</p>}>
              <Settings
                locale={locale}
                value={settings}
                t={t}
                disabled={!session || !!session?.read_only}
                onChange={setSettings}
              />
            </Suspense>
          )
        }
      </RetainedPanel>
      <RetainedPanel active={settingsTab === "developer"}>
        {() => (
          <DeveloperSettingsPanel
            constructionExamples={constructionExamples}
            freshConfirmedGeneration={freshConfirmedGeneration}
            disabled={!session || !!session?.read_only}
            onConstructionExamplesChange={setConstructionExamples}
            onFreshConfirmedGenerationChange={setFreshConfirmedGeneration}
            t={t}
          />
        )}
      </RetainedPanel>
    </Modal>
  );
}
