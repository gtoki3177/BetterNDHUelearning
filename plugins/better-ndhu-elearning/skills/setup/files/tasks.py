"""BetterElearning 任務欄：讓 AI agent (例如 Claude Cowork) 讀和新增「備忘」任務。

app 裡任務欄的備忘存在 Firestore users/{uid}/dash/pins 的 custom 欄位 (每筆 {t: 修改時間, v: 內容})，
這支程式用電腦上同步用的金鑰 (config.ini 的 [cloud]) 直接寫進去，app 打開就會看到。

  python tasks.py list --config config.ini                 # 列出現有的作業和備忘 (JSON)，新增前先看有沒有重複
  python tasks.py mail --config config.ini [--months 2]    # 讀信件 (雲端同步好的：主旨、寄件人、日期、摘要)，找截止日用；
                                                            #   --months N 連最近 N 個月的封存一起讀 (有載入過的才有)
  python tasks.py add  --config config.ini --file new.json # 新增 (new.json 是陣列，格式見下)
  python tasks.py add  --config config.ini --json '[...]'

每一筆：
  {"title": "期中考", "due": "2026-11-05 10:10", "tags": ["作業系統"], "note": "範圍第 1–6 章（來源：10/18 老師的信）", "source": "mail"}
  source：從哪裡整理來的 —— mail (信件) / lecture (講義) / other；app 的卡片上會標出來
  due 可以省略 (沒有期限)；只寫日期就是當天 23:59；時間是台灣時間。

連不到資料庫 (例如在沒有網路的環境) 時，會先存進同一層的 tasks_inbox.json，
電腦的排程下次跑 cloud_push.py overlay 時會自動補送。
"""
import argparse
import json
import random
import string
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
INBOX = HERE / "tasks_inbox.json"
TZ = timezone(timedelta(hours=8))


def iso_local(d):
    return d.astimezone(TZ).isoformat(timespec="seconds")


def parse_due(s):
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return iso_local(datetime.strptime(s, fmt).replace(tzinfo=TZ))
        except ValueError:
            pass
    try:
        return iso_local(datetime.strptime(s, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=TZ))
    except ValueError:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return iso_local(d if d.tzinfo else d.replace(tzinfo=TZ))
    except ValueError:
        raise SystemExit(f"看不懂的日期：{s!r} (用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM)")


def new_id():
    return "m" + format(int(time.time() * 1000), "x") + "".join(random.choices(string.ascii_lowercase + string.digits, k=3))


def memo_of(item):
    title = (item.get("title") or "").strip()
    if not title:
        raise SystemExit(f"少了 title：{item!r}")
    tags = [str(t).strip().lstrip("#") for t in (item.get("tags") or []) if str(t).strip()]
    m = {"id": new_id(), "title": title[:80], "tags": tags[:6], "due": parse_due(item.get("due")),
         "created": iso_local(datetime.now(TZ)), "done": False, "by": "agent"}
    if item.get("note"):
        m["note"] = str(item["note"])[:1000]
    src = str(item.get("source") or "other").lower()
    m["src"] = src if src in ("mail", "lecture") else "other"
    return m


def stamp():
    # 跟 app 一樣：UTC、毫秒 (app 比時間決定誰比較新)
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(cfg):
    sys.path.insert(0, str(HERE))
    import cloud_push
    if cfg:
        cloud_push.use_config(cfg)
    return cloud_push.connect()


def write_memos(user, memos):
    t = stamp()
    user.collection("dash").document("pins").set({"v": 2, "custom": {m["id"]: {"t": t, "v": m} for m in memos}}, merge=True)


