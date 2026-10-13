"""BetterElearning 完整安裝（手機 app + 網頁版 + 雲端同步），一支腳本跑完，不需要 Claude。

    python install_full.py

會做的事：檢查工具 → 登入 GitHub / Firebase → 建你自己的 Firebase 專案 → 建你自己的私有同步 repo、
設好帳密 → 跑第一次同步 → 產生 app 的專屬 QR code。中途關掉沒關係，再跑一次會從上次停下的地方接著做
（進度記在 ~/BetterElearning/install-state.json）。

需要：git、GitHub CLI (gh)、Node.js、Python 3.9+。密碼只會在這個視窗裡輸入，直接交給 GitHub 加密保存。
"""
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "cloud-template"
WORK = Path.home() / "BetterElearning"
STATE_FILE = WORK / "install-state.json"
FT = ["npx", "--yes", "firebase-tools@15"]
REPO_NAME = "betterel-sync"
APK = "https://github.com/gtoki3177/BetterNDHUelearning/releases/latest"
CONSOLE = "https://console.firebase.google.com/project/{p}"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------ 小工具
def say(msg=""):
    print(msg, flush=True)


def step(n, title):
    say(f"\n━━ {n}. {title} " + "━" * max(4, 40 - len(title) * 2))


def ask(prompt, default=None, check=None):
    while True:
        tail = f"（直接按 Enter = {default}）" if default else ""
        v = input(f"{prompt}{tail}：").strip() or (default or "")
        if v and (not check or check(v)):
            return v
        say("  格式不對，再輸入一次。")


def yes(prompt, default=True):
    v = input(f"{prompt} [{'Y/n' if default else 'y/N'}]：").strip().lower()
    return default if not v else v.startswith("y")


def wait_enter(msg="做好了按 Enter 繼續"):
    input(f"  {msg}…")


def exe(name):
    p = shutil.which(name) or shutil.which(name + ".cmd") or shutil.which(name + ".exe")
    if not p:
        raise FileNotFoundError(name)
    return p


def run(args, *, capture=True, check=True, input_text=None, cwd=None):
    args = [exe(args[0])] + list(args[1:])
    r = subprocess.run(args, capture_output=capture, text=True, encoding="utf-8", errors="replace",
                       input=input_text, cwd=cwd)
    if check and r.returncode != 0:
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        raise RuntimeError(f"指令失敗：{' '.join(args[1:3])}\n{out[-1500:]}")
    return r


def fjson(args, cwd=None):
    """跑 firebase-tools --json，回傳 result (失敗回傳 None 和錯誤訊息)。"""
    r = run(FT + args + ["--json"], check=False, cwd=cwd)
    try:
        d = json.loads(r.stdout[r.stdout.index("{"):])
    except ValueError:
        return None, (r.stdout + r.stderr)[-800:]
    if d.get("status") == "success":
        return d.get("result"), ""
    return None, d.get("error") or json.dumps(d, ensure_ascii=False)[:800]


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(s):
    WORK.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ 步驟
def check_tools():
    step(1, "檢查工具")
    hints = {"git": "winget install Git.Git", "gh": "winget install GitHub.cli", "node": "winget install OpenJS.NodeJS.LTS",
             "npx": "跟 Node.js 一起裝"}
    missing = []
    for name in hints:
        try:
            exe(name)
        except FileNotFoundError:
            missing.append(name)
    if missing:
        say("還缺這些工具，裝好後關掉這個視窗、重開一個再跑一次：")
        for m in missing:
            say(f"  {m}：{hints[m]}")
        sys.exit(1)
    if not TEMPLATE.exists():
        say(f"找不到同步引擎範本：{TEMPLATE}\n請用完整的外掛資料夾（或整個 repo 的 zip）來跑這支腳本。")
        sys.exit(1)
    say("  git、gh、Node.js 都有了。")


def logins():
    step(2, "登入 GitHub 和 Firebase")
    if run(["gh", "auth", "status"], check=False).returncode != 0:
        say("  會打開瀏覽器登入 GitHub（哪個帳號都可以，沒有就先註冊一個）。")
        run(["gh", "auth", "login", "--web", "--git-protocol", "https"], capture=False)
    say("  GitHub：已登入 " + run(["gh", "api", "user", "-q", ".login"]).stdout.strip())
    res, _ = fjson(["projects:list"])
    if res is None:
        say("  會打開瀏覽器登入 Firebase。請用「個人」Google 帳號，不要用 gms（學校帳號常常不能建專案）。")
        run(FT + ["login"], capture=False)
        res, err = fjson(["projects:list"])
        if res is None:
            raise RuntimeError("Firebase 還是沒登入成功：" + str(err))
    say("  Firebase：已登入。")


