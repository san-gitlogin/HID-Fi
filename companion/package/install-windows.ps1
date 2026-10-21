# HID-Fi Companion for Windows: install, update or remove, for this user.
#
#   irm https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-windows.ps1 | iex
#   .\install-windows.ps1 -Uninstall
#   .\install-windows.ps1 -From .\hidfi-companion.exe      (a build of your own)
#
# Puts the app in %LOCALAPPDATA%\Programs\HID-Fi Companion, adds a Start menu
# shortcut, and starts it (the app adds its own start-at-login entry, which its
# tray menu can turn off). Viewers connect to this computer, so Windows
# Firewall needs one rule: Windows asks for administrator approval once, for
# that rule only, for Private networks only.
[CmdletBinding()]
param([switch]$Uninstall, [string]$From)

$ErrorActionPreference = 'Stop'
$Repo = 'san-gitlogin/HID-Fi'
$Dir = Join-Path $env:LOCALAPPDATA 'Programs\HID-Fi Companion'
$Exe = Join-Path $Dir 'hidfi-companion.exe'
$Lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'HID-Fi Companion.lnk'
$Rule = 'HID-Fi Companion'

function Set-FirewallRule([bool]$Add) {
    $cmd = if ($Add) {
        "netsh advfirewall firewall delete rule name=`"$Rule`" | Out-Null; " +
        "netsh advfirewall firewall add rule name=`"$Rule`" dir=in action=allow program=`"$Exe`" profile=private enable=yes"
    } else {
        "netsh advfirewall firewall delete rule name=`"$Rule`""
    }
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $cmd
        return $true
    } catch {
        return $false   # the user said no to the administrator prompt
    }
}

Get-Process -Name 'hidfi-companion' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

if ($Uninstall) {
    Remove-Item -Force -ErrorAction SilentlyContinue $Lnk
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'HID-Fi Companion' -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Dir
    [void](Set-FirewallRule $false)
    Write-Host 'Removed HID-Fi Companion.'
    Write-Host "Its settings and pairing are in $env:APPDATA\HID-Fi Companion - delete that folder to forget the pairing,"
    Write-Host "and remove this computer on the dashboard's Screen tab."
    return
}

if (-not [Environment]::Is64BitOperatingSystem) { throw 'HID-Fi Companion needs 64-bit Windows.' }
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("hidfi-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $new = Join-Path $tmp 'hidfi-companion.exe'
    if ($From) {
        Copy-Item -LiteralPath $From -Destination $new
    } else {
        $asset = 'hidfi-companion-windows-x86_64.exe'
        Write-Host 'Downloading HID-Fi Companion...'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repo/releases/latest/download/$asset" -OutFile $new
        $sums = Join-Path $tmp 'SHA256SUMS'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repo/releases/latest/download/SHA256SUMS" -OutFile $sums
        $want = (Get-Content $sums | Where-Object { $_ -match " $asset$" } | ForEach-Object { ($_ -split ' ')[0] })
        $got = (Get-FileHash -Algorithm SHA256 $new).Hash.ToLower()
        if (-not $want -or $want -ne $got) { throw 'The download does not match the release checksum - not installed.' }
    }
    Copy-Item -Force -LiteralPath $new -Destination $Exe
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
}

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($Lnk)
$s.TargetPath = $Exe
$s.WorkingDirectory = $Dir
$s.Description = "Shows this computer's screen on the HID-Fi dashboard"
$s.Save()

Write-Host ''
Write-Host "Installed: $Exe"
Write-Host 'Windows will ask for administrator approval once, to let viewers on your home network connect.'
if (-not (Set-FirewallRule $true)) {
    Write-Host 'Skipped. Windows will ask again the first time a viewer connects.' -ForegroundColor Yellow
}
$cat = (Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select-Object -First 1).NetworkCategory
if ($cat -and "$cat" -ne 'Private' -and "$cat" -ne 'DomainAuthenticated') {
    Write-Host "NOTE: this network is set to '$cat'. Viewers can connect on Private networks:" -ForegroundColor Yellow
    Write-Host '      Settings > Network & internet > your WiFi > Network profile type > Private.' -ForegroundColor Yellow
}
$wv = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
if (-not (Test-Path $wv)) {
    Write-Host 'NOTE: Microsoft Edge WebView2 is not installed; the pairing code will show in the tray icon''s tooltip instead of a window.'
}
Start-Process -FilePath $Exe
Write-Host 'Started. Look for the HID-Fi icon by the clock, then open the dashboard''s Screen tab and tap Pair.'
