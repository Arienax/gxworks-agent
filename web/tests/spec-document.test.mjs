import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { specDocumentChanges } from '../src/workbench/specDocumentChanges.ts';

test('only changed rows and scalars are sent; unrelated sections survive', () => {
  const before = { requirements: [{id:'r1',text:'first'}, {id:'r2',text:'second'}], summary:'before', metadata:{keep:true} };
  const after = { ...before, requirements:[{id:'r1',text:'edited'}, {id:'r3',text:'new'}], summary:'after' };
  assert.deepEqual(specDocumentChanges(before, after), [
    {section:'requirements',id:'r2',remove:true},
    {section:'requirements',id:'r1',value:{id:'r1',text:'edited'}},
    {section:'requirements',id:'r3',value:{id:'r3',text:'new'}},
    {section:'summary',value:'after'},
  ]);
});
test('legacy unkeyed collections retain compatibility', () => {
  assert.deepEqual(specDocumentChanges({io_table:[{address:'X0'}]}, {io_table:[{address:'X1'}]}), [{section:'io_table',value:[{address:'X1'}]}]);
  assert.deepEqual(specDocumentChanges({summary:'old'}, {}), [{section:'summary',remove:true}]);
});

import { specificationContinuation } from '../src/workbench/specificationContinuation.ts';
test('confirmed edits preserve the analyzed old version, program and request in every language', () => {
  for (const language of ['ladder', 'st', 'fbd', 'sfc']) {
    const request = {versionId:'v0001',program:`${language}.Program.pou`,text:'Change delay 7 to 9',generationAction:'edit'};
    const continuation = specificationContinuation(request);
    assert.equal(continuation.generation_action, 'edit');
    assert.equal(continuation.version_id, 'v0001');
    assert.equal(continuation.program, request.program);
    assert.equal(continuation.text, request.text);
  }
  assert.deepEqual(specificationContinuation({versionId:null,text:'new'}), {});
  assert.deepEqual(specificationContinuation({versionId:null,text:'new',conversationId:'analysis-1'}), {conversation_id:'analysis-1'});
  assert.equal(specificationContinuation({versionId:'v0001',text:'replace',generationAction:'regenerate'}).generation_action, 'regenerate');
});

import { readFileSync } from 'node:fs';
import ts from 'typescript';

test('spec confirmation shows decisions and keeps the full draft when answering', () => {
  const path = new URL('../src/features/SpecEditor.tsx', import.meta.url);
  const code = ts.transpileModule(readFileSync(path, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} }, require = createRequire(path);
  const questions = [], actions = [];
  new Function('require', 'module', 'exports', code)(name => {
    if (name === '../components/ChoiceInput') return { ChoiceInput: props => {
      questions.push(props);
      return createElement('label', null, props.label);
    }};
    if (name === '../components/ui') return { Button: ({ variant, ...props }) => {
      actions.push({ variant, ...props });
      return createElement('button', props);
    }};
    return require(name);
  }, module, module.exports);

  const answered = { id: 'answered', name: '旧答案', value: '10' };
  const delay = { id: 'delay', name: '到站延时' };
  const confirmed = { parameters: [answered, { ...delay, value: '1' }] };
  const before = {
    summary: '工艺摘要', parameters: [answered, delay],
    open_questions: [
      { id: 'delay-repeat', text: 'internal-duplicate', required: true, related_missing_info: 'delay' },
      { id: 'stop', text: '停机时如何处理？', required: true, source: 'model' },
    ],
    requirements: [{ id: 'r1', text: 'internal-requirement' }],
    implementation_items: [{ id: 'i1', method: 'internal-method' }],
    io_table: [{ address: 'internal-io' }],
    operation_intents: [{ id: 'internal-intent' }], behavior_constraints: [{ id: 'internal-behavior' }],
    approaches: [
      {
        id: 'a1', name: '方案一', description: '每条记录独立追踪',
        pros: 'behavior: 独立保存已接收的记录\nrecommendation: 需要逐件记录时选用',
        cons: 'program_size: 回收代码随容量增长\nscan_work: 每节拍检查所有有效记录\nmaintenance: 增加容量需要调整重复逻辑',
        generation_guide: 'internal-guide', output_logic: [{ update: 'internal-output' }],
        instruction_choices: [{ opcode: 'SFRD', role: '回收到期记录' }],
        decision_basis: [{ conclusion: 'conditional', premise: 'internal-premise', references: ['internal-source'] }],
      },
      { id: 'a2', name: '方案二', pros: '历史方案优点', cons: '适用条件尚待核实' },
    ],
    selected_approach: { id: 'a1' },
  };
  const document = { draft: before, confirmed_spec: confirmed, differences: [{ path: '$.summary' }] };
  let draft = before, submitted;
  const render = () => renderToStaticMarkup(createElement(module.exports.SpecEditor, {
    value: draft, document, issues: [{ path: '$.open_questions[0]', message: 'internal-duplicate-error' }], t: text => text,
    onChange: next => { draft = next; }, onSave: next => { submitted = next; },
  }));

  const markup = render();
  assert.match(markup, /工艺摘要/);
  assert.match(markup, /到站延时/);
  assert.match(markup, /停机时如何处理？/);
  assert.match(markup, /方案二/);
  assert.match(markup, /实现方式.*每条记录独立追踪/);
  assert.match(markup, /SFRD · 回收到期记录/);
  assert.match(markup, /行为与适用条件.*独立保存已接收的记录/);
  assert.match(markup, /程序规模.*回收代码随容量增长/);
  assert.match(markup, /扫描处理量.*每节拍检查所有有效记录/);
  assert.match(markup, /扩展与维护.*增加容量需要调整重复逻辑/);
  assert.match(markup, /推荐理由.*需要逐件记录时选用/);
  assert.match(markup, /历史方案优点/);
  assert.match(markup, /适用条件尚待核实/);
  assert.doesNotMatch(markup, /program_size:|scan_work:/);
  assert.doesNotMatch(markup, /旧答案|internal-|高级规格数据/);

  questions.find(row => row.label === '到站延时').onChange('1');
  assert.match(render(), /到站延时/);
  questions.filter(row => row.label === '停机时如何处理？').at(-1).onChange('暂停并保留追踪');
  render();
  actions.filter(row => row.variant === 'primary').at(-1).onClick();
  assert.equal(submitted.open_questions[0].text, before.open_questions[1].text);
  assert.deepEqual(specDocumentChanges(before, submitted), [
    { section: 'parameters', id: 'delay', value: { ...delay, value: '1', source: 'user' } },
    { section: 'open_questions', id: 'delay-repeat', remove: true },
    { section: 'open_questions', id: 'stop', value: { ...before.open_questions[1], answer: '暂停并保留追踪' } },
  ]);
});

