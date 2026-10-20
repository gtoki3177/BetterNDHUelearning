"""代交作業：app 把檔案切塊存在 Firestore users/{uid}/submit/{id} (/chunks/{檔案序號}_{塊序號})，
這支在雲端 (GitHub Actions) 登入 e學苑交上去，交完把作業頁上的檔案下載回來比對 SHA-256，結果寫回 submit/{id}。

  python submit.py --id <submit id>

submit/{id} 的內容 (app 寫的)：
  {"cmid": 158926, "url": "...view.php?id=158926", "title": "計算機結構 HW#3", "replace": true, "dry": false,
   "files": [{"idx": 0, "name": "HW03.pdf", "size": 123456, "sha256": "...", "chunks": 1}], "state": "queued"}
dry = true：只傳到 e學苑的暫存區、把暫存區的檔案下載回來比對，不按「儲存」(作業不會變)，測流程用。

狀態 (state)：queued → login → uploading → saving → verifying → done / failed；失敗時 error 寫原因。
"""
import argparse
import base64
import configparser
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cloud_push        # noqa: E402
import moodle_sync as ms  # noqa: E402

TZ = timezone(timedelta(hours=8))


class SubmitError(Exception):
    pass


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def state(ref, **kw):
    ref.set({**kw, "at": now_iso()}, merge=True)
    print(json.dumps({k: v for k, v in kw.items() if k != "files_result"}, ensure_ascii=False))


def load_files(ref, meta):
    """把切塊存的檔案拼回來，每一塊都對過 app 算的指紋 (雲端暫存沒壞)；整個檔案的指紋之後拿來跟 e學苑上的比。"""
    out = []
    for f in meta.get("files", []):
        parts = []
        for k in range(int(f.get("chunks", 0))):
            d = ref.collection("chunks").document(f"{f['idx']}_{k}").get().to_dict() or {}
            piece = base64.b64decode(d.get("b64", ""))
            if d.get("h") and hashlib.sha256(piece).hexdigest() != d["h"]:
                raise SubmitError(f"{f['name']} 在雲端暫存時壞掉了 (第 {k + 1} 塊指紋對不上)，請重新送一次")
            parts.append(piece)
        raw = b"".join(parts)
        if f.get("sha256") and hashlib.sha256(raw).hexdigest() != f["sha256"]:
            raise SubmitError(f"{f['name']} 在雲端暫存時壞掉了 (指紋對不上)，請重新送一次")
        if f.get("size") and len(raw) != int(f["size"]):
            raise SubmitError(f"{f['name']} 在雲端暫存時少了一截 ({len(raw)} / {f['size']} bytes)，請重新送一次")
        out.append({"name": f["name"], "raw": raw, "sha256": hashlib.sha256(raw).hexdigest()})
    if not out:
        raise SubmitError("沒有要交的檔案")
    return out


