"""Pure Q/L token construction with explicit CPU-specific stored-width rules.

Lexical spellings are independent of CPU instruction admission. Q03UDV's
optimized widths and Q02/Q02H's legacy widths remain separate. Existing
unknown records are handled by the lossless source editor, not reconstructed
from their displayed text. No native library or research module is imported.
"""
from __future__ import annotations

from functools import lru_cache
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
import json
import math
import re
import struct
import sys

from .models import GXWFormatError
from .template_data import template_bytes
from .token_q import _catalog, q_instruction_header


@lru_cache(maxsize=1)
def _facts():
    return json.loads(template_bytes('q-token-encoder-forms.json'))


TABLES = _facts()
APP = {tuple(int(s, 16) for s in key.split(':')): dict(kind=value[0], values=value[1:])
       for key, value in TABLES['application'].items()}
GROUP = {tuple(int(s, 16) for s in key.split(':')): value for key, value in TABLES['groups'].items()}
FLAGS = bytes.fromhex(TABLES['operand_flags'])
QFLAGS = bytes.fromhex(TABLES['q71_operand_flags'])
DEVICE_CLASSES = bytes.fromhex(TABLES['device_classes'])
BASIC_WIDTHS = bytes.fromhex(TABLES['basic_device_widths'])
TABLES['basic'] = [dict(kind=kind) for kind in TABLES['basic_q71']]
TABLES['operand_rules'] = [dict(family=row[0], variant=row[1],
    offset=0x7fff if row[2] is None else 0,
    parameters=[] if row[2] is None else [dict(mask=v[0], devices=v[1], class_id=v[2]) for v in row[2]])
    for row in TABLES['operand_rules']]


@dataclass(frozen=True)
class QStorageContext:
    """Project-bound D/W extension ranges; absence is explicitly unknown.

    Each row is (source device code, source start, count, ZR start). These
    ranges describe source storage width, not allocation or execution safety.
    An empty tuple means a parsed parameter record has no extended ranges.
    ``None`` means its allocation information could not be established.
    """
    cpu: str
    parameter_record: bytes | None
    ranges: tuple[tuple[int, int, int, int], ...] | None
    reason: str | None = None
    device_limits: tuple[tuple[int, int], ...] = ()

    @property
    def status(self):
        return 'available' if self.ranges is not None else 'unavailable'

    @property
    def has_extended_ranges(self):
        return bool(self.ranges) if self.ranges is not None else None

    def as_dict(self):
        return dict(status=self.status, cpu=self.cpu, has_extended_ranges=self.has_extended_ranges,
                    ranges=[dict(device=code, start=start, count=count, zr_start=target)
                            for code, start, count, target in self.ranges or ()], reason=self.reason)


def q_storage_context(parameter_record: bytes, *, cpu: str) -> QStorageContext:
    """Read the bounded 2000 record used by the Q/L source encoder.

    The shared initializer has precisely two extension slots, D then W.
    Banks 0/1 give the ordinary capacity, 2 gives additional words and 3
    gives additional 1024-word blocks. ZR's bank 3 supplies their base.
    Other parameter rows are retained and cannot create extension slots.
    """
    identity = str(cpu).strip().upper()
    if identity not in TABLES['cpu_modes']:
        raise GXWFormatError('Q storage context needs the exact supported saved CPU')
    raw = bytes(parameter_record)
    if (not 6 <= len(raw) <= 4096 or struct.unpack_from('<HH', raw) != (len(raw), 0x2000)
            or len(raw) != 6 + 4 * struct.unpack_from('<H', raw, 4)[0]):
        raise GXWFormatError('unsupported Q device-allocation parameter record')
    ordinary = {0xa8: 0, 0xb4: 0}
    extended = {0xa8: 0, 0xb4: 0}
    seen = set()
    cursor = 0
    for offset in range(6, len(raw), 4):
        kind, value = struct.unpack_from('<HH', raw, offset)
        device, bank = kind & 255, kind >> 8
        if kind == 0x3b0:
            key = ('ZR',)
            cursor = value << 10
        elif device in ordinary:
            if bank > 3:
                # The native 2D375 initializer explicitly skips other banks.
                # Keep their bytes in parameter_record without making them a
                # new ordinary/extended range. Independent controls retain
                # bank 4 and 255 rows and produce identical known widths.
                continue
            key = (device, 'ordinary' if bank < 2 else 'extended')
            (ordinary if bank < 2 else extended)[device] = value << (10 if bank == 3 else 0)
        else:
            continue
        if key in seen:
            raise GXWFormatError('ambiguous duplicate Q allocation parameter row')
        seen.add(key)
    ranges = []
    for device in (0xa8, 0xb4):
        count = extended[device]
        if count:
            ranges.append((device, ordinary[device], count, cursor))
        cursor += count
    limits = tuple((device, ordinary[device] + extended[device]) for device in (0xa8, 0xb4))
    return QStorageContext(identity, raw, tuple(ranges), device_limits=limits)


def q_project_storage_context(parameter_stream: bytes | None, *, cpu: str) -> QStorageContext:
    """Read one complete Param.wpa chain; never search beyond malformed data.

    Missing/unimplemented input returns an unavailable context so the writer
    can still edit instructions independent of D/W extension allocation.
    """
    identity = str(cpu).strip().upper()
    if parameter_stream is None:
        return QStorageContext(identity, None, None, 'project Param.wpa is missing or ambiguous')
    raw, offset, records = bytes(parameter_stream), 0, []
    try:
        while offset + 2 <= len(raw):
            length, = struct.unpack_from('<H', raw, offset)
            if length == 0xffff:
                if offset + 2 != len(raw):
                    raise GXWFormatError('bytes after terminal parameter marker')
                if len(records) != 1:
                    raise GXWFormatError('expected one Q device-allocation parameter record')
                return q_storage_context(records[0], cpu=identity)
            if length < 4 or offset + length > len(raw):
                raise GXWFormatError('invalid project parameter record framing')
            if struct.unpack_from('<H', raw, offset + 2)[0] == 0x2000:
                records.append(raw[offset:offset + length])
            offset += length
        raise GXWFormatError('missing terminal project parameter marker')
    except GXWFormatError as error:
        return QStorageContext(identity, records[0] if len(records) == 1 else None, None, str(error))


def _context_operand(context, device, value):
    if context is None or device not in (0xa8, 0xb4):
        return None
    if context.ranges is None:
        raise GXWFormatError('Q D/W stored width needs project allocation context: ' + str(context.reason))
    for code, limit in context.device_limits:
        if code == device and value >= limit:
            raise GXWFormatError('Q D/W operand exceeds its saved ordinary and extended capacity')
    for code, start, count, target in context.ranges:
        if code == device and start <= value < start + count:
            return target + value - start
    return None


def u32(d,off=9):
    return int.from_bytes(d[off:off+4],'little')

def _tokens(raw):
    out=[]
    at=0
    while at<len(raw):
        n=raw[at]
        if n<3 or at+n>len(raw) or raw[at+n-1]!=n:
            raise GXWFormatError('malformed token framing')
        out.append(raw[at:at+n])
        at+=n
    return out

