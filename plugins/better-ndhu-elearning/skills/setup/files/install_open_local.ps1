# BetterElearning: register the betterel: link type for the current user (no admin needed).
# After this, clicking a material on the dashboard opens the downloaded file directly.
#   install:   powershell -ExecutionPolicy Bypass -File install_open_local.ps1
#   uninstall: powershell -ExecutionPolicy Bypass -File install_open_local.ps1 -Uninstall
param([switch]$Uninstall)
$key = 'HKCU:\Software\Classes\betterel'
if ($Uninstall) {
    Remove-Item -Path $key -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'betterel: removed.'
    return
}
$vbs = Join-Path $PSScriptRoot 'open_local.vbs'
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name '(default)' -Value 'URL:BetterElearning open local file'
Set-ItemProperty -Path $key -Name 'URL Protocol' -Value ''
New-Item -Path "$key\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$key\shell\open\command" -Name '(default)' -Value ("wscript.exe `"$vbs`" `"%1`"")
Write-Host "betterel: registered -> $vbs"
Write-Host ('Test: Win+R, paste  betterel:open?p=' + [uri]::EscapeDataString($PSScriptRoot) + '   (should open the _moodle folder)')
