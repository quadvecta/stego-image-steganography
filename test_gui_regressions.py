"""Real-Tk regressions for the authoritative STEGO GUI (no new dependencies).

Run: python -m unittest discover -s outputs -p test_gui_regressions.py -v
On the supplied runner: source /tmp/bcode/tk-env.sh && /tmp/bcode/venv/bin/python \
    -m unittest discover -s outputs -p test_gui_regressions.py -v
Tk tests explicitly skip when a display cannot be opened. Native pickers and
notifications are mocked; application widgets, themes, queues and workers are real.
"""

import base64
from contextlib import ExitStack
import io
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import cv2
import numpy as np

import stego_dialogs as dialogs
import stego_engine as se
import stego_test_file2_newuserguide as g


class _TkThreadGuard:
    """Delegate to real Tcl/Tk, but fail before an off-thread call can hang."""

    def __init__(self, interpreter):
        self.interpreter = interpreter
        self.owner = threading.get_ident()
        self.violations = []

    def __getattr__(self, name):
        value = getattr(self.interpreter, name)
        if not callable(value):
            return value

        def checked(*args, **kwargs):
            if threading.get_ident() != self.owner:
                self.violations.append((name, threading.get_ident()))
                raise AssertionError("Worker accessed Tcl/Tk: " + name)
            return value(*args, **kwargs)

        return checked


def _widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _widgets(child)


