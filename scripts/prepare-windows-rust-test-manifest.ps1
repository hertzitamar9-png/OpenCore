# Cargo's library test executable does not receive Tauri's application manifest.
# TaskDialogIndirect requires Common Controls v6 before the Windows loader runs.
# Prepare only Cargo's current test artifact; the production app is untouched.
$ErrorActionPreference = 'Stop'
# An absent original RT_MANIFEST is expected; inspect native exit codes below.
$PSNativeCommandUseErrorActionPreference = $false
if ($env:GITHUB_ACTIONS -ne 'true' -or [string]::IsNullOrEmpty($env:RUNNER_TEMP)) {
  throw 'Windows native test preparation runs only in GitHub Actions.'
}
$cargo = (Get-Command cargo.exe -ErrorAction Stop).Source
$manifestTool = (Get-Command mt.exe -ErrorAction Stop).Source
$output = Join-Path $env:RUNNER_TEMP 'opencore-rust-test-manifest'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$cargoMessages = Join-Path $output 'cargo-artifacts.jsonl'
$cargoDiagnostics = Join-Path $output 'cargo-stderr.log'
$compile = Start-Process -FilePath $cargo -ArgumentList @('test', '--lib', '--no-run', '--message-format=json') `
  -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -Wait -PassThru `
  -RedirectStandardOutput $cargoMessages -RedirectStandardError $cargoDiagnostics
Get-Content -LiteralPath $cargoDiagnostics | ForEach-Object { Write-Host $_ }
$messages = @(Get-Content -LiteralPath $cargoMessages | ForEach-Object { $_ | ConvertFrom-Json })
foreach ($message in $messages) {
  if ($message.reason -eq 'compiler-message' -and $message.message.rendered) {
    Write-Host $message.message.rendered
  }
}
if ($compile.ExitCode -ne 0) { throw "Compiling Rust library tests failed with exit code $($compile.ExitCode)." }
$executables = @($messages | Where-Object {
  $_.reason -eq 'compiler-artifact' -and $_.profile.test -and
  $_.target.name -eq 'opencore_control_center_lib' -and $_.executable
} | ForEach-Object { $_.executable } | Sort-Object -Unique)
if ($executables.Count -ne 1) { throw "Expected one current OpenCore library test executable; Cargo reported $($executables.Count)." }
$executable = (Resolve-Path -LiteralPath $executables[0]).Path
$dependency = Join-Path $output 'common-controls-v6.manifest'
# This is the dependency in tauri-build's default windows-app-manifest.xml.
# https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-taskdialogindirect
@'
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0" processorArchitecture="*"
        publicKeyToken="6595b64144ccf1df" language="*" />
    </dependentAssembly>
  </dependency>
</assembly>
'@ | Set-Content -LiteralPath $dependency -Encoding utf8
& $manifestTool -nologo -manifest $dependency -validate_manifest
if ($LASTEXITCODE -ne 0) { throw 'Common Controls v6 test manifest is invalid.' }
$original = Join-Path $output 'original.manifest'
$extractOutput = @(& $manifestTool -nologo "-inputresource:$executable;#1" "-out:$original" 2>&1)
$extractExit = $LASTEXITCODE
$embedArguments = @('-nologo', '-manifest', $dependency)
if ($extractExit -eq 0) {
  # Merge an existing activation manifest, preserving its other settings.
  $embedArguments += "-inputresource:$executable;#1"
} elseif (($extractOutput -join "`n") -notmatch '(?i)c101008c') {
  throw "Could not inspect the test executable activation manifest: $($extractOutput -join "`n")"
}
$embedArguments += "-outputresource:$executable;#1"
& $manifestTool @embedArguments
if ($LASTEXITCODE -ne 0) { throw 'Embedding the Common Controls v6 test manifest failed.' }
$embedded = Join-Path $output 'embedded.manifest'
& $manifestTool -nologo "-inputresource:$executable;#1" "-out:$embedded"
if ($LASTEXITCODE -ne 0) { throw 'Could not read back the embedded test manifest.' }
[xml]$activation = Get-Content -LiteralPath $embedded -Raw
$control = $activation.SelectSingleNode("//*[local-name()='assemblyIdentity' and @name='Microsoft.Windows.Common-Controls' and @version='6.0.0.0' and @publicKeyToken='6595b64144ccf1df']")
if ($null -eq $control) { throw 'The embedded test manifest does not activate Common Controls v6.' }
# Check the same executable's loader before cargo runs every library test.
$listing = @(& $executable --list 2>&1)
if ($LASTEXITCODE -ne 0) { throw "The manifested test executable cannot load: $($listing -join "`n")" }
Write-Host "Common Controls v6 embedded and test loader verified: $executable"
