# Creates "Expense Tracker" and "Stop Expense Tracker" shortcuts on the Desktop and in the Start menu.
# Run once:  powershell -ExecutionPolicy Bypass -File launcher\install-shortcuts.ps1

$shell = New-Object -ComObject WScript.Shell
$icon = Join-Path $PSScriptRoot "icon.ico"
$folders = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))

$shortcuts = @(
    @{ Name = "Expense Tracker"; Script = "start.ps1"; Description = "Open the Expense Tracker" },
    @{ Name = "Stop Expense Tracker"; Script = "stop.ps1"; Description = "Stop the Expense Tracker (data is kept)" }
)

foreach ($folder in $folders) {
    foreach ($s in $shortcuts) {
        $link = $shell.CreateShortcut((Join-Path $folder "$($s.Name).lnk"))
        $link.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $link.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot $s.Script)`""
        $link.WorkingDirectory = Split-Path -Parent $PSScriptRoot
        $link.IconLocation = "$icon,0"
        $link.WindowStyle = 7  # start minimised, so no console flashes up
        $link.Description = $s.Description
        $link.Save()
        Write-Output "Created: $(Join-Path $folder "$($s.Name).lnk")"
    }
}
