import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Bot,
  Bug,
  ChevronDown,
  ChevronRight,
  CircuitBoard,
  ClipboardCheck,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Github,
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
type Props = Pick<WorkbenchModel, typeof projectSidebarFields[number]>;
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
  } = props;
  const [showAll, setShowAll] = useState(false);
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
      <div className="sidebar-brand">
        <CircuitBoard size={20} />
        <strong>GXWorks <span>Agent</span></strong>
        <button className="icon-button sidebar-collapse" aria-label={t("隐藏侧栏")} title={t("隐藏侧栏")}
          onClick={() => setLeftOpen(false)}>
          <PanelLeftClose size={17} />
        </button>
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
        <Github size={19} />
        <span><strong>GitHub</strong><small>Arienax/gxworks-agent</small></span>
        <ArrowUpRight size={14} />
      </a>
      <button className="new-task-button" disabled={!canWrite && !!pid}
        onClick={() => (pid ? newTask() : setModal("new"))}>
        <SquarePen size={16} />
        <span>{t(pid ? "新任务" : "新建工程")}</span>
      </button>
      {session?.read_only && <div className="sidebar-notice"><Badge tone="warn">{t("只读恢复")}</Badge></div>}

      <div className="sidebar-heading">
        <span>{t("工程")}</span>
        <button className="icon-button" aria-label={t("新建工程")} title={t("新建工程")}
          disabled={!canWrite} onClick={() => setModal("new")}>
          <Plus size={15} />
        </button>
      </div>
      <nav className="project-tree">
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
              {selected && (
                <div className="task-list" role="list" aria-label={t("任务记录")}>
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
                  {!projectJobs.length && <p className="sidebar-empty">{t("暂无任务")}</p>}
                  {projectJobs.length > VISIBLE_TASKS && (
                    <button className="task-more" onClick={() => setShowAll((value) => !value)}>
                      {t(showAll ? "收起" : "显示全部")} · {projectJobs.length}
                    </button>
                  )}
                </div>
              )}
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
        </button>
        <Popover className="sidebar-menu" summaryClassName="sidebar-row"
          summary={<><Settings2 size={15} /><span>{t("设置")}</span><span className="connection-dot" title={t("已连接")} /></>}>
          <button onClick={() => { setSettingsTab("model"); setModal("settings"); }}>
            <Bot size={15} />{t("模型设置")}
          </button>
          <button onClick={() => { setSettingsTab("general"); setModal("settings"); }}>
            <Settings2 size={15} />{t("设置")}
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
    </aside>
  );
}
