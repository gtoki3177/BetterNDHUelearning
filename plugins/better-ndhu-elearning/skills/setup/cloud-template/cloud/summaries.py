"""信件摘要：在電腦上由 Claude 的排程呼叫，跟同步腳本無關，只讀寫 Firestore。

    python summaries.py need  --config <_moodle>/config.ini [--max 15]   # 印出還沒有摘要的「需注意」信 (JSON，含內文)
    python summaries.py write --config <_moodle>/config.ini summ.json   # 把摘要寫回 Firestore

summ.json 格式：{"<uid>": {"summary": "…", "action": "…", "flash": ["…", "…"]}}

寫到兩個地方：
    engine/summaries   uid → 摘要 (雲端同步每次重組信件時會合併進去，所以不會被蓋掉)
    dash/mail          直接補進對應的信，app 馬上看得到，不用等下一次雲端同步

設定讀 config.ini 的 [cloud]：service_account (金鑰檔，相對於 config.ini 的位置)、uid。
這支程式只印信件主旨 / 內文給呼叫它的 Claude 看，不會寫任何檔案到別處。
"""
import argparse
import configparser
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloud_push import NOISE_ADDR, bucket  # noqa: E402  (分類規則跟雲端同步同一份)

TZ = timezone(timedelta(hours=8))
KEEP_DAYS = 60          # engine/summaries 裡超過這麼久的摘要清掉 (信早就不在 14 天的範圍了)


def connect(cfg_path):
    import firebase_admin
    from firebase_admin import credentials, firestore
    cp = configparser.ConfigParser()
    cp.read(cfg_path, encoding="utf-8-sig")
    if not cp.has_section("cloud"):
        sys.exit(f"{cfg_path} 沒有 [cloud] 段落 (service_account、uid)")
    sa = Path(cp.get("cloud", "service_account"))
    if not sa.is_absolute():
        sa = Path(cfg_path).resolve().parent / sa
    uid = cp.get("cloud", "uid").strip()
    firebase_admin.initialize_app(credentials.Certificate(str(sa)))
    db = firestore.client()
    return db, db.collection("users").document(uid)


def get(ref):
    snap = ref.get()
    return snap.to_dict() if snap.exists else None


def cmd_need(user, limit):
    mail = get(user.collection("dash").document("mail")) or {}
    raw = get(user.collection("engine").document("mail_raw")) or {}
    done = get(user.collection("engine").document("summaries")) or {}
    bodies = {}
    if raw.get("json"):
        for m in json.loads(raw["json"]).get("messages", []):
            bodies[str(m.get("uid"))] = m.get("body") or ""
    noise = NOISE_ADDR + [x.lower() for x in (mail.get("noise") or [])]
    seen, out = set(), []
    for m in sorted(mail.get("messages", []), key=lambda r: r.get("date") or "", reverse=True):
        key = (m.get("addr") or "") + "|" + (m.get("subject") or "")
        if key in seen:
            continue
        seen.add(key)
        if m.get("summary") or m["uid"] in done or bucket(m, noise) != "focus":
            continue
        out.append({"uid": m["uid"], "date": m.get("date"), "from": m.get("from"), "addr": m.get("addr"),
                    "subject": m.get("subject"), "attachments": m.get("atts") or [], "keywords": m.get("kw") or [],
                    "body": (bodies.get(m["uid"]) or m.get("excerpt") or "")[:1500]})
        if len(out) >= limit:
            break
    print(json.dumps({"today": datetime.now(TZ).strftime("%Y/%m/%d"), "count": len(out), "mails": out}, ensure_ascii=False, indent=1))


def clean(r):
    if not isinstance(r, dict) or not isinstance(r.get("summary"), str) or not r["summary"].strip():
        return None
    o = {"summary": r["summary"].strip()[:400]}
    if isinstance(r.get("action"), str) and r["action"].strip():
        o["action"] = r["action"].strip()[:120]
    fl = [x.strip()[:16] for x in (r.get("flash") or []) if isinstance(x, str) and x.strip()][:3]
    if fl:
        o["flash"] = fl
    return o


def cmd_write(db, user, path):
    from firebase_admin import firestore
    src = json.loads(Path(path).read_text(encoding="utf-8"))
    now = datetime.now(TZ).isoformat(timespec="seconds")
    summ = {}
    for uid, r in src.items():
        c = clean(r)
        if c:
            summ[str(uid)] = {**c, "at": now}
    if not summ:
        print(json.dumps({"written": 0}))
        return
    # 1. engine/summaries：合併進去，順便清掉太舊的
    eng = user.collection("engine").document("summaries")
    old = get(eng) or {}
    cutoff = datetime.now(TZ) - timedelta(days=KEEP_DAYS)
    keep = {k: v for k, v in old.items() if isinstance(v, dict) and v.get("at") and datetime.fromisoformat(v["at"]) > cutoff}
    keep.update(summ)
    eng.set(keep)
    # 2. dash/mail：直接補進對應的信 (交易：跟雲端同步同時寫也不會互相蓋掉)
    ref = user.collection("dash").document("mail")
    tx = db.transaction()

    @firestore.transactional
    def patch(t):
        snap = ref.get(transaction=t)
        if not snap.exists:
            return 0
        d = snap.to_dict()
        n = 0
        for m in d.get("messages", []):
            s = summ.get(m.get("uid"))
            if s and not m.get("summary"):
                for k in ("summary", "action", "flash"):
                    if s.get(k):
                        m[k] = s[k]
                n += 1
        if n:
            t.update(ref, {"messages": d["messages"], "written_at": now, "written_by": "summaries"})
        return n

    n = patch(tx)
    print(json.dumps({"written": len(summ), "patched_into_mail": n}, ensure_ascii=False))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["need", "write"])
    ap.add_argument("file", nargs="?", help="write: summ.json")
    ap.add_argument("--config", required=True, help="_moodle/config.ini")
    ap.add_argument("--max", type=int, default=15)
    a = ap.parse_args()
    db, user = connect(a.config)
    if a.cmd == "need":
        cmd_need(user, a.max)
    else:
        if not a.file:
            sys.exit("write 要給 summ.json")
        cmd_write(db, user, a.file)


if __name__ == "__main__":
    main()
