@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv-build
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name STEGO ^
  --collect-all cv2 ^
  --collect-all cryptography ^
  --hidden-import=tkinter ^
  --hidden-import=tkinter.ttk ^
  --hidden-import=tkinter.filedialog ^
  --hidden-import=tkinter.messagebox ^
  stego_test_file2_newuserguide.py
if not exist release mkdir release
copy dist\STEGO.exe release\STEGO-windows-x86_64.exe
echo Built release\STEGO-windows-x86_64.exe
