import { Button } from "../components/ui";
import "./explorer.css";

export function FirstProjectGuide({example,t,hasSpec,disabled,onExample,onSpec,onGenerate}:{example:string;t:(s:string)=>string;hasSpec:boolean;disabled:boolean;onExample:(text:string)=>void;onSpec:()=>void;onGenerate:()=>void}) {
  return <div className="onboarding"><h2>{t("创建第一个程序")}</h2>
    {!hasSpec && <p>{t("说明控制对象、PLC 型号、I/O 点位与接线、动作和复位要求。")}</p>}
    <div className="form-actions">
      {hasSpec ? <><Button variant="primary" disabled={disabled} onClick={onGenerate}>{t("生成程序")}</Button>
        <Button disabled={disabled} onClick={onSpec}>{t("查看需求")}</Button></>
        : example && <Button disabled={disabled} onClick={()=>onExample(t(example))}>{t("填入启停示例")}</Button>}
    </div>
  </div>;
}
