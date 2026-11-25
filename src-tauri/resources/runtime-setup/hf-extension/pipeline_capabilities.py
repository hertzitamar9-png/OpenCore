"""Specialist pipelines that have an implemented direct and agent-tool dispatch."""
from pathlib import PurePosixPath


def pipeline_kind(model):
    if model.get('generationRuntime') == 'budget3d-idol-v1': return '3d'
    if model.get('computerRuntime') in ('hobbylm-v1', 'ternarypilot-v2', 'gui-jepa-v1', 'deskpaw-v1'): return 'computer-use'
    if model.get('ttsRuntime'): return 'tts'
    if model.get('speechRuntime') == 'parakeet-phonon25-v1': return 'speech'
    if model.get('specialist') == 'ocr' or model.get('repository') == 'baidu/Unlimited-OCR-2': return 'ocr'
    if model.get('imageRuntime') in ('iris-sdcpp-v1', 'tessera-small-v1'): return 'image'
    if (model.get('category') == 'image' and model.get('format') == 'diffusers' and model.get('packageKind') != 'adapter'
            and any(PurePosixPath(file['filename']).name == 'model_index.json' for file in model.get('files', []))): return 'image'
    return None
