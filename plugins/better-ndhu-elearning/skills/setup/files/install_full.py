"""BetterElearning 完整安裝（手機 app + 網頁版 + 雲端同步），一支腳本跑完，不需要 Claude。

    python install_full.py

會做的事：檢查工具 → 登入 GitHub / Firebase → 建你自己的 Firebase 專案 → 建你自己的私有同步 repo、
設好帳密 → 跑第一次同步 → 產生 app 的專屬 QR code → (Windows) 把講義下載到這台電腦、之後每天自動下載
→ (選用) 信件摘要：雲端 API、本地模型或電腦上的 agent。中途關掉沒關係，再跑一次會從上次停下的地方接著做
（進度記在 ~/BetterElearning/install-state.json）。

需要：git、GitHub CLI (gh)、Node.js、Python 3.9+。密碼只會在這個視窗裡輸入，直接交給 GitHub 加密保存。
"""
import configparser
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
WORK = Path(os.environ.get("BETTEREL_WORK") or Path.home() / "BetterElearning")
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


PC_FILES = ["moodle_sync.py", "search.py", "set_password.py", "setup.ps1", "install_task.ps1", "check_task.ps1",
            "open_local.ps1", "open_local.vbs", "install_open_local.ps1", "config.example.ini", "README.md"]


def ps(script, cwd):
    """跑 PowerShell 腳本 (畫面直接顯示)。"""
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], cwd=str(cwd)).returncode


