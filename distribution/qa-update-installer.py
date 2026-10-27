"""Run the real generated NSIS handoff against disposable mock executables."""
import argparse, ctypes, hashlib, json, os, pathlib, shutil, struct, subprocess, tempfile, time, uuid, winreg
from ctypes import wintypes
import package

parser=argparse.ArgumentParser()
parser.add_argument('--nsis',type=pathlib.Path,required=True)
args=parser.parse_args()
root=pathlib.Path(tempfile.mkdtemp(prefix='opencore-update-qa-',dir=os.environ.get('RUNNER_TEMP'))).resolve()
repo=pathlib.Path(__file__).resolve().parent.parent

target = root / 'installed'
other = root / 'unrelated'
payload = root / 'payload'
branding = root / 'branding'
for directory in (target, other, payload, branding): directory.mkdir()
def bitmap(path,width,height):
    row=b'\x33\x22\x11'*width+b'\x00'*((-3*width)%4)
    pixels=row*height
    header=struct.pack('<2sIHHI',b'BM',54+len(pixels),0,0,54)
    header+=struct.pack('<IiiHHIIiiII',40,width,height,1,24,0,len(pixels),2835,2835,0,0)
    path.write_bytes(header+pixels)
bitmap(branding/'header.bmp',150,57)
bitmap(branding/'welcome.bmp',164,314)
compiler = pathlib.Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
nsis = args.nsis.resolve(strict=True)
old_source = root/'old.cs'
old_source.write_text('using System.Threading; class Program { static void Main() { Thread.Sleep(60000); } }')
marker = root/'new-started.txt'
new_source = root/'new.cs'
new_source.write_text('using System; using System.IO; using System.Threading; class Program { static void Main() { File.WriteAllText(@"'+str(marker).replace('"','""')+'", "new"); Thread.Sleep(60000); } }')
flags = subprocess.CREATE_NO_WINDOW
def compile_cs(source, output, *extra):
    result = subprocess.run([str(compiler),'/nologo','/target:winexe','/out:'+str(output),*extra,str(source)],
                            capture_output=True,text=True,creationflags=flags)
    if result.returncode: raise RuntimeError(result.stdout+result.stderr)
compile_cs(old_source, target/'OpenCore.exe')
shutil.copyfile(target/'OpenCore.exe',other/'OpenCore.exe')
compile_cs(new_source,payload/'OpenCore.exe')
webview = root/'MicrosoftEdgeWebview2Setup.exe'
webview_source = root/'webview.cs'
webview_source.write_text('class Program { static void Main() {} }')
compile_cs(webview_source,webview)
decoy_source=root/'decoy.cs'
decoy_source.write_text('using System; using System.Windows.Forms; class Program { [STAThread] static void Main() { var f=new Form(); f.Text="OpenCore"; var handle=f.Handle; Application.Run(); } }')
decoy_exe=root/'decoy.exe'
compile_cs(decoy_source,decoy_exe,'/reference:System.Windows.Forms.dll')

regpath = r'Software\OpenCoreUpdaterTest_'+uuid.uuid4().hex
script = package.installer_script(app=[(pathlib.Path('OpenCore.exe'),payload/'OpenCore.exe')],python=[],
    output=root,version='0.3.175',icon=repo/'src-tauri/icons/icon.ico',branding=branding,webview=webview)
original_key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\OpenCore'
if script.count(original_key) < 7: raise RuntimeError('Unexpected installer registry references')
script=script.replace(original_key,regpath)
script=script.replace(r'$SMPROGRAMS\OpenCore\OpenCore.lnk',str(root/'start-menu.lnk'))
script=script.replace(r'$SMPROGRAMS\OpenCore',str(root/'start-menu'))
script=script.replace(r'$DESKTOP\OpenCore.lnk',str(root/'desktop.lnk'))
if original_key in script or r'$DESKTOP\OpenCore.lnk' in script: raise RuntimeError('Unsafe installer destination')
nsi=root/'fixture.nsi';nsi.write_text(script,encoding='utf-8')
compiled=subprocess.run([str(nsis),str(nsi)],capture_output=True,text=True,creationflags=flags,timeout=60)
(root/'compiler.log').write_text(compiled.stdout+'\n'+compiled.stderr)
if compiled.returncode: raise RuntimeError('NSIS compilation failed; inspect compiler.log')

