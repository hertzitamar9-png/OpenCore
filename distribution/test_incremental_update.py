import gzip,hashlib,json,os,re,subprocess,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from incremental_update import build_payload,update_script

def quoted(path):return "'"+str(path).replace("'","''")+"'"

class IncrementalTests(unittest.TestCase):
    def test_each_file_is_independently_addressable_and_weights_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/'app';python=root/'python';app.mkdir();python.mkdir()
            (app/'OpenCore.exe').write_bytes(b'executable'*100)
            (app/'runtime.dll').write_bytes(b'unchanged library'*500)
            (python/'python.exe').write_bytes(b'python'*100)
            (app/'installed-file-manifest.json').write_text('old local install record')
            index=build_payload(app,python,root/'output','0.3.204','a'*40)
            payload=(root/'output/OpenCore.Update.data').read_bytes()
            self.assertEqual(len(index['files']),3)
            for entry in index['files']:
                raw=gzip.decompress(payload[entry['offset']:entry['offset']+entry['compressedBytes']])
                self.assertEqual(len(raw),entry['bytes'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(),entry['sha256'])
            script=update_script(root/'output','0.3.204',Path('icon.ico'),Path('branding'),Path('webview.exe'))
            self.assertIn('apply-incremental-update.ps1',script)
            self.assertIn('ExecShell "open" "$INSTDIR\\OpenCore.exe"',script)
            self.assertNotIn('File "runtime.dll"',script)

    @unittest.skipUnless(os.name=='nt','Windows release comparison')
    def test_release_comparison_skips_runtime_reads_and_repair_checks_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/'app';app.mkdir();python=root/'python';python.mkdir();stage=root/'stage';stage.mkdir()
            (app/'OpenCore.exe').write_bytes(b'installed executable')
            (app/'runtime.dll').write_bytes(b'shared runtime'*100)
            (app/'build-info.json').write_text(json.dumps({'build':'0.3.203','commit':'b'*40,'appExeSha256':hashlib.sha256((app/'OpenCore.exe').read_bytes()).hexdigest()}))
            entries=[{'path':'app/'+file.name,'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'offset':0,'compressedBytes':10} for file in app.iterdir()]
            previous={'schema':1,'version':'0.3.203','commit':'b'*40,'files':entries}
            receipt=app/'installed-file-manifest.json';receipt.write_text(json.dumps(previous))
            index={**previous,'version':'0.3.204','commit':'a'*40,'files':[dict(entry) for entry in entries]}
            next(entry for entry in index['files'] if entry['path']=='app/OpenCore.exe')['sha256']='a'*64
            index_path=root/'index.json';index_path.write_text(json.dumps(index))
            script=root/'plan.ps1';script.write_text(f"""$ErrorActionPreference='Stop'
. {quoted(Path(__file__).with_name('apply-incremental-update.ps1').resolve())} -AppRoot {quoted(app)} -PythonRoot {quoted(python)} -IndexPath {quoted(index_path)} -FunctionsOnly
$index=Get-Content -LiteralPath {quoted(index_path)} -Raw | ConvertFrom-Json
$script:originalHash=(Get-Command Get-UpdateHash).ScriptBlock
$script:reads=New-Object Collections.Generic.List[string]
function Get-UpdateHash([string]$Path){{$script:reads.Add($Path);& $script:originalHash $Path}}
$fast=New-UpdatePlan $index {quoted(stage)}
if($fast.mode -ne 'release-difference' -or $fast.unchangedFilesSkipped -ne 2 -or $fast.filesInspected -ne 1 -or $fast.changes.Count -ne 1){{throw 'Incorrect release difference'}}
if(@($script:reads|Where-Object {{$_ -like '*runtime.dll'}}).Count){{throw 'Unchanged runtime was opened'}}
if($script:reads.Count -ne 1){{throw 'Unchanged identity files were read before the release comparison'}}
[IO.File]::WriteAllText({quoted(app/'OpenCore.exe')},'a local executable repair')
$script:reads.Clear()
$localRepair=New-UpdatePlan $index {quoted(stage)}
if($localRepair.mode -ne 'release-difference' -or $localRepair.unchangedFilesSkipped -ne 2 -or $localRepair.filesInspected -ne 1 -or $localRepair.changes.Count -ne 1){{throw 'A local executable change discarded unrelated runtime records'}}
if(@($script:reads|Where-Object {{$_ -like '*runtime.dll'}}).Count){{throw 'Unchanged runtime was opened after a local executable repair'}}
[IO.File]::WriteAllText({quoted(app/'runtime.dll')},'locally damaged runtime')
$repair=New-UpdatePlan $index {quoted(stage)} -VerifyInstalledFiles
if($repair.mode -ne 'full-verification' -or $repair.changes.Count -ne 2 -or $repair.filesInspected -ne 3){{throw 'Explicit repair did not find changed files'}}
$original=[IO.File]::ReadAllText({quoted(receipt)})
foreach($bad in @('{{broken json}}')){{
 [IO.File]::WriteAllText({quoted(receipt)},$bad)
 $fallback=New-UpdatePlan $index {quoted(stage)}
 if($fallback.mode -ne 'full-verification' -or $fallback.filesInspected -ne 3){{throw 'Invalid receipt did not trigger verification'}}
}}
[IO.File]::WriteAllText({quoted(receipt)},$original.Replace('0.3.203','0.3.202'))
$metadataRepair=New-UpdatePlan $index {quoted(stage)}
if($metadataRepair.mode -ne 'release-difference' -or $metadataRepair.filesInspected -ne 1){{throw 'A metadata-only difference discarded unrelated file records'}}
Remove-Item -LiteralPath {quoted(receipt)}
$missing=New-UpdatePlan $index {quoted(stage)}
if($missing.mode -ne 'full-verification'){{throw 'Missing receipt was accepted'}}
$index.files[0].path='app/../outside'
try{{$null=New-UpdatePlan $index {quoted(stage)};throw 'Invalid path accepted'}}catch{{if($_.Exception.Message -eq 'Invalid path accepted'){{throw}}}}
$index=Get-Content -LiteralPath {quoted(index_path)} -Raw | ConvertFrom-Json
Save-InstalledUpdateManifest $index {quoted(stage)}
Save-InstalledUpdateManifest $index {quoted(stage)}
$saved=[IO.File]::ReadAllText({quoted(receipt)}) | ConvertFrom-Json
if($saved.version -ne $index.version -or $saved.files.Count -ne $index.files.Count){{throw 'Install record was not saved completely'}}
Write-Output 'PLAN_OK'
""",encoding='utf-8-sig')
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script)],capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PLAN_OK',result.stdout)

    @unittest.skipUnless(os.name=='nt','Windows incremental transaction')
    def test_only_changed_file_bytes_download_and_failed_apply_rolls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir();python=root/'python';python.mkdir()
            (source/'OpenCore.exe').write_bytes(b'new executable'*100)
            (source/'runtime.dll').write_bytes(b'unchanged large library'*10000)
            index=build_payload(source,python,root/'output','0.3.204','a'*40)
            payload=(root/'output/OpenCore.Update.data').read_bytes();ranges=[]
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
                    if not match:self.send_error(400);return
                    start,end=map(int,match.groups());ranges.append((start,end));chunk=payload[start:end+1]
                    self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{end}/{len(payload)}');self.send_header('Content-Length',str(len(chunk)));self.end_headers();self.wfile.write(chunk)
                def log_message(self,*args):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            app=root/'installed';app.mkdir();(app/'OpenCore.exe').write_bytes(b'old executable');(app/'runtime.dll').write_bytes((source/'runtime.dll').read_bytes())
            unchanged=(app/'runtime.dll').stat().st_mtime_ns
            staged=root/'stage';staged.mkdir();(root/'index.json').write_text(json.dumps(index))
            code=f"""$ErrorActionPreference='Stop'
. {quoted(Path(__file__).with_name('apply-incremental-update.ps1').resolve())} -AppRoot {quoted(app)} -PythonRoot {quoted(python)} -IndexPath {quoted(root/'index.json')} -FunctionsOnly
$index=Get-Content -LiteralPath {quoted(root/'index.json')} -Raw | ConvertFrom-Json
$plan=@()
foreach($entry in $index.files){{
 $target=Get-UpdateTarget $entry.path
 if(-not(Test-UpdateFile $entry $target)){{
  $path=Join-Path {quoted(staged)} ('new-'+$plan.Count)
  Receive-UpdateFile $entry 'http://127.0.0.1:{server.server_port}/data' $path {{param($count)}}
  $plan+=@{{entry=$entry;staged=$path}}
 }}
}}
Apply-VerifiedUpdate $plan {quoted(staged)}
$target=Join-Path {quoted(app)} 'OpenCore.exe'
$before=[IO.File]::ReadAllBytes($target)
$again=Join-Path {quoted(staged)} 'again';[IO.File]::WriteAllBytes($again,$before)
$bad=Join-Path {quoted(staged)} 'bad';[IO.File]::WriteAllText($bad,'wrong checksum')
$rollback=@(@{{entry=$plan[0].entry;staged=$again}},@{{entry=$index.files[1];staged=$bad}})
try{{Apply-VerifiedUpdate $rollback {quoted(staged)};throw 'Expected failed transaction'}}catch{{if($_.Exception.Message -eq 'Expected failed transaction'){{throw}}}}
if((Get-UpdateHash $target) -ne $plan[0].entry.sha256){{throw 'Rollback lost the installed executable'}}
foreach($path in @('app/../outside','app/models/personal.gguf','app/C:/other','python/../../outside')){{try{{Get-UpdateTarget $path;throw 'Path was accepted'}}catch{{if($_.Exception.Message -eq 'Path was accepted'){{throw}}}}}}
Write-Output 'TRANSACTION_OK'
"""
            script=root/'test.ps1';script.write_text(code,encoding='utf-8-sig')
            try:
                result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script)],capture_output=True,text=True,timeout=40)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('TRANSACTION_OK',result.stdout)
            finally:server.shutdown();server.server_close();thread.join()
            changed=next(entry for entry in index['files'] if entry['path']=='app/OpenCore.exe')
            self.assertEqual(ranges,[(changed['offset'],changed['offset']+changed['compressedBytes']-1)])
            self.assertEqual((app/'runtime.dll').stat().st_mtime_ns,unchanged)
            self.assertEqual((app/'OpenCore.exe').read_bytes(),(source/'OpenCore.exe').read_bytes())

if __name__=='__main__':unittest.main()
