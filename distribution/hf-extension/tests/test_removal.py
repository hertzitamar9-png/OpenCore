import json,tempfile,unittest
from pathlib import Path
from hf_bridge import BridgeState

class RemovalTests(unittest.TestCase):
    def test_plan_lists_exact_files_and_changed_plan_cannot_be_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=BridgeState(root,root)
            try:
                identity='hf-'+'a'*24
                state.entries[identity]={'id':identity,'name':'Example Q4','repository':'owner/Model','revision':'b'*40,'files':[]}
                folder=root/'models/hub'/identity;folder.mkdir(parents=True)
                (folder/'model.gguf').write_bytes(b'weights')
                (folder/'config.json').write_text('{}')
                plan=state.removal_plan(identity)
                self.assertEqual(len(plan['files']),2)
                self.assertEqual(plan['totalBytes'],9)
                self.assertEqual(plan['retainedFiles'],[])
                (folder/'new-file').write_text('new')
                with self.assertRaisesRegex(ValueError,'changed|again'):
                    state.uninstall(identity,plan['confirmationToken'])
                self.assertTrue((folder/'model.gguf').exists())
                plan=state.removal_plan(identity)
                state.uninstall(identity,plan['confirmationToken'])
                self.assertFalse(folder.exists())
                self.assertTrue((root/'models/hub-imports.json').exists())
            finally:state.close()

if __name__=='__main__':unittest.main()
