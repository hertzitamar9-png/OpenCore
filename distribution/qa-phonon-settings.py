"""Render speech controls against the production authenticated HTTP controller.

Prepared model fixtures replace weight checks: tensor execution has its own real-audio QA.
Request parsing, validation, settings persistence and responses are production code.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
RESOURCES = HERE.parent / 'src-tauri/resources'
sys.path.insert(0, str(RESOURCES / 'runtime-setup/hf-extension'))
from hf_bridge import BridgeState, BridgeServer, RuntimeUnavailable
from hf_speech import SpeechManager


def main():
    with tempfile.TemporaryDirectory(prefix='opencore-speech-settings-') as temporary:
        state = BridgeState(Path(temporary), RESOURCES, runtime=RuntimeUnavailable())
        entries = json.loads((RESOURCES / 'runtime-setup/hf-extension/publisher-models.json').read_text(encoding='utf-8'))['entries']
        state.entries = {entry['id']: entry for entry in entries if entry.get('speechRuntime') == 'parakeet-phonon25-v1'}
        prepared = {identity: dict(entry, status='installed') for identity, entry in state.entries.items()}
        secret = secrets.token_hex(32)
        server = BridgeServer(('127.0.0.1', 0), state, secret)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            with patch.object(state.speech, 'ready', return_value=True), \
                    patch.object(state.speech, 'get_entry', side_effect=lambda identity: prepared[identity]):
                thread.start()
                env = dict(os.environ, OPENCORE_SPEECH_QA_URL='http://127.0.0.1:' + str(server.server_port), OPENCORE_SPEECH_QA_SECRET=secret)
                subprocess.run(['node', str(HERE / 'qa-phonon-settings.cjs')], env=env, check=True)
                restored = SpeechManager(state.root, state.resources, lambda identity: state.entries[identity])
                try:
                    assert restored.settings['modelId'] in state.entries
                    assert restored.settings['idleMode'] == 'cold'
                finally:
                    restored.close()
                print('PASS speech settings HTTP contract and persisted cold preference')
        finally:
            server.shutdown()
            server.server_close()
            state.close()


if __name__ == '__main__':
    main()
