"""Dialog regression tests; real Tk/subprocess smoke tests need a display."""

import subprocess
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

import stego_dialogs as d

PATH = "/tmp/\u6587\u4ef6/ a 'quoted' name.png "


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.assertFalse(d.dialog_active())
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.enterContext = stack.enter_context
        self.enterContext(mock.patch.object(d.sys, "platform", "linux"))
        self.which = self.enterContext(mock.patch.object(d.shutil, "which", return_value="/bin/zenity"))
        self.run = self.enterContext(mock.patch.object(d.subprocess, "run"))
        self.run.return_value = subprocess.CompletedProcess([], 0, PATH + "\n")
        self.open = self.enterContext(mock.patch.object(d.filedialog, "askopenfilename", return_value=PATH))
        self.save = self.enterContext(mock.patch.object(d.filedialog, "asksaveasfilename", return_value=PATH))
        self.confirm = self.enterContext(mock.patch.object(d.messagebox, "askyesno", return_value=True))
        self.exists = self.enterContext(mock.patch.object(d.os.path, "exists", return_value=False))

    def tearDown(self):
        self.assertFalse(d.dialog_active())

    def test_backend_selection_once(self):
        cases = [("linux", ["/bin/zenity"], 1), ("linux", [None, "/bin/kdialog"], 2),
                 ("linux", [None, None], 2), ("win32", [], 0), ("darwin", [], 0)]
        for platform, paths, checks in cases:
            with self.subTest(platform=platform, paths=paths), mock.patch.object(d.sys, "platform", platform):
                for target in (self.which, self.run, self.open):
                    target.reset_mock()
                self.which.side_effect = paths
                parent = None if paths and paths[-1] else mock.Mock()
                self.assertEqual(d.choose_file("open", "Select", parent=parent), PATH)
                self.assertEqual(self.which.call_count, checks)
                self.assertEqual(self.run.call_count + self.open.call_count, 1)
                if self.run.called:
                    self.assertEqual(self.run.call_args.args[0][0], paths[-1])
                else:
                    self.assertIs(self.open.call_args.kwargs["parent"], parent)
                self.save.assert_not_called()

    def test_cancel_and_operational_errors_never_fall_through(self):
        for tool in ("zenity", "kdialog", None):
            self.which.side_effect = lambda name: "/bin/" + name if name == tool else None
            for kind in ("open", "image", "text"):
                target = self.run if tool else self.open if kind == "open" else self.save
                outcomes = [subprocess.CompletedProcess([], code, out) for code, out in
                            ((1, PATH), (2, PATH), (-9, PATH), (0, ""))] if tool else ["", None]
                outcomes += [FileNotFoundError(), PermissionError(), d.tk.TclError(),
                             subprocess.TimeoutExpired("dialog", 120)]
                for outcome in outcomes:
                    with self.subTest(tool=tool, kind=kind, outcome=outcome):
                        for item in (self.run, self.open, self.save):
                            item.reset_mock(side_effect=True)
                        target.side_effect = [outcome]
                        if isinstance(outcome, Exception):
                            with self.assertRaises(type(outcome)) as caught:
                                d.choose_file(kind, "Error")
                            self.assertIs(caught.exception, outcome)
                        elif tool and outcome.returncode not in (0, 1):
                            with self.assertRaisesRegex(OSError, f"{tool}.*status {outcome.returncode}"):
                                d.choose_file(kind, "Error")
                        else:
                            self.assertEqual(d.choose_file(kind, "Cancel"), "")
                        self.assertEqual(sum(item.call_count for item in (self.run, self.open, self.save)), 1)
                        self.assertFalse(d.dialog_active())

    def test_reentry_is_global_for_external_and_tk(self):
        for tool in ("zenity", None):
            self.which.return_value = "/bin/zenity" if tool else None
            def nested(*args, **kwargs):
                self.assertTrue(d.dialog_active())
                for kind in ("open", "image", "text"):
                    self.assertEqual(d.choose_file(kind, "Nested", parent=object()), "")
                return subprocess.CompletedProcess([], 0, PATH + "\n") if tool else PATH
            target = self.run if tool else self.open
            target.side_effect = nested
            self.assertEqual(d.choose_file("open", "Outer"), PATH)
            target.assert_called_once()
            self.assertFalse(d.dialog_active())

    def test_unexpected_exceptions_release_guard(self):
        for target in (self.which, self.run, self.open, self.save):
            self.which.return_value = None if target in (self.open, self.save) else "/bin/zenity"
            kind = "text" if target is self.save else "open"
            target.side_effect = RuntimeError("backend bug")
            with self.assertRaisesRegex(RuntimeError, "backend bug"):
                d.choose_file(kind, "Error")
            target.side_effect = None
            self.assertFalse(d.dialog_active())
            self.assertEqual(d.choose_file(kind, "Retry"), PATH)

    def test_invalid_arguments_release_guard(self):
        for kind, mode in (("unknown", "secure"), ("image", "unknown")):
            with self.assertRaises(ValueError):
                d.choose_file(kind, "Invalid", mode=mode)
            self.assertFalse(d.dialog_active())
        self.which.assert_not_called()

    def test_filters_defaults_and_parent(self):
        cases = [("open", mode, "", "", "*.png *.bmp *.jpg *.jpeg *") for mode in ("secure", "standard")]
        cases += [("image", "secure", "bad.gif", ".png", "*.png *.bmp"),
                  ("image", "secure", "out.BMP", ".bmp", "*.png *.bmp"),
                  ("image", "standard", "", ".jpg", "*.jpg *.jpeg"),
                  ("image", "standard", "out.jpeg", ".jpeg", "*.jpg *.jpeg"),
                  ("text", "secure", "bad.pdf", ".txt", "*.txt")]
        title, parent = "Title 'quoted'; $(not a command)", mock.Mock()
        for tool in ("zenity", "kdialog", None):
            self.which.side_effect = lambda name: "/bin/" + name if name == tool else None
            for kind, mode, default, ext, patterns in cases:
                with self.subTest(tool=tool, kind=kind, mode=mode, default=default):
                    self.run.return_value = subprocess.CompletedProcess([], 0, "/tmp/name\n")
                    self.open.return_value = self.save.return_value = "/tmp/name" + ext
                    result = d.choose_file(kind, title, default, None if tool else parent, mode)
                    self.assertEqual(result, "/tmp/name" + ext)
                    if tool:
                        args = self.run.call_args.args[0]
                        self.assertFalse(self.run.call_args.kwargs.get("shell", False))
                        self.assertIn("--title=" + title if tool == "zenity" else title, args)
                        filters = [a.split(" | ")[1] for a in args if a.startswith("--file-filter=")]
                        if tool == "kdialog":
                            filters = [a.split("|")[0] for a in args[3].splitlines()]
                    else:
                        options = (self.open if kind == "open" else self.save).call_args.kwargs
                        self.assertIs(options["parent"], parent)
                        filters = [value for label, value in options["filetypes"]]
                        if kind != "open":
                            self.assertEqual(options["defaultextension"], ext)
                            self.assertEqual(options["initialfile"], default)
                    self.assertEqual(set(" ".join(filters).split()), set(patterns.split()))
        self.confirm.assert_not_called()

    def test_explicit_extensions_and_unicode_are_unchanged(self):
        self.exists.return_value = True
        for tool in ("zenity", "kdialog", None):
            self.which.side_effect = lambda name: "/bin/" + name if name == tool else None
            for suffix in (".jpg", ".jpeg", ".PNG", ".bmp", ".pdf", ".gif", ".txt", ".odd "):
                path = PATH + suffix
                self.run.return_value = subprocess.CompletedProcess([], 0, path + "\n")
                self.open.return_value = self.save.return_value = path
                for kind in ("open", "image", "text"):
                    for mode in ("standard", "secure"):
                        self.assertEqual(d.choose_file(kind, "Save", mode=mode), path)
        self.exists.assert_not_called()
        self.confirm.assert_not_called()

    def test_resolved_overwrite_path_approval_and_rejection(self):
        path = "/tmp/\u6587\u4ef6/ a 'quoted' name "
        self.exists.return_value = True
        for tool in ("zenity", "kdialog"):
            self.which.side_effect = lambda name: "/bin/" + name if name == tool else None
            for parent in (None, mock.Mock()):
                for kind, mode, default, ext in (("image", "secure", "", ".png"),
                                               ("image", "standard", "", ".jpg"),
                                               ("image", "secure", "out.bmp", ".bmp"),
                                               ("text", "secure", "", ".txt")):
                    resolved = path + ext
                    for approve in (True, False):
                        with self.subTest(tool=tool, parent=parent, kind=kind, approve=approve):
                            def confirm(*args, **kwargs):
                                self.assertTrue(d.dialog_active())
                                self.assertEqual(d.choose_file("open", "Nested"), "")
                                return approve
                            self.confirm.reset_mock()
                            self.confirm.side_effect = confirm
                            self.exists.reset_mock()
                            with mock.patch.object(d, "_external", return_value=path) as external:
                                self.assertEqual(d.choose_file(kind, "Save", default, parent, mode),
                                                 resolved if approve else "")
                            external.assert_called_once()
                            self.exists.assert_called_once_with(resolved)
                            self.confirm.assert_called_once_with(
                                "Confirm overwrite", f"Replace the existing file?\n\n{resolved}", parent=parent)
                            self.assertFalse(d.dialog_active())

    def test_confirmation_errors_and_parent_destruction(self):
        self.exists.return_value = True
        for error in (OSError("confirm failed"), d.tk.TclError("confirm failed")):
            self.confirm.side_effect = error
            with mock.patch.object(d, "_external", return_value="/tmp/name"):
                with self.assertRaises(type(error)) as caught:
                    d.choose_file("text", "Save", parent=mock.Mock())
                self.assertIs(caught.exception, error)
                self.assertFalse(d.dialog_active())
        parent = mock.Mock()
        parent.winfo_exists.side_effect = [True, False]
        with mock.patch.object(d, "_external", return_value="/tmp/name"):
            self.assertEqual(d.choose_file("text", "Save", parent=parent), "")

    def test_dead_parent_cancels_without_showing_anything(self):
        root = self.root()
        root.destroy()
        for kind in ("open", "image", "text"):
            self.assertEqual(d.choose_file(kind, "Closed", parent=root), "")
        for target in (self.which, self.run, self.open, self.save, self.confirm):
            target.assert_not_called()

    def root(self):
        try:
            root = d.tk.Tk()
        except d.tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        root.update_idletasks()
        self.addCleanup(lambda: root.destroy() if root.tk.call("info", "commands", ".") else None)
        return root

    def test_real_subprocess_keeps_tk_responsive_and_handles_destroy(self):
        real_popen = subprocess.Popen
        for outcome in (0, 1, 2, "destroy"):
            root, children, pulses = self.root(), [], []
            def launch(args, **kwargs):
                script = (f"import time; time.sleep({30 if outcome == 'destroy' else 0.15}); "
                          f"print({PATH!r}); exit({outcome if isinstance(outcome, int) else 0})")
                child = real_popen([sys.executable, "-c", script], **kwargs)
                children.append(child)
                return child
            root.after(10, lambda: pulses.append((d.dialog_active(), d.choose_file("text", "Nested"))))
            if outcome == "destroy":
                root.after(30, root.destroy)
            with mock.patch.object(d.subprocess, "Popen", side_effect=launch) as popen:
                if outcome == 2:
                    with self.assertRaisesRegex(OSError, "zenity.*status 2"):
                        d.choose_file("open", "Smoke", parent=root)
                else:
                    self.assertEqual(d.choose_file("open", "Smoke", parent=root), PATH if outcome == 0 else "")
                popen.assert_called_once()
            self.assertEqual(pulses, [(True, "")])
            self.assertIsNotNone(children[0].poll())
            self.assertFalse(root.tk.call("after", "info"))
            self.run.assert_not_called()
            self.assertFalse(d.dialog_active())

    def test_async_errors_timeout_and_forced_termination(self):
        root = self.root()
        with mock.patch.object(d.subprocess, "Popen", side_effect=OSError("launch failed")):
            with self.assertRaisesRegex(OSError, "launch failed"):
                d.choose_file("open", "Error", parent=root)
        self.assertFalse(d.dialog_active())
        self.assertEqual(self.open.call_count + self.save.call_count, 0)
        for failure in ("variable", "bind", "wait", "poll", "schedule", "timeout"):
            process = mock.Mock()
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired("fake", 0.2), None]
            if failure == "poll":
                process.poll.side_effect = [RuntimeError("poll"), None]
            patch = {"variable": mock.patch.object(d.tk, "BooleanVar", side_effect=d.tk.TclError("variable")),
                     "bind": mock.patch.object(root, "bind", side_effect=d.tk.TclError("bind")),
                     "wait": mock.patch.object(root, "wait_variable", side_effect=RuntimeError("wait")),
                     "poll": mock.patch.object(d.time, "monotonic", return_value=0),
                     "schedule": mock.patch.object(root, "after", side_effect=d.tk.TclError("closed")),
                     "timeout": mock.patch.object(d.time, "monotonic", side_effect=[0, 121])}[failure]
            with mock.patch.object(d.subprocess, "Popen", return_value=process), patch:
                if failure in ("wait", "poll"):
                    with self.assertRaisesRegex(RuntimeError, failure):
                        d.choose_file("open", "Error", parent=root)
                else:
                    expected = subprocess.TimeoutExpired if failure == "timeout" else d.tk.TclError
                    with self.assertRaises(expected):
                        d.choose_file("open", "Error", parent=root)
            process.terminate.assert_called_once()
            process.kill.assert_called_once()
            self.assertFalse(d.dialog_active())
            self.assertFalse(root.tk.call("after", "info"))
            self.assertEqual(root.bind("<Destroy>"), "")


if __name__ == "__main__":
    unittest.main()