def submission_form(html):
    """提交頁的表單：隱藏欄位、儲存按鈕、檔案暫存區 (itemid)、上傳用的 repository id、可接受的檔案類型。"""
    soup = BeautifulSoup(html, "html.parser")
    form = None
    for f in soup.find_all("form"):
        if f.find("input", attrs={"name": "action", "value": "savesubmission"}):
            form = f
            break
    if not form:
        raise SubmitError("找不到 e學苑的繳交表單 (作業可能已截止、不開放繳交，或頁面改版了)")
    fields = {i["name"]: i.get("value", "") for i in form.find_all("input")
              if i.get("name") and (i.get("type") or "").lower() == "hidden"}
    btn = form.find("input", attrs={"name": "submitbutton"})
    if not btn or "files_filemanager" not in fields:
        raise SubmitError("這個作業不是用上傳檔案繳交的 (例如線上文字)，目前還不支援")
    fields["submitbutton"] = btn.get("value", "")
    m = re.search(r'"id":"(\d+)","name":"[^"]*","type":"upload"', html)
    ctx = re.search(r'"target":"id_files_filemanager".*?"context":\{"id":(\d+)', html, re.S)
    acc = re.search(r'"target":"id_files_filemanager","accepted_types":\[([^\]]*)\]', html)
    lic = re.search(r'"defaultlicense":"([^"]+)"', html)
    maxfiles = re.search(r'"maxfiles":"?(-?\d+)"?,"client_id"', html)
    maxbytes = re.search(r'"maxbytes":"?(\d+)"?,"maxfiles"', html)
    return {
        "fields": fields,
        "itemid": fields["files_filemanager"],
        "repo_id": m.group(1) if m else None,
        "ctx_id": ctx.group(1) if ctx else None,
        "accepted": [t.strip().strip('"').lower() for t in acc.group(1).split(",")] if acc and acc.group(1).strip() else [],
        "license": lic.group(1) if lic else "unknown",
        "maxfiles": int(maxfiles.group(1)) if maxfiles else -1,
        "maxbytes": int(maxbytes.group(1)) if maxbytes else 0,
    }


def draft_list(mo, itemid):
    r = mo.s.post(f"{mo.base}/repository/draftfiles_ajax.php", params={"action": "list"},
                  data={"sesskey": mo.sesskey, "itemid": itemid, "filepath": "/"}, timeout=45)
    try:
        return (r.json() or {}).get("list", [])
    except ValueError:
        raise SubmitError("讀不到 e學苑的暫存區 (session 可能過期了)")


def draft_delete(mo, itemid, name):
    mo.s.post(f"{mo.base}/repository/draftfiles_ajax.php", params={"action": "delete"},
              data={"sesskey": mo.sesskey, "itemid": itemid, "filepath": "/", "filename": name}, timeout=45)


def draft_upload(mo, form, f, author):
    r = mo.s.post(f"{mo.base}/repository/repository_ajax.php", params={"action": "upload"},
                  data={"sesskey": mo.sesskey, "repo_id": form["repo_id"], "itemid": form["itemid"], "savepath": "/",
                        "title": f["name"], "author": author, "license": form["license"], "ctx_id": form["ctx_id"],
                        "overwrite": "1"},
                  files={"repo_upload_file": (f["name"], f["raw"], "application/octet-stream")}, timeout=180)
    try:
        j = r.json()
    except ValueError:
        raise SubmitError(f"上傳 {f['name']} 時 e學苑回了看不懂的東西 (HTTP {r.status_code})")
    if isinstance(j, dict) and j.get("error"):
        raise SubmitError(f"e學苑拒絕了 {f['name']}：{j['error']}")
    return j


