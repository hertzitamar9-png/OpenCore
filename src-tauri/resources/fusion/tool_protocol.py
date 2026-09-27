"""Reuse the app's streaming protocol, with registered-schema tool validation."""
import json

from lfm.protocol import OutputStream, with_tools


def validate_tools(tools):
    from jsonschema import Draft202012Validator
    if not isinstance(tools, list):
        raise ValueError('tools must be an array of registered function schemas')
    registered = {}
    for tool in tools:
        function = tool.get('function') if isinstance(tool, dict) and tool.get('type') == 'function' else None
        if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not function['name']:
            raise ValueError('Each tool requires a registered function name')
        name = function['name']
        if name in registered:
            raise ValueError('Duplicate registered tool name')
        schema = function.get('parameters', {'type': 'object'})
        try:
            Draft202012Validator.check_schema(schema)
            registered[name] = Draft202012Validator(schema)
        except Exception as error:
            raise ValueError(f'Invalid registered tool schema: {name}') from error
    return registered


def prepare_messages(messages, tools):
    validate_tools(tools)
    normalized = []
    for message in messages:
        role, content = message.get('role'), message.get('content')
        calls = message.get('tool_calls')
        if role not in ('system', 'developer', 'user', 'assistant', 'tool'):
            raise ValueError('Invalid chat message role')
        if content is None and role == 'assistant' and isinstance(calls, list) and calls:
            content = ''
        if not isinstance(content, str) or '\0' in content:
            raise ValueError('TwinCore accepts text messages; images require a vision-capable profile')
        row = {**message, 'role': 'system' if role == 'developer' else role, 'content': content}
        if calls and (role != 'assistant' or not isinstance(calls, list)):
            raise ValueError('Structured previous tool calls must belong to an assistant message')
        normalized.append(row)
    # Tools and their results remain in their original chronological positions.
    # Use normalization even after the set of currently exposed tools changes.
    if tools:
        return with_tools(normalized, tools)
    for row in normalized:
        if row.get('tool_calls'):
            row['content'] += '\nPrevious tool calls: ' + json.dumps(row['tool_calls'], ensure_ascii=False)
        if row['role'] == 'tool':
            row['content'] = f"Tool result {row.get('tool_call_id', '')}: {row['content']}"
            row['role'] = 'user'
    return normalized


def selected_message(decoder, tools):
    message = decoder.message(tools)
    validators = validate_tools(tools)
    for call in message.get('tool_calls', []):
        function = call['function']
        errors = list(validators[function['name']].iter_errors(json.loads(function['arguments'])))
        if errors:
            raise ValueError(f"Invalid tool arguments for {function['name']}: {errors[0].message}")
        call['id'] = call['id'].replace('call_lfm_', 'call_twincore_', 1)
    return message