def operand(raw, *, mode='q71', storage_context=None):
    """Build the source descriptor, including the saved D/W -> ZR mapping."""
    ts=_tokens(raw)
    d=bytearray([255]*18);d[1]=d[17]=0
    cost=0
    for i,t in enumerate(ts):
        code=t[1]
        v=int.from_bytes(t[2:6] if len(t)>6 else t[2:-1],'little')
        if i<len(ts)-1:
            if code in (0xf0,0xf4,0xf6):
                if code==0xf6:
                    # Native 5129f sets byte17 and shares the ordinary index
                    # descriptor/cost, including digit-group and D/W/bit forms.
                    # CPU/Use-of-ZZ requirements are reported separately.
                    d[17]=1
                d[4]=v&255
                if d[3]==255 and ts[i+1][1] not in (0xe9,0xeb):cost+=1
            elif code==0xf1:
                d[3]=v&255
                if d[4]==255:cost+=1
            elif code==0xf2:d[5]=v&255
            elif code==0xf3:d[0]=code
            elif code in (0xf8,0xf9,0xfc,0xff):
                d[1]=code;d[13:17]=v.to_bytes(4,'little')
                if d[4]!=255:d[2]=d[4];d[4]=255
                # 513bf is reached from both branches, including index transfer.
                cost+=1
            else:raise GXWFormatError(f'modifier {code:02x}')
            continue
        if not 0x90<=code<0xf0:raise GXWFormatError(f'base {code:02x}')
        if (d[1]==0xf8 and code!=0xab) or (d[1]==0xf9 and code not in (0x9c,0x9d,0xa0,0xa1,0xa2,0xa3,0xb4,0xb5)) or (d[1]==0xfc and code not in (0x98,0x99)):
            raise GXWFormatError('incompatible prefix')
        mapped = _context_operand(storage_context, code, v) if not d[1] else None
        if mapped is not None:
            d[6] = 0xb0
            d[9:13] = mapped.to_bytes(4, 'little')
            return bytes(d), cost + (2 if mode != 'q71' or mapped > 65535 and d[4] == 255 else 1)
        d[6]=code;d[9:13]=v.to_bytes(4,'little')
        if code == 0xdf:
            if i or len(ts) != 1:
                raise GXWFormatError('symbolic Q operand cannot carry numeric modifiers')
            cost = 1
        elif d[1]==0xf9:cost+=1
        elif code==0xee:cost+=1 if len(t)==4 and t[2]==0 else (len(t)-2)//2+1
        else:cost+=(len(t)-2)//2
        if d[1]:
            if d[1]==0xf9 and code==0xb4 and v+0x1220>65535 and d[4]==255:cost+=1
            special=(d[1]==0xf8 and 0x3e0<=u32(d,13)<=0x3e3 and code==0xab and v>=10000 and d[2]==255)
            if special:
                if d[4]!=255:cost-=1
            elif d[0]!=255:cost+=1
        if d[3] in (4,8) and d[4]==255 and v%16==0:cost-=1
        if code==0xb0 and d[4]!=255 and v>65535:cost-=1
    return bytes(d),cost

def validate_descriptor(d, *, mode='q71'):
    """515f9 modifier capabilities with the saved CPU's index-width flags."""
    d=bytearray(d)
    if d[6] in (0xdf, 0xad):
        # Native 515F9 leaves symbolic/virtual descriptors unresolved. Their
        # datatype and target are a separate current-declaration binding.
        return bytes(d)
    i=(d[6]-0x90)*3
    cap=FLAGS[i];flags=int.from_bytes(FLAGS[i+1:i+3],'little')
    if d[0]!=255 and not cap&32:raise GXWFormatError('indirect not permitted')
    if d[5]!=255:
        if not cap&1:raise GXWFormatError('bit selector not permitted')
        flags=1
    if d[3]!=255:
        if not flags&1 or not cap&16:raise GXWFormatError('group not permitted')
        if d[3]<=4:flags=2
        elif d[3]<=8:flags=4
        else:raise GXWFormatError('group count')
    index_flags = QFLAGS if mode == 'q71' else FLAGS
    if d[4]!=255 and (d[5]!=255 or not index_flags[i]&2):raise GXWFormatError('index not permitted')
    if d[1] and (not cap&8 or (d[2]!=255 and not QFLAGS[(d[1]-0x90)*3]&4)):raise GXWFormatError('prefix not permitted')
    if d[0]==255 and d[1]==0 and d[3]==255 and d[5]==255:
        flags|=64
        if d[4]==255:flags|=128
    d[7:9]=flags.to_bytes(2,'little')
    return bytes(d)

def expected_group(f,v,i):
    return (GROUP.get((f,v),0) >> ((7-i)*2))&3 if i<8 else 0

def group_ok(f,v,i,d):
    if d[3]==255:return True
    if d[4]!=255 or d[3] not in (4,8) or u32(d)%16:return False
    expected=expected_group(f,v,i)
    return expected<=1 if d[3]==4 else expected==2

def group_extra(f,v,i,d):
    return int(d[3] in (4,8) and d[4]==255 and u32(d)%16==0 and not group_ok(f,v,i,d))

def compact(f,v,i,d):
    return (group_ok(f,v,i,d) and d[4]==255 and d[6] not in (0xe9,0xeb)
        and not (d[6]==0xb0 and u32(d)>65535)
        and not (d[1]==0xf8 and 0x3e0<=u32(d,13)<=0x3e3 and d[6]==0xab and u32(d)>=10000))

def operand_rule_status(f,v,i,d):
    """5190a CPU class60/internal71 stage only; zero does not prove instruction validity.

    Negative values preserve the native distinctions (including literal width
    promotion requests -4 / -5). i is zero-based.
    """
    dev=d[6];flags=int.from_bytes(d[7:9],'little')
    if dev in (0xad,0xe7):return 0
    if f==0x3c:return 0 if dev in (0xd0,0xd1,0xdf) else -1
    for row in TABLES['operand_rules']:
        if row['family']!=f or (f>=0x40 and row['variant']!=v):continue
        if row['offset']==0x7fff:return -3
        if i>=len(row['parameters']):return -2
        rule=row['parameters'][i];mask=rule['mask'];bitmap=int.from_bytes(bytes.fromhex(rule['devices']),'little')
        def allowed(code):return bool(bitmap & (1<<(code-0x90)))
        if dev==0xdf and bitmap & ~(1<<(0xad-0x90)):return 0
        if mask==1 and d[0]!=255 and dev in (0xc2,0xc5,0xc8):return -1
        if mask==2 and dev in (0xe9,0xeb) and allowed(dev-1):return -5
        if not (mask&flags or (dev in (0x98,0x99,0xdc) and mask&0x80)):
            if f in (0x20,0x23,0x24):continue
            return -1
        if f==0x65 and rule['class_id'] in (0x46,0x4d,0x4f) and dev not in (0xc2,0xc5,0xc8) and flags&1 and d[3]==255:return -1
        if mask&0xc0 and not mask&0x8000 and (d[5]!=255 or d[3]!=255):return -1
        if mask&0x80 and not mask&0x8000 and d[4]!=255 and dev not in (0x98,0xdc):return -1
        if mask!=0xffff:
            if mask&4 and dev in (0xe8,0xea):return -4
            if mask==0x1e and dev in (0xec,0xed):return -1
            if mask==0x16:
                if dev in (0xe3,0xe4):return -4
                if dev==0xcc:return 0
        effective=d[1] or dev
        if mask in (0x16,0x2e,0x1e) and effective==0xf9 and dev in (0x9c,0x9d,0xa0,0xa1):return -1
        if allowed(effective):return 0
        if dev in (0x98,0x99,0xdc) and mask==1 and dev!=0x99 and f<0x40:continue
        return -1
    return -1

