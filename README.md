# BetterNDHUelearning

東華大學 e學苑（Moodle）和 gms 學校信箱的 Claude Cowork 外掛。自動整理成一個叫 **BetterElearning** 的儀表板，電腦開著時一天更新好幾次，手機也看得到：

- **任務**：待交作業倒數、三週日曆、作業詳情，可以依截止日 / 課程 / 最新加入排序、依繳交狀態篩選；超過三份會變成可以左右拖曳的一排。可以一鍵複製「開始做這份作業」的 Claude prompt，講義路徑已經帶進去。
- **信箱**：未讀信自動分類（系上 / 課程 / 學校單位 / 校園公告 / 系統通知）。需要注意的信會由 Claude 寫好中文摘要、要做的事和期限（傍晚的排程會寫；新信你一打開儀表板，頁面也會自己補寫）。
- **立即同步**：右上角的按鈕，按下去 Claude 會叫你電腦馬上同步一次再推上來，大約 1–2 分鐘。桌面版、瀏覽器、手機都能按。
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

Claude 會問你學號、系所、要不要同步信箱，把程式放進資料夾，再告訴你要在 PowerShell 貼哪幾行（裝套件、設密碼、跑第一次同步）。跑完它會建好儀表板，並設定自動更新的排程。

## 怎麼運作

```
你的電腦 (Windows 工作排程器，開機登入時 + 定時)
  mail_sync.py   每 10 分鐘  IMAP 讀 gms 信箱，只下載新信    → mail.json      (幾秒)
  moodle_sync.py 每 30 分鐘  看作業、繳交狀態、期限          → latest.json    (十幾秒)
                 每天第一次  另外下載新講義、建全文索引                        (幾十秒)
                 內容沒變就不重寫檔案
                                     │
Claude 排程「BetterElearning 更新」  ▼  (6:20 / 10:20 / 14:20 / 18:20 / 22:20)
  連到你電腦讀 latest.json / mail.json → 有變才寫進儀表板的頁面資料庫
  18:20 那次另外替需注意的信寫摘要、必要時推播
  「立即同步」按鈕也是觸發它：先叫你電腦同步，再推上來
                                     │
BetterElearning 儀表板               ▼
  從頁面資料庫讀資料，開著時資料一寫進去就自己更新
  需注意的新信還沒摘要的話，頁面會用你的 Claude 額度補寫，存回資料庫
```

抓資料全部在你自己的電腦上跑，原因有兩個：

- e學苑的 Moodle web service 是關的，拿不到 API token，只能模擬一般登入。
- Claude 的雲端環境連不到 `*.ndhu.edu.tw`。

所以 Claude 要推資料時，**你的電腦要開著、登入，而且 Claude 桌面版要開著**。電腦關著時儀表板照樣打得開，只是停在最後一次推上去的資料；下次開機後本機會先補同步，下一輪排程（或你按「立即同步」）就推上去。

### 資料放在哪

| 位置 | 內容 |
|---|---|
| 你電腦的 `_moodle` 資料夾 | `latest.json`、`mail.json`（最近 14 天的信，含內文前 1200 字）、`state.db`（講義索引）、`status_*.json`（上次同步結果）、log |
| 你電腦的 `<科目>\教材` | 下載下來的講義 |
| 頁面資料庫（claude.ai 上，跟著儀表板） | `dashboard/data`（課程、作業）、`dashboard/mail`（只有未讀信、開頭 260 字和摘要）、`dashboard/settings`（上次推送的時間和結果） |

## 隱私：什麼會交給 Claude、什麼不會

| 不會離開你電腦 | 會給 Claude 讀 |
|---|---|
| e學苑密碼、Gmail 應用程式密碼（Windows DPAPI 加密，換帳號或換電腦都解不開） | `latest.json`：課程、作業、教材清單、同步時間 |
| 講義檔案本身（除非你叫 Claude 去讀） | `mail.json`：最近 14 天信件的寄件者、主旨、內文前 1200 字（排程在你電腦上讀；進頁面資料庫的只有未讀信的開頭 260 字） |

儀表板是你 claude.ai 帳號下的私人頁面，除非你自己分享，不然別人看不到。資料放在頁面自己的資料庫裡，只有你（和替你跑排程的 Claude）寫得進去；分享出去的人只能看。

不想讓某些信給 Claude 讀，有兩種做法：

- 把寄件者加進 `config.ini` 的 `skip_senders`。
- 把 `include_body` 設成 `false`，就只給寄件者和主旨。

## 點教材直接開檔（選用）

