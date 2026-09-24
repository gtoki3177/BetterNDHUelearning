---
name: setup
description: 第一次安裝 BetterNDHUelearning：把東華 e學苑 / gms 信箱同步程式裝到使用者的 Windows 電腦、建立 BetterElearning 儀表板、設好每天自動更新的排程。Use when the user says「幫我設定 BetterNDHUelearning」「安裝 BetterElearning」「設定 e學苑儀表板」「set up BetterNDHUelearning」, or asks to reinstall / upgrade the local sync scripts.
---

# BetterNDHUelearning 安裝

帶使用者一步步裝好。使用者多半不是工程背景：講白話，一次只給一小段要做的事，等他說做好了再往下。

## 硬規則

- **不碰密碼**：絕對不要叫使用者把 e學苑密碼或 Gmail 應用程式密碼貼進對話。密碼只在他自己的 PowerShell 視窗裡輸入（`set_password.py`、`set_mail_password.py` 會用 Windows DPAPI 加密存檔）。使用者如果自己貼了，請他去改密碼，你也不要把它寫進任何檔案。
- **不動加密檔**：不讀、不搬 `cred.dat`、`mail_cred.dat`。
- **不連學校**：不自己連 `elearn4.ndhu.edu.tw` 或 `imap.gmail.com`，所有抓取都在使用者電腦上跑。
- **不覆蓋設定**：已存在的 `config.ini`、`state.db`、`latest.json`、`mail.json` 一律不覆蓋。重裝時只更新程式檔（`.py` / `.ps1` / `.vbs`），更新前先講清楚會換掉哪些檔。
- **需要 Windows**：同步程式只支援 Windows（密碼加密和工作排程都是 Windows 的功能）。使用者用 Mac / Linux 的話，老實說目前不支援。

## 1. 確認環境

1. 需要能存取使用者電腦上的一個資料夾，也就是之後放各科資料夾的地方，例如「大四」。還沒連線的話，請他在 Claude 桌面版用「新增資料夾」連上。
2. 用 AskUserQuestion 一次問完下面這些（有預設值的就列成選項）：
   - gms 帳號（學號，不含 `@gms.ndhu.edu.tw`）。
   - 系所：用來把系辦、系上老師的信分成一組，例如「資工系」。
   - 要不要同步學校信箱：需要 Google 兩步驟驗證和應用程式密碼。
   - 要不要「點教材直接開檔」：會在 Windows 登記一個 `betterel:` 連結，說明見第 4 步。
   - 有沒有不想同步的課：填課號，例如軍訓、體育。預設不跳過。

## 2. 把程式放到使用者電腦