def application(f,v,ds,cost):
    """6e7b7 optimized path. -1 selects the caller's fallback, not unsupported."""
    row=APP.get((f,v))
    if row is None:return -1
    if f in (0x50,0x51) and not group_ok(f,v,0,ds[0]):return -1
    for d in ds:
        if d[6] in (0xa2,0xa3) or d[1] in (0xf9,0xfc):return -1
        if d[1]==0xf8 and (not 0x3e0<=u32(d,13)<=0x3e3 or (d[6]==0xab and (u32(d)<10000 or d[2]!=255))):return -1
        if d[6]==0x98 and (f,v)!=(0x42,1):return -1
        if d[6]==0xdc and (f,v)!=(0x6c,2):return -1
    base,source,dest,indirect=row['values'];kind=row['kind']
    if kind==4 and ds[0]==ds[2]:kind=5
    if ds and all(d[6]==0xcc for d in ds):
        return 2 if kind==5 else (1 if kind<=9 else (base+1 if kind<=12 or kind==15 else (3 if kind==13 else 1)))
    cost+=2*sum(d[0]!=255 for d in ds)
    g=[group_ok(f,v,i,d) for i,d in enumerate(ds)]
    z=[d[6]==0xcc for d in ds]
    ind=[d[0]!=255 for d in ds]
    def src(i):return source if not g[i] and ds[i][3]<=4 else 0
    def dst(i):return dest if not g[i] else (indirect if ind[i] else 0)
    if kind==1:
        cost+=src(0)+src(1)
        return cost if any(z) else base+cost
    if kind==2:
        cost+=src(0)+src(1)+dst(1)
        if not compact(f,v,1,ds[1]) and not ind[1]:cost+=1
        return cost if z[1] or (z[0] and (not g[1] or ind[1])) else base+cost
    if kind in (3,4):
        cost+=src(0)+src(1)+dst(2)
        if z[0] and z[1]:
            if not g[2] or ind[2]:return cost-2
            return cost-1
        if z[0] or z[1]:
            if not g[2] or ind[2] or z[2]:return cost-1
        return base+cost
    if kind==5:
        if ind[2]:
            cost-=1
            if not compact(f,v,2,ds[2]):cost-=1
        cost+=1
        if all(ind):return cost
        cost+=src(0)+src(1)+(dest if not g[2] else 0)
        if (not g[0] or ind[0]) and z[1]:return cost-1
        if z[0] and not z[1]:return cost-1
        return base+cost
    if kind in (6,8):
        cost+=src(0)+dst(0)
        if not compact(f,v,0,ds[0]) and not ind[0]:cost+=1
        return base+cost
    if kind==7:
        cost+=src(0)+dst(1)
        return cost-1 if z[0] and (not g[1] or ind[1]) else base+cost
    if kind==9:
        if z[0]:
            if not g[1]:return cost
            if ind[1]:return cost-1
        return base+cost+src(0)
    if kind==10:
        if z[0] and (not g[1] or ind[1]):return cost
        return base+cost+src(0)+dst(1)
    if kind in (11,12):
        cost+=base+(indirect if ind[0] else 0)
        return cost-1 if (not g[0] or ind[0]) and z[1] else cost
    if kind==13:
        if not g[0]:cost+=dest
        elif ind[0]:cost+=indirect
        elif z[0]:cost+=1
        if (not g[0] or ind[0]) and z[1]:return cost
        if not compact(f,v,0,ds[0]) and not ind[0] and g[0]:cost+=1
        return base+cost
    if kind==14:return base+cost+src(0)-int(z[0])-int(z[1])
    if kind==15:return base+cost
    raise GXWFormatError(f'optimized kind {kind}')

def basic(f,ds,cost):
    """52ef9 internal CPU71 branches; cost includes 52e73 extension words."""
    original=f
    if f in (0x29,0x2b):f-=1
    if not ds:return 2 if f==0x34 else 1
    kind=TABLES['basic'][f]['kind']
    d=ds[0]
    def matrix(d):
        c=DEVICE_CLASSES[d[6]*2]
        if c>=23:raise GXWFormatError(f'device class {c}')
        return BASIC_WIDTHS[kind*23+c]
    def special(d):
        return d[1]==0xf8 and 0x3e0<=u32(d,13)<=0x3e3 and d[6]==0xab and u32(d)>=10000 and d[2]==255
    def plain(d):return d[0]==255 and d[1]==0
    def small(i):return compact(f,0,i,ds[i])
    if kind in (1,10):return 1 if small(0) else 2
    if kind==11:return 1
    if kind==2:
        if plain(d):
            width=matrix(d)
            if width:return width+int(not small(0))
        elif special(d):return 2
        return cost+1
    if kind in (3,5):
        if plain(d):
            width=matrix(d)
            if width and not (f==0x23 and d[6]==0x93):
                if f==0x23:width=1
                return width+int(not small(0))
        elif special(d):return 3 if f in (0x25,0x26,0x27) else 2
        return cost+1+int((f==0x23 and d[6]==0x93) or f in (0x25,0x26,0x27))
    if kind==4:
        d2=ds[1]
        if any(o[0]!=255 for o in ds):raise GXWFormatError('indirect timer/counter')
        if d2[1]==0:
            width=matrix(d2)
            if width:return width+3*int(not small(0))+int(not small(1))
        elif special(d2):return 5+3*int(not small(0))
        return cost+3+2*int(not small(0))
    if kind==6:
        if plain(d):
            width=matrix(d)
            if width:
                if not d[7]&1 and d[6]!=0xcc:width=2
                if not small(0):width+=3 if 0xc0<=d[6]<=0xc8 else 1
                return width
        elif d[1]==0:return 3+int(not small(0))
        elif special(d):return 2 if d[7]&1 else 3+int(d[0]!=255)
        return cost+1+int(d[6]==0x93)
    if kind==9:
        d2=ds[1]
        if plain(d2):
            width=matrix(d2)
            if width:return width+int(not small(1))
        elif special(d2):return 3
        return cost+1
    if kind in (7,8):
        if f in (0x28,0x2a):return cost+1+int(original in (0x29,0x2b))
        if plain(d) and d[7]&1:
            width=matrix(d)
            if width:return width+int(not small(0))
        elif special(d):return 3
        return cost+2+(2 if f in (4,5) else (3 if f in (0xa,0xb,0x15,0x16) else 0))
    return cost+(1 if kind==12 else 2)



def _token(code, payload):
    size = len(payload) + 3
    if not payload or size > 255:
        raise GXWFormatError('Q operand exceeds its bounded token extent')
    return bytes((size, code)) + payload + bytes((size,))


def _integer(code, value, bits=32, *, signed=False):
    lower, upper = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if not lower <= value <= upper:
        raise GXWFormatError('Q operand is outside its stored numeric range')
    size = bits // 8 if value < 0 else max(1, (value.bit_length() + 7) // 8)
    return _token(code, value.to_bytes(size, 'little', signed=value < 0))