def pc_materials(st):
    step(9, "把講義下載到這台電腦")
    if os.name != "nt":
        say("  這一步只支援 Windows（用到 Windows 的密碼加密和工作排程），略過。")
        return
    if st.get("pc_root") and Path(st["pc_root"], "_moodle", "cred.dat").exists() and st.get("pc_done"):
        say(f"  已經裝好了：{st['pc_root']}")
        return
    if st.get("pc_skip") and not yes("  上次選了不下載。現在要下載嗎？", False):
        return
    say("  每門課的講義會下載到你選的資料夾、建全文索引，之後每天自動下載新的；")
    say("  電腦版 app 點教材就會直接打開檔案，也可以用 search.py 全文搜尋「哪堂課講過 deadlock」。")
    say("  第一次下載視講義多寡要幾分鐘，檔案可能不小。電腦要開著才會每天下載。")
    if not yes("  要下載嗎？"):
        st["pc_skip"] = True
        save_state(st)
        return
    st.pop("pc_skip", None)
    p = st["profile"]
    root = Path(st.get("pc_root") or ask("  課程資料夾放哪裡（每門課會是一個子資料夾）", str(Path.home() / "Documents" / "NDHU")))
    md = root / "_moodle"
    md.mkdir(parents=True, exist_ok=True)
    st["pc_root"] = str(root)
    save_state(st)

    # 1. 程式 (位元組原樣複製: .ps1 要保留 UTF-8 BOM)
    for name in PC_FILES:
        if (HERE / name).exists():
            shutil.copyfile(HERE / name, md / name)
    shutil.copyfile(TEMPLATE / "cloud" / "cloud_push.py", md / "cloud_push.py")
    if not (md / "firebase-sa.json").exists():
        shutil.copyfile(WORK / "firebase-sa.json", md / "firebase-sa.json")

    # 2. config.ini (已經有就不動，只補 [cloud])
    cfgp = md / "config.ini"
    if not cfgp.exists():
        t = (HERE / "config.example.ini").read_text(encoding="utf-8-sig")
        # 替換內容用 lambda 給：Windows 路徑的反斜線放在替換字串裡會被當成跳脫字元
        for key, val in (("username", p["student"]), ("root_folder", str(root)),
                         ("dept_label", p["dept_label"]), ("dept_pattern", p["dept_pattern"])):
            t = re.sub(rf"(?m)^{key} =.*$", lambda m, k=key, v=val: f"{k} = {v}", t, count=1)
        cfgp.write_text(t, encoding="utf-8")
    # 作業、繳交狀態、行事曆由雲端同步負責；這台電腦每天只跑一次，只下載講義 (已經有的 config.ini 也補上)
    t = cfgp.read_text(encoding="utf-8-sig")
    if re.search(r"(?m)^light_sync\s*=", t):
        t2 = re.sub(r"(?m)^light_sync\s*=.*$", "light_sync = false", t, count=1)
    else:
        t2 = re.sub(r"(?m)^\[moodle\][ \t]*$", lambda m: m.group(0) + "\nlight_sync = false", t, count=1)
    if t2 != t:
        cfgp.write_text(t2, encoding="utf-8")
    cp = configparser.ConfigParser()
    cp.read(cfgp, encoding="utf-8-sig")
    if not cp.has_section("cloud"):
        with open(cfgp, "a", encoding="utf-8") as f:
            f.write(f"\n[cloud]\n# BetterElearning app：Firebase 金鑰 (相對於這個檔) 和登入帳號的 UID\nservice_account = firebase-sa.json\nuid = {st['uid']}\n")

    # 3. 密碼 (存在這台電腦，用 Windows 加密；跟 GitHub 上那份分開)
    if not (md / "cred.dat").exists():
        say("\n  電腦每天下載講義要用 e學苑密碼，只會加密存在這台電腦。輸入時畫面不會顯示。")
        subprocess.run([sys.executable, str(md / "set_password.py")], cwd=str(md))
        if not (md / "cred.dat").exists():
            raise RuntimeError("密碼沒有存成功，再跑一次這支腳本會從這裡接著做。")

    # 4. 套件 + 每天下載的排程 (setup.ps1 會叫 install_task.ps1，順便把補資料的動作加進排程)
    say("\n  安裝套件、註冊每天下載的排程…")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "firebase-admin"], check=False)
    ps(md / "setup.ps1", md)

    # 5. 第一次完整下載 + 把教材路徑補進 app
    say("\n  第一次下載講義（會花幾分鐘）…")
    r = subprocess.run([sys.executable, str(md / "moodle_sync.py"), "--force"], cwd=str(md))
    if r.returncode != 0:
        say(f"  下載有錯，看 {md / 'log.txt'} 最後幾行。排程之後還是會每天再試。")
    subprocess.run([sys.executable, str(md / "cloud_push.py"), "overlay", "--dir", str(md), "--config", str(cfgp)], cwd=str(md))

    # 6. 點教材直接開檔
    say("\n  電腦版 app 點教材要直接開檔，需要在 Windows 登記一個 betterel: 連結。")
    say("  它只開課程資料夾裡的文件（pdf、ppt、doc、xls、txt…），zip 和資料夾用檔案總管打開，其他檔案一律不執行。")
    if yes("  要登記嗎？"):
        ps(md / "install_open_local.ps1", md)
    st["pc_done"] = True
    save_state(st)
    say(f"  講義在 {root}，每天第一次開機後會自動下載新的。")


