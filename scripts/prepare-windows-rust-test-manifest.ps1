# Cargo target runner: activate Common Controls v6 after linking and immediately
# before running Cargo's selected library test executable. Never invoke Cargo here.
param(
  [Parameter(Position = 0)]
  [string]$TestExecutable,
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
  [AllowEmptyCollection()]
  [AllowEmptyString()]
  [string[]]$TestArguments = @()
)

$ErrorActionPreference = 'Stop'
# An absent original RT_MANIFEST is expected; inspect native exit codes below.
$PSNativeCommandUseErrorActionPreference = $false
if ($env:GITHUB_ACTIONS -ne 'true' -or [string]::IsNullOrEmpty($env:RUNNER_TEMP) -or
    [string]::IsNullOrEmpty($env:GITHUB_WORKSPACE)) {
  throw 'The Windows native test runner runs only in GitHub Actions.'
}
if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw 'Use pwsh (PowerShell 7) so every Cargo test argument is forwarded exactly.'
}

function Resolve-NativeTestExecutable {
  param([string]$Executable, [string]$Workspace)
  if ([string]::IsNullOrWhiteSpace($Executable)) {
    throw 'Cargo must supply the library test executable as the first runner argument.'
  }
  $checkout = (Resolve-Path -LiteralPath $Workspace -ErrorAction Stop).ProviderPath
  $target = [System.IO.Path]::GetFullPath((Join-Path $checkout 'src-tauri/target')).TrimEnd('\', '/')
  $item = Get-Item -LiteralPath $Executable -ErrorAction Stop
  if ($item -isnot [System.IO.FileInfo]) { throw 'Cargo selected an executable that is not a regular file.' }
  $path = [System.IO.Path]::GetFullPath($item.FullName)
  $prefix = $target + [System.IO.Path]::DirectorySeparatorChar
  if (-not $path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Cargo selected a test executable outside this Actions checkout target directory.'
  }
  $relative = $path.Substring($prefix.Length).Replace('\', '/')
  if ($relative -cnotmatch '^(?:x86_64-pc-windows-msvc/)?(?:debug|release)/deps/opencore_control_center_lib-[0-9a-f]{16}\.exe$') {
    throw 'Cargo must select the hashed OpenCore library test executable in target deps.'
  }
  # Do not follow a link into a different artifact or checkout.
  for ($entry = $item; $null -ne $entry; $entry = if ($entry -is [System.IO.FileInfo]) { $entry.Directory } else { $entry.Parent }) {
    if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw 'The Cargo test artifact path contains a filesystem link.'
    }
    if ($entry.FullName.TrimEnd('\', '/') -ieq $checkout.TrimEnd('\', '/')) { break }
  }
  $reader = [System.IO.BinaryReader]::new([System.IO.File]::OpenRead($path))
  try {
    if ($reader.BaseStream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5a4d) {
      throw 'Cargo selected an invalid Windows test executable.'
    }
    [void]$reader.BaseStream.Seek(0x3c, [System.IO.SeekOrigin]::Begin)
    $peOffset = $reader.ReadInt32()
    if ($peOffset -lt 64 -or $peOffset -gt ($reader.BaseStream.Length - 6)) {
      throw 'The Cargo test executable has an invalid PE header offset.'
    }
    [void]$reader.BaseStream.Seek($peOffset, [System.IO.SeekOrigin]::Begin)
    if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
      throw 'The Cargo test runner requires the x86_64 Windows executable.'
    }
  } finally {
    $reader.Dispose()
  }
  return $path
}

function New-NativeTestProcessStartInfo {
  param([string]$Executable, [AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments, [string]$WorkingDirectory)
  $info = [System.Diagnostics.ProcessStartInfo]::new()
  $info.FileName = $Executable
  $info.WorkingDirectory = $WorkingDirectory
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  # ArgumentList handles spaces, embedded quotes and empty arguments without
  # rebuilding a command line or applying PowerShell's native argument rules.
  foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
  return $info
}

$executable = Resolve-NativeTestExecutable $TestExecutable $env:GITHUB_WORKSPACE
$manifestTool = (Get-Command mt.exe -ErrorAction Stop).Source
$output = Join-Path $env:RUNNER_TEMP ('opencore-rust-test-manifest-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output | Out-Null
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
Write-Host "Common Controls v6 embedded for Cargo-selected native tests: $executable"
# This is Cargo's actual invocation, including filters, ignored-test switches and
# all future libtest arguments. Output is inherited and the exact exit code is
# returned. No Cargo command can relink between manifest activation and execution.
$test = [System.Diagnostics.Process]::new()
$test.StartInfo = New-NativeTestProcessStartInfo $executable $TestArguments (Get-Location).ProviderPath
try {
  if (-not $test.Start()) { throw 'Could not start the Cargo-selected native library tests.' }
  $test.WaitForExit()
  $testExitCode = $test.ExitCode
} finally {
  $test.Dispose()
}
exit $testExitCode
