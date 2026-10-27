param([Parameter(Mandatory=$true)][string]$AppPath, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($AppPath)
if ([IO.Path]::GetFileName($target) -ine 'OpenCore.exe') { throw 'Invalid OpenCore update target' }
if ($CheckOnly) {
    $running = @(Get-CimInstance Win32_Process -Filter "Name='OpenCore.exe'" | Where-Object {
        $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target
    })
    if ($running.Count) { exit 5 }
    exit 0
}
$quietSince = $null
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while ([DateTime]::UtcNow -lt $deadline) {
    $owned = @(Get-CimInstance Win32_Process -Filter "Name='OpenCore.exe'" | Where-Object {
        $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target
    })
    if ($owned.Count) {
        $quietSince = $null
        foreach ($item in $owned) {
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process -and $process.Path -and [IO.Path]::GetFullPath($process.Path) -ieq $target) {
                try { Stop-Process -InputObject $process -Force -ErrorAction Stop }
                catch { if (Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue) { throw } }
                $process.WaitForExit(10000) | Out-Null
                if (!$process.HasExited) { throw 'OpenCore did not stop for the update' }
            }
        }
    } else {
        if ($null -eq $quietSince) { $quietSince = [DateTime]::UtcNow }
        if (([DateTime]::UtcNow - $quietSince).TotalSeconds -ge 3) { exit 0 }
    }
    Start-Sleep -Milliseconds 300
}
throw 'OpenCore kept reopening while the update was starting'
