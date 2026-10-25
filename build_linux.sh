#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv-build
. .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name STEGO \
  --collect-all cv2 \
  --collect-all cryptography \
  --hidden-import=tkinter \
  --hidden-import=tkinter.ttk \
  --hidden-import=tkinter.filedialog \
  --hidden-import=tkinter.messagebox \
  stego_test_file2_newuserguide.py
mkdir -p release
cp dist/STEGO release/STEGO-linux-x86_64
chmod +x release/STEGO-linux-x86_64
file release/STEGO-linux-x86_64
