// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

const names: Record<string, string> = {
  create_global_labels: "建立全局标签表", edit_global_labels: "写入全局声明",
  edit_pou_labels: "写入局部声明和接口", create_structure: "建立结构类型",
  create_st_fb: "建立 ST 功能块", edit_st_fb: "修改 ST 功能块",
  create_st_program: "建立 ST 程序", edit_st_program: "修改 ST 程序",
  edit_fbd_graph: "修改图形连接",
  create_fbd_call: "建立 FBD 调用和连接", configure_project: "设置自动分配范围",
  compile: "执行本次全部编译", show_assignments: "显示本次编译的地址",
  save: "保存工程", reopen: "重新打开并核对",
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function endpoint(value: unknown): string {
  const end = record(value);
  return end?.node ? `${end.node}.${end.port}` : end?.wire ?
    `${end.wire} (${Array.isArray(end.point) ? end.point.join(", ") : ""})` : "";
}

export function NativeEngineeringPlan({ value, t }: { value: unknown; t: (text: string) => string }) {
  const plan = record(value);
  const steps = Array.isArray(plan?.commands) ? plan.commands : [];
  return <ol className="native-engineering-plan">{steps.map((item, index) => {
    const step = record(item);
    if (!step) return null;
    const operation = String(step.operation || "");
    const declarations = Array.isArray(step.declarations) ? step.declarations :
      Array.isArray(step.members) ? step.members : [];
    const arguments_ = record(step.arguments);
    const ranges = record(record(step.options)?.system_variable_ranges);
    return <li key={index}>
      <strong>{t(names[operation] || "工程操作")}</strong>
      {typeof step.source_name === "string" && operation !== "configure_project" &&
        <span> · {step.source_name}</span>}
      {typeof step.instance === "string" && <p>{String(step.definition || "")} · {step.instance}</p>}
      {typeof step.task === "string" && <p>{step.scope_mode === "existing_task" &&
        <>{t("加入现有任务")} · </>}{String(step.resource || "")} / {step.task}</p>}
      {typeof step.caller === "string" && <p>{step.caller}</p>}
      {declarations.length > 0 && <ul>{declarations.map((value, rowIndex) => {
        const row = record(value);
        return row && <li key={rowIndex}><code>{String(row.name || "")}</code>: {String(row.data_type || "")}
          {row.class_name || row.kind ? ` · ${row.class_name || row.kind}` : ""}
          {row.device ? ` · ${row.device}` : ""}
          {row.iec_address ? ` · ${row.iec_address}` : ""}
          {row.initial_value !== undefined ? ` = ${row.initial_value}` : ""}
          {row.comment ? ` · ${row.comment}` : ""}</li>;
      })}</ul>}
      {typeof step.st_text === "string" && <pre><code>{step.st_text}</code></pre>}
      {arguments_ && <ul>{Object.entries(arguments_).map(([name, symbol]) =>
        <li key={name}><code>{name}</code> → <code>{String(symbol)}</code></li>)}</ul>}
      {Array.isArray(step.actions) && <ul>{step.actions.map((value, actionIndex) => {
        const action = record(value);
        if (!action) return null;
        return <li key={actionIndex}>{action.action === "rebind_terminal" ?
          `${t("更新端子绑定")} ${action.node} → ${action.symbol}` : action.action === "delete_wire" ?
          `${t("删除连接线")} ${action.wire}` : action.action === "add_wire" ?
          `${t("连接端口")} ${endpoint(action.from)} → ${endpoint(action.to)}` : action.action === "delete_terminal" ?
          `${t("删除端子")} ${action.node}` : action.action === "move_node" ?
          `${t("移动图形对象")} ${action.node} → (${action.x}, ${action.y})` : t("图形操作")}
          {Array.isArray(action.via) && action.via.length > 0 &&
            ` · ${t("经过")} ${action.via.map(point => Array.isArray(point) ? `(${point.join(", ")})` : "").join(" → ")}`}
        </li>;
      })}</ul>}
      {ranges && <ul>{Object.entries(ranges).map(([name, range]) =>
        <li key={name}>{name}: {Array.isArray(range) ? range.join(" – ") : String(range)}</li>)}</ul>}
      {Array.isArray(step.queries) && <p>{step.queries.map(query => record(query)?.name)
        .filter(name => typeof name === "string").join("、")}</p>}
    </li>;
  })}</ol>;
}
