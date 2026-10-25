# STEGO v1.01 Change Report

## Outcome

The supplied application was refined, not rewritten. The final Linux suites pass
**147 tests on Python 3.13 and 147 on Python 3.10**, with no skips in either
display-enabled run. Source-format compatibility is also checked against the
unmodified supplied engine. Windows, macOS, native Zenity/KDialog desktop behavior,
and packaged executables remain unverified.

## Audit And Baseline

- The complete supplied project consists of four Python files. There is no Git
  repository, build configuration, requirements file, external asset folder,
  PyInstaller spec, or declared Python/dependency support matrix.
- `stego_test_file2_newuserguide.py` contains `StegoApp` and the `__main__` entry
  point. Its authoritative engine is the adjacent `stego_engine.py`.
- `test_gui_smoke.py` imported an absent `stego_gui` module. The baseline used an
  in-process import alias, without changing the uploaded source. The updated test
  imports the actual supplied GUI name directly; no duplicate GUI was introduced.
- `_draw_logo()` lacked `base64` and `io` imports. `_LOGO_B64` is also absent from
  the supplied files. Imports are fixed; the original fallback remains. An injected
  PNG exercises the embedded path, but a missing original logo was not fabricated.
- The initial stock-Python discovery failed at import because NumPy/OpenCV and
  shared Tk libraries were unavailable. A separate test environment and extracted
  Tcl/Tk/Xvfb libraries made actual engine and GUI execution possible.
- Functional baseline: **25 tests, 24 passes, one failure** in 1.048 seconds.
  Engine: 20/21; GUI smoke: 4/4. The failure was an obsolete `v1` expectation for a
  16-byte legacy token; the existing parser correctly says `v0.x`. The retained
  test now asserts that behavior. A test-only unclosed file warning was fixed.

## Defects And Evidence

| Item | Root Cause / Original Problem | Fix | Verification |
|---|---|---|---|
| A: three Linux pickers | Each wrapper tried Zenity, then KDialog, then Tk after cancellation/non-success. No duplicate widget bindings were found. | Choose one available backend before showing it. Global reentry lock plus application-operation guard; release in `finally`. Poll external dialog process through Tk rather than blocking redraw. | Original chain reproduced with mocks for all three wrappers. Twelve dialog tests cover choices, filters, cancel, nested calls, startup/exit/Tcl/timeout errors, destruction and Unicode. Real Linux Tk open/save/text dialogs return selections and cancel with one dialog window. |
| A: silent errors | A failed picker could look like cancellation. | Exit 1 means cancellation; other errors/timeouts reach the GUI error handler, with no backend retry. | Error-propagation and guard-release regressions. |
| A/D2: extension collision | Appending a default extension after selection could bypass overwrite confirmation at the final filename. | Confirm an existing completed path. Never append to an explicit unrelated extension; reject mode/extension mismatch. | Final-path overwrite approval/rejection tests; explicit Unicode filenames unchanged. |
| D1: logo | Missing imports were swallowed by fallback; original logo data is absent. | Import `base64`/`io`, theme the background and retain vector fallback. | Real ImageTk rendering from injected PNG; missing/corrupt asset fallback; live theme tests. |
| D2: JPEG/export mismatch | JPEG mode still showed PNG/BMP filters and could create doubled extensions. | Matching mode/filter/default extension; preserve explicit path; lossless recommendation and accurate JPEG warning. | PNG/BMP/JPG/JPEG mode tests, mismatch/cancel tests, actual engine JPEG rejection or verified recovery only. |
| D3: saved-image analysis | JPEG analysis used the pre-save array while PNG/BMP reopened disk output. | Always read the saved path for every format and label the comparison. Clear stale plots/metrics if reading fails. | Actual PNG/BMP/JPG/JPEG reads and mocked distinct reopened pixels fed to every metric/plot. |
| D4: unsafe failed saves | Direct writes could leave partial files; verification failure removed the destination, including overwritten pre-existing data. Successful decode was not compared with intended plaintext. | Unique same-directory temporary file, full write/flush/fsync, reopen, dimensions/pixels/authentication and exact-message checks, then atomic replace. Remove only owned staging files. Commit result metadata only on success. | Failure matrices for encoder, create, partial/short write, flush, fsync, stat, reopen, dimensions, pixels, authentication, wrong plaintext, rename and interruption; existing targets and prior verified state survive. |
| D4: permissions | Atomic replacement would otherwise reset existing mode bits or replace a read-only target. | Reject non-writable targets, retain mode bits, and check that POSIX mode preservation succeeded. | Real POSIX 0640 retained and 0444 rejected; access/chmod-failure regressions. ACL/ownership limitations remain below. |
| D4: other exports | Key text and heatmap writes could truncate an existing file before a write failed. A key path could have an image extension. | Shared atomic byte writer for key/preview exports, preserving permissions; key requires `.txt`; heatmap requires PNG/BMP. | Failed commit/write cleanup, read-only modes, invalid-extension preservation, Unicode text/image exports. |
| B: heatmap integrity | Resizing the whole cell grid stretched partial edge cells, shifting image-coordinate alignment. Constant positive grids looked empty. | Map display coordinates through image pixels and cell size; constant positive/zero grids use consistent colors and a constant legend. | Edge/corner and cropped-expansion oracle tests; exact header/payload aggregation; empty/invalid inputs and read-only-array tests. |
| B: complexity | Raw controls and unexplained numerical metrics dominated the initial screens. | Simple defaults, plain-language placement/area/distribution/change summaries, explained PSNR and identical-image wording. Advanced controls, difference image and blue histogram remain. Scrollable views fit smaller windows. | Summary/denominator tests, scope snapshots, actual subset calculations, toggling tests, screenshot inspection in all themes. No first-time-user usability study was performed. |
| C: themes | Colors were global Cyber-only values; captured defaults/closures would resist switching. | Central 21-role Cyber/Dark/Light palettes; recolor existing widgets, menus and vector items in place. Map captured defaults; current focus/hover/status colors follow the selected palette. | Theme/GUI tests preserve text, undo/selection, focus, layout, tokens, result identity, arrays, callbacks and worker state. Help/export windows are themed. |
| D5: lifecycle | Decode validation could retain old plaintext; Back could clear state during decoding; dialogs allowed nested operations; closing in a worker callback could reschedule polling. | Clear plaintext before validation, guard navigation/jobs/dialogs, clean modal windows in `finally`, cancel polls on destruction and never reschedule after close. Keep existing singleton help behavior. | Real worker success/failure/main-thread callbacks, repeat-click rejection, close/navigation, help reopen, stale-data and failure regressions. |
| D6: performance | Full image hit maps and padded float64 grids were unnecessary for small display grids. A full-resolution comparison added excessive temporary memory to heatmap headlines. | Direct position-to-cell aggregation; row-bounded summary calculation; worker rendering; no new cache. Theme changes do not reconstruct forms. | Exact equivalence tests, repeatable 4K benchmark, GUI heartbeat observation and allocation measurement below. |

