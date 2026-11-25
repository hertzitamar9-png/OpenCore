/* Render reviewed publisher packages without downloading model weights. */
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const original = fs.readFileSync(path.join(__dirname, 'qa-hub.cjs'), 'utf8');
const marker = original.indexOf('(async()=>{await new Promise');
if (marker < 0) throw Error('The catalog UI harness entry point changed');
let code = original.slice(0, marker);
const setup = String.raw`
const reviewed = JSON.parse(fs.readFileSync(path.join(__dirname, '../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'), 'utf8')).entries.filter(entry => ['publisher-edge0-145b', 'publisher-edge0-120b', 'publisher-raon-1-nano', 'publisher-zonos3-mini'].includes(entry.familyId));
const admissionScript = 'fixture.catalog=' + JSON.stringify(reviewed).replaceAll('<', '\\u003c') + ';';
`;
code = code.replace('const html=', setup + '\nconst html=').replace('${fixture}</script>', '${fixture}${admissionScript}</script>');
code += String.raw`
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, args: ['--mute-audio']});
  const page = await browser.newPage({viewport: {width: 1450, height: 1050}});
  const checks = [], errors = [];
  const output = process.env.OPENCORE_CATALOG_QA_DIR || path.join(require('node:os').tmpdir(), 'opencore-catalog-20261112');
  fs.mkdirSync(output, {recursive: true});
  page.on('pageerror', error => errors.push(error.message));
  const check = (name, passed) => {
    checks.push({name, passed: !!passed});
    if (!passed) throw Error(name);
  };
  try {
    await page.goto('http://127.0.0.1:' + server.address().port);
    const search = page.getByRole('textbox', {name: 'Search models', exact: true});
    for (const size of [120, 145]) {
      await search.fill('Edge0 ' + size + 'B');
      const card = page.getByRole('article', {name: 'Edge0 ' + size + 'B', exact: true});
      await card.waitFor();
      check('Edge0 ' + size + 'B has one family card', await card.count() === 1);
      const picker = card.getByRole('combobox', {name: /^Quantization for /});
      check('Edge0 ' + size + 'B exposes both genuine expert bundles', await picker.locator('option').count() === 2);
      const selected = reviewed.find(entry => entry.familyId === 'publisher-edge0-' + size + 'b' && entry.quantization === 'NVFP4');
      await picker.selectOption(selected.id);
      await card.getByText((selected.bytes / 1e9).toFixed(3) + ' GB', {exact: true}).waitFor();
      const text = await card.innerText();
      check('Edge0 ' + size + 'B shows its engine and hardware requirement', text.includes('edge0-strata') && text.includes('16 GiB VRAM'));
      check('Edge0 ' + size + 'B does not claim it is ready', text.includes('Compatible adapter required') && !text.includes('Ready to use') && await card.getByRole('button', {name: 'Use model', exact: true}).count() === 0);
      await card.screenshot({path: path.join(output, 'edge0-' + size + 'b.png')});
    }
    await search.fill('Raon');
    const raon = page.getByRole('article', {name: 'Raon 1 nano', exact: true});
    await raon.waitFor();
    check('Raon nano has one family card', await raon.count() === 1);
    const picker = raon.getByRole('combobox', {name: /^Quantization for /});
    check('Raon offers its eight GGUF editions and three other formats', await picker.locator('option').count() === 11);
    const mtp = reviewed.find(entry => entry.familyId === 'publisher-raon-1-nano' && entry.quantization === 'Q4_K_M + MTP');
    await picker.selectOption(mtp.id);
    check('Raon MTP selection preserves the exact package', await picker.inputValue() === mtp.id);
    check('Raon states the required Nano engine', (await raon.innerText()).includes('raon-1-nano-preview'));
    await raon.screenshot({path: path.join(output, 'raon-nano-gguf.png')});
    const litert = reviewed.find(entry => entry.familyId === 'publisher-raon-1-nano' && entry.format === 'litertlm');
    await picker.selectOption(litert.id);
    await raon.getByText(/fixed 4096-token context/).waitFor();
    check('LiteRT states its fixed context and runtime instead of generic GGUF support', (await raon.innerText()).includes('Cannot load in llama.cpp') && (await raon.innerText()).includes('Compatible adapter required'));
    await search.fill('ZONOS3');
    const zonos = page.getByRole('article', {name: 'ZONOS3-Mini', exact: true});
    await zonos.waitFor();
    check('ZONOS3-Mini is one speech card', await zonos.count() === 1 && (await zonos.innerText()).includes('Text to speech'));
    check('ZONOS3-Mini uses its existing managed runtime', (await zonos.innerText()).includes('Prepared automatically after download'));
    await zonos.screenshot({path: path.join(output, 'zonos3-mini.png')});
    check('No model weights were requested', await page.evaluate(() => !fixture.calls.some(call => call.route === '/install' || call.command === 'install_model')));
    check('Model cards render without JavaScript errors', errors.length === 0);
  } finally {
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({checks, errors, weightsDownloaded: false, inferenceVerified: false}, null, 2) + '\n');
    await browser.close();
    server.close();
  }
  console.log(JSON.stringify({checks, errors, output, weightsDownloaded: false, inferenceVerified: false}, null, 2));
})().catch(error => {console.error(error); process.exitCode = 1;});
`;
const taskModule = new Module(__filename, module);
taskModule.filename = __filename;
taskModule.paths = module.paths;
taskModule._compile(code, __filename);
