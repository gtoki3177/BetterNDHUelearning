#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 e學苑密碼用 Windows DPAPI 加密存成 cred.dat。

- 密碼只在你自己按鍵盤的那一刻存在於記憶體, 不會寫進任何 log
- cred.dat 用你這個 Windows 使用者帳戶的金鑰加密, 換帳戶或換電腦都解不開
- 這支程式只有你自己會跑; Claude 不會執行它, 也不會讀 cred.dat
"""

import base64
import getpass
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRED_PATH = HERE / "cred.dat"


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

    print("設定 e學苑 (gms) 密碼 — 輸入時不會顯示。")
    pw1 = getpass.getpass("密碼: ")
    if not pw1:
        print("空的, 取消。")
        return 1
    pw2 = getpass.getpass("再輸入一次: ")
    if pw1 != pw2:
        print("兩次不一樣, 取消。")
        return 1

    blob = win32crypt.CryptProtectData(
        pw1.encode("utf-8"), "ndhu-moodle-sync", None, None, None, 0
    )
    CRED_PATH.write_bytes(base64.b64encode(blob))
    del pw1, pw2
    print(f"已加密存到 {CRED_PATH}")
    print("驗證: python moodle_sync.py --force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