test('analysis sends the selected old program and captures its edit continuation', async () => {
  const code = ts.transpileModule(readFileSync(new URL('../src/workbench/useJobCommands.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  const module = {exports:{}};
  const submitted = [], captured = [];
  new Function('require', 'module', 'exports', code)(name => {
    assert.equal(name, '../api/client');
    return { key: () => 'request-1', api: async (_path, _method, body) => {
      submitted.push(body); return {id:'analysis-1',kind:body.kind,status:'queued'};
    }};
  }, module, module.exports);
  const noop = () => {};
  const options = {
    workspace:{project:{id:'p1'},pid:'p1',vid:'v0001',capture:()=>1,isProject:()=>true},
    feed:{conversationId:'',jobsPolling:{current:null},selectConversation:noop,setJobId:noop,setJobs:noop,setEvents:noop},
    composer:{text:'Change delay 7 to 9',attachments:[],scopeEnabled:false,scopeNetworks:'',scopeAddresses:'',analysisMode:'direct',setAttachments:noop,setText:noop,task:'edit',editAction:'edit'},
    intent:'analysis',continuingDirect:false,directReply:{complete:true},
    composerRoute:{kind:'analysis'},locale:'en',constructionExamples:false,freshConfirmedGeneration:false,
    clearSpecDirty:noop,setPanel:noop,t:x=>x,selectedGXWProgram:'Valve.Program.pou',
    recordAnalysisRequest:(...args)=>captured.push(args),
  };
  const commands = module.exports.useJobCommands(options);
  await commands.submitMessage();
  assert.equal(submitted[0].program,'Valve.Program.pou');
  assert.equal(submitted[0].version_id,'v0001');
  assert.equal(submitted[0].start_conversation,true);
  assert.deepEqual(captured[0],['analysis-1',{versionId:'v0001',program:'Valve.Program.pou',text:'Change delay 7 to 9',generationAction:'edit'}]);
  await commands.submitJob('generation', specificationContinuation({...captured[0][1],conversationId:'analysis-1'}));
  assert.equal(submitted[1].conversation_id,'analysis-1');
  assert.equal(submitted[1].generation_action,'edit');
  assert.equal(submitted[1].program,'Valve.Program.pou');
  assert.equal(submitted[1].version_id,'v0001');
  assert.equal(submitted[1].text,'Change delay 7 to 9');
  await commands.submitJob('generation');
  assert.equal(submitted[2].conversation_id,undefined);
  assert.equal(submitted[2].start_conversation,undefined);
  const continuing = module.exports.useJobCommands({...options,feed:{...options.feed,conversationId:'analysis-1'}});
  await continuing.submitMessage();
  assert.equal(submitted[3].conversation_id,'analysis-1');
  assert.equal(submitted[3].start_conversation,undefined);

  for (const kind of ['analysis', 'generation', 'direct_generation']) {
    const answering = module.exports.useJobCommands({ ...options,
      workspace: { ...options.workspace, vid: 'other-version' }, selectedGXWProgram: 'Other.Program.pou',
      composer: { ...options.composer, text: 'Supplemental answer', scopeEnabled: true, scopeNetworks: 'N4' },
      feed: { ...options.feed, conversationId: 'unrelated-conversation' },
      continuingDirect: true, currentJob: { id: 'waiting-job', kind },
      directReply: { complete: true, answers: { stop: 'Hold' } },
    });
    await answering.submitMessage();
    assert.deepEqual(submitted.at(-1), {
      kind, project_id: 'p1', request_id: 'request-1', text: 'Supplemental answer',
      response_language: 'en', attachment_ids: [],
      clarification_job_id: 'waiting-job', clarification_answers: { stop: 'Hold' },
    });
  }
  assert.deepEqual(captured.at(-1), ['analysis-1', { versionId: null, text: 'Supplemental answer' }, 'waiting-job']);
});
