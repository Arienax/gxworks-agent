import { usePanelVisible } from "../lifecycle/visibility";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Button } from "../components/ui";
import "./hardware.css";

type Target = { logical_station: number; target_label: string; addresses: string[]; plc_model: string; ttl_seconds: number };
type Audit = { sequence: number; created_at: string; action: string; addresses?: string[]; values?: Record<string, number>; reason?: string };
type Session = { id: string; status: string; target: Target; expires_at?: number; owned_by_current_operator: boolean; audit: Audit[]; read_count: number };
type State = { default_enabled: false; reader: { available: boolean; message: string }; sessions: Session[] };
type Observation = { values: Record<string, number>; target_label: string; logical_station: number; observed_at: string; replayed: boolean };
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const actionText: Record<string,string> = { proposed: "提出授权", approved: "确认只读授权", revoked: "撤销授权", read_requested: "发起手动读取", read_completed: "读取完成", read_failed: "读取失败", read_denied: "读取被拒绝" };

export function HardwarePanel({pid,vid,readOnly,t}:{pid:string;vid:string;readOnly:boolean;t:(text:string)=>string}) {
  const path = `/projects/${encodeURIComponent(pid)}/versions/${encodeURIComponent(vid)}/hardware`;
  const [data,setData]=useState<State|null>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const [station,setStation]=useState(0),[label,setLabel]=useState(""),[addresses,setAddresses]=useState(""),[ttl,setTtl]=useState(60);
  const [confirm,setConfirm]=useState<Record<string,boolean>>({}),[observation,setObservation]=useState<Observation|null>(null);
  const [now,setNow]=useState(Date.now());
  const epoch=useRef(0);
  useEffect(()=>{const stamp=++epoch.current;setData(null);setObservation(null);setConfirm({});setError("");setBusy(false);setLabel("");setAddresses("");
    api<State>(path).then(value=>{if(stamp===epoch.current)setData(value);}).catch(error=>{if(stamp===epoch.current)setError(errorText(error));});
    return()=>{epoch.current++;};
  },[path]);
  const visible=usePanelVisible();
  useEffect(()=>{if(!visible)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[visible]);
  async function act(action:()=>Promise<unknown>, onResult?:(value:unknown)=>void) {
    if(busy||readOnly)return;
    const stamp=epoch.current;setBusy(true);setError("");
    try {
      const value=await action();
      if(stamp!==epoch.current)return;
      onResult?.(value);
      const refreshed=await api<State>(path);
      if(stamp===epoch.current)setData(refreshed);
    } catch(error) {
      if(stamp===epoch.current){setError(errorText(error));api<State>(path).then(value=>{if(stamp===epoch.current)setData(value);}).catch(()=>{});}
    } finally {if(stamp===epoch.current)setBusy(false);}
  }
  const disabled=readOnly||busy;
  return <section className="hardware-panel" aria-label={t("真实 PLC 只读接入")}>
    <header><h2>{t("真实 PLC · 只读接入")}</h2><p>{t("默认关闭。授权仅允许手动读取，不执行程序下载、强制、写入或运行状态切换。")}</p></header>
    <p className="hardware-note">{t("每次读取会短暂打开 MX 逻辑站并立即关闭。逻辑站与现场设备的对应关系由操作员在 Communication Setup Utility 中核对；这里不会自动确认现场设备身份。")}</p>
    {error&&<p role="alert" className="hardware-error">{error}</p>}
    {!data&&!error&&<p>{t("正在读取本地授权状态…")}</p>}
    {data&&<>
      <p>{t(data.reader.message)}</p>
      <details><summary>{t("配置一次只读授权")}</summary><fieldset disabled={disabled||!data.reader.available}>
        <label>{t("已配置的 MX 逻辑站号")}<input type="number" min={0} max={1023} value={station} onChange={event=>setStation(Number(event.target.value))}/></label>
        <label>{t("现场设备名称与位置")}<input maxLength={160} placeholder={t("例如：1 号产线包装机 / 电柜 A")} value={label} onChange={event=>setLabel(event.target.value)}/></label>
        <label>{t("允许读取的地址")}<input value={addresses} onChange={event=>setAddresses(event.target.value)} placeholder="X0, Y0, M0, D0"/></label>
        <small>{t("1 至 64 个单独地址。T 和 C 读取当前计数值，不表示完成位。")}</small>
        <label>{t("授权有效时间（秒）")}<input type="number" min={30} max={300} value={ttl} onChange={event=>setTtl(Number(event.target.value))}/></label>
        <Button disabled={disabled||!data.reader.available||!label.trim()||!addresses.trim()} onClick={()=>void act(()=>api(`${path}/sessions`,"POST",{
          logical_station:station,target_label:label.trim(),addresses:addresses.trim().toUpperCase().split(/[\s,，]+/).filter(Boolean),ttl_seconds:ttl,
        }))}>{t("生成待确认授权")}</Button>
      </fieldset></details>
      {!data.sessions.length&&<p className="muted">{t("此版本没有只读授权，也没有进行任何设备连接。")}</p>}
      {data.sessions.map(session=>{
        const seconds=session.expires_at?Math.max(0,Math.ceil(session.expires_at-now/1000)):0;
        const state=session.status==="active"&&seconds===0?"expired":session.status;
        const active=state==="active";
        const mayAct=!disabled&&session.owned_by_current_operator;
        return <article key={session.id} className="hardware-session"><header><strong>{session.target.target_label}</strong><span>{t(({pending:"待确认",active:"只读授权有效",expired:"授权已过期",revoked:"授权已撤销"} as Record<string,string>)[state]||state)}{active?` · ${seconds}s`:""}</span></header>
          <p>{t("逻辑站")} {session.target.logical_station} · {session.target.plc_model} · {session.target.ttl_seconds}s</p><p><strong>{t("地址白名单")}</strong>：{session.target.addresses.join(", ")}</p>
          {!session.owned_by_current_operator&&<p>{t("此会话由另一操作员登录创建，可查看历史；操作需要该登录。")}</p>}
          {state==="pending"&&<><label className="hardware-confirm"><input type="checkbox" disabled={!mayAct} checked={!!confirm[session.id]} onChange={event=>setConfirm({...confirm,[session.id]:event.target.checked})}/>{t("我已在 MX 配置中核对该逻辑站对应上方现场设备，并确认只读取列出的地址。")}</label>
            <Button disabled={!mayAct||!confirm[session.id]} onClick={()=>void act(()=>api(`${path}/sessions/${session.id}/approve`,"POST",{
              logical_station:session.target.logical_station,target_label:session.target.target_label,addresses:session.target.addresses,
              ttl_seconds:session.target.ttl_seconds,mapping_confirmed:true,
            }))}>{t("确认并开启限时只读授权")}</Button></>}
          {active&&<Button disabled={!mayAct||session.read_count>=30} onClick={()=>void act(()=>api<Observation>(`${path}/sessions/${session.id}/read`,"POST",{
            addresses:session.target.addresses,request_id:`read_${crypto.randomUUID()}`,
          }),value=>setObservation(value as Observation))}>{t("手动读取一次")}</Button>}
          {(active||state==="pending")&&<Button variant="ghost" disabled={!mayAct} onClick={()=>void act(()=>api(`${path}/sessions/${session.id}/revoke`,"POST"))}>{t("撤销授权")}</Button>}
          <details><summary>{t("操作审计")} · {session.audit.length}</summary><ol>{session.audit.map(event=><li key={event.sequence}><time>{new Date(event.created_at).toLocaleString()}</time> · {t(actionText[event.action]||event.action)}{event.addresses&&` · ${event.addresses.join(", ")}`}{event.reason&&<p>{event.reason}</p>}{event.values&&<pre>{Object.entries(event.values).map(([address,value])=>`${address}: ${value}`).join("\n")}</pre>}</li>)}</ol></details>
        </article>;
      })}
    </>}
    {observation&&<section className="hardware-observation"><h3>{t("最近一次手动读取")}</h3><p>{observation.target_label} · {t("逻辑站")} {observation.logical_station} · {new Date(observation.observed_at).toLocaleString()}</p><p className="muted">{t("这是该时刻的观测快照，不会自动刷新，也不代表本地程序已经下载到设备。")}</p><table><thead><tr><th>{t("地址")}</th><th>{t("观测值")}</th></tr></thead><tbody>{Object.entries(observation.values).map(([address,value])=><tr key={address}><td>{address}</td><td>{value}</td></tr>)}</tbody></table></section>}
  </section>;
}
