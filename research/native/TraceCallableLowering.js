// Read-only observations of the pinned ECCompiler_IEC 15.50 fixture compiler.
// The runner checks the file SHA; relocation-adjusted entry bytes are checked here too.
// Observe only program "1" in the isolated callable fixture. No native calls,
// replacements, device sessions or inference of PLC execution effects.
'use strict';

const active = new Map();
let sequence = 0;
const guards = {
  0x8a720: '83ec4053568b74245085f68bd9750a5e',
  0x99350: '8b44240481ec2c0100003d0f01000075',
  0xa9940: '81ec380100005355568bf18b8e860000',
  0xab780: '81ec240100005355568bf18b86860000',
  0xc5cc0: '83ec7c5355568be98b45148b4804576a',
  0xaed60: '6aff6806040f1064a100000000506489',
};
const relocations = {0xaed60: [3]};

function hex(address, size) {
  return Array.from(new Uint8Array(address.readByteArray(size)),
    value => value.toString(16).padStart(2, '0')).join('');
}

function location(address) {
  const module = Process.findModuleByAddress(address);
  return module ? {module: module.name, rva: address.sub(module.base).toString()} : null;
}

function emit(event) {
  if (sequence < 10000) send({...event, sequence: ++sequence});
  else if (sequence === 10000) {
    sequence++;
    send({event: 'lowering-limit-error'});
  }
}

function node(manager, id, depth = 0) {
  if (id === 0) return null;
  if (id < 0 || id > 10000) throw new Error('node outside fixture observation bound');
  const address = manager.add(0x40).readPointer().add(id * 73);
  const result = {
    id, kind: address.add(4).readU32(), type: address.add(24).readS32(),
    reference: address.add(32).readS32(), descriptor: hex(address.add(48), 25),
  };
  // +20 is the parent, not another operand. Never recursively walk parents.
  if (depth < 2) result.children = [12, 16].map(offset =>
    node(manager, address.add(offset).readS32(), depth + 1));
  return result;
}

Process.attachModuleObserver({onAdded(module) {
  if (module.name.toLowerCase() !== 'eccompiler_iec.dll') return;
  try {
    if (Process.arch !== 'ia32') throw new Error('requires x86 compiler');
    for (const [rva, raw] of Object.entries(guards)) {
      const bytes = raw.match(/../g).map(value => parseInt(value, 16));
      for (const offset of relocations[rva] || []) {
        let value = (bytes[offset] | bytes[offset + 1] << 8
          | bytes[offset + 2] << 16 | bytes[offset + 3] << 24) >>> 0;
        value = (value + module.base.sub(ptr('0x10000000')).toUInt32()) >>> 0;
        for (let i = 0; i < 4; i++) bytes[offset + i] = (value >>> (8 * i)) & 255;
      }
      const expected = bytes.map(value => value.toString(16).padStart(2, '0')).join('');
      if (hex(module.base.add(+rva), 16) !== expected)
        throw new Error('entry guard mismatch at ' + Number(rva).toString(16));
    }
    emit({event: 'lowering-module', name: module.name, path: module.path,
      compiler_version: '15.50', program: '1', descriptor_handling: 'raw-preserved'});
    Interceptor.attach(module.findExportByName('Compile'), {
      onEnter(args) {
        this.prior = active.get(this.threadId);
        const count = args[1].toInt32(), names = [];
        if (count < 0 || count > 1024) throw new Error('compile descriptor bound');
        for (let i = 0; i < count; i++) {
          const p = args[2].add(i * 28 + 8).readPointer();
          let name = '';
          for (let j = 0; j < 256; j++) {
            const value = p.add(j).readU8();
            if (!value) break;
            name += String.fromCharCode(value);
          }
          names.push(name);
        }
        active.set(this.threadId, names.includes('1'));
        emit({event: 'lowering-compile', names, observed: names.includes('1')});
      },
      onLeave() { active.set(this.threadId, this.prior); },
    });

    Interceptor.attach(module.base.add(0x99350), {
      onEnter(args) {
        if (!active.get(this.threadId)) return;
        this.output = args[2];
        this.row = {event: 'array-classification', kind: args[0].toInt32(),
          reference: args[1].toInt32(), caller: location(this.returnAddress)};
      },
      onLeave(result) {
        if (this.row) emit({...this.row, result: result.toInt32(),
          count: result.toInt32() === 1 ? this.output.readS32() : null});
      },
    });

    for (const [event, rva, count] of [
      ['construct-assignment', 0x8a720, 2],
      ['expression-size', 0xa9940, 1],
      ['assignment-transfer', 0xab780, 2],
    ]) Interceptor.attach(module.base.add(rva), {
      onEnter(args) {
        if (!active.get(this.threadId)) return;
        try {
          const manager = event === 'construct-assignment' ? this.context.ecx
            : this.context.ecx.add(0x86).readPointer().add(0x30).readPointer();
          this.row = {event, operands: Array.from({length: count}, (_, i) =>
            node(manager, args[i].toInt32())), caller: location(this.returnAddress)};
          emit(this.row);
        } catch (error) { emit({event: 'lowering-observation-error', at: event, error: String(error)}); }
      },
      onLeave(result) {
        // Only expression-size has an established integer return contract.
        if (this.row && event === 'expression-size')
          emit({...this.row, event: 'expression-size-result', count: result.toInt32()});
      },
    });

    // Backend vtable RVA 0x11f108, slot +0x88: opcode and five nullable,
    // 25-byte operand descriptors. This precedes final native PCode encoding.
    Interceptor.attach(module.base.add(0xc5cc0), {
      onEnter(args) {
        if (!active.get(this.threadId)) return;
        try {
          emit({event: 'backend-operation', opcode: args[0].toInt32(),
            operands: Array.from({length: 5}, (_, i) =>
              args[i + 1].isNull() ? null : hex(args[i + 1], 25)),
            caller: location(this.returnAddress)});
        } catch (error) { emit({event: 'lowering-observation-error', at: 'backend-operation', error: String(error)}); }
      },
    });

    // Both numeric backend operations and name-based library templates reach
    // this record sink. Before the backend's +0x38 normalization, the record
    // name/count can still belong to the previous instruction; observe here.
    Interceptor.attach(module.base.add(0xaed60), {
      onEnter(args) {
        if (!active.get(this.threadId)) return;
        try {
          const record = args[0], text = record.readPointer();
          let name = '', terminated = false;
          for (let i = 0; i < 128; i++) {
            const value = text.add(i).readU8();
            if (!value) { terminated = true; break; }
            name += String.fromCharCode(value);
          }
          const count = record.add(12).readU8();
          if (!terminated || count > 9) throw new Error('instruction record bound');
          emit({event: 'emit-record', name, opcode: record.add(4).readS32(),
            source: record.add(8).readS32(), count,
            operands: Array.from({length: count}, (_, i) => hex(record.add(13 + i * 25), 25)),
            raw: hex(record, 238), caller: location(this.returnAddress)});
        } catch (error) { emit({event: 'lowering-observation-error', at: 'emit-record', error: String(error)}); }
      },
    });
  } catch (error) { emit({event: 'lowering-guard-error', error: String(error)}); }
}});
