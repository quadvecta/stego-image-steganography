# Building STEGO v1.01 releases

## Important status
These are build scripts, not prebuilt binaries. The current assistant environment has no PyInstaller installed and cannot reach the package index, so it could not produce or test ELF/EXE binaries here.

## Linux ELF
On a Linux desktop with Python 3.10–3.13, Tk development/runtime libraries, and internet access:
```bash
chmod +x build_linux.sh
./build_linux.sh
```
Output: `release/STEGO-linux-x86_64` (single-file PyInstaller executable).

## Windows EXE
On Windows with Python 3.10–3.13:
```bat
build_windows.bat
```
Output: `release\STEGO-windows-x86_64.exe`.

## Build both through GitHub Actions
Push this folder to a GitHub repository and run **Actions → Build STEGO desktop releases → Run workflow**, or push a `v*` tag. The workflow builds on native Linux and Windows runners and uploads separate artifacts. Download each artifact from the workflow run.

## QA before distribution
- Run the executable on a clean target machine.
- Verify encode → save PNG → decode saved PNG → compare exact payload.
- Test Light, Dark, Cyber themes and file-dialog cancellation.
- Confirm Linux display/Tk dependencies and Windows Defender behavior.
- Do not distribute a binary until this QA is completed.

PyInstaller builds are platform-specific: build Linux on Linux and Windows on Windows. The executable does not eliminate OS-level Tk/display requirements. No macOS build is included in this kit.
