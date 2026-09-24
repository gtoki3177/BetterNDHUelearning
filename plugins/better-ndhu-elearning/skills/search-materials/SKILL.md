---
name: search-materials
description: 在東華 e學苑同步下來的課程講義裡找東西（全文檢索 state.db）。Use when the user asks things like「哪堂課講過 context switch」「資料庫那份 PDF 第幾頁在講關聯模型」「deadlock 在哪份講義」「幫我找講義裡的 B+ tree」, or wants to know which lecture file / page covers a topic, for a user who has BetterNDHUelearning set up.
---

# 在講義裡找東西

BetterNDHUelearning 的 `moodle_sync.py` 會把每份講義（PDF / PPT / PPTX / DOCX / 文字檔，zip 會先解開）逐頁抽出文字，存進 `<根資料夾>\_moodle\state.db` 的 SQLite FTS5 索引。中文是一個字一個字拆開存的，所以搜「分頁」也找得到「虛擬記憶體分頁機制」。

## 怎麼查

1. **找到 `state.db`**：它在使用者連線的資料夾底下的 `_moodle\` 裡，同一層還有 `search.py`、`config.ini`。
2. **執行 `search.py`**（純 Python 標準函式庫，Linux / Windows 都能跑），挑一種方式：
   - 能在使用者電腦上跑 shell（`device_bash`）：直接在 `_moodle` 資料夾跑 `python3 search.py "<查詢>"`。
   - 只能搬檔案：把 `state.db` 和 `search.py` 放進同一個資料夾，在自己的環境裡跑。
   - 常用參數：`--course <課名>` 限定科目；`-n 20` 多給幾筆；`--list` 看索引了哪些檔；`--stats` 看概況。
3. **調整查詢語法**：這是 FTS5 語法。空白代表 AND，另外可用 `OR`、`NOT`、`"片語"`，英文前綴用 `term*`。先用使用者的原話查；結果太少就換同義詞，或中英都試，例如「死結」和 deadlock。
4. **看上下文再回答**：結果會列出檔名、頁碼和一段原文。挑最相關的幾筆，去對應的檔案讀那一頁前後的內容，確認真的在講這件事。講義在 `<根資料夾>\<課名>\教材\`。
5. **回答格式**：講清楚在哪門課、哪個檔、第幾頁（投影片就是第幾張），再用自己的話講重點。不要整段貼講義原文。

## 找不到的時候

- 索引是空的，或沒有那門課：學期初老師還沒上傳是正常的。可以看 `latest.json` 的 `new_files`，確認最近有沒有抓到新檔。
- 舊版 `.ppt` 的頁碼是依內容順序推算的，可能和投影片編號差一兩張，回答時要提醒使用者。
- 掃描成圖片的 PDF 抽不到文字，所以搜不到。這種情況直接說清楚。
