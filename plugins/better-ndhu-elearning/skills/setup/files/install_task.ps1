# 重新註冊 NDHU Moodle Sync 的兩個排程任務。
# 用法 (在 _moodle 資料夾):
#   powershell -ExecutionPolicy Bypass -File .\install_task.ps1   (setup.ps1 也會自動叫它)
# 不需要系統管理員權限 —— 這是註冊在你自己帳號底下的工作。

$here   = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$script = Join-Path $here 'moodle_sync.py'
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

# 1) py launcher 的無視窗版本 pyw.exe — 最穩, 不用管裝在哪
$exe = Get-FirstPath @(
    (Join-Path $env:WINDIR 'pyw.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\pyw.exe')
)
if ($exe) { Write-Host ("pyw.exe      : {0}" -f $exe) -ForegroundColor Green }

# 2) 沒有的話, 從 PATH 裡的 python/pythonw 找
if (-not $exe) {
    $fromPath = @()
    foreach ($n in @('pythonw.exe', 'python.exe')) {
        $cmds = @(Get-Command $n -All -ErrorAction SilentlyContinue)
        foreach ($c in $cmds) { if ($c.Source) { $fromPath += $c.Source } }
    }
    # Microsoft Store 的空殼排到最後
    $fromPath = @($fromPath | Where-Object { $_ -notmatch 'WindowsApps' }) + @($fromPath | Where-Object { $_ -match 'WindowsApps' })
    $exe = Get-FirstPath $fromPath
    if ($exe) { Write-Host ("PATH         : {0}" -f $exe) -ForegroundColor Green }
}

# 3) 再不行, 掃常見安裝位置
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

# 同資料夾有 pythonw.exe 就換過去 (跑起來不會閃黑視窗)
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
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

$me = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
# -User 一定要給: 不給的話等於「任何人登入都跑」, 那需要系統管理員權限, 會被擋成「存取被拒」
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $me
try { $logonTrigger.Delay = 'PT3M' } catch { }

$jobs = @(
    @{ Name = 'NDHU Moodle Sync (logon)'; Trigger = $logonTrigger },
    @{ Name = 'NDHU Moodle Sync (daily)'; Trigger = (New-ScheduledTaskTrigger -Daily -At '12:00') }
)

foreach ($j in $jobs) {
    try {
        Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $j.Trigger `
            -Settings $settings -Force -ErrorAction Stop | Out-Null
        Write-Host ("[OK] {0}" -f $j.Name) -ForegroundColor Green
    } catch {
        Write-Host ("[失敗] {0} -- {1}" -f $j.Name, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host ''
Write-Host '=== 註冊結果 ===' -ForegroundColor Cyan
$ok = $true
foreach ($j in $jobs) {
    $t = Get-ScheduledTask -TaskName $j.Name -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host ("[X] {0} 還是沒有" -f $j.Name) -ForegroundColor Red; $ok = $false; continue }
    $i = $t | Get-ScheduledTaskInfo
    Write-Host ("[{0}] {1}   下次執行: {2}" -f $t.State, $j.Name, $i.NextRunTime) -ForegroundColor Green
}

if (-not $ok) {
    Write-Host ''
    Write-Host '用內建的 schtasks 再試一次 (整行複製貼上):' -ForegroundColor Cyan
    Write-Host ('schtasks /Create /TN "NDHU Moodle Sync (daily)" /TR "\"{0}\" \"{1}\"" /SC DAILY /ST 12:00 /F' -f $exe, $script) -ForegroundColor Yellow
}
Write-Host ''
