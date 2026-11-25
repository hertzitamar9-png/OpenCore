"""One dispatch for specialist ECHO tools and the native generation forms."""
import base64
import copy
import os
from pathlib import Path
import sys
import uuid

from pipeline_capabilities import pipeline_kind
from hf_reverb import VARIANTS as REVERB_VARIANTS
import hf_tessera

SERVER_ID = 'opencore-pipelines'


def _kind(state, entry):
    # Older desktop builds can serve CPU speech without the GPU handoff controller.
    if getattr(state, 'pipeline_cpu_speech_only', False) and entry.get('ttsRuntime') not in REVERB_VARIANTS and not hf_tessera.supports(entry):
        return None
    return pipeline_kind(entry)


def catalog(state):
    fields = ('id', 'repository', 'familyLabel', 'category', 'status', 'runtimeReady', 'specialistReady', 'generationRuntime', 'modeAvailability')
    return [{**{key: entry[key] for key in fields if key in entry}, 'pipeline': kind}
            for entry in state.catalog() if (kind := _kind(state, entry))]


def connection(state):
    # A bundled MCP adapter is loaded by OpenCore's existing configured-agent transport.
    # It never starts an external AI app, and carries no Hugging Face credential.
    return {'id': SERVER_ID, 'name': 'opencore_pipelines', 'enabled': True,
            'command': sys.executable, 'args': ['-I', str(state.resources / 'runtime-setup/hf-extension/hf_pipeline_mcp.py'),
                                             '--root', str(state.root), '--resources', str(state.resources)],
            'env': {}, 'url': None, 'bearerTokenEnvVar': None, 'startupTimeoutSec': 30, 'toolTimeoutSec': 240}


def input_file(path, extensions, limit):
    if not isinstance(path, str) or not Path(path).is_absolute(): raise ValueError('Supply the approved local input file path')
    file = Path(path)
    if file.suffix.lower() not in extensions or not file.is_file() or not 1 <= file.stat().st_size <= limit:
        raise ValueError('The input file is missing, too large or has an unsupported format')
    return file


def manager(state, kind):
    attribute = {'3d': 'generation', 'image': 'images', 'tts': 'tts'}.get(kind)
    return getattr(state, attribute, None) if attribute else None


def require_idle(state):
    # Called while BridgeState.lock holds admission, shared with the direct forms.
    for name in ('generation','images','computer','duplex','ocr'):
        service=getattr(state,name,None)
        if service is not None and service.operation.locked():
            raise ValueError('Finish or cancel the active '+name+' operation before starting another pipeline')
    if state.tts.busy():raise ValueError('Finish or cancel the active speech synthesis before starting another pipeline')


def execute(state, action, body):
    if not isinstance(body, dict): raise ValueError('Pipeline arguments must be an object')
    if action == 'connection': return connection(state)
    if action == 'catalog': return {'models': catalog(state)}
    if action == 'admit': return state.admissions.admit(body.get('jobId'))
    if action in ('status', 'cancel'):
        identity = body.get('jobId')
        cpu_speech_only = getattr(state, 'pipeline_cpu_speech_only', False)
        for kind in ('tts', 'image') if cpu_speech_only else ('3d', 'image', 'tts'):
            service = manager(state, kind)
            if not service:
                continue
            job = next((job for job in service.list() if job['id'] == identity), None)
            if job:
                if cpu_speech_only and not (job.get('ttsRuntime') in REVERB_VARIANTS
                        or kind == 'image' and job.get('request', {}).get('modelId') == hf_tessera.MODEL_ID):
                    continue
                if action == 'cancel':
                    if kind == 'tts': service.cancel(identity)
                    else: service.stop(identity)
                    return {'id': identity, 'cancelRequested': True}
                return copy.deepcopy(job)
        for service in () if cpu_speech_only else (state.computer, state.ocr):
            job = service.status()
            if job.get('id') == identity:
                if action == 'cancel': service.stop(); return {'id': identity, 'cancelRequested': True}
                return job
        raise ValueError('This pipeline job was not found')
    if action != 'run': raise ValueError('Unknown pipeline action')
    with state.lock:
        if state.progress and state.progress.get('status') in ('running','pausing','cancelling'):
            raise ValueError('Wait for model installation before running a pipeline')
        require_idle(state)
        if pipeline_kind(state.entry(body.get('modelId'),installed=True))!='speech':return run(state,body)
    return run(state,body)  # Long CPU transcription never holds the catalog/settings lock.


def run(state,body):
    entry = state.entry(body.get('modelId'), installed=True)
    kind = _kind(state, entry)
    if not kind: raise ValueError('This model has no integrated specialist pipeline')
    request = {'modelId': entry['id'], 'prompt': body.get('prompt', ''), 'settings': copy.deepcopy(body.get('settings', {})),
               'conversationId': body.get('conversationId'), 'operatingMode': 'echo'}
    if not isinstance(request['settings'], dict): raise ValueError('Pipeline controls must be an object')
    if kind in ('3d', 'image'):
        return manager(state, kind).start(entry, request, on_ready=lambda proof: state.image_generated(entry, proof))
    if kind == 'tts':
        if not entry.get('runtimeReady'): raise ValueError('Prepare this voice model before generating speech')
        if request['settings'].get('referencePath'):
            reference=input_file(request['settings'].pop('referencePath'),{'.wav'},8*1024**2)
            request['settings']['referenceAudio']=base64.b64encode(reference.read_bytes()).decode('ascii')
        return state.tts.submit(entry, {'modelId': entry['id'], 'text': request['prompt'], 'settings': request['settings'],
                                        'conversationId': request['conversationId'],'operatingMode':'echo'})
    if kind in ('computer-use', 'ocr'):
        data=None
        if kind=='ocr' or entry.get('computerRuntime')!='gui-jepa-v1':
            image = input_file(request['settings'].get('inputPath'), {'.png', '.jpg', '.jpeg', '.webp'}, 5 * 1024**2)
            data = base64.b64encode(image.read_bytes()).decode('ascii')
        if kind == 'ocr': return state.ocr.start(entry, data, operating_mode='echo')
        return state.computer.start(entry, {'id': entry['id'], 'task': request['prompt'], 'image': data,'operatingMode':'echo',
                                           'state': request['settings'].get('state', ''),
                                           **({'candidates': request['settings']['candidates']} if 'candidates' in request['settings'] else {})})
    if kind == 'speech':
        selected = state.speech.status()
        if selected.get('modelId') != entry['id'] or not selected.get('enabled'):
            raise ValueError('Select and enable this speech model in Models before transcription')
        audio = input_file(request['settings'].get('inputPath'), {'.wav'}, 32 * 1024**2)
        session = 'pipeline-' + uuid.uuid4().hex
        try:
            state.speech.start(session)
            return {'modelId': entry['id'], 'status': 'completed', **state.speech.transcribe(session, base64.b64encode(audio.read_bytes()).decode('ascii'))}
        finally: state.speech.cancel(session)
    raise ValueError('Unsupported pipeline')
