---
name: daily-update
description: 每天更新東華大學 e學苑 / gms 信箱的 BetterElearning 儀表板。讀使用者電腦上同步程式產生的 latest.json 和 mail.json，替需注意的信寫摘要，把資料寫進儀表板的頁面資料庫（範本有新版時才重新發佈），必要時推播通知。Use when a scheduled task says to run the BetterNDHUelearning daily update, or the user says「更新儀表板」「更新 BetterElearning」「跑一次每日更新」「update my e學苑 dashboard」.
---

# BetterElearning 每日更新

使用者電腦上的 `moodle_sync.py`（抓 e學苑 → `latest.json`）和 `mail_sync.py`（IMAP 讀 gms 信箱 → `mail.json`）由 Windows 工作排程器每天跑。這個技能只負責把兩份 JSON 讀進來、寫好信件摘要，更新到儀表板的資料庫。

- 不要自己連 `elearn4.ndhu.edu.tw` 或 `imap.gmail.com`：雲端環境連不到，抓資料永遠是本機腳本的事。
- 不要碰 `cred.dat`、`mail_cred.dat`（加密的密碼），不要讀、不要搬。
- 排程觸發時沒人在旁邊：不要問問題，自己判斷後做完。

## 0. 找到設定檔

設定檔是 `<根資料夾>\_moodle\config.ini`，排程的指示裡會寫完整路徑。沒寫的話，在使用者連線的資料夾裡找 `_moodle\config.ini`。

讀 `[dashboard]` 段落：

- `artifact_url`：儀表板網址。空的就是還沒設定，告訴使用者先跑 setup 技能，然後結束。
- `notify`：要不要推播。

`config.ini` 裡沒有密碼，可以讀。

## 1. 把檔案拿進工作環境

需要 `_moodle` 裡的 `latest.json`、`mail.json`、`config.ini`，有需要時也可以看 `log.txt` 和 `mail_log.txt`。

- **雲端 session**：用 `device_stage_files` 把檔案搬進容器。
- **本機 session**：直接讀。

各種狀況的處理：

| 狀況 | 處理 |
|---|---|
| 連不到使用者電腦（電腦沒開） | 回報一句「今天連不到你電腦，沒更新」就結束，不要重試。 |
| `latest.json` 不存在 | 課程區維持上一版，信箱照常更新，回報「Moodle 同步還沒跑過」。 |
| `mail.json` 不存在 | 只更新課程區，信箱維持上一版。如果使用者沒開信箱功能，就不用提。 |

## 2. 讀頁面資料庫裡的上一版資料

從 0.3 起，儀表板的資料放在頁面自己的資料庫（`dashboard/data`、`dashboard/mail` 兩份文件），頁面開著時會自己更新。**平常不用讀、也不用重新發佈 HTML。**

用 ArtifactData 讀出來存成檔案：

- `action: "list"`、`url: <artifact_url>`、`collection: "dashboard"`、`out_dir: prev`

記下兩份文件的 `version`，第 5 步寫回去時要用。

- 讀到了 → 走下面的「一般流程」。
- `dashboard` 是空的（沒有 `data` 文件）→ 這是 0.3 之前的舊頁面或第一次：走「完整發佈流程」，把頁面升級並開好資料庫。

## 3. 第一輪：產生資料和待辦清單

用本技能資料夾裡的 `scripts/build.py`（Python 3.9+，只用標準函式庫）。

**一般流程**（只寫資料庫）：

```bash
python3 <本技能資料夾>/scripts/build.py \
  --prev-dir prev --latest latest.json --mail mail.json --config config.ini \
  --db-out db --report report.json
```

**完整發佈流程**（舊頁面、第一次，或 report 說要 `republish`）：先用 Artifact 工具 `action: "read"`（不帶 `path`）讀 `artifact_url`，依工具要求把整份 HTML 讀完，再：

```bash
python3 <本技能資料夾>/scripts/build.py \
  --page <讀到的 HTML> --prev-dir prev --latest latest.json --mail mail.json --config config.ini \
  --out new.html --db-out db --report report.json
```

`build.py` 會自動處理這些：

- 作業的 `first_seen`：沿用同一個網址上一版的值，新作業填這次的同步時間。
- 教材本機路徑（`local`）：latest.json 有就用，沒有就沿用上一版。
- 開課單位：依 `dept_map`。
- 全民國防課名：縮成簡寫。
- 教材索引統計。
- 錯誤訊息：顯示在「系統」分頁。
- 信件：只留未讀的，並算好 excerpt。信件文件太大（超過約 240 KiB）時，先拿掉系統通知和校園公告的摘錄。
- 已寫過的信件摘要：同一個 uid 會沿用。
- 上一版資料：頁面資料庫優先，沒有才用頁面內嵌的。
- **範本升級**：外掛裡 `assets/dashboard.html` 的 `TEMPLATE_VERSION` 比頁面新時，完整發佈流程會自動換成新範本（資料照上面的規則沿用）；一般流程則在 report 裡標 `republish`。不想升級就加 `--no-upgrade`。

讀 `report.json`：