def cmd_list(user):
    dash = user.collection("dash")
    pins = (dash.document("pins").get().to_dict() or {})
    data = (dash.document("data").get().to_dict() or {})
    memos = [e["v"] for e in (pins.get("custom") or {}).values() if isinstance(e, dict) and e.get("v")]
    out = {
        "assignments": [{"course": a.get("course"), "name": a.get("name"), "due": a.get("due"), "submitted": bool(a.get("submitted"))}
                        for a in data.get("assignments", [])],
        "memos": [{"title": m.get("title"), "due": m.get("due"), "tags": m.get("tags") or [], "done": bool(m.get("done")),
                   "note": m.get("note", "")} for m in memos],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_mail(user, months):
    """信件：最近 14 天 (雲端同步寫的 dash/mail) + 最近幾個月的封存 (dash/mail_YYYY-MM，app 載入過才有)。不用開瀏覽器、不用連信箱。"""
    dash = user.collection("dash")
    mail = dash.document("mail").get().to_dict() or {}
    msgs = {str(m.get("uid")): m for m in mail.get("messages", [])}
    now = datetime.now(TZ)
    for k in range(max(0, months)):
        y, mo = now.year, now.month - k
        while mo < 1:
            y, mo = y - 1, mo + 12
        snap = dash.document(f"mail_{y}-{mo:02d}").get()
        for m in ((snap.to_dict() or {}).get("messages", []) if snap.exists else []):
            msgs.setdefault(str(m.get("uid")), m)
    out = sorted(({"date": m.get("date"), "from": m.get("from"), "subject": m.get("subject"), "read": bool(m.get("read")), "keywords": m.get("kw") or [],
                   "excerpt": m.get("excerpt", ""), "summary": m.get("summary", ""), "attachments": m.get("atts") or [], "link": m.get("link"),
                   "links": m.get("links") or []}
                  for m in msgs.values()), key=lambda m: m["date"] or "", reverse=True)
    print(json.dumps({"window_days": mail.get("window_days"), "generated_at": mail.get("generated_at"), "count": len(out), "messages": out},
                     ensure_ascii=False, indent=2))


def flush_inbox(user):
    """把之前連不上時存下來的備忘送上去 (cloud_push.py overlay 會叫)。回傳送了幾筆。"""
    if not INBOX.exists():
        return 0
    memos = json.loads(INBOX.read_text(encoding="utf-8") or "[]")
    if memos:
        write_memos(user, memos)
    INBOX.unlink()
    return len(memos)


def cmd_add(cfg, items):
    memos = [memo_of(x) for x in items]
    if not memos:
        raise SystemExit("沒有要新增的東西")
    try:
        user = connect(cfg)
        flush_inbox(user)
        write_memos(user, memos)
        print(json.dumps({"added": [{"title": m["title"], "due": m["due"]} for m in memos]}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001  連不上就先存著，電腦排程會補送
        old = json.loads(INBOX.read_text(encoding="utf-8")) if INBOX.exists() else []
        INBOX.write_text(json.dumps(old + memos, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"queued": len(memos), "reason": repr(e)[:200],
                          "note": "連不到資料庫，先存在 tasks_inbox.json，電腦的排程下次同步時會補送"}, ensure_ascii=False))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="讀 / 新增 BetterElearning 任務欄的備忘")
    ap.add_argument("cmd", choices=["list", "add", "mail"])
    ap.add_argument("--months", type=int, default=0, help="mail：連最近幾個月的封存一起讀")
    ap.add_argument("--config", default=str(HERE / "config.ini"), help="config.ini (讀 [cloud] 的金鑰和 uid)")
    ap.add_argument("--file", help="add：JSON 陣列檔")
    ap.add_argument("--json", help="add：直接給 JSON 陣列字串")
    a = ap.parse_args()
    cfg = a.config if Path(a.config).exists() else None
    if a.cmd == "list":
        cmd_list(connect(cfg))
        return
    if a.cmd == "mail":
        cmd_mail(connect(cfg), a.months)
        return
    raw = Path(a.file).read_text(encoding="utf-8-sig") if a.file else (a.json or sys.stdin.read())
    items = json.loads(raw)
    cmd_add(cfg, items if isinstance(items, list) else [items])


if __name__ == "__main__":
    main()
