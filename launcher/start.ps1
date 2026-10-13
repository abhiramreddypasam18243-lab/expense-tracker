# Expense Tracker desktop launcher (spec 8.2).
# Starts the container in WSL (localhost-only compose file) and opens the app in its own window.
param([string]$Distro = "Ubuntu")

$ErrorActionPreference = "Stop"
# 127.0.0.1, not "localhost": Windows tries IPv6 (::1) first, which WSL does not forward, so requests hang.
$Url = "http://127.0.0.1:8501"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

function Test-Healthy {
    try { return (Invoke-WebRequest "$Url/_stcore/health" -UseBasicParsing -TimeoutSec 2).Content -eq "ok" }
    catch { return $false }
}

function Show-Error([string]$Message) {
    [System.Windows.Forms.MessageBox]::Show($Message, "Expense Tracker", "OK", "Error") | Out-Null
    exit 1
}

# WSL stops Ubuntu a few seconds after the last Windows-side wsl.exe exits, even with Docker running inside,
# which would take the app down. One hidden, tagged "sleep infinity" keeps it up; stop.ps1 ends it.
$KeepAliveTag = "expense-tracker-keepalive"
function Start-KeepAlive {
    $running = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
        Where-Object { $_.CommandLine -match $KeepAliveTag }
    if (-not $running) {
        Start-Process wsl.exe -WindowStyle Hidden `
            -ArgumentList "-d", $Distro, "--", "bash", "-c", "`"exec -a $KeepAliveTag sleep infinity`""
    }
}

Start-KeepAlive

if (-not (Test-Healthy)) {
    # Tell the user something is happening: the first start builds the image and can take a minute.
    $tray = New-Object System.Windows.Forms.NotifyIcon
    $tray.Icon = New-Object System.Drawing.Icon (Join-Path $PSScriptRoot "icon.ico")
    $tray.Visible = $true
    $tray.ShowBalloonTip(5000, "Expense Tracker", "Starting... the first start can take a minute.", "Info")

    try {
        $linuxDir = (& wsl.exe -d $Distro -- wslpath -a "$ProjectDir" | Out-String).Trim()
        & wsl.exe -d $Distro -- bash -lc "cd '$linuxDir' && docker compose up -d --build >/dev/null 2>&1"
        if ($LASTEXITCODE -ne 0) {
            Show-Error ("Could not start the container in WSL '$Distro'.`n`n" +
                "Check that Docker works there: open Ubuntu and run 'docker ps'.")
        }
        $deadline = (Get-Date).AddSeconds(180)
        while (-not (Test-Healthy)) {
            if ((Get-Date) -gt $deadline) {
                Show-Error ("The app did not become ready within 3 minutes.`n`n" +
                    "See what happened: open Ubuntu, go to the project folder and run 'docker compose logs'.")
            }
            Start-Sleep -Seconds 2
        }
    }
    finally {
        $tray.Visible = $false
        $tray.Dispose()
    }
}

# Own window, no tabs or address bar (Edge app mode). Falls back to the default browser.
$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    Start-Process $edge -ArgumentList "--app=$Url", "--window-size=1400,900"
}
else {
    Start-Process $Url
}
