#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 gms 信箱的「Gmail 應用程式密碼」用 Windows DPAPI 加密存成 mail_cred.dat。

- 密碼只在你按鍵盤的那一刻存在於記憶體, 不會寫進任何 log
- mail_cred.dat 用你這個 Windows 帳戶的金鑰加密, 換帳戶或換電腦都解不開
- 這支程式只有你自己會跑; Claude 不會執行它, 也不會讀 mail_cred.dat

應用程式密碼哪裡拿:
  1. 用 gms 帳號登入 → myaccount.google.com → 安全性 → 開「兩步驟驗證」
  2. myaccount.google.com/apppasswords → 取名 moodle-mail → 產生 16 碼
  3. 把那 16 碼貼進這支程式 (中間有沒有空格都可以)
"""

import base64
import getpass
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRED_PATH = HERE / "mail_cred.dat"
CRED_DESC = "ndhu-mail-sync"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        import win32crypt  # type: ignore
    except ImportError:
        print("缺少 pywin32。先跑:  pip install pywin32")
        return 1

    print("設定 gms 信箱的 Gmail 應用程式密碼 — 輸入時不會顯示。")
    print("(不是你平常登入的密碼, 是 myaccount.google.com/apppasswords 產生的 16 碼)")
    pw1 = getpass.getpass("應用程式密碼: ")
    pw1 = re.sub(r"\s+", "", pw1)
    if not pw1:
        print("空的, 取消。")
        return 1
    if len(pw1) != 16:
        print(f"注意: 一般應用程式密碼是 16 碼, 你輸入的是 {len(pw1)} 碼 — 還是先存起來, 等下用 --test 驗證。")
    pw2 = re.sub(r"\s+", "", getpass.getpass("再輸入一次: "))
    if pw1 != pw2:
        print("兩次不一樣, 取消。")
        return 1

    blob = win32crypt.CryptProtectData(
        pw1.encode("utf-8"), CRED_DESC, None, None, None, 0
    )
    CRED_PATH.write_bytes(base64.b64encode(blob))
    del pw1, pw2
    print(f"已加密存到 {CRED_PATH}")
    print("驗證: python mail_sync.py --test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
