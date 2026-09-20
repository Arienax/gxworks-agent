// Read-only, bounded ECCompiler observations. Never invoke or replace targets.
'use strict';
let serial = 0;
const seen = new Set();
function location(p) {
  const m = Process.findModuleByAddress(p);
  return m ? {module: m.name, rva: p.sub(m.base).toString()} : null;
}
function bytes(p, count) {
  try {
    const r = Process.findRangeByAddress(p);
    if (!r || r.protection.indexOf('r') < 0) return null;
    const n = Math.min(count, r.base.add(r.size).sub(p).toUInt32());
    return Array.from(new Uint8Array(p.readByteArray(n)), v => v.toString(16).padStart(2, '0')).join('');
  } catch (_) { return null; }
}
const specs = {
  ObjectNew: 1, ObjectDelete: 1, ChangeInit: 4,
  CompileStart: 28, Compile: 7, CompileEnd: 1, Link: 6,
  Load: 2, Save: 2, LoadFromProjectDB: 4, SaveToProjectDB: 4,
  InitializeAddressConverter: 25, SetSymbolicData: 4,
  GetSymbolicData: 4, SetSystemVariables: 3, Commit: 2,
  GetPCode: 7, GetPCodeInfo: 5, GetUnLinkedPCodeInfo: 5
};
const observer = Process.attachModuleObserver({onAdded(m) {
  if (globalThis.observeFrontend) globalThis.observeFrontend(m);
  if (globalThis.observeWorkspace) globalThis.observeWorkspace(m);
  if (!['eccompiler.dll', 'eccompiler_iec.dll'].includes(m.name.toLowerCase()) || seen.has(m.base.toString())) return;
  seen.add(m.base.toString());
  send({event: 'module', name: m.name, path: m.path, base: m.base.toString(), size: m.size, arch: Process.arch});
  for (const [name, count] of Object.entries(specs)) {
    const address = m.findExportByName(name);
    if (address === null) continue;
    Interceptor.attach(address, {
      onEnter(args) {
        this.id = ++serial;
        this.saved = [];
        for (let i = 0; i < count; ++i) this.saved.push(args[i]);
        const values = this.saved.map((p, i) => ({index: i, value: p.toString(), location: location(p), bytes: bytes(p, 256)}));
        const buffers = [];
        const bounded = (label, p, size) => {
          if (size >= 0 && size <= 0x1000000) buffers.push({label, address:p.toString(), requested_size:size, bytes:bytes(p,size)});
        };
        if (name === 'SetSystemVariables') bounded('system_variables', args[2], args[1].toInt32());
        if (name === 'InitializeAddressConverter') bounded('parameter_block', args[2], args[1].toInt32());
        if (name === 'CompileStart') bounded('parameter_block', args[3], args[2].toInt32());
        if (name === 'Compile') {
          bounded('symbolic_data', args[4], args[3].toInt32());
          const n = args[1].toInt32();
          if (n >= 0 && n <= 1024) {
            bounded('compile_descriptors', args[2], n * 28);
            for (let i=0; i<n; ++i) {
              const d = args[2].add(i*28);
              try {
                bounded('source_' + i, d.add(4).readPointer(), d.readS32());
                buffers.push({label:'name_' + i, bytes:bytes(d.add(8).readPointer(),256)});
              } catch (_) {}
            }
          }
        }
        let callStack = null;
        if (['Compile', 'CompileStart', 'LoadFromProjectDB'].includes(name)) {
          try { callStack = Thread.backtrace(this.context, Backtracer.ACCURATE).map(location); }
          catch (error) { callStack = {error:String(error)}; }
        }
        send({event:'enter', id:this.id, name, module:m.name, thread:this.threadId, caller:location(this.returnAddress),
              call_stack:callStack, args:values, buffers});
      },
      onLeave(result) {
        send({event:'leave', id:this.id, name, module:m.name, result:result.toString(),
          result_bytes: name === 'ObjectNew' ? bytes(result, m.name.toLowerCase().includes('_iec') ? 0x198 : 0x194) : null,
          args:this.saved.map((p, i) => ({index:i, value:p.toString(), bytes:bytes(p, 256)}))});
      }
    });
  }
}});
send({event:'ready', process_id:Process.id, arch:Process.arch});
