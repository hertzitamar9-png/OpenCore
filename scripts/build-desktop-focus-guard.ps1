#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest

if ($env:GITHUB_ACTIONS -ne 'true' -or [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
  throw 'The desktop focus guard may be compiled only in GitHub Actions.'
}
if (-not $IsWindows -or $env:VSCMD_ARG_TGT_ARCH -ne 'x64') {
  throw 'Run ilammy/msvc-dev-cmd with arch: x64 before building the desktop focus guard.'
}

$checkout = Split-Path -Parent $PSScriptRoot
$source = Join-Path $checkout 'src-tauri/native/desktop_focus_guard.c'
$dll = Join-Path $checkout 'src-tauri/resources/desktop/opencore-focus-guard.dll'
$intermediate = Join-Path $env:RUNNER_TEMP 'opencore-focus-guard'
$object = Join-Path $intermediate 'opencore-focus-guard.obj'
$library = Join-Path $intermediate 'opencore-focus-guard.lib'
$sourceText = Get-Content -LiteralPath $source -Raw
if ($sourceText -notmatch '\bUINT\s+WINAPI\s+OpenCoreDesktopHookAbi\s*\(\s*(?:void)?\s*\)\s*\{\s*return\s+2[Uu]?\s*;\s*\}' -or
    $sourceText -notmatch '\bLRESULT\s+CALLBACK\s+OpenCoreDesktopCbtProc\s*\(\s*int(?:\s+\w+)?\s*,\s*WPARAM(?:\s+\w+)?\s*,\s*LPARAM(?:\s+\w+)?\s*\)' -or
    $sourceText -notmatch '\bLRESULT\s+CALLBACK\s+OpenCoreDesktopAckProc\s*\(\s*int(?:\s+\w+)?\s*,\s*WPARAM(?:\s+\w+)?\s*,\s*LPARAM(?:\s+\w+)?\s*\)') {
  throw 'The desktop focus guard C source does not declare the agreed Windows hook ABI 2.'
}
$compiler = (Get-Command cl.exe -ErrorAction Stop).Source
$dumpbin = (Get-Command dumpbin.exe -ErrorAction Stop).Source
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dll), $intermediate | Out-Null

Push-Location -LiteralPath $intermediate
try {
  $compilerArguments = @(
    '/nologo', '/TC', '/LD', '/O2', '/W4', '/WX', '/MT', '/guard:cf',
    "/Fo$object", "/Fe$dll", $source,
    '/link', 'user32.lib', 'kernel32.lib', '/MACHINE:X64', '/INCREMENTAL:NO', '/DYNAMICBASE', '/NXCOMPAT',
    '/GUARD:CF', '/MANIFEST:EMBED', "/IMPLIB:$library"
  )
  & $compiler @compilerArguments
  if ($LASTEXITCODE -ne 0) { throw "Desktop focus guard compilation failed with exit code $LASTEXITCODE." }
} finally {
  Pop-Location
}

# Rust embeds these exact DLL bytes. Any future Authenticode signing must be
# performed here, before verification and before any Cargo test/build step.
$reader = [IO.BinaryReader]::new([IO.File]::OpenRead($dll))
try {
  if ($reader.BaseStream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5a4d) {
    throw 'The desktop focus guard output is not a Windows PE file.'
  }
  [void]$reader.BaseStream.Seek(0x3c, [IO.SeekOrigin]::Begin)
  $peOffset = $reader.ReadInt32()
  if ($peOffset -lt 64 -or $peOffset -gt ($reader.BaseStream.Length - 26)) {
    throw 'The desktop focus guard output has an invalid PE header.'
  }
  [void]$reader.BaseStream.Seek($peOffset, [IO.SeekOrigin]::Begin)
  if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
    throw 'The desktop focus guard must be an x64 Windows binary.'
  }
  [void]$reader.BaseStream.Seek($peOffset + 22, [IO.SeekOrigin]::Begin)
  if (($reader.ReadUInt16() -band 0x2000) -eq 0 -or $reader.ReadUInt16() -ne 0x20b) {
    throw 'The desktop focus guard must be a PE32+ DLL.'
  }
} finally {
  $reader.Dispose()
}

$exports = @(& $dumpbin /nologo /exports $dll 2>&1)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect desktop focus guard exports: $($exports -join "`n")" }
$exportText = $exports -join "`n"
foreach ($name in @('OpenCoreDesktopHookAbi', 'OpenCoreDesktopCbtProc', 'OpenCoreDesktopAckProc')) {
  if ($exportText -cnotmatch "(?m)^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+$name\s*$") {
    throw "The desktop focus guard DLL is missing the exact export $name."
  }
}
$digest = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Desktop focus guard built and verified: $dll (SHA-256 $digest)"
