// Read-only bootstrap diagnostics for an isolated WorkspaceReplayOracle process.
function loc(p){let m=Process.findModuleByAddress(p);return m?{module:m.name,rva:p.sub(m.base).toString()}:p.toString();}
function hex(p,n){try{return Array.from(new Uint8Array(p.readByteArray(n))).map(x=>x.toString(16).padStart(2,'0')).join('');}catch(e){return null;}}
Process.setExceptionHandler(function(d){send({event:'fault',type:d.type,address:loc(d.address),memory:d.memory,context:d.context,stack:Thread.backtrace(d.context,Backtracer.ACCURATE).map(loc)});return false;});
const observer=Process.attachModuleObserver({onAdded(m){
  if(m.name.toLowerCase()==='ole32.dll') {
    const fn=m.findExportByName('CoCreateInstance');if(fn)Interceptor.attach(fn,{
      onEnter(a){this.clsid=hex(a[0],16);this.iid=hex(a[3],16);},
      onLeave(r){send({event:'cocreate',clsid:this.clsid,iid:this.iid,result:r.toString()});}
    });
  }
  if(m.name.toLowerCase()==='dzdataabs_workspace.dll')Interceptor.attach(m.base.add(0x78091),{
    onEnter(a){this.id=a[1].toInt32();this.output=a[2];this.code=a[3];
      let inner=a[0].add(0x24).readPointer();
      send({event:'get_function',id:this.id,implementation:loc(inner.readPointer().add(0x20).readPointer())});
    },
    onLeave(r){send({event:'get_function_result',id:this.id,result:r.toString(),output:this.output.readPointer().toString(),code:this.code.readS32()});}
  });
}});
