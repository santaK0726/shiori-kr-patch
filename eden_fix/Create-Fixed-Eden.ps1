param([Parameter(Mandatory=$true)][string]$SourceExe)
$ErrorActionPreference = 'Stop'
function Get-TaskSha([string]$TaskFile) {
    $taskStream = [IO.File]::OpenRead($TaskFile)
    $taskSha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($taskSha.ComputeHash($taskStream)).Replace('-','').ToLowerInvariant() }
    finally { $taskSha.Dispose(); $taskStream.Dispose() }
}
$taskReport = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'eden_fix.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskSource = (Resolve-Path -LiteralPath $SourceExe).Path
if ((Get-TaskSha $taskSource) -ne $taskReport.source_sha256) { throw 'Unsupported Eden executable; nothing was changed.' }
$taskOutput = Join-Path ([IO.Path]::GetDirectoryName($taskSource)) 'eden-krfs-fixed.exe'
if (Test-Path -LiteralPath $taskOutput) {
    if ((Get-TaskSha $taskOutput) -eq $taskReport.output_sha256) { Write-Output $taskOutput; exit 0 }
    throw 'A different output already exists; it was not overwritten.'
}
$taskBytes = [IO.File]::ReadAllBytes($taskSource)
foreach ($taskWrite in $taskReport.writes) {
    if ($taskBytes[$taskWrite.file_offset] -ne $taskWrite.source_byte) { throw 'Expected source byte mismatch.' }
    $taskBytes[$taskWrite.file_offset] = [byte]$taskWrite.target_byte
}
$taskHasher = [Security.Cryptography.SHA256]::Create()
try { $taskDigest = [BitConverter]::ToString($taskHasher.ComputeHash($taskBytes)).Replace('-','').ToLowerInvariant() } finally { $taskHasher.Dispose() }
if ($taskDigest -ne $taskReport.output_sha256) { throw 'Output verification failed; no output was written.' }
[IO.File]::WriteAllBytes($taskOutput,$taskBytes)
if ((Get-TaskSha $taskSource) -ne $taskReport.source_sha256) { throw 'Original changed unexpectedly.' }
if ((Get-TaskSha $taskOutput) -ne $taskReport.output_sha256) { throw 'Output readback failed.' }
Write-Output $taskOutput
