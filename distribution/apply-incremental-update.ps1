param([Parameter(Mandatory=$true)][string]$AppRoot,[Parameter(Mandatory=$true)][string]$PythonRoot,[Parameter(Mandatory=$true)][string]$IndexPath,[Int64]$WindowHandle=0,[switch]$FunctionsOnly,[switch]$VerifyInstalledFiles)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression

function Get-UpdatePath([string]$Relative){
  if($Relative -notmatch '^(app|python)/[^:]+$' -or $Relative.Contains('\') -or $Relative -match '(^|/)(\.\.?|secrets|models|logs|backups)(/|$)'){throw 'Invalid packaged update path'}
  $parts=$Relative.Split('/',2)
  $root=if($parts[0] -eq 'app'){$AppRoot}else{$PythonRoot}
  $root=[IO.Path]::GetFullPath($root).TrimEnd('\')
  $target=[IO.Path]::GetFullPath([IO.Path]::Combine($root,$parts[1].Replace('/','\')))
  if(-not $target.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Update target escaped its root'}
  return $target
}
function Get-UpdateTarget([string]$Relative){
  $target=Get-UpdatePath $Relative
  $parts=$Relative.Split('/',2)
  $root=[IO.Path]::GetFullPath($(if($parts[0] -eq 'app'){$AppRoot}else{$PythonRoot})).TrimEnd('\')
  $cursor=$target
  while($cursor -and $cursor.Length -ge $root.Length){
    if((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Update target contains a junction or symbolic link'}
    $cursor=[IO.Path]::GetDirectoryName($cursor)
  }
  return $target
}
function Get-UpdateHash([string]$Path){
  $stream=[IO.File]::OpenRead($Path);$hash=[Security.Cryptography.SHA256]::Create()
  try{return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$hash.Dispose()}
}
function Test-UpdateFile($Entry,[string]$Path){
  return (Test-Path -LiteralPath $Path -PathType Leaf) -and (Get-Item -LiteralPath $Path).Length -eq $Entry.bytes -and (Get-UpdateHash $Path) -eq $Entry.sha256
}
function Write-UpdateProgress([string]$Message,[int]$Percent){
  Write-Host $Message
  if($WindowHandle -and ('OpenCoreUpdateWindows' -as [type])){
    [OpenCoreUpdateWindows]::Report([IntPtr]$WindowHandle,$Message,[Math]::Min(100,[Math]::Max(0,$Percent)))
  }
}
function Get-InstalledUpdateEntries {
  try {
    $receipt=Get-UpdateTarget 'app/installed-file-manifest.json'
    if(-not(Test-Path -LiteralPath $receipt -PathType Leaf) -or (Get-Item -LiteralPath $receipt).Length -gt 32MB){return $null}
    $previous=[IO.File]::ReadAllText($receipt) | ConvertFrom-Json
    if($previous.schema -ne 1 -or $previous.version -notmatch '^\d+\.\d+\.\d+$' -or $previous.commit -notmatch '^[a-f0-9]{40}$'){return $null}
    $entries=New-Object 'Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)
    foreach($entry in $previous.files){
      $null=Get-UpdatePath $entry.path
      if($entry.sha256 -notmatch '^[a-f0-9]{64}$' -or $null -eq $entry.bytes -or $entry.bytes -lt 0 -or $entries.ContainsKey($entry.path)){return $null}
      $entries.Add($entry.path,$entry)
    }
    if(-not $entries.ContainsKey('app/OpenCore.exe') -or -not $entries.ContainsKey('app/build-info.json')){return $null}
    # The install ledger records each packaged file independently. A local EXE
    # repair or changed build-info must not discard every unrelated runtime pin.
    # Compare ledgers first; only affected files are read below. A full content
    # audit remains available through the explicit VerifyInstalledFiles switch.
    return ,$entries
  }catch{return $null}
}
function New-UpdatePlan($Index,[string]$Stage,[switch]$VerifyInstalledFiles){
  $previous=if(-not $VerifyInstalledFiles){Get-InstalledUpdateEntries}else{$null}
  $mode=if($null -ne $previous){'release-difference'}else{'full-verification'}
  $changes=New-Object Collections.Generic.List[object]
  $seen=New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
  $compared=0;$skipped=0;$inspected=0
  if($mode -eq 'release-difference'){Write-UpdateProgress 'Comparing releases; unchanged files will be skipped.' 2}
  else{Write-UpdateProgress 'Verifying the installation to establish its file record.' 2}
  foreach($entry in $Index.files){
    if($entry.sha256 -notmatch '^[a-f0-9]{64}$' -or $null -eq $entry.bytes -or $entry.bytes -lt 0 -or $entry.offset -lt 0 -or $entry.compressedBytes -lt 1 -or -not $seen.Add($entry.path)){throw 'Invalid or duplicate update entry'}
    $null=Get-UpdatePath $entry.path
    $compared++
    if($null -ne $previous -and $previous.ContainsKey($entry.path) -and $previous[$entry.path].sha256 -eq $entry.sha256 -and $previous[$entry.path].bytes -eq $entry.bytes){
      $skipped++
      continue
    }
    $target=Get-UpdateTarget $entry.path
    $inspected++
    if(-not(Test-UpdateFile $entry $target)){$changes.Add(@{entry=$entry;staged=(Join-Path $Stage ('file-'+$changes.Count))})}
    if($inspected%20 -eq 0){
      $label=if($mode -eq 'release-difference'){'Checking '+$inspected+' changed files'}else{'Building the installation record: '+$inspected+' / '+$Index.files.Count+' files'}
      Write-UpdateProgress $label ([int](15*$compared/[Math]::Max(1,$Index.files.Count)))
    }
  }
  return @{changes=$changes;mode=$mode;unchangedFilesSkipped=$skipped;filesInspected=$inspected;filesCompared=$compared}
}
function Save-InstalledUpdateManifest($Index,[string]$Stage){
  $target=Get-UpdateTarget 'app/installed-file-manifest.json'
  $pending=Join-Path $Stage 'installed-file-manifest.json'
  [IO.File]::WriteAllText($pending,($Index | ConvertTo-Json -Depth 8 -Compress),(New-Object Text.UTF8Encoding($false)))
  if(Test-Path -LiteralPath $target){[IO.File]::Replace($pending,$target,(Join-Path $Stage 'previous-file-manifest.json'))}else{[IO.File]::Move($pending,$target)}
}
function Receive-UpdateFile($Entry,[string]$Url,[string]$Destination,[scriptblock]$OnProgress){
  $request=[Net.HttpWebRequest]::Create($Url)
  $request.UserAgent='OpenCore Incremental Update'
  $request.Timeout=60000;$request.ReadWriteTimeout=60000
  $request.AddRange([Int64]$Entry.offset,[Int64]($Entry.offset+$Entry.compressedBytes-1))
  $response=$request.GetResponse()
  try{
    $range='bytes '+$Entry.offset+'-'+($Entry.offset+$Entry.compressedBytes-1)+'/'
    if([int]$response.StatusCode -ne 206 -or -not $response.Headers['Content-Range'].StartsWith($range) -or $response.ContentLength -ne $Entry.compressedBytes){throw 'The download server did not honor the changed-file byte range'}
    $compressed=$Destination+'.gz'
    $source=$response.GetResponseStream();$output=[IO.File]::Create($compressed)
    try{
      $buffer=New-Object byte[] 262144;$received=0L
      while(($read=$source.Read($buffer,0,$buffer.Length)) -gt 0){
        $received+=$read
        if($received -gt $Entry.compressedBytes){throw 'The update file exceeded its declared size'}
        $output.Write($buffer,0,$read)
        & $OnProgress $received
      }
      if($received -ne $Entry.compressedBytes){throw 'The changed-file download was incomplete'}
    }finally{$output.Dispose();$source.Dispose()}
    $input=[IO.File]::OpenRead($compressed);$gzip=New-Object IO.Compression.GZipStream($input,[IO.Compression.CompressionMode]::Decompress)
    $output=[IO.File]::Create($Destination)
    try{
      $written=0L
      while(($read=$gzip.Read($buffer,0,$buffer.Length)) -gt 0){
        $written+=$read;if($written -gt $Entry.bytes){throw 'Decompressed update exceeded its declared size'}
        $output.Write($buffer,0,$read)
      }
    }finally{$output.Dispose();$gzip.Dispose();$input.Dispose()}
    if(-not(Test-UpdateFile $Entry $Destination)){throw 'Changed-file SHA-256 verification failed'}
    Remove-Item -LiteralPath $compressed
  }finally{$response.Dispose()}
}
function Apply-VerifiedUpdate($Plan,[string]$Stage){
  $replaced=New-Object Collections.Generic.List[object]
  try{
    foreach($item in $Plan){
      $target=Get-UpdateTarget $item.entry.path
      if(-not(Test-UpdateFile $item.entry $item.staged)){throw 'Staged update checksum changed'}
      $parent=[IO.Path]::GetDirectoryName($target)
      if(-not(Test-Path -LiteralPath $parent)){$null=New-Item -ItemType Directory -Path $parent -Force}
      $saved=Join-Path $Stage ('backup-'+$replaced.Count)
      $existed=Test-Path -LiteralPath $target
      if($existed){Move-Item -LiteralPath $target -Destination $saved}
      $replaced.Add(@{target=$target;backup=$saved;existed=$existed})
      Move-Item -LiteralPath $item.staged -Destination $target
      if(-not(Test-UpdateFile $item.entry $target)){throw 'Installed update checksum mismatch'}
    }
  }catch{
    for($i=$replaced.Count-1;$i -ge 0;$i--){
      $record=$replaced[$i]
      if(Test-Path -LiteralPath $record.target){Remove-Item -LiteralPath $record.target}
      if($record.existed){Move-Item -LiteralPath $record.backup -Destination $record.target}
    }
    throw
  }
}
if($FunctionsOnly){return}
try{
  $logRoot=Join-Path $AppRoot 'logs';$null=New-Item -ItemType Directory -Path $logRoot -Force
  Start-Transcript -LiteralPath (Join-Path $logRoot 'incremental-update.log') -Force | Out-Null
  if($WindowHandle){
    $systemAssembly=Join-Path ([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()) 'System.dll'
    Add-Type -ReferencedAssemblies $systemAssembly -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class OpenCoreUpdateWindows {
 [DllImport("user32.dll")]static extern IntPtr FindWindowEx(IntPtr p,IntPtr after,string cls,string title);
 [DllImport("user32.dll")]static extern IntPtr GetDlgItem(IntPtr p,int id);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern IntPtr SendMessage(IntPtr w,int m,IntPtr a,string b);
 [DllImport("user32.dll")]static extern IntPtr SendMessage(IntPtr w,int m,IntPtr a,IntPtr b);
 public static void Report(IntPtr parent,string text,int percent){var page=FindWindowEx(parent,IntPtr.Zero,"#32770",null);if(page==IntPtr.Zero)return;var bar=GetDlgItem(page,1004);var label=GetDlgItem(page,1006);SendMessage(bar,0x406,IntPtr.Zero,(IntPtr)100);SendMessage(bar,0x402,(IntPtr)percent,IntPtr.Zero);SendMessage(label,12,IntPtr.Zero,text);}
}
'@
  }
  $index=Get-Content -LiteralPath $IndexPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if($index.schema -ne 1 -or $index.version -notmatch '^\d+\.\d+\.\d+$' -or $index.payloadUrl -cne ('https://github.com/hertzitamar9-png/OpenCore/releases/download/app-v'+$index.version+'/OpenCore.Update.data')){throw 'Invalid trusted update index'}
  if(-not(Test-Path -LiteralPath (Join-Path $AppRoot 'OpenCore.exe'))){throw 'OpenCore is not installed. Run the permanent OpenCore Setup first.'}
  $stage=Join-Path $AppRoot ('.update-'+[Guid]::NewGuid().ToString('N'))
  $null=New-Item -ItemType Directory -Path $stage
  $planningTimer=[Diagnostics.Stopwatch]::StartNew()
  $plan=New-UpdatePlan $index $stage -VerifyInstalledFiles:$VerifyInstalledFiles
  $planningTimer.Stop()
  $changes=$plan.changes
  Write-UpdateProgress ('Update plan: '+$plan.mode+'; '+$plan.unchangedFilesSkipped+' unchanged files skipped; '+$plan.filesInspected+' changed or unrecorded files checked.') 15
  $total=[Int64](($changes | ForEach-Object {$_.entry.compressedBytes} | Measure-Object -Sum).Sum);$done=0L
  Write-UpdateProgress ('Downloading '+$changes.Count+' changed files; '+[Math]::Round($total/1MB,1)+' MB. Unchanged files will be kept.') 15
  foreach($item in $changes){
    $progressCallback={param($received) Write-UpdateProgress ('Downloading '+$item.entry.path+' - '+[Math]::Round(($done+$received)/1MB,1)+' / '+[Math]::Round($total/1MB,1)+' MB') ([int](15+65*($done+$received)/[Math]::Max(1,$total)))}
    Receive-UpdateFile $item.entry $index.payloadUrl $item.staged $progressCallback
    $done+=$item.entry.compressedBytes
  }
  Write-UpdateProgress 'Applying verified changed files; downloaded models and chats stay in place.' 85
  Apply-VerifiedUpdate $changes $stage
  Save-InstalledUpdateManifest $index $stage
  [IO.File]::WriteAllText((Join-Path $logRoot 'incremental-update-summary.json'),(@{version=$index.version;mode=$plan.mode;unchangedFilesSkipped=$plan.unchangedFilesSkipped;filesInspected=$plan.filesInspected;planningSeconds=[Math]::Round($planningTimer.Elapsed.TotalSeconds,3);downloadedFiles=$changes.Count;downloadedBytes=$total} | ConvertTo-Json -Compress))
  $resolvedStage=[IO.Path]::GetFullPath($stage)
  if(-not $resolvedStage.StartsWith([IO.Path]::GetFullPath($AppRoot).TrimEnd('\')+'\.update-',[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid update staging directory'}
  Remove-Item -LiteralPath $resolvedStage -Recurse
  Write-UpdateProgress ('OpenCore '+$index.version+' installed. Reopening OpenCore...') 100
  Stop-Transcript | Out-Null
  exit 0
}catch{
  Write-Error $_ -ErrorAction Continue
  try{Stop-Transcript | Out-Null}catch{}
  exit 1
}
