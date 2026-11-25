"""Bundled specialist tools for OpenCore's agent. No model is loaded by MCP startup."""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit

PROTOCOL = '2025-06-18'
LIMIT = 1024 * 1024


def definitions():
    def tool(name, description, properties, required=()):
        return {'name': name, 'description': description, 'inputSchema': {'type': 'object', 'properties': properties,
                'required': list(required), 'additionalProperties': False}}
    job = {'type': 'string', 'maxLength': 100}
    return [
        tool('pipeline_catalog', 'List the installed and downloadable OpenCore specialist pipelines and their real readiness. These models run through tools, not as the text brain.', {}),
        tool('pipeline_run', 'Run the selected installed specialist using its native pipeline. For 3D supply settings.inputPath (approved image), budget 0.0625..1, steps, guidanceScale, seed, numGaussians, imageMode synthetic/real. Original IDOL needs budget=1. Output is Gaussian PLY, not a mesh. Images use the image controls; speech-to-text and grounding require inputPath. TTS reads prompt using the selected settings.voice. A queued/running job is not a completed generation. When requiresTurnHandoff is true, tell the user where the job is queued and finish your current response: the desktop waits for this turn to end, unloads the text model and then starts the pipeline. Do not keep generating or polling while it is waiting for your turn. Inspect pipeline_status on a later turn to report actual results or errors.',
             {'modelId': {'type': 'string'}, 'prompt': {'type': 'string', 'maxLength': 16000},
              'settings': {'type': 'object'}, 'conversationId': {'type': 'string', 'description': 'Current OpenCore chat id, when available'}}, ('modelId', 'prompt', 'settings')),
        tool('pipeline_status', 'Read a pipeline job, progress, actual errors and generated file paths. Do not claim completion before its status is completed.', {'jobId': job}, ('jobId',)),
        tool('pipeline_cancel', 'Cancel the selected active specialist job and release its owned worker. Partial files remain associated with that job.', {'jobId': job}, ('jobId',)),
    ]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise ValueError('The local pipeline service cannot redirect')


class Client:
    def __init__(self, root, resources): self.root, self.resources = Path(root).resolve(), Path(resources).resolve()

    def call(self, action, arguments):
        file = self.root / 'runtime-setup/hub-bridge/service.json'
        if not file.is_file() or file.stat().st_size > 65536: raise ValueError('OpenCore must be running before its pipeline tools can be used')
        descriptor = json.loads(file.read_bytes())
        target = urlsplit(descriptor.get('url', ''))
        if (descriptor.get('schema') != 1 or Path(descriptor.get('root', '')).resolve() != self.root
                or Path(descriptor.get('resources', '')).resolve() != self.resources or target.scheme != 'http'
                or target.hostname != '127.0.0.1' or not target.port or target.path or target.query or target.fragment
                or target.username or target.password or not isinstance(descriptor.get('secret'), str) or len(descriptor['secret']) < 32):
            raise ValueError('The local pipeline service descriptor is invalid')
        secret = descriptor['secret']
        request = Request(descriptor['url'] + '/api/pipelines/' + action,
                          data=json.dumps(arguments, allow_nan=False).encode(),
                          headers={'Content-Type': 'application/json', 'X-OpenCore-Hub-Secret': secret})
        try:
            with build_opener(NoRedirect()).open(request, timeout=230) as response: raw = response.read(4 * LIMIT + 1)
        except Exception as error:
            from urllib.error import HTTPError
            if isinstance(error, HTTPError):
                raw = error.read(8192)
                try: message = json.loads(raw).get('error')
                except (ValueError, AttributeError): message = None
                raise ValueError(str(message or 'The pipeline service rejected this action').replace(secret, '[credential]')) from None
            raise ValueError('The local pipeline service is unavailable; reopen OpenCore and retry') from None
        if len(raw) > 4 * LIMIT: raise ValueError('The pipeline result exceeded its size limit')
        # Credentials are never model-visible, including a faulty service response.
        return json.loads(raw.decode().replace(secret, '[credential]'))


def handle(message, client):
    identity = message.get('id')
    if message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32600, 'message': 'Invalid request'}}
    method = message['method']
    if method.startswith('notifications/'): return None
    if method == 'initialize':
        return {'jsonrpc': '2.0', 'id': identity, 'result': {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'opencore-pipelines', 'version': '1.0'}}}
    if method == 'ping': return {'jsonrpc': '2.0', 'id': identity, 'result': {}}
    if method == 'tools/list': return {'jsonrpc': '2.0', 'id': identity, 'result': {'tools': definitions()}}
    if method != 'tools/call': return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32601, 'message': 'Unknown method'}}
    try:
        parameters = message.get('params') or {}
        name, arguments = parameters.get('name'), parameters.get('arguments', {})
        spec = next((entry for entry in definitions() if entry['name'] == name), None)
        if spec is None or not isinstance(arguments, dict): raise ValueError('Unknown pipeline tool or invalid arguments')
        schema = spec['inputSchema']
        if set(arguments) - schema['properties'].keys() or any(key not in arguments for key in schema['required']): raise ValueError('Invalid pipeline arguments')
        value = client.call(name.removeprefix('pipeline_'), arguments)
        result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value, 'isError': False}
    except Exception as error:
        result = {'content': [{'type': 'text', 'text': str(error)[:2000]}], 'isError': True}
    return {'jsonrpc': '2.0', 'id': identity, 'result': result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    args = parser.parse_args()
    client = Client(args.root, args.resources)
    while line := sys.stdin.buffer.readline(LIMIT + 1):
        if len(line) > LIMIT: return 1
        try: result = handle(json.loads(line), client)
        except ValueError: result = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}
        if result is not None: print(json.dumps(result), flush=True)
    return 0


if __name__ == '__main__': raise SystemExit(main())
