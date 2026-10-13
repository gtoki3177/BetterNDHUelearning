# BetterElearning 同步引擎（你的私有 repo）

每小時在 GitHub 的雲端機器上登入東華 e學苑和 gms 信箱，把作業、課程、未讀信整理好存進你自己的 Firebase。
BetterElearning app 從那裡讀資料，電腦不用開著。

同步程式本身在 [gtoki3177/BetterNDHUelearning](https://github.com/gtoki3177/BetterNDHUelearning) 的 `engine/`，
這個 repo 的 `.github/workflows/sync.yml` 只是去呼叫它（`@v1`），所以程式更新時你不用做任何事。
想自己決定何時升級的話，把 `@v1` 換成某個 commit 的 SHA。

repo 請設成 **private**。

## 設定

| 種類 | 名稱 | 內容 |
|---|---|---|
| Secret | `MOODLE_USERNAME` | e學苑帳號（學號） |
| Secret | `MOODLE_PASSWORD` | e學苑密碼 |
| Secret | `MAIL_APP_PASSWORD` | 選用：gms 信箱的 Google 應用程式密碼（16 碼），沒設就不同步信箱 |
| Secret | `FIREBASE_SA` | Firebase 服務帳戶金鑰（JSON 全文） |
| Variable | `FIREBASE_UID` | Firebase 登入帳號的 UID |
| Variable | `DEPT_LABEL`、`DEPT_PATTERN` | 選填：系上的名稱和信件比對用的關鍵字，例如 `電機系`、`電機\|EE\|系辦` |

## 其他檔案

| 路徑 | 內容 |
|---|---|
| `.github/workflows/probe.yml` | 只測連線：GitHub 的機器連不連得到 e學苑和 Gmail |
| `firestore.rules` | Firestore 安全規則：每個帳號只讀寫得到自己的資料 |
| `cloud/summaries.py`、`cloud/cloud_push.py` | 選用，在電腦上跑：讓 Claude 替需注意的信寫摘要，存回 Firestore |