class GuiRegressions(unittest.TestCase):
    message = "GUI regression: h\u00e9llo \u65e5\u672c\u8a9e \U0001f642"

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable (run under Xvfb): {exc}")
        self.stack = ExitStack()
        self.workers, self.gates, self.callback_errors = [], [], []
        self.addCleanup(self._cleanup)
        self.guard = _TkThreadGuard(self.root.tk)
        self.root.tk = self.guard
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.root.geometry("1000x1000+0+0")
        # The GUI owns module-level palette state; isolate it from other suites.
        self.stack.enter_context(mock.patch.dict(
            g.__dict__, dict(g.THEMES["Cyber"], _themes=None)))
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.errors = self.patch(g.messagebox, "showerror", return_value=None)
        self.warnings = self.patch(g.messagebox, "showwarning", return_value=None)
        self.patch(g.messagebox, "showinfo", return_value=None)
        self.confirm = self.patch(g.messagebox, "askyesno", return_value=False)
        for name in ("askokcancel", "askretrycancel", "askyesnocancel", "askquestion"):
            self.patch(g.messagebox, name, return_value=False)
        self.notify = self.patch(g, "_native_notify", return_value=None)
        self.open_picker = self.patch(g, "_native_open", return_value="")
        self.image_picker = self.patch(g, "_native_save_img", return_value="")
        self.text_picker = self.patch(g, "_native_save_txt", return_value="")
        self.patch(dialogs.filedialog, "askopenfilename", return_value="")
        self.patch(dialogs.filedialog, "asksaveasfilename", return_value="")
        real_thread = threading.Thread

        def track_thread(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            self.workers.append(worker)
            return worker

        self.patch(g.threading, "Thread", side_effect=track_thread)
        self.app = g.StegoApp(self.root)
        self.root.update()

    def patch(self, obj, name, **kwargs):
        return self.stack.enter_context(mock.patch.object(obj, name, **kwargs))

    def _cleanup(self):
        try:
            for gate in self.gates:
                gate.set()
            for worker in self.workers:
                if worker.ident is not None:
                    worker.join(5)
            try:
                for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                    self.root.after_cancel(callback)
                if self.root.winfo_exists():
                    self.root.destroy()
            except tk.TclError:
                pass
            self.assertFalse([w.name for w in self.workers if w.is_alive()], "Leaked worker")
            self.assertEqual(self.guard.violations, [], "Tk must only run on the main thread")
            self.assertEqual(self.callback_errors, [], "Unhandled Tk callback exception")
        finally:
            self.stack.close()

    def pump(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.005)
        self.fail(f"GUI timed out; busy={self.app._busy}; errors={self.errors.call_args_list}")

    def idle(self):
        self.pump(lambda: not self.app._busy and self.app._q.empty())

    def gate(self):
        event = threading.Event()
        self.gates.append(event)
        return event

    def result(self, save=True):
        cover = np.random.default_rng(730).integers(0, 256, (61, 83, 3), dtype=np.uint8)
        result = se.stego_encode(cover, self.message)
        if save:
            se.save_and_verify(result, str(self.tmp / "encoded.png"))
            self.app._after_save(result)
        else:
            self.app._result = result
        return result

    def picker_cases(self):
        self.app._set_key_text("test-token")
        self.app._hm_img = np.arange(13 * 17 * 3, dtype=np.uint8).reshape(13, 17, 3)
        return (("_browse_source", self.open_picker, "source.png", self.app.src_path),
                ("_browse_encoded", self.open_picker, "encoded.png", self.app.encrypted_path),
                ("_save_key_txt", self.text_picker, "key.txt", None),
                ("_save_heatmap", self.image_picker, "heatmap.png", None))

    def test_picker_selection_parent_and_real_file_writes(self):
        for method, picker, filename, variable in self.picker_cases():
            with self.subTest(action=method):
                path = str(self.tmp / ("\u00fc selected " + filename))
                picker.reset_mock()
                picker.return_value = path
                getattr(self.app, method)()
                self.idle()
                picker.assert_called_once()
                self.assertIs(picker.call_args.kwargs["parent"], self.root)
                self.assertFalse(self.app._dialog_open)
                if variable is not None:
                    self.assertEqual(variable.get(), path)
                elif method == "_save_key_txt":
                    self.assertEqual(Path(path).read_text(encoding="utf-8"), "test-token")
                    self.assertTrue(self.app._token_secured)
                else:
                    np.testing.assert_array_equal(se.read_image(path), self.app._hm_img)
        self.errors.assert_not_called()

    def test_picker_cancellation_preserves_paths_token_and_preview_without_jobs(self):
        self.app.src_path.set("original source")
        self.app.encrypted_path.set("original encoded")
        for method, picker, _, variable in self.picker_cases():
            with self.subTest(action=method), mock.patch.object(self.app, "_run_bg") as run:
                previous = variable.get() if variable is not None else None
                image = self.app._hm_img
                self.app._token_secured = False
                picker.reset_mock()
                picker.return_value = ""
                getattr(self.app, method)()
                picker.assert_called_once()
                run.assert_not_called()
                self.assertFalse(self.app._dialog_open)
                self.assertFalse(self.app._token_secured)
                self.assertEqual(self.app._last_key_token, "test-token")
                self.assertIs(self.app._hm_img, image)
                if variable is not None:
                    self.assertEqual(variable.get(), previous)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.errors.assert_not_called()
        self.notify.assert_not_called()

    def test_picker_exceptions_release_guard_and_allow_retry(self):
        for method, picker, filename, _ in self.picker_cases():
            with self.subTest(action=method):
                self.errors.reset_mock()
                self.app._token_secured = False
                picker.side_effect = RuntimeError("picker broke")
                with mock.patch.object(self.app, "_run_bg") as run:
                    getattr(self.app, method)()
                    run.assert_not_called()
                self.assertFalse(self.app._dialog_open)
                self.assertFalse(self.app._token_secured)
                self.errors.assert_called_once_with("File dialog", "RuntimeError: picker broke")
                picker.side_effect = None
                picker.return_value = str(self.tmp / filename)
                getattr(self.app, method)()
                self.idle()
                self.assertFalse(self.app._dialog_open)

    def test_picker_nested_reentry_blocks_all_actions_and_navigation(self):
        cases = self.picker_cases()
        for method, picker, _, _ in cases:
            with self.subTest(action=method):
                self.app._show(self.app.encode_frame)
                for p in (self.open_picker, self.image_picker, self.text_picker):
                    p.reset_mock()

                def nested(*args, **kwargs):
                    self.assertTrue(self.app._dialog_open)
                    for inner, _, _, _ in cases:
                        getattr(self.app, inner)()
                    self.app.encrypt()
                    self.app.decrypt()
                    self.app._back_from_encode()
                    self.app._show(self.app.decode_frame)
                    self.app._close()
                    self.assertTrue(self.app.encode_frame.winfo_ismapped())
                    return ""

                with mock.patch.object(self.app, "_run_bg") as run:
                    picker.side_effect = nested
                    try:
                        getattr(self.app, method)()
                    finally:
                        picker.side_effect = None
                    run.assert_not_called()
                self.assertEqual(sum(p.call_count for p in
                                     (self.open_picker, self.image_picker, self.text_picker)), 1)
                self.assertFalse(self.app._dialog_open)
        self.errors.assert_not_called()

    def test_picker_busy_local_and_global_guards(self):
        cases = self.picker_cases()
        for guard in ("busy", "local", "global"):
            with self.subTest(guard=guard), mock.patch.object(
                    g, "dialog_active", return_value=(guard == "global")):
                self.app._busy = guard == "busy"
                self.app._dialog_open = guard == "local"
                try:
                    for method, _, _, _ in cases:
                        getattr(self.app, method)()
                finally:
                    self.app._busy = self.app._dialog_open = False
        for picker in (self.open_picker, self.image_picker, self.text_picker):
            picker.assert_not_called()

    def test_real_choose_file_guard_integrates_with_browse_callback(self):
        selected = str(self.tmp / "selected.png")

        def native(title, parent=None):
            return g.choose_file("open", title, parent=parent)

        def picker(**kwargs):
            self.assertTrue(dialogs.dialog_active())
            self.assertTrue(self.app._dialog_open)
            self.assertIs(kwargs["parent"], self.root)
            self.app._browse_encoded()
            self.assertEqual(g.choose_file("open", "nested", parent=self.root), "")
            return selected

        self.open_picker.side_effect = native
        with (mock.patch.object(dialogs.shutil, "which", return_value=None),
              mock.patch.object(dialogs.filedialog, "askopenfilename", side_effect=picker) as ask):
            self.app._browse_source()
        ask.assert_called_once()
        self.assertEqual(self.app.src_path.get(), selected)
        self.assertEqual(self.app.encrypted_path.get(), "")
        self.assertFalse(dialogs.dialog_active())
        self.assertFalse(self.app._dialog_open)

    def test_key_save_write_failure_does_not_mark_token_secured(self):
        self.app._set_key_text("secret token")
        self.text_picker.return_value = str(self.tmp)  # A directory is not writable as a file.
        self.app._save_key_txt()
        self.assertFalse(self.app._token_secured)
        self.assertFalse(self.app._dialog_open)
        self.assertIn("Could not save key", self.errors.call_args.args[1])
        self.notify.assert_not_called()

    def test_key_and_heatmap_without_data_do_not_open_pickers(self):
        self.app._save_key_txt()
        self.app._save_heatmap()
        self.assertEqual(self.warnings.call_count, 2)
        self.text_picker.assert_not_called()
        self.image_picker.assert_not_called()

    def test_after_encode_passes_mode_and_exact_path_to_save_worker(self):
        result = self.result(save=False)
        for mode, extension in (("secure", ".PNG"), ("secure", ".bmp"),
                                ("standard", ".jpg"), ("standard", ".JPEG")):
            with self.subTest(mode=mode, extension=extension):
                path = str(self.tmp / ("\u65e5\u672c\u8a9e export" + extension))

                def picker(*args, **kwargs):
                    self.assertTrue(self.app._dialog_open)
                    self.app._browse_source()
                    self.app.encrypt()
                    return path

                self.image_picker.reset_mock()
                self.image_picker.side_effect = picker
                with (mock.patch.object(self.app, "_ask_export_mode", return_value=mode),
                      mock.patch.object(self.app, "_run_bg", return_value=True) as run,
                      mock.patch.object(se, "save_and_verify", return_value=result) as save):
                    self.app._after_encode(result)
                    run.assert_called_once()
                    job, on_ok, on_err, status = run.call_args.args
                    self.assertIs(job(), result)
                    save.assert_called_once_with(result, path)
                    self.assertEqual(on_ok, self.app._after_save)
                    self.assertEqual(on_err, self.app._save_failed)
                    self.assertIn("verifying", status)
                self.image_picker.assert_called_once_with(
                    "Save Encoded Image",
                    "secret_image.jpg" if mode == "standard" else "secret_image.png",
                    parent=self.root, mode=mode)
                self.assertFalse(self.app._dialog_open)
        self.open_picker.assert_not_called()
        self.errors.assert_not_called()
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_after_encode_extension_mismatch_refuses_without_rewriting(self):
        result = self.result(save=False)
        for mode, extension in (("standard", ".png"), ("secure", ".jpg"),
                                ("secure", ".txt"), ("secure", "")):
            with self.subTest(mode=mode, extension=extension):
                path = self.tmp / ("unchanged" + extension)
                path.write_bytes(b"existing destination")
                self.image_picker.return_value = str(path)
                self.errors.reset_mock()
                with (mock.patch.object(self.app, "_ask_export_mode", return_value=mode),
                      mock.patch.object(self.app, "_run_bg") as run):
                    self.app._after_encode(result)
                    run.assert_not_called()
                self.assertEqual(path.read_bytes(), b"existing destination")
                self.errors.assert_called_once()
                self.assertIn("does not match the export mode", self.errors.call_args.args[1])
                self.assertFalse(self.app._dialog_open)
                self.assertIs(self.app._result, result)

    def test_after_encode_mode_or_path_cancellation_never_starts_save(self):
        result = self.result(save=False)
        for mode in ("", "secure", "standard"):
            with self.subTest(mode=mode):
                self.image_picker.reset_mock()
                with (mock.patch.object(self.app, "_ask_export_mode", return_value=mode),
                      mock.patch.object(self.app, "_run_bg") as run):
                    self.app._after_encode(result)
                    run.assert_not_called()
                self.assertEqual(self.image_picker.call_count, int(bool(mode)))
                self.assertFalse(self.app._dialog_open)
                self.assertIn("cancelled", self.app._status_lbl.cget("text"))
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.errors.assert_not_called()

    def test_after_encode_exception_releases_guard_and_preserves_previous_output(self):
        old = self.result()
        self.app._token_secured = True
        new = se.stego_encode(old.cover, "new message")
        for stage in ("mode", "picker"):
            with self.subTest(stage=stage):
                self.errors.reset_mock()
                self.image_picker.side_effect = RuntimeError("export failed") if stage == "picker" else None
                with (mock.patch.object(self.app, "_ask_export_mode", return_value="secure",
                                        side_effect=RuntimeError("export failed") if stage == "mode" else None),
                      mock.patch.object(self.app, "_run_bg") as run):
                    self.app._after_encode(new)
                    run.assert_not_called()
                self.assertFalse(self.app._dialog_open)
                self.assertFalse(self.app._export_dlg_open)
                self.assertIs(self.app._result, old)
                self.assertEqual(self.app._last_saved_image, old.output_path)
                self.assertEqual(self.app._last_key_token, old.token)
                self.assertTrue(self.app._token_secured)
                self.errors.assert_called_once_with("Save failed", "RuntimeError: export failed")

    def test_real_export_dialog_choices_cancel_and_reentry_are_bounded(self):
        for choice in ("secure", "standard", "cancel", "window-close"):
            with self.subTest(choice=choice):
                failures, expired = [], []

                def interact():
                    win = self.app._export_win
                    if not win.winfo_viewable():
                        self.root.after(10, interact)
                        return
                    try:
                        self.assertTrue(self.app._export_dlg_open)
                        self.assertEqual(self.app._ask_export_mode(), "")
                        radios = [w for w in _widgets(win) if isinstance(w, tk.Radiobutton)]
                        self.assertEqual(win.getvar(radios[0].cget("variable")), "secure")
                        if choice in ("secure", "standard"):
                            next(w for w in radios if w.cget("value") == choice).invoke()
                        if choice == "window-close":
                            win.tk.call(win.protocol("WM_DELETE_WINDOW"))
                        else:
                            text = "CANCEL" if choice == "cancel" else "CONFIRM"
                            button = next(w for w in _widgets(win)
                                          if isinstance(w, g.GlowButton) and w._text == text)
                            button.event_generate("<Button-1>")
                    except Exception as exc:
                        failures.append(exc)
                        if win.winfo_exists():
                            win.destroy()

                def timeout():
                    expired.append(True)
                    win = getattr(self.app, "_export_win", None)
                    if win is not None and win.winfo_exists():
                        win.destroy()

                watchdog = self.root.after(3000, timeout)
                self.root.after(30, interact)
                try:
                    selected = self.app._ask_export_mode()
                finally:
                    self.root.after_cancel(watchdog)
                self.assertEqual(failures, [])
                self.assertEqual(expired, [], "Modal dialog timed out")
                self.assertEqual(selected, choice if choice in ("secure", "standard") else "")
                self.assertFalse(self.app._export_dlg_open)
                self.assertFalse(self.app._export_win.winfo_exists())
                self.assertIsNone(self.root.grab_current())

    def test_export_build_exception_destroys_partial_window_and_resets_both_guards(self):
        result = self.result(save=False)
        with (mock.patch.object(self.app.themes, "apply_current", side_effect=RuntimeError("theme")),
              mock.patch.object(self.app, "_run_bg") as run):
            self.app._after_encode(result)
            run.assert_not_called()
        self.assertFalse(self.app._export_win.winfo_exists())
        self.assertFalse(self.app._export_dlg_open)
        self.assertFalse(self.app._dialog_open)
        self.assertIsNone(self.root.grab_current())
        self.errors.assert_called_once_with("Save failed", "RuntimeError: theme")
        with mock.patch.object(self.app, "_build_export_mode", return_value="secure"):
            self.assertEqual(self.app._ask_export_mode(), "secure")

    def test_heatmap_save_png_bmp_and_invalid_extensions(self):
        self.picker_cases()
        image = self.app._hm_img
        before = image.copy()
        for extension in (".PNG", ".bmp", ".jpg", ".txt", ""):
            with self.subTest(extension=extension):
                path = self.tmp / ("heatmap" + extension)
                self.image_picker.return_value = str(path)
                self.errors.reset_mock()
                self.app._save_heatmap()
                self.idle()
                if extension.lower() in (".png", ".bmp"):
                    self.errors.assert_not_called()
                    np.testing.assert_array_equal(se.read_image(str(path)), image)
                else:
                    self.errors.assert_called_once()
                    self.assertFalse(path.exists())
                np.testing.assert_array_equal(image, before)
                self.assertIs(self.app._hm_img, image)

    def test_heatmap_save_failures_keep_destination_and_remove_owned_temporary(self):
        self.picker_cases()
        path = self.tmp / "heatmap.png"
        path.write_bytes(b"previous heatmap")
        self.image_picker.return_value = str(path)
        for stage in ("encoder", "fsync", "replace"):
            with self.subTest(stage=stage):
                self.errors.reset_mock()
                patcher = (mock.patch.object(g.cv2, "imencode", return_value=(False, None))
                           if stage == "encoder" else
                           mock.patch.object(g.os, stage, side_effect=OSError(stage)))
                with patcher:
                    self.app._save_heatmap()
                    self.idle()
                self.errors.assert_called_once()
                self.assertEqual(self.errors.call_args.args[0], "Save heatmap")
                self.assertEqual(path.read_bytes(), b"previous heatmap")
                self.assertEqual(list(self.tmp.iterdir()), [path])
                self.assertFalse(self.app._dialog_open)

    def test_logo_embedded_png_uses_real_photoimage_and_current_background(self):
        try:
            from PIL import ImageTk
        except ImportError as exc:
            self.skipTest(f"Optional Pillow logo support unavailable: {exc}")
        self.assertIs(g.base64, base64)
        self.assertIs(g.io, io)
        pixels = np.full((3, 4, 4), (20, 40, 80, 128), dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", pixels)
        self.assertTrue(ok)
        asset = base64.b64encode(encoded.tobytes()).decode("ascii")
        with mock.patch.object(g.StegoApp, "_LOGO_B64", asset, create=True):
            for theme in ("Cyber", "Light", "Dark"):
                with self.subTest(theme=theme):
                    self.app._set_theme(theme)
                    canvas = self.app._logo_canvas
                    self.assertEqual([canvas.type(i) for i in canvas.find_all()], ["image"])
                    self.assertEqual(canvas.itemcget(canvas.find_all()[0], "image"),
                                     str(self.app._logo_img))
                    image = ImageTk.getimage(self.app._logo_img)
                    self.assertEqual(image.size, (120, 120))
                    background = g.THEMES[theme]["SURFACE"]
                    rgb = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
                    expected = [(front * 128 + back * 127 + 127) // 255
                                for front, back in zip((80, 40, 20), rgb)]
                    np.testing.assert_allclose(image.getpixel((60, 60))[:3], expected, atol=1)
        self.errors.assert_not_called()

    def test_logo_missing_and_corrupt_assets_draw_themed_fallback(self):
        for asset in (None, "!not-base64!", base64.b64encode(b"not a PNG").decode()):
            with self.subTest(asset=asset):
                replacement = (mock.PropertyMock(side_effect=AttributeError("missing asset"))
                               if asset is None else asset)
                with mock.patch.object(g.StegoApp, "_LOGO_B64", replacement, create=True):
                    self.app._set_theme("Light")
                canvas = self.app._logo_canvas
                items = canvas.find_all()
                self.assertEqual([canvas.type(i) for i in items], ["oval", "text"])
                self.assertEqual(canvas.itemcget(items[1], "text"), "STEGO")
                self.assertEqual(canvas.itemcget(items[1], "fill"), g.THEMES["Light"]["ACCENT"])
                self.assertEqual(canvas.itemcget(items[0], "outline"), g.THEMES["Light"]["ACCENT"])
        self.errors.assert_not_called()

    def test_simple_heatmap_uses_all_positions_default_render_and_honest_summary(self):
        r = self.result()
        self.app._hm_scope.set("header")
        self.app._hm_mode.set("density")
        self.app._hm_bin.set(13)
        self.app._hm_alpha.set(25)
        with (mock.patch.object(se, "position_density", wraps=se.position_density) as density,
              mock.patch.object(se, "render_heatmap", wraps=se.render_heatmap) as render,
              mock.patch.object(self.app, "_after_heatmap", wraps=self.app._after_heatmap) as after):
            self.app._show_heatmap()
            self.idle()
        self.errors.assert_not_called()
        density.assert_called_once()
        np.testing.assert_array_equal(density.call_args.args[1], r.positions)
        self.assertEqual(density.call_args.args[2], se.auto_bin(r.shape))
        self.assertEqual(render.call_args.kwargs, dict(overlay=True, alpha=0.6, max_dim=560))
        out = after.call_args.args[0]
        self.assertEqual(out[4:7], (len(r.positions), "all", "overlay"))
        info = self.app._hm_info.cget("text")
        self.assertIn(f"{len(r.positions):,} of {r.n_pixels_total:,}", info)
        self.assertIn(f"({len(r.positions) / r.n_pixels_total * 100:.2f}%)", info)
        self.assertIn(f"Message area: {len(r.payload_positions):,} pixels; header: {r.n_header_pixels:,}", info)
        self.assertIn(f"{out[7]:,} of {out[8]:,} cells", info)
        self.assertIn(f"{out[3]} x {out[3]}", info)
        self.assertIn("not a steganalysis detector", " ".join(
            str(w.cget("text")) for w in _widgets(self.app.heatmap_frame) if isinstance(w, tk.Label)))
        metrics = se.compare(r.cover, r.img_out)
        self.assertIn(f"at most {metrics['max_abs_diff']}/255", info)
        self.assertIn(f"{metrics['changed_fraction'] * 100:.2f}% of pixels changed", info)
        self.assertEqual(self.app._hm_img.shape[:2], r.shape[:2])
        self.assertEqual((self.app._photos["hm"].width(), self.app._photos["hm"].height()), (83, 61))
        self.assertIn("83 \u00d7 61", self.app._out["dims"].cget("text"))
        self.assertIn(f"{r.n_header_pixels} header + {len(r.payload_positions):,} payload",
                      self.app._out["used"].cget("text"))
        self.assertIn(f"{len(self.message.encode('utf-8')):,} B text", self.app._out["payload"].cget("text"))
        self.assertIn(f"{r.ciphertext_bytes:,} B Fernet ciphertext + {se.HEADER_BYTES} B header",
                      self.app._out["payload"].cget("text"))
        self.assertFalse(self.app._hm_advanced)
        self.assertFalse(self.app._hm_controls.winfo_ismapped())
        self.assertEqual(self.app._hm_scope.get(), "header")

    def test_advanced_heatmap_worker_uses_captured_scope_and_actual_position_subsets(self):
        r = self.result()
        self.app._toggle_heatmap()
        self.idle()
        real_density = se.position_density
        for scope, positions in (("all", r.positions), ("header", r.header_positions),
                                 ("payload", r.payload_positions)):
            with self.subTest(scope=scope):
                entered, release = threading.Event(), self.gate()
                seen = []

                def density(shape, selected, bin_px):
                    seen.append((threading.get_ident(), selected.copy(), bin_px))
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("heatmap worker release timed out")
                    return real_density(shape, selected, bin_px)

                self.app._hm_scope.set(scope)
                self.app._hm_mode.set("density")
                self.app._hm_bin.set(7)
                self.app._hm_alpha.set(35)
                with (mock.patch.object(se, "position_density", side_effect=density),
                      mock.patch.object(self.app, "_after_heatmap", wraps=self.app._after_heatmap) as after):
                    self.app._render_heatmap()
                    self.assertTrue(entered.wait(3))
                    self.app._hm_scope.set("header" if scope != "header" else "payload")
                    self.app._hm_mode.set("overlay")
                    self.app._hm_bin.set(19)
                    self.app._hm_alpha.set(90)
                    release.set()
                    self.idle()
                self.assertNotEqual(seen[0][0], threading.get_ident())
                np.testing.assert_array_equal(seen[0][1], positions)
                self.assertEqual(seen[0][2], 7)
                out = after.call_args.args[0]
                self.assertEqual(out[3:7], (7, len(positions), scope, "density"))
                expected = np.zeros((9, 12), dtype=np.float64)
                np.add.at(expected, (positions // 83 // 7, positions % 83 // 7), 1)
                self.assertEqual(out[7:9], (int(np.count_nonzero(expected)), expected.size))
                expected_image, lo, hi = se.render_heatmap(r.cover, expected, 7, overlay=False, alpha=.35)
                np.testing.assert_array_equal(out[0], expected_image)
                self.assertEqual(out[1:3], (lo, hi))
                info = self.app._hm_info.cget("text")
                self.assertIn(f"{len(positions):,} of {r.n_pixels_total:,}", info)
                self.assertIn(f"in this {scope} view", info)
                self.assertIn("Placement density only", info)
        self.errors.assert_not_called()

    def test_simple_controls_default_hidden_and_advanced_choices_survive_toggling(self):
        a = self.app
        self.assertFalse(a._hm_advanced)
        self.assertFalse(a._an_advanced)
        self.assertEqual(a._hm_controls.winfo_manager(), "")
        self.assertEqual(a._an_controls.winfo_manager(), "")
        self.result()
        a._show_heatmap()
        self.idle()
        a._toggle_heatmap()
        self.idle()
        self.assertTrue(a._hm_controls.winfo_ismapped())
        self.assertEqual(a._hm_toggle.cget("text"), "Use simple view")
        a._hm_scope.set("payload")
        a._hm_mode.set("density")
        a._hm_bin.set(9)
        a._hm_alpha.set(45)
        for advanced in (False, True):
            a._toggle_heatmap()
            self.idle()
            self.assertEqual(bool(a._hm_controls.winfo_ismapped()), advanced)
            self.assertEqual((a._hm_scope.get(), a._hm_mode.get(), a._hm_bin.get(), a._hm_alpha.get()),
                             ("payload", "density", 9, 45))
        a._show_analysis()
        self.idle()
        detail = a._an_detail.cget("text")
        photos = dict(a._photos)
        self.assertFalse(a._an_controls.winfo_ismapped())
        for advanced in (True, False, True):
            a._toggle_analysis()
            self.root.update_idletasks()
            self.assertEqual(bool(a._an_controls.winfo_ismapped()), advanced)
            self.assertEqual(a._an_detail.cget("text"), detail)
            for key, photo in photos.items():
                self.assertIs(a._photos[key], photo)
        self.errors.assert_not_called()

    def test_heatmap_invalid_auto_and_oversized_bins(self):
        r = self.result()
        self.app._toggle_heatmap()
        self.idle()
        for value, expected in (("not an integer", se.auto_bin(r.shape)),
                                (0, se.auto_bin(r.shape)), (-5, se.auto_bin(r.shape)), (10000, 83)):
            with self.subTest(value=value), mock.patch.object(
                    self.app, "_after_heatmap", wraps=self.app._after_heatmap) as after:
                self.app._hm_bin.set(value)
                self.app._render_heatmap()
                self.idle()
                self.assertEqual(after.call_args.args[0][3], expected)
        self.errors.assert_not_called()

    def test_heatmap_missing_positions_and_worker_failure_clear_stale_preview(self):
        r = self.result()
        self.app._show_heatmap()
        self.idle()
        with mock.patch.object(se, "position_density", side_effect=MemoryError()):
            self.app._render_heatmap()
            self.idle()
        self.assertIsNone(self.app._hm_img)
        self.assertEqual(str(self.app._hm_label.cget("image")), "")
        self.assertEqual(str(self.app._hm_bar.cget("image")), "")
        self.assertEqual(self.app._hm_info.cget("text"), "")
        self.assertIn("Out of memory", self.errors.call_args.args[1])
        for positions in (None, np.array([], dtype=np.int64)):
            with self.subTest(positions=positions), mock.patch.object(self.app, "_run_bg") as run:
                r.positions = positions
                self.errors.reset_mock()
                self.app._show(self.app.output_frame)
                self.app._show_heatmap()
                run.assert_not_called()
                self.errors.assert_called_once()

    def test_analysis_reopens_actual_png_bmp_jpg_and_jpeg_not_presave_pixels(self):
        r = self.result(save=False)
        cover_before, encoded_before = r.cover.copy(), r.img_out.copy()
        r.cover.flags.writeable = r.img_out.flags.writeable = False
        for extension in (".png", ".bmp", ".jpg", ".jpeg"):
            with self.subTest(extension=extension):
                # Deliberately different saved pixels catch PNG/BMP in-memory shortcuts too.
                saved = r.img_out.copy()
                saved[:, :, 1] ^= 255
                ok, encoded = cv2.imencode(extension, saved)
                self.assertTrue(ok)
                path = self.tmp / ("actual saved" + extension)
                path.write_bytes(encoded.tobytes())
                r.output_path, r.output_format = str(path), extension[1:].upper()
                reopened = se.read_image(str(path))
                expected = se.compare(r.cover, reopened)
                self.assertNotEqual(expected, se.compare(r.cover, r.img_out))
                with (mock.patch.object(se, "read_image", wraps=se.read_image) as read,
                      mock.patch.object(se, "compare", wraps=se.compare) as compare,
                      mock.patch.object(se, "diff_image", wraps=se.diff_image) as diff,
                      mock.patch.object(se, "histogram_plot", wraps=se.histogram_plot) as hist,
                      mock.patch.object(self.app, "_after_analysis", wraps=self.app._after_analysis) as after):
                    self.app._show_analysis()
                    self.idle()
                self.errors.assert_not_called()
                read.assert_called_once_with(str(path))
                for operation in (compare, diff, hist):
                    operation.assert_called_once()
                    self.assertIs(operation.call_args.args[0], r.cover)
                    np.testing.assert_array_equal(operation.call_args.args[1], reopened)
                metrics, diff_pixels, hist_pixels = after.call_args.args[0]
                self.assertEqual(metrics, expected)
                np.testing.assert_array_equal(diff_pixels, se.diff_image(r.cover, reopened, 300))
                np.testing.assert_array_equal(hist_pixels, se.histogram_plot(r.cover, reopened, (480, 130)))
                text = self.app._an_metrics.cget("text")
                self.assertIn(f"saved/reopened {r.output_format}", text)
                self.assertIn(str(path), text)
                self.assertIn(f"Changed pixels: {expected['changed_pixels']:,}", text)
                self.assertIn(f"at most {expected['max_abs_diff']}/255", text)
                self.assertIn(f"{expected['mae']:.5f}/255", text)
                self.assertIn(f"{expected['psnr']:.2f} dB", text)
                self.assertIn("83 x 61 pixels", self.app._an_detail.cget("text"))
                np.testing.assert_array_equal(r.cover, cover_before)
                np.testing.assert_array_equal(r.img_out, encoded_before)

    def test_analysis_mocked_reopen_is_used_by_every_measurement(self):
        r = self.result()
        reopened = r.cover.copy()
        reopened[:, :, 2] ^= 127
        before = reopened.copy()
        reopened.flags.writeable = False
        expected = se.compare(r.cover, reopened)
        with (mock.patch.object(se, "read_image", return_value=reopened) as read,
              mock.patch.object(self.app, "_after_analysis", wraps=self.app._after_analysis) as after):
            self.app._show_analysis()
            self.idle()
        read.assert_called_once_with(r.output_path)
        metrics, diff, hist = after.call_args.args[0]
        self.assertEqual(metrics, expected)
        np.testing.assert_array_equal(diff, se.diff_image(r.cover, reopened, 300))
        np.testing.assert_array_equal(hist, se.histogram_plot(r.cover, reopened, (480, 130)))
        np.testing.assert_array_equal(reopened, before)
        self.errors.assert_not_called()

    def test_analysis_identical_saved_image_explains_infinite_psnr_in_simple_view(self):
        r = self.result()
        ok, encoded = cv2.imencode(".png", r.cover)
        self.assertTrue(ok)
        Path(r.output_path).write_bytes(encoded.tobytes())
        self.app._show_analysis()
        self.idle()
        text = self.app._an_metrics.cget("text")
        self.assertIn("Image similarity (PSNR): No pixel differences", text)
        self.assertIn("Changed pixels: 0", text)
        self.assertNotRegex(text.lower(), r"\binf(?:inity)?\b|\bnan\b")
        self.assertFalse(self.app._an_controls.winfo_ismapped())
        self.assertIn("infinite when MSE=0", self.app._an_detail.cget("text"))
        self.errors.assert_not_called()

    def test_analysis_failure_removes_stale_measurements_and_returns_to_output(self):
        r = self.result()
        for failure in ("missing path", "deleted file", "corrupt file", "different dimensions"):
            with self.subTest(failure=failure):
                se.save_and_verify(r, str(self.tmp / "encoded.png"))
                self.app._show_analysis()
                self.idle()
                self.assertTrue(self.app._an_diff.cget("image"))
                if failure == "missing path":
                    r.output_path = ""
                elif failure == "deleted file":
                    Path(r.output_path).unlink()
                elif failure == "corrupt file":
                    Path(r.output_path).write_bytes(b"not an image")
                else:
                    ok, encoded = cv2.imencode(".png", r.cover[:-1])
                    self.assertTrue(ok)
                    Path(r.output_path).write_bytes(encoded.tobytes())
                self.errors.reset_mock()
                self.app._show_analysis()
                self.idle()
                self.errors.assert_called_once()
                self.assertEqual(self.errors.call_args.args[0], "Analysis")
                self.assertEqual(self.app._an_detail.cget("text"), "")
                self.assertEqual(str(self.app._an_diff.cget("image")), "")
                self.assertEqual(str(self.app._an_hist.cget("image")), "")
                self.assertIn("could not be analysed", self.app._an_metrics.cget("text"))
                self.assertTrue(self.app.output_frame.winfo_ismapped())

    def test_analysis_without_cover_does_not_start_worker(self):
        for result in (None, self.result(save=False)):
            with self.subTest(result=result is None), mock.patch.object(self.app, "_run_bg") as run:
                self.app._result = result
                if result is not None:
                    result.cover = None
                self.errors.reset_mock()
                self.app._show_analysis()
                run.assert_not_called()
                self.errors.assert_called_once_with("Analysis", "The original cover image is not available.")

    def test_full_live_theme_switch_preserves_widgets_form_secrets_results_and_arrays(self):
        a, r = self.app, self.result()
        a._show_heatmap()
        self.idle()
        a._show_analysis()
        self.idle()
        a._show_help()
        a.src_path.set("cover form.png")
        a.encrypted_path.set(r.output_path)
        a.decrypt_key.set(r.token)
        a.secret_msg.configure(undo=True, exportselection=False)
        a.secret_msg.insert("1.0", "draft secret\nsecond line")
        a.secret_msg.edit_separator()
        a.secret_msg.mark_set("insert", "2.3")
        a.secret_msg.tag_add("sel", "1.1", "1.6")
        a._set_result_text(self.message)
        a._token_secured = True
        a._hm_scope.set("payload")
        a._hm_bin.set(7)
        a._hm_alpha.set(35)
        a._counts = {"all": np.arange(12).reshape(3, 4)}
        a._show(a.encode_frame)
        entry = next(w for w in _widgets(a.encode_frame) if isinstance(w, g.CyberEntry)).get_entry()
        entry.focus_force()
        self.root.update()
        original_widgets = tuple(_widgets(self.root))
        photos = dict(a._photos)
        photo_pixels = {key: photo.tk.call(str(photo), "data", "-format", "png")
                        for key, photo in photos.items()}
        arrays = (r.cover, r.img_out, r.positions, a._hm_img, a._counts["all"])
        snapshots = [array.copy() for array in arrays]
        for array in arrays:
            array.flags.writeable = False

        def state():
            return (a.src_path.get(), a.encrypted_path.get(), a.decrypt_key.get(),
                    a.secret_msg.get("1.0", "end-1c"), a.secret_msg.index("insert"),
                    a.secret_msg.tag_ranges("sel"), a.key_text.get("1.0", "end-1c"),
                    a.result_text.get("1.0", "end-1c"), a.key_text.cget("state"),
                    a.result_text.cget("state"), a._last_saved_image, a._last_key_token,
                    a._token_secured, a._hm_scope.get(), a._hm_bin.get(), a._hm_alpha.get(),
                    a._hm_advanced, a._an_advanced, self.root.focus_get(), self.root.geometry(),
                    tuple((str(w), w.winfo_manager(), w.bind()) for w in original_widgets),
                    tuple(a._out[key].cget("text") for key in a._out), a._hm_info.cget("text"),
                    a._an_metrics.cget("text"), a._an_detail.cget("text"))

        initial = state()
        manager = a.themes
        for name in ("Light", "Dark", "Cyber", "Light"):
            with self.subTest(theme=name):
                a._set_theme(name)
                self.root.update_idletasks()
                self.assertIs(a.themes, manager)
                self.assertIs(g._themes, manager)
                self.assertEqual(a.theme_name.get(), name)
                self.assertEqual(tuple(_widgets(self.root)), original_widgets)
                self.assertEqual(state(), initial)
                self.assertIs(a._result, r)
                self.assertEqual(self.root.cget("bg"), g.THEMES[name]["BG"])
                self.assertEqual(a._help_win.cget("bg"), g.THEMES[name]["BG"])
                self.assertEqual(a.key_text.cget("bg"), g.THEMES[name]["KEY_BG"])
                self.assertEqual(a.result_text.cget("fg"), g.THEMES[name]["RESULT_FG"])
                for key, photo in photos.items():
                    self.assertIs(a._photos[key], photo)
                    self.assertEqual(photo.tk.call(str(photo), "data", "-format", "png"), photo_pixels[key])
                current = (r.cover, r.img_out, r.positions, a._hm_img, a._counts["all"])
                for old, array, before in zip(arrays, current, snapshots):
                    self.assertIs(array, old)
                    np.testing.assert_array_equal(array, before)
                    self.assertFalse(array.flags.writeable)
        self.assertEqual(se.stego_decode(r.img_out, r.token), self.message)
        self.errors.assert_not_called()

    def test_live_entry_focus_and_button_hover_use_current_theme_colors(self):
        a = self.app
        for name in ("Light", "Dark", "Cyber"):
            with self.subTest(theme=name):
                a._set_theme(name)
                p = g.THEMES[name]
                for frame, accent in ((a.encode_frame, "ACCENT"), (a.decode_frame, "ACCENT3")):
                    a._show(frame)
                    self.root.update()
                    for border in (w for w in _widgets(frame) if isinstance(w, g.CyberEntry)):
                        entry = border.get_entry()
                        entry.event_generate("<FocusIn>")
                        self.assertEqual(border.cget("bg"), p[accent])
                        self.assertEqual(entry.cget("insertbackground"), p[accent])
                        self.assertEqual(entry.cget("selectforeground"), p["BTN_FG"])
                        entry.event_generate("<FocusOut>")
                        self.assertEqual(border.cget("bg"), p["ENTRY_BD"])
                    browse = next(w for w in _widgets(frame)
                                  if isinstance(w, tk.Button) and "BROWSE" in w.cget("text"))
                    browse.event_generate("<Enter>")
                    self.assertEqual(browse.cget("bg"), p["PANEL_BD"])
                    browse.event_generate("<Leave>")
                    self.assertEqual(browse.cget("bg"), p["SURFACE2"])
                    glow = next(w for w in _widgets(frame) if isinstance(w, g.GlowButton))
                    for event, role in (("<Enter>", "BTN_FG"), ("<Leave>", accent)):
                        glow.event_generate(event)
                        fills = [glow.itemcget(i, "fill") for i in glow.find_all() if glow.type(i) == "text"]
                        self.assertEqual(fills, [p[role]])

    def test_help_singleton_close_reopen_and_live_theme_without_new_manager(self):
        a = self.app
        manager = a.themes
        for theme in ("Light", "Dark", "Cyber"):
            a._set_theme(theme)
            a._show_help()
            self.root.update()
            win = a._help_win
            a._show_help()
            self.assertIs(a._help_win, win)
            self.assertEqual(len([w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]), 1)
            self.assertEqual(win.cget("bg"), g.THEMES[theme]["BG"])
            self.assertIsNone(self.root.grab_current())
            self.assertIs(a.themes, manager)
            win.event_generate("<Escape>")
            self.root.update()
            self.assertFalse(win.winfo_exists())
            a._show_help()
            self.assertIsNot(a._help_win, win)
            self.assertEqual(a._help_win.cget("bg"), g.THEMES[theme]["BG"])
            a._help_win.destroy()

    def test_live_theme_change_preserves_active_focus_and_hover_colors(self):
        a = self.app
        a._show(a.decode_frame)
        self.root.update()
        border = next(w for w in _widgets(a.decode_frame) if isinstance(w, g.CyberEntry))
        entry = border.get_entry()
        glow = next(w for w in _widgets(a.decode_frame) if isinstance(w, g.GlowButton))
        small = next(w for w in _widgets(a.decode_frame) if isinstance(w, g.SmallBtn))
        entry.focus_force()
        self.root.update()
        glow.event_generate("<Enter>")
        small.event_generate("<Enter>")
        for name in ("Light", "Dark", "Cyber"):
            with self.subTest(theme=name):
                a._set_theme(name)
                self.assertIs(self.root.focus_get(), entry)
                self.assertEqual(border.cget("bg"), g.THEMES[name]["ACCENT3"])
                self.assertEqual(small.cget("bg"), g.THEMES[name]["PANEL_BD"])
                fills = [glow.itemcget(i, "fill") for i in glow.find_all() if glow.type(i) == "text"]
                self.assertEqual(fills, [g.THEMES[name]["BTN_FG"]])
        glow.event_generate("<Leave>")
        small.event_generate("<Leave>")
        entry.event_generate("<FocusOut>")
        self.assertEqual(small.cget("bg"), g.THEMES["Cyber"]["SURFACE2"])
        self.assertEqual(border.cget("bg"), g.THEMES["Cyber"]["ENTRY_BD"])

    def test_worker_success_and_failure_only_deliver_callbacks_through_main_thread_queue(self):
        a = self.app
        owner = threading.get_ident()
        for failure in (False, True):
            with self.subTest(failure=failure):
                entered, release = threading.Event(), self.gate()
                calls, puts, executions = [], [], []
                payload = se.StegoError("worker failed") if failure else object()
                real_put = a._q.put

                def put(item):
                    puts.append((threading.get_ident(), item))
                    return real_put(item)

                def job():
                    executions.append(threading.get_ident())
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("worker release timed out")
                    if failure:
                        raise payload
                    return payload

                def ok(value):
                    calls.append(("ok", threading.get_ident(), value, a._busy))
                    a._set_status("success")

                def error(value):
                    calls.append(("err", threading.get_ident(), value, a._busy))
                    a._set_status("failure")

                with mock.patch.object(a._q, "put", side_effect=put):
                    self.assertTrue(a._run_bg(job, ok, error, "working"))
                    self.assertTrue(entered.wait(3))
                    self.assertTrue(a._busy)
                    self.assertFalse(a._run_bg(lambda: self.fail("duplicate job"), ok, error, "duplicate"))
                    release.set()
                    self.workers[-1].join(3)
                    self.assertFalse(self.workers[-1].is_alive())
                    self.assertEqual(calls, [], "Callback ran before Tk consumed the queue")
                    self.assertTrue(a._busy)
                    self.assertEqual(a._q.qsize(), 1)
                    self.idle()
                self.assertEqual(calls, [("err" if failure else "ok", owner, payload, False)])
                self.assertEqual(len(executions), 1)
                self.assertNotEqual(executions[0], owner)
                self.assertEqual(puts[0][0], executions[0])
                self.assertIs(puts[0][1][2], payload)
                self.assertEqual(self.guard.violations, [])

    def test_callback_exception_reports_error_without_killing_queue_polling(self):
        def broken(_):
            raise RuntimeError("callback exploded")

        self.app._run_bg(lambda: 1, broken, self.fail, "first")
        self.idle()
        self.errors.assert_called_once_with("Internal error", "RuntimeError: callback exploded")
        received = []
        self.assertTrue(self.app._run_bg(lambda: 2, received.append, self.fail, "second"))
        self.idle()
        self.assertEqual(received, [2])
        self.assertIn(self.app._poll_id, self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def test_real_encode_and_decode_capture_form_values_and_reject_repeated_clicks(self):
        a = self.app
        cover = np.random.default_rng(31).integers(0, 256, (61, 83, 3), dtype=np.uint8)
        source, destination = self.tmp / "source.png", self.tmp / "result.png"
        ok, encoded = cv2.imencode(".png", cover)
        self.assertTrue(ok)
        source.write_bytes(encoded.tobytes())
        a.src_path.set(str(source))
        a.secret_msg.insert("1.0", self.message)
        self.image_picker.return_value = str(destination)
        real_read = se.read_image
        entered, release = threading.Event(), self.gate()

        def read(path):
            if path == str(source):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("encode worker timed out")
            return real_read(path)

        with (mock.patch.object(a, "_ask_export_mode", return_value="secure"),
              mock.patch.object(se, "read_image", side_effect=read),
              mock.patch.object(se, "stego_encode", wraps=se.stego_encode) as encode,
              mock.patch.object(se, "save_and_verify", wraps=se.save_and_verify) as save):
            a.encrypt()
            self.assertTrue(entered.wait(3))
            a.encrypt()
            a.src_path.set("changed while encoding.png")
            a.secret_msg.delete("1.0", "end")
            a.secret_msg.insert("1.0", "changed while encoding")
            release.set()
            self.pump(lambda: a._result is not None and not a._busy)
            encode.assert_called_once()
            save.assert_called_once()
        r = a._result
        self.assertTrue(r.verified)
        self.assertEqual(r.output_path, str(destination))
        self.assertEqual(se.stego_decode(se.read_image(str(destination)), r.token), self.message)
        np.testing.assert_array_equal(r.cover, cover)
        self.assertTrue(a.output_frame.winfo_ismapped())
        a._show(a.decode_frame)
        a.encrypted_path.set(str(destination))
        a.decrypt_key.set(r.token)
        entered, release = threading.Event(), self.gate()

        def decode_read(path):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("decode worker timed out")
            return real_read(path)

        with (mock.patch.object(se, "read_image", side_effect=decode_read) as read,
              mock.patch.object(se, "stego_decode", wraps=se.stego_decode) as decode):
            a.decrypt()
            self.assertTrue(entered.wait(3))
            a.decrypt()
            a.encrypted_path.set("changed while decoding.png")
            a.decrypt_key.set("changed token")
            release.set()
            self.idle()
            read.assert_called_once_with(str(destination))
            decode.assert_called_once()
            self.assertEqual(decode.call_args.args[1], r.token)
        self.assertEqual(a.result_text.get("1.0", "end-1c"), self.message)
        self.assertEqual(a.result_text.cget("state"), "disabled")
        self.errors.assert_not_called()

    def test_decode_missing_path_or_token_clears_plaintext_before_validation(self):
        for path, token, expected in (("", "token", "select an encoded image"),
                                      ("image.png", "", "paste the decryption key token"),
                                      ("  ", "  ", "select an encoded image")):
            with self.subTest(path=path, token=token), mock.patch.object(self.app, "_run_bg") as run:
                self.app._set_result_text("previous secret plaintext")
                self.app.encrypted_path.set(path)
                self.app.decrypt_key.set(token)
                self.errors.reset_mock()
                self.app.decrypt()
                self.assertEqual(self.app.result_text.get("1.0", "end-1c"), "")
                self.assertEqual(self.app.result_text.cget("state"), "disabled")
                run.assert_not_called()
                self.errors.assert_called_once()
                self.assertIn(expected, self.errors.call_args.args[1])

    def test_decode_wrong_token_and_missing_file_clear_plaintext_on_worker_failure(self):
        r = self.result()
        for path, token in ((r.output_path, se.new_token()), (str(self.tmp / "missing.png"), r.token)):
            with self.subTest(path=path):
                self.app.encrypted_path.set(path)
                self.app.decrypt_key.set(token)
                self.app._set_result_text("stale secret")
                self.errors.reset_mock()
                self.app.decrypt()
                self.assertEqual(self.app.result_text.get("1.0", "end-1c"), "")
                self.idle()
                self.assertEqual(self.app.result_text.get("1.0", "end-1c"), "")
                self.assertEqual(self.app.result_text.cget("state"), "disabled")
                self.errors.assert_called_once()
                self.assertEqual(self.errors.call_args.args[0], "Decryption Failed")

    def test_encode_validation_and_worker_error_do_not_publish_output(self):
        a = self.app
        a._show(a.encode_frame)
        for path, message in (("", "message"), ("image.png", "")):
            with self.subTest(path=path), mock.patch.object(a, "_run_bg") as run:
                a.src_path.set(path)
                a.secret_msg.delete("1.0", "end")
                a.secret_msg.insert("1.0", message)
                a.encrypt()
                run.assert_not_called()
        self.errors.reset_mock()
        a.src_path.set(str(self.tmp / "missing.png"))
        a.secret_msg.insert("1.0", "secret")
        a.encrypt()
        self.idle()
        self.errors.assert_called_once()
        self.assertEqual(self.errors.call_args.args[0], "Encryption Error")
        self.assertIsNone(a._result)
        self.assertTrue(a.encode_frame.winfo_ismapped())
        self.image_picker.assert_not_called()

    def test_navigation_back_close_and_other_jobs_blocked_during_real_worker(self):
        a, r = self.app, self.result()
        a.src_path.set("keep source")
        a.encrypted_path.set("keep encoded")
        a.decrypt_key.set("keep token")
        a.secret_msg.insert("1.0", "keep draft")
        a._set_result_text("keep plaintext")
        a._show(a.encode_frame)
        entered, release = threading.Event(), self.gate()

        def job():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("navigation worker timed out")

        a._run_bg(job, lambda _: None, self.fail, "working")
        self.assertTrue(entered.wait(3))
        before = len(self.workers)
        for action in (lambda: a._show(a.decode_frame), a._back_from_encode, a._back_from_decode,
                       lambda: a._leave_output("home"), a._show_heatmap, a._show_analysis,
                       a._render_heatmap, a._toggle_heatmap, a.encrypt, a.decrypt, a._close):
            action()
        self.assertTrue(a.encode_frame.winfo_ismapped())
        self.assertEqual((a.src_path.get(), a.encrypted_path.get(), a.decrypt_key.get()),
                         ("keep source", "keep encoded", "keep token"))
        self.assertEqual(a.secret_msg.get("1.0", "end-1c"), "keep draft")
        self.assertEqual(a.result_text.get("1.0", "end-1c"), "keep plaintext")
        self.assertIs(a._result, r)
        self.assertEqual(a._last_key_token, r.token)
        self.assertFalse(a._hm_advanced)
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(len(self.workers), before)
        self.confirm.assert_not_called()
        release.set()
        self.idle()
        a._back_from_decode()
        self.assertTrue(a.home_frame.winfo_ismapped())
        self.assertEqual(a.decrypt_key.get(), "")
        self.assertEqual(a.result_text.get("1.0", "end-1c"), "")

    def test_heatmap_analysis_and_exports_do_not_mutate_readonly_engine_arrays(self):
        r = self.result()
        arrays = (r.cover, r.img_out, r.positions)
        before = [array.copy() for array in arrays]
        for array in arrays:
            array.flags.writeable = False
        self.app._show_heatmap()
        self.idle()
        self.app._toggle_heatmap()
        self.idle()
        for scope in ("header", "payload", "all"):
            self.app._hm_scope.set(scope)
            self.app._hm_mode.set("density")
            self.app._hm_bin.set(7)
            self.app._render_heatmap()
            self.idle()
        self.app._show_analysis()
        self.idle()
        heatmap = self.app._hm_img.copy()
        self.app._hm_img.flags.writeable = False
        self.image_picker.return_value = str(self.tmp / "immutable heatmap.png")
        self.app._save_heatmap()
        self.idle()
        for array, original in zip(arrays, before):
            np.testing.assert_array_equal(array, original)
            self.assertFalse(array.flags.writeable)
        np.testing.assert_array_equal(self.app._hm_img, heatmap)
        np.testing.assert_array_equal(se.read_image(r.output_path), r.img_out)
        self.assertEqual(se.stego_decode(r.img_out, r.token), self.message)
        self.errors.assert_not_called()

    def test_save_worker_failure_does_not_replace_previous_verified_result(self):
        old = self.result()
        new = se.stego_encode(old.cover, "replacement secret")
        original_bytes = Path(old.output_path).read_bytes()
        for mode, path in (("secure", self.tmp / "no-directory" / "result.png"),
                           ("standard", self.tmp / "existing.jpg")):
            with self.subTest(mode=mode):
                if mode == "standard":
                    path.write_bytes(b"existing JPEG destination")
                self.errors.reset_mock()
                self.image_picker.return_value = str(path)
                with mock.patch.object(self.app, "_ask_export_mode", return_value=mode):
                    self.app._after_encode(new)
                    self.idle()
                self.errors.assert_called_once()
                self.assertEqual(self.errors.call_args.args[0], "Save failed")
                self.assertIs(self.app._result, old)
                self.assertEqual(self.app._last_key_token, old.token)
                self.assertEqual(self.app._last_saved_image, old.output_path)
                self.assertEqual(Path(old.output_path).read_bytes(), original_bytes)
                self.assertFalse(new.verified)
                self.assertIsNone(new.output_path)
                if mode == "standard":
                    self.assertEqual(path.read_bytes(), b"existing JPEG destination")
                else:
                    self.assertFalse(path.exists())
                self.assertFalse(list(self.tmp.glob(".stego-*")))

    def test_leave_output_confirmation_retains_then_clears_sensitive_result_state(self):
        a, r = self.app, self.result()
        a._show_heatmap()
        self.idle()
        a._show_analysis()
        self.idle()
        a._show(a.output_frame)
        a.secret_msg.insert("1.0", "draft secret")
        a._leave_output("home")
        self.confirm.assert_called_once()
        self.assertIs(a._result, r)
        self.assertTrue(a.output_frame.winfo_ismapped())
        self.assertEqual(a._last_key_token, r.token)
        self.confirm.return_value = True
        a._leave_output("encode")
        self.assertTrue(a.encode_frame.winfo_ismapped())
        self.assertIsNone(a._result)
        self.assertIsNone(a._hm_img)
        self.assertEqual(a._photos, {})
        self.assertEqual(a._counts, {})
        self.assertEqual(a._last_key_token, "")
        self.assertEqual(a._last_saved_image, "")
        self.assertEqual(a.key_text.get("1.0", "end-1c"), "")
        self.assertEqual(a.key_text.cget("state"), "disabled")
        self.assertEqual(a.secret_msg.get("1.0", "end-1c"), "")
        self.assertFalse(a._token_secured)

    def test_close_waits_for_busy_or_dialog_without_prompting_or_destroying(self):
        self.app._set_key_text("unsaved token")
        for attribute in ("_busy", "_dialog_open"):
            with self.subTest(guard=attribute):
                setattr(self.app, attribute, True)
                try:
                    self.app._close()
                    self.assertTrue(self.root.winfo_exists())
                    self.assertFalse(self.app._closed)
                    self.assertIn("Please wait", self.app._status_lbl.cget("text"))
                finally:
                    setattr(self.app, attribute, False)
        self.confirm.assert_not_called()

    def test_close_unsaved_token_cancel_then_confirm_cancels_polling(self):
        a = self.app
        a._set_key_text("unsaved token")
        poll = a._poll_id
        a._close()
        self.confirm.assert_called_once()
        self.assertTrue(self.root.winfo_exists())
        self.assertFalse(a._closed)
        self.confirm.return_value = True
        a._close()
        self.assertTrue(a._closed)
        self.assertNotIn(poll, self.root.tk.splitlist(self.root.tk.call("after", "info")))
        self.assertFalse(a._run_bg(lambda: self.fail("closed worker"), self.fail, self.fail, "closed"))
        a._poll_queue()
        self.assertNotIn(poll, self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def test_copy_key_secures_token_and_close_needs_no_confirmation(self):
        self.app._set_key_text("copied token")
        self.app._copy_key()
        self.assertEqual(self.root.clipboard_get(), "copied token")
        self.assertTrue(self.app._token_secured)
        self.app._close()
        self.assertTrue(self.app._closed)
        self.confirm.assert_not_called()

    def test_child_destroy_does_not_close_application_or_cancel_poll(self):
        self.app._show_help()
        poll = self.app._poll_id
        self.app._help_win.destroy()
        self.assertFalse(self.app._closed)
        self.assertIn(poll, self.root.tk.splitlist(self.root.tk.call("after", "info")))
        received = []
        self.app._run_bg(lambda: "still alive", received.append, self.fail, "running")
        self.idle()
        self.assertEqual(received, ["still alive"])

    def test_forced_destroy_during_worker_leaves_queued_result_without_tk_callback(self):
        entered, release = threading.Event(), self.gate()
        callback = mock.Mock()

        def job():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("close worker timed out")
            return "late result"

        self.app._run_bg(job, callback, callback, "running")
        self.assertTrue(entered.wait(3))
        poll = self.app._poll_id
        self.root.destroy()
        self.assertTrue(self.app._closed)
        release.set()
        self.workers[-1].join(3)
        self.assertFalse(self.workers[-1].is_alive())
        self.assertEqual(self.app._q.qsize(), 1)
        self.app._poll_queue()
        callback.assert_not_called()
        self.assertNotIn(poll, self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def test_close_inside_worker_callback_does_not_reschedule_polling(self):
        self.app._run_bg(lambda: None, lambda _: self.app._close(), self.fail, "running")
        self.pump(lambda: self.app._closed)
        pending = self.root.tk.splitlist(self.root.tk.call("after", "info"))
        self.assertNotIn(self.app._poll_id, pending, "Queue polling was rescheduled after root destruction")


if __name__ == "__main__":
    unittest.main(verbosity=2)
