import type { Job } from "../api/client";
import { Button } from "../components/ui";
import { InteractionTraceExport } from "./InteractionTraceExport";

const reasons: Record<string, string> = {
  latin_prose: "回复含有不符合所选语言的英文说明",
  japanese_script: "回复含有不符合所选语言的日文",
  non_english_script: "回复含有不符合所选语言的文字",
  unsupported_script: "回复含有不支持的语言文字",
  ambiguous_han_only: "无法确认回复是否为所选语言",
  invalid_json_object: "回复格式不完整或不是有效数据",
  invalid_prose_field: "说明字段格式错误",
  invalid_code_field: "程序字段格式错误",
  invalid_shared_input: "公共串联输入中不能包含并联块；请在分支输入中表达并联逻辑。",
  invalid_ladder_structure: "梯形图结构或指令编码不符合协议。",
  field_too_long: "文本字段超过协议长度限制；请缩短或省略该说明。",
  repair_base_invalid: "缺少可合并的完整候选，修复必须返回完整程序。",
  repair_identity_invalid: "梯级编号重复或无效，无法安全合并修复。",
  repair_shape_invalid: "修复响应的 JSON 结构不符合协议。",
  repair_scope_violation: "修复试图删除、新增或重排梯级，已阻止。",
  repair_no_progress: "修复未改变失败候选。",
};

const errorMessages: Record<string, string> = {
  model_timeout: "模型服务请求超时，请稍后重试。",
  model_authentication: "模型服务认证失败，请检查 API Key 和访问权限。",
  model_rate_limit: "模型服务请求过于频繁，请稍后重试。",
  model_invalid_request: "模型服务拒绝了请求，请检查模型配置和输入。",
  model_unavailable: "模型服务暂时不可用，请检查连接或稍后重试。",
  model_protocol: "模型未返回有效候选结果，请重试或更换模型。",
  model_provider_error: "模型服务调用失败，请检查配置或稍后重试。",
  generation_failed: "生成流程失败，确认规格和已有版本未被修改。",
};

function repairableGenerationFailure(job: Job) {
  const rows = job.error_details?.violations || [];
  if (rows.length !== 1) return false;
  const violation = rows[0];
  if (violation.reason === "invalid_json_object" || violation.reason === "invalid_shared_input") return true;
  const leaf = violation.path.split(".").at(-1) || "";
  if (violation.reason === "field_too_long")
    return leaf === "label" || leaf === "debug_note" || leaf.startsWith("comment[");
  return violation.reason === "invalid_ladder_structure" &&
    (leaf === "branch_id" || leaf === "y_offset_level");
}

function FailureMessage({ job, t }: { job: Job; t: (key: string) => string }) {
  if (!job.error_code) return null;
  if (job.error_code === "generation_validation_failed") return <div className="job-failure" role="alert">
    <p className="error-text">{t("梯形图候选结构不符合协议，未接受任何程序。")}</p>
    {job.error_details?.violations?.map((violation, i) => <p key={i}>
      <code>{violation.path}</code><br /><span>{t(reasons[violation.reason] || "回复内容不符合要求")}</span>
    </p>)}
    <p className="muted">{t(repairableGenerationFailure(job)
      ? "系统没有自动再次调用模型。可由你确认后仅修复当前候选的结构问题。"
      : "该错误涉及指令、地址或参数语义，系统不会猜测修复；请重新生成候选或手动修改。")}</p>
  </div>;
  if (job.error_code === "change_scope_violation") return <p className="error-text" role="alert">{t("候选超出允许修改的范围。请查看任务详情，调整范围或重新生成。")}</p>;
  if (job.error_code !== "response_rejected") return <p className="error-text">{t(errorMessages[job.error_code] || job.error_code)}</p>;

  return <div className="job-failure" role="alert">
    <p className="error-text">{t("模型回复未通过检查，请重试。")}</p>
    {job.error_details?.violations?.length ? <ul>
      {job.error_details.violations.map((violation, i) => <li key={i}>
        {t(violation.path.startsWith("reasoning") ? "思考内容" : violation.path.startsWith("tool_calls") ? "工具参数" : "回复内容")}：{t(reasons[violation.reason] || "回复内容不符合要求")}
      </li>)}
    </ul> : <p className="muted">{t("此历史任务未记录具体原因；新任务将显示检查详情。")}</p>}
  </div>;
}

export function JobFailure({ job, busy, onRepair, t }: {
  job: Job;
  busy?: boolean;
  onRepair?: () => void;
  t: (key: string) => string;
}) {
  if (!job.error_code) return null;
  const repairable = job.kind === "generation" && job.error_code === "generation_validation_failed" && repairableGenerationFailure(job) && !!onRepair;
  return <section>
    <FailureMessage job={job} t={t} />
    {repairable && <Button disabled={busy} onClick={onRepair}>{t("让 AI 修复")}</Button>}
    <InteractionTraceExport jobId={job.id} t={t} />
  </section>;
}
