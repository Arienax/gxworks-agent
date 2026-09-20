// Read-only source frontend observations. Layouts come from the pinned native
// ParseData debug-export code; never call or modify any intercepted function.
{
  const targets = {
    'dzdataabs_datamanager_iec.dll': {
      '?Initialize@CDZDataABS_OnMemoryDataManager@@QAEJJPBD@Z': ['Memory.Initialize', 2],
      '?SetBuildData@CDZDataABS_OnMemoryDataManager@@QAEJPAU_stBuildData@@PAU_stParameterData@@PAK@Z': ['Memory.SetBuildData', 3],
      '?ParseData@CDZDataABS_OnMemoryDataManager@@QAEJXZ': ['Memory.ParseData', 0]
    },
    'dzdataabs_sicconverter_iec.dll': {
      '?Initialize@CDZDataABS_ProcessManager@@QAEJPAVCDZDataABS_OnMemoryDataManager@@@Z': ['Process.Initialize', 1],
      '?Build@CDZDataABS_ProcessManager@@QAEJJ@Z': ['Process.Build', 1]
    }
  };
  function cstring(p) {
    try {
      const raw = bytes(p,512);
      if(raw===null) return null;
      const pairs=raw.match(/../g)||[]; const zero=pairs.indexOf('00');
      if(zero<0) return null;
      return p.readUtf8String(zero);
    } catch (_) { return null; }
  }
  function captureArrays(p,specs) {
    const arrays=[];
    for (const [label, offset, stride, nameOffset, payloads] of specs) {
      const count = p.add(offset+4).readS32();
      const arr = p.add(offset).readPointer();
      const item = {label, count, address:arr.toString(), stride, records:[]};
      if (count < 0 || count > 4096) { item.error='count outside observation bound'; arrays.push(item); continue; }
      for (let i=0;i<count;i++) {
        const d=arr.add(i*stride);
        const record={raw:bytes(d,stride), name:cstring(d.add(nameOffset).readPointer()), payloads:[]};
        if(label==='task') record.resource_name=cstring(d.add(12).readPointer());
        for(const [pointerOffset,sizeOffset] of payloads) {
          const size=d.add(sizeOffset).readS32();
          const address=d.add(pointerOffset).readPointer();
          record.payloads.push({pointer_offset:pointerOffset, size_offset:sizeOffset, size,
            address:address.toString(), bytes:size>=0 && size<=0x1000000 ? bytes(address,size) : null});
        }
        item.records.push(record);
      }
      arrays.push(item);
    }
    return arrays;
  }
  function buildData(p) {
    const result = {address:p.toString(), header:bytes(p, 0x34), name:cstring(p.readPointer()), libraries:[], arrays:[]};
    result.arrays=captureArrays(p,[
      ['resource', 0x0c, 16, 0, [[8,12]]],
      ['global', 0x14, 16, 0, [[8,12]]],
      ['structure', 0x1c, 16, 0, [[8,12]]],
      ['task', 0x24, 24, 0, [[16,20]]],
      ['pou', 0x2c, 56, 8, [[16,20],[24,28]]]
    ]);
    const libCount=p.add(4).readS32(); const libs=p.add(8).readPointer();
    result.library_count=libCount;
    if(libCount<0||libCount>64) throw new Error('library count outside observation bound');
    for(let i=0;i<libCount;i++) {
      const d=libs.add(i*32);
      result.libraries.push({raw:bytes(d,32),name:cstring(d.readPointer()),arrays:captureArrays(d,[
        ['global',8,16,0,[[8,12]]], ['structure',16,16,0,[[8,12]]], ['pou',24,56,8,[[16,20],[24,28]]]
      ])});
    }
    return result;
  }
  globalThis.observeFrontend = function(m) {
    const exports=targets[m.name.toLowerCase()];
    if(!exports) return;
    send({event:'frontend_module', name:m.name, path:m.path, base:m.base.toString(), size:m.size});
    for(const [symbol,[name,count]] of Object.entries(exports)) {
      const address=m.findExportByName(symbol);
      if(!address) {send({event:'frontend_missing',name,symbol});continue;}
      Interceptor.attach(address, {
        onEnter(args) {
          this.id=++serial; this.self=this.context.ecx;
          const event={event:'frontend_enter',id:this.id,name,module:m.name,self:this.self.toString(),
            caller:location(this.returnAddress), args:[], call_stack:Thread.backtrace(this.context,Backtracer.ACCURATE).map(location)};
          try {
            for(let i=0;i<count;i++) event.args.push({value:args[i].toString(),bytes:bytes(args[i],128)});
            if(name==='Memory.Initialize') {event.cpu=args[0].toUInt32();event.path=cstring(args[1]);}
            if(name==='Memory.SetBuildData' || name==='Memory.ParseData') {
              event.cpu=this.self.add(0x44).readU32();
              const b=name==='Memory.SetBuildData'?args[0]:this.self.add(0x74).readPointer();
              const param=name==='Memory.SetBuildData'?args[1]:this.self.add(0x78).readPointer();
              const options=name==='Memory.SetBuildData'?args[2]:this.self.add(0x7c).readPointer();
              event.build=buildData(b); event.parameter=bytes(param,96);
              const size=param.add(4).readS32();
              event.parameter_block=size>=0&&size<=0x1000000?bytes(param.readPointer(),size):null;
              event.options=[];
              for(let i=0;i<256;i++){const v=options.add(i*4).readU32();event.options.push(v);if(v===0xffffffff)break;}
            }
          } catch(error) {event.error=String(error);}
          send(event);
        },
        onLeave(result) {send({event:'frontend_leave',id:this.id,name,result:result.toString()});}
      });
    }
  };
}
