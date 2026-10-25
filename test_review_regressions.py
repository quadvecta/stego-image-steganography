"""Regressions from the final maintenance review."""
import os
from pathlib import Path
import stat
import tempfile
import tkinter as tk
import unittest
from unittest import mock

import cv2
import numpy as np

import stego_engine as se
import stego_test_file2_newuserguide as g


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Display unavailable; use Xvfb: {exc}")
        self.addCleanup(self.root.destroy)
        patch = mock.patch.dict(g.__dict__, dict(g.THEMES["Cyber"], _themes=None))
        patch.start()
        self.addCleanup(patch.stop)
        self.app = g.StegoApp(self.root)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_key_invalid_extension_never_truncates_image(self):
        target = Path(self.tmp.name, "encoded.png")
        target.write_bytes(b"original image")
        self.app._set_key_text("test-token")
        with mock.patch.object(g, "_native_save_txt", return_value=str(target)), \
                mock.patch.object(g.messagebox, "showerror") as error:
            self.app._save_key_txt()
        self.assertEqual(target.read_bytes(), b"original image")
        self.assertFalse(self.app._token_secured)
        self.assertIn(".txt", error.call_args.args[1])

    def test_key_atomic_failure_preserves_destination_and_state(self):
        target = Path(self.tmp.name, "key.txt")
        target.write_bytes(b"previous key")
        self.app._set_key_text("test-token")
        with mock.patch.object(g, "_native_save_txt", return_value=str(target)), \
                mock.patch.object(g.os, "replace", side_effect=OSError("commit")), \
                mock.patch.object(g.messagebox, "showerror"):
            self.app._save_key_txt()
        self.assertEqual(target.read_bytes(), b"previous key")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])
        self.assertFalse(self.app._token_secured)

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits")
    def test_preview_and_key_writer_preserves_mode_and_rejects_readonly(self):
        for suffix in (".txt", ".png"):
            path = Path(self.tmp.name, "export" + suffix)
            path.write_bytes(b"old")
            path.chmod(0o640)
            g._write_export(str(path), b"new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            path.chmod(0o444)
            try:
                with self.assertRaises(se.StegoError):
                    g._write_export(str(path), b"must not replace")
                self.assertEqual(path.read_bytes(), b"new")
            finally:
                path.chmod(0o600)
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 2)

    def test_dialog_failure_is_reported_and_guard_released(self):
        with mock.patch.object(g, "choose_file", side_effect=OSError("picker failed")), \
                mock.patch.object(g.messagebox, "showerror") as error:
            self.app._browse_source()
        self.assertIn("picker failed", error.call_args.args[1])
        self.assertFalse(self.app._dialog_open)
        self.assertFalse(self.app._busy)

    def test_heatmap_toggle_during_dialog_preserves_mode_and_preview(self):
        self.app._hm_advanced = True
        self.app._hm_scope.set("header")
        self.app._dialog_open = True
        with mock.patch.object(self.app, "_render_heatmap") as render:
            self.app._toggle_heatmap()
        render.assert_not_called()
        self.assertTrue(self.app._hm_advanced)
        self.assertEqual(self.app._hm_scope.get(), "header")

    def test_chunked_headlines_match_full_metrics_without_full_compare(self):
        rng = np.random.default_rng(19)
        cover = rng.integers(0, 256, (259, 517, 3), dtype=np.uint8)
        result = se.stego_encode(cover, "headlines" * 100)
        self.app._result = result
        expected = se.compare(cover, result.img_out)
        with mock.patch.object(self.app, "_run_bg") as run:
            self.app._render_heatmap()
        job = run.call_args.args[0]
        sizes = []
        original = cv2.absdiff

        def difference(a, b):
            sizes.append(a.shape[0])
            return original(a, b)

        with mock.patch.object(se, "compare", side_effect=AssertionError("full compare")), \
                mock.patch.object(cv2, "absdiff", side_effect=difference):
            output = job()
        self.assertEqual(output[-1]["changed_fraction"], expected["changed_fraction"])
        self.assertEqual(output[-1]["max_abs_diff"], expected["max_abs_diff"])
        self.assertEqual(sizes, [128, 128, 3])

    def test_analysis_and_heatmap_fit_small_viewport_with_scroll_access(self):
        self.root.geometry("760x650")
        for frame, child in ((self.app.heatmap_frame, self.app._hm_info),
                             (self.app.analysis_frame, self.app._an_metrics)):
            self.app._show(frame)
            self.root.update()
            self.assertLessEqual(self.root.minsize()[1], 650)
            canvas = child.master.master
            self.assertIsInstance(canvas, tk.Canvas)
            self.assertTrue(canvas.cget("yscrollcommand"))
            self.assertTrue(canvas.cget("scrollregion"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
