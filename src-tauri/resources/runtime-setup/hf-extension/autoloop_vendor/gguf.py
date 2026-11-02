"""GGUF v3 reader/writer for training-free whole-block duplication.

Copies tensor bytes without dequantization or learning any weights. Modifications
are UNROLLED repeats: a standard runner will see extra blocks, not custom residual
loops. Currently supports single-file, little-endian, standard blk.N models.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import struct
import tempfile
from typing import Callable
from .errors import checkpoint

MAGIC = b"GGUF"
V3 = 3
# uint8,int8,uint16,int16,uint32,int32,float32,bool,string,array,uint64,int64,float64
SCALAR = {0:'<B',1:'<b',2:'<H',3:'<h',4:'<I',5:'<i',6:'<f',7:'<?',10:'<Q',11:'<q',12:'<d'}
GGUF_STRING = 8
GGUF_ARRAY = 9
SUPPORTED = {'llama','qwen2','qwen3','mistral','gemma2','phi3','gemma3','qwen35','dory'}
BLOCK_RE = re.compile(r'^blk\.(\d+)\.(.+)$')
MAX_STRING = 256 * 1024 * 1024
MAX_ENTRIES = 3_000_000

class GGUFError(ValueError):
    pass


def _exact(fp, n: int) -> bytes:
    if n < 0 or n > MAX_STRING:
        raise GGUFError(f'Invalid size: {n}')
    b = fp.read(n)
    if len(b) != n:
        raise GGUFError('Truncated GGUF')
    return b


def _read(fp, fmt: str):
    return struct.unpack(fmt, _exact(fp, struct.calcsize(fmt)))[0]


def _string(fp) -> str:
    n = _read(fp, '<Q')
    return _exact(fp, n).decode('utf-8')


def _write_string(v: str) -> bytes:
    b = v.encode('utf-8')
    return struct.pack('<Q', len(b)) + b


def _parse_value(fp, type_id: int, decode: bool = False, depth=0):
    if depth > 3:
        raise GGUFError('Nested metadata too deep')
    if type_id in SCALAR:
        val = _read(fp, SCALAR[type_id])
        return val if decode else None
    if type_id == GGUF_STRING:
        n = _read(fp, '<Q')
        if decode:
            return _exact(fp, n).decode('utf-8')
        fp.seek(n, os.SEEK_CUR)
        return None
    if type_id == GGUF_ARRAY:
        inner = _read(fp, '<I')
        n = _read(fp, '<Q')
        if n > MAX_ENTRIES:
            raise GGUFError('Too many array elements')
        if inner in SCALAR and not decode:
            fp.seek(n * struct.calcsize(SCALAR[inner]), os.SEEK_CUR)
            return None
        values = [] if decode else None
        for _ in range(n):
            v = _parse_value(fp, inner, decode, depth + 1)
            if decode:
                values.append(v)
        return (inner, values) if decode else None
    raise GGUFError(f'Unknown GGUF metadata type: {type_id}')


def _write_value(type_id: int, value) -> bytes:
    if type_id in SCALAR:
        return struct.pack(SCALAR[type_id], value)
    if type_id == GGUF_STRING:
        return _write_string(value)
    if type_id == GGUF_ARRAY:
        inner, vals = value
        return struct.pack('<IQ', inner, len(vals)) + b''.join(_write_value(inner, v) for v in vals)
    raise GGUFError(f'Cannot write metadata type {type_id}')


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass(frozen=True)
class Metadata:
    key: str
    type_id: int
    offset: int
    size: int
    value: object | None = None


@dataclass(frozen=True)
class Tensor:
    name: str
    shape: tuple[int,...]
    type_id: int
    offset: int
    size: int = 0


class GGUFModel:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise GGUFError(f'Model not found: {self.path}')
        self.file_size = self.path.stat().st_size
        with self.path.open('rb') as f:
            if _exact(f, 4) != MAGIC:
                raise GGUFError('Not a GGUF file')
            self.version = _read(f, '<I')
            if self.version != V3:
                raise GGUFError('Only little-endian GGUF version 3 is supported')
            num_tensors = _read(f, '<Q')
            num_metadata = _read(f, '<Q')
            if num_tensors > MAX_ENTRIES or num_metadata > MAX_ENTRIES:
                raise GGUFError('Unreasonably large GGUF header counts')
            self.metadata = []
            quick = {}
            for _ in range(num_metadata):
                key = _string(f)
                t = _read(f, '<I')
                start = f.tell()
                wanted = key in ('general.architecture','general.alignment','split.count') or key.endswith(('.block_count','.context_length','.expert_count','.expert_used_count'))
                value = _parse_value(f, t, wanted)
                self.metadata.append(Metadata(key,t,start,f.tell()-start,value))
                if wanted:
                    quick[key] = value
            self.architecture = quick.get('general.architecture')
            self.has_vision_metadata=any(any(term in item.key.lower() for term in ('vision','projector','image_token')) or item.key.startswith('clip.') for item in self.metadata)
            self.has_experts=any(type(value) is int and value>0 for key,value in quick.items() if key.endswith(('.expert_count','.expert_used_count')))
            if not isinstance(self.architecture, str):
                raise GGUFError('Missing general.architecture')
            self.block_key = f'{self.architecture}.block_count'
            self.block_count = quick.get(self.block_key)
            context=quick.get(self.architecture+'.context_length')
            self.context_tokens=context if type(context) is int and 0<context<=16*1048576 else None
            if not isinstance(self.block_count, int) or self.block_count < 2:
                raise GGUFError(f'Missing/invalid {self.block_key}')
            if quick.get('split.count', 1) != 1:
                raise GGUFError('Split GGUF not supported: first merge the shards')
            self.alignment = quick.get('general.alignment') or 32
            if not isinstance(self.alignment, int) or self.alignment < 8 or self.alignment > 4096 or self.alignment % 8:
                raise GGUFError('Unsupported GGUF alignment')
            infos = []
            names = set()
            for _ in range(num_tensors):
                name = _string(f)
                nd = _read(f, '<I')
                if nd > 8:
                    raise GGUFError(f'Invalid tensor rank for {name}')
                shape = tuple(_read(f, '<Q') for _ in range(nd))
                t = _read(f, '<I')
                off = _read(f, '<Q')
                if name in names:
                    raise GGUFError(f'Duplicate tensor name: {name}')
                names.add(name)
                if off % self.alignment:
                    raise GGUFError(f'Unaligned tensor: {name}')
                infos.append(Tensor(name,shape,t,off))
            self.data_start = _align(f.tell(), self.alignment)
            if self.data_start > self.file_size:
                raise GGUFError('Truncated tensor header')
            sorted_infos = sorted(infos, key=lambda x:x.offset)
            sizes = {}
            for j,t in enumerate(sorted_infos):
                end = sorted_infos[j+1].offset if j + 1 < len(sorted_infos) else self.file_size - self.data_start
                if end <= t.offset or self.data_start + end > self.file_size:
                    raise GGUFError(f'Invalid tensor offsets near {t.name}')
                sizes[t.name] = end - t.offset
            self.tensors = [Tensor(t.name,t.shape,t.type_id,t.offset,sizes[t.name]) for t in infos]

    def metadata_value(self, key):
        """Read a declared metadata field without decoding large token arrays."""
        entry = next((item for item in self.metadata if item.key == key), None)
        if entry is None:
            return None
        with self.path.open('rb') as stream:
            stream.seek(entry.offset)
            return _parse_value(stream, entry.type_id, True)

    def _groups(self):
        groups = {}
        for tensor in self.tensors:
            match = BLOCK_RE.fullmatch(tensor.name)
            if match:
                group = groups.setdefault(int(match[1]), {})
                group[match[2]] = tensor.shape
        if set(groups) != set(range(self.block_count)):
            raise GGUFError('Incomplete/unsupported blk.N tensor groups')
        return groups

    def _structured_layout(self, groups):
        """Recognize supported mixed block *patterns*, not just an architecture label."""
        n = self.block_count
        if self.architecture == 'qwen35':
            tail = self.metadata_value('qwen35.nextn_predict_layers') or 0
            if type(tail) is not int or tail not in (0, 1) or (n-tail) < 8 or (n-tail) % 4:
                raise GGUFError('Qwen3.5 layout needs complete 3-recurrent/1-attention groups and at most one terminal MTP block')
            base = n - tail
            for index in range(base):
                suffixes = set(groups[index])
                if index % 4 < 3:
                    required = {'ssm_a', 'ssm_conv1d.weight', 'ssm_out.weight', 'attn_qkv.weight'}
                    if not required.issubset(suffixes) or 'attn_q.weight' in suffixes:
                        raise GGUFError(f'Qwen3.5 recurrent layout mismatch at layer {index}')
                else:
                    required = {'attn_q.weight', 'attn_k.weight', 'attn_v.weight', 'attn_output.weight'}
                    if not required.issubset(suffixes) or 'ssm_a' in suffixes:
                        raise GGUFError(f'Qwen3.5 attention layout mismatch at layer {index}')
            if tail and not any(key.startswith('nextn.') for key in groups[n-1]):
                raise GGUFError('Qwen3.5 MTP tail not identified; cannot safely move it')
            if not tail and any(key.startswith('nextn.') for group in groups.values() for key in group):
                raise GGUFError('Undeclared Qwen3.5 MTP tensors')
            flags = self.metadata_value('qwen35.attention.recurrent_layers')
            if flags is not None:
                if (not isinstance(flags, tuple) or len(flags[1]) != n or
                    any(bool(flags[1][i]) != (i % 4 < 3) for i in range(base))):
                    raise GGUFError('Qwen3.5 recurrent layer metadata disagrees with tensors')
            return {'size': 4, 'start': 0, 'stop': base, 'tail': tail}
        if self.architecture == 'dory':
            prefix = self.metadata_value('dory.input_layer_count')
            middle = self.metadata_value('dory.recurrent_layer_count')
            suffix = self.metadata_value('dory.output_layer_count')
            pattern = self.metadata_value('dory.layer_pattern')
            if (any(type(v) is not int or v < 0 or v % 8 for v in (prefix,middle,suffix))
                or prefix + middle + suffix != n or middle < 8):
                raise GGUFError('Dory stage counts do not describe aligned eight-block recurrent loops')
            tokens = pattern.strip('-').split('-') if isinstance(pattern,str) else []
            if len(tokens) != n//2 or not all(tokens[i:i+4] == tokens[:4] for i in range(0,len(tokens),4)):
                raise GGUFError('Dory per-pair layer pattern is not a uniform four-pair cycle')
            if self.metadata_value('dory.recurrent_kv_cache_mode') != 'per_loop':
                raise GGUFError('Dory recurrent cache mode requires a separately verified adapter')
            for index in range(n):
                names = set(groups[index])
                if index % 2 == 0:
                    if not {'attn_q.weight','attn_output.weight'}.issubset(names) or 'ffn_down.weight' in names:
                        raise GGUFError(f'Dory attention layer mismatch at {index}')
                elif not {'ffn_down.weight','ffn_up.weight'}.issubset(names) or 'attn_q.weight' in names:
                    raise GGUFError(f'Dory feedforward layer mismatch at {index}')
            return {'size':8,'start':prefix,'stop':prefix+middle,'tail':0}
        raise GGUFError('No structural adapter for this architecture')

    def validate_for_surgery(self):
        if self.architecture not in SUPPORTED:
            raise GGUFError(f'Architecture {self.architecture!r} not in tested compatibility allowlist; no modification made')
        if self.has_experts:
            raise GGUFError('Mixture-of-experts metadata requires a separately validated surgery adapter')
        groups = self._groups()
        if self.architecture in ('qwen35','dory'):
            self._structured_layout(groups)
            return True
        for group in groups.values():
            if any(value in name.lower() for name in group for value in
                   ('exps','shexp','expert','gate_inp','ssm','time_mix','delta_net')):
                raise GGUFError('MoE or hybrid/recurrent blocks need a separately validated surgery adapter')
        pattern = groups[0]
        if len(pattern) < 3 or any(group != pattern for group in groups.values()):
            raise GGUFError('Heterogeneous layer tensors: not safe for generic block surgery')
        return True

    def _mapping_info(self, mapping):
        n = self.block_count
        if self.architecture in ('qwen35','dory'):
            layout = self._structured_layout(self._groups())
            size = layout['size']
            if len(mapping) != n + size:
                raise GGUFError(f'{self.architecture} requires a complete {size}-block pattern repeat')
            for pos in range(layout['start'],layout['stop']-size+1,size):
                if self.architecture == 'qwen35' and pos == 0:
                    continue
                expected = list(range(n))
                expected[pos+size:pos+size] = list(range(pos,pos+size))
                if mapping == expected:
                    return {'start':pos,'size':size}
            raise GGUFError(f'{self.architecture} repeat must preserve complete contiguous layer patterns')
        compact = [block for i,block in enumerate(mapping) if i==0 or block!=mapping[i-1]]
        if compact != list(range(n)):
            raise GGUFError('Mapping may only repeat existing blocks in their original order')
        return None

    def propose_variants(self, maximum=3, experimental_damping=False):
        """Use architecture-specific complete motifs instead of arbitrary single layers."""
        self.validate_for_surgery()
        if self.architecture not in ('qwen35','dory'):
            return repeat_variants(self.block_count,maximum,experimental_damping)
        layout = self._structured_layout(self._groups())
        size = layout['size']
        positions = list(range(layout['start'],layout['stop']-size+1,size))
        if self.architecture == 'qwen35':
            positions = [x for x in positions if x != 0]
        if not positions:
            return []
        center=(layout['start']+layout['stop']-size)//2
        positions.sort(key=lambda pos:(abs(pos-center),pos))
        results=[]
        for position in positions[:maximum]:
            mapping=list(range(self.block_count))
            mapping[position+size:position+size]=list(range(position,position+size))
            results.append((f'{self.architecture}-whole-pattern-{position}',mapping,False))
        return results

    def describe(self) -> dict:
        try:
            self.validate_for_surgery()
            supported, reason = True, ('Eligible architecture-aware full-pattern repetition; runtime loading and quality confirmation remain required.' if self.architecture in ('qwen35', 'dory') else 'Eligible homogeneous decoder blocks; actual runtime and quality verification are still required.')
        except GGUFError as error:
            supported, reason = False, str(error)
        return {'file':str(self.path), 'bytes':self.file_size,'architecture':self.architecture,
                'block_count':self.block_count,'tensor_count':len(self.tensors),
                'blockCount':self.block_count,'contextTokens':self.context_tokens,'quantTypes':sorted({t.type_id for t in self.tensors}),
                'requiresVisionCompatibility':self.has_vision_metadata,
                'alignment':self.alignment, 'supported':supported,'reason':reason}

    def build(self, destination: str | Path, mapping: list[int], progress: Callable[[str],None] | None = None,
              damped: bool = False, cancel=None) -> Path:
        checkpoint(cancel)
        self.validate_for_surgery()
        if len(mapping) <= self.block_count or len(mapping) > self.block_count + 8:
            raise GGUFError('Mapping must add between 1 and 8 duplicate blocks')
        if not mapping or any(not (0 <= n < self.block_count) for n in mapping):
            raise GGUFError('Invalid source block indices')
        # Structural adapters accept only one complete motif after that motif.
        mapping_info = self._mapping_info(mapping)
        if damped and self.architecture in ('qwen35','dory'):
            raise GGUFError('Damping hybrid/recurrent architectures is not validated')
        output = Path(destination).resolve()
        if output == self.path:
            raise GGUFError('Never overwrite the source model')
        if output.suffix.lower() != '.gguf':
            raise GGUFError('Output must end in .gguf')
        output.parent.mkdir(parents=True,exist_ok=True)
        if output.exists():
            raise GGUFError('Output file already exists; refusing to overwrite')
        # A damped loop scales terminal attention and FFN projections for every
        # reused copy, including the first occurrence: x + F(x) becomes a series
        # of smaller residual updates. This modifies bytes, not by training.
        repeat_counts = {i:mapping.count(i) for i in set(mapping)}
        if damped:
            from .scaling import payload_bytes
            for idx,count in repeat_counts.items():
                if count <= 1:
                    continue
                in_block = [t for t in self.tensors if t.name.startswith(f'blk.{idx}.')]
                suffixes = {t.name.split('.',2)[2] for t in in_block}
                if not {'attn_output.weight','ffn_down.weight'}.issubset(suffixes):
                    raise GGUFError(f'Damped loop requires attention and FFN terminal projections in block {idx}')
                if any('ffn_down_exps' in x or 'ffn_down_shexp' in x for x in suffixes):
                    raise GGUFError('Damped mixture-of-experts residuals not supported')
                for t in in_block:
                    if _is_terminal_projection(t.name):
                        payload_bytes(t)  # Validate formats before opening output.
        replacements = []
        blocks:dict[int,list[Tensor]]={}

        for t in self.tensors:
            m = BLOCK_RE.fullmatch(t.name)
            if m:
                blocks.setdefault(int(m[1]),[]).append(t)
        for t in self.tensors:
            if not BLOCK_RE.fullmatch(t.name):
                replacements.append((t.name,t))
        for new_idx,old_idx in enumerate(mapping):
            for t in blocks[old_idx]:
                suffix = t.name.split('.',2)[2]
                replacements.append((f'blk.{new_idx}.{suffix}',t))
        # GGUF can store tensor infos in any order. We emit physical data in the same order.
        offset = 0
        out_infos = []
        for name,t in replacements:
            offset = _align(offset,self.alignment)
            out_infos.append((name,t,offset))
            offset += t.size
        estimated = offset + self.data_start + len(out_infos)*100
        disk = shutil.disk_usage(output.parent).free
        if disk < estimated + 32*1024*1024:
            raise GGUFError(f'Not enough free disk space (need about {estimated/2**30:.2f} GiB)')
        replacement_meta = {}
        replacement_meta[self.block_key] = len(mapping)
        if self.architecture == 'dory':
            if not mapping_info:
                raise GGUFError('Dory recurrent loop mapping was not validated')
            old_count = self.metadata_value('dory.recurrent_layer_count')
            old_pattern = self.metadata_value('dory.layer_pattern')
            tokens = old_pattern.strip('-').split('-')
            start_pair = mapping_info['start'] // 2
            end_pair = start_pair + mapping_info['size'] // 2
            tokens[end_pair:end_pair] = tokens[start_pair:end_pair]
            replacement_meta['dory.recurrent_layer_count'] = old_count + mapping_info['size']
            replacement_meta['dory.layer_pattern'] = '-'.join(tokens) + '-'
        # Architecture per-block arrays: remap elements alongside the block indices.
        # All other metadata (tokenizer, templates, etc.) is copied byte-for-byte.
        with self.path.open('rb') as src:
            for entry in self.metadata:
                if entry.key.startswith(self.architecture+'.') and entry.type_id == GGUF_ARRAY:
                    src.seek(entry.offset)
                    inner = _read(src,'<I')
                    num = _read(src,'<Q')
                    if num == self.block_count:
                        src.seek(entry.offset)
                        full = _parse_value(src,GGUF_ARRAY,True)
                        if full[0] not in SCALAR:
                            raise GGUFError(f'Unsupported architecture-specific array: {entry.key}')
                        replacement_meta[entry.key] = (full[0],[full[1][i] for i in mapping])
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='wb', prefix='.autoloop_',suffix='.gguf.tmp',dir=output.parent,delete=False) as out:
                    temp_path = Path(out.name)
                    out.write(MAGIC)
                    out.write(struct.pack('<IQQ',V3,len(out_infos),len(self.metadata)))
                    for entry in self.metadata:
                        out.write(_write_string(entry.key))
                        out.write(struct.pack('<I',entry.type_id))
                        if entry.key in replacement_meta:
                            out.write(_write_value(entry.type_id,replacement_meta[entry.key]))
                        else:
                            src.seek(entry.offset)
                            _copy(src,out,entry.size,cancel)
                    for name,t,new_offset in out_infos:
                        out.write(_write_string(name))
                        out.write(struct.pack('<I',len(t.shape)))
                        for dim in t.shape:
                            out.write(struct.pack('<Q',dim))
                        out.write(struct.pack('<IQ',t.type_id,new_offset))
                    _pad(out, self.alignment)
                    data_start = out.tell()
                    total = len(out_infos)
                    for j,(name,t,new_offset) in enumerate(out_infos):
                        checkpoint(cancel)
                        actual = out.tell()-data_start
                        if actual > new_offset:
                            raise GGUFError('Internal output alignment error')
                        out.write(b'\0'*(new_offset-actual))
                        src.seek(self.data_start + t.offset)
                        match = BLOCK_RE.fullmatch(name)
                        if damped and match and repeat_counts[int(mapping[int(match[1])])]>1 and _is_terminal_projection(name):
                            from .scaling import copy_scaled
                            repeat = repeat_counts[mapping[int(match[1])]]
                            copy_scaled(src,out,t,1.0/repeat,cancel=cancel)
                        else:
                            _copy(src,out,t.size,cancel)
                        if progress and (j+1 == total or (j+1)%max(1,total//10)==0):
                            progress(f'Writing tensors: {j+1}/{total}')
                    out.flush()
                    os.fsync(out.fileno())
                check=GGUFModel(temp_path)
                if check.block_count != len(mapping) or len(check.tensors) != len(out_infos):
                    raise GGUFError('Output verification failed')
                check.validate_for_surgery()
                if progress:
                    progress('Candidate tensor copy verified; publishing without replacement')
                checkpoint(cancel)
                publish_no_replace(temp_path,output)
                return output
            finally:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink()


def _copy(src,dst,n,cancel=None):
    while n:
        checkpoint(cancel)
        b=src.read(min(n,8*1024*1024))
        if not b:
            raise GGUFError('Unexpected EOF while copying tensor data')
        dst.write(b)
        n-=len(b)


def _pad(fp, alignment):
    extra = _align(fp.tell(),alignment)-fp.tell()
    if extra:
        fp.write(b'\0'*extra)


def _is_terminal_projection(name: str) -> bool:
    return name.endswith(('attn_output.weight','attn_output.bias','ffn_down.weight','ffn_down.bias'))


def variants(block_count: int, max_variants: int = 6) -> list[tuple[str,list[int],bool]]:
    """Useful generic hypotheses; all are independent of the model's weights."""
    n=block_count
    patterns = [
      ('damped-middle', [n//2], True),
      ('damped-third', [n//3], True),
      ('damped-two-thirds', [2*n//3], True),
      ('plain-middle', [n//2], False),
      ('damped-middle-pair', [max(0,n//2-1),n//2], True),
      ('plain-middle-pair', [max(0,n//2-1),n//2], False),
      ('damped-middle-triple', [max(0,n//2-1),n//2,min(n-1,n//2+1)], True),
      ('damped-quarter-pair', [n//4,3*n//4], True),
    ]
    seen=set()
    result=[]
    for name,indices,damped in patterns:
        indices=sorted(set(indices))
        mapping=[]
        for i in range(n):
            mapping.append(i)
            if i in indices:
                mapping.append(i)
        key=(tuple(mapping),damped)
        if key not in seen:
            seen.add(key)
            result.append((name,mapping,damped))
        if len(result)>=max_variants:
            break
    return result


def repeat_variants(block_count, maximum=3, experimental_damping=False):
    """Fixed hypotheses; no holdout answers are used to choose their placement."""
    n=block_count
    patterns=[('middle',[n//2]),('third',[n//3]),('two-thirds',[2*n//3]),
              ('middle-pair',[max(0,n//2-1),n//2]),('quarter',[n//4]),
              ('three-quarters',[3*n//4]),('middle-triple',[max(0,n//2-1),n//2,min(n-1,n//2+1)]),
              ('quarter-pair',[n//4,3*n//4])]
    result=[];seen=set()
    for name,indices in patterns:
        mapping=[old for old in range(n) for _ in range(2 if old in indices else 1)]
        key=tuple(mapping)
        if key in seen:continue
        seen.add(key)
        result.append((('experimental-damped-' if experimental_damping else 'repeat-')+name,mapping,experimental_damping))
        if len(result)>=maximum:break
    return result


def inspect_model(path):
    """Header/tensor-directory inspection only: no hashing, builds or execution."""
    try:return GGUFModel(path).describe()
    except (OSError,ValueError,OverflowError) as error:
        return {'file':str(path),'architecture':None,'blockCount':None,'contextTokens':None,'quantTypes':[],'requiresVisionCompatibility':False,
                'supported':False,'reason':str(error)}


def publish_no_replace(source,destination):
    """Publish an owned file without ever replacing an existing destination."""
    source,destination=Path(source),Path(destination)
    if os.name=='nt':os.rename(source,destination)
    else:
        os.link(source,destination)
        source.unlink()
