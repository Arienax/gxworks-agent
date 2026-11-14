// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { GitCommitHorizontal } from "lucide-react";
import { useState } from "react";
import type { Project } from "../api/client";
import { Badge, Button, Modal } from "../components/ui";
import type { Locale } from "../i18n";
import { useResource } from "../lifecycle/useResource";
import { DiffView } from "./ResultViews";
import type { Translate } from "./types";

function SavedVersionDiff({ projectId, versionId, baseId, t }: {
  projectId: string; versionId: string; baseId: string; t: Translate;
}) {
  const { value, error, loading } = useResource<unknown>(
    `/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/diff?base_version_id=${encodeURIComponent(baseId)}`,
  );
  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (loading) return <p role="status">{t("正在读取…")}</p>;
  return value ? <DiffView value={value} t={t} /> : <p className="muted">{t("暂无可比较的程序")}</p>;
}

type Props = {
  project: Project;
  viewedId: string;
  canActivate: boolean;
  locale: Locale;
  t: Translate;
  onClose: () => void;
  onView: (id: string) => void;
  onActivate: (id: string) => void;
};

export function VersionHistory({ project, viewedId, canActivate, locale, t, onClose, onView, onActivate }: Props) {
  const versions = [...(project.versions || [])].reverse();
  const [selectedId, setSelectedId] = useState(viewedId || project.active_version_id || versions[0]?.id);
  const selected = versions.find(version => version.id === selectedId) || versions[0];
  const parent = versions.find(version => version.id === selected?.parent_version_id);
  const children = versions.filter(version => version.parent_version_id === selected?.id);
  const [comparisonId, setComparisonId] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const baseId = comparisonId || parent?.id || "";
  function select(id: string) {
    setSelectedId(id);
    setComparisonId("");
    setShowDiff(false);
  }

  return (
    <Modal open onOpenChange={(open) => { if (!open) onClose(); }} title={t("版本历史")} description={project.name || ""}>
      {!selected ? <p className="muted">{t("暂无版本")}</p> : (
        <>
          <ol className="version-history-list">
            {versions.map(version => (
              <li key={version.id}>
                <button type="button" className="version-history-row" aria-pressed={selected.id === version.id}
                  onClick={() => select(version.id)}>
                  <GitCommitHorizontal size={17} aria-hidden="true" />
                  <span className="mono">{version.id}</span>
                  {version.id === project.active_version_id && <Badge tone="success">{t("当前版本")}</Badge>}
                  {version.id === viewedId && <Badge>{t("正在查看")}</Badge>}
                  <span className="version-history-summary">{version.summary || version.program_name}</span>
                  {version.created_at && <time dateTime={version.created_at}>{new Date(version.created_at).toLocaleString(locale)}</time>}
                </button>
              </li>
            ))}
          </ol>
          <section className="version-history-details" aria-label={selected.id}>
            {selected.parent_version_id && (
              <p>{t("基础版本")} {parent
                ? <button className="text-button mono" onClick={() => select(parent.id)}>{parent.id}</button>
                : <span className="mono">{selected.parent_version_id}</span>}</p>
            )}
            {!!children.length && <p>{t("后续版本")} {children.map(child => (
              <button key={child.id} className="text-button mono" onClick={() => select(child.id)}>{child.id}</button>
            ))}</p>}
            <div className="version-history-actions">
              <Button onClick={() => onView(selected.id)}>{t("查看版本")}</Button>
              <Button disabled={!canActivate || selected.id === project.active_version_id} onClick={() => onActivate(selected.id)}>
                {t("设为当前版本")}
              </Button>
            </div>
            {versions.length > 1 && (
              <div className="version-history-actions">
                <select aria-label={t("对比版本")} value={baseId} onChange={event => {
                  setComparisonId(event.target.value);
                  setShowDiff(false);
                }}>
                  {!baseId && <option value="">{t("对比版本")}</option>}
                  {versions.filter(version => version.id !== selected.id).map(version => (
                    <option key={version.id} value={version.id}>{version.id}</option>
                  ))}
                </select>
                <Button disabled={!baseId} onClick={() => setShowDiff(true)}>{t("查看差异")}</Button>
              </div>
            )}
            {showDiff && baseId && <SavedVersionDiff key={`${selected.id}:${baseId}`} projectId={project.id}
              versionId={selected.id} baseId={baseId} t={t} />}
          </section>
        </>
      )}
    </Modal>
  );
}
