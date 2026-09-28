#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BetterElearning 本機 MCP server —— 讓儀表板在 Claude 桌面版裡直接讀同步腳本的輸出。

三個工具:
- get_dashboard_data  讀同一個資料夾裡的 latest.json (moodle_sync.py 產生) 和 mail.json
                      (mail_sync.py 產生), 整理成儀表板要的形狀回傳, 附上兩支同步腳本的狀態。
- get_mail_bodies     指定幾封未讀信, 回傳內文前 1500 字 (給頁面寫摘要用)。
- run_sync            「立即同步」: 在背景啟動同步腳本, 馬上回傳, 不等它跑完。

- get_dashboard_data / get_mail_bodies 只讀檔 (加 config.ini 拿科系對照), 不寫任何東西, 不連網路。
- run_sync 只會啟動同一個資料夾裡的 moodle_sync.py / mail_sync.py, 不接受任意指令。
- 不碰 cred.dat / mail_cred.dat。
- 信件內文不整封送出: 只回未讀信, 內文只留開頭 260 字 (跟儀表板上顯示的一樣)。
- 平常閒著不吃 CPU; 由 Claude 桌面版啟動, 桌面版關掉它就跟著結束。

只用 Python 標準函式庫, 不用裝任何套件。
通訊協定: MCP over stdio (一行一個 JSON-RPC 訊息)。
"""

import configparser
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LATEST = HERE / "latest.json"
MAIL = HERE / "mail.json"
CONFIG = HERE / "config.ini"

SERVER_INFO = {"name": "betterel", "version": "1.1.0"}
LOCK_STALE_MIN = {"moodle": 70, "mail": 15}   # 要跟兩支同步腳本裡的 LOCK_STALE_MIN 一致
DEFAULT_DEPT_MAP = "CSIE:資訊工程學系, EE:電機工程學系, GC:通識教育中心, XX:通識教育中心, YY:體育中心"

TOOLS = [{
    "name": "get_dashboard_data",
    "title": "讀取 BetterElearning 同步結果",
    "description": "讀本機 _moodle 資料夾裡 moodle_sync.py 的 latest.json 和 mail_sync.py 的 mail.json，"
                   "回傳整理好的課程、作業和未讀信件（信件只含開頭摘錄），以及兩支同步腳本的狀態（sync）。唯讀。",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}, {
    "name": "get_mail_bodies",
    "title": "讀幾封未讀信的內文",
    "description": "給 uid 清單（最多 10 封），回傳 mail.json 裡這些未讀信的純文字內文前 1500 字，"
                   "格式 {\"bodies\": {uid: 內文}}。已讀或找不到的信不回傳。唯讀。",
    "inputSchema": {"type": "object", "properties": {
        "uids": {"type": "array", "items": {"type": "string"}, "maxItems": 10}},
        "required": ["uids"], "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}, {
    "name": "run_sync",
    "title": "立即同步",
    "description": "在這台電腦背景啟動同步腳本後馬上回傳（不等跑完）：e學苑輕量同步（作業、繳交狀態，十幾秒）"
                   "和信箱增量同步（幾秒）。heavy=true 時 e學苑改跑完整同步（含下載教材、建索引，比較久）。"
                   "已經在跑的會略過。進度看 get_dashboard_data 的 sync 欄位。",
    "inputSchema": {"type": "object", "properties": {
        "target": {"type": "string", "enum": ["all", "moodle", "mail"], "default": "all"},
        "heavy": {"type": "boolean", "default": False}},
        "additionalProperties": False},
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
}]


# --------------------------------------------------------------------------- 資料整理
# (跟 daily-update 技能的 build.py 同一套規則; 需要沿用上一版的欄位
#  —— first_seen、教材本機路徑、信件摘要 —— 由頁面自己合併)

def read_json(p):
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8-sig"))


def tz(s):
    if not s:
        return None
    s = str(s)
    if re.search(r"[+-]\d\d:\d\d$|Z$", s):
        return s
    if len(s) == 16:
        s += ":00"
    return s + "+08:00"


def excerpt(body, n=260):
    lines = [l for l in (body or "").splitlines()
             if not re.match(r"^\s*(分類|來源|對象|標題|日期|附檔)[：:]", l)]
    s = "\n".join(lines)
    s = re.sub(r"https?://\S+|www\.\S+", "", s)
    s = re.sub(r"[-=_*]{4,}", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + " …" if len(s) > n else s


def short_name(name):
    m = re.match(r"^全民國防教育軍事訓練課程[-－](.+)$", name or "")
    return f"全民國防－{m.group(1)}" if m else name


def load_config():
    cp = configparser.ConfigParser()
    if CONFIG.exists():
        cp.read(CONFIG, encoding="utf-8-sig")
    d = cp["dashboard"] if cp.has_section("dashboard") else {}
    m = cp["moodle"] if cp.has_section("moodle") else {}
    dept_map = {}
    for part in ((d.get("dept_map") if d else None) or DEFAULT_DEPT_MAP).split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            dept_map[k.strip()] = v.strip()
    root = ((m.get("root_folder") if m else None) or "").strip() or str(HERE.parent)
    return {"root": root, "dept_map": dept_map}


def build_data(L, cfg):
    assignments = [{
        "course": a.get("course"), "code": a.get("course_code"), "name": a.get("name"),
        "due": tz(a.get("due")), "open": tz(a.get("open")), "url": a.get("url"),
        "submitted": bool(a.get("submitted")), "submission": a.get("submission"),
        "grading": a.get("grading"), "description": a.get("description") or "",
    } for a in L.get("assignments", [])]

    order = lambda k: 0 if k == "assign" else (2 if k == "forum" else 1)
    courses = []
    for c in L.get("courses", []):
        items = []
        for act in c.get("activities", []):
            it = {"kind": act.get("type"), "name": act.get("name"), "url": act.get("url")}
            if act.get("local"):
                it["local"] = act["local"]
            items.append(it)
        items.sort(key=lambda i: order(i["kind"]))
        code = c.get("code") or ""
        pre = (re.match(r"[A-Z]+", code) or [""])[0]
        courses.append({"code": code, "name": short_name(c.get("name")), "id": c.get("id"),
                        "dept": cfg["dept_map"].get(pre, ""), "items": items})

    st = L.get("stats", {}) or {}
    index = ({"files": st.get("indexed_files", 0), "chunks": st.get("indexed_chunks", 0),
              "courses": st.get("index", [])} if st.get("index") is not None
             else {"files": st.get("indexed_files"), "chunks": None, "courses": None})

    errors = list(L.get("errors") or [])
    if L.get("last_run_failed"):
        errors.insert(0, f"Moodle 同步在 {L['last_run_failed']} 失敗，資料可能是舊的（看 _moodle\\log.txt）")

    return {"generated_at": tz(L.get("generated_at")), "semester": L.get("semester") or "",
            "root": cfg["root"], "assignments": assignments, "courses": courses,
            "index": index, "errors": errors, "new_files": L.get("new_files", [])}


def build_mail(M):
    msgs = []
    for m in M.get("messages", []):
        if not m.get("unread"):
            continue
        atts = [a if isinstance(a, str) else (a.get("filename") or a.get("name") or "")
                for a in (m.get("attachments") or [])]
        msgs.append({"uid": str(m.get("uid")), "date": m.get("date"),
                     "from": m.get("from_name") or m.get("from_addr"),
                     "addr": (m.get("from_addr") or "").lower(), "subject": m.get("subject") or "",
                     "kw": m.get("keywords") or [], "att": len(atts), "atts": atts,
                     "link": m.get("link"), "excerpt": excerpt(m.get("body")), "new": bool(m.get("new"))})
    return {"generated_at": M.get("generated_at"), "account": M.get("account"),
            "window_days": M.get("window_days"), "stats": M.get("stats", {}),
            "messages": msgs, "errors": list(M.get("errors") or [])}


def mtime(p):
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        return None


def parse_ts(v):
    try:
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.astimezone()
    except (TypeError, ValueError):
        return None


def sync_state():
    """兩支同步腳本的狀態: 上次跑完的結果 (status_*.json) + 現在有沒有在跑 (*.lock)。"""
    out = {}
    for name in ("moodle", "mail"):
        try:
            st = read_json(HERE / f"status_{name}.json") or {}
        except Exception:
            st = {}
        running, since, mode = False, None, None
        lp = HERE / f"{name}.lock"
        try:
            if lp.exists() and time.time() - lp.stat().st_mtime < LOCK_STALE_MIN[name] * 60:
                running = True
                try:
                    lk = json.loads(lp.read_text(encoding="utf-8") or "{}")
                    since, mode = lk.get("started_at"), lk.get("mode")
                except Exception:
                    pass
        except OSError:
            pass
        out[name] = {"running": running, "running_since": since, "running_mode": mode,
                     **{k: st.get(k) for k in ("mode", "started_at", "finished_at", "ok", "error",
                                               "changed", "changed_at", "last_heavy")}}
    return out


def get_mail_bodies(uids):
    want = [str(u) for u in (uids or [])][:10]
    M = read_json(MAIL) or {}
    have = {str(m.get("uid")): m for m in M.get("messages", []) if m.get("unread")}
    return {"bodies": {u: (have[u].get("body") or "")[:1500] for u in want if u in have}}


def background_python():
    """背景跑腳本用 pythonw.exe (不會閃黑視窗); 找不到就用現在這個 python。"""
    exe = Path(sys.executable)
    if os.name == "nt":
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            return str(quiet)
    return str(exe)


def spawn(args):
    kw = dict(cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW: 桌面版關掉也不會把它一起帶走
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kw["start_new_session"] = True
    subprocess.Popen([background_python(), *args], **kw)


_SPAWNED = {}   # 這個 server 剛啟動過的腳本: 腳本自己建好鎖之前, 別再按一次就又啟動一個


def run_sync(target="all", heavy=False):
    target = target if target in ("all", "moodle", "mail") else "all"
    state = sync_state()
    jobs = []
    if target in ("all", "moodle"):
        jobs.append(("moodle", [str(HERE / "moodle_sync.py"), "--force" if heavy else "--light"]))
    if target in ("all", "mail") and (HERE / "mail_sync.py").exists() and (HERE / "mail_cred.dat").exists():
        jobs.append(("mail", [str(HERE / "mail_sync.py")]))
    started, skipped = [], []
    now = datetime.now().astimezone()
    for name, args in jobs:
        st = state[name]
        if st["running"] or time.time() - _SPAWNED.get(name, 0) < 20:
            skipped.append({"target": name, "reason": "已經在跑了"})
            continue
        fin = parse_ts(st.get("finished_at"))
        if fin and not heavy and (now - fin).total_seconds() < 30:
            skipped.append({"target": name, "reason": "30 秒內剛同步完"})
            continue
        try:
            spawn(args)
            _SPAWNED[name] = time.time()
            started.append(name)
        except Exception as e:
            skipped.append({"target": name, "reason": f"啟動失敗：{type(e).__name__}: {e}"})
    return {"started": started, "skipped": skipped, "requested_at": now.isoformat(timespec="seconds")}


def get_dashboard_data():
    cfg = load_config()
    out = {"read_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "data": None, "mail": None, "problems": [], "sync": sync_state()}
    for key, path, fn in (("data", LATEST, lambda x: build_data(x, cfg)), ("mail", MAIL, build_mail)):
        try:
            raw = read_json(path)
            if raw is None:
                out["problems"].append(f"{path.name} 不存在")
            else:
                out[key] = fn(raw)
                out[key]["file_mtime"] = mtime(path)
        except Exception as e:  # 檔案寫到一半之類的: 回報就好, 頁面會保留上一份
            out["problems"].append(f"{path.name} 讀不了：{type(e).__name__}: {e}")
    return out


# --------------------------------------------------------------------------- MCP (JSON-RPC over stdio)

def send(msg):
    sys.stdout.buffer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def handle(req):
    method, rid = req.get("method"), req.get("id")
    if rid is None:                       # notification (initialized, cancelled …): 不用回
        return
    if method == "initialize":
        pv = (req.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": pv, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "BetterElearning 儀表板的本機資料來源。get_dashboard_data 回傳 e學苑課程/作業、gms 未讀信件摘錄和同步狀態；"
                            "get_mail_bodies 回傳指定未讀信的內文；run_sync 在背景啟動同步。"}})
    if method == "ping":
        return send({"jsonrpc": "2.0", "id": rid, "result": {}})
    if method == "tools/list":
        return send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    if method == "tools/call":
        params = req.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name in ("get_mail_bodies", "run_sync"):
            try:
                res = get_mail_bodies(args.get("uids")) if name == "get_mail_bodies" \
                    else run_sync(args.get("target") or "all", bool(args.get("heavy")))
                text = (f"內文 {len(res['bodies'])} 封" if name == "get_mail_bodies"
                        else "已啟動：" + ("、".join(res["started"]) or "沒有") +
                        "".join(f"；{x['target']} 略過（{x['reason']}）" for x in res["skipped"]))
                return send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": text}], "structuredContent": res, "isError": False}})
            except Exception as e:
                return send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"失敗：{type(e).__name__}: {e}"}], "isError": True}})
        if name != "get_dashboard_data":
            return send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool: {name}"}})
        try:
            data = get_dashboard_data()
            return send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "ok：作業 %d 份、未讀信 %d 封%s（完整資料在 structuredContent）" % (
                    len((data["data"] or {}).get("assignments", [])), len((data["mail"] or {}).get("messages", [])),
                    ("；" + "、".join(data["problems"])) if data["problems"] else "")}],
                "structuredContent": data, "isError": False}})
        except Exception as e:
            return send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"讀取失敗：{type(e).__name__}: {e}"}], "isError": True}})
    if method in ("resources/list", "prompts/list"):
        key = method.split("/")[0]
        return send({"jsonrpc": "2.0", "id": rid, "result": {key: []}})
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}})


def register(cfg_path, python_exe=None, remove=False):
    """把 betterel 加進 (或移出) Claude 桌面版的 claude_desktop_config.json。

    用 Python 的 json 來改, 不用 PowerShell 5.1 的 ConvertFrom-Json:
    後者把 key 當成不分大小寫, 設定檔裡只要有 "C:\\code stuff\\geothings" 和
    "...\\Geothings" 這種只差大小寫的 key 就會解析失敗。
    其他設定原封不動 (順序也保留), 只動 mcpServers.betterel。"""
    p = Path(cfg_path)
    cfg = {}
    if p.exists() and p.read_text(encoding="utf-8-sig").strip():
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    servers = cfg.setdefault("mcpServers", {})
    if remove:
        servers.pop("betterel", None)
    else:
        servers["betterel"] = {"command": python_exe or sys.executable,
                               "args": [str(Path(__file__).resolve())],
                               "env": {"PYTHONIOENCODING": "utf-8"}}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    print(("已移除" if remove else "已加入") + " betterel → " + str(p))


def main():
    if len(sys.argv) >= 3 and sys.argv[1] in ("--register", "--unregister"):
        # python betterel_mcp.py --register <設定檔> [python.exe]
        register(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None, remove=sys.argv[1] == "--unregister")
        return
    if "--test" in sys.argv:              # 手動測試: python betterel_mcp.py --test
        d = get_dashboard_data()
        print(json.dumps({"problems": d["problems"], "sync": d["sync"],
                          "assignments": len((d["data"] or {}).get("assignments", [])),
                          "unread_mail": len((d["mail"] or {}).get("messages", []))}, ensure_ascii=False))
        return
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        for r in (req if isinstance(req, list) else [req]):
            try:
                handle(r)
            except Exception as e:
                if r.get("id") is not None:
                    send({"jsonrpc": "2.0", "id": r["id"], "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
