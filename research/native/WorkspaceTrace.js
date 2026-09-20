// Bounded read-only observations of the pinned, type-library-mapped COM ABI.
globalThis.observeWorkspace=function(m) {
  const table={
    'dzdataabs_projectoperation.dll':[
      ['Project.SetWorkspace',0x31418,3,[]],
      ['Project.Open',0x31991,9,[2,3,4]],
      ['Project.OpenEX2',0x3b8d6,10,[2,3,4]],
      ['Project.OpenByTypeEX',0x3d4ac,10,[2,3,4]],
      ['Project.ImportOneFile',0x300aa,6,[1,2,3,4]],
      ['Project.Save',0x3b65f,19,[1,2,3]],
      ['Project.ExportOneFile',0x30112,9,[1,2,3,4]],
      ['Project.SetProjectStatus',0x358ce,17,[]],
      ['Project.SetProjectStatusNavi',0x3c651,8,[1,2,3]],
      ['Project.OpenHome',0x3176a,4,[1]],
      ['Project.OpenWorkspace',0x31822,5,[1,2]]
    ],
    'dzdataabs_compileradapter.dll':[
      ['CompilerAdapter.Build',0x13cc0,4,[]],
      ['CompilerAdapter.Commit',0x13eac,3,[]],
      ['CompilerAdapter.SaveData',0x13c42,5,[1,2,3]],
      ['CompilerAdapter.SetSymbolicData',0x148e0,5,[1,2,3]],
      ['CompilerAdapter.GetSymbolicData',0x1495e,5,[1,2,3]],
      ['CompilerAdapter.ToBackward',0x11834,3,[]],
      ['CompilerAdapter.SetSymbolicData_HighSpeed',0x15e3b,3,[1]],
      ['CompilerAdapter.GetSymbolicData_HighSpeed',0x15dc0,4,[1]]
    ],
    'dzdataabs_datamanager_iec.dll':[
      ['Temporary.Commit',0x1ccfa,1,[]],
      ['Temporary.InternalCommit',0x1bb91,1,[]]
    ],
    'dzdataabs_standarddatauni.dll':[
      ['NativeData.SetPOUCompileStatus',0x1c096,2,[]],
      ['NativeData.SetProgramCompileStatus',0x1e6e8,2,[]],
      ['NativeData.SetPCode',0x1f46f,7,[]],
      ['NativeData.SetPCodeByCompiler',0x1f800,7,[]]
    ],
    'dzdataabs_workspace.dll':[
      ['Workspace.Initialize',0x8268e,2,[]],
      ['Workspace.GetProjectFunctionObject',0x780da,16,[]],
      ['Workspace.SetDataCompileStatus',0x863f7,15,[]],
      ['Workspace.GetCollectionID',0x78127,16,[]],
      ['Workspace.GetCollectionIDEX',0x7c81d,16,[]],
      ['Workspace.GetObjectIDList',0x781c0,16,[]],
      ['Workspace.CountObject',0x78047,15,[]],
      ['Workspace.SetPCode',0x78caf,20,[]],
      ['Workspace.SetPCodeByCompiler',0x78d61,20,[]],
      ['Workspace.UpdatePCode',0x8663c,14,[]],
      ['Workspace.BeginCompileStatusTrans',0x6283a,14,[]],
      ['Workspace.CommitCompileStatusTrans',0x62881,14,[]],
      ['Workspace.UpdatePCodeStep',0x86805,14,[]],
      ['Workspace.MakingOfResourcePcode',0x7dc65,14,[]],
      ['Workspace.SetCompileAllAfter',0x80b3a,15,[]],
      ['Workspace.CreatePLCProject',0x77f97,4,[1]]
    ]
  };
  const specs=table[m.name.toLowerCase()];if(!specs)return;
  send({event:'workspace_module',name:m.name,path:m.path,base:m.base.toString()});
  for(const [name,rva,count,strings] of specs)Interceptor.attach(m.base.add(rva),{
    onEnter(args) {
      this.skip=name==='Workspace.GetProjectFunctionObject'&&args[13].toUInt32()!==0x10001;
      if(this.skip)return;
      this.id=++serial;this.saved=[];const values=[];
      for(let i=0;i<count;i++) {
        const p=args[i];this.saved.push(p);
        const item={value:p.toString(),bytes:bytes(p,64)};
        if(strings.includes(i))try{
          const n=p.sub(4).readU32();
          item.text=n<=8192&&n%2===0?p.readUtf16String(n/2):null;
        }catch(e){item.error=String(e);}
        values.push(item);
      }
      send({event:'workspace_enter',id:this.id,name,args:values,caller:location(this.returnAddress),
        stack:name.startsWith('NativeData.')?Thread.backtrace(this.context,Backtracer.ACCURATE).slice(0,12).map(location):null});
    },
    onLeave(result){if(this.skip)return;send({event:'workspace_leave',id:this.id,name,result:result.toString(),
      args:this.saved.map(p=>({value:p.toString(),bytes:bytes(p,64)}))});}
  });
};
