# HID-Fi Companion for Windows: install, update or remove, for this user.
#
#   irm https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-windows.ps1 | iex
#   .\install-windows.ps1 -Uninstall
#   .\install-windows.ps1 -From .\hidfi-companion.exe      (a build of your own)
#   .\install-windows.ps1 -Yes      (no question: for scripts; a person is asked first)
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
#
# Before it changes anything it says who publishes the app, where it comes from and
# what it will do, and asks. It also lists the app in Settings > Apps, with HID-Fi's
# icon and publisher and an Uninstall button that works offline.
[CmdletBinding()]
param([switch]$Uninstall, [string]$From, [switch]$Yes)

$ErrorActionPreference = 'Stop'
$Repo = 'san-gitlogin/HID-Fi'
$Dir = Join-Path $env:LOCALAPPDATA 'Programs\HID-Fi Companion'
$Exe = Join-Path $Dir 'hidfi-companion.exe'
$Lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'HID-Fi Companion.lnk'
$Rule = 'HID-Fi Companion'
$AppKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\HID-Fi Companion'
$Publisher = 'HID-Fi'
$Site = "https://github.com/$Repo"

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
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppKey
    [void](Set-FirewallRule $false)
    Write-Host 'Removed HID-Fi Companion.'
    Write-Host "Its settings and pairing are in $env:APPDATA\HID-Fi Companion - delete that folder to forget the pairing,"
    Write-Host "and remove this computer on the dashboard's Screen tab."
    return
}

if (-not [Environment]::Is64BitOperatingSystem) { throw 'HID-Fi Companion needs 64-bit Windows.' }

# Consent first: nothing is written before a person says yes (or -Yes, for scripts).
$what = if ($From) { "your own build: $From" } else { "the latest release from $Site/releases (its SHA-256 checksum is checked)" }
Write-Host ''
Write-Host 'HID-Fi Companion' -ForegroundColor Cyan
Write-Host "  Publisher: $Publisher   ($Site)"
Write-Host "  Installs:  $what"
Write-Host ''
Write-Host '  It will:'
Write-Host "   - put the app in $Dir, for you only (no administrator needed for this)"
Write-Host '   - add it to the Start menu, and to Settings > Apps to remove it later'
Write-Host '   - start it now and when you sign in (its tray menu turns that off)'
Write-Host '   - ask Windows once for administrator approval for one firewall rule, so viewers'
Write-Host '     on your Private (home) network can connect - you may say no'
Write-Host ''
Write-Host '  It shows this computer''s screen only on a dashboard you pair, after you tap Pair.'
Write-Host '  License: PolyForm Noncommercial 1.0.0 - free for personal, non-commercial use:'
Write-Host "           $Site/blob/main/LICENSE.md"
Write-Host ''
if (-not $Yes) {
    if (-not [Environment]::UserInteractive) { throw 'No one to ask: run it again with -Yes to install without the question.' }
    $answer = Read-Host 'Install HID-Fi Companion? [Y/n]'
    if ($null -eq $answer -or ($answer.Trim() -ne '' -and $answer.Trim() -notmatch '^(y|yes)$')) {
        Write-Host 'Not installed. Nothing was changed.'
        return
    }
}
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

# Settings > Apps (and Control Panel's Programs): HID-Fi's icon, publisher and
# version from the .exe itself, and an Uninstall button. That button runs a copy of
# the uninstall steps kept beside the app, so removing it needs no download.
$Unins = Join-Path $Dir 'uninstall.ps1'
@"
# Removes HID-Fi Companion for this user (Settings > Apps > Uninstall runs this).
`$ErrorActionPreference = 'Continue'
Get-Process -Name 'hidfi-companion' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800
Remove-Item -Force -ErrorAction SilentlyContinue '$Lnk'
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'HID-Fi Companion' -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '$AppKey'
try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', 'netsh advfirewall firewall delete rule name="$Rule"' } catch {}
Set-Location `$env:TEMP
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '$Dir'
"@ | Set-Content -Encoding UTF8 -LiteralPath $Unins
$info = (Get-Item -LiteralPath $Exe).VersionInfo
New-Item -Force -Path $AppKey | Out-Null
$props = @{
    DisplayName = 'HID-Fi Companion'; DisplayIcon = "$Exe,0"; Publisher = $Publisher
    DisplayVersion = "$($info.ProductVersion)"; InstallLocation = $Dir
    URLInfoAbout = $Site; HelpLink = "$Site/blob/main/docs/COMPANION.md"
    Comments = "Shows this computer's screen on the HID-Fi dashboard"
    UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Unins`""
}
foreach ($k in $props.Keys) { Set-ItemProperty -Path $AppKey -Name $k -Value $props[$k] }
foreach ($k in 'NoModify', 'NoRepair') { Set-ItemProperty -Path $AppKey -Name $k -Value 1 -Type DWord }
Set-ItemProperty -Path $AppKey -Name 'EstimatedSize' -Type DWord -Value ([int]((Get-Item -LiteralPath $Exe).Length / 1KB))

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