def ask_profile(st):
    step(3, "基本資料")
    if st.get("profile"):
        p = st["profile"]
        say(f"  學號 {p['student']}、系所 {p['dept_label']}、信箱同步 {'要' if p['mail'] else '不要'}、登入 email {p['email']}")
        if yes("  沿用這些嗎？"):
            return
    student = ask("  e學苑帳號（學號）", check=lambda v: re.fullmatch(r"[A-Za-z0-9]{5,12}", v))
    label = ask("  系所簡稱，用來把系上寄的信分一組（例如 資工系）", "系上")
    base = re.sub(r"系$", "", label)
    pattern = ask("  比對系上信件的關鍵字，用 | 隔開", f"{base}|系辦" if base and base != "系上" else "系辦")
    mail = yes("  要同步 gms 信箱嗎？（需要 Google 兩步驟驗證和應用程式密碼）")
    email = ask("  登入 app 要用的 email（建議用個人信箱）", check=lambda v: re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v))
    st["profile"] = {"student": student, "dept_label": label, "dept_pattern": pattern, "mail": mail, "email": email}
    save_state(st)


def firebase_project(st):
    step(4, "建你自己的 Firebase 專案")
    if not st.get("project"):
        for _ in range(3):
            pid = "betterel-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            res, err = fjson(["projects:create", pid, "--display-name", "BetterElearning"])
            if res is not None:
                st["project"] = pid
                save_state(st)
                break
            if "already exists" not in str(err):
                say("  自動建立失敗（第一次用 Firebase 的帳號要先在網頁上同意條款）：")
                say(f"  {str(err)[:300]}")
                say("  請到 https://console.firebase.google.com 按「建立專案」，名稱隨便取，Google Analytics 可以關掉。")
                st["project"] = ask("  建好後把「專案 ID」貼在這裡（例如 betterel-abc12）", check=lambda v: re.fullmatch(r"[a-z0-9-]{6,30}", v))
                save_state(st)
                break
    pid = st["project"]
    say(f"  專案：{pid}")

    if not st.get("firestore"):
        res, err = fjson(["firestore:databases:create", "(default)", "--location", "asia-east1", "--project", pid])
        if res is None and "already exists" not in str(err).lower():
            say("  資料庫自動建立失敗：" + str(err)[:300])
            say(f"  請到 {CONSOLE.format(p=pid)}/firestore 按「建立資料庫」，位置選 asia-east1（台灣），模式選正式版。")
            wait_enter()
        st["firestore"] = True
        save_state(st)

    if not st.get("rules"):
        shutil.copy(TEMPLATE / "firestore.rules", WORK / "firestore.rules")
        (WORK / "firebase.json").write_text(json.dumps({"firestore": {"rules": "firestore.rules"}}), encoding="utf-8")
        run(FT + ["deploy", "--only", "firestore:rules", "--project", pid, "--non-interactive"], cwd=str(WORK))
        st["rules"] = True
        save_state(st)
    say("  資料庫和安全規則好了。")

    if not st.get("webconfig"):
        apps, _ = fjson(["apps:list", "--project", pid])
        app_id = next((a["appId"] for a in (apps or []) if a.get("platform") == "WEB"), None)
        if not app_id:
            res, err = fjson(["apps:create", "web", "BetterElearning", "--project", pid])
            if res is None:
                raise RuntimeError("建立網頁 app 失敗：" + str(err))
            app_id = res["appId"]
        res, err = fjson(["apps:sdkconfig", "web", app_id, "--project", pid])
        if res is None:
            raise RuntimeError("讀網頁設定失敗：" + str(err))
        keys = ("apiKey", "authDomain", "projectId", "storageBucket", "messagingSenderId", "appId")
        cfg = {k: res["sdkConfig"][k] for k in keys if res["sdkConfig"].get(k)}
        (WORK / "firebase-config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
        st["webconfig"] = True
        save_state(st)


def firebase_console(st):
    pid, email = st["project"], st["profile"]["email"]
    step(5, "在 Firebase 網頁上點兩個地方")
    if not st.get("uid"):
        url = f"{CONSOLE.format(p=pid)}/authentication/providers"
        say("  (1) 開登入帳號。等一下會打開這個網頁：")
        say(f"      {url}")
        say("      按「開始使用」→「電子郵件/密碼」→ 啟用 → 儲存。")
        say(f"      再到上面的「Users / 使用者」分頁 → 新增使用者：email 填 {email}，密碼自己想一組新的（這是登入 app 用的）。")
        say("      建好後，複製那一列的「使用者 UID」。")
        webbrowser.open(url)
        st["uid"] = ask("  把使用者 UID 貼在這裡", check=lambda v: re.fullmatch(r"[A-Za-z0-9]{20,40}", v))
        save_state(st)
    sa = WORK / "firebase-sa.json"
    if not sa.exists():
        url = f"{CONSOLE.format(p=pid)}/settings/serviceaccounts/adminsdk"
        say("  (2) 下載金鑰。等一下會打開這個網頁：")
        say(f"      {url}")
        say("      按「產生新的私密金鑰」→ 產生金鑰，瀏覽器會下載一個 .json 檔。")
        say("      這個檔等於整個資料庫的鑰匙，不要傳給任何人。")
        webbrowser.open(url)
        downloads = Path.home() / "Downloads"
        t0 = time.time() - 5
        while not sa.exists():
            found = sorted((p for p in downloads.glob(f"{pid}-firebase-adminsdk-*.json") if p.stat().st_mtime > t0 - 3600),
                           key=lambda p: p.stat().st_mtime, reverse=True) if downloads.exists() else []
            if found:
                shutil.move(str(found[0]), sa)
                break
            v = input(f"  下載好了按 Enter（自動在「下載」資料夾找）；或把檔案拖進這個視窗再按 Enter：").strip().strip('"')
            if v and Path(v).is_file():
                shutil.move(v, sa)
        say(f"  金鑰放在 {sa}")


def github_repo(st):
    p = st["profile"]
    step(6, "建你自己的同步 repo（GitHub，私人）")
    login = run(["gh", "api", "user", "-q", ".login"]).stdout.strip()
    repo = f"{login}/{REPO_NAME}"
    local = WORK / REPO_NAME
    if run(["gh", "repo", "view", repo], check=False).returncode != 0:
        if local.exists():
            shutil.rmtree(local)
        shutil.copytree(TEMPLATE, local)
        uid = run(["gh", "api", "user", "-q", ".id"]).stdout.strip()
        git = ["git", "-c", f"user.name={login}", "-c", f"user.email={uid}+{login}@users.noreply.github.com"]
        run(["git", "init", "-q", "-b", "main"], cwd=str(local))
        run(["git", "add", "-A"], cwd=str(local))
        run(git + ["commit", "-q", "-m", "BetterElearning 同步引擎"], cwd=str(local))
        run(["gh", "repo", "create", REPO_NAME, "--private", "--source", ".", "--push"], cwd=str(local))
    st["repo"] = repo
    save_state(st)
    say(f"  repo：https://github.com/{repo}")

    run(["gh", "secret", "set", "MOODLE_USERNAME", "--repo", repo, "--body", p["student"]])
    run(["gh", "secret", "set", "FIREBASE_SA", "--repo", repo], input_text=(WORK / "firebase-sa.json").read_text(encoding="utf-8"))
    for k, v in (("FIREBASE_UID", st["uid"]), ("DEPT_LABEL", p["dept_label"]), ("DEPT_PATTERN", p["dept_pattern"])):
        run(["gh", "variable", "set", k, "--repo", repo, "--body", v])

    if not st.get("pw_moodle"):
        say("\n  接下來輸入 e學苑密碼。畫面上不會顯示你打的字，打完按 Enter。")
        run(["gh", "secret", "set", "MOODLE_PASSWORD", "--repo", repo], capture=False)
        st["pw_moodle"] = True
        save_state(st)
    if p["mail"] and not st.get("pw_mail"):
        say("\n  信箱要用 Google 的「應用程式密碼」（不是平常的密碼）：")
        say("    1. 用 gms 帳號登入 https://myaccount.google.com/security ，開啟「兩步驟驗證」。")
        say("    2. 到 https://myaccount.google.com/apppasswords ，名稱填 betterel，按建立，會出現 16 個英文字母。")
        webbrowser.open("https://myaccount.google.com/apppasswords")
        wait_enter("拿到 16 碼後按 Enter，再把它貼上")
        run(["gh", "secret", "set", "MAIL_APP_PASSWORD", "--repo", repo], capture=False)
        st["pw_mail"] = True
        save_state(st)


def first_sync(st):
    repo = st["repo"]
    step(7, "第一次同步（大約 1–2 分鐘）")
    run(["gh", "workflow", "run", "sync.yml", "--repo", repo])
    rid = None
    for _ in range(20):
        time.sleep(3)
        r = run(["gh", "run", "list", "--repo", repo, "--workflow", "sync.yml", "--limit", "1", "--json", "databaseId,status"])
        runs = json.loads(r.stdout or "[]")
        if runs:
            rid = str(runs[0]["databaseId"])
            break
    if not rid:
        say(f"  還看不到同步紀錄，等一下到 https://github.com/{repo}/actions 看結果。")
        return
    run(["gh", "run", "watch", rid, "--repo", repo, "--interval", "5"], check=False)
    log = run(["gh", "run", "view", rid, "--repo", repo, "--log"], check=False).stdout
    ok = run(["gh", "run", "view", rid, "--repo", repo, "--json", "conclusion", "-q", ".conclusion"]).stdout.strip() == "success"
    if "登入失敗" in log and "帳號或密碼" in log:
        say("  e學苑登入失敗：學號或密碼打錯了。重跑這支腳本前，先刪掉 install-state.json 裡的 \"pw_moodle\" 那行。")
    elif "Application-specific password required" in log:
        say("  信箱登入失敗：存成了一般密碼，要用 16 碼的應用程式密碼。刪掉 install-state.json 裡的 \"pw_mail\" 那行再跑一次。")
    elif ok and '"data": "written' in log:
        say("  同步成功！之後每小時第 17 分會自動同步一次，電腦不用開。")
    else:
        say(f"  同步好像沒有完全成功，到 https://github.com/{repo}/actions/runs/{rid} 看看（或把這個網址給 Claude）。")
    st["synced"] = ok
    save_state(st)


def link_app(st):
    step(8, "連上 app")
    sys.path.insert(0, str(HERE))
    try:
        import qrcode  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", "qrcode"], check=False)
    r = subprocess.run([sys.executable, str(HERE / "setup_link.py"), str(WORK / "firebase-config.json"),
                        "--email", st["profile"]["email"], "--qr", str(WORK / "setup-qr.svg")],
                       capture_output=True, text=True, encoding="utf-8")
    link = r.stdout.strip()
    (WORK / "setup-link.txt").write_text(link + "\n", encoding="utf-8")
    say(f"  手機（Android）：先到 {APK} 下載 BetterElearning.apk 安裝，")
    say("        再用相機掃剛打開的 QR code，會直接開 app、停在登入畫面。")
    say("  電腦 / iPhone：用 Chrome 或 Edge 打開這個連結，登入後網址列右邊可以「安裝應用程式」：")
    say(f"        {link}")
    say(f"  登入用第 5 步建的 email（{st['profile']['email']}）和那組新密碼。")
    if (WORK / "setup-qr.svg").exists():
        webbrowser.open((WORK / "setup-qr.svg").as_uri())


def main():
    say("BetterElearning 完整安裝")
    say(f"檔案會放在 {WORK}")
    WORK.mkdir(parents=True, exist_ok=True)
    st = load_state()
    try:
        check_tools()
        logins()
        ask_profile(st)
        firebase_project(st)
        firebase_console(st)
        github_repo(st)
        if not st.get("synced") or yes("\n  要再跑一次同步測試嗎？", False):
            first_sync(st)
        link_app(st)
    except KeyboardInterrupt:
        say("\n已中斷。再跑一次會從這裡接著做。")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        say(f"\n出錯了：{e}")
        say("再跑一次會從這一步接著做。卡住的話，把上面的訊息給 Claude 看。")
        sys.exit(1)
    say("\n完成！")
    say(f"- {WORK} 裡的 firebase-sa.json 不要傳給任何人。學校密碼改了：gh secret set MOODLE_PASSWORD --repo {st['repo']}")
    say("- 想讓 app 有「立即同步」按鈕、或讓 Claude 替重要的信寫摘要：在 Claude 桌面版說「BetterElearning 的選用設定」。")


if __name__ == "__main__":
    main()
