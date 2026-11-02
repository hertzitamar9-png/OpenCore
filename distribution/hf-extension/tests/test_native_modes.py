import hashlib,json,os
from pathlib import Path
import tempfile,unittest
from native_modes import activate

class NativeModes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.resources=self.root/'resources'
        self.weight=self.root/'models/model.gguf';self.weight.parent.mkdir();self.weight.write_bytes(b'actual-shared-checkpoint')
        self.receipts=self.root/'models/receipts';self.receipts.mkdir()
        self.binding=self.resources/'runtime-setup/hf-extension/native-model-bindings.json';self.binding.parent.mkdir(parents=True)
        self.models=[{'id':mode,'backend':'gguf','artifacts':['weight'],'contextTokens':32768 if mode=='echo' else 131072,
                     'modeProfiles':{'native':'native','echo':'echo'}} for mode in ('native','echo')]
        self.artifacts=[{'id':'weight','path':'models/model.gguf','bytes':len(self.weight.read_bytes()),'sha256':hashlib.sha256(self.weight.read_bytes()).hexdigest()}]
        self.save();(self.receipts/'model-echo.json').write_text('["weight"]')
    def save(self):self.binding.write_text(json.dumps({'schema':1,'models':self.models,'artifacts':self.artifacts}))
    def test_modes_reuse_exact_file_and_one_receipt_so_uninstall_has_no_hidden_weight_owner(self):
        before=self.weight.stat();result=activate(self.root,self.resources,'echo','native')
        self.assertEqual(result['profileId'],'native');self.assertEqual(result['contextTokens'],131072)
        self.assertEqual(self.weight.stat().st_ino,before.st_ino);self.assertEqual(self.weight.stat().st_mtime_ns,before.st_mtime_ns)
        self.assertEqual([p.name for p in self.receipts.iterdir()],['model-native.json'])
        activate(self.root,self.resources,'echo','echo')
        self.assertEqual([p.name for p in self.receipts.iterdir()],['model-echo.json'])
    def test_changed_weights_and_unowned_receipts_are_never_admitted(self):
        self.weight.write_bytes(b'x'*self.weight.stat().st_size)
        with self.assertRaisesRegex(ValueError,'checksum'):activate(self.root,self.resources,'echo','native')
        self.assertFalse((self.receipts/'model-native.json').exists())
        (self.receipts/'model-echo.json').write_text('{"personal":"preserve"}')
        with self.assertRaisesRegex(ValueError,'receipt'):activate(self.root,self.resources,'echo','native')
        self.assertEqual((self.receipts/'model-echo.json').read_text(),'{"personal":"preserve"}')
    def test_paths_and_profile_ids_are_frozen_not_caller_supplied(self):
        for identity in ('../echo','echo:other','missing'):
            with self.assertRaises(ValueError):activate(self.root,self.resources,identity,'native')
        self.artifacts[0]['path']='../outside.gguf';self.save()
        with self.assertRaises(ValueError):activate(self.root,self.resources,'echo','native')
    def test_missing_checkpoint_cannot_create_a_ready_mode_receipt(self):
        self.weight.unlink()
        with self.assertRaises(OSError):activate(self.root,self.resources,'echo','native')
        self.assertFalse((self.receipts/'model-native.json').exists())

if __name__=='__main__':unittest.main()
