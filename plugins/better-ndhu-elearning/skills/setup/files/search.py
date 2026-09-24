#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教材全文檢索。

用法:
    python search.py "B+ tree"
    python search.py "分頁"                  # 中文可以搜到詞的一部分
    python search.py "deadlock" --course 課程名
    python search.py "quantum NOT deadlock" -n 20
    python search.py --list                  # 看索引了哪些檔案
    python search.py --stats                 # 索引概況

查詢語法是 SQLite FTS5: 空白 = AND, OR, NOT, "片語", 英文前綴用 term*
中文會自動逐字拆開再當片語查, 所以搜「分頁」在「虛擬記憶體分頁機制」裡也找得到。
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "state.db"

CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]")


def cjk_space(text):
    return CJK_RE.sub(lambda m: f" {m.group(0)} ", text or "")


def cjk_query(q):
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
            buf.extend(cjk_space(tok).split())
        else:
            flush()
            out.append(tok)
    flush()
    return " ".join(out) or q


def make_snippet(raw, query, width=170):
    """在原文裡找查詢字, 抓前後文。"""
    raw = re.sub(r"\s+", " ", raw or "").strip()
    terms = [t for t in re.findall(r'[A-Za-z0-9_]+|[^\W\d_]{1,}', query) if len(t) > 1]
    pos = -1
    hit = ""
    for t in terms:
        m = re.search(re.escape(t), raw, re.IGNORECASE)
        if m:
            pos, hit = m.start(), m.group(0)
            break
    if pos < 0:
        return raw[:width] + ("…" if len(raw) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(raw), start + width)
    frag = raw[start:end]
    if hit:
        frag = re.sub(re.escape(hit), lambda m: f">>>{m.group(0)}<<<", frag, count=1, flags=re.IGNORECASE)
    return ("…" if start else "") + frag + ("…" if end < len(raw) else "")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="搜尋已下載的課程教材")
    ap.add_argument("query", nargs="*", help="FTS5 查詢字串")
    ap.add_argument("--course", "-c", default=None, help="限定科目 (資料夾名, 可部分比對)")
    ap.add_argument("-n", type=int, default=12, help="回傳幾筆 (預設 12)")
    ap.add_argument("--list", action="store_true", help="列出已索引的檔案")
    ap.add_argument("--stats", action="store_true", help="索引概況")
    ap.add_argument("--raw", action="store_true", help="直接用原始 FTS5 語法, 不做中文處理")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print("還沒有 state.db — 先跑:  python moodle_sync.py --force")
        return 1
    con = sqlite3.connect(DB_PATH)

    if args.stats:
        files, chunks = con.execute("SELECT COUNT(DISTINCT file), COUNT(*) FROM chunks").fetchone()
        print(f"已索引 {files} 個檔案 / {chunks} 段")
        for course, n in con.execute(
                "SELECT course, COUNT(DISTINCT file) FROM chunks GROUP BY course ORDER BY 2 DESC"):
            print(f"  {course:<24} {n} 檔")
        return 0

    if args.list:
        rows = con.execute(
            "SELECT course, file, COUNT(*) FROM chunks GROUP BY file ORDER BY course, file"
        ).fetchall()
        if not rows:
            print("索引是空的 — 學期初正常, 老師還沒傳東西。")
            return 0
        cur = None
        for course, file, n in rows:
            if course != cur:
                print(f"\n=== {course} ===")
                cur = course
            print(f"  {Path(file).name}  ({n} 段)")
        print(f"\n共 {len(rows)} 個檔案")
        return 0

    if not args.query:
        ap.print_help()
        return 1

    q = " ".join(args.query)
    fts = q if args.raw else cjk_query(q)

    sql = ("SELECT course, file, page, raw, bm25(chunks) AS rank "
           "FROM chunks WHERE chunks MATCH ?")
    params = [fts]
    if args.course:
        sql += " AND course LIKE ?"
        params.append(f"%{args.course}%")
    sql += " ORDER BY rank LIMIT ?"
    params.append(args.n)

    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"查詢語法錯誤: {e}\n(送進 FTS5 的是: {fts})")
        return 1

    if not rows:
        print(f"找不到「{q}」。`--list` 看索引裡有什麼, `--stats` 看概況。")
        return 0

    for course, file, page, raw, _rank in rows:
        print(f"\n[{course}] {Path(file).name}  —  {page}")
        print(f"    {make_snippet(raw, q)}")
        print(f"    {file}")
    print(f"\n{len(rows)} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main())
