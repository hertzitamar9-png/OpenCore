"""Verify shipped official SDK identity, ARM64/16 KB ABI and Java runtime resources."""
import importlib.util
import io
from pathlib import Path
import struct
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('litert_sdk',ROOT/'litert_sdk.py')
sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)


class LiteRtPackagingTests(unittest.TestCase):
    def test_official_sdk_inputs_pages_services_and_kotlin_resources(self):
        vendor,manifest,jars=sdk.inputs()
        self.assertEqual(manifest['version'],'0.18.1')
        self.assertEqual(len(jars),8)
        data=(vendor/'liblitertlm_jni.so').read_bytes()
        self.assertEqual(data[:6],b'\x7fELF\x02\x01')
        self.assertEqual(struct.unpack_from('<H',data,18)[0],183)
        offset=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
        loads=[struct.unpack_from('<IIQQQQQQ',data,offset+i*size) for i in range(count) if struct.unpack_from('<I',data,offset+i*size)[0]==1]
        self.assertTrue(loads and all(p[-1]>=16384 and p[2]%16384==p[3]%16384 for p in loads))
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:sdk.resources(archive,jars)
        with zipfile.ZipFile(output) as archive:
            names=archive.namelist()
            self.assertTrue(any(n.endswith('.kotlin_builtins') for n in names))
            self.assertIn('META-INF/services/kotlinx.coroutines.internal.MainDispatcherFactory',names)
            self.assertIn(b'AndroidDispatcherFactory',archive.read('META-INF/services/kotlinx.coroutines.internal.MainDispatcherFactory'))
            self.assertFalse(any(n.endswith(('.class','.RSA','.SF')) for n in names))


if __name__=='__main__':unittest.main()