# ------------------------------------------------------------------ 信件摘要 (選用)
CLOUD_PRESETS = [   # (名稱, provider, base_url)
    ("OpenAI", "openai", "https://api.openai.com/v1"),
    ("Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("OpenRouter", "openai", "https://openrouter.ai/api/v1"),
    ("Anthropic (Claude)", "anthropic", ""),
    ("其他 OpenAI 相容的服務", "openai", None),
]
PC_PRESETS = [
    ("Ollama（本地模型）", "openai", "http://localhost:11434/v1"),
    ("LM Studio（本地模型）", "openai", "http://localhost:1234/v1"),
    ("雲端 API（OpenAI 相容：OpenAI、Gemini、OpenRouter…）", "openai", None),
    ("Anthropic API (Claude)", "anthropic", ""),
    ("指令列 agent（codex、claude…，或任何讀 stdin、印出結果的指令）", "command", ""),
]
SUMM_SECRETS = ("SUMMARY_PROVIDER", "SUMMARY_BASE_URL", "SUMMARY_MODEL", "SUMMARY_API_KEY")


def pick(options):
    for i, o in enumerate(options, 1):
        say(f"    {i}. {o[0]}")
    return options[int(ask("  選哪個", "1", check=lambda v: v.isdigit() and 1 <= int(v) <= len(options))) - 1]


def ask_model(provider):
    if provider == "anthropic":
        return ask("  模型名稱", "claude-opus-5-5")
    return ask("  模型名稱（照服務商或本地模型的名稱填，例如 Ollama 用 ollama list 看）")


def pc_section(cfgp, body):
    """把 config.ini 的 [summaries] 換成 body (None = 拿掉)。"""
    t = cfgp.read_text(encoding="utf-8-sig")
    t = re.sub(r"(?ms)^\[summaries\][ \t]*\n.*?(?=^\[|\Z)", "", t).rstrip("\n") + "\n"
    if body:
        t += "\n[summaries]\n" + body
    cfgp.write_text(t, encoding="utf-8")


def summaries_setup(st):
    step(10, "信件摘要（選用）")
    pc = os.name == "nt" and st.get("pc_done")
    if st.get("summ_mode") and not yes(f"  已經設定過（{st['summ_mode']}）。要重新設定嗎？", False):
        return
    say("  需注意的新信可以交給語言模型寫中文摘要、要做的事和幻燈片短句；不設的話 app 會用信裡抓到的關鍵字。")
    modes = [("不用", "none"), ("雲端 API：GitHub 每小時同步完順便寫，電腦不用開。要 API 金鑰，依用量付費。", "cloud")]
    if pc:
        modes.append(("這台電腦：本地模型（Ollama、LM Studio）或電腦上的 agent 指令，電腦開著才會寫。", "pc"))
    else:
        say("  （要用本地模型或電腦上的 agent，先在第 9 步選下載講義。）")
    mode = pick(modes)[1]
    repo, md = st["repo"], Path(st.get("pc_root") or ".") / "_moodle"
    cfgp = md / "config.ini"

    # 換方式時先清掉舊的設定，避免兩邊都在寫
    if st.get("summ_mode") == "cloud" and mode != "cloud":
        for k in SUMM_SECRETS:
            run(["gh", "secret", "delete", k, "--repo", repo], check=False)
    if st.get("summ_mode") == "pc" and mode != "pc" and cfgp.exists():
        pc_section(cfgp, None)
        ps(md / "install_task.ps1", md)

    if mode == "cloud":
        say("  用哪一家：")
        name, provider, base = pick(CLOUD_PRESETS)
        if base is None:
            base = ask("  API 網址（到 /v1 為止，例如 https://api.example.com/v1）", check=lambda v: v.startswith("http"))
        model = ask_model(provider)
        for k, v in (("SUMMARY_PROVIDER", provider), ("SUMMARY_BASE_URL", base), ("SUMMARY_MODEL", model)):
            if v:
                run(["gh", "secret", "set", k, "--repo", repo, "--body", v])
        say(f"\n  接下來輸入 {name} 的 API 金鑰。畫面上不會顯示你打的字，打完按 Enter。金鑰直接交給 GitHub 加密保存。")
        run(["gh", "secret", "set", "SUMMARY_API_KEY", "--repo", repo], capture=False)
        say("  好了。下一次整點同步完就會開始寫摘要；結果在 GitHub Actions 那次執行的「Mail summaries」步驟。")

    elif mode == "pc":
        say("  用什麼寫：")
        name, provider, base = pick(PC_PRESETS)
        lines = [f"provider = {provider}"]
        if provider == "command":
            say("  信件內容會從 stdin 交給這個指令，它要把 JSON 結果印出來。agent 請用不能改檔案、不能執行指令的唯讀模式。")
            lines.append("command = " + ask("  指令（例如 codex exec -、claude -p、ollama run <模型>）"))
        else:
            if base is None:
                base = ask("  API 網址（到 /v1 為止）", check=lambda v: v.startswith("http"))
            if base:
                lines.append(f"base_url = {base}")
            lines.append(f"model = {ask_model(provider)}")
            if provider == "openai" and "localhost" in base:
                lines.append("batch = 2")
            elif yes("  這個服務要 API 金鑰嗎？", True):
                import getpass
                import winreg
                key = getpass.getpass("  輸入金鑰（畫面不會顯示）：").strip()
                if key:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as h:
                        winreg.SetValueEx(h, "SUMMARY_API_KEY", 0, winreg.REG_SZ, key)
                    os.environ["SUMMARY_API_KEY"] = key
                    say("  金鑰存在這個 Windows 帳號的環境變數 SUMMARY_API_KEY，沒有寫進任何檔案。")
        if provider == "anthropic":
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "anthropic"], check=False)
        shutil.copyfile(TEMPLATE / "cloud" / "summaries.py", md / "summaries.py")
        shutil.copyfile(TEMPLATE / "cloud" / "cloud_push.py", md / "cloud_push.py")
        shutil.copyfile(HERE / "install_task.ps1", md / "install_task.ps1")     # 舊版的排程腳本不認得 [summaries]
        pc_section(cfgp, "\n".join(lines) + "\n")
        say("\n  試跑一次（只印結果、不存）…")
        subprocess.run([sys.executable, str(md / "summaries.py"), "auto", "--config", str(cfgp), "--dry-run"], cwd=str(md))
        say("\n  把寫摘要加進每天的排程…")
        ps(md / "install_task.ps1", md)
        say(f"  好了。電腦開著時排程會順便寫摘要，紀錄在 {md / 'summaries_log.txt'}。")

    st["summ_mode"] = mode
    save_state(st)


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
        pc_materials(st)
        summaries_setup(st)
    except KeyboardInterrupt:
        say("\n已中斷。再跑一次會從這裡接著做。")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        say(f"\n出錯了：{e}")
        say("再跑一次會從這一步接著做。卡住的話，帶著上面的訊息到 GitHub 開 issue。")
        sys.exit(1)
    say("\n完成！")
    say(f"- {WORK} 裡的 firebase-sa.json 不要傳給任何人。學校密碼改了：gh secret set MOODLE_PASSWORD --repo {st['repo']}")
    say("- app 的「立即同步」按鈕要一組 GitHub token，做法在 README 的〈立即同步〉。")
    if st.get("pc_done"):
        say(f"- 講義在 {st['pc_root']}。排程有沒有在跑：在那裡的 _moodle 資料夾執行 check_task.ps1。")
    say("- 想改信件摘要的設定或加裝講義下載：再跑一次這支腳本。")


