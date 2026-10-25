"""Real Linux Tk fallback dialogs under Xvfb, isolated from desktop backends."""
import sys
import tempfile
from pathlib import Path
import tkinter as tk
import unittest
from unittest import mock

import stego_dialogs as d


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux Tk fallback only")
class NativeTkDialogs(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Display unavailable; use Xvfb: {exc}")
        self.addCleanup(self.root.destroy)
        self.root.update()

    def choose(self, kind, selected=None, mode="secure"):
        seen, errors = [], []

        def finish():
            try:
                children = self.root.tk.splitlist(self.root.tk.call("winfo", "children", "."))
                dialogs = [str(w) for w in children if "tk_filedialog" in str(w)]
                seen.append(dialogs)
                if selected is None:
                    self.root.tk.call("::tk::dialog::file::CancelCmd", dialogs[0])
                else:
                    self.root.tk.call("::tk::dialog::file::Done", dialogs[0], str(selected))
            except Exception as exc:
                errors.append(exc)
                self.root.tk.call("set", "::tk::Priv(selectFilePath)", "")

        self.root.after(150, finish)
        with mock.patch.object(d.shutil, "which", return_value=None):
            result = d.choose_file(kind, "STEGO real Tk test", "file.txt" if kind == "text"
                                   else "file.jpg" if mode == "standard" else "file.png",
                                   self.root, mode)
        self.assertEqual(errors, [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertFalse(d.dialog_active())
        return result

    def test_one_dialog_on_cancel_for_each_picker(self):
        for kind, mode in (("open", "secure"), ("image", "secure"),
                           ("image", "standard"), ("text", "secure")):
            with self.subTest(kind=kind, mode=mode):
                self.assertEqual(self.choose(kind, mode=mode), "")

    def test_one_dialog_returns_unicode_selection_for_each_picker(self):
        with tempfile.TemporaryDirectory() as directory:
            for kind, suffix, mode in (("open", ".png", "secure"),
                                       ("image", ".bmp", "secure"),
                                       ("image", ".jpg", "standard"),
                                       ("text", ".txt", "secure")):
                with self.subTest(kind=kind, suffix=suffix):
                    target = Path(directory, "\u65e5\u672c\u8a9e" + suffix)
                    if kind == "open":
                        target.write_bytes(b"path selection only")
                    self.assertEqual(self.choose(kind, target, mode), str(target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
