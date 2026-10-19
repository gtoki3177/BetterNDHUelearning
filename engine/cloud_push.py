"""BetterElearning 雲端中繼：同步腳本和 Firestore 之間的搬運工。

    python cloud_push.py pull --dir .                  # 同步前: 從 Firestore 取回上一次的原始檔和 e學苑 session
    python cloud_push.py push --dir . --source actions # 同步後: 組成儀表板文件寫進 Firestore
    python cloud_push.py overlay --dir <_moodle> --config <_moodle>/config.ini
                                                       # 電腦端: 把教材本機路徑、全文索引統計補進 Firestore

Firestore 結構 (都在 users/{uid}/ 底下):
    dash/data      儀表板的 e學苑資料 (格式同 artifact 頁面資料庫的 dashboard/data)
    dash/mail      儀表板的信箱資料 (只留未讀, 格式同 dashboard/mail)
    dash/settings  各同步引擎上次跑的時間和結果 (engines.<source>)
    engine/moodle_raw, engine/mail_raw   moodle_sync.py / mail_sync.py 的原始輸出 (字串), 給下次同步當「上一版」
    engine/session                       e學苑登入 cookie, 讓下次同步不用重新登入
    engine/summaries                     電腦端寫好的信件摘要 {uid: {summary, action, flash}} (選用)

金鑰: 環境變數 FIREBASE_SA (服務帳戶 JSON 內容) 或 FIREBASE_SA_FILE (路徑)。帳號: FIREBASE_UID。
這支程式只印統計數字, 不印信件內容。
"""

import argparse
import configparser
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))

# ---- 信件分類: 跟頁面 JS、daily-update 的 build.py 一致 ----
NOISE_ADDR = ["no-reply@accounts.google.com", "elearn@gms.ndhu.edu.tw"]
NOISE_SUBJ = [r"安全性快訊", r"安全性警示", r"新登入紀錄", r"(?i)newsletter"]
BULK_ADDR = ["announce@gms.ndhu.edu.tw"]
DIGEST = r"批次寄送"
STRONG = ["截止", "逾期", "繳費", "選課", "停課", "補課", "調課",
          "成績", "獎學金", "考試", "註冊", "重要"]
DEFAULT_DEPT_MAP = "CSIE:資訊工程學系, EE:電機工程學系, GC:通識教育中心, XX:通識教育中心, YY:體育中心"
DOC_LIMIT = 900 * 1024      # Firestore 單一文件上限 1 MiB, 留一點空間

RAW = {"moodle_raw": "latest.json", "mail_raw": "mail.json"}
COOKIE_FILE = "moodle_cookies.json"


# --------------------------------------------------------------------------- 轉換 (搬自 daily-update/scripts/build.py)

def tz(s):
    if not s:
        return None
    s = str(s)
    if re.search(r"[+-]\d\d:\d\d$|Z$", s):
        return s
    if len(s) == 16:
        s += ":00"
    return s + "+08:00"


