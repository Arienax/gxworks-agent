// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { Suspense, lazy, useEffect } from "react";
import { RetainedPanel } from "../components/RetainedPanel";
import { Button, Modal } from "../components/ui";
import { ApprovalSettingsPanel } from "../features/ApprovalSettings";
import { DeveloperSettingsPanel } from "../features/DeveloperSettings";
import type { WorkbenchModel } from "./useWorkbenchController";

/** Shared by the lazy panel and its prefetch so both resolve the same chunk. */
const loadSettings = () =>
  import("../features/Settings").then((module) => ({ default: module.Settings }));
const Settings = lazy(loadSettings);
/** Placeholder with the panel's own rhythm, so a slow first read never looks like a freeze. */
function SettingsSkeleton({ t, rows }: { t: (s: string) => string; rows: number }) {
  return (
    <div className="settings-loading" role="status">
      {Array.from({ length: rows }, (_, index) => (
        <span className="skeleton-row" key={index} />
      ))}
      <span className="sr-only">{t("正在读取设置")}</span>
    </div>
  );
}

export const settingsDialogFields = [
  "approvalSettings",
  "constructionExamples",
  "freshConfirmedGeneration",
  "locale",
  "modal",
  "connection",
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
    connection,
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
  /** Warm the model panel while the reader is still on another tab. */
  useEffect(() => {
    if (modal !== "settings") return;
    void loadSettings();
  }, [modal]);

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
        {() => (
          <div className="settings-panel" data-active={settingsTab === "general" || undefined}>
            {approvalSettings ? (
              <ApprovalSettingsPanel
                value={approvalSettings}
                disabled={!!connection?.read_only}
                onChange={setApprovalSettings}
                t={t}
              />
            ) : (
              <SettingsSkeleton t={t} rows={3} />
            )}
          </div>
        )}
      </RetainedPanel>
      {settingsTab === "general" && settingsError && (
        <p role="alert" className="error-text">
          {settingsError}
          <Button onClick={() => setSettingsRetry((n) => n + 1)}>{t("重试")}</Button>
        </p>
      )}
      <RetainedPanel active={settingsTab === "model"}>
        {() => (
          <div className="settings-panel" data-active={settingsTab === "model" || undefined}>
            {settings ? (
              <Suspense fallback={<SettingsSkeleton t={t} rows={4} />}>
                <Settings
                  locale={locale}
                  value={settings}
                  t={t}
                  disabled={!connection || !!connection?.read_only}
                  onChange={setSettings}
                />
              </Suspense>
            ) : (
              <SettingsSkeleton t={t} rows={4} />
            )}
          </div>
        )}
      </RetainedPanel>
      <RetainedPanel active={settingsTab === "developer"}>
        {() => (
          <div className="settings-panel" data-active={settingsTab === "developer" || undefined}>
            <DeveloperSettingsPanel
              constructionExamples={constructionExamples}
              freshConfirmedGeneration={freshConfirmedGeneration}
              disabled={!connection || !!connection?.read_only}
              onConstructionExamplesChange={setConstructionExamples}
              onFreshConfirmedGenerationChange={setFreshConfirmedGeneration}
              t={t}
            />
          </div>
        )}
      </RetainedPanel>
    </Modal>
  );
}