目標資料夾是 `<根資料夾>\_moodle\`。把本技能資料夾裡 `files/` 的全部內容複製過去，包括 `brand/`：

- **本機 session**：直接複製。
- **雲端 session**：先複製到 `/mnt/user-data/outputs/betterndhu/`，再用 `device_commit_files` 寫到使用者電腦（一次最多 50 個檔）。

注意事項：

- **位元組原樣複製**：`.ps1` 檔開頭有 UTF-8 BOM，少了它 PowerShell 5.1 會把中文讀成亂碼。
- **建立 `config.ini`**：從 `config.example.ini` 產生，填入：
  - `[moodle] username = <學號>`、`root_folder = <根資料夾完整路徑>`、`skip_courses = <課號>`（有的話）。
  - `[dashboard] dept_label = <系所簡稱>`、`dept_pattern = <比對系上寄件者的正規表示式>`。例如資工系用 `資工|資訊工程|CSIE|系辦`，電機系用 `電機|EE|系辦`。
  - `dept_map` 保留預設值。使用者的系所課號前綴不在清單裡的話，補上去，例如 `AM:應用數學系`。
- `config.example.ini` 留著，不要刪。

## 3. 請使用者在自己電腦跑安裝（逐段給指令）

請他在 `_moodle` 資料夾空白處按右鍵 →「在終端中開啟」，然後依序貼上：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

這會裝 Python 套件，並註冊兩個工作排程：登入時一次、每天 12:00 一次。沒有 Python 的話，請他先到 python.org 下載安裝，安裝時勾選「Add python.exe to PATH」。

```powershell
python set_password.py
python moodle_sync.py --force
```

`set_password.py` 要他輸入 e學苑（gms）密碼，輸入時畫面不會顯示任何字。`moodle_sync.py --force` 第一次會跑幾十秒到幾分鐘。

**信箱（有選才做）：**

1. 用 gms 帳號登入 Google，到 myaccount.google.com →「安全性」，開啟「兩步驟驗證」。
2. 到 myaccount.google.com/apppasswords，名稱取 `moodle-mail`，產生一組 16 碼的應用程式密碼。這組密碼只貼在終端機裡，不要貼給 Claude。
3. 依序執行：

```powershell
python set_mail_password.py
python mail_sync.py --test
python mail_sync.py
powershell -ExecutionPolicy Bypass -File .\install_mail_task.ps1
```

學校如果鎖了應用程式密碼，`--test` 會登入失敗。這時就先跳過信箱，把 `config.ini` 整個 `[mail]` 段保留不動即可。

## 4. 點教材直接開檔（有選才做）

先把這段講給使用者聽，他同意了再給指令：

> 網頁本身不能開你電腦上的檔案，所以要在 Windows 登記一個 `betterel:` 連結，點了就交給 `_moodle\open_local.ps1` 開檔。它只開根資料夾裡的文件檔（pdf / ppt / pptx / doc / docx / xls / xlsx / txt / md / csv）；zip 和資料夾會在檔案總管打開；exe 之類的只在檔案總管裡標出來，不會執行。任何網頁都能觸發這種連結，但因為有上面的限制，最多就是幫你打開根資料夾裡的文件。想拿掉，就加 `-Uninstall` 再跑一次。

```powershell
powershell -ExecutionPolicy Bypass -File .\install_open_local.ps1
```

限制要先講清楚：Claude 桌面版的 artifact 面板不會把這種連結交給 Windows，所以在桌面版裡點教材只會複製路徑。要點了就直接開檔，得用 Chrome 或 Edge 開儀表板網址。開檔前瀏覽器會閃一下新分頁，這是正常的。

## 5. 建立儀表板

等使用者說 `moodle_sync.py --force` 跑完了：

1. **檢查同步結果**：把 `latest.json`、`config.ini`（有信箱的話加 `mail.json`）拿進工作環境。先看 `latest.json` 的 `errors` 和 `log.txt` 最後幾行。登入失敗通常是學號打錯，或密碼要重跑 `set_password.py`。
2. **產生頁面**：用 daily-update 技能的 `scripts/build.py`，以 `--template` 指向 daily-update 技能的 `assets/dashboard.html`（第一次沒有 `--page`）。
3. **補寫信件摘要**：照 daily-update 技能第 4 步，替 `need_summary` 寫好摘要，再跑第二輪。
4. **發佈**：用 Artifact 工具發佈成新頁面，`icon` 用 `calendar`，不帶 `url`。
5. **記下網址**：把拿到的網址寫回 `config.ini` 的 `[dashboard] artifact_url =`。只改這一行，其他行照原樣保留。

## 6. 設定每天自動更新

用 `create_trigger` 建立排程（名稱：「BetterElearning 每日更新」）：

- 時間：台北時間每天 13:05，也就是 `cron_expression: "5 5 * * *"`（UTC）。這個時間排在本機 12:00 / 12:20 的同步之後。
- `requires_local_device: true`。
- `folders`：填根資料夾。
- `prompt`：

```text
執行 BetterNDHUelearning 的每日更新（better-ndhu-elearning 外掛的 daily-update 技能）。
設定檔：<根資料夾>\_moodle\config.ini
這是全自動排程，沒人在旁邊，不要問問題，照技能的步驟做完。
```

建好後，用一句話告訴使用者這個排程的核准設定。如果每次執行都要核准，提醒他可以在排程設定裡改成「自動核准」，不然沒人按核准就不會更新。

## 7. 收尾

用幾句話告訴使用者：

- 儀表板網址。建議用 Chrome / Edge 開，把分頁固定起來；如果裝了「直接開檔」，在瀏覽器裡點教材才打得開。
- 每天 13:05 左右會自動更新。電腦沒開的那天不會更新。
- 排程有沒有在跑，可以在 `_moodle` 執行 `check_task.ps1` 查。
- 查講義內容可以直接問 Claude，例如「哪堂課講過 deadlock」。
