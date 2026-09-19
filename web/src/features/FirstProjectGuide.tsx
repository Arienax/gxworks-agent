import { useState } from "react";
import { Button } from "../components/ui";
import "./explorer.css";

export function FirstProjectGuide({example,t,hasSpec,disabled,onExample,onSpec,onGenerate}:{example:string;t:(s:string)=>string;hasSpec:boolean;disabled:boolean;onExample:(text:string)=>void;onSpec:()=>void;onGenerate:()=>void}) {
  const [compact,setCompact]=useState(()=>localStorage.getItem("gx.guide.compact")==="true");
  if(compact)return <div className="onboarding"><h2>{t("工程中还没有程序")}</h2><p>{t("描述控制需求，确认规格后生成第一个程序。")}</p><Button onClick={()=>{localStorage.removeItem("gx.guide.compact");setCompact(false);}}>{t("显示入门引导")}</Button></div>;
  return <div className="onboarding"><h2>{t("完成第一个工程")}</h2><p>{t("从一个电机启停练习开始，把需求、程序和测试联系起来。")}</p>
    <ol><li><strong>{t("说明输入和行为")}</strong><p>{t("输入是按钮或传感器，输出是希望控制的动作；同时说明停止、复位和上电时的状态。")}</p><Button disabled={disabled||!example} onClick={()=>onExample(t(example))}>{t("填入启停示例")}</Button><p className="muted">{t("示例会填入 Agent 输入框，发送后开始需求分析。")}</p></li>
      <li><strong>{t("确认规格")}</strong><p>{t("检查地址分配和运行条件；确认后，规格会作为生成与验证的依据。")}</p><Button disabled={disabled} onClick={onSpec}>{t("编辑并确认规格")}</Button></li>
      <li><strong>{t("检查保存的程序")}</strong><p>{t("生成结果通过结构校验后自动保存。点击网络与地址理解程序，查看变更摘要；需要时可切回历史版本。")}</p><Button disabled={disabled||!hasSpec} onClick={onGenerate}>{t("生成第一个程序")}</Button></li>
      <li><strong>{t("设计并核对测试")}</strong><p>{t("在仿真页设置输入时刻和期望输出。执行遵循工作区审批设置，环境不可用时会保留未验证状态。")}</p></li></ol>
    <Button variant="ghost" onClick={()=>{localStorage.setItem("gx.guide.compact","true");setCompact(true);}}>{t("以后收起引导")}</Button>
  </div>;
}
