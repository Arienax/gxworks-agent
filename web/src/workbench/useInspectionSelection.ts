import { useEffect, useState } from "react";
import type { Json } from "../api/client";
import type { IssueContext } from "../features/ProgramExplorer";

export function useInspectionSelection(pid: string, vid: string) {
  const [report, setReport] = useState<Json>(null);
  const [network, setNetwork] = useState<Record<string, Json> | null>(null);
  const [jumpAddress, setJumpAddress] = useState("");
  const [jumpVersion, setJumpVersion] = useState("");
  const [issueContext, setIssueContext] = useState<IssueContext & { versionId: string }>();
  useEffect(() => {
    setNetwork((old) => old?.version_id === vid ? old : null);
    setIssueContext((old) => old?.versionId === vid ? old : undefined);
    setReport(null);
  }, [pid, vid]);
  return { report, setReport, network, setNetwork, jumpAddress, setJumpAddress, jumpVersion, setJumpVersion, issueContext, setIssueContext };
}
