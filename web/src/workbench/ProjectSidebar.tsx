// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import {
  Activity,
  ArrowUpRight,
  Bot,
  ChevronDown,
  ChevronRight,
  FolderOpen,
  FolderPlus,
  Github,
  Info,
  ListChecks,
  LoaderCircle,
  MessageSquare,
  MessageSquarePlus,
  Moon,
  PanelLeftClose,
  Search,
  Settings2,
  Sun,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { activeJob } from "../api/client";
import type { Job } from "../api/client";
import { Badge } from "../components/ui";
import { Popover } from "../components/Popover";
import type { Locale } from "../i18n";
import { statusText, statusTone } from "../i18n";
import { conversationTitle, filterConversations, orderByProject } from "./conversationRows";
import { jobKindLabel, listedJobStatus } from "./jobLabels";
import type { WorkbenchModel } from "./useWorkbenchController";

const VISIBLE = 12;

export const projectSidebarFields = [
  "busy",
  "displayedJobStatus",
  "environment",
  "jobId",
  "locale",
  "newTask",
  "pid",
  "session",
  "setLeftOpen",
  "setLocale",
  "setModal",
  "setSettingsTab",
  "t",
  "theme",
  "toggleTheme",
] as const;

type WorkspaceGroup = {
  id: string;
  path: string;
  label: string;
  active: boolean;
  projects: { id?: string | null; name?: string | null; plc_model?: string | null;
    target_mode?: string | null }[];
};

type Props = Pick<WorkbenchModel, typeof projectSidebarFields[number]> & {
  /** Every conversation in the served workspace, newest first. */
  conversations?: Job[];
  onOpenConversation?: (job: Job) => void;
  /** Every registered workspace, so several folders stay one click apart. */
  workspaces?: WorkspaceGroup[];
  workspaceError?: string;
  workspacesBusy?: boolean;
  onAddWorkspace?: () => void;
  onActivateWorkspace?: (identifier: string, projectId?: string) => void;
  onForgetWorkspace?: (identifier: string) => void;
};

/**
 * One workspace list, and beneath the active one its conversations.
 *
 * A conversation is one job, so the list answers "what did I ask for" instead of
 * nesting a project tree above the work itself.
 */
