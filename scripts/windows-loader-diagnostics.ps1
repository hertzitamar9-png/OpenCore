param(
  [string]$BinaryDirectory = 'src-tauri/target/debug/deps',
  [string]$OutputDirectory = 'artifacts/windows-loader'
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class OpenCoreLoaderProbe {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern IntPtr LoadLibraryExW(string path, IntPtr file, uint flags);
  [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true, SetLastError=true)]
  public static extern IntPtr GetProcAddress(IntPtr module, string name);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern uint GetModuleFileNameW(IntPtr module, StringBuilder path, uint length);
  [DllImport("kernel32.dll")]
  public static extern bool FreeLibrary(IntPtr module);
}
'@
$diagnostics = @()
$executables = @(Get-ChildItem -LiteralPath $BinaryDirectory -Filter 'opencore_control_center_lib-*.exe' -File)
if ($executables.Count -eq 0) { throw 'No compiled OpenCore test executable was found.' }
foreach ($executable in $executables) {
  $imports = @(& dumpbin /imports $executable.FullName)
  if ($LASTEXITCODE -ne 0) { throw "dumpbin failed for $($executable.Name)" }
  $imports | Set-Content -LiteralPath (Join-Path $OutputDirectory "$($executable.Name).imports.txt")
  $moduleName = $null
  $module = [IntPtr]::Zero
  foreach ($line in $imports) {
    if ($line -match '^\s+([A-Za-z0-9_.-]+\.dll)\s*$') {
      if ($module -ne [IntPtr]::Zero) { [void][OpenCoreLoaderProbe]::FreeLibrary($module) }
      $moduleName = $Matches[1]
      $localPath = Join-Path $executable.DirectoryName $moduleName
      $loadPath = if (Test-Path -LiteralPath $localPath -PathType Leaf) { $localPath } else { $moduleName }
      # Map exports without running a DLL entry point. This never runs the test app.
      $module = [OpenCoreLoaderProbe]::LoadLibraryExW($loadPath, [IntPtr]::Zero, 1)
      $errorCode = if ($module -eq [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::GetLastWin32Error() } else { 0 }
      $resolved = New-Object Text.StringBuilder 32768
      if ($module -ne [IntPtr]::Zero) { [void][OpenCoreLoaderProbe]::GetModuleFileNameW($module, $resolved, 32768) }
      $diagnostics += [pscustomobject]@{ binary=$executable.Name; module=$moduleName; path=$resolved.ToString(); loadError=$errorCode }
    } elseif ($moduleName -and $module -ne [IntPtr]::Zero -and $line -match '^\s+[0-9A-Fa-f]+\s+([A-Za-z_?][^\s]+)\s*$') {
      $symbol = $Matches[1]
      if ([OpenCoreLoaderProbe]::GetProcAddress($module, $symbol) -eq [IntPtr]::Zero) {
        $diagnostics += [pscustomobject]@{ binary=$executable.Name; module=$moduleName; missingEntryPoint=$symbol }
      }
    }
  }
  if ($module -ne [IntPtr]::Zero) { [void][OpenCoreLoaderProbe]::FreeLibrary($module) }
}
$diagnostics | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'loader-probe.json')
$diagnostics | ConvertTo-Json -Depth 4
