// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import type { Project, WorkbenchConnection } from "../api/client";
import { api } from "../api/client";
import { LatestRead, reconcileById } from "../lifecycle/requests";

/** Own project selection, URL binding and all reads of the selected project. */
export function useProjectWorkspace(connection: WorkbenchConnection | null, setError: (message: string) => void) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [pid, setPid] = useState(new URLSearchParams(location.search).get("project") || "");
  const [vid, setVid] = useState("");
  const [loading, setLoading] = useState(false);
  const [projectListRevision, setProjectListRevision] = useState(0);
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  const scope = connection?.path || null;
  const projectsReady = scope !== null && scope === loadedPath;
  const owner = useRef({ pid, scope, epoch: 0 });
  const projectRead = useRef(new LatestRead());
  const listRead = useRef(new LatestRead());
  const adoptedVersion = useRef<{ pid: string; vid: string } | null>(null);
  const adoptedBinding = useRef<WorkbenchConnection | null>(null);
  const adoptedDetail = useRef<{ pid: string; connection: WorkbenchConnection | null } | null>(null);
  if (owner.current.pid !== pid || owner.current.scope !== scope) {
    owner.current = { pid, scope, epoch: owner.current.epoch + 1 };
    projectRead.current.cancel();
  }
  const capture = () => owner.current.epoch;
  const isProject = (id: string | null | undefined) => owner.current.pid === id;
  const isCurrent = (epoch: number, id = pid) => isProject(id) && owner.current.epoch === epoch;
  const cancelRead = () => projectRead.current.cancel();
  useEffect(() => () => { owner.current.epoch++; cancelRead(); listRead.current.cancel(); }, []);
  useEffect(() => {
    if (!connection?.selected) { setProjects([]); setLoadedPath(null); return; }
    if (adoptedBinding.current === connection) {
      adoptedBinding.current = null;
      return;
    }
    const read = listRead.current.begin();
    const selectionOwner = owner.current;
    void api<{ projects: Project[] }>("/projects", "GET", undefined, { signal: read.signal })
      .then(({ projects: value }) => {
        if (!read.current()) return;
        setProjects((old) => reconcileById(old, value));
        setLoadedPath(scope);
        setPid((selected) => owner.current !== selectionOwner ? selected
          : value.some((item) => item.id === selected) ? selected : value[0]?.id || "");
      })
      .catch((error: Error) => { if (read.current()) setError(error.message); });
    return read.cancel;
  }, [connection, projectListRevision]);
  useEffect(() => {
    setVid(adoptedVersion.current?.pid === pid ? adoptedVersion.current.vid : "");
    adoptedVersion.current = null;
  }, [pid]);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (pid) url.searchParams.set("project", pid);
    else url.searchParams.delete("project");
    history.replaceState(null, "", url);
    if (!pid || !connection?.selected) { setProject(null); setLoading(false); return; }
    if (!projectsReady) return;
    if (adoptedDetail.current?.pid === pid && adoptedDetail.current.connection === connection) {
      adoptedDetail.current = null;
      return;
    }
    setLoading(true);
    void reloadProjectSilently(pid).catch((error: Error) => { if (isProject(pid)) setError(error.message); });
    return cancelRead;
  }, [pid, connection, projectsReady]);
  async function reloadProjectSilently(targetPid: string | null | undefined = pid) {
    const epoch = capture();
    if (!targetPid || !isProject(targetPid)) return;
    const read = projectRead.current.begin();
    try {
      const fresh = await api<Project>("/projects/" + targetPid, "GET", undefined, { signal: read.signal });
      if (!read.current() || !isCurrent(epoch, targetPid) || fresh.id !== targetPid) return;
      // Reads follow engineering actions, so adopt the authoritative snapshot
      // without deep-comparing the project's complete version history.
      setProject(fresh);
      setLoading(false);
      setProjects((old) => old.map((item) => item.id === fresh.id ? {
        ...item,
        name: fresh.name, plc_model: fresh.plc_model, target_mode: fresh.target_mode,
        active_version_id: fresh.active_version_id, version_count: fresh.version_count
      } : item));
      setVid((selected) => fresh.versions?.some((item) => item.id === selected)
        ? selected : fresh.active_version_id || fresh.versions?.[0]?.id || "");
      return fresh;
    } catch (error) {
      if (!read.current() || !isCurrent(epoch, targetPid)) return;
      setLoading(false);
      throw error;
    }
  }
  function adoptSelection(fresh: Project | null, versionId: string | null, binding = connection) {
    const selected = versionId || fresh?.active_version_id || fresh?.versions?.[0]?.id || "";
    const nextPid = fresh?.id || "", nextScope = binding?.path || null;
    cancelRead();
    if (owner.current.pid !== nextPid || owner.current.scope !== nextScope)
      owner.current = { pid: nextPid, scope: nextScope, epoch: owner.current.epoch + 1 };
    if (nextPid !== pid || binding !== connection) {
      adoptedDetail.current = { pid: nextPid, connection: binding };
      adoptedVersion.current = { pid: nextPid, vid: selected };
    }
    setProject(fresh);
    setPid(nextPid);
    setVid(selected);
    setLoading(false);
  }
  function adoptProject(fresh: Project, versionId: string | null) {
    setProjects((old) => old.some((item) => item.id === fresh.id)
      ? old.map((item) => item.id === fresh.id ? fresh : item) : [fresh, ...old]);
    adoptSelection(fresh, versionId);
    setProjectListRevision((value) => value + 1);
  }
  async function selectProject(projectId: string) {
    if (projectId === pid) return true;
    const epoch = capture();
    const read = projectRead.current.begin();
    try {
      const fresh = await api<Project>(`/projects/${encodeURIComponent(projectId)}`, "GET", undefined, { signal: read.signal });
      if (!read.current() || !isCurrent(epoch) || fresh.id !== projectId) return false;
      adoptSelection(fresh, null);
      return true;
    } catch (error) {
      if (read.current() && isCurrent(epoch)) throw error;
      return false;
    }
  }
  async function readBinding(binding: WorkbenchConnection, preferredId?: string) {
    const projects = binding.selected ? (await api<{ projects: Project[] }>("/projects")).projects : [];
    const selected = projects.find(item => item.id === preferredId) || projects[0];
    const project = selected ? await api<Project>(`/projects/${encodeURIComponent(selected.id)}`) : null;
    return { projects, project };
  }
  function adoptBinding(binding: WorkbenchConnection, snapshot?: Awaited<ReturnType<typeof readBinding>>) {
    listRead.current.cancel();
    adoptedBinding.current = snapshot ? binding : null;
    setProjects(snapshot?.projects || []);
    setLoadedPath(snapshot ? binding.path || null : null);
    adoptSelection(snapshot?.project || null, null, binding);
  }
  async function deleteProject() {
    const deletedId = pid;
    const epoch = capture();
    await api<{ deleted: boolean }>(`/projects/${encodeURIComponent(deletedId)}`, "DELETE");
    if (owner.current.scope !== scope) return;
    listRead.current.cancel();
    try {
      if (!isCurrent(epoch)) return;
      const next = projects.find(item => item.id !== deletedId);
      const fresh = next ? await api<Project>(`/projects/${encodeURIComponent(next.id)}`) : null;
      if (isCurrent(epoch)) adoptSelection(fresh, null);
    } catch (error) {
      if (isCurrent(epoch)) adoptSelection(null, null);
      throw error;
    } finally {
      if (owner.current.scope === scope) setProjects(old => old.filter(item => item.id !== deletedId));
    }
  }
  const version = project?.id === pid ? project.versions?.find((item) => item.id === vid) : undefined;
  const hasSavedVersions = project?.id === pid && ((project.version_count || 0) > 0 || !!project.versions?.length);
  return {
    projects, project, setProject, pid, vid, setVid, loading, version,
    hasSavedVersions, reloadProjectSilently, adoptProject, selectProject,
    readBinding, adoptBinding, deleteProject, capture, isCurrent, isProject, cancelRead
  };
}
export type ProjectWorkspace = ReturnType<typeof useProjectWorkspace>;
