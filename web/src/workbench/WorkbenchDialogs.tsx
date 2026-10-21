import { Suspense, lazy } from "react";
import { Button, Modal } from "../components/ui";
import { DataView } from "../workbench/ResultViews";
import { ReportDialog, reportDialogFields } from "./ReportDialog";
import { SFCRequirementDialog, sFCRequirementDialogFields } from "./SFCRequirementDialog";
import { SettingsDialog, settingsDialogFields } from "./SettingsDialog";
import { workbenchDialogsFields } from "./dialogFields";
import type { WorkbenchModel } from "./useWorkbenchController";
import { pickView } from "./viewProps";
const NewProjectDialog = lazy(() =>
  import("../workbench/NewProjectDialog").then((module) => ({ default: module.NewProjectDialog })),
);
const GXWImportDialog = lazy(() =>
  import("./GXWImportDialog").then((module) => ({ default: module.GXWImportDialog })),
);
const FBDImport = lazy(() =>
  import("../features/FBDPanel").then((module) => ({ default: module.FBDImport })),
);
type Props = Pick<WorkbenchModel, typeof workbenchDialogsFields[number]>;
export function WorkbenchDialogs(props: Props) {
  const {
    busy,
    canWrite,
    confirmGXSend,
    creation,
    environment,
    guarded,
    gxSend,
    gxwImport,
    modal,
    pid,
    setGXSend,
    setModal,
    setPid,
    setProjectListRevision,
    setVid,
    showFBDProposal,
    t,
    vid,
  } = props;

  return (
    <>
      {modal === "gxw-import" && (
        <Suspense fallback={<p role="status">{t("正在读取…")}</p>}>
          <GXWImportDialog open model={gxwImport}
            enabled={canWrite || gxwImport.busy} t={t} />
        </Suspense>
      )}
      <Modal
        open={!!gxSend}
        onOpenChange={(open) => !open && setGXSend(null)}
        title={t("确认发送到 GX")}
        description={t("将覆盖 GX Works2 当前 MAIN 和相关软元件注释，已有 GXW 文件会自动备份。")}
      >
        <p>
          {t("本地项目")}：<strong>{gxSend?.projectName}</strong>
        </p>
        <p>
          {t("待发送版本")}：<strong>{gxSend?.versionId}</strong>
        </p>
        <div className="proposal-actions">
          <Button onClick={() => setGXSend(null)}>{t("取消")}</Button>
          <Button
            variant="primary"
            disabled={!canWrite}
            onClick={() => void guarded(confirmGXSend)}
          >
            {t("继续发送")}
          </Button>
        </div>
      </Modal>
      {modal === "new" && (
        <Suspense fallback={null}>
          <NewProjectDialog
            open={modal === "new"}
            onClose={() => setModal("")}
            creation={creation}
            t={t}
            busy={busy}
            guarded={guarded}
            onCreated={(p) => {
              setPid(p.id);
              setVid("");
              setModal("");
              setProjectListRevision((n) => n + 1);
            }}
          />
        </Suspense>
      )}
      <Modal
        open={modal === "fbd-import"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("导入 GXW 工程")}
      >
        <Suspense fallback={<p role="status">{t("正在读取…")}</p>}>
          <FBDImport
            key={`${pid}:${vid}`}
            pid={pid}
            vid={vid}
            disabled={!canWrite}
            onProposal={showFBDProposal}
            t={t}
          />
        </Suspense>
      </Modal>
      <SettingsDialog {...pickView(props, settingsDialogFields)} />
      <Modal
        open={modal === "environment"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("环境详情")}
      >
        <DataView value={environment} />
      </Modal>
      <ReportDialog {...pickView(props, reportDialogFields)} />
      <SFCRequirementDialog {...pickView(props, sFCRequirementDialogFields)} />
    </>
  );
}
