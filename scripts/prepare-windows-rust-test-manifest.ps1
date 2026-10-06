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
# -Wait follows the entire descendant tree, including MSVC's persistent PDB
# server. Wait for Cargo itself and show its progress as the redirected log grows.
$compile = Start-Process -FilePath $cargo -ArgumentList @('test', '--lib', '--no-run', '--message-format=json') `
  -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput $cargoMessages -RedirectStandardError $cargoDiagnostics
$diagnosticsStream = [System.IO.File]::Open($cargoDiagnostics, [System.IO.FileMode]::Open,
  [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$diagnosticsReader = [System.IO.StreamReader]::new($diagnosticsStream)
try {
  do {
    $progress = $diagnosticsReader.ReadToEnd()
    if ($progress.Length -gt 0) { Write-Host -NoNewline $progress }
  } while (-not $compile.WaitForExit(1000))
  # Drain redirected output callbacks before parsing the completed artifact log.
  $compile.WaitForExit()
  $progress = $diagnosticsReader.ReadToEnd()
  if ($progress.Length -gt 0) { Write-Host -NoNewline $progress }
} finally {
  $diagnosticsReader.Dispose()
}
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
$original = Join-Path $output 'original.manifest'
$extractOutput = @(& $manifestTool -nologo "-inputresource:$executable;#1" "-out:$original" 2>&1)
$extractExit = $LASTEXITCODE
$activation = [System.Xml.XmlDocument]::new()
$activation.PreserveWhitespace = $true
if ($extractExit -eq 0) {
  $activation.Load($original)
} elseif (($extractOutput -join "`n") -notmatch '(?i)c101008c') {
  throw "Could not inspect the test executable activation manifest: $($extractOutput -join "`n")"
} else {
  $activation.LoadXml('<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0" />')
}

function Set-NativeTestActivationManifest {
  param([System.Xml.XmlDocument]$Manifest)
  $namespace = 'urn:schemas-microsoft-com:asm.v1'
  $assembly = $Manifest.DocumentElement
  if ($assembly.LocalName -ne 'assembly' -or $assembly.NamespaceURI -ne $namespace) {
    throw 'The existing test activation manifest has an invalid assembly root.'
  }
  $namespaces = [System.Xml.XmlNamespaceManager]::new($Manifest.NameTable)
  $namespaces.AddNamespace('asm', $namespace)
  $definitions = $assembly.SelectNodes('asm:assemblyIdentity', $namespaces)
  if ($definitions.Count -gt 1) { throw 'The test manifest contains multiple definition identities.' }
  if ($definitions.Count -eq 0) {
    # mt.exe validates a complete application manifest, requiring this DEF-context
    # identity in addition to the dependent assembly's REF-context identity.
    # https://learn.microsoft.com/en-us/windows/win32/sbscs/application-manifests
    $definition = $Manifest.CreateElement('assemblyIdentity', $namespace)
    $definition.SetAttribute('type', 'win32')
    $definition.SetAttribute('name', 'OpenCore.ControlCenter.NativeTests')
    $definition.SetAttribute('version', '1.0.0.0')
    $noInherit = $assembly.SelectSingleNode('asm:noInherit', $namespaces)
    if ($null -ne $noInherit) {
      [void]$assembly.InsertAfter($definition, $noInherit)
    } else {
      [void]$assembly.PrependChild($definition)
    }
  }
  # Change only this dependency; retain the definition identity and every other
  # activation setting from the extracted current Cargo test executable.
  $controls = $assembly.SelectNodes('asm:dependency/asm:dependentAssembly/asm:assemblyIdentity[@name="Microsoft.Windows.Common-Controls"]', $namespaces)
  if ($controls.Count -gt 1) { throw 'The test manifest contains multiple Common Controls dependencies.' }
  if ($controls.Count -eq 0) {
    $dependency = $Manifest.CreateElement('dependency', $namespace)
    $dependent = $Manifest.CreateElement('dependentAssembly', $namespace)
    $control = $Manifest.CreateElement('assemblyIdentity', $namespace)
    [void]$dependent.AppendChild($control)
    [void]$dependency.AppendChild($dependent)
    [void]$assembly.AppendChild($dependency)
  } else {
    $control = $controls.Item(0)
  }
  # This is the dependency in tauri-build's default windows-app-manifest.xml.
  # https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-taskdialogindirect
  $control.SetAttribute('type', 'win32')
  $control.SetAttribute('name', 'Microsoft.Windows.Common-Controls')
  $control.SetAttribute('version', '6.0.0.0')
  $control.SetAttribute('processorArchitecture', '*')
  $control.SetAttribute('publicKeyToken', '6595b64144ccf1df')
  $control.SetAttribute('language', '*')
}

Set-NativeTestActivationManifest $activation
$expectedDefinition = $activation.DocumentElement.SelectSingleNode('*[local-name()="assemblyIdentity" and namespace-uri()="urn:schemas-microsoft-com:asm.v1"]')
$prepared = Join-Path $output 'native-tests.manifest'
$activation.Save($prepared)
& $manifestTool -nologo -manifest $prepared -validate_manifest
if ($LASTEXITCODE -ne 0) { throw 'The complete native test activation manifest is invalid.' }
& $manifestTool -nologo -manifest $prepared "-outputresource:$executable;#1"
if ($LASTEXITCODE -ne 0) { throw 'Embedding the Common Controls v6 test manifest failed.' }
$embedded = Join-Path $output 'embedded.manifest'
& $manifestTool -nologo "-inputresource:$executable;#1" "-out:$embedded"
if ($LASTEXITCODE -ne 0) { throw 'Could not read back the embedded test manifest.' }
[xml]$activation = Get-Content -LiteralPath $embedded -Raw
$definition = $activation.DocumentElement.SelectSingleNode('*[local-name()="assemblyIdentity" and namespace-uri()="urn:schemas-microsoft-com:asm.v1"]')
if ($null -eq $definition) { throw 'The embedded test manifest has no definition identity.' }
foreach ($attribute in $expectedDefinition.Attributes) {
  if ($definition.GetAttribute($attribute.LocalName, $attribute.NamespaceURI) -cne $attribute.Value) {
    throw "Embedding changed the test definition identity attribute $($attribute.Name)."
  }
}
$control = $activation.SelectSingleNode("//*[local-name()='assemblyIdentity' and @name='Microsoft.Windows.Common-Controls' and @version='6.0.0.0' and @publicKeyToken='6595b64144ccf1df']")
if ($null -eq $control) { throw 'The embedded test manifest does not activate Common Controls v6.' }
# Check the same executable's loader before cargo runs every library test.
$listing = @(& $executable --list 2>&1)
if ($LASTEXITCODE -ne 0) { throw "The manifested test executable cannot load: $($listing -join "`n")" }
Write-Host "Common Controls v6 embedded and test loader verified: $executable"
