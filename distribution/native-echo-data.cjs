const fs = require('node:fs');
const path = require('node:path');
const {patchNativePythonWorker} = require('./native-studio-data.cjs');

const slot = {
  name: 'echo_server_runtime.py', raw: 127982256, length: 176936,
  sha256: 'a302e1ab5cdb9ccd5cb99fb062d28ecb4dad3449811fdfbe0f0d37c82811105a'
};

function patchNativeEchoWorker(buffer, directory) {
  const read = relative => fs.readFileSync(path.join(directory, relative), 'utf8').replace(/\r\n/g, '\n');
  // The recovered launcher writes its own ECHO entry point and older helper
  // modules into the workspace. Load one matching set before the worker runs.
  const modules = [
    'evoagent.echo_loop', 'evoagent.echo_memory', 'evoagent.echo_context',
    'evoagent.echo_output', 'evoagent.echo_adapters', 'evoagent.echo_virtual',
    'evoagent.generation_settings', 'echo_salience', 'echo_summarize'
  ].map(name => {
    const relative = name.replaceAll('.', '/') + '.py';
    return [name, relative, read(relative)];
  });
  const source = `import sys as _sys, types as _types
from pathlib import Path as _Path
_package = _types.ModuleType('evoagent')
_package.__path__ = []
_sys.modules['evoagent'] = _package
for _name, _relative, _source in ${JSON.stringify(modules)}:
    _module = _types.ModuleType(_name)
    _module.__package__ = _name.rpartition('.')[0]
    _module.__file__ = str(_Path(__file__).parent / _relative)
    _sys.modules[_name] = _module
    exec(compile(_source, _module.__file__, 'exec'), _module.__dict__)
exec(compile(${JSON.stringify(read('echo_server.py'))}, __file__, 'exec'), globals())
`;
  return patchNativePythonWorker(buffer, slot, source, 'native-echo-worker');
}

module.exports = {patchNativeEchoWorker};
