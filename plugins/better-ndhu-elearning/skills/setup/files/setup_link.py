"""產生 BetterElearning 的設定連結和 QR code。

    python setup_link.py <firebaseConfig 檔> [--email 登入用的 email] [--qr 輸出.svg] [--site https://betterel.web.app]

<firebaseConfig 檔> 可以是 `firebase apps:sdkconfig web` 的輸出、主控台那段 `const firebaseConfig = {...}`，或純 JSON。
電腦：用瀏覽器打開連結。手機：裝好 app 後用相機掃 QR code，會直接用 app 打開。
連結裡只有 Firebase 的網頁設定（不是秘密）和 email，沒有密碼。
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path

KEYS = ("apiKey", "authDomain", "projectId", "storageBucket", "messagingSenderId", "appId")


def parse(text):
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        sys.exit("找不到 firebaseConfig 的 { ... }")
    body = re.sub(r"//[^\n]*", "", m.group(0))
    body = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', body).replace("'", '"')
    body = re.sub(r",\s*}", "}", body)
    cfg = json.loads(body)
    cfg = {k: cfg[k] for k in KEYS if cfg.get(k)}
    for k in ("apiKey", "projectId", "appId"):
        if k not in cfg:
            sys.exit(f"firebaseConfig 少了 {k}")
    return cfg


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--email", default="")
    ap.add_argument("--qr", help="輸出 QR code (.svg)")
    ap.add_argument("--site", default="https://betterel.web.app")
    a = ap.parse_args()
    cfg = parse(Path(a.config).read_text(encoding="utf-8"))
    payload = {"config": cfg}
    if a.email:
        payload["email"] = a.email.strip()
    c = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).decode().rstrip("=")
    url = f"{a.site.rstrip('/')}/setup?c={c}"
    print(url)
    if a.qr:
        try:
            import qrcode
            import qrcode.image.svg
        except ImportError:
            sys.exit("要產生 QR code 請先安裝：pip install qrcode")
        img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=12, border=3)
        img.save(a.qr)
        print(f"QR code：{a.qr}", file=sys.stderr)


if __name__ == "__main__":
    main()
