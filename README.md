# BetterNDHUelearning

把東華大學 e學苑（Moodle）和 gms 學校信箱整合成 **BetterElearning**：作業倒數、信件分類和摘要、課程講義，手機 app 和電腦網頁版都能看。

> 這不是學校官方的東西，也和學校沒有任何關係。它用你自己的帳號登入、只抓你自己的資料。

## 功能

- **任務**：待交作業倒數、日曆、作業詳情，依截止日或課程排序、依繳交狀態篩選。
- **信箱**：未讀信自動分成系上、課程、學校單位、校園公告、系統通知。需要注意的信會標出關鍵字，也可以接語言模型寫中文摘要和要做的事。
- **課程 / 教材**：每門課的活動和講義連結。選用：講義下載到電腦、建全文索引，電腦版點教材直接開檔。
- **釘選、打勾、備忘**：手機和電腦即時同步，離線也能改。

## 怎麼運作

```
GitHub Actions（你自己的私有 repo，每小時一次，電腦不用開）
  登入 e學苑和 gms 信箱 → 整理作業、課程、未讀信 → (選用) 替需注意的新信寫摘要
                                   │
你自己的 Firebase（Google 的免費資料庫）  ▼
                                   │
Android app、網頁版 betterel.web.app  ▼  登入後讀你自己的資料，離線時用上次的
```

- **大家共用同一個 app 和網頁**，但資料各自放在自己的 Firebase。用安裝時產生的專屬連結或 QR code 登入，才會連到你的資料。別人，包括這個 repo 的作者，都看不到。
- **同步程式在這個 repo 的 [`engine/`](engine/)**。你的私有 repo 引用它的 `v1` 版，爬蟲修好後所有人下一個整點就更新。

## 安裝

需要一個 GitHub 帳號和一個個人 Google 帳號（建 Firebase 用，不要用 gms），都免費。核心功能任何作業系統都能裝；講義下載只支援 Windows。

1. **裝工具**。Windows 在 PowerShell 一行一行跑，Mac 用 `brew install git gh node python`。裝完把終端機關掉重開：

   ```powershell
   winget install Git.Git
   winget install GitHub.cli
   winget install OpenJS.NodeJS.LTS
   winget install Python.Python.3.12
   ```

2. **下載這個 repo**：`git clone https://github.com/gtoki3177/BetterNDHUelearning.git`，或在這個頁面按 **Code → Download ZIP** 解壓縮。

3. **跑安裝腳本**，照畫面做，大約 15 分鐘：

   ```powershell
   cd BetterNDHUelearning
   python install.py
   ```

   腳本會依序做這些事。中途關掉沒關係，再跑一次會從停下的地方接著做（進度在 `~/BetterElearning/install-state.json`）：

   1. 檢查工具，登入 GitHub 和 Firebase。
   2. 問學號、系所、要不要同步信箱、登入 app 用的 email。
   3. 建你自己的 Firebase 專案和資料庫。
   4. 請你在 Firebase 網頁上點兩個地方：開一個登入帳號、下載金鑰。腳本會印出網址等你。
   5. 建你自己的私有同步 repo `betterel-sync`，請你輸入 e學苑密碼（和信箱的應用程式密碼）。密碼直接交給 GitHub 加密保存，不會寫進任何檔案。
   6. 跑第一次同步，產生你的專屬連結和 QR code。
   7. （Windows）問要不要把講義下載到這台電腦。
   8. 問要不要設定信件摘要。

