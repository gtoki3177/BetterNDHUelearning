# BetterElearning 同步引擎

每小時在 GitHub 的雲端機器上登入東華 e學苑和 gms 信箱，把作業、課程、未讀信整理好存進你自己的 Firebase。
BetterElearning app 從那裡讀資料，電腦不用開著。

這個 repo 只放同步引擎。一般不用自己改任何東西，安裝交給 Claude 的 BetterNDHUelearning 外掛（setup）帶著做。

## 需要的設定

| 種類 | 名稱 | 內容 |
|---|---|---|
| Secret | `MOODLE_USERNAME` | e學苑帳號（學號） |
| Secret | `MOODLE_PASSWORD` | e學苑密碼 |
| Secret | `MAIL_APP_PASSWORD` | gms 信箱的 Google 應用程式密碼（16 碼） |
| Secret | `FIREBASE_SA` | Firebase 服務帳戶金鑰（JSON 全文） |
| Variable | `FIREBASE_UID` | Firebase 登入帳號的 UID |
| Variable | `DEPT_LABEL`、`DEPT_PATTERN` | 選填：系上的名稱和信件比對用的關鍵字，例如 `電機系`、`電機\|EE\|系辦` |

repo 請設成 **private**：同步紀錄只印統計數字，但沒必要讓別人看到。

## 檔案

| 路徑 | 內容 |
|---|---|
| `.github/workflows/sync.yml` | 每小時第 17 分跑一次同步，也可以在 Actions 頁手動觸發 |
| `.github/workflows/probe.yml` | 只測連線：GitHub 的機器連不連得到 e學苑和 Gmail |
| `cloud/moodle_sync.py`、`cloud/mail_sync.py` | 同步程式（跟電腦版同一份，密碼改從環境變數讀） |
| `cloud/cloud_push.py` | 同步前從 Firestore 取回上一版、同步後寫回 |
| `cloud/summaries.py` | （選用，在電腦上跑）讓 Claude 替需注意的信寫摘要，存回 Firestore |
| `firestore.rules` | Firestore 安全規則：每個帳號只讀寫得到自己的資料 |
