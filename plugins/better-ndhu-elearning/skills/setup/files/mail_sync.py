#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDHU gms 信箱同步 — 用 IMAP 把最近的信抓成 mail.json。

跟 moodle_sync.py 同一套做法: 全部在你自己的電腦上跑, 密碼用 Windows DPAPI
加密存在 mail_cred.dat, Claude 只會去讀產生出來的 mail.json。

第一次用:
    pip install pywin32
    python set_mail_password.py        # 貼 Gmail 應用程式密碼
    python mail_sync.py --test         # 只登入試試, 不寫檔
    python mail_sync.py                # 正式跑一次

增量同步 (0.4 起): 已經抓過的信只重讀「已讀/未讀」狀態, 只有新信才下載內文,
所以一次只要幾秒, 排程每 10 分鐘跑一次也不會吃資源。內容沒變就不重寫 mail.json。
同一時間只會跑一個 (mail.lock); 每次的結果記在 status_mail.json, 給 betterel 看。

常用:
    python mail_sync.py --full         # 忽略快取, 全部重抓
    python mail_sync.py --days 30 --limit 120
    python mail_sync.py --unread-only
    python mail_sync.py --no-body      # 只抓標題寄件者, 不抓內文
    python mail_sync.py --stats        # 看上次抓的結果摘要
