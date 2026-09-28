# 註冊「NDHU Mail Sync」排程 (跑 mail_sync.py): 0.4 起改成登入後一次 + 每 10 分鐘一次。
# mail_sync.py 是增量同步, 只下載新信的內文, 一次幾秒, 常跑沒關係。舊的「(daily)」排程會一起移除。
# 用法 (在 _moodle 資料夾):
#   powershell -ExecutionPolicy Bypass -File .\install_mail_task.ps1
# 不需要系統管理員權限 —— 註冊在你自己帳號底下。

param([int]$Minutes = 10)

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
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$me = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $me
try { $logonTrigger.Delay = 'PT2M' } catch { }

$name = 'NDHU Mail Sync'
try {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logonTrigger, (New-EveryTrigger $Minutes)) `
        -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host ("[OK] {0}  (登入時 + 每 {1} 分鐘)" -f $name, $Minutes) -ForegroundColor Green
    if (Get-ScheduledTask -TaskName 'NDHU Mail Sync (daily)' -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName 'NDHU Mail Sync (daily)' -Confirm:$false
        Write-Host '移除舊排程 NDHU Mail Sync (daily)' -ForegroundColor DarkGray
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
