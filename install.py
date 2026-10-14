"""BetterElearning 安裝：在這個資料夾跑 python install.py，照畫面做。

實際的安裝腳本在 plugins/better-ndhu-elearning/skills/setup/files/install_full.py。
"""
import runpy
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("需要 Python 3.9 以上")
script = Path(__file__).resolve().parent / "plugins" / "better-ndhu-elearning" / "skills" / "setup" / "files" / "install_full.py"
sys.argv[0] = str(script)
runpy.run_path(str(script), run_name="__main__")
