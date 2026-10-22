# First logon of the HID-Fi Companion test VM (companion/tests/windows/vm.sh):
# OpenSSH server with key-only login for the local admin, no sleep or screen
# lock (the app captures the desktop), and a marker when done. Runs from the
# setup disc, next to OpenSSH-ARM64-*.msi and authorized_keys.
$ErrorActionPreference = "Stop"
$disc = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Transcript -Path C:\hidfi-setup.log

# OpenSSH (Microsoft's Win32-OpenSSH build: no Windows Update needed).
$msi = Get-ChildItem $disc -Filter "OpenSSH-*.msi" | Select-Object -First 1
Start-Process msiexec.exe -Wait -ArgumentList "/i `"$($msi.FullName)`" /qn"
$keys = "C:\ProgramData\ssh\administrators_authorized_keys"
New-Item -ItemType Directory -Force C:\ProgramData\ssh | Out-Null
Copy-Item (Join-Path $disc "authorized_keys") $keys -Force
icacls $keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null
$cfg = "C:\ProgramData\ssh\sshd_config"
if (Test-Path $cfg) {
    (Get-Content $cfg) -replace '^#?PasswordAuthentication .*', 'PasswordAuthentication no' | Set-Content $cfg
}
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null
Set-Service sshd -StartupType Automatic
Restart-Service sshd
New-NetFirewallRule -DisplayName "OpenSSH (HID-Fi VM)" -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -ErrorAction SilentlyContinue | Out-Null

# Never sleep, never lock: the app is tested against a live desktop.
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization" -Force | Out-Null
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization" -Name NoLockScreen -Value 1
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name ScreenSaveActive -Value 0

"done $(Get-Date -Format o)" | Set-Content C:\hidfi-setup-done.txt
Stop-Transcript
