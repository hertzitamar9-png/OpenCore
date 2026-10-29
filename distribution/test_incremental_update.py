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