## File Changes

| File | Purpose |
|---|---|
| `stego_test_file2_newuserguide.py` | Existing GUI refined: picker integration, logo, export validation, simple/advanced views, themes, scrolling and lifecycle. |
| `stego_engine.py` | Only save safety, visualization aggregation/rendering and inaccurate legacy documentation changed. |
| `stego_dialogs.py` | New small centralized picker implementation; replaces duplicated platform file-picker wrappers. Standard Tk provides parent-aware native Windows/macOS dialogs rather than separate PowerShell/AppleScript processes. No custom file browser. |
| `stego_themes.py` | Central palettes and classic-Tk in-place styling. |
| `test_stego_engine.py` | Retains original coverage; fixes legacy-version assertion and closes a test file. |
| `test_gui_smoke.py` | Correct GUI import, explicit no-display skip, advanced-control exercise. |
| `test_engine_regressions.py` | 42 save, visualization, normalization and original-compatibility tests. |
| `test_gui_regressions.py` | 48 real-Tk application regression tests. |
| `test_dialogs.py`, `test_themes.py` | 12 dialog and 11 theme tests. |
| `test_review_regressions.py`, `test_native_tk_dialogs.py` | Seven final-review and two real Linux-Tk chooser tests. |
| `benchmark_engine.py`, `qa_visuals.py` | Repeatable engine benchmark, GUI responsiveness observation and synthetic screenshots. |
| `reference/stego_engine.py` | Exact uploaded engine fixture, explicitly loaded only for compatibility/benchmark evidence. |
| `README.md`, `requirements.txt`, reports/logs/patch/screenshots | Launch instructions, existing dependency families, detailed evidence and handoff. |

## Actual Verification

Test environment: Linux arm64, Xvfb 1600x1200, isolated test dependencies. The cloud
browser was not used to emulate the Python app. Screenshots are from real Tk under
Xvfb. Native Windows/macOS and GNOME/KDE desktop sessions were not available.

