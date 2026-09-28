#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDHU e學苑 (Moodle) 同步器
--------------------------------
每次執行會:
  1. 用 config.ini 的帳號 + cred.dat 的加密密碼登入 elearn4.ndhu.edu.tw
  2. 透過 Moodle 站內 AJAX 取得選課清單與行事曆事件
  3. 逐課解析課程頁, 取出教材/作業/公告
  4. 下載新的或改版的教材到 <科目資料夾>\教材\
  5. 抽出 PDF/PPTX/DOCX/ZIP 內的文字, 建立 SQLite FTS5 全文索引
  6. 輸出 latest.json 給 Claude 讀

兩種模式 (0.4 起):
  完整 (heavy)  上面全部, 包括下載教材、建索引。每天第一次跑自動用這個; --force 強制。
  輕量 (light)  只看課程頁、作業頁、行事曆 (繳交狀態、新作業、期限), 不下載。十幾秒就好,
                排程每 30 分鐘跑一次。--light 強制。
不帶參數 = 自動: 今天還沒跑過完整的就跑完整, 否則跑輕量 (10 分鐘內剛跑過就跳過)。
內容沒變就不重寫 latest.json; 同一時間只會跑一個 (moodle.lock); 結果記在 status_moodle.json。

密碼永遠不會以明文出現在這支程式或任何 log 裡。
"""

import base64
import configparser
import datetime as dt
import fnmatch
import io
import json
import os
import re
import sqlite3
import sys
import time
import traceback
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.ini"
CRED_PATH = HERE / "cred.dat"
DB_PATH = HERE / "state.db"
JSON_PATH = HERE / "latest.json"
LOG_PATH = HERE / "log.txt"
LOCK_PATH = HERE / "moodle.lock"
STATUS_PATH = HERE / "status_moodle.json"
LOCK_STALE_MIN = 70          # 完整同步第一次可能要下載很多, 給久一點
MIN_GAP_MIN = 10             # 自動模式: 距離上次同步不到這麼久就跳過
LOG_MAX_BYTES = 1_000_000

# 教材副檔名白名單 (下載)
DOWNLOAD_EXT = {
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
    ".zip", ".txt", ".md", ".csv", ".py", ".c", ".cpp", ".h", ".java",
}
# 可抽文字的副檔名 (索引)
INDEX_EXT = {".pdf", ".pptx", ".docx", ".txt", ".md", ".csv", ".py", ".c", ".cpp", ".h", ".java"}

WIN_BAD = r'<>:"/\|?*'


# --------------------------------------------------------------------------
# 基礎工具
# --------------------------------------------------------------------------

def log(msg, level="INFO"):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass  # pythonw 沒有 console, sys.stdout 是 None
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def rotate_log():
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH.with_name(LOG_PATH.stem + ".old.txt"))
    except OSError:
        pass


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def acquire_lock(mode):
    """同一時間只讓一個同步在跑 (排程 + 儀表板的「立即同步」可能撞在一起)。拿不到回 False。"""
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
            except OSError:
                continue
            if age < LOCK_STALE_MIN * 60:
                return False
            try:
                LOCK_PATH.unlink()
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "mode": mode, "started_at": now_iso()}, fh)
        return True
    return False


def release_lock():
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


def write_status(**kw):
    st = {}
    try:
        st = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    st.update(kw)
    tmp = STATUS_PATH.with_name(STATUS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATUS_PATH)


def content_view(snap, with_new_files):
    """比對「內容有沒有變」用: 拿掉時間戳、耗時、會隨時間變的剩餘天數。"""
    if not snap:
        return None
    s = json.loads(json.dumps(snap))
    s.pop("generated_at", None)
    s.pop("mode", None)
    if not with_new_files:
        s.pop("new_files", None)
    st = s.get("stats") or {}
    s["stats"] = {k: st.get(k) for k in ("courses", "assignments", "pending", "indexed_files",
                                          "indexed_chunks", "index", "total_files")}
    for a in s.get("assignments", []):
        a.pop("days_left", None)
    for c in s.get("courses", []):
        for a in c.get("assignments", []):
            a.pop("days_left", None)
    return json.dumps(s, ensure_ascii=False, sort_keys=True)


def safe_name(name, maxlen=90):
    """把課程/檔案名稱轉成合法的 Windows 資料夾名。"""
    name = re.sub(r"\s+", " ", (name or "")).strip()
    name = "".join("_" if c in WIN_BAD else c for c in name)
    name = name.rstrip(" .")
    if len(name) > maxlen:
        name = name[:maxlen].rstrip(" .")
    return name or "unnamed"


def load_password():
    """從 DPAPI 加密的 cred.dat 解出密碼。只有本機這個 Windows 帳戶解得開。"""
    if not CRED_PATH.exists():
        raise SystemExit(
            "找不到 cred.dat — 請先執行:  python set_password.py"
        )
    blob = base64.b64decode(CRED_PATH.read_bytes())
    try:
        import win32crypt  # type: ignore
    except ImportError:
        raise SystemExit("缺少 pywin32 — 請執行:  pip install pywin32")
    _desc, secret = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return secret.decode("utf-8")


def semester_key(tok):
    """'115上' -> (115, 0) ; '114下' -> (114, 1)。用來比大小找最新學期。"""
    m = re.match(r"(\d{3})([上下])", tok)
    if not m:
        return (0, 0)
    return (int(m.group(1)), 0 if m.group(2) == "上" else 1)


def parse_zh_datetime(text):
    """'2026年 09月 30日(週三) 00:00' -> ISO 字串。解析失敗回 None。"""
    if not text:
        return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
                  r"(?:\s*\([^)]*\))?\s*(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    y, mo, d, hh, mm = (int(x) for x in m.groups())
    try:
        return dt.datetime(y, mo, d, hh, mm).isoformat(timespec="minutes")
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 資料庫
# --------------------------------------------------------------------------

SCHEMA_VERSION = "2"

# FTS5 的 unicode61 會把一整串中文當成單一 token,
# 所以「虛擬記憶體分頁機制」裡面搜「分頁」會搜不到。
# 解法: 索引時把每個 CJK 字元拆開用空白隔開, 查詢時做同樣處理再當片語查。
# 顯示用的原文另外存在 raw 欄 (UNINDEXED)。
CJK_RE = re.compile(
    r"[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]"
)


def cjk_space(text):
    """把每個 CJK 字元前後補空白, 讓 unicode61 拆成單字 token。"""
    return CJK_RE.sub(lambda m: f" {m.group(0)} ", text or "")


def cjk_query(q):
    """把使用者查詢裡的中文片段轉成 FTS5 片語, 英文與運算子原樣保留。"""
    out, buf = [], []

    def flush():
        if buf:
            out.append('"' + " ".join(buf) + '"')
            buf.clear()

    for tok in re.findall(r'"[^"]*"|\S+', q or ""):
        if tok.startswith('"') and tok.endswith('"') and len(tok) > 1:
            flush()
            out.append('"' + cjk_space(tok[1:-1]).strip() + '"')
        elif tok in {"AND", "OR", "NOT", "(", ")"}:
            flush()
            out.append(tok)
        elif CJK_RE.search(tok):
            # 中英混雜也一起拆, 例如 "TLB快取"
            buf.extend(cjk_space(tok).split())
        else:
            flush()
            out.append(tok)
    flush()
    return " ".join(out) or q


def open_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files (
        url          TEXT PRIMARY KEY,   -- pluginfile URL (含 revision)
        course       TEXT,
        local_path   TEXT,
        size         INTEGER,
        first_seen   TEXT,
        last_checked TEXT,
        indexed      INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    row = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if row and row[0] != SCHEMA_VERSION:
        # 索引格式換了 -> 砍掉重建 (檔案本身留著, 下面會標記成未索引)
        con.executescript("DROP TABLE IF EXISTS chunks;")
        con.execute("UPDATE files SET indexed=0")
        log("索引格式更新, 重建中")
    con.executescript("""
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
        course, file, page, body, raw UNINDEXED, tokenize='unicode61'
    );
    """)
    con.execute("INSERT INTO meta(k,v) VALUES('schema',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (SCHEMA_VERSION,))
    con.commit()
    return con


def meta_get(con, k, default=None):
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def meta_set(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    con.commit()


# --------------------------------------------------------------------------
# Moodle client
# --------------------------------------------------------------------------

class Moodle:
    def __init__(self, base, username, password, delay=0.6):
        self.base = base.rstrip("/")
        self.username = username
        self._password = password
        self.delay = delay
        self.sesskey = None
        self.s = requests.Session()
        self.s.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) moodle-sync/1.0"
        )

    def _sleep(self):
        time.sleep(self.delay)

    def get(self, url, **kw):
        self._sleep()
        if url.startswith("/"):
            url = self.base + url
        return self.s.get(url, timeout=45, **kw)

    def login(self):
        r = self.get("/login/index.php")
        m = re.search(r'name="logintoken"\s+value="([^"]+)"', r.text)
        token = m.group(1) if m else ""
        self._sleep()
        r = self.s.post(
            f"{self.base}/login/index.php",
            data={"username": self.username,
                  "password": self._password,
                  "logintoken": token},
            timeout=45, allow_redirects=True,
        )
        self._password = None  # 用完就丟
        if "notloggedin" in r.text and "logintoken" in r.text:
            raise RuntimeError("登入失敗 — 帳號或密碼不對 (或學校改了登入流程)")
        m = re.search(r'"sesskey":"(\w+)"', r.text)
        if not m:
            r2 = self.get("/my/")
            m = re.search(r'"sesskey":"(\w+)"', r2.text)
        if not m:
            raise RuntimeError("登入後找不到 sesskey — 頁面結構可能變了")
        self.sesskey = m.group(1)
        log(f"登入成功 (sesskey ok)")

    def ajax(self, method, args):
        self._sleep()
        r = self.s.post(
            f"{self.base}/lib/ajax/service.php",
            params={"sesskey": self.sesskey, "info": method},
            json=[{"index": 0, "methodname": method, "args": args}],
            timeout=45,
        )
        payload = r.json()[0]
        if payload.get("error"):
            exc = payload.get("exception") or {}
            raise RuntimeError(f"AJAX {method} 失敗: {exc.get('message')}")
        return payload["data"]

    # -- 高階 ------------------------------------------------------------

    def enrolled_courses(self):
        return self.ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"offset": 0, "limit": 0, "classification": "all", "sort": "fullname"},
        )["courses"]

    def calendar_events(self, days_back=14, days_ahead=120):
        now = int(time.time())
        data = self.ajax(
            "core_calendar_get_action_events_by_timesort",
            {"timesortfrom": now - days_back * 86400,
             "timesortto": now + days_ahead * 86400,
             "limitnum": 50},
        )
        return data.get("events", [])


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

COURSE_NAME_RE = re.compile(r"^\s*([A-Za-z_0-9]+)\s*-\s*(.+)$", re.S)
# 尾巴的 "-115上(1st semester)" 以及後面可能跟著的合班括號
SEM_TAIL_RE = re.compile(r"\s*-\s*\d{3}[上下]\s*\([^)]*\)\s*(?:\([^)]*\))?\s*$")


def strip_en_paren(s):
    """砍掉尾端那組英文原名括號 (含巢狀), 但中文括號要留著。"""
    s = s.rstrip()
    if not s.endswith(")"):
        return s
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        if s[i] == ")":
            depth += 1
        elif s[i] == "(":
            depth -= 1
            if depth == 0:
                inner = s[i + 1:-1]
                if re.search(r"[A-Za-z]", inner) and not CJK_RE.search(inner):
                    return s[:i].rstrip()
                return s
    return s


def course_folder_name(fullname):
    """
    'ABCD10100-範例課程(Sample Course)-115上(1st semester)'          -> '範例課程'
    'YY__10000-體育(一)_範例AB(Physical Education (I): ...)'          -> '體育(一)_範例AB'
    'GC__10000-範例-課程名 (Sample - Course Name)-114下(..)'          -> '範例-課程名'
    """
    s = (fullname or "").strip()
    s = SEM_TAIL_RE.sub("", s)
    m = COURSE_NAME_RE.match(s)
    if m:
        s = m.group(2)
    s = strip_en_paren(s)
    return safe_name(s.strip()) or safe_name((fullname or "course").split("-")[0])


def course_code(fullname):
    m = COURSE_NAME_RE.match((fullname or "").strip())
    return m.group(1).strip() if m else ""


# 活動連結後面 Moodle 會塞一段類型文字 (檔案 / 作業 / 討論區 ...)，砍掉
TRAILING_TYPE_RE = re.compile(
    r"\s*(檔案|資料夾|作業|討論區|測驗|網址|網頁|頁面|說明|問卷|回饋|"
    r"File|Folder|Assignment|Forum|Quiz|URL|Page)\s*$"
)


def parse_course_page(html, base):
    """回傳 (sections, activities)。activity = dict(type,name,url,section)"""
    soup = BeautifulSoup(html, "html.parser")
    activities = []
    for li in soup.select("li.activity"):
        cls = " ".join(li.get("class") or [])
        m = re.search(r"modtype_(\w+)", cls)
        if not m:
            continue
        mtype = m.group(1)
        a = li.select_one('a.aalink, a[href*="/mod/"]')
        if not a or not a.get("href"):
            continue
        name = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        name = TRAILING_TYPE_RE.sub("", name).strip()
        # 找所屬 section 標題
        sec = ""
        parent = li.find_parent(["li", "div"], class_=re.compile(r"(course-)?section"))
        if parent:
            h = parent.select_one(".sectionname, h3.sectionname, .section-title, h3")
            if h:
                sec = re.sub(r"\s+", " ", h.get_text(" ", strip=True))[:80]
        activities.append({
            "type": mtype,
            "name": name or "(未命名)",
            "url": a["href"],
            "section": sec,
        })
    return activities


def parse_assign_page(html):
    """從作業頁抽出 到期日 / 繳交狀態 / 評分狀態。"""
    soup = BeautifulSoup(html, "html.parser")
    out = {"due": None, "open": None, "submission": None, "grading": None,
           "remaining": None, "description": None}

    date_text = " ".join(
        re.sub(r"\s+", " ", d.get_text(" ", strip=True))
        for d in soup.select('[data-region="activity-dates"] div, .activity-dates div')
    )
    if not date_text:
        date_text = soup.get_text(" ", strip=True)
    m = re.search(r"到期[:：]?\s*([^開]{0,40})", date_text)
    if m:
        out["due"] = parse_zh_datetime(m.group(1)) or parse_zh_datetime(date_text)
    m = re.search(r"開始[:：]?\s*([^到]{0,40})", date_text)
    if m:
        out["open"] = parse_zh_datetime(m.group(1))
    if out["due"] is None:
        # 英文介面 fallback
        m = re.search(r"Due[:\s]+([A-Za-z0-9 ,:]+)", date_text)
        if m:
            for fmt in ("%A, %d %B %Y, %I:%M %p", "%d %B %Y, %I:%M %p"):
                try:
                    out["due"] = dt.datetime.strptime(m.group(1).strip(), fmt).isoformat(timespec="minutes")
                    break
                except ValueError:
                    pass

    label_map = {
        "繳交狀態": "submission", "Submission status": "submission",
        "評分狀態": "grading", "Grading status": "grading",
        "剩餘時間": "remaining", "Time remaining": "remaining",
    }
    for tr in soup.select("table tr"):
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0] in label_map:
            out[label_map[cells[0]]] = cells[1][:120]

    intro = soup.select_one("#intro, .activity-description, div[role='main'] .box.py-3")
    if intro:
        out["description"] = re.sub(r"\s+", " ", intro.get_text(" ", strip=True))[:1200]
    return out


PLUGINFILE_RE = re.compile(r'https?://[^\s"\'<>]*?/pluginfile\.php/[^\s"\'<>]+')


def find_pluginfiles(html):
    urls = []
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select('a[href*="pluginfile.php"], object[data*="pluginfile.php"], '
                          'iframe[src*="pluginfile.php"], source[src*="pluginfile.php"]'):
        u = el.get("href") or el.get("data") or el.get("src")
        if u:
            urls.append(u)
    for u in PLUGINFILE_RE.findall(html):
        urls.append(u)
    seen, out = set(), []
    for u in urls:
        u = u.split("#")[0]
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def filename_from_url(url):
    path = urlparse(url).path
    return unquote(path.rsplit("/", 1)[-1]) or "download.bin"


# --------------------------------------------------------------------------
# 文字抽取
# --------------------------------------------------------------------------

def extract_text(path: Path):
    """回傳 [(page_label, text), ...]。抽不出來回空 list。"""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            import pdfplumber
            out = []
            with pdfplumber.open(str(path)) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    t = page.extract_text() or ""
                    if t.strip():
                        out.append((f"p.{i}", t))
            return out
        if ext == ".pptx":
            from pptx import Presentation
            out = []
            prs = Presentation(str(path))
            for i, slide in enumerate(prs.slides, 1):
                bits = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        bits.append(shape.text_frame.text)
                    if getattr(shape, "has_table", False):
                        for row in shape.table.rows:
                            bits.append(" ".join(c.text for c in row.cells))
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    bits.append(slide.notes_slide.notes_text_frame.text)
                t = "\n".join(b for b in bits if b and b.strip())
                if t.strip():
                    out.append((f"slide {i}", t))
            return out
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            paras = [p.text for p in d.paragraphs if p.text.strip()]
            for tbl in d.tables:
                for row in tbl.rows:
                    paras.append(" ".join(c.text for c in row.cells))
            # 每 40 段當一個 chunk
            out, buf = [], []
            for i, p in enumerate(paras, 1):
                buf.append(p)
                if len(buf) >= 40:
                    out.append((f"~para {i-len(buf)+1}", "\n".join(buf)))
                    buf = []
            if buf:
                out.append((f"~para {len(paras)-len(buf)+1}", "\n".join(buf)))
            return out
        if ext in {".txt", ".md", ".csv", ".py", ".c", ".cpp", ".h", ".java"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            out = []
            for i in range(0, len(lines), 120):
                blk = "\n".join(lines[i:i + 120])
                if blk.strip():
                    out.append((f"L{i+1}", blk))
            return out
    except Exception as e:
        log(f"抽文字失敗 {path.name}: {e}", "WARN")
    return []


def unzip_and_collect(zip_path: Path, dest: Path):
    """把 zip 解到同名資料夾, 回傳解出來、可索引的檔案清單。"""
    out = []
    target = dest / (zip_path.stem + "_unzipped")
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner = Path(info.filename)
                if inner.suffix.lower() not in INDEX_EXT:
                    continue
                if ".." in inner.parts or inner.is_absolute():
                    continue
                safe_rel = Path(*[safe_name(p, 60) for p in inner.parts])
                dst = target / safe_rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists() or dst.stat().st_size != info.file_size:
                    with z.open(info) as src, open(dst, "wb") as fh:
                        fh.write(src.read())
                out.append(dst)
    except zipfile.BadZipFile:
        log(f"壞掉的 zip: {zip_path.name}", "WARN")
    except Exception as e:
        log(f"解壓失敗 {zip_path.name}: {e}", "WARN")
    return out


def index_file(con, course, path: Path):
    rel = str(path)
    con.execute("DELETE FROM chunks WHERE file=?", (rel,))
    n = 0
    for page, body in extract_text(path):
        raw = re.sub(r"[ \t]+", " ", body)[:20000]
        con.execute("INSERT INTO chunks(course,file,page,body,raw) VALUES(?,?,?,?,?)",
                    (course, rel, page, cjk_space(raw), raw))
        n += 1
    con.commit()
    return n


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def choose_semester(courses, want):
    toks = []
    for c in courses:
        toks += re.findall(r"\d{3}[上下]", c.get("fullname") or "")
    if not toks:
        return None
    if want and want.lower() != "auto":
        return want.strip()
    return max(set(toks), key=semester_key)


def run(mode="heavy"):
    light = mode == "light"
    cfg = configparser.ConfigParser()
    if not CONFIG_PATH.exists():
        raise SystemExit(f"找不到 {CONFIG_PATH}")
    cfg.read(CONFIG_PATH, encoding="utf-8-sig")  # -sig: 容忍記事本存出來的 BOM
    S = cfg["moodle"]

    base = S.get("base_url", "https://elearn4.ndhu.edu.tw/moodle")
    username = S.get("username", "").strip()
    root = Path((S.get("root_folder") or "").strip() or str(HERE.parent))
    want_sem = S.get("semester", "auto")
    do_download = S.getboolean("download_materials", True) and not light
    do_index = S.getboolean("build_index", True)
    max_mb = S.getfloat("max_file_mb", 80.0)
    delay = S.getfloat("request_delay", 0.6)
    skip_courses = {x.strip() for x in S.get("skip_courses", "").split(",") if x.strip()}
    skip_files = [x.strip() for x in S.get("skip_files", "").split(",") if x.strip()]

    if not username:
        raise SystemExit("config.ini 裡的 username 是空的")

    con = open_db()
    started = dt.datetime.now()
    try:
        prev = json.loads(JSON_PATH.read_text(encoding="utf-8")) if JSON_PATH.exists() else {}
    except Exception:
        prev = {}
    # 輕量模式不下載, 教材的本機路徑和數量沿用上一次完整同步的結果
    prev_local = {a.get("url"): a["local"] for c in prev.get("courses", [])
                  for a in c.get("activities", []) if a.get("local")}
    prev_materials = {c.get("id"): c.get("materials", 0) for c in prev.get("courses", [])}
    snapshot = {
        "generated_at": started.isoformat(timespec="seconds"),
        "semester": None,
        "courses": [],
        "assignments": [],
        "events": [],
        "new_files": [],
        "errors": [],
        "stats": {},
    }

    # 索引格式換版, 或上次索引到一半掛掉 -> 先把舊檔補索引 (不需要網路)
    if do_index and not light:
        stale = con.execute(
            "SELECT course, local_path FROM files WHERE indexed=0").fetchall()
        if stale:
            log(f"補索引 {len(stale)} 個既有檔案")
            for course, lp in stale:
                p = Path(lp)
                if not p.exists():
                    continue
                targets = [p]
                if p.suffix.lower() == ".zip":
                    targets = unzip_and_collect(p, p.parent)
                for t in targets:
                    if t.suffix.lower() in INDEX_EXT:
                        index_file(con, course, t)
                con.execute("UPDATE files SET indexed=1 WHERE local_path=?", (lp,))
            con.commit()

    mo = Moodle(base, username, load_password(), delay=delay)
    mo.login()

    all_courses = mo.enrolled_courses()
    sem = choose_semester(all_courses, want_sem)
    snapshot["semester"] = sem
    if not light:
        log(f"目標學期: {sem}  (選課總數 {len(all_courses)})")

    courses = [c for c in all_courses
               if sem and sem in (c.get("fullname") or "")
               and course_code(c.get("fullname")) not in skip_courses]
    if not light:
        log(f"本學期課程 {len(courses)} 門")

    new_files_total = 0
    indexed_total = 0

    def note_local(entry, label, path):
        """記下「這個 e學苑活動 → 本機哪個檔」, 給儀表板直接開檔案總管用。"""
        entry.setdefault("_local", {}).setdefault(label, [])
        if str(path) not in entry["_local"][label]:
            entry["_local"][label].append(str(path))

    def download_file(url, label, folder, dest_dir, entry):
        """下載一個 pluginfile。已存在且 URL 沒變就跳過。回傳 True 表示這次是新抓的。"""
        nonlocal new_files_total, indexed_total
        fname = filename_from_url(url)
        if Path(fname).suffix.lower() not in DOWNLOAD_EXT:
            return False
        # config.ini 的 skip_files: 不想要的檔案 (太大/用不到), 永遠不抓。
        # 就算本機被刪掉也不會再抓回來 —— 這正是設它的用意。
        for pat in skip_files:
            if fnmatch.fnmatch(fname.lower(), pat.lower()):
                log(f"略過 {folder}/{fname} (config.ini skip_files: {pat})")
                old = dest_dir / safe_name(fname, 120)
                if old.exists():
                    note_local(entry, label, old)
                return False
        key = url.split("?")[0]
        row = con.execute("SELECT local_path FROM files WHERE url=?", (key,)).fetchone()
        dest = dest_dir / safe_name(fname, 120)
        if row and Path(row[0]).exists():
            con.execute("UPDATE files SET last_checked=? WHERE url=?",
                        (started.isoformat(timespec="seconds"), key))
            con.commit()
            entry["materials"] += 1
            note_local(entry, label, Path(row[0]))
            return False
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with mo.s.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                clen = int(resp.headers.get("content-length") or 0)
                if clen and clen > max_mb * 1024 * 1024:
                    log(f"跳過過大檔案 {fname} ({clen/1048576:.1f}MB)", "WARN")
                    return False
                tmp = dest.with_suffix(dest.suffix + ".part")
                written = 0
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(65536):
                        fh.write(chunk)
                        written += len(chunk)
                        if written > max_mb * 1024 * 1024:
                            break
                tmp.replace(dest)
            con.execute(
                "INSERT OR REPLACE INTO files(url,course,local_path,size,first_seen,last_checked,indexed)"
                " VALUES(?,?,?,?,?,?,0)",
                (key, folder, str(dest), dest.stat().st_size,
                 started.isoformat(timespec="seconds"),
                 started.isoformat(timespec="seconds")))
            con.commit()
            new_files_total += 1
            entry["materials"] += 1
            note_local(entry, label, dest)
            snapshot["new_files"].append(
                {"course": folder, "file": dest.name, "activity": label,
                 "path": str(dest), "size": dest.stat().st_size})
            log(f"下載 {folder}/{dest.name}")

            if do_index:
                targets = [dest]
                if dest.suffix.lower() == ".zip":
                    targets = unzip_and_collect(dest, dest_dir)
                for t in targets:
                    if t.suffix.lower() in INDEX_EXT:
                        indexed_total += index_file(con, folder, t)
                con.execute("UPDATE files SET indexed=1 WHERE url=?", (key,))
                con.commit()
            return True
        except Exception as e:
            snapshot["errors"].append(f"{folder}/{fname}: 下載失敗 {e}")
            log(f"下載失敗 {fname}: {e}", "WARN")
            return False

    for c in courses:
        cid = c["id"]
        fullname = c.get("fullname") or str(cid)
        folder = course_folder_name(fullname)
        code = course_code(fullname)
        cdir = root / folder
        mdir = cdir / "教材"
        adir = cdir / "作業"
        try:
            mdir.mkdir(parents=True, exist_ok=True)
            adir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            snapshot["errors"].append(f"{folder}: 建資料夾失敗 {e}")
        entry = {
            "id": cid, "code": code, "name": folder, "fullname": fullname,
            "url": c.get("viewurl") or f"{base}/course/view.php?id={cid}",
            "folder": str(cdir), "activities": [], "assignments": [],
            "counts": {}, "materials": 0,
        }
        try:
            html = mo.get(f"/course/view.php?id={cid}").text
        except Exception as e:
            snapshot["errors"].append(f"{folder}: 課程頁抓取失敗 {e}")
            snapshot["courses"].append(entry)
            continue

        acts = parse_course_page(html, base)
        for a in acts:
            entry["counts"][a["type"]] = entry["counts"].get(a["type"], 0) + 1
        entry["activities"] = [
            {"type": a["type"], "name": a["name"], "url": a["url"], "section": a["section"]}
            for a in acts
        ]

        # ---- 作業 ----
        for a in acts:
            if a["type"] != "assign":
                continue
            try:
                ah = mo.get(a["url"]).text
                info = parse_assign_page(ah)
            except Exception as e:
                snapshot["errors"].append(f"{folder}/{a['name']}: 作業頁失敗 {e}")
                continue
            rec = {"course": folder, "course_code": code, "name": a["name"],
                   "url": a["url"], **info}
            if info.get("due"):
                try:
                    due = dt.datetime.fromisoformat(info["due"])
                    rec["days_left"] = round((due - started).total_seconds() / 86400, 1)
                except ValueError:
                    pass
            done = (info.get("submission") or "")
            rec["submitted"] = bool(re.search(r"已(繳交|送出)|Submitted", done))
            entry["assignments"].append(rec)
            snapshot["assignments"].append(rec)
            # 作業說明附檔 (spec PDF 之類) 收進 <科目>\作業\
            if do_download:
                for u in find_pluginfiles(ah):
                    if "mod_assign/intro" in u or "mod_assign/introattachment" in u:
                        download_file(u, a["name"], folder, adir, entry)

        # ---- 教材 ----
        if do_download:
            file_urls = []
            for a in acts:
                if a["type"] == "resource":
                    try:
                        r = mo.get(a["url"], allow_redirects=True)
                    except Exception as e:
                        snapshot["errors"].append(f"{folder}/{a['name']}: {e}")
                        continue
                    ctype = r.headers.get("content-type", "")
                    if "text/html" in ctype:
                        file_urls += [(u, a["name"]) for u in find_pluginfiles(r.text)]
                    else:
                        file_urls.append((r.url, a["name"]))
                elif a["type"] == "folder":
                    try:
                        r = mo.get(a["url"])
                        file_urls += [(u, a["name"]) for u in find_pluginfiles(r.text)]
                    except Exception as e:
                        snapshot["errors"].append(f"{folder}/{a['name']}: {e}")

            for url, label in file_urls:
                download_file(url, label, folder, mdir, entry)

        # 每個教材活動帶上本機路徑 (一個檔就指檔案, 多個檔就指它們所在的資料夾)
        loc = entry.pop("_local", {})
        for act in entry["activities"]:
            ps = loc.get(act["name"])
            if ps and act["type"] in ("resource", "folder"):
                act["local"] = ps[0] if len(ps) == 1 else str(Path(ps[0]).parent)
            elif light and act["url"] in prev_local and act["type"] in ("resource", "folder"):
                act["local"] = prev_local[act["url"]]
        if light:
            entry["materials"] = prev_materials.get(cid, 0)

        snapshot["courses"].append(entry)
        if not light:   # 輕量模式一天跑幾十次, 只記總結那行
            log(f"  {folder}: 活動 {len(acts)} / 作業 {len(entry['assignments'])} / 教材 {entry['materials']}")

    # ---- 行事曆 ----
    try:
        for e in mo.calendar_events():
            snapshot["events"].append({
                "name": e.get("name"),
                "course": course_folder_name((e.get("course") or {}).get("fullname", "")),
                "module": e.get("modulename"),
                "when": dt.datetime.fromtimestamp(e["timesort"]).isoformat(timespec="minutes"),
                "url": ((e.get("action") or {}).get("url")) or e.get("url"),
                "actionable": (e.get("action") or {}).get("actionable"),
            })
    except Exception as e:
        snapshot["errors"].append(f"行事曆抓取失敗: {e}")

    if light:
        snapshot["new_files"] = prev.get("new_files", [])   # 「上次完整同步新下載的」, 留給每日更新回報
    snapshot["mode"] = mode
    snapshot["assignments"].sort(key=lambda r: (r.get("due") is None, r.get("due") or ""))
    snapshot["events"].sort(key=lambda r: r["when"])
    snapshot["stats"] = {
        "courses": len(snapshot["courses"]),
        "assignments": len(snapshot["assignments"]),
        "pending": sum(1 for a in snapshot["assignments"] if not a.get("submitted")),
        "new_files": new_files_total,
        "new_chunks": indexed_total,
        "indexed_files": con.execute("SELECT COUNT(DISTINCT file) FROM chunks").fetchone()[0],
        "indexed_chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        # 每科索引了幾個檔、幾段文字 (儀表板「教材」分頁用)
        "index": [{"course": c, "files": f, "chunks": n} for c, f, n in con.execute(
            "SELECT course, COUNT(DISTINCT file), COUNT(*) FROM chunks GROUP BY course ORDER BY COUNT(*) DESC")],
        "total_files": con.execute("SELECT COUNT(*) FROM files").fetchone()[0],
        "elapsed_sec": round((dt.datetime.now() - started).total_seconds(), 1),
    }

    changed = content_view(snapshot, not light) != content_view(prev, not light)
    if changed:
        tmp = JSON_PATH.with_name(JSON_PATH.name + ".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, JSON_PATH)
    meta_set(con, "last_run", started.isoformat(timespec="seconds"))
    if not light:
        meta_set(con, "last_success", started.isoformat(timespec="seconds"))
    st = snapshot["stats"]
    if light:
        log(f"輕量同步{'完成' if changed else ', 沒有變化'} — 作業 {st['assignments']} (未交 {st['pending']}) / {st['elapsed_sec']}s")
    else:
        log(f"完成{'' if changed else ' (沒有變化)'} — {st}")
    con.close()
    return {"changed": changed, "pending": st["pending"], "assignments": st["assignments"]}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    argv = sys.argv[1:]
    con = open_db()
    last_heavy = meta_get(con, "last_success")
    last_any = meta_get(con, "last_run") or last_heavy
    con.close()

    def parse(v):
        try:
            return dt.datetime.fromisoformat(v) if v else None
        except ValueError:
            return None

    if "--force" in argv or "--heavy" in argv:
        mode = "heavy"
    elif "--light" in argv:
        mode = "light"
    else:
        lh, la = parse(last_heavy), parse(last_any)
        if not lh or lh.date() != dt.date.today():
            mode = "heavy"
        elif la and (dt.datetime.now() - la).total_seconds() < MIN_GAP_MIN * 60:
            return 0                                   # 剛跑過, 安靜跳過 (不寫 log)
        else:
            mode = "light"

    rotate_log()
    if not acquire_lock(mode):
        log("另一個 e學苑同步還在跑, 這次跳過")
        return 0
    write_status(started_at=now_iso(), mode=mode)
    try:
        res = run(mode)
        fin = now_iso()
        write_status(finished_at=fin, ok=True, error=None, changed=res["changed"],
                     **({"changed_at": fin} if res["changed"] else {}),
                     **({"last_heavy": fin} if mode == "heavy" else {}))
        return 0
    except SystemExit as exc:
        write_status(finished_at=now_iso(), ok=False, error=str(exc.code))
        raise
    except Exception:
        tb = traceback.format_exc()
        log(f"同步失敗 ({mode}):\n" + tb, "ERROR")
        write_status(finished_at=now_iso(), ok=False, error=tb.strip().splitlines()[-1])
        # 完整同步失敗才寫進 latest.json 讓儀表板看得到;
        # 輕量同步一天幾十次, 偶爾網路抖一下不要洗版 (狀態在 status_moodle.json)
        if mode == "heavy":
            try:
                prev = json.loads(JSON_PATH.read_text(encoding="utf-8")) if JSON_PATH.exists() else {}
            except Exception:
                prev = {}
            prev["last_run_failed"] = dt.datetime.now().isoformat(timespec="seconds")
            prev.setdefault("errors", []).append(tb.strip().splitlines()[-1])
            JSON_PATH.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
