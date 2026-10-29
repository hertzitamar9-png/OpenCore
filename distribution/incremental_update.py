"""Signed updater EXE plus independently compressed, range-addressable files."""
import gzip,hashlib,json,re,struct,subprocess
from pathlib import Path,PurePosixPath
from package import collect,file_hash,installer_script,nsis_literal

def build_payload(app,python,output,version,commit):
    output.mkdir(parents=True,exist_ok=True)
    entries=[]
    with (output/'OpenCore.Update.data').open('wb') as payload:
        for group,label in [(collect(app),'app'),(collect(python),'python')]:
            for relative,source in group:
                begin=payload.tell()
                with gzip.GzipFile(fileobj=payload,mode='wb',mtime=0,compresslevel=6) as compressed,source.open('rb') as stream:
                    while block:=stream.read(1024*1024):compressed.write(block)
                end=payload.tell()
                entries.append({'path':label+'/'+relative.as_posix(),'bytes':source.stat().st_size,'sha256':file_hash(source),'offset':begin,'compressedBytes':end-begin})
    index={'schema':1,'version':version,'commit':commit,'files':entries,
           'payloadUrl':f'https://github.com/hertzitamar9-png/OpenCore/releases/download/app-v{version}/OpenCore.Update.data'}
    (output/'incremental-index.json').write_text(json.dumps(index,separators=(',',':')),encoding='utf-8')
    return index

def update_script(output,version,icon,branding,webview):
    script=installer_script(app=[],python=[],output=output,version=version,icon=icon,branding=branding,webview=webview)
    script=script.replace('OpenCore Package.exe','OpenCore Update.exe')
    start=script.index('Section "OpenCore"')
    end=script.index('SectionEnd',start)+len('SectionEnd')
    files='\n'.join(f'  File "/oname={name}" "{nsis_literal(path)}"' for name,path in [
        ('incremental-index.json',output/'incremental-index.json'),('apply-incremental-update.ps1',Path(__file__).with_name('apply-incremental-update.ps1'))])
    body=f'''Section "OpenCore"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
{files}
  DetailPrint "Checking installed files; unchanged runtimes will be kept."
  SetDetailsView show
  nsExec::ExecToLog '"$WINDIR\\Sysnative\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\\apply-incremental-update.ps1" -AppRoot "$INSTDIR" -PythonRoot "$PROFILE\\OpenCore\\runtime-setup\\python-3.12.10" -IndexPath "$PLUGINSDIR\\incremental-index.json" -WindowHandle $HWNDPARENT'
  Pop $0
  ${{If}} $0 != "0"
    MessageBox MB_ICONSTOP "The update could not be applied. Your existing app and downloaded models were kept. See $LOCALAPPDATA\\OpenCore\\logs\\incremental-update.log."
    SetErrorLevel 7
    Quit
  ${{EndIf}}
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "DisplayVersion" "{version}"
  StrCpy $UpdateInstallSucceeded "1"
SectionEnd'''
    return script[:start]+body+script[end:]

def build(app,python,output,version,commit,icon,branding,webview,nsis):
    index=build_payload(app,python,output,version,commit)
    source=output/'OpenCore Update.nsi'
    source.write_text(update_script(output,version,icon,branding,webview),encoding='utf-8-sig')
    subprocess.run([str(nsis),'/V2',str(source)],check=True)
    return index
