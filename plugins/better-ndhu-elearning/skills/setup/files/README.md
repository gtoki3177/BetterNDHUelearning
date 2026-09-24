# BetterNDHUelearning — 本機同步程式

> 這個資料夾是 BetterNDHUelearning 外掛裝進來的。一般不用看這份，有問題直接問 Claude。


每天自動把 Moodle 上的作業、課程安排、教材抓下來，存成分科目的資料夾 + 一個可全文檢索的索引。

## 為什麼是這種架構

兩個限制逼出來的：

1. **e學苑的 Moodle web service 是關閉的** — `login/token.php` 回 `enablewsdescription`，所以拿不到 API token，只能用一般的登入 session。
2. **Claude 的雲端容器連不到 `*.ndhu.edu.tw`** — 出網被組織政策擋掉，所以抓取不可能在雲端跑。
結論：抓取邏輯是一支**純本機、不依賴 Claude 的 Python 腳本**，由 Windows 工作排程器叫。Claude 只負責事後讀 `latest.json` 更新儀表板、以及幫你查索引。

## 檔案

| 檔案 | 幹嘛的 |
|---|---|
| `moodle_sync.py` | 主程式。登入 → 抓資料 → 下載 → 建索引 → 寫 `latest.json` |
| `set_password.py` | 用 Windows DPAPI 加密存密碼到 `cred.dat`。**只有你會跑這支** |
| `search.py` | 教材全文檢索 CLI |
| `setup.ps1` | 裝套件 + 叫 `install_task.ps1` 註冊工作排程器任務 |
| `install_task.ps1` | 註冊 Moodle 同步的兩個排程 (登入時 + 每天 12:00) |
| `check_task.ps1` | 看排程上次執行結果和下次時間 |
| `install_open_local.ps1` / `open_local.ps1` / `open_local.vbs` | (選用) 讓儀表板點教材直接開檔 |
| `config.ini` | 設定：帳號、學期、要不要下載、跳過哪些課、儀表板設定 (從 `config.example.ini` 產生) |
| `state.db` | SQLite：已下載檔案紀錄 + FTS5 全文索引。`SCHEMA_VERSION` 一改就會自動砍掉索引重建（檔案本身留著） |
| `latest.json` | 最新快照，給 Claude 讀 |
| `log.txt` | 執行紀錄 |
| `cred.dat` | 加密後的密碼。**不要傳給任何人，包括 Claude** |

## 安裝

```powershell
# 在 _moodle 資料夾按右鍵 → 在終端中開啟
powershell -ExecutionPolicy Bypass -File .\setup.ps1

# 然後填 config.ini 的 username，接著
python set_password.py
python moodle_sync.py --force
```

## 資料怎麼抓的

- **課程清單 / 行事曆**：走 Moodle 站內 AJAX `lib/ajax/service.php?sesskey=…`，呼叫
  `core_course_get_enrolled_courses_by_timeline_classification` 和
  `core_calendar_get_action_events_by_timesort`。回乾淨 JSON，不是爬 HTML。
  這些 endpoint 不需要 web service 開啟，只要有登入 session + sesskey。
- **課程內容**：解析 `course/view.php?id=N`，找 `li.activity.modtype_*`。
- **作業**：解析 `mod/assign/view.php?id=N` 的到期日與繳交狀態表格。
- **教材下載**：`mod/resource/view.php?id=N` 會 302 到 `pluginfile.php/.../content/<rev>/檔名`。
  URL 裡的 `<rev>` 是 Moodle 的檔案版本號，老師換檔就會變 —— 所以拿整個 URL 當主鍵，
  沒變就不重抓，換版了就自動重抓重建索引。
- **索引**：pdfplumber 逐頁 / python-pptx 逐張投影片 / python-docx 逐段，
  寫進 SQLite FTS5。`.zip` 會自動解開再索引裡面的檔案。
- **舊版 `.ppt`**：`python-pptx` 只讀 OOXML 的 `.pptx`，但老師很常直接發課本原廠的
  PowerPoint 97-2003 `.ppt`。
  所以 `extract_ppt()` 自己解 OLE2 容器裡的 `PowerPoint Document` 串流：走 record
  樹（header 8 bytes：recVer/recInstance、recType、recLen；recVer 為 `0xF` 代表容器要遞迴），
  文字在 `TextCharsAtom`（UTF-16LE）與 `TextBytesAtom`（cp1252，一 byte 一字），
  遇到 `Slide` 容器（`0x03EE`）就換頁。母片佔位字串與 `___PPT12` 之類的內部標記會濾掉。
  頁碼是串流出現順序，實務上等於投影片順序，但不保證。只需要 `olefile`，沒有外部程式。
- **中文檢索**：FTS5 的 `unicode61` 會把一整串中文當成單一 token，
  所以「虛擬記憶體分頁機制」裡搜「分頁」原本會搜不到。
  解法是索引時把每個 CJK 字元拆開存（`body` 欄），查詢時做同樣處理再當片語查，
  顯示用的原文另外存在 `raw` 欄。英文照常走一般 token，`term*` 前綴一樣能用。