網頁本身不能開你電腦上的檔案，所以這個功能要執行 `install_open_local.ps1`：它會在 Windows 登記一種 `betterel:` 連結，點了就交給 `open_local.ps1` 處理。

- **開得了什麼**：只開你課程資料夾裡的文件檔（pdf / ppt / pptx / doc / docx / xls / xlsx / txt / md / csv），zip 和資料夾會在檔案總管打開。
- **exe 等其他檔案**：只會在檔案總管裡標出來，一律不執行。
- **在哪裡能用**：只在 Chrome / Edge 開儀表板時有效。Claude 桌面版裡點了只會複製路徑。
- **移除**：`powershell -ExecutionPolicy Bypass -File .\install_open_local.ps1 -Uninstall`

## 立即同步和 betterel

「立即同步」要在 `_moodle` 執行過 `install_mcp.ps1`（先完全關掉 Claude 桌面版）：它把 `betterel_mcp.py` 註冊成桌面版的本機 MCP server。排程裡的 Claude 透過它叫你電腦跑同步腳本。

- `betterel` 能做三件事：讀同步結果、讀指定未讀信的內文、在背景啟動 `moodle_sync.py` / `mail_sync.py`。不接受其他指令，不碰密碼檔。
- 按鈕背後是觸發「BetterElearning 更新」排程，每按一次會用掉一次 Claude 排程的額度。
- 頁面裡還留著「桌面版直接讀 betterel、每 2 分鐘更新」的程式，但現在平台不讓頁面宣告本機 MCP server 的權限，所以沒有作用；平台開放後補宣告就能用。
- 移除：`powershell -ExecutionPolicy Bypass -File .\install_mcp.ps1 -Remove`

## 更新外掛

這個 repo 更新後，在 Cowork 的外掛設定更新 **better-ndhu-elearning**：

- **儀表板**：不用做任何事。下一次每日更新時，Claude 發現外掛裡的範本比你的頁面新，會自動換成新版，作業、信件摘要這些資料都會保留。（從 0.2 以前升上來的頁面，這次會順便開好頁面資料庫，之後每天只更新資料、不再重新發佈頁面。）
- **電腦上的程式**（`_moodle` 裡的 `.py` / `.ps1`）：跟 Claude 說「更新 BetterNDHUelearning 的程式」，它會只換程式檔，不動你的 `config.ini` 和資料。
- **從 0.3 升到 0.4**：換完程式後要重跑 `install_task.ps1`、`install_mail_task.ps1`（換成新的同步頻率）和 `install_mcp.ps1`（betterel 多了工具，跑完重開桌面版），再請 Claude 把 Claude 排程改成「BetterElearning 更新」那套（見 setup 技能第 6 步）。

## 常見問題

- **登入失敗**：確認 `config.ini` 的 `username` 是學號。改過 gms 密碼的話，要重跑 `python set_password.py`。
- **排程有沒有在跑**：在 `_moodle` 資料夾執行 `powershell -ExecutionPolicy Bypass -File .\check_task.ps1`。
- **手動同步一次**：按儀表板的「立即同步」，或在 `_moodle` 跑 `python moodle_sync.py --force`（完整）/ `--light`（只看作業）。
- **按了立即同步但沒反應**：看儀表板「系統」分頁最後一行的結果。寫「電腦沒開」就是 Claude 連不到你電腦（桌面版沒開或電腦睡了）；寫權限錯誤的話，到 Claude 桌面版的排程設定確認排程能用你的課程資料夾。
- **不想同步某些課**：`config.ini` 的 `skip_courses` 填課號，用逗號分隔。
- **學校改了 e學苑版面導致抓不到**：看 `latest.json` 的 `errors`，歡迎開 issue 或送 PR。

## 請客氣一點

每個請求之間預設停 0.6 秒。每 30 分鐘的輕量同步大約只有十來個請求，下載講義的完整同步一天只跑一次。請不要把 `request_delay` 調小、也不要把排程改得更密，別把學校伺服器當沙包。也請遵守學校的資訊使用規範。

## 開發

```
plugins/better-ndhu-elearning/
  skills/setup/            安裝流程；files/ 是會被複製到使用者 _moodle 資料夾的程式
  skills/daily-update/     儀表板更新流程 (完整 / 快速模式)；scripts/build.py 產生儀表板；assets/dashboard.html 是範本
                           (改了範本記得調高裡面的 TEMPLATE_VERSION，使用者的頁面才會自動升級)
  skills/search-materials/ 講義全文檢索
```

改信件分類規則時，`dashboard.html` 的 JS、`build.py`、`daily-update/SKILL.md` 三個地方要一起改。

MIT License。
