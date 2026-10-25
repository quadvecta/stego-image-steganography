"""Headless GUI smoke test (run under xvfb-run). Drives the real StegoApp, with native dialogs stubbed."""
import os, sys, tempfile, time, unittest
from unittest import mock
import numpy as np, cv2
import tkinter as tk
import stego_test_file2_newuserguide as g
import stego_engine as se


def pump(root, cond, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        root.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


class GuiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        rng = np.random.default_rng(3)
        img = (np.linspace(0, 255, 320)[None, :, None] * np.ones((240, 1, 3)) + rng.integers(-15, 15, (240, 320, 3))).clip(0, 255).astype(np.uint8)
        cls.src = os.path.join(cls.tmp, "cover.png"); cv2.imwrite(cls.src, img)
        cls.out = os.path.join(cls.tmp, "out.png")
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Display unavailable; run under Xvfb: {exc}")
        cls.app = g.StegoApp(cls.root)
        cls.root.update()
        cls.errors = []
        cls.p = [mock.patch.object(g.messagebox, "showerror", lambda t, m: cls.errors.append((t, m))),
                 mock.patch.object(g.messagebox, "showinfo", lambda *a: None),
                 mock.patch.object(g.messagebox, "askyesno", lambda *a: True),
                 mock.patch.object(g, "_native_notify", lambda *a, **k: None),
                 mock.patch.object(g, "_native_save_img", lambda *a, **k: cls.out),
                 mock.patch.object(cls.app, "_ask_export_mode", lambda: "secure")]
        for p in cls.p: p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.p: p.stop()
        cls.root.destroy()

    def test_full_flow(self):
        a, root = self.app, self.root
        a._show(a.encode_frame)
        a.src_path.set(self.src)
        a.secret_msg.delete("1.0", "end"); a.secret_msg.insert("1.0", "GUI round trip ✓ héllo")
        a.encrypt()
        self.assertTrue(pump(root, lambda: a.output_frame.winfo_ismapped()), self.errors)
        r = a._result
        self.assertTrue(r.verified and os.path.isfile(self.out))
        self.assertEqual(a.key_text.get("1.0", "end").strip(), r.token)
        self.assertIn("320 × 240", a._out["dims"].cget("text"))

        # heatmap
        a._show_heatmap()
        self.assertTrue(pump(root, lambda: a._hm_img is not None and not a._busy), self.errors)
        self.assertTrue(a.heatmap_frame.winfo_ismapped())
        before = r.img_out.copy()
        a._toggle_heatmap()
        self.assertTrue(pump(root, lambda: not a._busy))
        a._hm_mode.set("density"); a._hm_scope.set("payload"); a._hm_bin.set(8)
        a._render_heatmap()
        self.assertTrue(pump(root, lambda: not a._busy))
        a._hm_scope.set("header"); a._render_heatmap()
        self.assertTrue(pump(root, lambda: not a._busy))
        np.testing.assert_array_equal(r.img_out, before)
        self.assertEqual(se.stego_decode(r.img_out, r.token), "GUI round trip ✓ héllo")
        a._show(a.output_frame)
        self.assertIs(a._result, r)                                   # state intact after returning
        self.assertEqual(a._last_saved_image, self.out)

        # analysis
        a._show_analysis()
        self.assertTrue(pump(root, lambda: "PSNR" in a._an_metrics.cget("text")), self.errors)

        # decode via GUI (correct then wrong token)
        a._show(a.decode_frame)
        a.encrypted_path.set(self.out); a.decrypt_key.set(r.token)
        a.decrypt()
        self.assertTrue(pump(root, lambda: a.result_text.get("1.0", "end").strip() != ""))
        self.assertEqual(a.result_text.get("1.0", "end").strip(), "GUI round trip ✓ héllo")
        a.decrypt_key.set(se.new_token()); a.decrypt()
        self.assertTrue(pump(root, lambda: len(self.errors) > 0 and not a._busy))
        self.assertEqual(a.result_text.get("1.0", "end").strip(), "")  # no stale plaintext
        self.assertIn("No valid STEGO payload", self.errors[-1][1])

        # leaving output clears secrets
        a._show(a.output_frame); a._leave_output("home")
        self.assertIsNone(a._result)
        self.assertEqual(a._last_key_token, "")

    def test_resizable_and_help(self):
        self.assertEqual(self.root.resizable(), (True, True))
        a = self.app
        a._show_help(); self.root.update()
        w = a._help_win
        self.assertTrue(w.winfo_exists())
        a._show_help()                                  # no duplicate window
        self.assertIs(a._help_win, w)
        self.root.update(); w.destroy()
        self.root.geometry("1200x900"); self.root.update()
        a._show(a.home_frame); self.root.update()
        self.assertGreaterEqual(self.root.winfo_width(), 1100)

    def test_heatmap_without_result(self):
        self.app._result = None
        self.errors.clear()
        self.app._show_heatmap()
        self.assertTrue(self.errors)

    def test_failed_save_stays_on_encode(self):
        a, root = self.app, self.root
        a._show(a.encode_frame); a._result = None
        a.src_path.set(self.src); a.secret_msg.delete("1.0", "end"); a.secret_msg.insert("1.0", "x")
        bad = os.path.join(self.tmp, "nodir", "x.png")
        self.errors.clear()
        with mock.patch.object(g, "_native_save_img", lambda *a, **k: bad):
            a.encrypt()
            self.assertTrue(pump(root, lambda: len(self.errors) > 0))
        self.assertIsNone(a._result)
        self.assertTrue(a.encode_frame.winfo_ismapped())


if __name__ == "__main__":
    unittest.main(verbosity=2)