def verify(mo, cmid, files, dry, itemid):
    """把 e學苑上的檔案下載回來，跟要交的比對 SHA-256。dry：比對暫存區；正式：比對作業頁「繳交的檔案」。"""
    remote = {}
    status = ""
    if dry:
        for it in draft_list(mo, itemid):
            if it.get("url"):
                remote[it.get("filename")] = it["url"]
    else:
        html = mo.get(f"/mod/assign/view.php?id={cmid}").text
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href*="assignsubmission_file/submission_files"]'):
            name = unquote(a["href"].split("?")[0].rsplit("/", 1)[-1])
            remote[name] = a["href"]
        for tr in soup.select("table tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0] in ("繳交狀態", "Submission status"):
                status = cells[1]
    result = []
    for f in files:
        url = remote.get(f["name"])
        if not url:
            result.append({"name": f["name"], "ok": False, "why": "e學苑上找不到這個檔案"})
            continue
        got = mo.s.get(url, timeout=180).content
        same = hashlib.sha256(got).hexdigest() == f["sha256"]
        result.append({"name": f["name"], "ok": same, "size": len(got), **({} if same else {"why": "內容跟你選的檔案不一樣"})})
    return result, status, sorted(remote)


def run(sid):
    user = cloud_push.connect()
    ref = user.collection("submit").document(sid)
    meta = ref.get().to_dict()
    if not meta:
        raise SystemExit(f"找不到 submit/{sid}")
    try:
        files = load_files(ref, meta)
        cmid = int(meta.get("cmid") or re.search(r"id=(\d+)", meta.get("url", "")).group(1))
        dry = bool(meta.get("dry"))

        state(ref, state="login")
        cfg = configparser.ConfigParser()
        cfg.read(HERE / "config.ini", encoding="utf-8-sig")
        S = cfg["moodle"]
        mo = ms.Moodle(S.get("base_url", "https://elearn4.ndhu.edu.tw/moodle"), S.get("username", "").strip(),
                       ms.load_password(), delay=0.3)
        mo.login()

        page = mo.get(f"/mod/assign/view.php?id={cmid}&action=editsubmission")
        form = submission_form(page.text)
        if not form["repo_id"] or not form["ctx_id"]:
            raise SubmitError("e學苑的上傳設定讀不到 (頁面可能改版了)")
        if form["accepted"]:
            bad = [f["name"] for f in files if not any(f["name"].lower().endswith(t) for t in form["accepted"] if t.startswith("."))]
            if bad and not any(t in ("*", "") for t in form["accepted"]):
                raise SubmitError(f"這個作業只收 {'、'.join(form['accepted'])}，{'、'.join(bad)} 不符合")
        big = [f["name"] for f in files if form["maxbytes"] and len(f["raw"]) > form["maxbytes"]]
        if big:
            raise SubmitError(f"這個作業每個檔案最大 {form['maxbytes'] // 1048576} MB，{'、'.join(big)} 太大了")
        if form["maxfiles"] > 0 and len(files) > form["maxfiles"]:
            raise SubmitError(f"這個作業最多只能交 {form['maxfiles']} 個檔案")

        state(ref, state="uploading")
        existing = [it.get("filename") for it in draft_list(mo, form["itemid"]) if it.get("type") != "folder"]
        names = {f["name"] for f in files}
        for name in existing:                       # 取代：先把原本交的拿掉；不取代：只換掉同名的
            if meta.get("replace", True) or name in names:
                draft_delete(mo, form["itemid"], name)
        for f in files:
            draft_upload(mo, form, f, S.get("username", ""))

        if not dry:
            state(ref, state="saving")
            r = mo.s.post(f"{mo.base}/mod/assign/view.php", data=form["fields"], timeout=90)
            if 'value="savesubmission"' in r.text:     # 存成功會跳回作業頁；表單還在 = 被退回 (表單過期、格式不符…)
                err = BeautifulSoup(r.text, "html.parser").select_one(".error, .alert-danger, .form-control-feedback")
                raise SubmitError("按下儲存後 e學苑沒有接受" + (f"：{err.get_text(' ', strip=True)[:120]}" if err else ""))

        state(ref, state="verifying")
        result, status, remote = verify(mo, cmid, files, dry, form["itemid"])
        ok = all(x["ok"] for x in result)
        state(ref, state="done" if ok else "failed", dry=dry, files_result=result, status_text=status, remote_files=remote,
              **({} if ok else {"error": "交上去了，但檔案比對沒有通過：" + "；".join(f"{x['name']} {x.get('why', '')}" for x in result if not x["ok"])}))
    except SubmitError as e:
        state(ref, state="failed", error=str(e))
    except Exception as e:  # noqa: BLE001
        state(ref, state="failed", error=f"未預期的錯誤：{e!r}"[:300])
        raise
    finally:
        for d in ref.collection("chunks").list_documents():   # 暫存的檔案用完就刪
            d.delete()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="代交 e學苑作業 (app 送來的)")
    ap.add_argument("--id", required=True, help="submit 文件 id")
    a = ap.parse_args()
    run(a.id)


if __name__ == "__main__":
    main()
