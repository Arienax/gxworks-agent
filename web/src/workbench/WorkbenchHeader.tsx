import {
  CircuitBoard,
  Github,
  Moon,
  Settings2,
  ShieldCheck,
  Sun
} from "lucide-react";
import { activeJob } from "../api/client";
import { Badge } from "../components/ui";
import { approvalLabels } from "../features/ApprovalSettings";
import { ModelSwitcher } from "../features/ModelUsage";
import type { Locale } from "../i18n";
import type { WorkbenchModel } from "./useWorkbenchController";


export const workbenchHeaderFields = [
  "approvalSettings",
  "busy",
  "canWrite",
  "jobs",
  "locale",
  "session",
  "setLocale",
  "setModal",
  "setSettings",
  "setSettingsTab",
  "settings",
  "t",
  "theme",
  "toggleTheme",
] as const;
type Props = Pick<WorkbenchModel, typeof workbenchHeaderFields[number]>;
export function WorkbenchHeader(props: Props) {
  const {
    approvalSettings,
    busy,
    canWrite,
    jobs,
    locale,
    session,
    setLocale,
    setModal,
    setSettings,
    setSettingsTab,
    settings,
    t,
    theme,
    toggleTheme,
  } = props;

  return (
    <header className="app-header">
      <div className="brand">
        <CircuitBoard size={24} />
        <strong>
          GXWorks <span>Agent</span>
        </strong>
        <span className="edition">WORKBENCH</span>
      </div>
      <div className="header-center">
        <span className="connection-dot" />
        {t("本地服务")}
        <span className="separator">/</span>
        {t("工程工作台")}
      </div>
      <div className="header-actions">
        <a
          className="project-source"
          href="https://github.com/Arienax/gxworks-agent"
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
          aria-label={t("GitHub 项目来源")}
          title="GitHub · Arienax/gxworks-agent"
        >
          <Github size={18} />
          <span>GitHub</span>
        </a>
        <button
          className="icon-button"
          disabled={busy}
          aria-label={t(theme === "dark" ? "切换浅色主题" : "切换深色主题")}
          title={t(theme === "dark" ? "切换浅色主题" : "切换深色主题")}
          onClick={toggleTheme}
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </button>
        <button
          className={`approval-mode-indicator ${approvalSettings?.mode === "full" ? "full-access" : ""}`}
          title={t("在设置中调整审批模式")}
          onClick={() => {
            setSettingsTab("general");
            setModal("settings");
          }}
        >
          <ShieldCheck size={14} />
          {t(approvalSettings ? approvalLabels[approvalSettings.mode] : "审批设置")}
        </button>
        {session?.read_only && <Badge tone="warn">{t("只读恢复")}</Badge>}
        <ModelSwitcher
          settings={settings}
          job={jobs.find(activeJob)}
          disabled={!canWrite}
          onChange={setSettings}
          onSettings={() => {
            setSettingsTab("model");
            setModal("settings");
          }}
          t={t}
        />
        <select
          aria-label={t("响应语言")}
          className="language-select"
          value={locale}
          onChange={(e) => setLocale(e.target.value as Locale)}
        >
          <option value="zh-CN">简体中文</option>
          <option value="en">English</option>
          <option value="ja">日本語</option>
        </select>
        <button
          className="icon-button"
          aria-label={t("设置")}
          title={t("设置")}
          onClick={() => setModal("settings")}
        >
          <Settings2 size={18} />
        </button>
      </div>
    </header>
  );
}
