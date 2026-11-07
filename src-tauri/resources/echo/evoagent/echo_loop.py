"""Bounded, exact tool receipts for no-progress checks across working-set trims."""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque


RECEIPT_HISTORY = 96  # Evidence retention, not a limit on task length.


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8', 'surrogatepass')).hexdigest()


def call_identity(call):
    function = call.get('function') or {}
    if not isinstance(function, dict):
        return None, None
    name = function.get('name')
    try:
        args = json.loads(function.get('arguments', '{}'))
        if not isinstance(name, str) or not isinstance(args, dict):
            return None, None
        if re.search(r'(?:^|__)dev$', name):
            args.pop('explanation', None)
        return _digest([name, args]), args
    except (TypeError, ValueError):
        return None, None


def result_details(result, failed=False):
    value = result
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                break
        if not isinstance(value, dict):
            break
        failed = (failed or value.get('isError') is True or value.get('is_error') is True
                  or bool(value.get('error')) or value.get('ok') is False
                  or value.get('success') is False
                  or any(isinstance(value.get(key), (int, float)) and not isinstance(value[key], bool)
                         and value[key] != 0 for key in ('exitCode', 'exit_code')))
        content = value.get('content')
        if not (isinstance(content, list) and len(content) == 1
                and isinstance(content[0], dict) and content[0].get('type') == 'text'
                and isinstance(content[0].get('text'), str)):
            break
        value = content[0]['text']
    return value, failed


def tool_receipt(call, result, failed=False):
    identity, args = call_identity(call)
    value, failed = result_details(result, failed)
    function = call.get('function')
    name = function.get('name') if isinstance(function, dict) else None
    workspace = (isinstance(value, dict) and isinstance(value.get('workspace'), str)
                 and isinstance(value.get('files'), list))
    inspection = (name in ('Read', 'Grep', 'Glob', 'LS')
                  or (name == 'exec_command' and isinstance(args, dict)
                      and str(args.get('cmd', args.get('command', ''))).strip().lower()
                      in ('dir', 'ls', 'pwd', 'get-childitem', 'get-location'))
                  or (isinstance(name, str) and re.search(r'(?:^|__)dev$', name)
                      and isinstance(args, dict) and
                      (args.get('action') in ('read', 'search')
                       or (args.get('action') in ('status', 'list') and workspace)))
                  or (name == '_echo' and isinstance(args, dict)
                      and args.get('op') in ('search', 'browse', 'load', 'fault', 'status')))
    kind = 'failed' if failed else 'inspection' if inspection else 'progress'
    if identity is None or result is None:
        kind = 'unknown'
    try:
        signature = _digest([identity, value, kind]) if kind != 'unknown' else None
    except (TypeError, ValueError):
        signature, kind = None, 'unknown'
    return {'id': call.get('id'), 'action': identity, 'signature': signature, 'kind': kind}


def receipts_from_messages(messages, user_index=-1):
    pending, receipts, seen = {}, deque(maxlen=RECEIPT_HISTORY), set()
    for message in messages[user_index + 1:]:
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls') or []:
                if isinstance(call, dict) and call.get('id'):
                    pending[call['id']] = call
        elif message.get('role') == 'tool':
            call_id = message.get('tool_call_id')
            if call_id in seen:
                continue  # Replayed client history is not another execution.
            call = pending.pop(call_id, None)
            if not call:
                receipts.append({'id': call_id, 'kind': 'unknown'})
            else:
                receipts.append(tool_receipt(call, message.get('content'), message.get('is_error') is True))
            if call_id:
                seen.add(call_id)
    return list(receipts)


def merge_receipts(previous, current):
    receipts, seen = [], set()
    for record in reversed(list(previous) + list(current)):
        if not isinstance(record, dict):
            continue
        call_id = record.get('id')
        if call_id and call_id in seen:
            continue
        if call_id:
            seen.add(call_id)
        receipts.append(record)
        if len(receipts) == RECEIPT_HISTORY:
            break
    return list(reversed(receipts))


def stalled_actions(receipts):
    recent = []
    for record in receipts[-RECEIPT_HISTORY:]:
        if (record.get('kind') not in ('inspection', 'failed')
                or not record.get('signature') or not record.get('action')):
            recent = []  # Unknown receipts and successful mutations break inference.
        else:
            recent.append(record)
    for width in range(1, len(recent) // 3 + 1):
        tail = recent[-width * 3:]
        if all(record['signature'] == tail[index % width]['signature']
               for index, record in enumerate(tail)):
            return {record['action'] for record in tail[-width:]}
    return set()
