# 註冊 NDHU Moodle Sync 排程 (0.4 起): 登入後 3 分鐘一次 + 每 30 分鐘一次。
# moodle_sync.py 自己會決定跑哪種: 每天第一次跑完整同步 (下載教材、建索引), 其他時候只跑十幾秒的輕量同步。
# 舊版的「(logon)」「(daily)」兩個排程會一起移除。
# 用法 (在 _moodle 資料夾):
#   powershell -ExecutionPolicy Bypass -File .\install_task.ps1   (setup.ps1 也會自動叫它)
# 不需要系統管理員權限 —— 這是註冊在你自己帳號底下的工作。
param([int]$Minutes = 30)


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

# 1) 問 Python 自己裝在哪 (py 啟動器或 PATH 上的 python)，等一下換成同資料夾的 pythonw.exe。
#    不用 pyw.exe 啟動器直接跑：它會照腳本第一行的 #!/usr/bin/env python3 去 PATH 找 python3.exe，
#    常常找到有主控台的那個 (或 Microsoft Store 的空殼)，每次排程都會閃一下黑窗，甚至根本沒跑成。
$exe = $null
foreach ($cand in @(@('py', '-3'), @('python'))) {
    $c = Get-Command $cand[0] -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
    if (-not $c) { continue }
    $pyArgs = @($cand | Select-Object -Skip 1) + @('-c', 'import sys; print(sys.executable)')
    $real = & $c.Source @pyArgs 2>$null | Select-Object -First 1
    $exe = Get-FirstPath @($real)
    if ($exe) { Write-Host ("Python       : {0}" -f $exe) -ForegroundColor Green; break }
}

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

# 每 N 分鐘一次的觸發器 (開始時間設在今天 00:00, 之後一直重複; 關機期間錯過的會在開機後補跑一次)
function New-EveryTrigger([int]$Minutes) {
    try {
        return New-ScheduledTaskTrigger -Once -At ((Get-Date).Date) -RepetitionInterval (New-TimeSpan -Minutes $Minutes) -ErrorAction Stop
    } catch {
        # 比較舊的 Windows: 改成「每天 00:00 開始, 24 小時內每 N 分鐘重複」
        $t = New-ScheduledTaskTrigger -Daily -At '00:00'
        $t.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
                         -RepetitionDuration (New-TimeSpan -Hours 23 -Minutes 59)).Repetition
        return $t
    }
}

$action = New-ScheduledTaskAction -Execute $exe -Argument ('"{0}"' -f $script) -WorkingDirectory $here
$actions = @($action)
# 完整安裝 (BetterElearning app) 有放 cloud_push.py 的話: 同步完接著把教材路徑和索引統計補進 Firebase
$push = Join-Path $here 'cloud_push.py'
if (Test-Path $push) {
    $actions += New-ScheduledTaskAction -Execute $exe -WorkingDirectory $here `
        -Argument ('"{0}" overlay --dir "{1}" --config "{2}"' -f $push, $here, (Join-Path $here 'config.ini'))
    Write-Host '同步完會接著把教材路徑補進 BetterElearning app (cloud_push.py)' -ForegroundColor DarkGray
}
# config.ini 有 [summaries] (用 API、本地模型或 agent 寫信件摘要) 的話: 再加一個動作，替需注意的新信寫摘要
$summ = Join-Path $here 'summaries.py'
$cfg  = Join-Path $here 'config.ini'
if ((Test-Path $summ) -and (Test-Path $cfg) -and (Select-String -LiteralPath $cfg -Pattern '^\s*\[summaries\]' -Quiet)) {
    $actions += New-ScheduledTaskAction -Execute $exe -WorkingDirectory $here `
        -Argument ('"{0}" auto --config "{1}"' -f $summ, $cfg)
    Write-Host '也會替需注意的新信寫摘要 (summaries.py，記錄在 summaries_log.txt)' -ForegroundColor DarkGray
}
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$me = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
# -User 一定要給: 不給的話等於「任何人登入都跑」, 那需要系統管理員權限, 會被擋成「存取被拒」
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $me
try { $logonTrigger.Delay = 'PT3M' } catch { }

$name = 'NDHU Moodle Sync'
try {
    Register-ScheduledTask -TaskName $name -Action $actions -Trigger @($logonTrigger, (New-EveryTrigger $Minutes)) `
        -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host ("[OK] {0}  (登入時 + 每 {1} 分鐘)" -f $name, $Minutes) -ForegroundColor Green
    foreach ($old in @('NDHU Moodle Sync (logon)', 'NDHU Moodle Sync (daily)')) {
        if (Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $old -Confirm:$false
            Write-Host ("移除舊排程 {0}" -f $old) -ForegroundColor DarkGray
        }
    }
    # 完整安裝：雲端 (GitHub，機器在國外) 連不到 e學苑 時，app 送來的代交作業由這台電腦接手 (每 3 分鐘看一次，沒事就馬上結束)
    $submit = Join-Path $here 'submit.py'
    if ((Test-Path $submit) -and (Test-Path $push)) {
        $relay = 'BetterElearning Submit Relay'
        Register-ScheduledTask -TaskName $relay -Settings $settings -Force -ErrorAction Stop `
            -Action (New-ScheduledTaskAction -Execute $exe -WorkingDirectory $here -Argument ('"{0}" --pending' -f $submit)) `
            -Trigger @((New-EveryTrigger 3)) | Out-Null
        Write-Host ("[OK] {0}  (每 3 分鐘：雲端連不到 e學苑 時代交作業)" -f $relay) -ForegroundColor Green
    }
} catch {
    Write-Host ("[失敗] {0} -- {1}" -f $name, $_.Exception.Message) -ForegroundColor Red
    Write-Host '用內建的 schtasks 再試一次 (整行複製貼上):' -ForegroundColor Cyan
    Write-Host ('schtasks /Create /TN "{0}" /TR "\"{1}\" \"{2}\"" /SC MINUTE /MO {3} /F' -f $name, $exe, $script, $Minutes) -ForegroundColor Yellow
    return
}

Write-Host ''
$t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($t) {
    $i = $t | Get-ScheduledTaskInfo
    Write-Host ("[{0}] {1}   下次執行: {2}" -f $t.State, $name, $i.NextRunTime) -ForegroundColor Green
}
Write-Host ''
