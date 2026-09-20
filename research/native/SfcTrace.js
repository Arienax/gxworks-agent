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
