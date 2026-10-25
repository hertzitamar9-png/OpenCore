"""Find published community GGUF packages with explicit checkpoint lineage."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re

from hf_catalog import build_plan, parse_repository


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
        if checkpoint_name(source) == checkpoint_name(base):
            return base, {source, base}
    return source, {source}


def same_checkpoint(metadata, identities):
    card = metadata.get('cardData') or {}
    card = card if isinstance(card, dict) else {}
    relation = str(card.get('base_model_relation') or '').lower()
    if relation and relation != 'quantized':
        return False
    declared = bases(metadata)
    # Multi-model merges are distinct models, even when one constituent matches.
    return quantized_relation(metadata) and len(declared) == 1 and declared.issubset(identities)


def discover(client, repository):
    from hf_download import HubClient
    if isinstance(client, HubClient):
        client = HubClient(client.token_provider, endpoint=client.endpoint,
                           allow_test_http=client.endpoint != 'https://huggingface.co', timeout=6)
    source = client.metadata(parse_repository(repository))
    primary = build_plan(source)
    canonical, identities = source_identity(source)
    references = [source['id']]
    card = source.get('cardData') or {}
    values = card.get('base_model') or [] if isinstance(card, dict) else []
    references += [values] if isinstance(values, str) else values if isinstance(values, list) else []
    references += [tag[len('base_model:quantized:'):] for tag in source.get('tags') or []
                   if isinstance(tag, str) and tag.startswith('base_model:quantized:')]
    spelling = next((value for value in references if isinstance(value, str) and value.lower() == canonical), canonical)
    query = spelling.split('/')[-1]
    candidates = []
    derivatives = getattr(client, 'quantization_derivatives', None)
    if derivatives:
        candidates = [candidate for candidate in derivatives(spelling) if isinstance(candidate, dict) and (
                      candidate.get('library_name') == 'gguf' or 'gguf' in (candidate.get('tags') or [])
                      or 'gguf' in str(candidate.get('id', '')).lower())]
    candidates += client.search_quantizations(query)
    names, seen = [], set(identities)
    for candidate in candidates:
        try:
            name = parse_repository(candidate.get('id', ''))
        except (AttributeError, ValueError):
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    # Bound interactive metadata requests; no weights or repository code are read.
    names = names[:12]
    def inspect(name):
        try:
            metadata = client.metadata(name)
            if not same_checkpoint(metadata, identities):
                return None, {'repository': name, 'reason': 'Exact checkpoint lineage was not confirmed'}
            plan = build_plan(metadata)
            plan['variants'] = [value for value in plan['variants'] if value['format'] == 'gguf']
            return plan, None
        except (OSError, ValueError, RuntimeError) as error:
            return None, {'repository': name, 'reason': str(error)[:300]}
    accepted, rejected = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for plan, error in pool.map(inspect, names):
            if plan and plan['variants']:
                accepted.append(plan)
            if error:
                rejected.append(error)
    family = 'checkpoint-' + hashlib.sha256(canonical.encode()).hexdigest()[:24]
    label = spelling.split('/')[-1]
    variants = []
    for plan in [primary] + accepted:
        for entry in plan['variants']:
            entry.update(familyId=family, familyLabel=label, originalRepository=spelling,
                         quantizationPublisher=entry['repository'].split('/')[0])
            variants.append(entry)
    variants = list({value['id']: value for value in variants}.values())
    identity = json.dumps(variants, sort_keys=True, separators=(',', ':'))
    return {**primary, 'variants': variants, 'planId': hashlib.sha256(identity.encode()).hexdigest(),
            'originalRepository': spelling, 'relatedRepositories': [plan['repository'] for plan in accepted],
            'rejectedRepositories': rejected, 'searchedRepositories': len(names),
            'quantizationCount': len({entry['quantization'] for entry in variants if entry['installable']}),
            'warning': 'These published files require compatible hardware and runtime verification before use.'}
