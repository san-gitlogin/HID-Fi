# HID-Fi Companion for Windows: install, update or remove, for this user.
#
#   irm https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-windows.ps1 | iex
#   .\install-windows.ps1 -Uninstall
#   .\install-windows.ps1 -From .\hidfi-companion.exe      (a build of your own)
#
# Updating is running it again: it closes the running app, replaces it and
# starts the new one - no need to quit from the tray first. Starting a newer
# hidfi-companion.exe by hand also works: it asks the running one to quit and
# takes over (handover.rs). The installed version is in the tray menu, at the
# top of the app's window, and from "hidfi-companion.exe --version" with its
# output redirected (it has no console): Start-Process ... -RedirectStandardOutput.
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

# An update replaces the running app: stop it, and wait until Windows has let
# go of its program file - Stop-Process returns at once, and a copy started
# while the file was still locked failed with "in use" (seen live: people had
# to quit the app from the tray first).
$running = Get-Process -Name 'hidfi-companion' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host 'Closing the running HID-Fi Companion...'
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    $running | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue
}

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
    # The file can stay locked a moment after the process is gone: try again.
    for ($i = 1; ; $i++) {
        try { Copy-Item -Force -LiteralPath $new -Destination $Exe; break }
        catch {
            if ($i -ge 20) { throw "Could not replace $Exe - is HID-Fi Companion still running? Quit it from the tray and run this again." }
            Start-Sleep -Milliseconds 500
        }
    }
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
}

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($Lnk)
$s.TargetPath = $Exe
$s.WorkingDirectory = $Dir
$s.Description = "Shows this computer's screen on the HID-Fi dashboard"
$s.Save()

# The version, from the app itself. A tray app has no console, so its output is
# only seen through a redirected file (seen: '& $Exe --version' printed nothing).
$ver = ''
$vf = Join-Path ([IO.Path]::GetTempPath()) ("hidfi-ver-" + [guid]::NewGuid() + ".txt")
try {
    # A build from before --version starts the whole app instead of answering:
    # give it 5 s, then stop it (seen: an older -From build hung the installer).
    $vp = Start-Process -FilePath $Exe -ArgumentList '--version' -NoNewWindow -PassThru -RedirectStandardOutput $vf
    if (-not $vp.WaitForExit(5000)) { $vp.Kill(); $vp.WaitForExit(5000) | Out-Null }
    else { $ver = (Get-Content -Raw $vf).Trim() }
} catch {} finally { Remove-Item -Force -ErrorAction SilentlyContinue $vf }
if (-not $ver) { $ver = 'HID-Fi Companion' }
Write-Host ''
Write-Host "Installed $ver at $Exe"
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
