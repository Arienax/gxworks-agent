import { Modal } from "../components/ui";
import { IssueCards } from "../features/ProgramExplorer";
import { DataView } from "../workbench/ResultViews";
import type { WorkbenchModel } from "./useWorkbenchController";


export const reportDialogFields = [
  "canWrite",
  "modal",
  "pid",
  "report",
  "setIssueContext",
  "setJumpAddress",
  "setJumpVersion",
  "setModal",
  "setNetwork",
  "setTab",
  "setVid",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof reportDialogFields[number]>;
export function ReportDialog(props: Props) {
  const {
    canWrite,
    modal,
    pid,
    report,
    setIssueContext,
    setJumpAddress,
    setJumpVersion,
    setModal,
    setNetwork,
    setTab,
    setVid,
    t,
  } = props;

  return (
    <Modal
      open={modal === "report"}
      onOpenChange={(v) => !v && setModal("")}
      title={t("检查报告详情")}
    >
      {modal === "report" &&
        report &&
        typeof report === "object" &&
        !Array.isArray(report) &&
        Array.isArray(report.findings) &&
        report.report_id &&
        report.base_version_id ? (
        <IssueCards
          pid={pid}
          vid={String(report.base_version_id)}
          reportId={String(report.report_id)}
          readOnly={!canWrite}
          t={t}
          onNetwork={(id, address) => {


            setVid(String(report.base_version_id));
            setNetwork({ id, version_id: String(report.base_version_id) });
            setJumpAddress(address || "");
            setJumpVersion(String(report.base_version_id));
            setTab("ladder");
            setModal("");
          }}
          onTest={(issue) => {


            setVid(String(report.base_version_id));
            setIssueContext({ ...issue, versionId: String(report.base_version_id) });
            setTab("simulation");
            setModal("");
          }}
        />
      ) : (
        <DataView value={report} />
      )}
    </Modal>
  );
}
