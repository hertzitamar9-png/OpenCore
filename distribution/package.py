"""Build a complete, branded, per-user OpenCore installer from verified inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

FORBIDDEN_PARTS = {'secrets', 'logs', 'backups', 'codex-backups', 'repair-backups',
                   '__pycache__', '.git', '.cache', '.pytest_cache', 'test-python'}
FORBIDDEN_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.dpapi', '.gguf', '.safetensors', '.ocbackup'}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def collect(root):
    files = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if {part.lower() for part in relative.parts} & FORBIDDEN_PARTS or path.suffix.lower() in {'.pyc', '.pyo'}:
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError('Personal state or weights cannot be packaged: ' + str(relative))
        if any(word in path.name.lower() for word in ('before-', 'backup-', 'codex-backup')):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Package file leaves the verified input root')
        files.append((relative, path))
    return files


def nsis_literal(value):
    return str(value).replace('$', '$$').replace('"', '$\\"')


def file_commands(files, destination):
    lines, last = [], None
    for relative, path in files:
        folder = str(relative.parent).replace('/', '\\')
        target = destination if folder == '.' else destination + '\\' + nsis_literal(folder)
        if target != last:
            lines.append(f'  SetOutPath "{target}"')
            last = target
        lines.append(f'  File "{nsis_literal(path)}"')
    return '\n'.join(lines)


def installer_script(*, app, python, output, version, icon, branding, webview):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or any(int(part) > 65535 for part in version.split('.')):
        raise ValueError('The installer version must be a Windows-compatible release version')
    lines = [f'''Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
Name "OpenCore"
Caption "OpenCore Setup"
OutFile "{nsis_literal(output / 'OpenCore Package.exe')}"
InstallDir "$LOCALAPPDATA\\OpenCore"
InstallDirRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
Icon "{nsis_literal(icon)}"
UninstallIcon "{nsis_literal(icon)}"
!define MUI_ICON "{nsis_literal(icon)}"
!define MUI_UNICON "{nsis_literal(icon)}"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "{nsis_literal(branding / 'header.bmp')}"
!define MUI_WELCOMEFINISHPAGE_BITMAP "{nsis_literal(branding / 'welcome.bmp')}"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "{nsis_literal(branding / 'welcome.bmp')}"
BrandingText "OpenCore"
VIProductVersion "{version}.0"
VIAddVersionKey "ProductName" "OpenCore"
VIAddVersionKey "FileDescription" "OpenCore Setup"
VIAddVersionKey "FileVersion" "{version}"
VIAddVersionKey "LegalCopyright" "OpenCore and bundled component authors"
Var TestInstall
Var UpdateInstall
Var RestartAfterInstall
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\\OpenCore.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
Function .onInit
  SetRegView 64
  ${{IfNot}} ${{RunningX64}}
    MessageBox MB_ICONSTOP "OpenCore requires 64-bit Windows."
    Abort
  ${{EndIf}}
  ${{GetParameters}} $0
  ClearErrors
  ${{GetOptions}} $0 "/TEST" $1
  ${{IfNot}} ${{Errors}}
    StrCpy $TestInstall "1"
    Return
  ${{EndIf}}
  StrCpy $4 $INSTDIR 1
  StrCpy $5 $INSTDIR 1 -1
  ${{If}} $4 == '"'
  ${{AndIf}} $5 == '"'
    StrLen $4 $INSTDIR
    IntOp $4 $4 - 2
    StrCpy $INSTDIR $INSTDIR $4 1
  ${{EndIf}}
  ClearErrors
  ${{GetOptions}} $0 "/UPDATE" $1
  ${{IfNot}} ${{Errors}}
    StrCpy $UpdateInstall "1"
    ReadRegStr $3 HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "InstallLocation"
    StrCpy $4 $3 1
    StrCpy $5 $3 1 -1
    ${{If}} $4 == '"'
    ${{AndIf}} $5 == '"'
      StrLen $4 $3
      IntOp $4 $4 - 2
      StrCpy $3 $3 $4 1
    ${{EndIf}}
    ${{If}} $3 != ""
      StrCpy $INSTDIR $3
    ${{EndIf}}
  ${{EndIf}}
  ClearErrors
  ${{GetOptions}} $0 "/R" $1
  ${{IfNot}} ${{Errors}}
    StrCpy $RestartAfterInstall "1"
  ${{EndIf}}
  StrCpy $2 0
  wait_for_app:
    FindWindow $1 "" "OpenCore"
    ${{If}} $1 != 0
      ${{If}} $UpdateInstall == "1"
        IntOp $2 $2 + 1
        ${{If}} $2 < 60
          Sleep 500
          Goto wait_for_app
        ${{EndIf}}
      ${{EndIf}}
      IfSilent app_still_open
      MessageBox MB_ICONINFORMATION "Close OpenCore, including its tray icon, before installing."
      app_still_open:
      SetErrorLevel 5
      Quit
    ${{EndIf}}
FunctionEnd
Section "OpenCore"
  ${{If}} $TestInstall != "1"
    ReadRegStr $0 HKCU "Software\\Microsoft\\EdgeUpdate\\Clients\\{{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}}" "pv"
    ${{If}} $0 == ""
      ReadRegStr $0 HKLM "Software\\Microsoft\\EdgeUpdate\\Clients\\{{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}}" "pv"
    ${{EndIf}}
    ${{If}} $0 == ""
      ReadRegStr $0 HKLM "Software\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}}" "pv"
    ${{EndIf}}
    ${{If}} $0 == ""
      InitPluginsDir
      SetOutPath "$PLUGINSDIR"
      File "{nsis_literal(webview)}"
      DetailPrint "Preparing Microsoft WebView2. Internet access may be required."
      ExecWait '"$PLUGINSDIR\\MicrosoftEdgeWebview2Setup.exe" /silent /install' $1
      ${{If}} $1 != 0
        IfSilent webview_failed
        MessageBox MB_ICONSTOP "WebView2 could not be installed. Connect to the internet and run OpenCore Setup again."
        webview_failed:
        SetErrorLevel 6
        Quit
      ${{EndIf}}
    ${{EndIf}}
  ${{EndIf}}
''', file_commands(app, '$INSTDIR'), '''
  ${If} $TestInstall == "1"
''', file_commands(python, '$INSTDIR\\test-python'), '''
    Goto installed
  ${EndIf}
  SetOverwrite ifnewer
''', file_commands(python, '$PROFILE\\OpenCore\\runtime-setup\\python-3.12.10'), f'''
  SetOverwrite on
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\\uninstall.exe"
  CreateDirectory "$SMPROGRAMS\\OpenCore"
  CreateShortcut "$SMPROGRAMS\\OpenCore\\OpenCore.lnk" "$INSTDIR\\OpenCore.exe" "" "$INSTDIR\\OpenCore.exe" 0
  CreateShortcut "$DESKTOP\\OpenCore.lnk" "$INSTDIR\\OpenCore.exe" "" "$INSTDIR\\OpenCore.exe" 0
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "DisplayName" "OpenCore"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "DisplayVersion" "{version}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "UninstallString" '$\\"$INSTDIR\\uninstall.exe$\\"'
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore" "DisplayIcon" "$INSTDIR\\OpenCore.exe"
  installed:
SectionEnd
Function .onInstSuccess
  ${{If}} $TestInstall == "1"
    Return
  ${{EndIf}}
  ${{If}} $RestartAfterInstall == "1"
    ${{GetParameters}} $0
    StrCpy $1 ""
    ClearErrors
    ${{GetOptions}} $0 "/ARGS" $1
    ${{If}} ${{Errors}}
      StrCpy $1 ""
    ${{EndIf}}
    SetOutPath "$INSTDIR"
    Exec '"$INSTDIR\\OpenCore.exe" $1'
  ${{EndIf}}
FunctionEnd
Section "Uninstall"
  SetRegView 64
  MessageBox MB_OKCANCEL "Remove OpenCore? Your chats, models and managed environments will be kept." IDOK remove
  Abort
  remove:
  Delete "$DESKTOP\\OpenCore.lnk"
  Delete "$SMPROGRAMS\\OpenCore\\OpenCore.lnk"
  RMDir "$SMPROGRAMS\\OpenCore"
  DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenCore"
''']
    for relative, _ in app:
        lines.append(f'  Delete "$INSTDIR\\{nsis_literal(str(relative).replace(chr(47), chr(92)))}"')
    directories = {parent for relative, _ in app for parent in relative.parents if str(parent) != '.'}
    for directory in sorted(directories, key=lambda value: (len(value.parts), str(value)), reverse=True):
        lines.append(f'  RMDir "$INSTDIR\\{nsis_literal(str(directory).replace(chr(47), chr(92)))}"')
    lines.append('  Delete "$INSTDIR\\uninstall.exe"\n  RMDir "$INSTDIR"\nSectionEnd\n')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--icon', type=Path, required=True)
    parser.add_argument('--logo', type=Path, required=True)
    parser.add_argument('--webview', type=Path, required=True)
    parser.add_argument('--nsis', type=Path)
    args = parser.parse_args()
    args.app, args.python, args.output = args.app.resolve(), args.python.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.app / 'build-repair.json').unlink(missing_ok=True)
    info = {'build': args.version, 'commit': args.commit, 'nativeBaseline': '0.2.305',
            'appExeSha256': file_hash(args.app / 'OpenCore.exe'),
            'personalStateIncluded': False, 'modelWeightsIncluded': False}
    (args.app / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    app, python = collect(args.app), collect(args.python)
    files = [{'path': label + '/' + relative.as_posix(), 'bytes': path.stat().st_size, 'sha256': file_hash(path)}
             for group, label in [(app, 'app'), (python, 'python')] for relative, path in group]
    manifest = {**info, 'files': files, 'bytes': sum(file['bytes'] for file in files)}
    (args.output / 'package-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    branding = args.output / 'branding'
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(Path(__file__).with_name('brand-assets.ps1')), '-Logo', str(args.logo.resolve()),
                    '-Output', str(branding)], check=True)
    script = installer_script(app=app, python=python, output=args.output, version=args.version,
                              icon=args.icon.resolve(), branding=branding, webview=args.webview.resolve())
    source = args.output / 'OpenCore Setup.nsi'
    source.write_text(script, encoding='utf-8-sig')
    if args.nsis:
        subprocess.run([str(args.nsis.resolve()), '/V2', str(source)], check=True)
        installer = args.output / 'OpenCore Package.exe'
        (args.output / 'SHA256SUMS.txt').write_text(file_hash(installer) + '  OpenCore Package.exe\n', encoding='ascii')
    print(json.dumps({'build': args.version, 'files': len(files), 'bytes': manifest['bytes'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
