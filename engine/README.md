# engine — 同步引擎

每個使用者自己的私有 repo 用 `.github/workflows/engine-sync.yml`（reusable workflow）跑這裡的程式：
登入東華 e學苑和 gms 信箱，結果寫進使用者自己的 Firebase。密碼放在使用者自己 repo 的 Secrets，不經過這個 repo。

| 檔案 | 內容 |
|---|---|
| `moodle_sync.py`、`mail_sync.py` | 同步程式（跟電腦版同一份，密碼改從環境變數讀、e學苑 session 可沿用） |
| `cloud_push.py` | 同步前從 Firestore 取回上一版、同步後寫回 |
| `config.cloud.ini` | 設定範本，workflow 會把帳號和系所填進去 |

## 版本

- 使用者的 repo 引用 `@v1`。改這裡的程式推到 `main` 之後，引用 `@main` 的人（開發者自己）下一個整點就會跑到新版。
- 確認沒問題再把 `v1` 標籤移過去，所有人下一個整點就會更新：

  ```bash
  git tag -f v1 && git push -f origin v1
  ```

- 不相容的改動（例如需要新的 Secret）請開 `v2`，並在 setup 技能裡寫清楚怎麼升級。

所有使用者每小時都會用自己的學校密碼執行這裡的程式碼：這個 repo 的維護者帳號一定要開兩步驟驗證，合併別人的 PR 前要看過 `engine/` 和 workflow 的改動。
