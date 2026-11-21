# One-time setup so JARVIS can create restore points without running elevated.
# Run this YOURSELF in an elevated PowerShell (Run as administrator). JARVIS must not run it.
#
# What it changes (read before running):
#   1. Turns on System Protection (restore points) for the system drive.
#   2. Sets SystemRestorePointCreationFrequency to 0 in HKLM, so Windows allows more than one
#      restore point per 24 hours. Without this, Windows silently skips all but the first.
#   3. Creates C:\ProgramData\Jarvis with a fixed restore_point.ps1, locked so a non-elevated
#      process can read but not modify it.
#   4. Registers scheduled task "JarvisRestorePoint" that runs that script elevated under YOUR account.
#
# Security note: this lets a non-elevated process on your account trigger an elevated restore point
# without a UAC prompt. The script is fixed and ignores all input, so the only thing it can do is
# create a restore point. To remove: Unregister-ScheduledTask JarvisRestorePoint -Confirm:$false
#
# Works when your account is an administrator (UAC split token). A pure standard-user account
# cannot elevate this way.

#Requires -RunAsAdministrator
param([string]$UserName = "$env:USERDOMAIN\$env:USERNAME")

$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:ProgramData 'Jarvis'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$script = @'
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:ProgramData 'Jarvis'
$out = Join-Path $dir 'restore_result.json'
$ok = $false
$err = ''
try {
    Checkpoint-Computer -Description ("JARVIS pre-change " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -RestorePointType MODIFY_SETTINGS
    $ok = $true
} catch {
    $err = $_.Exception.Message
}
@{ ok = $ok; error = $err; finished_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } |
    ConvertTo-Json | Set-Content -Path $out -Encoding UTF8
'@
Set-Content -Path (Join-Path $dir 'restore_point.ps1') -Value $script -Encoding UTF8

Enable-ComputerRestore -Drive "$($env:SystemDrive)\"
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore' `
    -Name 'SystemRestorePointCreationFrequency' -Value 0 -PropertyType DWord -Force | Out-Null

# Lock the folder: SYSTEM and Administrators full control; your account read/execute only.
icacls $dir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "${UserName}:(OI)(CI)RX" | Out-Null

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\restore_point.ps1`""
$principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName 'JarvisRestorePoint' -Action $action -Principal $principal -Force | Out-Null

Write-Host "Done. Test it from a NORMAL (non-admin) PowerShell:  schtasks /run /tn JarvisRestorePoint"
Write-Host "Then check: Get-Content $dir\restore_result.json   (ok should be true)"
