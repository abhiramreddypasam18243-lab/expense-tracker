# Stops the Expense Tracker containers. Your data is kept (it lives in a Docker volume).
param([string]$Distro = "Ubuntu")

$ProjectDir = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms

$linuxDir = (& wsl.exe -d $Distro -- wslpath -a "$ProjectDir" | Out-String).Trim()
& wsl.exe -d $Distro -- bash -lc "cd '$linuxDir' && docker compose stop >/dev/null 2>&1"
$stopped = $LASTEXITCODE -eq 0

# End the hidden keep-alive that start.ps1 started, so WSL can shut down and free its memory.
Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
    Where-Object { $_.CommandLine -match "expense-tracker-keepalive" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

if ($stopped) {
    [System.Windows.Forms.MessageBox]::Show("Expense Tracker stopped. Your data is kept.", "Expense Tracker") | Out-Null
}
else {
    [System.Windows.Forms.MessageBox]::Show("Could not stop the container in WSL '$Distro'.", "Expense Tracker",
        "OK", "Error") | Out-Null
}