- `need_summary`：需要寫摘要的信。已經去重，同寄件者同主旨只留最新一封。
- `new_focus_mail`：這次新進、而且屬於「需注意」的信。
- `due_soon_unsubmitted`：3 天內到期、還沒交的作業。
- `upcoming`、`new_assignments`、`new_files`、`moodle_errors`、`mail_errors`。
- `republish`：一般流程才會有，代表外掛的範本比頁面新。寫完資料庫後，照「完整發佈流程」再跑一次、發佈 HTML。
- `template_upgraded`：完整發佈流程把頁面換成了新範本（`from` → `to`）。
- `mail_trimmed` / `doc_too_big`：信件太多被修剪 / 還是超過上限。後者要在回報裡提一句。

## 4. 替需注意的信寫摘要

對 `need_summary` 裡的每一封信，照 `body` 的內容寫一份，存成 `summ.json`：`{"<uid>": {"summary": ..., "action": ..., "flash": [...]}}`。

- `summary`：繁體中文 1–3 句，講清楚這封信在說什麼、提到哪些日期。
- `action`：一句「使用者要做的事＋期限」。沒有要做的事就省略這個欄位。
- `flash`：2–3 個超短句，每句大約 8 個中文字以內。滑鼠移到信上時會用滿版大字輪播：
  - 第一句講這封是什麼，例如「五年修讀申請」。
  - 第二句講最重要的日期或要做的事，例如「10/07 截止」「10/27 交第一篇」。
  - 第三句可省略。
- 期限已經過了，要寫「已過」。
- `body` 是空的（內文抓不到）：照主旨寫，並註明細節看原信。
- 不要推測使用者本人的身分、資格或個人狀況。像是「符合資格才需要處理」這種條件，照信裡的原話寫就好。
- 行銷信、電子報：summary 寫一句「純推廣，不用處理」就好，不用寫 action。

寫完用同樣的參數再跑一次 `build.py`，加上 `--summaries summ.json`。這次 `need_summary` 應該是空的。

## 5. 寫回資料庫（和必要時發佈）

**寫資料庫**：用 ArtifactData `action: "batch"`，一次寫兩份：

```json
{"url": "<artifact_url>", "action": "batch", "writes": [
  {"op": "set", "collection": "dashboard", "doc_id": "data", "file_path": "db/data.json", "if_version": <第 2 步讀到的 data version>},
  {"op": "set", "collection": "dashboard", "doc_id": "mail", "file_path": "db/mail.json", "if_version": <第 2 步讀到的 mail version>}
]}
```

- 第一次（第 2 步沒讀到文件）就不要帶 `if_version`。
- 版本不符被拒絕：代表有別的 session 剛寫過，重新做第 2、3 步再寫，不要硬蓋。
- 寫進去之後，開著的儀表板會自己換成新資料，不用發佈。

**發佈 HTML**（只有完整發佈流程）：用 Artifact 工具發佈 `new.html`，要先發佈、再寫資料庫：

- 一定要帶 `url: <artifact_url>`，不帶會變成另一個新頁面。
- 不要帶 `icon`。
- 舊頁面第一次升級（第 2 步 `dashboard` 是空的）時帶 `capabilities: {"db": {"rules": [{"path": "", "read": "view", "write": "admin"}]}}`：讓頁面讀得到資料庫，而且只有擁有者（和 Claude 代擁有者）能寫。
- 其他時候不要帶 `capabilities`。省略時會保留頁面原本的權限；傳 `{}` 或只傳一部分，反而會把沒寫到的權限清掉，例如使用者在本機 session 開過的即時模式（`host:betterel`）。

## 6. 回報

用兩三句話回報，語氣輕鬆、繁體中文。內容包括：

- 有沒有新作業。
- 最近的截止日剩幾天。
- 有沒有新下載的教材。
- 信箱有沒有值得注意的新信。

什麼都沒變就講一句「沒新東西」，不要硬湊。這次有升級範本（`template_upgraded`）的話，多講一句「儀表板順便更新到新版了」。

## 7. 推播（`notify = true` 才做；沒事就安靜）

遇到下列任一情況，用 PushNotification 推播：

- `due_soon_unsubmitted` 不是空的：講清楚是哪一門、哪一份、剩幾天。
- `new_focus_mail` 不是空的：附上你幫它寫的 action。
- 同步失敗：`moodle_errors` 或 `mail_errors` 有內容，或 `moodle_generated_at` 已經超過兩天沒更新。

通知內容用 `<routine_summary>` 包起來，第一句話講最重要的那件事。

## 「需注意」的定義（和頁面 JS、build.py 一致，改規則三處要一起改）

- **系統通知**：寄件者是 `no-reply@accounts.google.com`、`elearn@gms.ndhu.edu.tw` 或 config 的 `noise_senders`；或主旨含「安全性快訊 / 安全性警示 / 新登入紀錄 / newsletter」。
- **校園公告**：主旨含「批次寄送」；或寄件者是 `announce@gms.ndhu.edu.tw`，但命中的強關鍵字少於兩個。
- **需注意**：其他所有信，加上命中兩個以上強關鍵字的 announce 信。
- **強關鍵字**：截止 / 逾期 / 繳費 / 選課 / 停課 / 補課 / 調課 / 成績 / 獎學金 / 考試 / 註冊 / 重要。
