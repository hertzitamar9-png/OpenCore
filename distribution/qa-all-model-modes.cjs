/* Render the patched installed UI with CPU rendering and native/API fixtures. No models execute. */
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const original = fs.readFileSync(path.join(__dirname, 'qa-hub.cjs'), 'utf8');
let code = original.slice(0, original.indexOf('(async()=>{await new Promise'));
const setup = String.raw`
const entries = JSON.parse(fs.readFileSync(path.join(__dirname, '../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'), 'utf8')).entries.filter(entry => ['publisher-budget3d-idol','publisher-baphomet-1-1-4b','publisher-zonos3-mini'].includes(entry.familyId));
const modeScript = 'fixture.catalog=' + JSON.stringify(entries).replaceAll('<', '\\u003c') + ';';
`;
code = code.replace('const html=', setup + '\nconst html=').replace('${fixture}</script>', '${fixture}${modeScript}</script>');
code += String.raw`
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, args: ['--disable-gpu', '--disable-gpu-compositing', '--mute-audio']});
  const page = await browser.newPage({viewport: {width: 1450, height: 1000}});
  const checks = [], errors = [];
  const output = process.env.OPENCORE_MODE_QA_DIR || path.join(require('node:os').tmpdir(), 'opencore-all-model-modes-20261117');
  fs.mkdirSync(output, {recursive: true});
  page.on('pageerror', error => errors.push(error.message));
  const check = (name, passed) => {checks.push({name, passed: !!passed}); if (!passed) throw Error(name);};
  try {
    await page.goto('http://127.0.0.1:' + server.address().port);
    const search = page.getByRole('textbox', {name: 'Search models', exact: true});
    await search.fill('Budget3D');
    const budget = page.getByRole('article', {name: 'Budget3D-Idol', exact: true});
    await budget.waitFor();
    const budgetMode = budget.getByRole('combobox', {name: 'Operating mode', exact: true});
    check('Budget3D displays both Regular and ECHO', await budgetMode.locator('option').count() === 2);
    check('Budget3D modes are disabled until supported', await budgetMode.locator('option').evaluateAll(items=>items.every(item=>item.disabled)));
    const budgetSection = budget.getByRole('region', {name: 'Operating mode', exact: true});
    check('Budget3D explains its native runtime and ECHO interface requirements', (await budgetSection.innerText()).includes('Budget3D') && (await budgetSection.innerText()).includes('conversational text-generation interface'));
    check('3D mode controls never claim token context capacity', !(await budgetSection.innerText()).includes('1M') && !(await budgetSection.innerText()).includes('32K'));
    await budget.screenshot({path: path.join(output, 'budget3d-modes.png')});
    await search.fill('Baphomet');
    const coder = page.getByRole('article', {name: 'Baphomet-1.1-4B', exact: true});
    const coderMode = coder.getByRole('combobox', {name: 'Operating mode', exact: true});
    await coderMode.waitFor();
    check('Text model keeps both supported execution modes', await coderMode.locator('option').evaluateAll(items=>items.every(item=>!item.disabled)));
    await coderMode.selectOption('native');
    await page.waitForFunction(() => fixture.calls.some(call => call.route === '/runtime/mode' && call.body.operatingMode === 'native'));
    check('Regular selection reaches the existing mode API', await coderMode.inputValue() === 'native');
    await coderMode.selectOption('echo');
    await page.waitForFunction(() => fixture.calls.some(call => call.route === '/runtime/mode' && call.body.operatingMode === 'echo'));
    check('ECHO selection reaches the existing mode API', await coderMode.inputValue() === 'echo');
    await search.fill('ZONOS3');
    const voice = page.getByRole('article', {name: 'ZONOS3-Mini', exact: true});
    const voiceMode = voice.getByRole('combobox', {name: 'Operating mode', exact: true});
    await voiceMode.waitFor();
    check('Managed speech supports Regular and explains unsupported ECHO', await voiceMode.locator('option').evaluateAll(items=>items.find(item=>item.value==='native').disabled===false&&items.find(item=>item.value==='echo').disabled===true));
    check('No installation or model execution was requested', await page.evaluate(() => !fixture.calls.some(call => ['/install','/prepare','/v1/chat/completions'].includes(call.route) || ['install_model','start_model'].includes(call.command))));
    check('Patched mode UI has no JavaScript errors', errors.length === 0);
  } finally {
    fs.writeFileSync(path.join(output, 'mode-ui-checks.json'), JSON.stringify({checks, errors, modelExecuted: false, gpuDisabled: true}, null, 2) + '\n');
    await browser.close(); server.close();
  }
  console.log(JSON.stringify({checks, errors, output, modelExecuted: false, gpuDisabled: true}, null, 2));
})().catch(error => {console.error(error); process.exitCode = 1;});
`;
const qa = new Module(__filename, module); qa.filename = __filename; qa.paths = module.paths; qa._compile(code, __filename);
