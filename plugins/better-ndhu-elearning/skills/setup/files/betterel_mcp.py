#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BetterElearning 本機 MCP server —— 讓儀表板在 Claude 桌面版裡直接讀同步腳本的輸出。

只做一件事: 有人呼叫 get_dashboard_data 時, 讀同一個資料夾裡的
latest.json (moodle_sync.py 產生) 和 mail.json (mail_sync.py 產生),
整理成儀表板要的形狀回傳。

- 只讀這兩個檔 (加 config.ini 拿科系對照), 不寫任何東西, 不連網路。
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
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LATEST = HERE / "latest.json"
MAIL = HERE / "mail.json"
CONFIG = HERE / "config.ini"

SERVER_INFO = {"name": "betterel", "version": "1.0.0"}
DEFAULT_DEPT_MAP = "CSIE:資訊工程學系, EE:電機工程學系, GC:通識教育中心, XX:通識教育中心, YY:體育中心"

TOOLS = [{
    "name": "get_dashboard_data",
    "title": "讀取 BetterElearning 同步結果",
    "description": "讀本機 _moodle 資料夾裡 moodle_sync.py 的 latest.json 和 mail_sync.py 的 mail.json，"
                   "回傳整理好的課程、作業和未讀信件（信件只含開頭摘錄）。唯讀。",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
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


def get_dashboard_data():
    cfg = load_config()
    out = {"read_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "data": None, "mail": None, "problems": []}
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
            "instructions": "BetterElearning 儀表板的本機資料來源。get_dashboard_data 回傳 e學苑課程/作業和 gms 未讀信件摘錄。"}})
    if method == "ping":
        return send({"jsonrpc": "2.0", "id": rid, "result": {}})
    if method == "tools/list":
        return send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    if method == "tools/call":
        name = (req.get("params") or {}).get("name")
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
        print(json.dumps({"problems": d["problems"],
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
