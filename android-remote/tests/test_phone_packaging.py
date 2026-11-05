from pathlib import Path
import hashlib
import json
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]


class PhonePackagingTests(unittest.TestCase):
    def test_native_binary_identity_and_android_16k_pages(self):
        receipt=json.loads((ROOT/'native/binaries.json').read_text())
        self.assertEqual(receipt['revision'],json.loads((ROOT/'native/runtime.json').read_text())['revision'])
        for name,sha in receipt['sources'].items():
            self.assertEqual(hashlib.sha256((ROOT/'native'/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),sha)
        for name,file in receipt['files'].items():
            data=(ROOT/'native'/name).read_bytes()
            self.assertEqual(len(data),file['bytes']);self.assertEqual(hashlib.sha256(data).hexdigest(),file['sha256'])
            self.assertEqual(data[:6],b'\x7fELF\x02\x01');self.assertEqual(struct.unpack_from('<H',data,18)[0],183)
            offset=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
            loads=[struct.unpack_from('<IIQQQQQQ',data,offset+i*size) for i in range(count) if struct.unpack_from('<I',data,offset+i*size)[0]==1]
            self.assertGreater(len(loads),0)
            self.assertTrue(all(p[-1]>=16384 and p[2]%16384==p[3]%16384 for p in loads),'Native libraries must support Android 16 KB pages')

    def test_phone_data_and_route_are_not_remote_preferences(self):
        source=(ROOT/'src/ai/opencore/remote/DeviceRouting.java').read_text()
        self.assertIn('"execution-device"',source)
        self.assertNotIn('"opencore.',source)
        phone=(ROOT/'src/ai/opencore/remote/PhoneActivity.java').read_text()
        self.assertIn('new LiveUi(this,"phone"',phone)
        self.assertIn('phone/shared-ui/manifest.json',(ROOT/'src/ai/opencore/remote/LiveUi.java').read_text())
        self.assertIn('SystemInsets.apply(this,root)',phone)
        self.assertNotIn('addJavascriptInterface',phone)
        self.assertNotIn('selected.json("/request"',phone)

    def test_phone_reuses_desktop_assets_and_has_no_replacement_composer(self):
        manifest=json.loads((ROOT/'shared-ui/manifest.json').read_text())
        self.assertTrue({'index.html','opencore-logo.png'}.issubset(manifest['files']))
        self.assertTrue(any(name.startswith('assets/') and name.endswith('.js') for name in manifest['files']))
        self.assertTrue(any(name.startswith('assets/') and name.endswith('.css') for name in manifest['files']))
        if 'shellApi' in manifest:
            self.assertEqual(manifest['shellApi'],1)
            self.assertEqual(set(manifest['modes']),{'pc','phone'})
            self.assertTrue({'phone/mobile-shim.js','phone/mobile.css'}.issubset(manifest['files']))
        for name,record in manifest['files'].items():
            data=(ROOT/'shared-ui'/name).read_bytes()
            self.assertEqual(len(data),record['bytes']);self.assertEqual(hashlib.sha256(data).hexdigest(),record['sha256'])
        self.assertFalse((ROOT/'assets/local').is_dir() and any((ROOT/'assets/local').iterdir()),'The separate redesigned Phone UI must not be shipped')


if __name__=='__main__':unittest.main()
