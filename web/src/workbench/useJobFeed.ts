import type { SetStateAction } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Job, JobEvent, Json, Proposal, Session } from "../api/client";
import { activeJob, api } from "../api/client";
import { reconcileByRevision } from "../lifecycle/requests";
import { usePolling } from "../lifecycle/usePolling";

export function useJobFeed(
  pid: string,
  session: Session | null,
  setError: (error: string) => void,
) {
  const activeProjectRef = useRef(pid);
  activeProjectRef.current = pid;
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, storeJobId] = useState("");
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  // An explicit "new task" keeps the conversation empty instead of letting the
  // next poll fall back to the latest job. Any explicit selection releases it.
  const draftingNew = useRef(false);
  const setJobId = useCallback((value: SetStateAction<string>) => {
    draftingNew.current = false;
    storeJobId(value);
  }, []);
  const startNewTask = useCallback(() => {
    draftingNew.current = true;
    storeJobId("");
  }, []);
  useEffect(() => {
    draftingNew.current = false;
    setJobs([]);
    storeJobId("");
    setEvents([]);
    setProposals([]);
  }, [pid]);
  const jobsPolling = usePolling(
    session ? pid : null,
    async (signal) => {
      const query = pid ? `?project_id=${encodeURIComponent(pid)}` : "";
      const [j, p] = await Promise.all([
        api<{ jobs: Job[] }>(`/jobs${query}`, "GET", undefined, { signal }),
        api<{ proposals: Proposal[] }>(`/proposals${query}`, "GET", undefined, { signal }),
      ]);
      return { jobs: j.jobs, proposals: p.proposals };
    },
    (value) => {
      setJobs((old) => reconcileByRevision(old, value.jobs));
      setProposals((old) => reconcileByRevision(old, value.proposals));
      storeJobId((old) =>
        value.jobs.some((item) => item.id === old)
          ? old
          : draftingNew.current ? "" : value.jobs[0]?.id || "",
      );
    },
    (e) => setError(String(e instanceof Error ? e.message : e)),
    2500,
  );

  useEffect(() => {
    if (!jobId || !session) {
      setEvents([]);
      return;
    }
    setEvents([]);
    let sequence = 0;
    let stopped = false;
    const stream = new EventSource(`/api/jobs/${jobId}/events`);
    let frame = 0;
    let pendingEvents: JobEvent[] = [];
    const flushEvents = () => {
      frame = 0;
      if (stopped || !pendingEvents.length) return;
      const batch = pendingEvents;
      pendingEvents = [];
      setEvents((old) => [...old, ...batch]);
    };
    stream.onmessage = (event) => {
      if (stopped) return;
      let value: JobEvent;
      try {
        value = JSON.parse(event.data) as JobEvent;
      } catch {
        return;
      } // A malformed frame must not break the following event.
      if (value.job_id !== jobId || value.project_id !== activeProjectRef.current) return;
      if (value.sequence <= sequence) return;
      sequence = value.sequence;
      pendingEvents.push(value);
      if (!frame) frame = requestAnimationFrame(flushEvents);
      if (
        ["running", "completed", "failed", "cancelled", "interrupted"].includes(value.event_type)
      ) {
        jobsPolling.current?.refresh(); // Discard snapshots started before this event.
        setJobs((old) =>
          old.map((job) =>
            job.id === value.job_id
              ? (!activeJob(job) || job.status === "cancelling") && value.event_type === "running"
                ? job
                : {
                  ...job,
                  revision: null,
                  status: value.event_type,
                  ...(value.event_type === "failed"
                    ? {
                      error_code: value.payload?.error_code as Job["error_code"],
                      error_details: value.payload?.error_details as Job["error_details"],
                    }
                    : {}),
                  ...(value.payload?.result
                    ? { result: value.payload.result as Record<string, Json> }
                    : {}),
                }
              : job,
          ),
        );
      }
      if (["completed", "failed", "cancelled", "interrupted"].includes(value.event_type)) {
        stream.close();
        cancelAnimationFrame(frame);
        flushEvents();
      }
    };
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      pendingEvents = [];
      stream.close();
    };
  }, [jobId, session, pid]);

  return {
    jobs,
    setJobs,
    jobId,
    setJobId,
    startNewTask,
    events,
    setEvents,
    proposals,
    setProposals,
    jobsPolling,
  };
}
