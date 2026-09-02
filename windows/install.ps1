# Install "Document to Markdown" on Windows and put it on the Desktop.
#
# From anywhere, in one line, in PowerShell:
#   irm https://raw.githubusercontent.com/charles-martech/markdown-anything/main/windows/install.ps1 | iex
# From a copy of this repository:
#   powershell -ExecutionPolicy Bypass -File windows\install.ps1
#
# It installs the newest release into %LOCALAPPDATA%\Document to Markdown,
# adds a Desktop and a Start Menu shortcut, and opens the app. It never asks
# for administrator rights and installs nothing system-wide: deleting that one
# folder and the two shortcuts removes every trace.
#
# Python 3.9 or newer has to be installed already (from python.org or the
# Microsoft Store). The app finds it; nothing else is needed, and the app
# fetches Pandoc and its readers into its own folder when first opened.
#
# $env:MDA_REF = "main" installs a branch instead of the newest release.
# $env:MDA_NO_OPEN = "1" installs without opening the app afterwards.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Repo = "charles-martech/markdown-anything"
$AppName = "Document to Markdown"
$Support = if ($env:DOC2MD_HOME) { $env:DOC2MD_HOME } else { Join-Path $env:LOCALAPPDATA $AppName }
$Bundle = Join-Path $Support "bundle"

function Say($text) { Write-Host $text }

# A Python that can run without a console window. pyw.exe comes with the
# python.org installer and keeps working when Python is upgraded; pythonw.exe
# is beside any python.exe, including the Microsoft Store one. The Store's
# "python" alias that only opens the Store is skipped by asking it its version.
function Find-Pythonw {
    $py = Get-Command "pyw.exe" -ErrorAction SilentlyContinue
    if ($py) {
        $version = & py -3 -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
        if ("$version".Trim() -eq "True") { return $py.Source }
    }
    foreach ($name in @("python.exe", "python3.exe")) {
        $python = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $python) { continue }
        $version = & $python.Source -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
        if ("$version".Trim() -ne "True") { continue }
        $pythonw = Join-Path (Split-Path $python.Source) "pythonw.exe"
        if (Test-Path $pythonw) { return $pythonw }
        return $python.Source
    }
    return $null
}

$Pythonw = Find-Pythonw
if (-not $Pythonw) {
    Say "Python 3.9 or newer is needed and was not found."
    Say "Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe"
    Say "to PATH') or from the Microsoft Store, then run this again."
    Start-Process "https://www.python.org/downloads/windows/"
    exit 1
}

# Work from a local checkout when there is one, otherwise fetch a copy.
$Source = ""
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "..\app\server.py"))) {
    $Source = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$Temp = ""
if (-not $Source) {
    $Ref = $env:MDA_REF
    if (-not $Ref) {
        try {
            $latest = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
                -Headers @{ "User-Agent" = "document-to-markdown" } -TimeoutSec 20
            if ($latest.tag_name -match '^v?\d+(\.\d+){0,3}$') { $Ref = $latest.tag_name }
        } catch { }
        if (-not $Ref) {
            $Ref = "main"
            Say "Could not reach GitHub to ask for the newest release."
            Say "Installing the latest code from the main branch instead."
        }
    }
    Say "Downloading $AppName $Ref..."
    $Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("mda-" + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $Temp | Out-Null
    $Url = if ($Ref -like "v*") { "https://github.com/$Repo/archive/refs/tags/$Ref.zip" }
           else { "https://github.com/$Repo/archive/refs/heads/$Ref.zip" }
    $Zip = Join-Path $Temp "source.zip"
    Invoke-WebRequest -Uri $Url -OutFile $Zip -Headers @{ "User-Agent" = "document-to-markdown" }
    Expand-Archive -Path $Zip -DestinationPath (Join-Path $Temp "src")
    $Source = (Get-ChildItem (Join-Path $Temp "src") -Directory | Select-Object -First 1).FullName
}

# An older copy may still be serving in the background. Ask it to stop, so the
# icon opens the version we are about to install rather than the old one.
$Instance = Join-Path $Support "instance.json"
if (Test-Path $Instance) {
    try {
        $saved = Get-Content $Instance -Raw | ConvertFrom-Json
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($saved.port)/api/quit?token=$($saved.token)" `
            -ContentType "application/json" -Body "{}" -TimeoutSec 2 | Out-Null
        Say "Stopped the copy that was already running."
    } catch { }
}

# An in-app update leaves a newer copy of the app's files in "current", and
# the app prefers it. Clearing it means this installer always gets you the
# version it just downloaded.
New-Item -ItemType Directory -Path $Support -Force | Out-Null
Remove-Item -Recurse -Force (Join-Path $Support "current") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $Bundle -ErrorAction SilentlyContinue
foreach ($sub in @("app", "scripts", "docs")) {
    New-Item -ItemType Directory -Path (Join-Path $Bundle $sub) -Force | Out-Null
}
Copy-Item (Join-Path $Source "app\server.py"), (Join-Path $Source "app\index.html") (Join-Path $Bundle "app")
Copy-Item (Join-Path $Source "scripts\doc2gfm.py"), (Join-Path $Source "scripts\mcp_server.py") (Join-Path $Bundle "scripts")
Copy-Item (Join-Path $Source "VERSION"), (Join-Path $Source "BUNDLE_FORMAT") $Bundle
Copy-Item (Join-Path $Source "docs\icon.png") (Join-Path $Bundle "docs")
$Icon = Join-Path $Source "windows\icon.ico"
if (Test-Path $Icon) { Copy-Item $Icon (Join-Path $Bundle "icon.ico") }

# Shortcuts. The target is the Python without a console, the argument is the
# app's own server, which opens the page in the browser and gets out of the
# way, exactly as the Mac icon does.
$Server = Join-Path $Bundle "app\server.py"
function New-Shortcut($path) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $Pythonw
    $shortcut.Arguments = "`"$Server`""
    $shortcut.WorkingDirectory = $Support
    $shortcut.Description = "Turn documents into Markdown, on this computer"
    if (Test-Path (Join-Path $Bundle "icon.ico")) { $shortcut.IconLocation = (Join-Path $Bundle "icon.ico") }
    $shortcut.Save()
}
$Desktop = [Environment]::GetFolderPath("Desktop")
if ($Desktop -and (Test-Path $Desktop)) { New-Shortcut (Join-Path $Desktop "$AppName.lnk") }
$StartMenu = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Microsoft\Windows\Start Menu\Programs"
if (Test-Path $StartMenu) { New-Shortcut (Join-Path $StartMenu "$AppName.lnk") }

if ($Temp) { Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue }

Say ""
Say "Installed: $Bundle"
Say "A shortcut called `"$AppName`" is on your Desktop and in the Start Menu."
if (-not $env:MDA_NO_OPEN) {
    Say "Opening it now. The first time, it will offer to set itself up."
    Start-Process -FilePath $Pythonw -ArgumentList "`"$Server`"" -WorkingDirectory $Support
}