def _header_choices(op, arity=None, *, profile):
    catalog = _catalog('l' if profile == 'l02' else profile)
    named = _NAMED_CALL.fullmatch(op)
    if named:
        family = named_call_family(op)
        if arity is None:
            counts = {count for key, (name, count) in catalog['headers'].items()
                      if key.startswith(f'{family:02x}:') and name == named[1] + '.'}
            return tuple(choice for count in sorted(counts) for choice in _header_choices(op, count, profile=profile))
        variant = arity - 1 if family in (0x72, 0x73, 0x79) else arity
        if (catalog['headers'].get(f'{family:02x}:{variant:02x}') != [named[1] + '.', arity]
                or family == 0x74 and not arity):
            return ()
        suffix = named[3].encode('ascii')
        size = len(suffix) + 6
        if size > 255:
            return ()
        return ((bytes((size, family, 1, variant, 2 if named[2] else 0x0a)) + suffix + bytes((size,)), arity),)
    result = []
    modes = [(op, None)]
    if op.endswith('P'):
        modes.append((op[:-1], 2))
    for prefix, modifier in (('AND', 0x11), ('LD', 0x10), ('OR', 0x12)):
        if op.startswith(prefix) and len(op) > len(prefix):
            modes.append((op[len(prefix):], modifier))
    for key, (name, count) in catalog.get('headers', {}).items():
        family, variant = (int(part, 16) for part in key.split(':'))
        if family >= 0x70 or (arity is not None and count != arity):
            continue
        for wanted, modifier in modes:
            if name.lstrip(',') != wanted:
                continue
            if modifier is not None and family < 0x40:
                continue
            if family < 0x40:
                raw = bytes((3, family, 3))
            elif modifier is None:
                raw = (bytes((6, family, 1, variant, 2, 6))
                       if family == 0x65 and variant in (5, 6, 7, 0x22, 0x23, 0x48, 0x49)
                       else bytes((5, family, 1, variant, 5)))
            else:
                raw = bytes((6, family, 1, variant, modifier, 6))
            try:
                actual, actual_count = q_instruction_header(raw, profile=profile if profile != 'l02' else 'l')
            except GXWFormatError:
                continue
            if actual == op and (arity is None or actual_count == arity):
                result.append((raw, count))
    return tuple(dict.fromkeys(result))


def _parameter_rule(family, variant, index):
    for row in TABLES['operand_rules']:
        if row['family'] == family and (family < 0x40 or row['variant'] == variant):
            if row['offset'] == 0x7fff or index >= len(row['parameters']):
                return None
            return row['parameters'][index]
    return None


def _literal_code(op, index, text, *, profile, source=None, cpu=None, token_code=None,
                  indexed=False):
    float_match = re.fullmatch(r'E[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)([+-][0-9]+)?', text)
    wanted = (0xe8, 0xe9) if text.startswith('K') else ((0xea, 0xeb) if text.startswith('H') else
             (0xee,) if text.startswith('"') else (0xed, 0xe4) if float_match and float_match[1] else (0xec, 0xe3))
    if token_code is not None:
        if float_match and token_code in (0xec, 0xed, 0xe3, 0xe4):
            wanted = (token_code,)
        elif token_code not in wanted:
            raise GXWFormatError('structured Q literal identity disagrees with its value kind')
    def selected(default, allowed=None):
        if token_code is None:
            return default
        if token_code not in (allowed or {default}):
            raise GXWFormatError('structured Q literal identity is outside this parameter role')
        return token_code
    if re.fullmatch(r'[SZGJDMU]P?\.[!-~]+', op):
        macro = op[0] in ('M', 'U')
        if text.startswith('K'):
            if indexed and not macro:
                # 37272 normalizes indexed named-call constants after the
                # width check: Q71 uses E9/EB; legacy/Q66 uses E8/EA. Plain
                # named constants remain E9/EB. Independent six-CPU controls
                # retain the F0 modifier and establish both stored forms.
                return selected(0xe9 if _cpu_mode(profile, cpu) == 'q71' else 0xe8)
            return selected(0xe8 if macro and -32768 <= int(text[1:]) <= 32767 else 0xe9,
                            {0xe8, 0xe9} if macro else {0xe9})
        if text.startswith('H'):
            if indexed and not macro:
                return selected(0xeb if _cpu_mode(profile, cpu) == 'q71' else 0xea)
            return selected(0xea if macro and int(text[1:], 16) <= 65535 else 0xeb,
                            {0xea, 0xeb} if macro else {0xeb})
        if text.startswith('"'):
            return selected(0xee)
        if text.startswith('E'):
            modern = _cpu_mode(profile, cpu) in ('q66', 'q71')
            normal = (0xe4 if float_match and float_match[1] else 0xe3) if modern else (
                0xed if float_match and float_match[1] else 0xec)
            return selected(normal, {0xe3, 0xe4} if modern else {0xec, 0xed})
    codes = set()
    for header, count in _header_choices(op, profile=profile):
        if index >= count:
            continue
        family = header[1]
        variant = header[3] if len(header) >= 5 else 0
        rule = _parameter_rule(family, variant, index)
        if rule is None:
            continue
        allowed = int.from_bytes(bytes.fromhex(rule['devices']), 'little')
        choices = [code for code in wanted if allowed & (1 << (code - 0x90))]
        if text.startswith('E'):
            if rule['mask'] == 0x16:
                choices = [code for code in choices if code in (0xec, 0xed)]
            elif rule['mask'] == 0x1e:
                choices = [code for code in choices if code in (0xe3, 0xe4)]
        if choices and rule['mask'] != 0xffff and rule['mask'] & 4:
            choices = [code for code in choices if code not in (0xe8, 0xea)]
        elif len(choices) > 1 and token_code is None:
            choices = choices[:1]
        if token_code is not None:
            choices = [code for code in choices if code == token_code]
        codes.update(choices)
    if len(codes) == 1:
        return codes.pop()
    if not codes and source is not None and len(source.tokens) == 1 and source.tokens[0].raw[1] in wanted:
        return source.tokens[0].raw[1]
    raise GXWFormatError('Q literal width is unproven or ambiguous for this instruction parameter')


