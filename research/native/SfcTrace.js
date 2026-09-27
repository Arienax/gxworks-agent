// Read-only bounded observations of inspected native SFC conversion/check ABI.
(() => {
  let sequence = 0;
  const hex = (p, n) => {
    try { return Array.from(new Uint8Array(p.readByteArray(n)), x => x.toString(16).padStart(2, '0')).join(''); }
    catch (_) { return null; }
  };
  const bstr = p => {
    try { const n=p.sub(4).readU32();return n<=8192 && n%2===0?p.readUtf16String(n/2):null; }
    catch (_) { return null; }
  };
  Process.attachModuleObserver({onAdded(m) {
    if(m.name.toLowerCase() === 'dzdataabs_workspace.dll') {
      // The modal paste workflow also changes this SharedData value through
      // the internal thiscall setter, without reentering the COM wrapper.
      Interceptor.attach(m.base.add(0x8f8a1),{
        onEnter(args){send({event:'sfc_paste_conditions',value:args[0].toUInt32()});}
      });
      for(const [name,rva,count] of [
        ['CreateData',550761,18],['CreateDataEX',509661,18],
        ['CreateElementData',498869,15],['SetBlockNumber',551060,15],
        ['SetSFCBlockPropertyInfo',512027,15],['CreateZoomData',513359,17],
        ['SetObjectName',0x84eb4,15],['SetProgramType',0x82e7e,15],
        ['SetVariableLineNumber',535692,15],['SetLineNumber',512767,15],
        ['SortObject',498522,16],['SetSortStatus',498702,16],
        ['GetCopyData',514472,7],['SetPasteData',514557,20],
        ['SetPasteDataEx',519791,22],['SetPasteDataEX2',528547,21],
        ['SetBlockListPasteConditions',514668,2],['SetSFCBlkPasteCheckStatus',531152,6],
        ['SetSFCOverWriteFlg',528007,15]
      ]) Interceptor.attach(m.base.add(rva),{
        onEnter(args) {
          this.id=++sequence;this.args=[];
          for(let i=0;i<count;i++)this.args.push(args[i]);
          send({event:'sfc_workspace_enter',id:this.id,name,
            args:this.args.map((p,i)=>({value:p.toString(),raw:i>=13||count<13?hex(p,64):null})),
            data_name:['CreateData','CreateDataEX','SetObjectName'].includes(name)?bstr(args[13]):null});
        },
        onLeave(ret) {
          send({event:'sfc_workspace_leave',id:this.id,name,result:ret.toString(),
            buffers:this.args.slice(count<13?1:13).map(p=>({value:p.toString(),raw:hex(p,64)}))});
        }
      });
    }
    if(m.name.toLowerCase() === 'dzdataabs_codegenerator.dll') {
      const seen=new Map();
      for(const [name,rva,count] of [
        ['Initialize',0xbfc7,3],['InitializeVer2',0xd943,4],['SAPOpen',0xf450,7],
        ['SAPOpenFX',0xf4a2,9],['SAPOpenFX2',0xf4fa,3],
        ['CheckSFCError',0xf540,4],['ConvertSFCToPcode',0xf589,5],
        ['ConvertPcodeToSFC',0xf661,4],['SetMaxCmXY',0x10559,5],
        ['WriteStepSymbol',0xffa0,10],['WriteStepAttribute',0x10044,10],
        ['WriteTransitionSymbol',0x10134,7]
      ]) Interceptor.attach(m.base.add(rva), {
        onEnter(args) {
          this.id=++sequence;this.args=[];
          for(let i=0;i<count;i++)this.args.push(args[i]);
          const signature=name+this.args.map(p=>p.toString()+':'+hex(p,16)).join('|');
          const repeats=seen.get(signature)||0;seen.set(signature,repeats+1);
          this.skip=repeats>=3;if(this.skip)return;
          send({event:'sfc_control_enter',id:this.id,name,
            args:this.args.map(p=>({value:p.toString(),raw:hex(p,64)}))});
        },
        onLeave(ret) {
          if(this.skip)return;
          send({event:'sfc_control_leave',id:this.id,name,result:ret.toString(),
            buffers:this.args.slice(1).map(p=>({value:p.toString(),raw:hex(p,64)}))});
        }
      });
    }
    if(m.name.toLowerCase() === 'gd2datamng.dll') {
      const location = p => {
        const owner=Process.findModuleByAddress(p);
        return owner?{module:owner.name,rva:p.sub(owner.base).toString()}:null;
      };
      for(const [name,rva,slot,count] of [
        ['CheckSFCError',0x12b59b,0x19f8,14],
        ['ConvertSFCToPcode',0x12b7b9,0x19fc,15],
        ['ConvertPcodeToSFC',0x12b90c,0x1a08,14]
      ]) {
        Interceptor.attach(m.base.add(rva),{
          onEnter(args) {
            this.id=++sequence;this.args=[];
            for(let i=0;i<count;i++)this.args.push(args[i]);
            const backend=this.context.ecx.add(16).readPointer();
            const target=backend.readPointer().add(slot).readPointer();
            send({event:'sfc_editor_enter',id:this.id,name,target:location(target),
              args:this.args.map((p,i)=>({value:p.toString(),raw:i>=12?hex(p,256):null}))});
          },
          onLeave(ret) {
            send({event:'sfc_editor_leave',id:this.id,name,result:ret.toString(),
              buffers:this.args.slice(12).map(p=>({value:p.toString(),raw:hex(p,256)}))});
          }
        });
      }
    }
    if(m.name.toLowerCase() !== 'dzdataabs_compileradapter.dll')return;
    send({event:'sfc_module',name:m.name,path:m.path,base:m.base.toString()});
    for(const [name,rva,count,progress] of [
      ['ChangeSFCProgram',0x14c34,2,false],
      ['ProgramCheck',0x15061,15,false],
      ['GetProgressOfSFCChange',0x14e38,5,true],
      ['GetProgress',0x13e2e,5,true]
    ]) {
      Interceptor.attach(m.base.add(rva),{
        onEnter(args){
          this.id=++sequence;this.args=[];
          for(let i=0;i<count;i++)this.args.push(args[i]);
          if(!progress)send({event:'sfc_enter',id:this.id,name,args:this.args.map(p=>({value:p.toString(),raw:hex(p,64)}))});
        },
        onLeave(ret){
          const row={event:'sfc_leave',id:this.id,name,result:ret.toString()};
          try {
            row.code=this.args[count-1].readU32();
            if(progress){
              row.percent=this.args[1].readS32();row.count=this.args[2].readS32();
              if(row.count<0 || row.count>10000)throw Error('report bound');
              const list=this.args[3].readPointer();row.reports=[];
              for(let i=0;i<row.count;i++){
                const p=list.add(i*100);
                row.reports.push({kind:p.readS32(),code:p.add(4).readU32(),name:bstr(p.add(8).readPointer()),
                  instance:bstr(p.add(16).readPointer()),raw:hex(p,100)});
              }
            }
          } catch(e){row.error=String(e);}
          send(row);
        }
      });
    }
  }});
})();
