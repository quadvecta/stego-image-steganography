# STEGO v1.01
Python desktop app for encrypting, embedding, and recovering messages in images using LSB steganography.

No AI service (For Now), telemetry,
runtime downloads, or new network integration was added.

## Run

Keep these four application modules together:

- `stego_test_file2_newuserguide.py`: authoritative GUI and executable entry point.
- `stego_engine.py`: matching engine; unchanged v1.0 cryptographic format.
- `stego_dialogs.py`: guarded native file selection.
- `stego_themes.py`: Cyber, Dark and Light palettes and in-place styling.

```bash
python stego_test_file2_newuserguide.py
```

Use your existing Python environment with Tkinter, NumPy, OpenCV and cryptography.
Pillow remains optional for the embedded-logo path; the supplied project has no
embedded logo data, so the existing vector fallback is used. Pillow is required
only for the optional screenshot script and embedded-logo tests.

`requirements.txt` lists the existing Python dependency families without imposing
a new version floor. Tkinter is an OS/Python component, not a pip package. No
original dependency lockfile or supported-version matrix was supplied.

Verified Linux combinations:

| Python | Tk | NumPy | OpenCV | cryptography | Pillow |
|---|---|---|---|---|---|
| 3.13.16 | 8.6.13 | 2.5.4 | 5.0.0 | 50.0.2 | 13.0.0 |
| 3.10.20 | 9.0 | 1.26.4 | 4.10.0 | 42.0.8 | 10.4.0 |

Verification used the `opencv-python-headless` distribution because the application
uses Tk, not OpenCV HighGUI, for its interface. These versions describe tests, not
a recommendation to downgrade security dependencies.

## UI Screenshots
### 1. Homescreen:
<img width="823" height="592" alt="Screenshot_20261025_104559" src="https://github.com/user-attachments/assets/bae0e690-87f7-4214-ad2f-8821ab40fd93" />

---

### 2. Encode Screen:
  <img width="733" height="592" alt="image" src="https://github.com/user-attachments/assets/03cf8d79-0643-440a-9077-a4c3b2ca4786" />

---

### 3. Decode Screen:
<img width="730" height="590" alt="image" src="https://github.com/user-attachments/assets/27c1446e-d610-41e4-8986-b4c8654097b1" />

---

### 4. Heatmap instance:
   <img width="691" height="584" alt="image" src="https://github.com/user-attachments/assets/3aece055-e56e-4157-a6b8-5fc9740dad8a" />

---

## Test

From this release directory:

```bash
python -m unittest discover -s . -p 'test_*.py' -v
# On a headless Linux machine with Xvfb installed:
xvfb-run -a python -m unittest discover -s . -p 'test_*.py' -v
python benchmark_engine.py --repeats 9
python qa_visuals.py
```

`qa_visuals.py` needs a display and Pillow. It generates synthetic-data screenshots
and measures the complete 4K heatmap job. Unit tests explicitly skip display-only
checks when a display is unavailable; skips are not passes. Native Tk chooser tests
are Linux-specific and complete real dialogs using Tcl commands, not manual clicks.

`reference/stego_engine.py` is an unmodified audit fixture, not an alternate
application engine. Compatibility tests and benchmarks load it explicitly.

## Behavior

- PNG is recommended; BMP remains supported. JPEG remains experimental and is
  committed only if its saved pixels decode to the intended message.
- A verified temporary image atomically replaces the chosen destination. Failure
  leaves an existing destination intact. Existing mode bits are retained, and
  read-only destinations are rejected. See metadata limitations in the report.
- Image filters show PNG/JPEG/BMP; the original All Files option remains. Image
  reading continues to normalize grayscale/alpha/16-bit files to 8-bit BGR.
- The engine supports empty text. The GUI retains its original empty-message
  validation and outer-whitespace trimming. No message-handling change was made.
- Theme choice is session-only. Switching never rebuilds forms or changes image,
  plaintext, token, or worker state. The default remains Cyber.
- Heatmap and analysis start in simple mode. Advanced controls and plots remain.
  These screens scroll on smaller windows.
- Analysis always reads the saved file. Heatmap difference headlines explicitly
  describe the in-memory encoding instead.

## Handoff

Read `CHANGE_REPORT.md` for defects, exact test commands, results, limitations and
Linux/Windows manual QA. `source-changes.patch` is the complete Python source diff.
The browser-friendly summary is delivered separately as `stego-report.html`.

No macOS native tests, PyInstaller build, executable, or ELF release is
claimed.macOS use standard parent-aware Tk native dialogs; Linux selects
one installed Zenity/KDialog backend or uses Tk. 
