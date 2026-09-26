# 把 betterel_mcp.py 註冊成 Claude 桌面版的本機 MCP server (名字: betterel)
# 之後在 Claude 桌面版打開 BetterElearning 儀表板, 頁面就會直接讀這台電腦上的 latest.json / mail.json。
#
# 用法: 先把 Claude 桌面版完全關掉 (系統匣圖示也按結束, 不然它關閉時可能把設定檔蓋回去),
#       再在檔案總管對這個檔按右鍵 → 「用 PowerShell 執行」
#       (或在 PowerShell 裡: powershell -ExecutionPolicy Bypass -File .\install_mcp.ps1)
# 移除: powershell -ExecutionPolicy Bypass -File .\install_mcp.ps1 -Remove
#
# 它只會改 Claude 桌面版設定檔裡 mcpServers.betterel 這一項, 改之前會先備份。

param([switch]$Remove)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $here 'betterel_mcp.py'
if (-not (Test-Path -LiteralPath $server)) { Write-Host "找不到 $server" -ForegroundColor Red; Read-Host '按 Enter 結束'; return }

# ---------------------------------------------------------------- 找 python.exe
# MCP 靠標準輸入輸出溝通, 要用 python.exe (不是 pythonw.exe)
function Get-FirstPath($list) { foreach ($p in $list) { if ($p -and (Test-Path -LiteralPath $p)) { return $p } }; return $null }

Write-Host '=== 找 Python ===' -ForegroundColor Cyan
$cands = @()
foreach ($c in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) { if ($c.Source) { $cands += $c.Source } }
$cands = @($cands | Where-Object { $_ -notmatch 'WindowsApps' }) + @($cands | Where-Object { $_ -match 'WindowsApps' })
foreach ($root in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'),
                    'C:\Program Files\Python313', 'C:\Program Files\Python312', 'C:\Program Files\Python311',
                    'C:\Python313', 'C:\Python312', 'C:\Python311')) {
    if (Test-Path -LiteralPath $root) {
        $cands += (Get-ChildItem -LiteralPath $root -Recurse -Depth 1 -Filter 'python.exe' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
}
$py = Get-FirstPath $cands
if (-not $py) {
    Write-Host '找不到 python.exe。在終端機打 "where python" 看它裝在哪, 把路徑貼給 Claude。' -ForegroundColor Red
    Read-Host '按 Enter 結束'; return
}
Write-Host ("python : {0}" -f $py) -ForegroundColor Green

# ---------------------------------------------------------------- 先自我測試一次
if (-not $Remove) {
    Write-Host ''
    Write-Host '=== 測試讀檔 ===' -ForegroundColor Cyan
    & $py $server --test
    if ($LASTEXITCODE -ne 0) { Write-Host 'betterel_mcp.py 自我測試失敗, 先不註冊。' -ForegroundColor Red; Read-Host '按 Enter 結束'; return }
}

# ---------------------------------------------------------------- 找 Claude 桌面版設定檔
# 一般安裝: %APPDATA%\Claude ; Microsoft Store 版: %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude
$targets = @(Join-Path $env:APPDATA 'Claude\claude_desktop_config.json')
foreach ($pkg in @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Packages') -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue)) {
    $targets += (Join-Path $pkg.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json')
}
# 只寫已經存在的 Claude 資料夾 (第一個一定寫)
$targets = @($targets | Select-Object -Unique | Where-Object { ($_ -eq $targets[0]) -or (Test-Path -LiteralPath (Split-Path -Parent $_)) })

foreach ($cfgPath in $targets) {
    Write-Host ''
    Write-Host ("=== 設定檔: {0} ===" -f $cfgPath) -ForegroundColor Cyan
    $dir = Split-Path -Parent $cfgPath
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }

    if (Test-Path -LiteralPath $cfgPath) {
        $bak = "$cfgPath.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Copy-Item -LiteralPath $cfgPath -Destination $bak
        Write-Host ("已備份到 {0}" -f $bak) -ForegroundColor DarkGray
    }
    # 用 Python 改 JSON (PowerShell 5.1 的 ConvertFrom-Json 遇到只差大小寫的 key 會失敗)
    if ($Remove) { & $py $server --unregister $cfgPath } else { & $py $server --register $cfgPath $py }
    if ($LASTEXITCODE -ne 0) { Write-Host '改設定檔失敗, 原檔沒動 (有備份)。把上面的錯誤訊息貼給 Claude。' -ForegroundColor Red }
}

Write-Host ''
if ($Remove) {
    Write-Host '完成。把 Claude 桌面版完全關掉 (右下角系統匣的圖示也要結束) 再打開就生效。' -ForegroundColor Cyan
} else {
    Write-Host '完成！接下來:' -ForegroundColor Cyan
    Write-Host '  1. 把 Claude 桌面版完全關掉 (右下角系統匣的圖示也要按「結束」), 再打開。'
    Write-Host '  2. 在桌面版裡打開 BetterElearning 儀表板, 第一次會問你要不要讓這頁讀 betterel, 按允許。'
    Write-Host '  3. 右上角時間旁邊出現「即時・本機」就成功了。'
}
Read-Host '按 Enter 結束'