def encode_q_operand(text, *, op, index, source=None, profile='q03udv', text_encoding=None, cpu=None,
                     token_codes=None):
    """Encode one lexical operand; the header validates its precise parameter role."""
    if not isinstance(text, str) or not text or any(c in text for c in '\0\r\n'):
        raise GXWFormatError('Q operand must be one bounded nonempty text value')
    literal_code = token_codes[0] if token_codes is not None and len(token_codes) == 1 else None
    if text.startswith('"'):
        if not text.endswith('"') or '"' in text[1:-1]:
            raise GXWFormatError('unsupported Q quoted string')
        code = _literal_code(op, index, text, profile=profile, source=source, cpu=cpu, token_code=literal_code)
        if code != 0xee or text_encoding is None:
            raise GXWFormatError('Q string needs its parameter role and project code page')
        try:
            value = text[1:-1].encode(text_encoding)
        except (UnicodeError, LookupError) as exc:
            raise GXWFormatError('Q string cannot be encoded in the project code page') from exc
        if len(value) > 32:
            raise GXWFormatError('Q strings require at most 32 encoded bytes')
        return _token(code, value or b'\0')
    integer = re.fullmatch(r'(K[+-]?[0-9]+|H[0-9A-F]+)(?:Z([0-9]+))?', text)
    if integer is not None:
        value, register = integer.groups()
        if register is not None and token_codes is not None and len(token_codes) == 2:
            literal_code = token_codes[-1]
        code = _literal_code(op, index, value, profile=profile, source=source, cpu=cpu,
                             token_code=literal_code, indexed=register is not None)
        raw = _integer(code, int(value[1:], 16 if value[0] == 'H' else 10),
                       32 if code in (0xe9, 0xeb) else 16, signed=value[0] == 'K')
        return (_integer(0xf0, int(register), 8) if register is not None else b'') + raw
    if text.startswith('E'):
        code = _literal_code(op, index, text, profile=profile, source=source, cpu=cpu, token_code=literal_code)
        return _float_literal(text, code, mode=_cpu_mode(profile, cpu))
    if text.startswith("'"):
        if text_encoding is None or len(text) == 1:
            raise GXWFormatError('Q label needs the explicit project code page')
        try:
            return _token(0xdf, text[1:].encode(text_encoding))
        except (UnicodeError, LookupError) as exc:
            raise GXWFormatError('Q label cannot be encoded in the project code page') from exc
    pieces = []
    if text.startswith('@'):
        pieces.append(_integer(0xf3, 0))
        text = text[1:]
    if '\\' in text:
        if text.count('\\') != 1:
            raise GXWFormatError('Q operand has more than one module/network prefix')
        prefix, text = text.split('\\')
        match = re.fullmatch(r'(U[0-9A-F]+|J[0-9]+|BL[0-9]+|\.[0-9A-F]+)(?:(ZZ|Z)([0-9]+))?', prefix)
        if match is None:
            raise GXWFormatError('unsupported Q module/network prefix')
        if match[2]:
            pieces.append(_integer(0xf6 if match[2] == 'ZZ' else 0xf0, int(match[3]), 8))
        if match[1].startswith('.'):
            # FD/FE/FF have identical native displays. An existing operand or
            # its structured storage identity selects the exact source form.
            requested = [code for code in token_codes or () if code in (0xfd, 0xfe, 0xff)]
            if not requested and source is not None:
                requested = [token.raw[1] for token in source.tokens if token.raw[1] in (0xfd, 0xfe, 0xff)]
            if len(requested) != 1:
                raise GXWFormatError('extended Q prefix requires its explicit source storage identity')
            pieces.append(_integer(requested[0], int(match[1][1:], 16)))
        elif match[1].startswith('U'):
            pieces.append(_integer(0xf8, int(match[1][1:], 16)))
        elif match[1].startswith('BL'):
            pieces.append(_integer(0xfc, int(match[1][2:])))
        else:
            pieces.append(_integer(0xf9, int(match[1][1:])))
    catalog = _catalog('l' if profile == 'l02' else profile)['operands']
    names = sorted({name for code, (name, kind) in catalog.items()
                    if kind in (0, 4) and int(code, 16) < 0xe8}, key=lambda name: (-len(name), name))
    match = re.fullmatch(r'(K[1-8])?(' + '|'.join(re.escape(name) for name in names)
                        + r')([0-9A-F]+)(?:(ZZ|Z)([0-9]+)|\.([0-9A-F]))?', text)
    if match is None:
        raise GXWFormatError('unsupported Q operand spelling')
    group, prefix, number, index_kind, index_number, bit = match.groups()
    if group:
        pieces.append(_integer(0xf1, int(group[1:])))
    if index_kind:
        pieces.append(_integer(0xf6 if index_kind == 'ZZ' else 0xf0, int(index_number), 8))
    if bit:
        pieces.append(_integer(0xf2, int(bit, 16)))
    choices = [(int(code, 16), kind) for code, (name, kind) in catalog.items()
               if name == prefix and kind in (0, 4) and int(code, 16) < 0xe8]
    if token_codes is not None:
        choices = [choice for choice in choices if choice[0] == token_codes[-1]]
    elif source is not None:
        preferred = [choice for choice in choices if choice[0] == source.tokens[-1].raw[1]]
        if preferred:
            choices = preferred
    if len(choices) > 1 and prefix in ('T', 'ST', 'C'):
        choices = [max(choices)]
    if len(choices) != 1:
        raise GXWFormatError('Q device prefix is unproven or ambiguous')
    code, kind = choices[0]
    try:
        value = int(number, 16 if kind == 4 else 10)
    except ValueError as exc:
        raise GXWFormatError('invalid Q device number') from exc
    result = [*pieces, _integer(code, value)]
    if token_codes is not None:
        # U0Z1\\G0 and U0\\G0Z1 display identically in the legacy formatter,
        # while the index belongs to a different storage component. Select
        # only the exact requested ordering of already encoded components.
        remaining = list(result)
        ordered = []
        for requested in token_codes:
            found = next((i for i, token in enumerate(remaining) if token[1] == requested), None)
            if found is None:
                raise GXWFormatError('structured Q operand storage identity disagrees with its spelling')
            ordered.append(remaining.pop(found))
        if remaining:
            raise GXWFormatError('structured Q operand omits a storage component')
        result = ordered
    return b''.join(result)


#: Device codes whose stored width can follow the saved D/W -> ZR mapping.
ALLOCATION_DEVICE_CODES = (0xa8, 0xb4)

#: Encoded prefix components that address another module or station rather than
#: the local device allocation: U..\, J..\, BL..\ and the extended ..\ prefix.
_MODULE_PREFIX_CODES = (0xf8, 0xf9, 0xfc, 0xff)

#: Encoded family of a native named call, keyed by its letter prefix.
_NAMED_CALL_FAMILIES = {"S": 0x70, "Z": 0x71, "G": 0x72, "J": 0x73, "D": 0x74,
                        "M": 0x78, "U": 0x79}
_NAMED_CALL = re.compile(r"([SZGJDMU])(P?)\.([!-~]+)")

#: Named-call families the stored-width algorithm gives one placeholder word.
_PLACEHOLDER_FAMILIES = (0x78, 0x79)


def named_call_family(name):
    """Encoded family number of one native named call, else None.

    Only the 0x78/0x79 pair (M./U.) is stored as a one-word placeholder whatever
    its unexpanded arguments name; S./Z./G./J./D. are ordinary applied
    instructions whose width follows the operands, the D/W allocation included.
    The stored-width algorithm and the allocation classifier ask this one
    function, so one mnemonic cannot be a placeholder in one place and an applied
    instruction in the other.
    """
    match = _NAMED_CALL.fullmatch(str(name or "").strip().upper())
    return _NAMED_CALL_FAMILIES[match[1]] if match is not None else None


def allocation_dependent_operands(operands, *, op=None, profile="q03udv", cpu=None):
    r"""Operands whose stored width can follow the saved D/W device allocation.

    The decision is taken from the encoded operand rather than from its spelling,
    so every spelling of one address answers the same way: W is a hexadecimal
    device, so ``WA00`` and ``W0A00`` are the same local word, an ``@`` indirect
    D operand still reads the local allocation, an index or bit selector does not
    change what is addressed, and a module or network prefix (``U0\G0``,
    ``J1\W...``) addresses another unit and therefore never depends on the local
    mapping. Only the placeholder families (M./U., 0x78/0x79) store one word
    regardless of their arguments; the other named calls (S./Z./D. ...) keep the
    operand-dependent width their encoding has, so a D/W argument there does need
    the saved allocation.
    """
    name = str(op or "").strip().upper()
    if named_call_family(name) in _PLACEHOLDER_FAMILIES:
        return ()
    dependent = []
    for index, value in enumerate(operands or ()):
        text = str(value).strip()
        if not text or text.startswith(('"', "'")):
            continue
        try:
            raw = encode_q_operand(text, op=name, index=index, profile=profile, cpu=cpu)
        except GXWFormatError:
            # An operand this encoder cannot parse yet is reported by the width
            # path itself; it is not evidence of an allocation dependency.
            continue
        tokens = _tokens(raw)
        if not tokens:
            continue
        if any(token[1] in _MODULE_PREFIX_CODES for token in tokens[:-1]):
            continue
        if tokens[-1][1] in ALLOCATION_DEVICE_CODES:
            dependent.append(text)
    return tuple(dependent)


def _rounded(value, digits):
    with localcontext() as context:
        context.prec = 800
        exact = Decimal.from_float(value)
        return float(exact.quantize(Decimal(1).scaleb(exact.adjusted() - digits + 1),
                                    rounding=ROUND_HALF_UP)) if value else 0.0


