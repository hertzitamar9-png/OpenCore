"""Execute the generated updater on disposable files; never touch the user's app."""
import ast,ctypes,hashlib,json,os,re,struct,subprocess,tempfile,threading,time,uuid
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from incremental_update import build_payload,update_script
from package import nsis_literal

def run(nsis):
    with tempfile.TemporaryDirectory(prefix='opencore-incremental-smoke-') as temporary:
        root=Path(temporary).resolve();source=root/'source';python=root/'python';target=root/'installed';output=root/'release';branding=root/'branding'
        for folder in [source,python,target,output,branding]:folder.mkdir()
        (source/'OpenCore.exe').write_bytes(os.urandom(768000))
        (source/'runtime.dll').write_bytes(b'unchanged shared runtime'*10000)
        (target/'OpenCore.exe').write_bytes(b'old executable')
        (target/'runtime.dll').write_bytes((source/'runtime.dll').read_bytes())
        (source/'build-info.json').write_text(json.dumps({'build':'0.3.204','commit':'a'*40,'appExeSha256':hashlib.sha256((source/'OpenCore.exe').read_bytes()).hexdigest()}))
        (target/'build-info.json').write_text(json.dumps({'build':'0.3.203','commit':'b'*40,'appExeSha256':hashlib.sha256((target/'OpenCore.exe').read_bytes()).hexdigest()}))
        old_files=[{'path':'app/'+file.name,'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()} for file in target.iterdir()]
        (target/'installed-file-manifest.json').write_text(json.dumps({'schema':1,'version':'0.3.203','commit':'b'*40,'files':old_files}))
        # Reproduce a local app repair without invalidating unchanged DLL pins.
        (target/'OpenCore.exe').write_bytes(b'locally repaired executable')
        unchanged=(target/'runtime.dll').stat().st_mtime_ns
        index=build_payload(source,python,output,'0.3.204','a'*40)
        assert all(entry['path'].startswith('app/') for entry in index['files'])
        payload=(output/'OpenCore.Update.data').read_bytes();ranges=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
                if not match:self.send_error(400);return
                start,end=map(int,match.groups());ranges.append((start,end));chunk=payload[start:end+1]
                self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{end}/{len(payload)}');self.send_header('Content-Length',str(len(chunk)));self.end_headers()
                for position in range(0,len(chunk),32768):
                    self.wfile.write(chunk[position:position+32768]);self.wfile.flush();time.sleep(.035)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            # Only this disposable copy admits the loopback fixture. The release
            # helper continues to require the exact HTTPS repository/tag URL.
            base=f'http://127.0.0.1:{server.server_port}/'
            original='https://github.com/hertzitamar9-png/OpenCore/releases/download/'
            helper=Path(__file__).with_name('apply-incremental-update.ps1').resolve()
            text=helper.read_text(encoding='utf-8');assert text.count(original)==1
            fixture_helper=root/'fixture-apply.ps1';fixture_helper.write_text(text.replace(original,base),encoding='utf-8-sig')
            index['payloadUrl']=base+'app-v0.3.204/OpenCore.Update.data'
            (output/'incremental-index.json').write_text(json.dumps(index),encoding='utf-8')
            def bitmap(path,width,height):
                row=b'\x33\x22\x11'*width+b'\0'*((-3*width)%4);pixels=row*height
                path.write_bytes(struct.pack('<2sIHHI',b'BM',54+len(pixels),0,0,54)+struct.pack('<IiiHHIIiiII',40,width,height,1,24,0,len(pixels),2835,2835,0,0)+pixels)
            bitmap(branding/'header.bmp',150,57);bitmap(branding/'welcome.bmp',164,314)
            script=update_script(output,'0.3.204',Path(__file__).resolve().parents[1]/'src-tauri/icons/icon.ico',branding,Path('unused.exe'))
            assert script.count(nsis_literal(helper))==1
            script=script.replace(nsis_literal(helper),nsis_literal(fixture_helper))
            marker='StrCpy $TestInstall "1"\n    Return'
            assert script.count(marker)==1
            script=script.replace(marker,'StrCpy $TestInstall "1"\n    StrCpy $UpdateInstall "1"\n    SetSilent normal\n    SetAutoClose true\n    Return')
            nsi=root/'fixture.nsi';nsi.write_text(script,encoding='utf-8-sig')
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            built=subprocess.run([str(nsis),'/V2',str(nsi)],capture_output=True,text=True,timeout=60,creationflags=flags)
            assert built.returncode==0,built.stdout+built.stderr
            exe=output/'OpenCore Update.exe'
            # Reuse only the audited process structures from the existing NSIS
            # test, without executing its module-level setup or registry writes.
            definitions=ast.parse(Path(__file__).with_name('qa-update-installer.py').read_text(encoding='utf-8'))
            classes=ast.Module(body=[node for node in definitions.body if isinstance(node,ast.ClassDef)],type_ignores=[])
            scope={'ctypes':ctypes,'wintypes':wintypes,'subprocess':subprocess}
            exec(compile(classes,'isolated-installer-process','exec'),scope)
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.CreateDesktopW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p];user.CreateDesktopW.restype=wintypes.HANDLE
            user.CloseDesktop.argtypes=[wintypes.HANDLE]
            callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
            user.EnumDesktopWindows.argtypes=[wintypes.HANDLE,callback,wintypes.LPARAM];user.EnumChildWindows.argtypes=[wintypes.HWND,callback,wintypes.LPARAM]
            user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int];user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
            user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
            user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM];user.SendMessageW.restype=ctypes.c_ssize_t
            name='OpenCoreIncremental_'+uuid.uuid4().hex;desktop=user.CreateDesktopW(name,None,None,0,0x10000000,None)
            assert desktop,'Could not create the isolated updater test desktop'
            observed={'downloadText':False,'movingProgress':False,'texts':[]}
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            kernel.CreateFileW.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            # A normal full scan cannot open this DLL for hashing. Both actual
            # updater runs must succeed using the saved release comparison.
            locked=kernel.CreateFileW(str(target/'runtime.dll'),0x80000000,0x2|0x4,None,3,0,None)
            assert locked not in (None,ctypes.c_void_p(-1).value),'Could not lock the unchanged runtime fixture'
            try:
                for attempt in range(2):
                    applied=scope['DesktopProcess'](exe,['/S','/TEST','/D='+str(target)],name)
                    try:
                        deadline=time.monotonic()+90
                        def child(window,_):
                            text=ctypes.create_unicode_buffer(1024);kind=ctypes.create_unicode_buffer(256)
                            user.GetWindowTextW(window,text,len(text));user.GetClassNameW(window,kind,len(kind))
                            if text.value and text.value not in observed['texts'] and len(observed['texts'])<30:observed['texts'].append(text.value)
                            if kind.value=='Static' and text.value.startswith('Downloading '):observed['downloadText']=True
                            if kind.value=='msctls_progress32' and 15<int(user.SendMessageW(window,0x408,0,0))<85:observed['movingProgress']=True
                            return True
                        child_callback=callback(child)
                        def parent(window,_):
                            owner=wintypes.DWORD();user.GetWindowThreadProcessId(window,ctypes.byref(owner))
                            if owner.value==applied.pid:user.EnumChildWindows(window,child_callback,0)
                            return True
                        parent_callback=callback(parent)
                        while applied.poll() is None and time.monotonic()<deadline:
                            user.EnumDesktopWindows(desktop,parent_callback,0);time.sleep(.025)
                        log=target/'logs/incremental-update.log'
                        assert applied.poll() is not None,{'error':'Incremental updater did not exit','observed':observed,'log':log.read_text(encoding='utf-8-sig',errors='replace')[-5000:] if log.is_file() else 'No log'}
                        assert applied.returncode==0,log.read_text(encoding='utf-8-sig',errors='replace') if log.is_file() else 'No update log'
                    finally:applied.close()
                    assert (target/'OpenCore.exe').read_bytes()==(source/'OpenCore.exe').read_bytes()
                    assert (target/'runtime.dll').stat().st_mtime_ns==unchanged
                    assert len(ranges)==2,'An unchanged file was downloaded again'
                    summary=json.loads((target/'logs/incremental-update-summary.json').read_text())
                    assert summary['mode']=='release-difference',summary
                    assert summary['filesInspected']==(2 if attempt==0 else 0),summary
                    assert summary['unchangedFilesSkipped']==(1 if attempt==0 else 3),summary
                    assert not list(target.glob('.update-*')),'Successful update left staging directories'
                assert observed['downloadText'] and observed['movingProgress'],observed
            finally:kernel.CloseHandle(locked);user.CloseDesktop(desktop)
            return {'actualNsisApplyPassed':True,'repeatUpdateDownloadedNoFiles':True,'unchangedLibraryUntouched':True,'unchangedFilesNotReadVerified':True,'releaseDifferenceVerified':True,'downloadedRanges':len(ranges),'visibleDownloadProgressVerified':True}
        finally:server.shutdown();server.server_close();thread.join()
