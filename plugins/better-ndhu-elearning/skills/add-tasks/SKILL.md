---
name: add-tasks
description: 把信件、講義、公告裡提到的考試、截止日、重要行程，加進使用者 BetterElearning app 的任務欄（備忘）。Use when the user says things like「幫我把這些截止日加進任務」「把信裡的考試時間放到 BetterElearning」「加到我的任務欄」「整理成任務」, or after summarizing course mails / lecture notes that contain dates the user wants to track, for a user who has BetterNDHUelearning set up on this computer.
---

# 加任務到 BetterElearning 任務欄

使用者的 BetterElearning app 有一個任務欄，裡面是 e學苑的作業和使用者自己加的「備忘」。這個 skill 用電腦上的 `tasks.py` 把新的備忘直接寫進雲端，app 打開（或正在開著）就會出現。

## 找到 tasks.py

在使用者連線的資料夾底下找 `_moodle\` 資料夾：裡面同時有 `tasks.py`、`cloud_push.py`、`config.ini`。找不到就問使用者課程資料夾放在哪裡。

如果 `_moodle\` 裡沒有 `tasks.py`（舊版安裝），請使用者在安裝的那台電腦跑一次 `python install.py --check` 更新，或從 BetterNDHUelearning 的 `engine/tasks.py` 複製一份過去。

## 資料從哪裡來

- **信件**：用 `tasks.py mail` 讀，不要開瀏覽器、不要連信箱。信件是雲端同步整理好的（最近 14 天，讀過的也在，`read: true`），每封有主旨、寄件人、日期、關鍵字、內文前段摘要（`excerpt`）和附件檔名：

  ```
  python tasks.py mail --config config.ini
  python tasks.py mail --config config.ini --months 2   # 連最近兩個月的封存一起讀（使用者在 app 裡載入過的月份才有）
  ```

  電腦上的 `_moodle` 資料夾裡**沒有** `mail.json`（信件同步在雲端跑），不要去找它。`excerpt` 只有內文前段；如果截止日不在裡面、又確定需要全文，再問使用者要不要打開那封信。
- **e學苑作業**：`tasks.py list` 的 `assignments`（已經在任務欄裡了，不用再加成備忘）。
- **講義**：用 search-materials skill 查 `state.db`。

## 步驟

1. **先看現有的任務**，避免重複：

   ```
   python tasks.py list --config config.ini
   ```

   輸出是 JSON：`assignments`（e學苑作業）和 `memos`（備忘）。標題和日期都差不多的就不要再加。

2. **整理要加的項目**，每一筆：
   - `title`：短、看得懂，例如「作業系統 期中考」「資料庫 Project 第一階段繳交」。不要整句照抄信件主旨。
   - `due`：`YYYY-MM-DD HH:MM`（台灣時間）。只知道日期就寫 `YYYY-MM-DD`（會當成 23:59）。完全沒有日期就省略。
   - `tags`：課程名稱或類別，例如 `["作業系統"]`、`["考試"]`。一到兩個就好。
   - `note`：補充細節和來源，例如「範圍第 1–6 章；來源：10/18 老師寄的『期中考說明』」。
   - `source`：從哪裡整理來的：`mail`（信件）、`lecture`（講義）或 `other`。app 的卡片會依這個標出小圖示。

3. **先給使用者看清單、確認後才寫入**：列出每一筆的標題、日期、標籤，問「要加這幾筆嗎？」。使用者說好才進行下一步，也可以讓他刪改。

4. **寫入**：把確認好的清單存成 `new_tasks.json`（UTF-8，JSON 陣列），然後：

   ```
   python tasks.py add --config config.ini --file new_tasks.json
   ```

   - 輸出 `{"added": [...]}`：成功，告訴使用者打開 app 的任務欄就看得到。
   - 輸出 `{"queued": ...}`：這個環境連不到資料庫，備忘先存在 `_moodle\tasks_inbox.json`，電腦的排程下次同步時（通常 30 分鐘內）會自動送上去。照實告訴使用者。

## 注意

- 只新增，不要刪除或修改使用者原本的備忘和作業。
- 日期不確定（例如信裡只說「下週」）就問使用者，或寫在 `note` 裡並省略 `due`，不要自己猜。
- 不要把信件全文、個人資料（學號、電話）寫進 `note`，只寫跟這個任務有關的重點和來源。
