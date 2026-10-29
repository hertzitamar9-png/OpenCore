param([Parameter(Mandatory=$true)][string]$AppRoot,[Parameter(Mandatory=$true)][string]$PythonRoot,[Parameter(Mandatory=$true)][string]$IndexPath,[Int64]$WindowHandle=0,[switch]$FunctionsOnly)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression

function Get-UpdateTarget([string]$Relative){
  if($Relative -notmatch '^(app|python)/[^:]+$' -or $Relative.Contains('\') -or $Relative -match '(^|/)(\.\.?|secrets|models|logs|backups)(/|$)'){throw 'Invalid packaged update path'}
  $parts=$Relative.Split('/',2)
  $root=if($parts[0] -eq 'app'){$AppRoot}else{$PythonRoot}
  $root=[IO.Path]::GetFullPath($root).TrimEnd('\')
  $target=[IO.Path]::GetFullPath((Join-Path $root $parts[1]))
  if(-not $target.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Update target escaped its root'}
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
  Write-Output $Message
  if($WindowHandle -and ('OpenCoreUpdateWindows' -as [type])){
    [OpenCoreUpdateWindows]::Report([IntPtr]$WindowHandle,$Message,[Math]::Min(100,[Math]::Max(0,$Percent)))
  }
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
  $changes=New-Object Collections.Generic.List[object];$seen=New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
  $checked=0
  foreach($entry in $index.files){
    if($entry.sha256 -notmatch '^[a-f0-9]{64}$' -or $entry.bytes -lt 0 -or $entry.offset -lt 0 -or $entry.compressedBytes -lt 1 -or -not $seen.Add($entry.path)){throw 'Invalid or duplicate update entry'}
    $target=Get-UpdateTarget $entry.path
    $checked++
    if(-not(Test-UpdateFile $entry $target)){$changes.Add(@{entry=$entry;staged=(Join-Path $stage ('file-'+$changes.Count))})}
    if($checked%20 -eq 0){Write-UpdateProgress ('Checking installed files '+$checked+' of '+$index.files.Count) ([int](15*$checked/$index.files.Count))}
  }
  $total=[Int64](($changes | ForEach-Object {$_.entry.compressedBytes} | Measure-Object -Sum).Sum);$done=0L
  Write-UpdateProgress ('Downloading '+$changes.Count+' changed files; '+[Math]::Round($total/1MB,1)+' MB. Unchanged files will be kept.') 15
  foreach($item in $changes){
    $progressCallback={param($received) Write-UpdateProgress ('Downloading '+$item.entry.path+' - '+[Math]::Round(($done+$received)/1MB,1)+' / '+[Math]::Round($total/1MB,1)+' MB') ([int](15+65*($done+$received)/[Math]::Max(1,$total)))}
    Receive-UpdateFile $item.entry $index.payloadUrl $item.staged $progressCallback
    $done+=$item.entry.compressedBytes
  }
  Write-UpdateProgress 'Applying verified changed files; downloaded models and chats stay in place.' 85
  Apply-VerifiedUpdate $changes $stage
  $index | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath (Join-Path $AppRoot 'installed-file-manifest.json') -Encoding UTF8
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