4. **裝 app**：
   - **Android**：到 [Releases](https://github.com/gtoki3177/BetterNDHUelearning/releases/latest) 下載 `BetterElearning.apk` 安裝，再用相機掃 QR code。
   - **電腦、iPhone**：用 Chrome 或 Edge 開專屬連結（存在 `~/BetterElearning/setup-link.txt`），登入後網址列可以「安裝應用程式」。

   登入用第 4 步在 Firebase 建的 email 和密碼，不是學校帳號。

### 信箱的應用程式密碼

gms 是 Google 帳號，要用「應用程式密碼」才能讓程式讀信：

1. 用 gms 登入 <https://myaccount.google.com/security>，開啟「兩步驟驗證」。
2. 到 <https://myaccount.google.com/apppasswords>，名稱隨便填，按建立，會出現 16 個英文字母。安裝腳本問的時候貼上。

## 選用功能

想加裝或改設定時，再跑一次 `python install.py`，做過的步驟會跳過。

### 講義下載和全文檢索（Windows）

每門課的講義會下載到你選的資料夾、建全文索引，之後每天第一次開機自動下載新的。電腦版 app 點教材會直接開檔，教材分頁會顯示索引統計。全文搜尋用 `_moodle` 資料夾裡的 `search.py`。

- 作業、繳交狀態、行事曆一律由雲端同步負責，電腦每天只登入 e學苑一次，只為了下載講義。
- **點教材直接開檔**：安裝時會問要不要登記 `betterel:` 連結。它只開課程資料夾裡的文件（pdf、ppt、doc、xls、txt…），zip 和資料夾用檔案總管打開，其他檔案一律不執行。移除：在 `_moodle` 跑 `powershell -ExecutionPolicy Bypass -File .\install_open_local.ps1 -Uninstall`。

### 信件摘要

需注意的新信可以交給語言模型，寫成 1–3 句中文摘要、一句「要做的事＋期限」和 2–3 個幻燈片短句。沒設的話，app 會用信裡抓到的關鍵字。

| 方式 | 在哪裡跑 | 需要 |
|---|---|---|
| 雲端 API | GitHub 每小時同步完順便寫，電腦不用開 | OpenAI、Gemini、OpenRouter、Anthropic 或其他 OpenAI 相容服務的 API 金鑰，依用量付費 |
| 本地模型 | 你的電腦，跟講義下載同一個排程 | Ollama 或 LM Studio，電腦開著才會寫 |
| 指令列 agent | 同上 | 任何從 stdin 讀提示詞、把結果印到 stdout 的指令，例如 `codex exec -`、`claude -p`、`ollama run <模型>` |

- 安裝腳本的最後一步會帶你設定。
- **雲端 API** 的設定是同步 repo 的四個 secrets：`SUMMARY_MODEL`、`SUMMARY_API_KEY`、`SUMMARY_PROVIDER`（`openai` 或 `anthropic`）、`SUMMARY_BASE_URL`。
- **本地模型和 agent** 的設定在 `_moodle\config.ini` 的 `[summaries]` 段落，說明在 `config.example.ini`。金鑰放環境變數 `SUMMARY_API_KEY`，不寫進設定檔。
- 試跑（只印出結果、不存）：

  ```powershell
  python summaries.py auto --config config.ini --dry-run
  ```

- 交給 agent 時，請用不能改檔案、不能執行指令的唯讀模式。信件內容是外人寫的，程式會提醒模型把它當資料，但沒辦法保證。
- 也可以用 `need` 和 `write` 兩個指令自己接任何 agent 流程：`need` 印出要寫的信，寫好的 JSON 交給 `write` 存回去。格式寫在 `summaries.py` 開頭。

### 立即同步

app 選單裡的「立即同步」會叫 GitHub 馬上跑一次同步，大約 1–2 分鐘。它需要一組只能觸發你同步 repo 的 GitHub token：

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token。
2. Repository access 只選 `betterel-sync`；Permissions 把 **Actions** 設成 Read and write；到期日最長一年。
3. 在 app 的「設定」頁填 repo（`<你的帳號>/betterel-sync`）和 token。手機設好，電腦也能用。

## 更新

- **同步程式**：自動。你的同步 repo 引用 `engine-sync.yml@v1`，維護者把 `v1` 標籤移到新版後，下一個整點就生效。
- **app**：畫面自己熱更新。選單出現「有新版 app」時，到 Releases 下載覆蓋安裝，資料和登入都會保留。
- **網頁版**：重新整理就是新版。
- **電腦上的程式**（講義下載、信件摘要）：下載新版的這個 repo，再跑一次 `python install.py`。它會換掉 `_moodle` 裡的程式、重新註冊排程，`config.ini`、密碼和講義索引都不動。

## 移除

```powershell
python install.py --uninstall
```

每一項都會先問：電腦上的排程、點教材開檔的連結、`_moodle` 資料夾、雲端同步、安裝紀錄。各科資料夾裡的講義檔案會留著。GitHub 的同步 repo 和 Firebase 專案要到網頁上刪，它會給你網址。

## 隱私

| 東西 | 放在哪 | 誰看得到 |
|---|---|---|
| e學苑密碼、信箱應用程式密碼 | 你同步 repo 的 GitHub Secrets（加密）；有下載講義的話，另一份用 Windows DPAPI 加密存在你電腦 | 只有 GitHub Actions 執行時讀得到 |
| 作業、課程、最近 14 天的信 | 你自己的 Firebase | 只有你的登入帳號 |
| Firebase 金鑰 `firebase-sa.json` | `~/BetterElearning/`、你的同步 repo secrets | 不要傳給任何人 |
| 信件摘要 | 需注意的信的寄件者、主旨、內文前 1500 字會送到你選的模型 | 你選的服務商（本地模型則不離開你電腦） |

## 常見問題

- **app 登入了但什麼資料都沒有**（右上角寫「雲端還沒有資料」）：在當初安裝的那台電腦、repo 資料夾裡跑 `python install.py --check`。它會列出最近幾次同步的結果、同步寫進哪個帳號、資料庫裡哪個帳號有資料，指出哪裡不對；最常見的是安裝時貼錯了 UID，它會問要不要直接改好。
- **e學苑登入失敗**：學號或密碼打錯。刪掉 `install-state.json` 裡的 `"pw_moodle"` 那行再跑一次 `install.py`。學校密碼改了：`gh secret set MOODLE_PASSWORD --repo <你的帳號>/betterel-sync`。
- **信箱出現 `Application-specific password required`**：存成一般密碼了，要用 16 碼應用程式密碼。刪掉 `"pw_mail"` 那行再跑一次。
- **同步失敗**：到 `https://github.com/<你的帳號>/betterel-sync/actions` 看最近一次的紀錄，或跑 `gh run view --repo <你的帳號>/betterel-sync --log-failed`。
- **講義下載有錯**：看 `<課程資料夾>\_moodle\log.txt` 最後幾行。排程有沒有在跑：在 `_moodle` 跑 `check_task.ps1`。
- **每隔一陣子閃一下黑色小視窗**：那是講義下載的排程「NDHU Moodle Sync」，在 Windows 的「工作排程器」裡，不在工作管理員。舊版的排程會用到有視窗的 Python；下載新版再跑一次 `python install.py` 就會換掉。
- **不想同步某些課**：`config.ini` 的 `skip_courses` 填課號，用逗號分隔。
- **學校改了 e學苑版面導致抓不到**：歡迎開 issue 或送 PR。

## 請客氣一點

每個請求之間預設停 0.6 秒，雲端每小時同步一次，講義一天只下載一次。請不要把 `request_delay` 調小、也不要把排程改得更密，別把學校伺服器當沙包。也請遵守學校的資訊使用規範。

## 其他裝法

用 Claude 付費方案的話，另有一種只在 Claude 裡看儀表板、電腦開著才更新的輕量版，見 [docs/lite.md](docs/lite.md)。

## 開發

```
install.py                 安裝腳本的入口 (呼叫 files/install_full.py)
engine/                    雲端同步程式；.github/workflows/engine-sync.yml 是大家的同步 repo 呼叫的 reusable workflow
plugins/better-ndhu-elearning/
  skills/setup/files/      安裝腳本和電腦端程式 (講義下載、全文檢索、排程)
  skills/setup/cloud-template/
                           安裝時推到使用者私有 repo 的內容；cloud/summaries.py 是信件摘要
  skills/                  Claude 外掛 (輕量版和選用設定用)
```

App 和網頁版的原始碼在另一個 repo。

MIT License。
