"""Pure checkpoint lineage checks; no network or repository search."""
import re
from hf_catalog import parse_repository


def quantized_relation(metadata):
    card = metadata.get('cardData') or {}
    relation = str(card.get('base_model_relation') or '').lower() if isinstance(card, dict) else ''
    tags = [tag for tag in metadata.get('tags') or [] if isinstance(tag, str)]
    if relation and relation != 'quantized':
        return False
    if any(tag.startswith(('base_model:finetune:', 'base_model:merge:', 'base_model:adapter:')) for tag in tags):
        return False
    return relation == 'quantized' or any(tag.startswith('base_model:quantized:') for tag in tags)


def bases(metadata):
    card = metadata.get('cardData') or {}
    card = card if isinstance(card, dict) else {}
    values = card.get('base_model') or []
    if isinstance(values, str):
        values = [values]
    result = set()
    for value in values if isinstance(values, list) else []:
        try:
            result.add(parse_repository(value).lower())
        except ValueError:
            pass
    for tag in metadata.get('tags') or []:
        if isinstance(tag, str) and tag.startswith('base_model:quantized:'):
            try:
                result.add(parse_repository(tag[len('base_model:quantized:'):]).lower())
            except ValueError:
                pass
    return result


def checkpoint_name(repository):
    name = repository.split('/')[-1]
    name = re.sub(r'[-_.](?:gguf|(?:nv|mx)fp4.*|fp8.*|[qi][q]?\d(?:_.*)?)(?:[-_.].*)?$', '', name, flags=re.I)
    return re.sub(r'[^a-z0-9]', '', name.lower())


def source_identity(metadata):
    source = parse_repository(metadata['id']).lower()
    declared = bases(metadata)
    card = metadata.get('cardData') or {}
    relation = str(card.get('base_model_relation') or '').lower() if isinstance(card, dict) else ''
    if relation and relation != 'quantized':
        return source, {source}
    # A matching checkpoint name plus explicit lineage can resolve an original
    # publisher's GGUF repository. Never replace a finetune by its training base.
    if len(declared) == 1 and quantized_relation(metadata):
        base = next(iter(declared))
        source_name, base_name = checkpoint_name(source), checkpoint_name(base)
        if source_name == base_name or source_name.endswith(base_name) or (len(source_name) >= 6 and base_name.startswith(source_name)):
            return base, {source, base}
    return source, {source}


def same_checkpoint(metadata, identities):
    card = metadata.get('cardData') or {}
    card = card if isinstance(card, dict) else {}
    relation = str(card.get('base_model_relation') or '').lower()
    if relation and relation != 'quantized':
        return False
    declared = bases(metadata)
    name = str(metadata.get('id') or '').lower().split('/')[-1]
    if not any(checkpoint_name(identity) in checkpoint_name(name) for identity in identities):
        return False
    modifiers = ('uncensored', 'heretic', 'abliterat', 'finetune', 'distill', 'lora', 'merged')
    if any(word in name and not any(word in identity.split('/')[-1] for identity in identities) for word in modifiers):
        return False
    # Multi-model merges are distinct models, even when one constituent matches.
    return quantized_relation(metadata) and len(declared) == 1 and declared.issubset(identities)
