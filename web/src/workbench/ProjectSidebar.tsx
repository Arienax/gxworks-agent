import {
  Activity,
  Bot,
  ChevronRight,
  FolderOpen,
  GitBranch,
  History,
  Plus
} from "lucide-react";
import { Button } from "../components/ui";
import type { WorkbenchModel } from "./useWorkbenchController";


export const projectSidebarFields = [
  "busy",
  "canWrite",
  "environment",
  "locale",
  "pid",
  "project",
  "projects",
  "selectVersion",
  "setModal",
  "setPid",
  "setSettingsTab",
  "t",
  "vid",
] as const;
type Props = Pick<WorkbenchModel, typeof projectSidebarFields[number]>;
export function ProjectSidebar(props: Props) {
  const {
    busy,
    canWrite,
    environment,
    locale,
    pid,
    project,
    projects,
    selectVersion,
    setModal,
    setPid,
    setSettingsTab,
    t,
    vid,
  } = props;

  return (
    <aside className="project-sidebar">
      <div className="sidebar-heading">
        <span>{t("工程")}</span>
        <Button
          variant="ghost"
          aria-label={t("新建工程")}
          disabled={!canWrite}
          onClick={() => setModal("new")}
        >
          <Plus size={16} />
        </Button>
      </div>
      <div className="project-list">
        {projects.map((p) => (
          <button
            key={p.id}
            disabled={busy}
            className={`project-item ${pid === p.id ? "selected" : ""}`}
            onClick={() => {
              if (p.id === pid) return;
              setPid(p.id);
            }}
          >
            <FolderOpen size={17} />
            <span>
              {p.name}
              <small>
                {p.plc_model} · {p.target_mode.toUpperCase()}
              </small>
            </span>
            {pid === p.id && <ChevronRight size={14} />}
          </button>
        ))}
      </div>
      <div className="sidebar-heading versions-title">
        <span>{t("版本历史")}</span>
        <History size={15} />
      </div>
      <div className="version-list">
        {project?.versions
          ?.slice()
          .reverse()
          .map((v) => (
            <button
              className={`version-item ${v.id === vid ? "selected" : ""}`}
              key={v.id}
              disabled={busy}
              onClick={() => selectVersion(v.id)}
            >
              <GitBranch size={15} />
              <span>
                <strong className="mono">{v.id}</strong>
                <small>
                  {new Date(v.created_at || "").toLocaleString(locale, {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </small>
              </span>
              {v.id === project.active_version_id && (
                <span className="active-version" title={t("当前版本")} />
              )}
            </button>
          ))}
        {!project?.versions?.length && <p className="sidebar-empty">{t("暂无版本")}</p>}
      </div>
      <div className="sidebar-bottom">
        <button onClick={() => setModal("environment")}>
          <Activity size={16} />
          <span>GX Works2</span>
          <span className="muted">{String(environment.status || t("未运行"))}</span>
        </button>
        <button
          onClick={() => {
            setSettingsTab("model");
            setModal("settings");
          }}
        >
          <Bot size={16} />
          <span>{t("模型设置")}</span>
        </button>
      </div>
    </aside>
  );
}