def _float_literal(text, code, *, mode):
    match = re.fullmatch(r'E([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([+-][0-9]+)?', text)
    if match is None:
        raise GXWFormatError('unsupported Q floating-point literal spelling')
    exponent = int(match[2] or '0')
    try:
        value = float(match[1] + ('e' + match[2] if match[2] else ''))
        if not math.isfinite(value):
            raise GXWFormatError('non-finite Q floating-point operand')
        exact_input = Decimal(match[1]).scaleb(exponent)
        if value == 0 and exact_input:
            raise GXWFormatError('Q floating-point operand underflows binary64')
        if code in (0xe3, 0xe4):
            minimum, maximum = ((1e-20, 9.99999999999999e20) if code == 0xe3 else
                                (sys.float_info.min, sys.float_info.max))
            if value and not minimum <= abs(value) <= maximum:
                raise GXWFormatError('Q double is outside its stored notation range')
            return _token(code, struct.pack('<d', _rounded(value, 15)))
        # Q66/Q71 first store a 15-digit double, then the parameter's -4
        # promotion path converts it through the seven-digit float formatter.
        if mode in ('q66', 'q71'):
            value = _rounded(value, 15)
        minimum, maximum = ((1e-10, 999999900000.0) if code == 0xec else
                            (1.175495e-38, 3.402823e38))
        if value and not minimum <= abs(value) <= maximum:
            raise GXWFormatError('Q float is outside its stored notation range')
        payload = struct.pack('<f', _rounded(value, 7))
        if not math.isfinite(struct.unpack('<f', payload)[0]):
            raise GXWFormatError('Q floating-point operand overflows binary32')
        return _token(code, payload)
    except (OverflowError, ValueError, DecimalException) as exc:
        raise GXWFormatError('Q float is outside its bounded stored range') from exc


def _cpu_mode(profile, cpu):
    identity = str(cpu or '').strip().upper()
    if identity:
        return TABLES['cpu_modes'].get(identity)
    if profile == 'q03udv':
        return 'q71'
    if profile == 'q02':
        return 'q02'
    if profile in ('l', 'l02'):
        return 'q66'
    return None


def _descriptors(operands, mode='q71', storage_context=None):
    decoded = []
    cost = 0
    for raw in operands:
        value, extra = operand(raw, mode=mode, storage_context=storage_context)
        if mode != 'q71':
            ts = _tokens(raw)
            terminal = ts[-1]
            extra = sum(t[1] in (0xf3, 0xf8, 0xf9, 0xfc, 0xff) for t in ts[:-1])
            if terminal[1] == 0xdf:
                extra = 1
            elif terminal[1] in (0xa8, 0xb4) and value[6] == 0xb0:
                extra += 2
            elif value[1] == 0xf9:
                extra += 1
            elif terminal[1] == 0xee:
                extra += 1 if terminal == b'\x04\xee\0\x04' else (len(terminal) - 2) // 2 + 1
            else:
                extra += (len(terminal) - 2) // 2
        decoded.append(validate_descriptor(value, mode=mode))
        cost += extra
    return decoded, cost


def _q71_width(header, operands, storage_context=None):
    family, variant = header[1], header[3] if len(header) >= 5 else 0
    descriptors, cost = _descriptors(operands, storage_context=storage_context)
    extra = sum(t[1] in (0xe9, 0xeb) and len(t) in (4, 5) for raw in operands for t in _tokens(raw))
    pulse = len(header) >= 6 and header[4] == 2
    if family < 0x40:
        return basic(family, descriptors, cost + extra)
    if family == 0x41:
        return _pchk_width(header, operands)
    if family in (0x42, 0x62, 0x6c):
        if (family, variant) == (0x62, 1) and not operands:
            return 0
        width = bytes.fromhex(TABLES['special_widths'][f'{family:02x}'])[variant * 3]
        if width == 255:
            raise GXWFormatError('Q special application storage is unavailable')
        if not width and family != 0x42:
            width = 1
        return width + sum(not compact(family, variant, i, d) for i, d in enumerate(descriptors[:2]))
    if (family, variant) == (0x4c, 0):
        optimized = application(family, variant, descriptors, cost + extra)
        return (optimized if optimized > 0 else cost + extra + 1) + pulse
    cost += sum(group_extra(family, variant, i, d) for i, d in enumerate(descriptors))
    optimized = application(family, variant, descriptors, cost)
    if optimized >= 0:
        return optimized + extra + pulse
    base = 2 if (family, variant) in ((0x45, 1), (0x47, 2), (0x67, 0)) else 1
    width = base + cost + extra
    if pulse:
        width += 1
    elif family == 0x63:
        width += int(variant <= 3 or 5 <= variant <= 9)
    elif family == 0x54:
        width += int(0x1a <= variant <= 0x1f)
    elif (family, variant) in ((0x57, 4), (0x61, 1), (0x58, 0xa), (0x4d, 1), (0x53, 0x11), (0x53, 0x12), (0x65, 1), (0x65, 2)):
        width += 1
    if (family, variant) == (0x6a, 0):
        width += 1
    return width


def _header_width(raw):
    return 1 if len(raw) <= 3 else raw[2]


def _pchk_width(header, operands):
    # Native 52C3B reads the one string token's extent directly. The legal
    # source mnemonics are LDPCHK/ANDPCHK/ORPCHK (SH080809ENG Rev.X p.733).
    # Independent Q02/QPH controls cover all three, empty/multibyte/32-byte
    # names. File existence and CPU admission remain source diagnostics.
    if (len(header) != 6 or header[3] != 0 or header[4] not in (0x10, 0x11, 0x12)
            or len(operands) != 1):
        raise GXWFormatError('PCHK source requires LDPCHK, ANDPCHK or ORPCHK and one program filename')
    tokens = _tokens(operands[0])
    if len(tokens) != 1 or tokens[0][1] != 0xee:
        raise GXWFormatError('PCHK source requires a program filename string')
    return (len(tokens[0]) - 2) // 2 + 2


def _context_covers_cpu(context_cpu, cpu) -> bool:
    """A context read for a merged menu option covers each of its members.

    One saved project carries one allocation, and the menu option it was read
    from names its members; the exact member a caller asks about is one of them.
    """
    context_text = str(context_cpu or "").strip().upper()
    cpu_text = str(cpu or "").strip().upper()
    if not context_text or not cpu_text:
        return True
    if context_text == cpu_text:
        return True
    # A context read from one member also covers a merged option that contains
    # that member, and a merged option's context covers each of its members: a
    # saved project carries one allocation, not one per spelling of the CPU.
    context_members = {part.strip() for part in context_text.removesuffix("(REMOTEI/O)").split("/")
                       if part.strip()}
    cpu_members = {part.strip() for part in cpu_text.removesuffix("(REMOTEI/O)").split("/")
                   if part.strip()}
    return bool(context_members & cpu_members)


