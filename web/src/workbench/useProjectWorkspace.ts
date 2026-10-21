import { useEffect, useRef, useState } from "react";
import type { Project, Session } from "../api/client";
import { api } from "../api/client";
import { LatestRead, reconcileById } from "../lifecycle/requests";

/** Own project selection, URL binding and all reads of the selected project. */
export function useProjectWorkspace(session: Session | null, setError: (message: string) => void) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [pid, setPid] = useState(new URLSearchParams(location.search).get("project") || "");
  const [vid, setVid] = useState("");
  const [loading, setLoading] = useState(false);
  const [projectListRevision, setProjectListRevision] = useState(0);
  const owner = useRef({ pid, epoch: 0 });
  const projectRead = useRef(new LatestRead());
  const adoptedVersion = useRef<{ pid: string; vid: string } | null>(null);
  if (owner.current.pid !== pid) {
    owner.current = { pid, epoch: owner.current.epoch + 1 };
    projectRead.current.cancel();
  }
  const capture = () => owner.current.epoch;
  const isProject = (id: string | null | undefined) => owner.current.pid === id;
  const isCurrent = (epoch: number, id = pid) => isProject(id) && owner.current.epoch === epoch;
  const cancelRead = () => projectRead.current.cancel();
  useEffect(() => () => { owner.current.epoch++; cancelRead(); }, []);
  useEffect(() => {
    if (!session) { setProjects([]); return; }
    const controller = new AbortController();
    const selectionOwner = owner.current;
    void api<{ projects: Project[] }>("/projects", "GET", undefined, { signal: controller.signal })
      .then(({ projects: value }) => {
        if (controller.signal.aborted || owner.current !== selectionOwner) return;
        setProjects((old) => reconcileById(old, value));
        setPid((selected) => owner.current !== selectionOwner ? selected
          : value.some((item) => item.id === selected) ? selected : value[0]?.id || "");
      })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [session, projectListRevision]);
  useEffect(() => {
    setVid(adoptedVersion.current?.pid === pid ? adoptedVersion.current.vid : "");
    adoptedVersion.current = null;
  }, [pid]);
  useEffect(() => {
    if (!pid || !session) { setProject(null); setLoading(false); return; }
    setLoading(true);
    history.replaceState(null, "", "?project=" + encodeURIComponent(pid));
    void reloadProjectSilently(pid).catch((error: Error) => { if (isProject(pid)) setError(error.message); });
    return cancelRead;
  }, [pid, session]);
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
  function adoptProject(fresh: Project, versionId: string | null) {
    const selected = versionId || fresh.active_version_id || fresh.versions?.[0]?.id || "";
    cancelRead();
    if (owner.current.pid !== fresh.id) owner.current = { pid: fresh.id, epoch: owner.current.epoch + 1 };
    adoptedVersion.current = fresh.id !== pid ? { pid: fresh.id, vid: selected } : null;
    setProjects((old) => old.some((item) => item.id === fresh.id)
      ? old.map((item) => item.id === fresh.id ? fresh : item) : [fresh, ...old]);
    setProject(fresh);
    setPid(fresh.id);
    setVid(selected);
    setLoading(false);
    setProjectListRevision((value) => value + 1);
  }
  const version = project?.id === pid ? project.versions?.find((item) => item.id === vid) : undefined;
  const hasSavedVersions = project?.id === pid && ((project.version_count || 0) > 0 || !!project.versions?.length);
  return {
    projects, project, setProject, pid, setPid, vid, setVid, loading, version,
    hasSavedVersions, setProjectListRevision, reloadProjectSilently, adoptProject, capture, isCurrent, isProject, cancelRead
  };
}
export type ProjectWorkspace = ReturnType<typeof useProjectWorkspace>;