export function ProjectSidebar(props: Props) {
  const {
    busy,
    displayedJobStatus,
    environment,
    jobId,
    locale,
    newTask,
    pid,
    session,
    setLeftOpen,
    setLocale,
    setModal,
    setSettingsTab,
    t,
    theme,
    toggleTheme,
    conversations,
    onOpenConversation,
    workspaces,
    workspaceError,
    workspacesBusy,
    onAddWorkspace,
    onActivateWorkspace,
    onForgetWorkspace,
  } = props;
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [byProject, setByProject] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState(false);
  const workspaceGroups = workspaces ?? [];

  // Ctrl+O adds a workspace, Ctrl+N starts a conversation, like the harness.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === "o") {
        event.preventDefault();
        onAddWorkspace?.();
      } else if (key === "n") {
        event.preventDefault();
        if (pid) newTask();
        else setModal("new");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onAddWorkspace, newTask, pid, setModal]);

  const closeOnNarrow = () => { if (window.innerWidth <= 1100) setLeftOpen(false); };
  const titleOf = (job: Job) => conversationTitle(job, (kind) => t(jobKindLabel(kind)));
  const when = (job: Job) => {
    const date = new Date(job.created_at);
    const today = new Date().toDateString() === date.toDateString();
    return date.toLocaleString(locale, today
      ? { hour: "2-digit", minute: "2-digit" }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  const matched = filterConversations(conversations ?? [], query, titleOf);
  const ordered = byProject ? orderByProject(matched) : matched;
  const visible = showAll ? ordered : ordered.slice(0, VISIBLE);
  const capped = ordered.length > VISIBLE;

  return (
    <aside className="project-sidebar" aria-label={t("工作区")}>
      <div className="sidebar-inner">
      <div className="sidebar-brand">
        <img className="brand-mark" src="./icon.svg" alt="" aria-hidden="true" />
        <strong>GXWorks <span>Agent</span></strong>
        <button className="icon-button sidebar-collapse" aria-label={t("隐藏侧栏")} title={t("隐藏侧栏")}
          onClick={() => setLeftOpen(false)}>
          <PanelLeftClose size={17} />
        </button>
      </div>
      {session?.read_only && <div className="sidebar-notice"><Badge tone="warn">{t("只读恢复")}</Badge></div>}

      <div className="sidebar-heading">
        <span>{t("工作区")}</span>
        <button className="icon-button" aria-label={t("搜索对话")} title={t("搜索对话")}
          aria-pressed={searchOpen} onClick={() => setSearchOpen((open) => !open)}>
          <Search size={15} />
        </button>
        <button className="icon-button"
          aria-label={byProject ? t("按时间排序") : t("按工程排序")}
          title={byProject ? t("按时间排序") : t("按工程排序")}
          aria-pressed={byProject} onClick={() => setByProject((value) => !value)}>
          <ListChecks size={15} />
        </button>
        <button className="icon-button" aria-label={t("添加工作区")}
          title={`${t("添加工作区")} · Ctrl+O`} disabled={!!workspacesBusy} onClick={onAddWorkspace}>
          <FolderPlus size={15} />
        </button>
      </div>
      {searchOpen && (
        <div className="sidebar-search">
          <input type="search" autoFocus value={query} placeholder={t("搜索对话")}
            aria-label={t("搜索对话")} onChange={(event) => setQuery(event.target.value)} />
        </div>
      )}
      {workspaceError && <p className="workspace-pick-error" role="alert">{workspaceError}</p>}

      <nav className="project-tree">
        {workspaceGroups.map((group) => {
          const open = group.active || !!expandedGroups[group.id];
          return (
            <section key={group.id} className={`workspace-group ${group.active ? "active" : ""}`}>
              <div className="workspace-row-wrap">
                <button type="button" className="workspace-row" title={group.path}
                  disabled={!!workspacesBusy}
                  onClick={() => {
                    if (!group.active) {
                      onActivateWorkspace?.(group.id);
                      return;
                    }
                    setExpandedGroups((old) => ({ ...old, [group.id]: !open }));
                  }}>
                  <span className="workspace-caret">
                    {group.active && (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />)}
                  </span>
                  <FolderOpen size={15} />
                  <span className="workspace-row-label">{group.label}</span>
                  {group.active && !!ordered.length && (
                    <small className="workspace-row-count">{ordered.length}</small>
                  )}
                </button>
                {group.active && (
                  <button type="button" className="icon-button workspace-new-chat"
                    aria-label={t("新建对话")} title={`${t("新建对话")} · Ctrl+N`}
                    onClick={() => (pid ? newTask() : setModal("new"))}>
                    <MessageSquarePlus size={13} />
                  </button>
                )}
                <button type="button" className="icon-button workspace-group-forget"
                  aria-label={t("从列表移除工作区")} title={t("从列表移除工作区")}
                  disabled={!!workspacesBusy} onClick={() => onForgetWorkspace?.(group.id)}>
                  <X size={12} />
                </button>
              </div>

              {/* The active workspace lists its conversations; others list their
                  projects, so a folder is still one click away without switching. */}
              {group.active && open && !jobId && !!pid && (
                <div className="conversation-node">
                  <button type="button" className="conversation-item selected"
                    aria-current="true" title={t("新建对话")}>
                    <span className="conversation-icon"><MessageSquarePlus size={13} /></span>
                    <span className="conversation-title">{t("新建对话")}</span>
                  </button>
                </div>
              )}
              {group.active && open && visible.map((job) => {
                const state = listedJobStatus(job, jobId, displayedJobStatus);
                return (
                  <div key={job.id} className="conversation-node">
                    <button type="button"
                      className={`conversation-item ${job.id === jobId ? "selected" : ""}`}
                      aria-current={job.id === jobId ? "true" : undefined}
                      title={titleOf(job)}
                      onClick={() => { onOpenConversation?.(job); closeOnNarrow(); }}>
                      <span className="conversation-icon">
                        {activeJob(job)
                          ? <LoaderCircle size={13} className="spin" />
                          : <MessageSquare size={13} />}
                      </span>
                      <span className="conversation-title">{titleOf(job)}</span>
                      <small>{when(job)}</small>
                      <span className={`status-dot ${statusTone(state)}`}
                        aria-label={statusText(locale, state)} />
                    </button>
                  </div>
                );
              })}
              {group.active && open && capped && (
                <button className="task-more" onClick={() => setShowAll((value) => !value)}>
                  {t(showAll ? "收起" : "显示全部")} · {ordered.length}
                </button>
              )}
              {group.active && open && !ordered.length && (
                <p className="sidebar-empty">
                  {t(query.trim() ? "没有匹配的对话。" : "还没有对话，点「新任务」开始。")}
                </p>
              )}
              {!group.active && open && group.projects.map((project) => (
                <div key={`${group.id}:${project.id}`} className="project-node remote">
                  <button type="button" className="project-item project-item-remote"
                    disabled={!!workspacesBusy} title={t("切换到该工作区并打开此工程")}
                    onClick={() => onActivateWorkspace?.(group.id, project.id || undefined)}>
                    <FolderOpen size={13} />
                    <span>{project.name}
                      <small>{String(project.target_mode || "").toUpperCase()}</small>
                    </span>
                    <ArrowUpRight size={12} />
                  </button>
                </div>
              ))}
              {!group.active && open && !group.projects.length && (
                <p className="sidebar-empty">{t("该工作区还没有工程。")}</p>
              )}
            </section>
          );
        })}
        {!workspaceGroups.length && (
          <p className="sidebar-empty">{t("添加一个工作区文件夹，工程就存放在里面。")}</p>
        )}
      </nav>

      <div className="sidebar-bottom">
        <a className="sidebar-row" href="https://github.com/Arienax/gxworks-agent"
          target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"
          aria-label={t("GitHub 项目来源")} title="GitHub · Arienax/gxworks-agent">
          <Github size={15} />
          <span>GitHub</span>
          <ArrowUpRight size={13} />
        </a>
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
