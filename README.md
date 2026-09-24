# BetterNDHUelearning

東華大學 e學苑（Moodle）和 gms 學校信箱的 Claude Cowork 外掛。每天自動整理成一個叫 **BetterElearning** 的儀表板：

- **任務**：待交作業倒數、三週日曆、作業詳情。可以一鍵複製「開始做這份作業」的 Claude prompt，講義路徑已經帶進去。
- **信箱**：未讀信自動分類（系上 / 課程 / 學校單位 / 校園公告 / 系統通知）。需要注意的信會由 Claude 寫好中文摘要、要做的事和期限。
- **課程 / 教材**：每門課的活動和講義。講義都會下載到你電腦，建全文索引，直接問 Claude「哪堂課講過 deadlock」就找得到。
- **推播**：作業 3 天內到期還沒交、有需要注意的新信、同步壞掉時，會推到手機。

> 這不是學校官方的東西，也和學校沒有任何關係。它用你自己的帳號登入、只抓你自己的資料。

## 需要什麼

- **Windows 電腦**：同步程式用到 Windows 的密碼加密（DPAPI）和工作排程器。
- **Python 3.10 以上**：到 [python.org](https://www.python.org/downloads/) 下載，安裝時勾「Add python.exe to PATH」。
- **Claude 付費方案 + Claude 桌面版（Cowork）**：要能用排程任務，而且 Claude 要能連到你電腦上的資料夾。
- **gms 帳號**。要同步信箱的話，還要開 Google 兩步驟驗證並產生應用程式密碼，設定時 Claude 會一步步帶你。

## 安裝

1. 在 Cowork 的外掛設定新增市集（marketplace），填這個 repo：`gtoki3177/BetterNDHUelearning`。
2. 從市集安裝 **better-ndhu-elearning**。
3. 在 Claude 桌面版連上你要放課程資料的資料夾，例如「大四」，然後跟 Claude 說：

   > 幫我設定 BetterNDHUelearning

Claude 會問你學號、系所、要不要同步信箱，把程式放進資料夾，再告訴你要在 PowerShell 貼哪幾行（裝套件、設密碼、跑第一次同步）。跑完它會建好儀表板，並設定每天自動更新。

## 怎麼運作

```
你的電腦 (Windows 工作排程器，每天 12:00 / 12:20)
  moodle_sync.py → 登入 e學苑 → 下載講義、建索引 → latest.json
  mail_sync.py   → IMAP 讀 gms 信箱             → mail.json
                                     │
Claude 排程 (每天 13:05)             ▼
  讀 latest.json / mail.json → 替需注意的信寫摘要 → 更新 BetterElearning 儀表板 → 必要時推播
```

抓資料全部在你自己的電腦上跑，原因有兩個：

- e學苑的 Moodle web service 是關的，拿不到 API token，只能模擬一般登入。
- Claude 的雲端環境連不到 `*.ndhu.edu.tw`。

## 隱私：什麼會交給 Claude、什麼不會

| 不會離開你電腦 | 會給 Claude 讀 |
|---|---|
| e學苑密碼、Gmail 應用程式密碼（Windows DPAPI 加密，換帳號或換電腦都解不開） | `latest.json`：課程、作業、教材清單、同步時間 |
| 講義檔案本身（除非你叫 Claude 去讀） | `mail.json`：最近 14 天信件的寄件者、主旨、內文前 1200 字 |

儀表板是你 claude.ai 帳號下的私人頁面，除非你自己分享，不然別人看不到。

不想讓某些信給 Claude 讀，有兩種做法：

- 把寄件者加進 `config.ini` 的 `skip_senders`。
- 把 `include_body` 設成 `false`，就只給寄件者和主旨。

## 點教材直接開檔（選用）

網頁本身不能開你電腦上的檔案，所以這個功能要執行 `install_open_local.ps1`：它會在 Windows 登記一種 `betterel:` 連結，點了就交給 `open_local.ps1` 處理。

- **開得了什麼**：只開你課程資料夾裡的文件檔（pdf / ppt / pptx / doc / docx / xls / xlsx / txt / md / csv），zip 和資料夾會在檔案總管打開。
- **exe 等其他檔案**：只會在檔案總管裡標出來，一律不執行。
- **在哪裡能用**：只在 Chrome / Edge 開儀表板時有效。Claude 桌面版裡點了只會複製路徑。
- **移除**：`powershell -ExecutionPolicy Bypass -File .\install_open_local.ps1 -Uninstall`

## 常見問題

- **登入失敗**：確認 `config.ini` 的 `username` 是學號。改過 gms 密碼的話，要重跑 `python set_password.py`。
- **排程有沒有在跑**：在 `_moodle` 資料夾執行 `powershell -ExecutionPolicy Bypass -File .\check_task.ps1`。
- **手動同步一次**：`python moodle_sync.py --force`。
- **不想同步某些課**：`config.ini` 的 `skip_courses` 填課號，用逗號分隔。
- **學校改了 e學苑版面導致抓不到**：看 `latest.json` 的 `errors`，歡迎開 issue 或送 PR。

## 請客氣一點

每個請求之間預設停 0.6 秒，每天只同步一次。請不要把 `request_delay` 調小、也不要把排程改得很密，別把學校伺服器當沙包。也請遵守學校的資訊使用規範。

## 開發

```
plugins/better-ndhu-elearning/
  skills/setup/            安裝流程；files/ 是會被複製到使用者 _moodle 資料夾的程式
  skills/daily-update/     每日更新流程；scripts/build.py 產生儀表板；assets/dashboard.html 是範本
  skills/search-materials/ 講義全文檢索
```

改信件分類規則時，`dashboard.html` 的 JS、`build.py`、`daily-update/SKILL.md` 三個地方要一起改。

MIT License。
