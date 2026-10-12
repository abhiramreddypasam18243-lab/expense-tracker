# Stops the Expense Tracker container. Your data is kept (it lives in a Docker volume).
param([string]$Distro = "Ubuntu")

$ProjectDir = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms

$linuxDir = (& wsl.exe -d $Distro -- wslpath -a "$ProjectDir" | Out-String).Trim()
& wsl.exe -d $Distro -- bash -lc "cd '$linuxDir' && docker compose stop >/dev/null 2>&1"
if ($LASTEXITCODE -eq 0) {
    [System.Windows.Forms.MessageBox]::Show("Expense Tracker stopped. Your data is kept.", "Expense Tracker") | Out-Null
}
else {
    [System.Windows.Forms.MessageBox]::Show("Could not stop the container in WSL '$Distro'.", "Expense Tracker",
        "OK", "Error") | Out-Null
}