## 密碼

`set_password.py` 用 `win32crypt.CryptProtectData` 加密，綁你的 Windows 使用者帳戶。
換帳戶、換電腦、複製 `cred.dat` 到別台都解不開。

`moodle_sync.py` 裡密碼只在 `login()` 用一次，用完馬上 `self._password = None`。
不會進 `log.txt`，不會進 `latest.json`。

## 常見狀況

**登入失敗** → 先確認 `config.ini` 的 username 對、密碼有重設（gms 改密碼後要重跑 `set_password.py`）。

**某門課抓不到 / 解析錯** → 看 `latest.json` 的 `errors` 陣列，裡面有課名和原因。
學校改 Moodle 版面的話，`parse_course_page()` 的 selector 要跟著改。

**索引是空的** → 學期初本來就空的，老師還沒上傳。`python search.py --list` 看目前索引了什麼。

**不想同步軍訓體育那些** → `config.ini` 的 `skip_courses` 填課號，逗號分隔。

**手動跑一次** → `python moodle_sync.py --force`（不加 `--force` 的話同一天跑第二次會直接跳過）。

**拆掉排程** →
```powershell
Unregister-ScheduledTask -TaskName 'NDHU Moodle Sync (logon)','NDHU Moodle Sync (daily)'
```

---

# 學校信箱同步 (mail_sync.py)

gms 信箱就是 Gmail，所以走 IMAP 抓，跟 Moodle 那套同一個模式：**本機跑、密碼 DPAPI 加密、
產出一份 JSON 給 Claude 讀**。Claude 不會碰你的信箱帳密，只讀 `mail.json`。

## 檔案

| 檔案 | 幹嘛的 |
|---|---|
| `mail_sync.py` | 主程式。IMAP 登入 → 抓最近的信 → 寫 `mail.json` |
| `set_mail_password.py` | 把 Gmail **應用程式密碼** 加密存成 `mail_cred.dat`。**只有你會跑這支** |
| `install_mail_task.ps1` | 註冊每天 12:20 的工作排程（Moodle 那支是 12:00，錯開） |
| `mail.json` | 最新快照，給 Claude 讀 |
| `mail_log.txt` | 執行紀錄 |
| `mail_cred.dat` | 加密後的應用程式密碼。**不要傳給任何人，包括 Claude** |

設定在 `config.ini` 的 `[mail]` 段。

## 安裝

```powershell
# 在 _moodle 資料夾按右鍵 → 在終端中開啟
pip install pywin32                  # 已經裝過就跳過

# 1. 先去拿應用程式密碼（用 gms 帳號登入 Google）
#    myaccount.google.com → 安全性 → 開「兩步驟驗證」
#    myaccount.google.com/apppasswords → 取名 moodle-mail → 產生 16 碼
python set_mail_password.py

# 2. 驗證登入（只連線，不寫檔）
python mail_sync.py --test

# 3. 正式跑一次，然後註冊排程
python mail_sync.py
powershell -ExecutionPolicy Bypass -File .\install_mail_task.ps1
```

## 常用指令

```powershell
python mail_sync.py --days 30 --limit 120   # 抓久一點
python mail_sync.py --unread-only           # 只抓未讀
python mail_sync.py --no-body               # 只要寄件者+主旨
python mail_sync.py --stats                 # 看上次抓到什麼
```

## mail.json 裡有什麼

每封信：`date`（+08:00）、`from_name` / `from_addr`、`subject`、`unread`、`starred`、
`attachments`（檔名清單）、`body`（純文字，預設截到 1200 字）、`keywords`（命中
`highlight_keywords` 的字）、`link`（直接開那封信的 Gmail 連結）、`new`（上次同步之後才出現的）。
`stats` 有總數 / 未讀 / 新增 / 有附件 / 關鍵字命中數。

附件只記檔名不下載；超過 `max_fetch_kb`（預設 512KB）的信只抓標頭不抓內文。

## 注意

- **一定要用應用程式密碼**，Gmail 從 2022 起就不收一般密碼登入 IMAP。
  要用應用程式密碼就得先開兩步驟驗證。
- 學校的 Google Workspace 如果把 IMAP 或應用程式密碼鎖起來，這條路就走不通，
  只能改用「gms 設定自動轉寄到個人 Gmail」那招。
- `body` 是純文字化的內容，Claude 讀得到信件內文 —— 不想讓某些信進 JSON 的話，
  把寄件者加進 `config.ini` 的 `skip_senders`，或把 `include_body` 設成 false。
- 信箱改密碼 / 撤銷應用程式密碼之後要重跑 `set_mail_password.py`。

**拆掉排程** →
```powershell
Unregister-ScheduledTask -TaskName 'NDHU Mail Sync (daily)'
```
