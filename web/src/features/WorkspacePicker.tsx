// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, FolderPlus } from "lucide-react";
import { api } from "../api/client";
import { Button, Modal } from "../components/ui";
import {
  isRootCrumb,
  jumpTarget,
  pickTarget,
  visibleEntries,
  type WorkspaceEntry,
  type WorkspaceListing,
} from "./workspaceBrowse";
import "./workspacePicker.css";

type Translate = (key: string) => string;

function failureText(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  return typeof error === "string" && error ? error : fallback;
}

export type DirectoryBrowserProps = {
  open: boolean;
  /** List one level; an absent path lists the host home directory. */
  listDirectory: (path?: string, signal?: AbortSignal) => Promise<WorkspaceListing>;
  createDirectory: (parent: string, name: string) => Promise<string>;
  /** The operator confirmed a directory: the selection, else the listed level. */
  onOpen: (path: string) => void;
  onClose: () => void;
  busy: boolean;
  /** False while no workspace exists: the dialog then has no way out. */
  dismissible?: boolean;
  t: Translate;
};

/**
 * In-app directory browser: breadcrumbs, an editable path, one level per column,
 * an optional second column previewing the selected folder, and folder creation.
 * No operating-system dialog is involved, so a remote browser works the same way.
 */
export function DirectoryBrowser({
  open,
  listDirectory,
  createDirectory,
  onOpen,
  onClose,
  busy,
  dismissible = true,
  t,
}: DirectoryBrowserProps) {
  const [listing, setListing] = useState<WorkspaceListing>();
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkspaceListing>();
  const [query, setQuery] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const pending = useRef<AbortController | null>(null);

  const load = useCallback(async (path?: string) => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    try {
      const value = await listDirectory(path, controller.signal);
      if (controller.signal.aborted) return;
      setListing(value);
      setDraft(value.path);
      setSelected(null);
      setPreview(undefined);
      setQuery("");
      setError("");
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(failureText(failure, t("无法读取该文件夹")));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [listDirectory, t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  useEffect(() => {
    if (!open || !selected) {
      setPreview(undefined);
      return;
    }
    let live = true;
    void listDirectory(selected).then(
      (value) => { if (live) setPreview(value); },
      (failure) => { if (live) setError(failureText(failure, t("无法读取该文件夹"))); },
    );
    return () => { live = false; };
  }, [open, selected, listDirectory, t]);

  const rows = useMemo(
    () => visibleEntries(listing, { query, showHidden }),
    [listing, query, showHidden],
  );
  const target = pickTarget(listing, selected);
  const busyNow = busy || loading;

  async function create() {
    if (!target || !newName.trim()) return;
    try {
      const created = await createDirectory(target, newName.trim());
      setCreating(false);
      setNewName("");
      await load(target);
      setSelected(created);
      setError("");
    } catch (failure) {
      setError(failureText(failure, t("无法新建文件夹")));
    }
  }

  function row(entry: WorkspaceEntry, depth: number) {
    const isSelected = selected === entry.path;
    return (
      <div key={`${depth}-${entry.path}`} className="picker-row-wrap">
        <button
          type="button"
          className={`picker-row ${isSelected ? "selected" : ""}`}
          aria-pressed={isSelected}
          title={entry.path}
          onClick={() => setSelected(entry.path)}
          onDoubleClick={() => void load(entry.path)}
        >
          <span className="picker-row-name">{entry.name}</span>
          {entry.hidden && <span className="picker-row-flag">{t("隐藏")}</span>}
        </button>
        <button
          type="button"
          className="picker-row-enter"
          aria-label={`${t("进入")} ${entry.name}`}
          title={t("进入")}
          onClick={() => void load(entry.path)}
        >
          <ChevronRight size={13} aria-hidden="true" />
        </button>
      </div>
    );
  }

  return (
    <Modal
      open={open}
      onOpenChange={(next) => { if (!next && dismissible) onClose(); }}
      title={t("选择工作区")}
      description={t("逐级进入文件夹，或用完整路径直接跳转；确认后即以该目录作为工作区。")}
    >
      <div className="workspace-picker">
        <div className="picker-head">
          <nav className="picker-crumbs" aria-label={t("当前路径")}>
            {(listing?.crumbs ?? []).map((crumb) => (
              <button
                key={crumb.path}
                type="button"
                className="picker-crumb"
                title={crumb.path}
                onClick={() => void load(crumb.path)}
              >
                {isRootCrumb(crumb) ? crumb.path : crumb.name}
              </button>
            ))}
          </nav>
          <form
            className="picker-path"
            onSubmit={(event) => { event.preventDefault(); void load(jumpTarget(draft)); }}
          >
            <input
              aria-label={t("路径")}
              value={draft}
              spellCheck={false}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") void load(jumpTarget(draft)); }}
            />
            <Button type="submit" disabled={busyNow}>{t("跳转")}</Button>
          </form>
        </div>

        {error && <p role="alert" className="picker-error">{error}</p>}
        {listing?.truncated && <p role="status" className="picker-note">{t("该层级项目过多，只显示前一部分。")}</p>}

        <div className={`picker-columns ${selected ? "split" : ""}`}>
          <div className="picker-column" role="list">
            {rows.map((entry) => row(entry, 0))}
            {!rows.length && (
              <p className="picker-empty">
                {loading ? t("正在读取") : listing ? t("没有子文件夹") : t("正在读取")}
              </p>
            )}
          </div>
          {selected && (
            <div className="picker-column picker-preview" role="list">
              <p className="picker-preview-head">{t("选中文件夹内的子文件夹")}</p>
              {(preview?.entries ?? [])
                .filter((entry) => showHidden || !entry.hidden)
                .map((entry) => row(entry, 1))}
              {!preview && <p className="picker-empty">{t("正在读取")}</p>}
              {preview && !preview.entries.length && <p className="picker-empty">{t("没有子文件夹")}</p>}
            </div>
          )}
        </div>

        <div className="picker-foot">
          <label className="picker-toggle">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(event) => setShowHidden(event.target.checked)}
            />
            {t("显示隐藏项")}
          </label>
          <span className="picker-target" title={target}>
            {t("将使用")}：{target || t("尚未选择")}
          </span>
          <div className="picker-actions">
            <Button disabled={busyNow} onClick={() => setCreating(true)}>
              <FolderPlus size={13} aria-hidden="true" /> {t("新建文件夹")}
            </Button>
            {dismissible && <Button disabled={busyNow} onClick={onClose}>{t("取消")}</Button>}
            <Button
              disabled={busyNow || !target}
              onClick={() => onOpen(target)}
            >
              {t("打开")}
            </Button>
          </div>
        </div>

        {creating && (
          <div className="picker-create" role="dialog" aria-label={t("新建文件夹")}>
            <label>
              {t("文件夹名称")}
              <input
                autoFocus
                value={newName}
                maxLength={255}
                onChange={(event) => setNewName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void create();
                  if (event.key === "Escape") { setCreating(false); setNewName(""); }
                }}
              />
            </label>
            <p className="picker-note">
              {t("将在")} {target} {t("下创建")}
            </p>
            <div className="picker-actions">
              <Button disabled={busyNow} onClick={() => { setCreating(false); setNewName(""); }}>
                {t("取消")}
              </Button>
              <Button disabled={busyNow || !newName.trim()} onClick={() => void create()}>
                {t("创建")}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

/**
 * The workbench's workspace choice: browse on the service, then bind the picked
 * directory. Rendering and navigation belong to DirectoryBrowser; this wrapper
 * owns the wire calls and the confirmation.
 */
export function WorkspacePicker({
  open,
  onClose,
  onSelected,
  dismissible = true,
  t,
}: {
  open: boolean;
  onClose: () => void;
  onSelected: (path: string) => void;
  dismissible?: boolean;
  t: Translate;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const listDirectory = useCallback(
    (path?: string, signal?: AbortSignal) =>
      api<WorkspaceListing>(
        `/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`,
        "GET",
        undefined,
        { signal },
      ),
    [],
  );

  const createDirectory = useCallback(async (parent: string, name: string) => {
    const value = await api<{ path: string }>("/workspace/directory", "POST", { parent, name });
    return value.path;
  }, []);

  async function choose(path: string) {
    setBusy(true);
    setError("");
    try {
      await api("/workspace", "POST", { path });
      onSelected(path);
    } catch (failure) {
      setError(failureText(failure, t("无法切换工作区")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <DirectoryBrowser
        open={open}
        listDirectory={listDirectory}
        createDirectory={createDirectory}
        onOpen={(path) => void choose(path)}
        onClose={onClose}
        busy={busy}
        dismissible={dismissible}
        t={t}
      />
      {error && <p role="alert">{error}</p>}
    </>
  );
}
