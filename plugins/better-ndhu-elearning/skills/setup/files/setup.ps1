<#
    NDHU Moodle 同步器 — 安裝腳本

    做三件事:
      1. 檢查 Python, 裝需要的套件
      2. 註冊兩個工作排程器任務 (登入時 + 每天中午)
      3. 提醒你去設密碼

    用法 (一般 PowerShell 視窗就好, 不用系統管理員):
      powershell -ExecutionPolicy Bypass -File .\setup.ps1   (在 _moodle 資料夾裡)

    兩個 Windows 地雷, 改這個檔時別踩回去:
      1. 存成 UTF-8 with BOM。PowerShell 5.1 讀沒有 BOM 的 .ps1 會用 ANSI 解碼,
         中文變亂碼後 parser 就爆了。
      2. 傳給原生 exe (python/pip) 的參數裡不要有雙引號。PowerShell 5.1 重新
         組裝命令列時不會正確逸出它們, 引號會被吃掉。
      3. 不要設 $ErrorActionPreference = 'Stop'。原生指令只要往 stderr 寫一個字
         (pip 的 WARNING 就會), 整支腳本就會被當成終止錯誤停掉。
#>

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host '=== NDHU Moodle 同步器 安裝 ===' -ForegroundColor Cyan
Write-Host "位置: $here"
Write-Host ''

# --- 1. Python -------------------------------------------------------------
# 用 --version 而不是 -c 'print(...)': 後者帶雙引號, 傳給原生 exe 會被 PS 吃掉
$py = $null
$pyver = ''
foreach ($cand in @('py', 'python', 'python3')) {
    if (-not (Get-Command $cand -ErrorAction SilentlyContinue)) { continue }
    $out = ''
    try { $out = (& $cand --version 2>&1 | Out-String) } catch { continue }
    if ($out -match 'Python\s+(\d+)\.(\d+)') {
        if ([int]$Matches[1] -lt 3) { continue }   # 跳過 Python 2
        $py = $cand
        $pyver = "$($Matches[1]).$($Matches[2])"
        break
    }
}
if (-not $py) {
    Write-Host ' 找不到可用的 Python 3。' -ForegroundColor Red
    Write-Host ' 去 https://www.python.org/downloads/ 裝一個, 記得勾 Add python.exe to PATH。'
    Write-Host ' (如果打 python 會跳出 Microsoft Store, 那是系統的假捷徑, 不算數。)'
    exit 1
}
Write-Host " Python: $py (版本 $pyver)" -ForegroundColor Green

# pythonw = 不會跳黑色視窗的版本, 排程用它比較不吵
$pyw = $py
$pywCmd = Get-Command 'pythonw' -ErrorAction SilentlyContinue
if ($pywCmd) {
    $pyw = $pywCmd.Source
} else {
    $pyCmd = Get-Command $py -ErrorAction SilentlyContinue
    if ($pyCmd -and $pyCmd.Source) {
        $guess = Join-Path (Split-Path -Parent $pyCmd.Source) 'pythonw.exe'
        if (Test-Path $guess) { $pyw = $guess } else { $pyw = $pyCmd.Source }
    }
}
Write-Host " 排程會用: $pyw" -ForegroundColor DarkGray

# --- 2. 套件 ---------------------------------------------------------------
Write-Host ''
Write-Host '安裝套件 (pip 的 WARNING 可以無視)...' -ForegroundColor Cyan
& $py -m pip install --upgrade pip 2>&1 | Out-Null
& $py -m pip install --upgrade requests beautifulsoup4 pdfplumber python-pptx python-docx pywin32 olefile 2>&1 |
    ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
$pipCode = $LASTEXITCODE

# 直接驗證裝好沒, 比看 exit code 可靠
$checker = Join-Path $here '_check_imports.py'
@'
import importlib, sys
missing = []
for mod, pkg in [("requests","requests"), ("bs4","beautifulsoup4"),
                 ("pdfplumber","pdfplumber"), ("pptx","python-pptx"),
                 ("docx","python-docx"), ("win32crypt","pywin32"),
                 ("olefile","olefile")]:
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(pkg)
print("MISSING:" + ",".join(missing))
'@ | Set-Content -Path $checker -Encoding UTF8

$check = (& $py $checker 2>&1 | Out-String)
Remove-Item $checker -ErrorAction SilentlyContinue

if ($check -match 'MISSING:(.*)') {
    $missing = $Matches[1].Trim()
    if ($missing) {
        Write-Host " 這些套件沒裝成功: $missing" -ForegroundColor Yellow
        Write-Host " 手動補: $py -m pip install $($missing -replace ',',' ')" -ForegroundColor Yellow
    } else {
        Write-Host ' 套件全部 OK' -ForegroundColor Green
    }
} else {
    Write-Host " 套件檢查沒跑起來 (pip exit code $pipCode), 上面訊息看一下。" -ForegroundColor Yellow
}

# --- 3. 工作排程器 ---------------------------------------------------------
# 交給 install_task.ps1: 它會找到正確的 pythonw / pyw, 並用你自己的帳號註冊
# (登入時 + 每天 12:00 兩個工作), 不需要系統管理員權限。
Write-Host ''
& (Join-Path $here 'install_task.ps1')

# --- 4. 下一步 -------------------------------------------------------------
Write-Host ''
Write-Host '=== 還差兩步 ===' -ForegroundColor Cyan
Write-Host ('1) 打開 {0}, 把 username 填成你的 gms 帳號 (不用 @gms.ndhu.edu.tw)' -f (Join-Path $here 'config.ini'))
Write-Host '2) 設密碼, 然後試跑一次:'
Write-Host ('     {0} "{1}"' -f $py, (Join-Path $here 'set_password.py')) -ForegroundColor Yellow
Write-Host ('     {0} "{1}" --force' -f $py, (Join-Path $here 'moodle_sync.py')) -ForegroundColor Yellow
Write-Host ''
Write-Host ('之後查教材:  {0} "{1}" 關鍵字' -f $py, (Join-Path $here 'search.py')) -ForegroundColor DarkGray
Write-Host '移除排程:    Unregister-ScheduledTask -TaskName ''NDHU Moodle Sync (logon)'', ''NDHU Moodle Sync (daily)''' -ForegroundColor DarkGray
