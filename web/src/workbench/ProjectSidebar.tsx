// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Bot,
  Bug,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  X,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Github,
  Info,
  ListChecks,
  LoaderCircle,
  MessageSquare,
  Moon,
  PanelLeftClose,
  Play,
  Plus,
  Settings2,
  SquarePen,
  Sun,
  Wand2,
} from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { activeJob } from "../api/client";
import type { Job } from "../api/client";
import { Badge } from "../components/ui";
import { Popover } from "../components/Popover";
import type { Locale } from "../i18n";
import { statusText, statusTone } from "../i18n";
import { jobKindLabel, listedJobStatus } from "./jobLabels";
import type { WorkbenchModel } from "./useWorkbenchController";

const jobIcons: Record<string, ReactNode> = {
  analysis: <ListChecks size={15} />,
  generation: <Wand2 size={15} />,
  direct_generation: <Wand2 size={15} />,
  agent: <MessageSquare size={15} />,
  review: <ClipboardCheck size={15} />,
  test_plan: <FlaskConical size={15} />,
  debug_plan: <Bug size={15} />,
  gx_read: <ArrowLeft size={15} />,
  gx_inspect: <GitBranch size={15} />,
  execution: <Play size={15} />,
};
const VISIBLE_TASKS = 12;

export const projectSidebarFields = [
  "busy",
  "canWrite",
  "displayedJobStatus",
  "environment",
  "jobId",
  "jobs",
  "locale",
  "newTask",
  "pid",
  "projects",
  "session",
  "setJobId",
  "setLeftOpen",
  "setLocale",
  "setModal",
  "setPid",
  "setSettingsTab",
  "t",
  "theme",
  "toggleTheme",
] as const;
type Props = Pick<WorkbenchModel, typeof projectSidebarFields[number]> & {
  /** Every registered workspace with its projects, plus how to manage the list. */
  workspaces?: { id: string; path: string; label: string; active: boolean;
    projects: { id?: string | null; name?: string | null; plc_model?: string | null;
      target_mode?: string | null }[] }[];
  activeWorkspaceId?: string | null;
  workspaceError?: string;
  workspacesBusy?: boolean;
  onAddWorkspace?: () => void;
  onActivateWorkspace?: (identifier: string) => void;
  onForgetWorkspace?: (identifier: string) => void;
};
export function ProjectSidebar(props: Props) {
  const {
    busy,
    canWrite,
    displayedJobStatus,
    environment,
    jobId,
    jobs,
    locale,
    newTask,
    pid,
    projects,
    session,
    setJobId,
    setLeftOpen,
    setLocale,
    setModal,
    setPid,
    setSettingsTab,
    t,
    theme,
    toggleTheme,
    workspaces,
    workspaceError,
    workspacesBusy,
    onAddWorkspace,
    onActivateWorkspace,
    onForgetWorkspace,
  } = props;
  const [showAll, setShowAll] = useState(false);
  const workspaceGroups = workspaces ?? [];
  const projectJobs = jobs.filter((job) => job.project_id === pid);
  const visibleJobs = showAll ? projectJobs : projectJobs.slice(0, VISIBLE_TASKS);
  const closeOnNarrow = () => { if (window.innerWidth <= 1100) setLeftOpen(false); };
  const when = (job: Job) => {
    const date = new Date(job.created_at);
    const today = new Date().toDateString() === date.toDateString();
    return date.toLocaleString(locale, today
      ? { hour: "2-digit", minute: "2-digit" }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  return (
    <aside className="project-sidebar" aria-label={t("工程")}>
      <div className="sidebar-inner">
      <div className="sidebar-brand">
        <img className="brand-mark" src="./icon.svg" alt="" aria-hidden="true" />
        <strong>GXWorks <span>Agent</span></strong>
        <button className="icon-button sidebar-collapse" aria-label={t("隐藏侧栏")} title={t("隐藏侧栏")}
          onClick={() => setLeftOpen(false)}>
          <PanelLeftClose size={17} />
        </button>
      </div>
      <div className="sidebar-workspace">
        <button
          type="button"
          className={`workspace-pick ${workspaceGroups.some((group) => group.active) ? "" : "empty"}`}
          title={t("添加一个工作区文件夹")}
          onClick={onAddWorkspace}
          disabled={!!workspacesBusy}
        >
          <FolderOpen size={14} aria-hidden="true" />
          <span className="workspace-pick-name">
            {workspacesBusy ? t("正在选择…") : t("添加工作区")}
          </span>
          <Plus size={13} aria-hidden="true" />
        </button>
        {workspaceError && <p className="workspace-pick-error" role="alert">{workspaceError}</p>}
      </div>
      <a
        className="project-source sidebar-github"
        href="https://github.com/Arienax/gxworks-agent"
        target="_blank"
        rel="noopener noreferrer"
        referrerPolicy="no-referrer"
        aria-label={t("GitHub 项目来源")}
        title="GitHub · Arienax/gxworks-agent"
      >
        <Github size={16} />
        <span>GitHub · Arienax/gxworks-agent</span>
        <ArrowUpRight size={13} />
      </a>
      <button className="new-task-button" disabled={!canWrite && !!pid}
        onClick={() => (pid ? newTask() : setModal("new"))}>
        <SquarePen size={16} />
        <span>{t(pid ? "新任务" : "新建工程")}</span>
      </button>
      {session?.read_only && <div className="sidebar-notice"><Badge tone="warn">{t("只读恢复")}</Badge></div>}

      <div className="sidebar-heading">
        <span>{t("工程")}</span>
        {workspaceGroups.some((group) => group.active) && (
          <small className="sidebar-heading-workspace" title={workspaceGroups.find((group) => group.active)?.path}>
            {workspaceGroups.find((group) => group.active)?.label}
          </small>
        )}
        <button className="icon-button" aria-label={t("新建工程")} title={t("新建工程")}
          disabled={!canWrite} onClick={() => setModal("new")}>
          <Plus size={15} />
        </button>
      </div>
      <nav className="project-tree">
        {/* Other workspaces stay one click away; each keeps its own projects. */}
        {workspaceGroups.filter((group) => !group.active).map((group) => (
          <section key={group.id} className="workspace-group">
            <div className="workspace-group-head">
              <button type="button" className="workspace-group-toggle" title={group.path}
                disabled={!!workspacesBusy} onClick={() => onActivateWorkspace?.(group.id)}>
                <ChevronRight size={14} />
                <FolderOpen size={14} />
                <span className="workspace-group-label">{group.label}</span>
                <small>{group.projects.length}</small>
              </button>
              <button type="button" className="icon-button workspace-group-forget"
                aria-label={t("从列表移除工作区")} title={t("从列表移除工作区")}
                disabled={!!workspacesBusy} onClick={() => onForgetWorkspace?.(group.id)}>
                <X size={12} />
              </button>
            </div>
          </section>
        ))}
        {projects.map((p) => {
          const selected = pid === p.id;
          return (
            <div key={p.id} className={`project-node ${selected ? "selected" : ""}`}>
              <button
                disabled={busy}
                className="project-item"
                aria-expanded={selected}
                onClick={() => {
                  if (p.id === pid) return;
                  setShowAll(false);
                  setPid(p.id);
                }}
              >
                {selected ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <FolderOpen size={15} />
                <span>
                  {p.name}
                  <small>{p.plc_model} · {p.target_mode.toUpperCase()}</small>
                </span>
              </button>
              {/* Kept mounted so the task list can animate open and closed. */}
              <div className="task-list" data-open={selected || undefined} role="list"
                aria-label={t("任务记录")} inert={selected ? undefined : true}>
                <div className="task-list-inner">
                  {visibleJobs.map((job) => {
                    const state = listedJobStatus(job, jobId, displayedJobStatus);
                    return (
                      <div role="listitem" key={job.id}><button
                        className={`task-item ${job.id === jobId ? "selected" : ""}`}
                        aria-current={job.id === jobId ? "true" : undefined}
                        title={`${t(jobKindLabel(job.kind))} · ${statusText(locale, state)} · ${job.id}`}
                        onClick={() => { setJobId(job.id); closeOnNarrow(); }}
                      >
                        <span className="task-icon">
                          {activeJob(job) ? <LoaderCircle size={15} className="spin" /> : jobIcons[job.kind] || <Activity size={15} />}
                        </span>
                        <span className="task-label">{t(jobKindLabel(job.kind))}</span>
                        <span className={`status-dot ${statusTone(state)}`} aria-label={statusText(locale, state)} />
                        <small>{when(job)}</small>
                      </button></div>
                    );
                  })}
                  {selected && !projectJobs.length && <p className="sidebar-empty">{t("暂无任务")}</p>}
                  {selected && projectJobs.length > VISIBLE_TASKS && (
                    <button className="task-more" onClick={() => setShowAll((value) => !value)}>
                      {t(showAll ? "收起" : "显示全部")} · {projectJobs.length}
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {!projects.length && <p className="sidebar-empty">{t("新建工程，或将 GXW 工程拖到对话输入框导入。")}</p>}
      </nav>

      <div className="sidebar-bottom">
        <button className="sidebar-row" onClick={() => setModal("environment")}>
          <Activity size={15} />
          <span>GX Works2</span>
          <span className="muted">{environment.status ? statusText(locale, String(environment.status)) : t("未运行")}</span>
          <span className={`status-dot ${environment.status ? statusTone(String(environment.status)) : "neutral"}`}
            aria-hidden="true" />
        </button>
        <Popover className="sidebar-menu" summaryClassName="sidebar-row"
          summary={<><Settings2 size={15} /><span>{t("设置")}</span><span className="connection-dot" title={t("已连接")} /></>}>
          <button onClick={() => { setSettingsTab("model"); setModal("settings"); }}>
            <Bot size={15} />{t("模型设置")}
          </button>
          <button onClick={() => { setSettingsTab("general"); setModal("settings"); }}>
            <Settings2 size={15} />{t("设置")}
          </button>
          <button onClick={() => setModal("about")}>
            <Info size={15} />{t("关于 GXWorks Agent")}
          </button>
          <button disabled={busy} onClick={toggleTheme}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            {t(theme === "dark" ? "切换浅色主题" : "切换深色主题")}
          </button>
          <label className="popover-field">
            <span>{t("响应语言")}</span>
            <select aria-label={t("响应语言")} value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
              <option value="zh-CN">简体中文</option>
              <option value="en">English</option>
              <option value="ja">日本語</option>
            </select>
          </label>
          <p className="popover-footnote"><span className="connection-dot" />{t("本地服务")} · {t("已连接")}</p>
        </Popover>
      </div>
      </div>
    </aside>
  );
}