| Check | Executed Result |
|---|---|
| Original functional baseline | 25 tests: 24 passed, 1 failed; 1.048 s |
| Final Python 3.13.16 / Tk 8.6.13 | **147 passed**, no skips; 11.752 s; ResourceWarning treated as error |
| Final Python 3.10.20 / Tk 9.0 | **147 passed**, no skips; 13.427 s |
| Independently extracted source archive | **147 passed**, no skips; 11.902 s, without access to `uploads` |
| No-display run | Unittest reports 143 tests, **69 explicitly skipped**, no failures; 0.766 s. This is not full GUI verification. |
| Syntax compilation | `compileall` completed with exit 0; separate from functional tests |
| Original/current compatibility | Protected functions/classes and constants byte/AST-checked; deterministic pixel/position equality; bidirectional original/new decoding |
| GUI visual inspection | Cyber/Dark/Light heatmaps, Light simple/advanced analysis inspected; 760x650 scrolling regression test |
| Real Linux Tk choosers | One real dialog window per action; automated Unicode selection and cancellation for open, PNG/BMP/JPEG save, text save. Tcl completion automation, not manual clicking. |

### Exact Commands Used

Initial stock environment, before dependency setup:

```bash
python -m unittest discover -s uploads -p 'test_*.py' -v
```

Result: two import errors (NumPy/OpenCV unavailable). Tk's shared library was also
missing. This did not establish an application failure.

Baseline after environment setup:

```bash
source /tmp/bcode/tk-env.sh && python -B /tmp/bcode/run-baseline.py
```

That runner inserted `uploads` into `sys.path` and assigned
`sys.modules['stego_gui'] = importlib.import_module('stego_test_file2_newuserguide')`.
It ran both unmodified test modules. `baseline-results.txt` preserves the output.

Final commands, executed by the verification runner with the shown environment:

```bash
source /tmp/bcode/tk-env.sh
/tmp/bcode/venv/bin/python -W error::ResourceWarning -m unittest discover -s outputs -p 'test_*.py' -v
TCL_LIBRARY=/tmp/bcode/python/cpython-3.10.20-linux-aarch64-gnu/lib/tcl9.0 TK_LIBRARY=/tmp/bcode/python/cpython-3.10.20-linux-aarch64-gnu/lib/tk9.0 /tmp/bcode/venv310/bin/python -m unittest discover -s outputs -p 'test_*.py' -v
/tmp/bcode/venv/bin/python outputs/benchmark_engine.py --repeats 9
/tmp/bcode/venv/bin/python outputs/qa_visuals.py
DISPLAY='' /tmp/bcode/venv/bin/python -m unittest discover -s outputs -p 'test_*.py' -q
/tmp/bcode/venv/bin/python -m compileall -q outputs
```

These `/tmp/bcode` paths are this run's temporary test environment, not application
dependencies or hard-coded runtime paths. Portable commands are in `README.md`.
Full final output is in `tests-python313.txt`, `tests-python310.txt`,
`tests-no-display.txt`, `benchmark-results.txt` and `gui-visual-results.txt`.
The archive was also extracted into a temporary directory and tested with
`/tmp/bcode/venv/bin/python -m unittest discover -s . -p 'test_*.py' -v`;
`package-test-results.txt` records that independent-layout run.

## Performance Evidence

Fixed 3840x2160 input, cell size 61, 560-pixel overlay preview, fixed seed, one
OpenCV thread, two warmups, nine timed repetitions. Peak values are traced transient
allocations, not process RSS, and may omit native allocations.

| Position Count | Original Full Preview | Direct-Density Preview | Original Peak | New Peak |
|---|---:|---:|---:|---:|
| 100,000 | 54.022 ms | 20.918 ms | 96.046 MiB | 1.727 MiB |
| 1,000,000 | 185.851 ms | 32.100 ms | 96.046 MiB | 15.259 MiB |

These preview numbers exclude the new visual-change headlines. The **actual GUI
worker including those headlines**, on a 4K encoding with 96,464 positions, measured
**117.988 ms median** over five repetitions and **3.336 MiB traced peak**. Twelve Tk
heartbeat samples had a maximum observed gap of **20.545 ms**. This is a repeatable
observation on this host, not a latency guarantee for all images/hardware.

All summaries use actual arrays; chunking changes allocation size, not units or
definitions. Direct density matches the original aggregation, including duplicate
positions and clipped edge cells. No speculative caching, crypto shortcuts,
authentication bypass or reduced save verification was introduced. Exact-message
save verification intentionally adds one authenticated in-memory decode.

## Preserved Compatibility