installer=root/'OpenCore Package.exe'
if not installer.is_file(): raise RuntimeError('The fixture installer was not built')
with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,regpath,0,winreg.KEY_WRITE|winreg.KEY_WOW64_64KEY) as key:
    winreg.SetValueEx(key,'InstallLocation',0,winreg.REG_SZ,str(target))
processes=[]
result={}
try:
    first=subprocess.Popen([str(target/'OpenCore.exe')],creationflags=flags);processes.append(first)
    unrelated=subprocess.Popen([str(other/'OpenCore.exe')],creationflags=flags);processes.append(unrelated)
    decoy=subprocess.Popen([str(decoy_exe)],creationflags=flags);processes.append(decoy)
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.FindWindowExW.argtypes=[wintypes.HWND,wintypes.HWND,wintypes.LPCWSTR,wintypes.LPCWSTR]
    user.FindWindowExW.restype=wintypes.HWND
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    deadline=time.monotonic()+10
    found=False
    while time.monotonic()<deadline and not found:
        cursor=None
        for _ in range(200):
            cursor=user.FindWindowExW(None,cursor,None,'OpenCore')
            if not cursor:break
            pid=wintypes.DWORD();user.GetWindowThreadProcessId(cursor,ctypes.byref(pid))
            if pid.value==decoy.pid:found=True;break
        if not found:time.sleep(.1)
    if not found:raise RuntimeError('The unrelated OpenCore window was not created')
    ordinary=subprocess.run([str(installer),'/S'],capture_output=True,creationflags=flags,timeout=30)
    if ordinary.returncode!=5 or first.poll() is not None or unrelated.poll() is not None:
        raise RuntimeError('A standard install did not safely detect the running target')
    result['normalInstallDetectsActualProcessWithoutStoppingIt']=True
    update=subprocess.Popen([str(installer),'/S','/UPDATE'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=flags)
    time.sleep(1)
    second=subprocess.Popen([str(target/'OpenCore.exe')],creationflags=flags);processes.append(second)
    stdout,stderr=update.communicate(timeout=45)
    if update.returncode!=0: raise RuntimeError('The update exited '+str(update.returncode)+' '+stderr.decode(errors='replace'))
    deadline=time.monotonic()+15
    while time.monotonic()<deadline and not marker.exists(): time.sleep(.25)
    if first.poll() is None or second.poll() is None: raise RuntimeError('The old app or its immediate restart survived the handoff')
    if unrelated.poll() is not None: raise RuntimeError('A different installation was stopped')
    if not marker.exists() or marker.read_text()!='new': raise RuntimeError('The new executable did not reopen')
    def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    if sha(target/'OpenCore.exe')!=sha(payload/'OpenCore.exe'): raise RuntimeError('The new executable was not installed')
    result.update({'oldAndRestartedProcessesStopped':True,'unrelatedProcessSurvived':True,
                   'newExecutableInstalledAndReopened':True,'installedSha256':sha(target/'OpenCore.exe')})
    # Stop only the disposable test installation's new process.
    stop=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(repo/'distribution/stop-opencore-update.ps1'),'-AppPath',str(target/'OpenCore.exe')],
        capture_output=True,creationflags=flags,timeout=40)
    if stop.returncode: raise RuntimeError('Could not stop the disposable reopened app')
    ordinary2=subprocess.run([str(installer),'/S'],capture_output=True,creationflags=flags,timeout=30)
    if ordinary2.returncode!=0: raise RuntimeError('A normal installer was blocked by an unrelated OpenCore window')
    result['normalInstallAcceptsUnrelatedOpenCoreWindows']=True
    result['passed']=True
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate();process.wait(timeout=5)
    try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER,regpath)
    except FileNotFoundError: pass
    (root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