def when(s):
    try:
        return datetime.fromisoformat(tz(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def excerpt(body, n=260):
    body = body or ""
    lines = [l for l in body.splitlines()
             if not re.match(r"^\s*(分類|來源|對象|標題|日期|附檔)[：:]", l)]
    s = "\n".join(lines)
    s = re.sub(r"https?://\S+|www\.\S+", "", s)
    s = re.sub(r"[-=_*]{4,}", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + " …" if len(s) > n else s


def short_name(name):
    m = re.match(r"^全民國防教育軍事訓練課程[-－](.+)$", name or "")
    return f"全民國防－{m.group(1)}" if m else name


def bucket(m, noise_addr):
    if m["addr"] in noise_addr or any(re.search(p, m["subject"]) for p in NOISE_SUBJ):
        return "noise"
    if re.search(DIGEST, m["subject"]):
        return "bulk"
    if m["addr"] in BULK_ADDR:
        return "focus" if len([k for k in m["kw"] if k in STRONG]) >= 2 else "bulk"
    return "focus"


def load_config(p, extra_noise=()):
    cp = configparser.ConfigParser()
    if p and Path(p).exists():
        cp.read(p, encoding="utf-8-sig")
    d = cp["dashboard"] if cp.has_section("dashboard") else {}
    get = lambda k, fb="": (d.get(k, fb) if hasattr(d, "get") else fb) or fb
    dept_map = {}
    for part in get("dept_map", DEFAULT_DEPT_MAP).split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            dept_map[k.strip()] = v.strip()
    noise = [x.strip() for x in get("noise_senders", "").split(",") if x.strip()]
    noise += [x for x in extra_noise if x and x not in noise]
    return {"dept_map": dept_map,
            "dept": {"label": get("dept_label", "系上").strip() or "系上",
                     "pattern": get("dept_pattern", "系辦").strip() or "系辦"},
            "noise": noise}


def build_data(L, prev, cfg):
    prev = prev or {}
    gen = tz(L.get("generated_at"))
    old_fs = {a["url"]: a.get("first_seen") for a in prev.get("assignments", [])}
    old_att = {x["url"]: x["local"] for a in prev.get("assignments", []) for x in (a.get("attachments") or []) if x.get("local")}
    old_local = {i["url"]: i.get("local") for c in prev.get("courses", [])
                 for i in c.get("items", []) if i.get("local")}
    old_dept = {c["code"]: c.get("dept") for c in prev.get("courses", [])}

    assignments = []
    for a in L.get("assignments", []):
        assignments.append({
            "course": a.get("course"), "code": a.get("course_code"), "name": a.get("name"),
            "due": tz(a.get("due")), "open": tz(a.get("open")),
            "first_seen": old_fs.get(a.get("url")) or gen,
            "url": a.get("url"), "submitted": bool(a.get("submitted")),
            "submission": a.get("submission"), "grading": a.get("grading"),
            "description": a.get("description") or "",
            # 附件：雲端只有檔名和網址；電腦端下載過的帶本機路徑 (雲端重組時沿用上一版的)
            "attachments": [{**x, **({"local": x.get("local") or old_att[x["url"]]} if x.get("local") or x.get("url") in old_att else {})}
                            for x in (a.get("attachments") or []) if x.get("url")],
        })

    order = lambda k: 0 if k == "assign" else (2 if k == "forum" else 1)
    courses = []
    for c in L.get("courses", []):
        items = []
        for act in c.get("activities", []):
            it = {"kind": act.get("type"), "name": act.get("name"), "url": act.get("url")}
            loc = act.get("local") or old_local.get(act.get("url"))
            if loc:
                it["local"] = loc
            items.append(it)
        items.sort(key=lambda i: order(i["kind"]))
        code = c.get("code") or ""
        pre = (re.match(r"[A-Z]+", code) or [""])[0]
        courses.append({
            "code": code, "name": short_name(c.get("name")), "id": c.get("id"),
            "dept": cfg["dept_map"].get(pre) or old_dept.get(code) or "",
            "items": items,
        })

    # 雲端不建索引: 有統計 (電腦端) 才用, 否則沿用上一版
    st = L.get("stats", {}) or {}
    if st.get("index"):
        index = {"files": st.get("indexed_files", 0), "chunks": st.get("indexed_chunks", 0),
                 "courses": st.get("index", [])}
    else:
        index = dict(prev.get("index") or {"files": 0, "chunks": 0, "courses": []})

    errors = list(L.get("errors") or [])
    if L.get("last_run_failed"):
        errors.insert(0, f"Moodle 同步在 {L['last_run_failed']} 失敗，資料可能是舊的")

    return {"generated_at": gen, "semester": L.get("semester") or prev.get("semester", ""),
            "root": prev.get("root", ""),
            "assignments": assignments, "courses": courses, "index": index, "errors": errors}


def mail_record(m):
    atts = [a if isinstance(a, str) else (a.get("filename") or a.get("name") or "")
            for a in (m.get("attachments") or [])]
    return {"uid": str(m.get("uid")), "date": m.get("date"), "from": m.get("from_name") or m.get("from_addr"),
            "addr": (m.get("from_addr") or "").lower(), "subject": m.get("subject") or "",
            "kw": m.get("keywords") or [], "att": len(atts), "atts": atts,
            "link": m.get("link"), "excerpt": excerpt(m.get("body"))}


def build_mail(M, prev, cfg, summaries):
    prev = prev or {}
    old = {m["uid"]: m for m in prev.get("messages", [])}
    msgs = []
    for m in M.get("messages", []):
        if not m.get("unread"):
            continue
        r = mail_record(m)
        src = {**old.get(r["uid"], {}), **summaries.get(r["uid"], {})}
        for k in ("summary", "action", "flash"):
            if src.get(k):
                r[k] = src[k]
        msgs.append(r)
    return {"generated_at": tz(M.get("generated_at")), "account": M.get("account"),
            "window_days": M.get("window_days"), "stats": M.get("stats", {}),
            "messages": msgs, "noise": cfg["noise"], "dept": cfg["dept"],
            "errors": list(M.get("errors") or [])}


def build_archive(M, cfg):
    """某個月的信件封存：已讀未讀都留 (read 標記)，給 app 往回翻。"""
    msgs = []
    for m in M.get("messages", []):
        r = mail_record(m)
        if not m.get("unread"):
            r["read"] = True
        msgs.append(r)
    return {"month": M.get("month"), "generated_at": tz(M.get("generated_at")), "account": M.get("account"),
            "messages": msgs, "noise": cfg["noise"]}


def fit_mail(mail, noise_addr):
    size = lambda: len(json.dumps(mail, ensure_ascii=False).encode("utf-8"))
    trimmed = 0
    for level in ("noise", "bulk"):
        if size() <= DOC_LIMIT:
            break
        for r in mail["messages"]:
            if bucket(r, noise_addr) == level and r.get("excerpt"):
                r["excerpt"] = ""
                trimmed += 1
    while size() > DOC_LIMIT and mail["messages"]:
        mail["messages"].pop()
        trimmed += 1
    return trimmed


# --------------------------------------------------------------------------- Firestore

def connect():
    import firebase_admin
    from firebase_admin import credentials, firestore
    raw = os.environ.get("FIREBASE_SA")
    path = os.environ.get("FIREBASE_SA_FILE")
    if raw and raw.strip():
        cred = credentials.Certificate(json.loads(raw))
    elif path:
        cred = credentials.Certificate(path)
    else:
        sys.exit("沒有 Firebase 金鑰：設 FIREBASE_SA (JSON 內容) 或 FIREBASE_SA_FILE (路徑)")
    uid = (os.environ.get("FIREBASE_UID") or "").strip()
    if not uid:
        sys.exit("沒有 FIREBASE_UID")
    firebase_admin.initialize_app(cred)
    return firestore.client().collection("users").document(uid)


def get(ref):
    snap = ref.get()
    return snap.to_dict() if snap.exists else None


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def strip_meta(doc):
    return {k: v for k, v in (doc or {}).items() if k not in ("written_at", "written_by")}


def cmd_pull(user, d):
    for doc_id, fname in RAW.items():
        doc = get(user.collection("engine").document(doc_id))
        if doc and doc.get("json"):
            (d / fname).write_text(doc["json"], encoding="utf-8")
            print(f"pull {doc_id}: {len(doc['json'])} bytes")
        else:
            print(f"pull {doc_id}: 沒有上一版 (第一次跑)")
    sess = get(user.collection("engine").document("session"))
    if sess and sess.get("cookies"):
        (d / COOKIE_FILE).write_text(sess["cookies"], encoding="utf-8")
        print("pull session: ok")


def cmd_overlay(user, d, cfg_path):
    """電腦端 (每次 moodle_sync.py 跑完)：把教材在這台電腦的路徑、全文索引統計、根資料夾補進 dash/data。
    只改 courses 裡教材的 local、root、index 三樣，作業和課程本身維持雲端同步寫的；
    雲端同步之後重組資料時會從上一版沿用這三樣，所以電腦關機也不會被洗掉。"""
    dash = user.collection("dash")
    L = read_json(d / "latest.json")
    if not L:
        print(json.dumps({"overlay": "skip (沒有 latest.json)"}))
        return
    ref = dash.document("data")
    doc = get(ref)
    if not doc:
        print(json.dumps({"overlay": "skip (Firestore 還沒有資料，雲端同步還沒跑過)"}))
        return
    local = {a["url"]: a["local"] for c in L.get("courses", []) for a in c.get("activities", [])
             if a.get("local") and a.get("url")}
    cp = configparser.ConfigParser()
    if cfg_path and Path(cfg_path).exists():
        cp.read(cfg_path, encoding="utf-8-sig")
    root = (cp.get("moodle", "root_folder", fallback="") or "").strip() or str(Path(d).resolve().parent)
    st = L.get("stats") or {}
    index = ({"files": st.get("indexed_files", 0), "chunks": st.get("indexed_chunks", 0), "courses": st.get("index") or []}
             if st.get("index") is not None else doc.get("index"))
    courses = doc.get("courses", [])
    att_local = {x["url"]: x["local"] for a in L.get("assignments", []) for x in (a.get("attachments") or [])
                 if x.get("local") and x.get("url")}
    assigns = doc.get("assignments", [])
    att_changed = 0
    for a in assigns:
        for x in a.get("attachments") or []:
            lp = att_local.get(x.get("url"))
            if lp and x.get("local") != lp:
                x["local"] = lp
                att_changed += 1
    changed = 0
    for c in courses:
        for it in c.get("items", []):
            lp = local.get(it.get("url"))
            if lp and it.get("local") != lp:
                it["local"] = lp
                changed += 1
    patch = {}
    if changed:
        patch["courses"] = courses
    if att_changed:
        patch["assignments"] = assigns
    if doc.get("root") != root:
        patch["root"] = root
    if index is not None and doc.get("index") != index:
        patch["index"] = index
    if patch:
        ref.update(patch)
    status = read_json(d / "status_moodle.json")
    dash.document("settings").set({"engines": {"pc": {"at": now_iso(), **({"moodle": status} if status else {})}}}, merge=True)
    print(json.dumps({"overlay": "updated" if patch else "unchanged", "fields": sorted(patch), "local_paths": changed}, ensure_ascii=False))


def use_config(cfg_path):
    """電腦端用 config.ini 的 [cloud] (service_account 相對於 config.ini、uid) 取代環境變數。"""
    cp = configparser.ConfigParser()
    cp.read(cfg_path, encoding="utf-8-sig")
    if not cp.has_section("cloud"):
        sys.exit(f"{cfg_path} 沒有 [cloud] 段落 (service_account、uid)")
    sa = Path(cp.get("cloud", "service_account"))
    if not sa.is_absolute():
        sa = Path(cfg_path).resolve().parent / sa
    os.environ.setdefault("FIREBASE_SA_FILE", str(sa))
    os.environ.setdefault("FIREBASE_UID", cp.get("cloud", "uid").strip())


def cmd_push(user, d, source, save_state):
    dash, engine = user.collection("dash"), user.collection("engine")
    L, M = read_json(d / "latest.json"), read_json(d / "mail.json")
    settings = get(dash.document("settings")) or {}
    summaries = (get(engine.document("summaries")) or {})
    summaries.pop("written_at", None)
    cfg = load_config(d / "config.ini", settings.get("noise_senders") or [])
    written = {"written_at": now_iso(), "written_by": source}
    out = {}

    if L:
        prev = get(dash.document("data"))
        data = build_data(L, prev, cfg)
        p_at, n_at = when((prev or {}).get("generated_at")), when(data["generated_at"])
        if prev and p_at and n_at and p_at > n_at:
            out["data"] = "skip (Firestore 上的比較新)"
        elif strip_meta(prev) == data:
            out["data"] = "unchanged"
        else:
            dash.document("data").set({**data, **written})
            out["data"] = f"written ({len(data['courses'])} courses, {len(data['assignments'])} assignments)"

    if M:
        prev = get(dash.document("mail"))
        mail = build_mail(M, prev, cfg, summaries)
        trimmed = fit_mail(mail, NOISE_ADDR + cfg["noise"])
        p_at, n_at = when((prev or {}).get("generated_at")), when(mail["generated_at"])
        if prev and p_at and n_at and p_at > n_at:
            out["mail"] = "skip (Firestore 上的比較新)"
        elif strip_meta(prev) == mail:
            out["mail"] = "unchanged"
        else:
            dash.document("mail").set({**mail, **written})
            out["mail"] = f"written ({len(mail['messages'])} unread" + (f", trimmed {trimmed}" if trimmed else "") + ")"

    if save_state:
        for doc_id, fname in RAW.items():
            p = d / fname
            if p.exists():
                engine.document(doc_id).set({"json": p.read_text(encoding="utf-8"), "at": now_iso()})
        c = d / COOKIE_FILE
        if c.exists():
            engine.document("session").set({"cookies": c.read_text(encoding="utf-8"), "at": now_iso()})

    status = {k: read_json(d / f"status_{k}.json") for k in ("moodle", "mail")}
    dash.document("settings").set(
        {"engines": {source: {"at": now_iso(), **{k: v for k, v in status.items() if v}}}}, merge=True)
    print(json.dumps(out, ensure_ascii=False))


def cmd_archive(user, d, month):
    M = read_json(d / "mail_month.json")
    if not M or M.get("month") != month:
        sys.exit(f"沒有 {month} 的 mail_month.json (先跑 python mail_sync.py --month {month})")
    dash = user.collection("dash")
    settings = get(dash.document("settings")) or {}
    cfg = load_config(d / "config.ini", settings.get("noise_senders") or [])
    arch = build_archive(M, cfg)
    trimmed = fit_mail(arch, NOISE_ADDR + cfg["noise"])
    dash.document(f"mail_{month}").set({**arch, "written_at": now_iso()})
    print(json.dumps({f"mail_{month}": f"written ({len(arch['messages'])} 封" + (f", trimmed {trimmed}" if trimmed else "") + ")"},
                     ensure_ascii=False))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pull", "push", "overlay", "archive"])
    ap.add_argument("--month", help="archive：哪個月 (YYYY-MM)，讀 mail_month.json 寫進 dash/mail_YYYY-MM")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--source", default="actions", help="actions / pc：寫進 settings.engines 的名字")
    ap.add_argument("--no-state", action="store_true", help="push 時不寫回原始檔和 session (電腦端用)")
    ap.add_argument("--config", help="電腦端: config.ini (讀 [cloud] 的金鑰和 uid、[moodle] 的 root_folder)")
    a = ap.parse_args()
    if a.config:
        use_config(a.config)
    d = Path(a.dir)
    if a.cmd == "overlay":
        # 電腦的排程用 pythonw 跑，沒有視窗：出錯寫進 cloud_push_log.txt
        try:
            cmd_overlay(connect(), d, a.config)
        except BaseException as e:  # noqa: BLE001
            with open(d / "cloud_push_log.txt", "a", encoding="utf-8") as f:
                f.write(f"{now_iso()} overlay 失敗: {e!r}\n")
            raise
        return
    user = connect()
    d = Path(a.dir)
    if a.cmd == "pull":
        cmd_pull(user, d)
    elif a.cmd == "archive":
        cmd_archive(user, d, a.month)
    else:
        cmd_push(user, d, a.source, not a.no_state)


if __name__ == "__main__":
    main()
