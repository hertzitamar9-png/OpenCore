"""Training-free residual damping by scaling terminal projection tensors.

This is a restricted *approximation* to a smaller-residual Euler loop. It is not
identical to intercepting intermediate residuals in a custom runtime.
"""
from __future__ import annotations
import math
import struct
from .gguf import GGUFError
from .errors import checkpoint

# GGML tensor type -> (elements/block, bytes/block, fp16 scale field offsets,
#                            fp32 scale offsets). Layout verified against
# ggml/src/ggml-common.h. Unsupported encodings fail closed.
FORMATS={
    0:(1,4,(),(0,)),          # F32 values
    1:(1,2,(0,),()),          # F16 values
    2:(32,18,(0,),()),        # Q4_0
    3:(32,20,(0,2),()),       # Q4_1 (scale and min)
    6:(32,22,(0,),()),        # Q5_0
    7:(32,24,(0,2),()),       # Q5_1
    8:(32,34,(0,),()),        # Q8_0
    9:(32,36,(0,2),()),       # Q8_1 d and precomputed s
    10:(256,84,(80,82),()),   # Q2_K d,dmin at end
    11:(256,110,(108,),()),   # Q3_K
    12:(256,144,(0,2),()),    # Q4_K
    13:(256,176,(0,2),()),    # Q5_K
    14:(256,210,(208,),()),   # Q6_K
    15:(256,292,(),(0,)),    # Q8_K
}


def payload_bytes(tensor):
    if tensor.type_id not in FORMATS:
        raise GGUFError(f'Cannot residual-scale GGML tensor type {tensor.type_id} in {tensor.name}')
    count=math.prod(tensor.shape)
    block,bytes_per,_,_=FORMATS[tensor.type_id]
    if count%block:
        raise GGUFError(f'Invalid quantized dimensions in {tensor.name}')
    size=(count//block)*bytes_per
    if size>tensor.size:
        raise GGUFError(f'Tensor {tensor.name} is shorter than expected')
    return size


def copy_scaled(src,dst,tensor,factor:float,cancel=None):
    if not 0 < factor <= 1:
        raise GGUFError('Damping factor must be in (0,1]')
    usable=payload_bytes(tensor)
    block_e,block_size,half_offsets,float_offsets=FORMATS[tensor.type_id]
    # Block boundaries maintained at every streaming chunk.
    chunk_blocks=max(1,4*1024*1024//block_size)
    left=usable
    while left:
        checkpoint(cancel)
        size=min(left,chunk_blocks*block_size)
        b=bytearray(src.read(size))
        if len(b)!=size:
            raise GGUFError('Truncated tensor during residual damping')
        for pos in range(0,size,block_size):
            for off in half_offsets:
                val=struct.unpack_from('<e',b,pos+off)[0]
                struct.pack_into('<e',b,pos+off,val*factor)
            for off in float_offsets:
                val=struct.unpack_from('<f',b,pos+off)[0]
                struct.pack_into('<f',b,pos+off,val*factor)
        dst.write(b)
        left-=size
    rest=tensor.size-usable
    while rest:
        checkpoint(cancel)
        b=src.read(min(rest,4*1024*1024))
        if not b:raise GGUFError('Truncated alignment padding')
        dst.write(b)
        rest-=len(b)