- Application v1.0's internal **ST2 / version 2** header and payload layout remain.
- 32-byte random token and URL-safe base64 handling are unchanged.
- HKDF-SHA256 schedule and domain separation are unchanged.
- Fernet AES-128-CBC plus HMAC-SHA256 and header authentication are unchanged.
- ChaCha20-keyed scatter order and 2-LSB blue-channel embedding are unchanged.
- UTF-8, empty engine messages, capacity boundaries, wrong/malformed tokens,
  corruption rejection, dimensions and Unicode-path behavior are covered.
- Direct grayscale/alpha arrays remain rejected by encode/decode; file reads still
  normalize them, including dropping alpha. Existing behavior was not redefined.
- v0.x payload incompatibility remains documented and tested. The inaccurate engine
  docstring's legacy `v1.x` references were corrected to `v0.x` only.

**No cryptographic/payload exception was requested or made.**

## Remaining Limits

- Windows and macOS execution, native dialogs, file attributes/ACLs and packaged
  releases are unverified. No `.exe`, ELF or PyInstaller success is claimed.
- Zenity/KDialog selection/cancel/error paths were simulated; real GNOME/KDE/portal
  integration was not available. The confirmed application fallback bug explains
  sequential dialogs. It does not prove every user's simultaneous-window report
  has the same cause. If duplicates persist, record the selected backend and
  desktop/portal versions; do not add fallback retries after cancellation.
- Atomic replacement preserves destination content on ordinary failure and mode
  bits on success, not necessarily ownership, ACLs, xattrs or filesystem-specific
  metadata. Concurrent external changes to the destination are not locked.
- Files are fsynced, but the parent directory is not. Forced process termination,
  power loss or a cleanup failure can leave a staging file. Cleanup errors are
  reported; there is no claim of crash-proof saving or secure erasure.
- The original application did not provide a token-file import picker, cancel
  button for running image work, cover/stego side-by-side previews, or additional
  channel histograms. None was removed; these were not invented as new workflows.
  Running jobs are serialized and normal close/navigation waits for completion.
- Theme selection is session-only. Native OS picker colors follow the operating
  system, not the application theme. Existing plots retain their own readable
  fixed color scale. Cyber retains its original status color (about 4.30:1 contrast);
  tested main text/color pairs meet 4.5:1. Some original decorative glyphs depend
  on installed fonts.
- No original logo asset was supplied. Fallback is expected until one is supplied.
- Existing token parsing permissiveness, GUI whitespace trimming and empty-message
  rejection were retained to avoid changing token/message behavior without approval.
- Existing malformed image decoder errors can surface as OpenCV errors rather than
  `StegoError`; the GUI still catches and displays them. No hostile-image sandbox
  or broader security audit was added.
- No novice-user study, high-DPI/multiple-monitor testing, or performance claim
  beyond the recorded host measurements is made.

## Manual QA: Linux And Windows

Run this checklist on each target OS before distributing a binary:

1. Launch the authoritative GUI with normal fonts/display scaling; resize and verify
   scrolling on heatmap/analysis, navigation, status and help close/reopen.
2. Open/cancel each picker: cover, encoded image, encoded output, key TXT and heatmap.
   Rapidly click again. Expect one picker and unchanged state after cancellation.
3. On Linux, repeat with the installed Zenity/KDialog backend and the Tk fallback;
   note desktop/portal versions. On Windows verify native parenting/focus and Unicode.
4. Encode short/long Unicode and emoji text, then save/reopen/decode PNG and BMP.
   Verify capacity rejection, exact dimensions, correct token and lossless output.
5. Try wrong/invalid tokens, missing files and corrupted images. Old plaintext must
   disappear; controls/navigation must recover after failure.
6. Try JPEG with its warning, mismatched extensions, an unwritable destination, a
   read-only existing file and overwrites. Failed saves must leave original files
   intact and must never display verified success.
7. Compare simple/advanced heatmaps: all/header/message scopes, opacity/cell size,
   clipped edges and saved preview. Confirm labels match the displayed snapshot.
8. Check analysis against the final saved file, including identical-image wording.
   Inspect amplified differences and blue histogram in advanced view.
9. Switch Cyber/Dark/Light on every screen and with help open, selected text, a token
   and a completed result. Nothing except styling should change. Check hover, focus,
   disabled states, warnings/errors and native OS-dialog appearance.
10. Exercise a large image: repeat Encode/Decode/Apply, attempt Back/Close during work,
    then complete/fail the job. Expect one worker, responsive redraw and clean state.
    Build and test any future binary on its own target OS separately.