def encode_q_header(op, args, operands, *, profile='q03udv', cpu=None, source=None,
                    storage_context=None, target_identity=None):
    """Construct a stored header without treating a lexical profile as CPU admission."""
    if storage_context is not None:
        if not isinstance(storage_context, QStorageContext):
            raise GXWFormatError('invalid Q source storage context')
        target = target_identity if target_identity is not None else cpu
        if target is not None and not _context_covers_cpu(storage_context.cpu, target):
            raise GXWFormatError('Q source storage context differs from the saved CPU')
        if cpu is None:
            cpu = storage_context.cpu
    if source is not None and storage_context is None:
        before = tuple(b''.join(token.raw for token in value.tokens) for value in source.operands)
        old_header = encode_q_header(source.mnemonic, source.args, before, profile=profile, cpu=cpu)
        old_width = _header_width(old_header)
        if source.step_width != old_width:
            raise GXWFormatError('original Q stored width differs; its project allocation context is required')
    if op == 'NOP' and not args and not operands:
        return b'\x02\x02'
    choices = _header_choices(op, len(args), profile=profile)
    if not choices:
        raise GXWFormatError('Q instruction mnemonic/arity has no proven storage descriptor')
    mode = _cpu_mode(profile, cpu)
    results = set()
    for initial, _ in choices:
        family, variant = initial[1], initial[3] if len(initial) >= 5 else 0
        # Native M./U. macro placeholders are stored with width 1, independent
        # of their unexpanded arguments. Their binding/expansion is a separate
        # source check; applying the ordinary named-call width would corrupt
        # every newly constructed macro. Independent controls cover all six
        # Q/L CPU storage groups and ordinary/pulse/constant/modifier forms.
        descriptors = [] if family in (0x78, 0x79) else _descriptors(
            operands, mode=mode, storage_context=storage_context)[0]
        normalized = family - 1 if family in (0x29, 0x2b) else family
        if family in (0x78, 0x79):
            width = 1
        elif family >= 0x70:
            if family in (0x72, 0x73) and (not descriptors or descriptors[0][6] not in (
                    0xd8 if family == 0x72 else 0xd9, 0xdf)):
                continue
            if any(d[6] == 0xd2 or d[6] in (0xe8, 0xea) and
                   (mode == 'q71' or d[4] == 255) for d in descriptors):
                continue
            width = _named_width(initial, operands, mode, storage_context)
        elif any(operand_rule_status(normalized, variant, i, d) < 0 for i, d in enumerate(descriptors)):
            continue
        elif mode == 'q71':
            width = _q71_width(initial, operands, storage_context)
        elif mode in ('q02', 'q61', 'q63', 'q66'):
            width = _q02_width(initial, operands, mode=mode, storage_context=storage_context)
        else:
            width = _common_width(initial, operands, storage_context)
        if not 0 <= width <= 127 or width == 0 and (family, variant) != (0x62, 1):
            raise GXWFormatError('Q instruction stored width exceeds its bounded field')
        results.add(bytes((3, family, 3)) if family < 0x40 and width == 1 else
                    bytes((4, family, width, 4)) if family < 0x40 else initial[:2] + bytes((width,)) + initial[3:])
    if len(results) != 1:
        raise GXWFormatError('Q instruction parameter roles or stored header are ambiguous')
    return results.pop()


def _named_width(header, operands, mode, storage_context=None):
    if mode is None:
        raise GXWFormatError('named instruction storage needs the exact saved CPU')
    _, cost = _descriptors(operands, mode, storage_context)
    codes = (0xe9, 0xeb) if mode == 'q71' else (0xb0, 0xbf, 0xe9, 0xeb)
    extra = sum(t[1] in codes and len(t) in (4, 5) for raw in operands for t in _tokens(raw))
    return (len(header) - 5) // 2 + 2 + cost + extra + (mode == 'q71' and header[4] == 2)


def _legacy_matrix(family, descriptor):
    kind = TABLES['basic_legacy_matrix_kind'][family]
    device_class = DEVICE_CLASSES[descriptor[6] * 2]
    values = bytes.fromhex(TABLES['basic_legacy_device_widths'])
    index = kind * 23 + device_class
    return values[index] if device_class < 23 and index < len(values) else 0


def _legacy_basic(family, descriptors, cost, mode='q02'):
    if family in (0x29, 0x2b):
        family -= 1
    if not descriptors:
        return 1
    kinds = TABLES.get('basic_' + mode, TABLES['basic_legacy'])
    kind = kinds[family]
    first = descriptors[0]
    def plain(d):
        return d[0] == 255 and not d[1] and d[6] != 0xb0
    def matrix(d):
        return _legacy_matrix(family, d)
    def extended(d):
        return mode == 'q66' and (d[6] == 0xb0 or
            d[6] == 0xab and d[1] == 0xf8 and 0x3e0 <= u32(d, 13) <= 0x3e3
            and u32(d) >= 10000 and d[2] == 255)
    if kind in (1, 10, 11):
        width = 1
    elif kind in (2, 3, 5):
        width = matrix(first) if plain(first) else 0
        if not width or family in (0x25, 0x26, 0x27):
            width = cost + 1
            if family not in (0x25, 0x26, 0x27) and extended(first):
                width = cost
    elif kind == 4:
        second = descriptors[1]
        width = matrix(second) if second[4] == 255 and not second[1] else 0
        if not width:
            width = cost + 3
    elif kind == 6:
        width = matrix(first) if plain(first) else 0
        if width:
            if not first[7] & 1:
                width = 2
        else:
            width = cost + 1
            if extended(first):
                width = cost if first[7] & 1 else 3 if first[0] == 255 else width
    elif kind == 9:
        second = descriptors[1]
        width = matrix(second) if second[4] == 255 and not second[1] and second[6] != 0xb0 else 0
        if not width:
            width = 3 if extended(second) else cost + 1
    else:
        width = cost + (1 if kind in (7, 8, 12) else 2)
    if family in (4, 5):
        width += 2
    elif family in (10, 11, 21, 22):
        width += 3
    return width


_BIT_DEVICES = frozenset((*range(0x90, 0x95), 0x98, 0x99, *range(0x9c, 0xa4),
                          0xc0, 0xc1, 0xc3, 0xc4, 0xc6, 0xc7))
_WORD_DEVICES = frozenset((*range(0xa8, 0xb1), 0xb4, 0xb5, 0xbf, 0xc2, 0xc5, 0xc8,
                           0xe3, 0xe4, *range(0xe8, 0xef)))


def _compact_parameter(d, words, *, extended=False):
    if d[6] in _WORD_DEVICES and (extended or d[6] != 0xb0):
        return True
    if extended and d[6] == 0xcc:
        return True
    return (d[6] in _BIT_DEVICES and d[4] == 255 and
            (d[3] == 255 or d[3] == words * 4 and u32(d) % 16 == 0))


def _legacy_compact_width(header, descriptors, width, mode):
    family, variant = header[1], header[3] if len(header) >= 5 else 0
    modifier = header[4] if len(header) >= 6 else 0
    for index, row in enumerate(TABLES['legacy_compact']):
        if row[:3] != [family, variant, modifier]:
            continue
        if index < 6:
            count, kind = 1, 1
        elif index < 24 or index == 33:
            count, kind = 2, 2
        else:
            table = TABLES['legacy_compact_parameters'][mode if mode in ('q61', 'q66') else 'q02']
            parameters = table[f'{family:02x}:{variant:02x}']
            count, kind = parameters[2], parameters[4]
            if kind == 255:
                return width
        if count > len(descriptors):
            return width
        ds = descriptors[:count]
        if any(d[0] != 255 or d[1] for d in ds):
            return width
        kinds = ([1] * count if kind == 1 else [2] * count if kind == 2 else
                 [1] * (count - 1) + [2] if kind == 3 else [2, 1] if kind == 4 else [])
        if len(kinds) != count or not all(_compact_parameter(d, words) for d, words in zip(ds, kinds)):
            return width
        replacement = row[5] if mode == 'q66' else row[4] if mode == 'q61' else row[3]
        return width - 1 if replacement == 255 else replacement if replacement else width
    return width


