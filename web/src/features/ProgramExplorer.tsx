import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useResource } from "../lifecycle/useResource";
import { Button } from "../components/ui";
import { ChangeSummary } from "./ChangeSummary";
import "./explorer.css";

export type ExploreNetwork = {id:string; comment:string; reads:string[]; writes:string[]; instructions: {op?:string; args?:string[]}[]; bounds:{top:number;height:number;display_number:number}};
type Device = {comment?:string;kind?:string;read_by:string[];written_by:string[]};
type Exploration = {width:number;height:number;svg:string;networks:ExploreNetwork[];devices:Record<string,Device>;address_targets:{address:string;x:number;y:number;width:number;height:number}[]};
type Props = {pid:string;vid:string;proposalId?:string;jobId?:string;refreshKey?:unknown;changeSummary?:Record<string,unknown>;theme:"dark"|"light";selectedNetwork?:string;initialAddress?:string;onNetwork:(id:string)=>void;t:(s:string)=>string};

export const ProgramExplorer = memo(function ProgramExplorer({pid,vid,proposalId,jobId,refreshKey,changeSummary,theme,selectedNetwork,initialAddress,onNetwork,t}:Props) {
  const path=proposalId?`/proposals/${proposalId}/explorer`:jobId?`/jobs/${jobId}/explorer`:`/projects/${pid}/versions/${vid}/explorer`;
  const {value:data,error:readError}=useResource<Exploration>(`${path}?theme=${theme}`,refreshKey);
  const [imageError,setImageError]=useState("");
  const error=readError||imageError;
  const image=useMemo(()=>data?`data:image/svg+xml;charset=utf-8,${encodeURIComponent(data.svg)}`:"",[data?.svg]);
  const [query,setQuery] = useState(""), [address,setAddress] = useState(""), [zoom,setZoom] = useState(1);
  const [selected,setSelected] = useState(selectedNetwork || "");
  const targets = useRef<Record<string,HTMLButtonElement|null>>({});
  useEffect(()=>{if(data){setImageError("");setSelected(old=>selectedNetwork||(data.networks.some(n=>n.id===old)?old:data.networks[0]?.id)||"");}},[data]);
  useEffect(()=>{if(selectedNetwork){setSelected(selectedNetwork);targets.current[selectedNetwork]?.scrollIntoView({block:"nearest",behavior:"smooth"});}},[selectedNetwork]);
  useEffect(()=>{if(initialAddress){setAddress(initialAddress);setQuery(initialAddress);}},[initialAddress]);
  function select(id:string){setSelected(id);onNetwork(id);targets.current[id]?.scrollIntoView({block:"nearest",behavior:"smooth"});}
  if(error&&!data)return <p role="alert" className="panel-empty">{error}</p>;
  if(!data)return <p className="panel-empty">{t("正在读取程序引用")}</p>;
  const current=data.networks.find(n=>n.id===selected);
  const device=data.devices[address];
  const matches=Object.entries(data.devices).filter(([a,d])=>!query||`${a} ${d.comment||""}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="program-explorer">
    {error&&<p role="alert">{error}</p>}
    {changeSummary && <ChangeSummary summary={changeSummary} t={t}/>}
    <div className="explorer-toolbar"><label>{t("地址或注释")}<input aria-label={t("搜索地址或注释")} value={query} onChange={e=>setQuery(e.target.value)} placeholder={t("X0 / 电机")}/></label><Button onClick={()=>setZoom(z=>Math.max(.5,z-.25))}>−</Button><Button onClick={()=>setZoom(1)}>{Math.round(zoom*100)}%</Button><Button onClick={()=>setZoom(z=>Math.min(3,z+.25))}>+</Button></div>
    <div className="explorer-main"><div className="explorer-canvas"><div className="explorer-drawing" style={{width:`${zoom*100}%`}}>
      <img src={image} alt={t("可选择网络与地址的梯形图")} onError={()=>setImageError(t("梯形图加载失败，请点击“刷新结果 / 重绘梯形图”。"))} />
      {data.networks.map(n=><button ref={el=>{targets.current[n.id]=el;}} key={n.id} className={`network-target ${selected===n.id?"selected":""}`} style={{top:`${n.bounds.top/data.height*100}%`,height:`${n.bounds.height/data.height*100}%`}} aria-label={`${t("选择网络")} ${n.id}`} aria-pressed={selected===n.id} title={`${n.id} ${n.comment||""}`} onClick={()=>select(n.id)}/>)}
      {data.address_targets.map((a,i)=><button key={i} className={`address-target ${address===a.address?"selected":""}`} style={{left:`${a.x/data.width*100}%`,top:`${a.y/data.height*100}%`,width:`${a.width/data.width*100}%`,height:`${a.height/data.height*100}%`}} aria-label={`${t("查看引用")} ${a.address}`} title={`${a.address} ${data.devices[a.address]?.comment||""}`} onClick={()=>{setAddress(a.address);setQuery(a.address);}}/>)}
    </div></div><aside className="explorer-detail">
      <p className="muted">{t("点击图中的网络查看指令；点击地址追踪读取和写入位置。")}</p>
      <div className="address-results">{matches.map(([a,d])=><button key={a} className={address===a?"selected":""} onClick={()=>setAddress(a)}><strong>{a}</strong> {d.comment||t("未填写注释")}</button>)}{!matches.length&&<p>{t("没有匹配地址")}</p>}</div>
      {device&&<section><h3>{address} · {device.comment||t("未填写注释")}</h3>{([["读取位置",device.read_by],["写入位置",device.written_by]] as [string,string[]][]).map(([label,refs])=><div key={label}><p>{t(label)}</p><div className="reference-buttons">{refs.length?refs.map(id=><Button key={id} onClick={()=>select(id)}>{id}</Button>):<span className="muted">{t("当前程序无此类引用")}</span>}</div></div>)}</section>}
      {current&&<section><h3>{current.id} · {t("网络")} {current.bounds.display_number}</h3><p>{current.comment||t("此网络没有说明，可结合输入、输出及指令检查行为。")}</p><p>{t("读取")}：{current.reads.join(", ")||"—"}</p><p>{t("写入")}：{current.writes.join(", ")||"—"}</p><ol className="instruction-list">{current.instructions.map((inst,i)=><li key={i}><code>{inst.op} {(inst.args||[]).join(" ")}</code></li>)}</ol></section>}
    </aside></div>
    <nav className="network-strip" aria-label={t("程序网络")}>{data.networks.map(n=><button key={n.id} className={selected===n.id?"active":""} onClick={()=>select(n.id)}>{n.id}</button>)}</nav>
  </div>;
});

export type IssueContext = {id:string;title:string;addresses:string[];planId?:string};
type Issue = IssueContext & {severity:string;source:string;message:string;suggestion:string;evidence:string[];networks:string[];unresolved_networks:string[];tests:{plan_id:string;test_names:string[]}[]};
export function IssueCards({pid,vid,reportId,readOnly,onNetwork,onTest,t,refreshKey=0}:{pid:string;vid:string;reportId?:string;readOnly:boolean;onNetwork:(id:string,address?:string)=>void;onTest:(issue:IssueContext)=>void;t:(s:string)=>string;refreshKey?:number}) {
  const {value,error,loading}=useResource<{issues:Issue[]}>(`/projects/${pid}/versions/${vid}/issues${reportId?`?report_id=${encodeURIComponent(reportId)}`:""}`,refreshKey);
  const issues=value?.issues||[];
  if(error&&!value)return <p role="alert">{error}</p>;
  if(loading)return <p>{t("正在读取问题证据")}</p>;
  return <div className="issue-cards">{error&&<p role="alert">{error}</p>}{issues.length?issues.map(issue=><article className="issue-card" key={issue.id}><header><span className={`issue-severity ${issue.severity}`}>{t(issue.severity)}</span><strong>{issue.title}</strong><small>{issue.source}</small></header><p>{issue.message}</p><div className="reference-buttons">{issue.networks.map(id=><Button key={id} onClick={()=>onNetwork(id,issue.addresses[0])}>{id}</Button>)}{issue.addresses.map(a=><Button key={a} onClick={()=>onNetwork(issue.networks[0]||"",a)}>{a}</Button>)}</div>{issue.unresolved_networks.length>0&&<p>{t("无法定位的网络")}：{issue.unresolved_networks.join(", ")}</p>}<details open><summary>{t("证据")}</summary>{issue.evidence.length?<ul>{issue.evidence.map((e,i)=><li key={i}>{e}</li>)}</ul>:<p className="muted">{t("此发现没有附带执行证据，需要复核。")}</p>}</details><p>{issue.suggestion}</p>
    {!!issue.tests?.length && <section><p>{t("已关联复现方案")}</p><div className="reference-buttons">{issue.tests.map(plan=><Button key={plan.plan_id} aria-label={`${t("打开复现方案")} ${plan.plan_id}`} onClick={()=>onTest({id:issue.id,title:issue.title,addresses:issue.addresses,planId:plan.plan_id})}>{plan.test_names.join(" · ")} · {plan.plan_id.slice(-8)}</Button>)}</div></section>}
    <Button disabled={readOnly} onClick={()=>onTest({id:issue.id,title:issue.title,addresses:issue.addresses})}>{t("设计复现测试")}</Button></article>):<p className="muted">{t("当前检查没有问题卡片；这不代表原生编译或运行已验证。")}</p>}</div>;
}