# ------------------------------------------------------------------ 檢查 (python install.py --check)
def check():
    """app 登入了卻沒資料時用：比對同步 repo、Firebase 專案、登入帳號的 UID 和資料庫裡實際有資料的帳號。"""
    say("BetterElearning 檢查")
    st = load_state()
    repo, sa_path, cfg_path = st.get("repo"), WORK / "firebase-sa.json", WORK / "firebase-config.json"
    if not repo or not sa_path.exists():
        say(f"  找不到安裝紀錄或金鑰（{STATE_FILE}、{sa_path}）。請在當初跑安裝的那台電腦、同一個帳號執行。")
        return
    problems = []

    # 1. 最近幾次同步
    say("\n━━ 1. 最近幾次雲端同步")
    r = run(["gh", "run", "list", "--repo", repo, "--workflow", "sync.yml", "--limit", "5",
             "--json", "databaseId,status,conclusion,createdAt,event"], check=False)
    runs = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    for x in runs:
        say(f"  {x['createdAt'][:16].replace('T', ' ')} UTC  {x['event']:<17} {x['conclusion'] or x['status']}")
    if not runs:
        problems.append(("一次都沒有同步過", f"gh workflow run sync.yml --repo {repo}"))
    elif not any(x["conclusion"] == "success" for x in runs):
        problems.append(("最近的同步都沒有成功", f"gh run view {runs[0]['databaseId']} --repo {repo} --log-failed"))

    # 2. 專案和 UID
    say("\n━━ 2. Firebase 專案和帳號")
    sa = json.loads(sa_path.read_text(encoding="utf-8"))
    sa_proj = sa.get("project_id")
    app_proj = json.loads(cfg_path.read_text(encoding="utf-8")).get("projectId") if cfg_path.exists() else None
    say(f"  金鑰的專案：{sa_proj}　app 連的專案：{app_proj}")
    if app_proj and sa_proj != app_proj:
        problems.append(("金鑰和 app 屬於不同的 Firebase 專案：同步寫進一個專案，app 讀另一個",
                         f"到 {CONSOLE.format(p=app_proj)}/settings/serviceaccounts/adminsdk 重新下載金鑰，"
                         f"放到 {sa_path}，再在 PowerShell 執行：Get-Content \"{sa_path}\" -Raw | gh secret set FIREBASE_SA --repo {repo}"))
    r = run(["gh", "variable", "list", "--repo", repo, "--json", "name,value"], check=False)
    var_uid = next((v["value"] for v in json.loads(r.stdout or "[]") if v["name"] == "FIREBASE_UID"), None) if r.returncode == 0 else None
    say(f"  同步寫進的 UID（repo 變數 FIREBASE_UID）：{var_uid}")

    try:
        import firebase_admin
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "firebase-admin"], check=False)
        import firebase_admin
    from firebase_admin import auth, credentials, firestore
    firebase_admin.initialize_app(credentials.Certificate(str(sa_path)))
    users = [(u.uid, u.email or "") for u in auth.list_users().iterate_all()]
    say("  Firebase 裡的登入帳號：")
    for uid, email in users:
        say(f"    {email:<32} {uid}" + ("  ← 同步寫這個" if uid == var_uid else ""))
    db = firestore.client()
    with_data = []
    for ref in db.collection("users").list_documents():
        snap = ref.collection("dash").document("data").get()
        if snap.exists:
            with_data.append((ref.id, (snap.to_dict() or {}).get("generated_at")))
    say("  資料庫裡有同步資料的 UID：" + ("、".join(f"{u}（{g}）" for u, g in with_data) or "沒有"))

    email = (st.get("profile") or {}).get("email", "")
    login_uid = next((u for u, e in users if e.lower() == email.lower()), None)
    if var_uid and users and var_uid not in [u for u, _ in users]:
        fix = login_uid or (users[0][0] if len(users) == 1 else None)
        problems.append((f"FIREBASE_UID ({var_uid}) 不是任何一個登入帳號的 UID，同步寫進了沒人讀得到的地方",
                         f"gh variable set FIREBASE_UID --repo {repo} --body {fix}" if fix else "把它改成上面 app 登入那個帳號的 UID"))
        if fix and yes(f"\n  要把 FIREBASE_UID 改成 {fix}（{email or '唯一的帳號'}）並馬上同步一次嗎？"):
            run(["gh", "variable", "set", "FIREBASE_UID", "--repo", repo, "--body", fix])
            run(["gh", "workflow", "run", "sync.yml", "--repo", repo])
            st["uid"] = fix
            save_state(st)
            say("  改好了，同步已經開始，大約 1–2 分鐘後 app 就會有資料。")
            problems.pop()
    elif not users:
        problems.append(("Firebase 裡還沒有登入帳號", f"到 {CONSOLE.format(p=sa_proj)}/authentication/users 新增一個"))
    elif var_uid and runs and any(x["conclusion"] == "success" for x in runs) and var_uid not in [u for u, _ in with_data]:
        problems.append(("同步顯示成功，但資料庫裡這個 UID 沒有資料", f"gh run view {runs[0]['databaseId']} --repo {repo} --log"))

    say("\n━━ 結果")
    if not problems:
        say("  沒看到問題。app 登入的 email 要跟上面標「同步寫這個」的那個帳號一樣；不一樣的話先在 app 的設定登出，再用那個 email 登入。")
    for what, how in problems:
        say(f"  ✗ {what}")
        say(f"    → {how}")


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        check()
    else:
        main()
