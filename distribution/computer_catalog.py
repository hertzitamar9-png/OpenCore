"""Offline dependency contracts for the reviewed computer-use checkpoints."""
import copy
import json
from hf_catalog import build_plan, _pin
from hf_download import validate_entry


def build_computer_catalog(snapshots, entries):
    def read(repository):
        return json.loads((snapshots/(repository.replace('/', '--')+'.api.json')).read_text(encoding='utf-8'))
    definitions = [
        ('hobbylm-v1', 'Hobbylm/hobbylm-1B-computer-use', 'harims95/hobbylm-1B-instruct', 'safetensors:.', 'computer-hobbylm-v1'),
        ('ternarypilot-v2', 'adriandj3/TernaryPilot-v2-Computer-Browser-Use-LoRA', 'prism-ml/Ternary-Bonsai-2-9B-gguf', 'gguf:Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf', None),
        ('gui-jepa-v1', 'LilCC/GUI-JEPA', 'openai/clip-vit-base-patch32', 'pytorch:.', 'computer-gui-jepa-v1'),
        # The publisher ships its own Q8 base beside the LoRA; the projector and
        # template travel with the adapter entry, so the base is the weights only.
        ('minicpm-cua-v1', 'banyohavlusu23/minicpm-v-4.7-1b-computer-use', 'banyohavlusu23/minicpm-v-4.7-1b-computer-use',
         'gguf:MiniCPM-V-4.7-1B-base-Q8_0.gguf', None),
    ]
    dependencies, profiles = [], {}
    for runtime, repository, base, key, recipe in definitions:
        metadata = read(base)
        dependency = copy.deepcopy(next(value for value in build_plan(metadata)['variants'] if value['variantKey'] == key))
        if runtime == 'ternarypilot-v2':
            projector = next(item for item in metadata['siblings'] if item['rfilename'] == 'Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf')
            dependency['files'] = [pin for pin in dependency['files'] if pin['role'] != 'companion']+[_pin(base, metadata['sha'], projector, 'companion')]
            dependency['bytes'] = sum(pin['bytes'] for pin in dependency['files'])
        if runtime == 'minicpm-cua-v1':
            dependency['files'] = [pin for pin in dependency['files'] if pin['role'] == 'model']
            dependency['bytes'] = sum(pin['bytes'] for pin in dependency['files'])
        validate_entry(dependency)
        dependencies.append(dependency)
        entry = next(value for value in entries if value.get('computerRuntime') == runtime)
        profiles[runtime] = {'id': entry['id'], 'repository': repository, 'revision': entry['revision'],
                             'dependencyId': dependency['id'], 'recipe': recipe, 'baseBytes': dependency['bytes']}
    return {'schema': 1, 'profiles': profiles, 'dependencies': dependencies}
