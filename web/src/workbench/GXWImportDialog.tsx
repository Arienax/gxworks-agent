import type { GXWSourceSummary } from "../api/gxw";
import { Button, Modal } from "../components/ui";
import type { GXWImportModel } from "./useGXWImport";

export function GXWImportDialog({ open, model, enabled, t }: {
  open: boolean;
  model: GXWImportModel;
  enabled: boolean;
  t: (message: string) => string;
}) {
  const { inspection, program, phase } = model;
  const selected = inspection?.programs.find((source) => source.source_name === program);
  const label = (source: GXWSourceSummary) =>
    `${source.name} · ${source.language === "ladder" ? t("梯形图") : source.language?.toUpperCase() || t("未知语言")}`;
  const programs = inspection?.programs.filter((source) => source.unit_kind === "program") || [];
  const blocks = inspection?.programs.filter((source) => source.unit_kind === "function_block") || [];
  const functions = inspection?.programs.filter((source) => source.unit_kind === "function") || [];
  return <Modal open={open} onOpenChange={(value) => !value && model.close()} title={t("导入 GXW 工程")}
    description={t("读取工程文件并保存到本地项目，不需要启动 GX Works2。")}>
    <div className="form gxw-import">
      {model.filename && <p className="gxw-filename">{model.filename}</p>}
      {model.busy && <p role="status">{t(phase === "importing" ? "正在导入工程…" : "正在检查工程…")}</p>}
      {inspection && <>
        <p className="muted">CPU：{inspection.cpu_menu}</p>
        <label>{t("选择程序、功能块或函数")}
          <select aria-label={t("选择程序、功能块或函数")} value={program}
            disabled={model.busy || !enabled} onChange={(event) => void model.selectProgram(event.target.value)}>
            <option value="">{t("请选择要导入的对象")}</option>
            {programs.length > 0 && <optgroup label={t("程序")}>
              {programs.map((source) => <option key={source.source_name} value={source.source_name} disabled={!source.readable}>{label(source)}</option>)}
            </optgroup>}
            {blocks.length > 0 && <optgroup label={t("功能块")}>
              {blocks.map((source) => <option key={source.source_name} value={source.source_name} disabled={!source.readable}>{label(source)}</option>)}
            </optgroup>}
            {functions.length > 0 && <optgroup label={t("函数")}>
              {functions.map((source) => <option key={source.source_name} value={source.source_name} disabled={!source.readable}>{label(source)}</option>)}
            </optgroup>}
          </select>
        </label>
        {selected && <p className="muted">{t(selected.offline_editable ? "所选对象支持离线修改。" : "所选对象的修改能力受当前文件内容限制。")}</p>}
        {selected?.reason && <details><summary>{t("读取与修改限制")}</summary><p>{selected.reason}</p></details>}
      </>}
      {model.error && <p role="alert" className="gxw-import-error">{model.error}</p>}
      <div className="proposal-actions">
        <Button disabled={phase === "importing"} onClick={model.close}>{t("取消")}</Button>
        <Button variant="primary" disabled={!enabled || model.busy || !selected?.readable}
          onClick={() => void model.save()}>{t("导入工程")}</Button>
      </div>
    </div>
  </Modal>;
}
