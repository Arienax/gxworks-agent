import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowDownToLine, ArrowLeft, ArrowRight, ChevronDown, MoreHorizontal, RefreshCw, Trash2 } from "lucide-react";
import type { Artifact } from "../api/client";
import { api, artifactUrl, freshGxCsvUrl, jobDiagnosticsUrl } from "../api/client";
import { Button } from "../components/ui";

export function artifactLabel(id: string) {
  return ({ program_csv: "程序 CSV", comment_csv: "软元件注释 CSV", json: "梯形图 JSON", ir: "程序 IR",
    svg: "梯形图 SVG", st: "ST 程序", st_from_ir: "ST 程序", gxw: "GXW 工程", fbd: "FBD 模型" } as Record<string,string>)[id] || id;
}
function Menu({ label, icon, children, disabled, title, className = "" }: {
  label: string; icon: ReactNode; children: ReactNode; disabled?: boolean; title?: string; className?: string;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function close(e: PointerEvent) { if (!ref.current?.contains(e.target as Node)) ref.current?.removeAttribute("open"); }
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  if (disabled) return <Button disabled title={title}>{icon}{label}<ChevronDown size={13}/></Button>;
  return <details ref={ref} className={`toolbar-menu ${className}`} onKeyDown={(e) => {
    if (e.key === "Escape") { ref.current?.removeAttribute("open"); ref.current?.querySelector("summary")?.focus(); }
  }}>
    <summary className="button" title={title}>{icon}{label}<ChevronDown size={13}/></summary>
    <div className="toolbar-menu-content" onClick={(e) => {
      const item = (e.target as HTMLElement).closest("button,a");
      if (item && !item.hasAttribute("disabled")) ref.current?.removeAttribute("open");
    }}>{children}</div>
  </details>;
}
export function ProjectToolbar({ pid, vid, artifacts, exportable, diagnosticJobId, canRead, canSend, canRefresh,
  refreshing, onRead, onSend, onRefresh, more, t }: {
  pid: string; vid: string; artifacts: Artifact[]; exportable: boolean; diagnosticJobId?: string; canRead: boolean;
  canSend: boolean; canRefresh: boolean; refreshing: boolean; onRead: () => void;
  onSend: () => void; onRefresh: () => void; more: ReactNode; t: (s:string) => string;
}) {
  const files = artifacts.filter((a) => a.available);
  const canArtifactExport = exportable && !!pid && !!vid && files.length > 0;
  const canDiagnosticExport = !!diagnosticJobId;
  const canFreshExport = canArtifactExport && files.some((a) => ["ir", "json", "program_csv"].includes(a.id));
  const [deleting, setDeleting] = useState(false);
  async function deleteProject() {
    if (!pid || deleting) return;
    if (!window.confirm(t("确认删除当前项目？项目文件和全部版本将被永久删除。"))) return;
    setDeleting(true);
    try {
      await api<{ deleted: boolean }>(`/projects/${encodeURIComponent(pid)}`, "DELETE");
      window.location.assign(window.location.pathname);
    } catch (error) {
      window.alert(String((error as Error).message || error));
    } finally {
      setDeleting(false);
    }
  }
  return <div className="project-toolbar" role="toolbar" aria-label={t("工程操作")}>
    <div className="toolbar-group">
      <Menu label={t("导出文件")} icon={<ArrowDownToLine size={15}/>} disabled={!canArtifactExport && !canDiagnosticExport}
        title={t(canArtifactExport ? (canDiagnosticExport ? "下载当前版本的文件和任务记录" : "下载当前版本的文件") : canDiagnosticExport ? "下载当前任务记录" : "生成并通过校验后即可导出文件")} className="export-menu">
        {canArtifactExport && files.map((a) => <a key={a.id} href={artifactUrl(pid, vid, a.id, true)}>
          <span>{t(artifactLabel(a.id))}</span><small>{a.filename}</small>
        </a>)}
        {canFreshExport && <a href={freshGxCsvUrl(pid, vid)}>
          <span>{t("重新导出 GX Works2 CSV")}</span>
          <small>{t("不调用模型，按当前导出器从已保存程序重新生成")}</small>
        </a>}
        {canDiagnosticExport && <a href={jobDiagnosticsUrl(diagnosticJobId)} download>
          <span>{t("任务交互与诊断 ZIP")}</span>
          <small>{t("当前任务")} · {diagnosticJobId}</small>
        </a>}
      </Menu>
    </div>
    <div className="toolbar-group gx-transfer-group">
      <Button disabled={!canRead} onClick={onRead} title={t("GX Works2 → 工作台")}><ArrowLeft size={15}/>{t("从 GX 读取")}</Button>
      <Button disabled={!canSend} onClick={onSend} title={t("工作台 → GX Works2")}><ArrowRight size={15}/>{t("发送到 GX")}</Button>
    </div>
    <div className="toolbar-group toolbar-secondary">
      <Button variant="ghost" disabled={!canRefresh} onClick={onRefresh}
        aria-label={t("刷新结果 / 重绘梯形图")} title={t("刷新结果 / 重绘梯形图")}>
        <RefreshCw size={16} className={refreshing ? "spin" : ""}/>
      </Button>
      <Menu label={t("更多")} icon={<MoreHorizontal size={16}/>} className="more-menu">
        {more}
        <Button variant="danger" disabled={!pid || deleting} onClick={() => void deleteProject()}>
          <Trash2 size={15}/>{t(deleting ? "正在删除…" : "删除项目")}
        </Button>
      </Menu>
    </div>
  </div>;
}
