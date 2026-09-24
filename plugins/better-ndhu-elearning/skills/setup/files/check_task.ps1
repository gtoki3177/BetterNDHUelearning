# 檢查 NDHU Moodle Sync 的兩個排程任務現在到底有沒有在跑。
# 用法: 在 _moodle 資料夾按右鍵 -> 在終端中開啟, 然後:
#   powershell -ExecutionPolicy Bypass -File .\check_task.ps1

$names = @('NDHU Moodle Sync (logon)', 'NDHU Moodle Sync (daily)', 'NDHU Mail Sync (daily)')

$codes = @{
    0          = '成功'
    1          = '一般錯誤 (通常是 python 那行有問題)'
    2147942402 = '找不到檔案 (python 路徑或 moodle_sync.py 路徑不對)'
    267011     = '還沒跑過'
    267009     = '正在執行中'
    267014     = '上次被中止'
}

Write-Host ''
Write-Host '=== 排程任務狀態 ===' -ForegroundColor Cyan

foreach ($n in $names) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host ("[X] {0}  -- 沒有註冊" -f $n) -ForegroundColor Red
        continue
    }
    $i = $t | Get-ScheduledTaskInfo
    $res = if ($codes.ContainsKey([int]$i.LastTaskResult)) {
        $codes[[int]$i.LastTaskResult]
    } else {
        '未知代碼 ' + $i.LastTaskResult
    }
    $colour = if ($t.State -eq 'Disabled') { 'Yellow' } else { 'Green' }

    Write-Host ("[{0}] {1}" -f $t.State, $n) -ForegroundColor $colour
    Write-Host ("      上次執行 : {0}" -f $i.LastRunTime)
    Write-Host ("      上次結果 : {0} ({1})" -f $res, $i.LastTaskResult)
    Write-Host ("      下次執行 : {0}" -f $i.NextRunTime)
    Write-Host ("      執行內容 : {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments) -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '=== log.txt 最後 12 行 ===' -ForegroundColor Cyan
$log = Join-Path $PSScriptRoot 'log.txt'
if (Test-Path $log) { Get-Content $log -Tail 12 } else { Write-Host '(還沒有 log.txt)' }

Write-Host ''
Write-Host '--- 常見情況 ---' -ForegroundColor Cyan
Write-Host '沒有註冊 / State=Disabled  -> 重跑一次 setup.ps1 就會重新註冊'
Write-Host '上次結果不是 0            -> 照上面「執行內容」那行手動跑一次, 看錯在哪'
Write-Host '上次執行時間很舊          -> 12:00 那個時段電腦是關機或睡眠; 開機後會自己補跑一次'
Write-Host ''
Write-Host '手動跑一次: ' -NoNewline
Write-Host ('python "{0}" --force' -f (Join-Path $PSScriptRoot 'moodle_sync.py')) -ForegroundColor Yellow
Write-Host ''
