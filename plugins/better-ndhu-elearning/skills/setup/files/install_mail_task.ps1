# 註冊「NDHU Mail Sync」每日排程 (跑 mail_sync.py)。
# 用法 (在 _moodle 資料夾):
#   powershell -ExecutionPolicy Bypass -File .\install_mail_task.ps1
# 不需要系統管理員權限 —— 註冊在你自己帳號底下。
# 預設每天 12:20 跑一次 (Moodle 那支是 12:00, 錯開避免同時卡網路)。

param(
    [string]$At = '12:20'
)

$here   = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$script = Join-Path $here 'mail_sync.py'
if (-not (Test-Path $script)) { throw "找不到 $script" }

function Get-FirstPath([string[]]$candidates) {
    foreach ($c in $candidates) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        $c = $c.Trim().Trim('"')
        if (Test-Path -LiteralPath $c) { return (Resolve-Path -LiteralPath $c).Path }
    }
    return $null
}

Write-Host ''
Write-Host '=== 找 Python ===' -ForegroundColor Cyan

$exe = Get-FirstPath @(
    (Join-Path $env:WINDIR 'pyw.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\pyw.exe')
)
if ($exe) { Write-Host ("pyw.exe      : {0}" -f $exe) -ForegroundColor Green }

if (-not $exe) {
    $fromPath = @()
    foreach ($n in @('pythonw.exe', 'python.exe')) {
        $cmds = @(Get-Command $n -All -ErrorAction SilentlyContinue)
        foreach ($c in $cmds) { if ($c.Source) { $fromPath += $c.Source } }
    }
    $fromPath = @($fromPath | Where-Object { $_ -notmatch 'WindowsApps' }) + @($fromPath | Where-Object { $_ -match 'WindowsApps' })
    $exe = Get-FirstPath $fromPath
    if ($exe) { Write-Host ("PATH         : {0}" -f $exe) -ForegroundColor Green }
}

if (-not $exe) {
    $guesses = @()
    foreach ($root in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
            'C:\Program Files\Python313', 'C:\Program Files\Python312', 'C:\Program Files\Python311',
            'C:\Python313', 'C:\Python312', 'C:\Python311'
        )) {
        if (Test-Path -LiteralPath $root) {
            $guesses += (Get-ChildItem -LiteralPath $root -Recurse -Depth 1 -Filter 'pythonw.exe' -ErrorAction SilentlyContinue |
                         ForEach-Object { $_.FullName })
            $guesses += (Get-ChildItem -LiteralPath $root -Recurse -Depth 1 -Filter 'python.exe' -ErrorAction SilentlyContinue |
                         ForEach-Object { $_.FullName })
        }
    }
    $exe = Get-FirstPath $guesses
    if ($exe) { Write-Host ("掃到的       : {0}" -f $exe) -ForegroundColor Green }
}

if (-not $exe) {
    Write-Host '找不到任何 python。在終端機打 "where python" 看看它裝在哪, 把路徑貼給我。' -ForegroundColor Red
    return
}

$dir = Split-Path -Parent $exe
if ($dir) {
    $quiet = Join-Path $dir 'pythonw.exe'
    if ((Test-Path -LiteralPath $quiet) -and ($exe -ne $quiet)) {
        $exe = $quiet
        Write-Host ("改用無視窗版 : {0}" -f $exe) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host ('執行內容 : {0} "{1}"' -f $exe, $script) -ForegroundColor DarkGray
Write-Host ''
Write-Host '=== 註冊排程 ===' -ForegroundColor Cyan

$action = New-ScheduledTaskAction -Execute $exe -Argument ('"{0}"' -f $script) -WorkingDirectory $here
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

$name = 'NDHU Mail Sync (daily)'
try {
    Register-ScheduledTask -TaskName $name -Action $action `
        -Trigger (New-ScheduledTaskTrigger -Daily -At $At) `
        -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host ("[OK] {0}" -f $name) -ForegroundColor Green
} catch {
    Write-Host ("[失敗] {0} -- {1}" -f $name, $_.Exception.Message) -ForegroundColor Red
    Write-Host '用內建的 schtasks 再試一次 (整行複製貼上):' -ForegroundColor Cyan
    Write-Host ('schtasks /Create /TN "{0}" /TR "\"{1}\" \"{2}\"" /SC DAILY /ST {3} /F' -f $name, $exe, $script, $At) -ForegroundColor Yellow
    return
}

Write-Host ''
$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($t) {
    $i = $t | Get-ScheduledTaskInfo
    Write-Host ("[{0}] {1}   下次執行: {2}" -f $t.State, $name, $i.NextRunTime) -ForegroundColor Green
}
Write-Host ''