def _legacy_mov_compact(descriptor):
    if descriptor[0] != 255 or descriptor[1]:
        return False
    kind = DEVICE_CLASSES[descriptor[6] * 2]
    if kind in (3, 4, 7, 8, 13, 15, 16):
        return True
    return (kind in (0, 1, 6, 19) and descriptor[3] == 4 and descriptor[4] == 255
            and u32(descriptor) % 16 == 0 and descriptor[6] != 0x98)


def _q02_width(header, operands, *, mode='q02', storage_context=None):
    family, variant = header[1], header[3] if len(header) >= 5 else 0
    descriptors, cost = _descriptors(operands, mode=mode, storage_context=storage_context)
    extra = sum(t[1] in (0xb0, 0xbf, 0xe9, 0xeb) and len(t) in (4, 5)
                for raw in operands for t in _tokens(raw))
    if family < 0x40:
        return _legacy_basic(family, descriptors, cost + extra, mode)
    if family == 0x41:
        return _pchk_width(header, operands)
    if (family, variant) == (0x4c, 0):
        width = 2 if all(_legacy_mov_compact(d) for d in descriptors) else cost + extra + 1
        if mode == 'q66':
            optimized = _q66_application(family, variant, descriptors, cost + extra)
            if optimized > 0:
                return optimized + (len(header) >= 6 and header[4] == 2)
        return width
    if family in (0x42, 0x62, 0x6c):
        if (family, variant) == (0x62, 1) and not operands:
            return 0
        width = bytes.fromhex(TABLES['special_widths_legacy'][f'{family:02x}'])[variant * 3]
        if width == 255:
            raise GXWFormatError('legacy Q special application storage is unavailable')
        return max(width, 1) if family != 0x42 else width
    base = 2 if (family, variant) in ((0x45, 1), (0x47, 2), (0x67, 0)) else 1
    width = base + cost + extra
    if mode == 'q66':
        optimized = _q66_application(family, variant, descriptors, cost)
        if optimized >= 0:
            width = optimized + extra
    # The final compact pass reparses the original source tokens (6F76D),
    # rather than using 5129F's allocation-normalized ZR descriptors.
    compact_descriptors = _descriptors(operands, mode=mode)[0] if storage_context is not None else descriptors
    return _legacy_compact_width(header, compact_descriptors, width, mode)


def _q66_application(family, variant, descriptors, cost):
    row = TABLES['application_q66'].get(f'{family:02x}:{variant:02x}')
    if row is None:
        return -1
    base, destination, kind, category = row
    ds = []
    for original in descriptors:
        d = bytearray(original)
        if d[0] != 255 or d[6] == 0x98:
            return -1
        if d[1]:
            if d[1] != 0xf8:
                return -1
            if d[6] == 0xab:
                if not (0x3e0 <= u32(d, 13) <= 0x3e3 and u32(d) >= 10000 and d[2] == 255):
                    return -1
                d[1], d[6] = 0, 0xb0
        ds.append(bytes(d))
    counts = len(ds)
    words = ([1] * counts if category in (1, 5) else [2] * counts if category in (2, 6) else
             [1] * (counts - 1) + [2] if category == 3 else [2, 1] if category == 4 else
             [2] * (counts - 1) + [0] if category == 7 else [])
    if len(words) != counts:
        return -1
    for d, word_count in zip(ds, words):
        if word_count == 0:
            valid = d[6] in _WORD_DEVICES or d[6] == 0xcc
        else:
            valid = _compact_parameter(d, word_count, extended=True)
        if not valid or category in (5, 6) and (d[0] != 255 or d[1]):
            return -1
    if kind == 5 and counts == 3 and ds[0] == ds[2]:
        kind = 6
    if ds and all(d[6] == 0xcc for d in ds) and kind != 0xfe:
        return 2 if kind == 6 else 1
    z = [d[6] == 0xcc for d in ds]
    extended = [d[6] == 0xb0 for d in ds]
    if kind == 1 and any(z[:2]):
        return cost
    if kind == 2:
        if z[1]:
            return cost
        if z[0] and extended[1]:
            return cost + 2
    elif kind in (3, 5):
        if (z[0] and (z[1] and extended[2] or z[2])) or z[1] and z[2]:
            return base + cost - 1
        if z[0] and extended[2] and not z[1] or z[1] and extended[2] and not z[0]:
            return base + cost
    elif kind == 4 and z[0] and extended[1]:
        return cost
    elif kind == 6:
        if z[1] and extended[0] and extended[2]:
            return cost + 1
        if not z[1] and z[0] and z[2]:
            return cost
        cost += 1
    return base + cost + (destination if ds and extended[-1] else 0)


def _common_width(header, operands, storage_context=None):
    family, variant = header[1], header[3] if len(header) >= 5 else 0
    descriptors, _ = _descriptors(operands, storage_context=storage_context)
    if not descriptors:
        return 1
    if family in (0, 1, 6, 7, 12, 13, 0x20, 0x23, 0x24) and len(descriptors) == 1:
        d = descriptors[0]
        if d[0] == 255 and not d[1] and d[3] == d[4] == d[5] == 255 and d[6] in (0x90, 0x9c, 0x9d):
            return 1
    if (family, variant) == (0x4c, 0) and len(descriptors) == 2:
        if all(d[0] == 255 and not d[1] and d[3] == d[4] == d[5] == 255
               and d[6] in (0x90, 0x9c, 0x9d, 0xa8, 0xe8, 0xea) and u32(d) <= 65535 for d in descriptors):
            return 2
    raise GXWFormatError('stored width needs an exact CPU rule or a compatible original source header')


def q_header_compatible(source, op, args, operands, *, profile, cpu=None, storage_context=None):
    """Prove width invariance before preserving a source header on an edit."""
    if source is None or source.mnemonic != op or len(source.operands) != len(operands):
        return False
    before = tuple(b''.join(token.raw for token in value.tokens) for value in source.operands)
    try:
        old_header = encode_q_header(op, source.args, before, profile=profile, cpu=cpu,
                                     storage_context=storage_context)
        new_header = encode_q_header(op, args, operands, profile=profile, cpu=cpu,
                                     storage_context=storage_context)
    except GXWFormatError:
        if before == tuple(operands):
            return True
        # An opaque extension does not prevent a proven independent operand
        # update. MOV's destination width depends on its storage class, cost
        # and modifiers, not its ordinary D address. Keep the extension's
        # complete bytes and require exactly the same saved D allocation.
        # Do not turn the display equivalence of FD/FE/FF into a new width
        # rule, or apply this invariant to alias-sensitive arithmetic forms.
        if op != 'MOV' or len(before) != 2 or before[0] != operands[0]:
            return False
        if not any(token[1] in (0xfd, 0xfe, 0xff) for token in _tokens(before[0])):
            return False
        if (len(_tokens(before[1])) != 1 or len(_tokens(operands[1])) != 1
                or _tokens(before[1])[0][1] != 0xa8 or _tokens(operands[1])[0][1] != 0xa8):
            return False
        mode = _cpu_mode(profile, cpu or getattr(storage_context, 'cpu', None))
        try:
            old, old_cost = _descriptors((before[1],), mode=mode, storage_context=storage_context)
            new, new_cost = _descriptors((operands[1],), mode=mode, storage_context=storage_context)
        except GXWFormatError:
            return False
        if old[0][6] != 0xa8 or new[0][6] != 0xa8:
            return False
        return old_cost == new_cost and old[0][:9] + old[0][13:] == new[0][:9] + new[0][13:]
    old_width = _header_width(old_header)
    return source.step_width == old_width and old_header == new_header


__all__ = ['QStorageContext', 'q_storage_context', 'q_project_storage_context',
           'encode_q_operand', 'encode_q_header', 'q_header_compatible']