"""

import argparse
import base64
import configparser
import email
import html as html_mod
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.ini"
CRED_PATH = HERE / "mail_cred.dat"
OUT_PATH = HERE / "mail.json"
LOG_PATH = HERE / "mail_log.txt"
LOCK_PATH = HERE / "mail.lock"
STATUS_PATH = HERE / "status_mail.json"
LOCK_STALE_MIN = 15      # 鎖超過這麼久還在 = 上次當掉了, 直接接手
NEW_HOURS = 24           # 第一次看到在 24 小時內的信算「新信」(每日更新靠它推播)
LOG_MAX_BYTES = 1_000_000

TZ = timezone(timedelta(hours=8))
CRED_DESC = "ndhu-mail-sync"

DEFAULTS = {
    "imap_host": "imap.gmail.com",
    "imap_port": "993",
    "folder": "INBOX",
    "days": "14",
    "limit": "80",
    "include_body": "true",
    "body_chars": "1200",
    "unread_only": "false",
    "max_fetch_kb": "512",
    "skip_senders": "",
    "highlight_keywords": ("作業,考試,期中,期末,停課,補課,調課,繳費,註冊,選課,成績,"
                           "獎學金,實習,面試,截止,逾期,重要,通知"),
    "request_delay": "0.1",
}


# ---------------------------------------------------------------- 小工具

def log(msg, level="INFO"):
    line = f"{datetime.now(TZ):%Y-%m-%d %H:%M:%S} [{level}] {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def rotate_log():
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH.with_name(LOG_PATH.stem + ".old.txt"))
    except OSError:
        pass


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def acquire_lock(mode):
    """同一時間只讓一個同步在跑。拿不到鎖回 False。"""
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
            except OSError:
                continue                      # 剛好被刪掉, 再搶一次
            if age < LOCK_STALE_MIN * 60:
                return False
            try:
                LOCK_PATH.unlink()            # 上次當掉留下的舊鎖
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


def as_bool(v, fallback=False):
    if v is None:
        return fallback
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def load_config():
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cp.read(CONFIG_PATH, encoding="utf-8")
        if cp.has_section("mail"):
            for k, v in cp.items("mail"):
                cfg[k.strip()] = v.strip()
        if not cfg.get("address") and cp.has_section("moodle"):
            user = (cp.get("moodle", "username", fallback="") or "").strip()
            if user:
                cfg["address"] = f"{user}@gms.ndhu.edu.tw"
    return cfg


def load_password():
    """從 mail_cred.dat 解出應用程式密碼 (只有這台電腦的這個帳戶解得開)。"""
    if not CRED_PATH.exists():
        raise SystemExit("找不到 mail_cred.dat — 先跑:  python set_mail_password.py")
    try:
        import win32crypt  # type: ignore
    except ImportError:
        raise SystemExit("缺少 pywin32。先跑:  pip install pywin32")
    blob = base64.b64decode(CRED_PATH.read_bytes())
    try:
        _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    except Exception as exc:
        raise SystemExit(f"mail_cred.dat 解不開 ({exc}) — 重跑 set_mail_password.py")
    return data.decode("utf-8")


def decode_mime(value):
    """把 =?UTF-8?B?...?= 這種標頭還原成正常文字。"""
    if not value:
        return ""
    out = []
    try:
        parts = decode_header(value)
    except Exception:
        return str(value)
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            for enc in (charset, "utf-8", "big5", "cp950", "latin-1"):
                if not enc:
                    continue
                try:
                    out.append(chunk.decode(enc, errors="strict"))
                    break
                except Exception:
                    continue
            else:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def html_to_text(raw):
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html_mod.unescape(raw)


def part_text(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset()
    for enc in (charset, "utf-8", "big5", "cp950", "latin-1"):
        if not enc:
            continue
        try:
            return payload.decode(enc, errors="strict")
        except Exception:
            continue
    return payload.decode("utf-8", errors="replace")


def extract_body(msg, limit):
    """抓內文: 優先 text/plain, 沒有才把 html 去標籤。"""
    plain, rich = [], []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain.append(part_text(part))
            elif ctype == "text/html":
                rich.append(html_to_text(part_text(part)))
    else:
        if msg.get_content_type() == "text/html":
            rich.append(html_to_text(part_text(msg)))
        else:
            plain.append(part_text(msg))

    text = "\n".join(plain).strip() or "\n".join(rich).strip()
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip() + " …"
    return text, truncated


def attachment_names(msg):
    names = []
    if not msg.is_multipart():
        return names
    for part in msg.walk():
        disp = str(part.get("Content-Disposition") or "").lower()
        fname = part.get_filename()
        if fname and ("attachment" in disp or "inline" not in disp):
            name = decode_mime(fname)
            if name and name not in names:
                names.append(name)
    return names


# ---------------------------------------------------------------- IMAP

FETCH_META_RE = re.compile(
    rb"UID\s+(?P<uid>\d+)|X-GM-THRID\s+(?P<thrid>\d+)|RFC822\.SIZE\s+(?P<size>\d+)|FLAGS\s+\((?P<flags>[^)]*)\)"
)


def parse_meta(blob):
    meta = {"uid": None, "thrid": None, "size": 0, "flags": ""}
    for m in FETCH_META_RE.finditer(blob or b""):
        if m.group("uid"):
            meta["uid"] = m.group("uid").decode()
        if m.group("thrid"):
            meta["thrid"] = m.group("thrid").decode()
        if m.group("size"):
            meta["size"] = int(m.group("size"))
        if m.group("flags") is not None:
            meta["flags"] = m.group("flags").decode("latin-1")
    return meta


def connect(cfg, password):
    host = cfg["imap_host"]
    port = int(cfg["imap_port"])
    log(f"連線 {host}:{port} — {cfg['address']}")
    box = imaplib.IMAP4_SSL(host, port, timeout=45)
    try:
        box.login(cfg["address"], password)
    except imaplib.IMAP4.error as exc:
        detail = str(exc)
        hint = ""
        low = detail.lower()
        if "imap access is disabled" in low or "imap is disabled" in low:
            hint = ("\n  → 這個網域把 IMAP 關掉了 (學校管理員設定), 腳本這條路走不通,"
                    " 只能改用轉寄到個人信箱。")
        elif "application-specific" in low or "invalid credentials" in low:
            hint = ("\n  Gmail 說帳密不對。照順序檢查:"
                    "\n  1) 存進去的是不是「應用程式密碼」? 跑 python mail_sync.py --check-cred"
                    " 看形狀 (應為 16 碼純小寫英文字母, 不會印出密碼本身)。"
                    "\n  2) 那組密碼是不是在「gms 帳號」底下產生的? 在 apppasswords 頁面右上角"
                    " 確認登入的是 你的學號@gms.ndhu.edu.tw, 不是個人 Gmail。"
                    "\n  3) apppasswords 頁面若顯示「這項設定不適用於你的帳戶」, 代表沒開兩步驟驗證,"
                    " 或學校管理員鎖住了 —— 那就只能改用轉寄到個人信箱。")
        raise SystemExit(f"登入失敗: {detail}{hint}")
    log("登入成功")
    return box


def search_uids(box, cfg, days, unread_only):
    folder = cfg["folder"]
    typ, _ = box.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        raise SystemExit(f"打不開信件匣 {folder!r} — 檢查 config.ini 的 folder")
    try:
        _t, uv = box.response("UIDVALIDITY")
        uidvalidity = (uv[0].decode() if uv and uv[0] else None)
    except Exception:
        uidvalidity = None
    since = (datetime.now(TZ) - timedelta(days=days)).strftime("%d-%b-%Y")
    crit = [f'SINCE "{since}"']
    if unread_only:
        crit.append("UNSEEN")
    typ, data = box.uid("search", None, f'({" ".join(crit)})')
    if typ != "OK":
        raise SystemExit("IMAP search 失敗")
    uids = (data[0] or b"").split()
    return [u.decode() for u in uids], uidvalidity


def bulk_meta(box, uids):
    """一個來回拿到所有 uid 的 FLAGS / thread id / 大小 (不抓內容)。"""
    out = {}
    for k in range(0, len(uids), 200):
        chunk = uids[k:k + 200]
        typ, data = box.uid("fetch", ",".join(chunk), "(UID FLAGS X-GM-THRID RFC822.SIZE)")
        if typ != "OK":
            raise SystemExit("IMAP 讀旗標失敗")
        for item in data or []:
            blob = item[0] if isinstance(item, tuple) else item
            if not isinstance(blob, (bytes, bytearray)):
                continue
            meta = parse_meta(blob)
            if meta["uid"]:
                out[meta["uid"]] = meta
    return out


def fetch_message(box, uid, meta, cfg, want_body):
    max_bytes = int(cfg["max_fetch_kb"]) * 1024
    heavy = bool(meta["size"] and meta["size"] > max_bytes)
    spec = "(BODY.PEEK[HEADER])" if (heavy or not want_body) else "(BODY.PEEK[])"
    typ, data = box.uid("fetch", uid, spec)
    if typ != "OK" or not data or not isinstance(data[0], tuple):
        return None
    return email.message_from_bytes(data[0][1]), heavy


def make_record(uid, meta, msg, heavy, cfg, want_body, body_chars, keywords, skip_senders):
    """把一封信整理成 mail.json 的一筆。寄件者在 skip_senders 裡就回 None。"""
    # 先把整行 From 解碼再拆, 有些信會把「名字 <位址>」整串編碼掉
    from_name, from_addr = parseaddr(decode_mime(msg.get("From", "")))
    from_name = (from_name or from_addr).strip()
    from_addr = (from_addr or "").lower()
    if any(s in from_addr for s in skip_senders):
        return None

    subject = decode_mime(msg.get("Subject", "")) or "(無主旨)"
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        dt = dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)
    except Exception:
        dt = datetime.now(TZ)

    body, truncated = ("", False)
    if want_body and not heavy:
        body, truncated = extract_body(msg, body_chars)

    haystack = f"{subject}\n{body}"
    hits = [k for k in keywords if k in haystack]

    thrid = meta.get("thrid")
    link = (f"https://mail.google.com/mail/u/?authuser={cfg['address']}"
            f"#all/{int(thrid):x}") if thrid else \
           f"https://mail.google.com/mail/u/?authuser={cfg['address']}"

    return {
        "uid": meta.get("uid") or uid,
        "date": dt.isoformat(timespec="seconds"),
        "from_name": from_name,
        "from_addr": from_addr,
        "to": decode_mime(msg.get("To", "")),
        "subject": subject,
        "unread": "\\Seen" not in meta["flags"],
        "starred": "\\Flagged" in meta["flags"],
        "attachments": attachment_names(msg),
        "body": body,
        "body_truncated": truncated or heavy,
        "size_kb": round(meta["size"] / 1024, 1) if meta["size"] else None,
        "keywords": hits,
        "link": link,
    }


CONTENT_KEYS = ("account", "folder", "window_days", "include_body", "messages")


def content_key(p):
    return json.dumps({k: (p or {}).get(k) for k in CONTENT_KEYS}, ensure_ascii=False, sort_keys=True)


def run_sync(args):
    """回傳 {"changed", "total", "unread", "fetched"}; --test 時回 {"test": True}。"""
    cfg = load_config()
    if not cfg.get("address"):
        raise SystemExit("config.ini 的 [mail] 沒有 address — 填上 你的學號@gms.ndhu.edu.tw")

    days = args.days or int(cfg["days"])
    limit = args.limit or int(cfg["limit"])
    unread_only = args.unread_only or as_bool(cfg["unread_only"])
    want_body = (not args.no_body) and as_bool(cfg["include_body"], True)
    body_chars = int(cfg["body_chars"])
    delay = float(cfg["request_delay"])
    skip_senders = [s.strip().lower() for s in cfg["skip_senders"].split(",") if s.strip()]
    keywords = [k.strip() for k in cfg["highlight_keywords"].split(",") if k.strip()]

    started = time.time()
    now = datetime.now(TZ)
    password = load_password()
    box = connect(cfg, password)
    del password

    try:
        uids, uidvalidity = search_uids(box, cfg, days, unread_only)
        if args.test:
            log(f"最近 {days} 天共 {len(uids)} 封" + (" (只看未讀)" if unread_only else ""))
            log("--test: 只驗證登入, 不寫檔")
            return {"test": True}
        uids = uids[-limit:]

        prev = {}
        if OUT_PATH.exists():
            try:
                prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        # 快取只在「同一個信箱、同一個信件匣、UID 沒被重排」時能沿用
        reuse = (not args.full and uidvalidity is not None
                 and prev.get("uidvalidity") == uidvalidity
                 and prev.get("account") == cfg["address"]
                 and prev.get("folder") == cfg["folder"]
                 and prev.get("include_body", True) == want_body)
        previous = {m.get("uid"): m for m in prev.get("messages", [])} if reuse else {}
        # 就算要重抓內文, 「第一次看到的時間」還是沿用 (不然升級那次每封都會被當成新信推播)
        known = ({m.get("uid"): m for m in prev.get("messages", [])}
                 if prev.get("account") == cfg["address"] and prev.get("uidvalidity") in (None, uidvalidity) else {})
        prev_gen = prev.get("generated_at") or now.isoformat(timespec="seconds")

        def first_seen_of(old):
            if old.get("first_seen"):
                return old["first_seen"]
            # 0.3 以前的 mail.json 沒記: 上次標成新信的算上次同步, 其他用寄信時間
            return prev_gen if old.get("new") else min(old.get("date") or prev_gen, prev_gen)
        new_since = (now - timedelta(hours=NEW_HOURS)).isoformat(timespec="seconds")

        metas = bulk_meta(box, uids)
        messages, skipped, fetched = [], 0, 0
        for uid in reversed(uids):          # 新的在前面
            meta = metas.get(uid)
            if not meta:
                continue
            old = previous.get(uid)
            if old and old.get("from_addr") is not None:
                if any(s in (old.get("from_addr") or "") for s in skip_senders):
                    skipped += 1
                    continue
                rec = dict(old)
                rec["unread"] = "\\Seen" not in meta["flags"]
                rec["starred"] = "\\Flagged" in meta["flags"]
                rec["first_seen"] = first_seen_of(old)
            else:
                try:
                    got = fetch_message(box, uid, meta, cfg, want_body)
                except Exception as exc:
                    log(f"uid {uid} 抓失敗: {exc}", "WARN")
                    continue
                if not got:
                    continue
                msg, heavy = got
                fetched += 1
                rec = make_record(uid, meta, msg, heavy, cfg, want_body, body_chars, keywords, skip_senders)
                if rec is None:
                    skipped += 1
                    continue
                rec["first_seen"] = first_seen_of(known[uid]) if uid in known else now.isoformat(timespec="seconds")
                if delay:
                    time.sleep(delay)
            rec["new"] = rec["first_seen"] >= new_since
            messages.append(rec)

        messages.sort(key=lambda m: m["date"], reverse=True)
        body = {"account": cfg["address"], "folder": cfg["folder"], "window_days": days,
                "include_body": want_body, "uidvalidity": uidvalidity, "messages": messages}
        changed = content_key(body) != content_key(prev)
        elapsed = round(time.time() - started, 1)
        stats = {
            "total": len(messages),
            "unread": sum(1 for m in messages if m["unread"]),
            "new": sum(1 for m in messages if m["new"]),
            "with_attachments": sum(1 for m in messages if m["attachments"]),
            "highlighted": sum(1 for m in messages if m["keywords"]),
            "skipped_senders": skipped,
            "fetched": fetched,
            "elapsed_sec": elapsed,
        }
        if changed:
            payload = {"generated_at": now.isoformat(timespec="seconds"), **body, "stats": stats}
            tmp = OUT_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(OUT_PATH)
            log(f"完成 — {stats['total']} 封 / 未讀 {stats['unread']} / 新下載 {fetched} / "
                f"關鍵字命中 {stats['highlighted']} / {elapsed}s")
        else:
            log(f"沒有變化 — {stats['total']} 封 / 未讀 {stats['unread']} / {elapsed}s")
        return {"changed": changed, "total": stats["total"], "unread": stats["unread"], "fetched": fetched}
    finally:
        try:
            box.logout()
        except Exception:
            pass


def show_stats():
    if not OUT_PATH.exists():
        print("還沒有 mail.json — 先跑一次 python mail_sync.py")
        return 1
    data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    s = data.get("stats", {})
    print(f"帳號      : {data.get('account')}")
    print(f"擷取時間  : {data.get('generated_at')}")
    print(f"範圍      : 最近 {data.get('window_days')} 天 · {data.get('folder')}")
    print(f"總數/未讀 : {s.get('total')} / {s.get('unread')}")
    print(f"關鍵字    : {s.get('highlighted')} 封命中")
    print()
    for m in data.get("messages", [])[:10]:
        mark = "●" if m["unread"] else "○"
        kw = (" [" + "/".join(m["keywords"]) + "]") if m["keywords"] else ""
        print(f"{mark} {m['date'][:16].replace('T',' ')}  {m['from_name'][:18]:<18} {m['subject'][:44]}{kw}")
    return 0


def check_cred():
    """只檢查 mail_cred.dat 存的東西「長什麼樣子」, 不會印出密碼本身。"""
    try:
        pw = load_password()
    except SystemExit as exc:
        print(exc)
        return 1
    kinds = []
    if any(c.islower() for c in pw):
        kinds.append("小寫字母")
    if any(c.isupper() for c in pw):
        kinds.append("大寫字母")
    if any(c.isdigit() for c in pw):
        kinds.append("數字")
    if any((not c.isalnum()) and not c.isspace() for c in pw):
        kinds.append("符號")
    length = len(pw)
    looks_right = length == 16 and pw.isalpha() and pw.islower()
    del pw

    print("mail_cred.dat : 解得開")
    print(f"長度     : {length} 碼")
    print("字元種類 :", "、".join(kinds) or "(空的?)")
    if looks_right:
        print("形狀     : 16 碼純小寫字母 — 符合 Gmail 應用程式密碼的長相")
        print("→ 那問題多半在「這組是不是用 gms 帳號產生的」。")
    else:
        print("形狀     : 不像應用程式密碼 (應該是 16 碼、純小寫英文字母、沒有數字或符號)")
        print("→ 你存進去的八成是平常登入用的密碼。到 myaccount.google.com/apppasswords")
        print("  (用 gms 帳號登入) 產生一組, 再跑一次 set_mail_password.py。")
    print("(密碼本身不會印出來)")
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="把 gms 信箱最近的信抓成 mail.json")
    ap.add_argument("--days", type=int, help="往回抓幾天 (預設看 config.ini)")
    ap.add_argument("--limit", type=int, help="最多幾封")
    ap.add_argument("--unread-only", action="store_true", help="只抓未讀")
    ap.add_argument("--no-body", action="store_true", help="不抓內文, 只留標題寄件者")
    ap.add_argument("--test", action="store_true", help="只驗證登入, 不寫檔")
    ap.add_argument("--full", action="store_true", help="忽略上次的結果, 每封信都重新下載")
    ap.add_argument("--stats", action="store_true", help="印出 mail.json 的摘要")
    ap.add_argument("--check-cred", action="store_true",
                    help="檢查存起來的密碼長相 (不會印出密碼本身)")
    args = ap.parse_args()

    if args.check_cred:
        return check_cred()
    if args.stats:
        return show_stats()
    if args.test:
        run_sync(args)
        return 0

    rotate_log()
    if not acquire_lock("full" if args.full else "incremental"):
        log("另一個信箱同步還在跑, 這次跳過")
        return 0
    write_status(started_at=now_iso())
    try:
        res = run_sync(args)
        fin = now_iso()
        write_status(finished_at=fin, ok=True, error=None, changed=res["changed"],
                     unread=res["unread"], **({"changed_at": fin} if res["changed"] else {}))
        return 0
    except SystemExit as exc:
        write_status(finished_at=now_iso(), ok=False, error=str(exc.code))
        raise
    except Exception as exc:
        log(f"未預期的錯誤: {exc!r}", "ERROR")
        write_status(finished_at=now_iso(), ok=False, error=repr(exc))
        return 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
