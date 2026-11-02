from io import BytesIO
import math
import struct
import pytest
from autoloop_vendor.gguf import Tensor,GGUFError
from autoloop_vendor.scaling import copy_scaled,payload_bytes,FORMATS

@pytest.mark.parametrize('typ,scale_offsets',[(2,[0]),(8,[0]),(10,[80,82]),(11,[108]),(12,[0,2]),(13,[0,2]),(14,[208])])
def test_quantized_damping_preserves_codes(typ,scale_offsets):
    block,sz,_,_=FORMATS[typ]
    raw=bytearray(b'\x05'*sz)
    for offset in scale_offsets:
        struct.pack_into('<e',raw,offset,4.0)
    tensor=Tensor('blk.3.ffn_down.weight',(block,),typ,0,sz)
    output=BytesIO()
    copy_scaled(BytesIO(raw),output,tensor,0.5)
    changed=output.getvalue()
    assert len(changed)==sz
    for offset in scale_offsets:
        assert struct.unpack_from('<e',changed,offset)[0]==2.0
    for j in range(sz):
        if not any(offset<=j<offset+2 for offset in scale_offsets):
            assert changed[j]==raw[j]

def test_unsupported_quantization_fails():
    with pytest.raises(GGUFError):
        payload_bytes(Tensor('blk.2.ffn_down.weight',(256,),999,0,1))
