#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BetterElearning dashboard builder.

Takes the two JSON files written by the local sync scripts (latest.json from
moodle_sync.py, mail.json from mail_sync.py), plus config.ini, and produces the
dashboard HTML by replacing the `const DATA = {...}` and `const MAIL = {...}`
blocks. Nothing else in the page is touched.

Typical daily run (two passes):

    python build.py --page current.html --latest latest.json --mail mail.json \
        --config config.ini --out new.html --report report.json
    # -> read report.json; for each entry in need_summary write a summary into summ.json
    python build.py ... --summaries summ.json --out new.html --report report.json

First run (no dashboard yet): pass --template assets/dashboard.html instead of --page.
To move an existing dashboard onto a newer template while keeping its data:
pass both --template (new code) and --page (old page, for carried-over fields).

Carried over from the previous page (--page):
  - assignment first_seen (by url)
  - course item local paths (by url) when latest.json has none
  - mail summary / action / flash (by uid)
  - the whole DATA or MAIL block when latest.json / mail.json is missing
"""

import argparse
import configparser
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))

# ---- mail classification: must match the page's JS (bucket / groupOf) ----
NOISE_ADDR = ["no-reply@accounts.google.com", "elearn@gms.ndhu.edu.tw"]
NOISE_SUBJ = [r"安全性快訊", r"安全性警示", r"新登入紀錄", r"(?i)newsletter"]
BULK_ADDR = ["announce@gms.ndhu.edu.tw"]
DIGEST = r"批次寄送"
STRONG = ["截止", "逾期", "繳費", "選課", "停課", "補課", "調課",
          "成績", "獎學金", "考試", "註冊", "重要"]

DEFAULT_DEPT_MAP = "CSIE:資訊工程學系, EE:電機工程學系, GC:通識教育中心, XX:通識教育中心, YY:體育中心"


# --------------------------------------------------------------------------- helpers

def read_json(p):
    if not p:
        return None
    p = Path(p)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8-sig"))


def block(html, name):
    """Return (start, end, obj) of `const NAME = {...};` in the page."""
    key = f"const {name} = "
    i = html.index(key) + len(key)
    # JSON object: walk braces, respecting strings
    depth, j, in_str, esc = 0, i, False, False
    while True:
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
        j += 1
    return i, j, json.loads(html[i:j])


def put(html, name, obj):
    i, j, _ = block(html, name)
    body = json.dumps(obj, ensure_ascii=False, indent=2).replace("\n", "\n  ")
    body = body.replace("</", "<\\/")          # never close the <script> early
    return html[:i] + body + html[j:]


def tz(s):
    """'2026-09-29T23:59' -> '2026-09-29T23:59:00+08:00'; None stays None."""
    if not s:
        return None
    s = str(s)
    if re.search(r"[+-]\d\d:\d\d$|Z$", s):
        return s
    if len(s) == 16:
        s += ":00"
    return s + "+08:00"


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
    # 全民國防教育軍事訓練課程-國際情勢 -> 全民國防－國際情勢
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


def load_config(p):
    cp = configparser.ConfigParser()
    if p and Path(p).exists():
        cp.read(p, encoding="utf-8-sig")
    d = cp["dashboard"] if cp.has_section("dashboard") else {}
    m = cp["moodle"] if cp.has_section("moodle") else {}
    get = lambda sec, k, fb="": (sec.get(k, fb) if hasattr(sec, "get") else fb) or fb
    dept_map = {}
    for part in get(d, "dept_map", DEFAULT_DEPT_MAP).split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            dept_map[k.strip()] = v.strip()
    root = get(m, "root_folder", "").strip()
    if not root and p:
        root = str(Path(p).resolve().parent.parent)   # _moodle 的上一層
    return {
        "root": root,
        "dept_map": dept_map,
        "dept": {"label": get(d, "dept_label", "系上").strip() or "系上",
                 "pattern": get(d, "dept_pattern", "系辦").strip() or "系辦"},
        "noise": [x.strip() for x in get(d, "noise_senders", "").split(",") if x.strip()],
    }


# --------------------------------------------------------------------------- build

def build_data(L, prev, cfg):
    prev = prev or {}
    gen = tz(L.get("generated_at"))
    old_fs = {a["url"]: a.get("first_seen") for a in prev.get("assignments", [])}
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

    st = L.get("stats", {}) or {}
    if st.get("index") is not None:
        index = {"files": st.get("indexed_files", 0), "chunks": st.get("indexed_chunks", 0),
                 "courses": st.get("index", [])}
    else:  # 舊版 moodle_sync.py 沒有每科統計: 沿用上一版, 只更新總檔數
        index = dict(prev.get("index") or {"files": 0, "chunks": 0, "courses": []})
        index["files"] = st.get("indexed_files", index.get("files", 0))

    errors = list(L.get("errors") or [])
    if L.get("last_run_failed"):
        errors.insert(0, f"Moodle 同步在 {L['last_run_failed']} 失敗，資料可能是舊的（看 _moodle\\log.txt）")

    return {"generated_at": gen, "semester": L.get("semester") or prev.get("semester", ""),
            "root": cfg["root"] or prev.get("root", ""),
            "assignments": assignments, "courses": courses, "index": index, "errors": errors}


def build_mail(M, prev, cfg, summaries):
    prev = prev or {}
    old = {m["uid"]: m for m in prev.get("messages", [])}
    msgs = []
    for m in M.get("messages", []):
        if not m.get("unread"):
            continue
        atts = [a if isinstance(a, str) else (a.get("filename") or a.get("name") or "")
                for a in (m.get("attachments") or [])]
        r = {"uid": str(m.get("uid")), "date": m.get("date"), "from": m.get("from_name") or m.get("from_addr"),
             "addr": (m.get("from_addr") or "").lower(), "subject": m.get("subject") or "",
             "kw": m.get("keywords") or [], "att": len(atts), "atts": atts,
             "link": m.get("link"), "excerpt": excerpt(m.get("body"))}
        src = {**old.get(r["uid"], {}), **summaries.get(r["uid"], {})}
        for k in ("summary", "action", "flash"):
            if src.get(k):
                r[k] = src[k]
        r["_new"] = bool(m.get("new"))
        r["_body"] = m.get("body") or ""
        msgs.append(r)
    return {"generated_at": M.get("generated_at"), "account": M.get("account"),
            "window_days": M.get("window_days"), "stats": M.get("stats", {}),
            "messages": msgs, "noise": cfg["noise"], "dept": cfg["dept"],
            "errors": list(M.get("errors") or [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", help="current dashboard HTML (from Artifact read)")
    ap.add_argument("--template", help="blank dashboard template (first run / upgrade)")
    ap.add_argument("--latest", help="latest.json")
    ap.add_argument("--mail", help="mail.json")
    ap.add_argument("--config", help="config.ini")
    ap.add_argument("--summaries", help="JSON {uid: {summary, action, flash}}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    a = ap.parse_args()

    if not (a.page or a.template):
        sys.exit("need --page or --template")
    shell = Path(a.template or a.page).read_text(encoding="utf-8")
    prev_html = Path(a.page).read_text(encoding="utf-8") if a.page else shell
    _, _, PD = block(prev_html, "DATA")
    _, _, PM = block(prev_html, "MAIL")

    cfg = load_config(a.config)
    L = read_json(a.latest)
    M = read_json(a.mail)
    summ = read_json(a.summaries) or {}

    data = build_data(L, PD, cfg) if L else PD
    mail = build_mail(M, PM, cfg, summ) if M else PM
    now = datetime.now(TZ)

    # ---- report ----
    report = {"latest_found": L is not None, "mail_found": M is not None}
    if L:
        prev_urls = {x["url"] for x in PD.get("assignments", [])}
        pend = []
        for x in data["assignments"]:
            if x["due"]:
                d = datetime.fromisoformat(x["due"])
                pend.append({**{k: x[k] for k in ("course", "name", "url", "submitted", "due")},
                             "days_left": round((d - now).total_seconds() / 86400, 1)})
        pend.sort(key=lambda r: r["due"])
        report.update({
            "new_assignments": [x["course"] + "：" + x["name"] for x in data["assignments"] if x["url"] not in prev_urls],
            "upcoming": [p for p in pend if p["days_left"] >= 0],
            "due_soon_unsubmitted": [p for p in pend if 0 <= p["days_left"] <= 3 and not p["submitted"]],
            "new_files": L.get("new_files", []),
            "moodle_errors": data["errors"],
            "moodle_generated_at": data["generated_at"],
        })
    if M:
        noise_addr = NOISE_ADDR + cfg["noise"]
        seen, need, new_focus = set(), [], []
        for r in sorted(mail["messages"], key=lambda r: r["date"] or "", reverse=True):
            key = r["addr"] + "|" + r["subject"]
            if key in seen:
                continue
            seen.add(key)
            if bucket(r, noise_addr) != "focus":
                continue
            if not r.get("summary"):
                need.append({k: r[k] for k in ("uid", "date", "from", "addr", "subject", "kw", "atts")}
                            | {"body": r["_body"][:1500]})
            if r["_new"]:
                new_focus.append({k: r.get(k) for k in ("uid", "from", "subject", "action")})
        report.update({"need_summary": need, "new_focus_mail": new_focus,
                       "mail_generated_at": mail.get("generated_at"), "mail_errors": mail.get("errors", [])})
        for r in mail["messages"]:
            r.pop("_new", None); r.pop("_body", None)

    out = put(put(shell, "DATA", data), "MAIL", mail)
    Path(a.out).write_text(out, encoding="utf-8")
    Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
