"""Single-dialog file selection; call from the Tk thread when supplying a parent.

Tk's Windows/macOS file dialogs are native and parent-aware, so PowerShell and
AppleScript wrappers are unnecessary. Linux selects Zenity, KDialog, or Tk once
per action, before showing anything; cancellation/errors never select another.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from contextlib import suppress
from tkinter import filedialog, messagebox

_guard = threading.Lock()


def dialog_active():
    """Whether any choose_file call is currently active (including nested waits)."""
    return _guard.locked()


def _parent_alive(parent):
    try:
        return parent is None or bool(parent.winfo_exists())
    except tk.TclError:
        return False


def _result_path(args, returncode, output):
    if returncode not in (0, 1):
        raise OSError(f"{os.path.basename(args[0])} file dialog exited with status {returncode}")
    return output.removesuffix("\n") if returncode == 0 else ""


def _external(args, parent):
    if parent is None:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding="utf-8", timeout=120)
        return _result_path(args, result.returncode, result.stdout)

    # A file instead of a pipe prevents a verbose child from blocking on stdout.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        process = subprocess.Popen(args, stdout=output, stderr=subprocess.DEVNULL)
        timer = binding = None
        error = None
        cancelled = False
        try:
            done = tk.BooleanVar(master=parent, value=False)
            deadline = time.monotonic() + 120

            def destroyed(event):
                nonlocal cancelled
                if event.widget is parent:
                    cancelled = True
                    done.set(True)

            def poll():
                nonlocal timer, error, cancelled
                timer = None
                try:
                    cancelled = not _parent_alive(parent)
                    if cancelled or process.poll() is not None:
                        done.set(True)
                    elif time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(args, 120)
                    else:
                        timer = parent.after(40, poll)
                except Exception as exc:
                    error = exc
                    done.set(True)

            binding = parent.bind("<Destroy>", destroyed, add="+")
            timer = parent.after(0, poll)
            parent.wait_variable(done)
            if error is not None:
                raise error
            if cancelled:
                return ""
            output.seek(0)
            return _result_path(args, process.poll(), output.read())
        finally:
            with suppress(tk.TclError):
                if timer is not None:
                    parent.after_cancel(timer)
                if binding is not None:
                    parent.unbind("<Destroy>", binding)
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=0.2)


def choose_file(kind, title, default_name="", parent=None, mode="secure"):
    """Return a path or '' on cancel, reentry, or parent destruction.

    kind: 'open' (all input images), 'image' (save), or 'text' (save).
    mode: 'standard' (JPEG) or 'secure' (PNG/BMP), affecting image saves only.
    Explicit extensions are never rewritten/validated here; the GUI must do that.
    Errors/timeouts propagate, with the guard released. Appended extensions get
    a final-path overwrite confirmation; explicit extensions use native checks.
    """
    if not _guard.acquire(blocking=False):
        return ""
    try:
        if kind not in ("open", "image", "text") or mode not in ("secure", "standard"):
            raise ValueError("Expected kind open/image/text and mode secure/standard")
        if not _parent_alive(parent):
            return ""
        extensions = (".jpg", ".jpeg") if mode == "standard" else (".png", ".bmp")
        if kind == "text":
            extensions = (".txt",)
        extension = os.path.splitext(default_name)[1].lower()
        if extension not in extensions:
            extension = extensions[0]
        if kind == "open":
            filetypes = [("Images", "*.png *.bmp *.jpg *.jpeg"), ("All files", "*")]
        elif kind == "text":
            filetypes = [("Text file", "*.txt")]
        elif mode == "standard":
            filetypes = [("JPEG image", "*.jpg *.jpeg")]
        else:
            filetypes = [("PNG image", "*.png"), ("BMP image", "*.bmp")]
            filetypes.sort(key=lambda item: "*" + extension not in item[1].split())

        executable = None
        if sys.platform.startswith("linux"):
            for tool in ("zenity", "kdialog"):
                executable = shutil.which(tool)
                if executable:
                    break
        if executable is None:
            options = dict(title=title, parent=parent, filetypes=filetypes)
            if kind == "open":
                return filedialog.askopenfilename(**options) or ""
            return filedialog.asksaveasfilename(
                **options, initialfile=default_name, defaultextension=extension) or ""

        initial = os.path.join(os.path.expanduser("~"), default_name)
        if tool == "zenity":
            args = [executable, "--file-selection", f"--title={title}", f"--filename={initial}"]
            if kind != "open":
                args += ["--save", "--confirm-overwrite"]
            args += [f"--file-filter={label} | {patterns}" for label, patterns in filetypes]
        else:
            filters = "\n".join(f"{patterns}|{label}" for label, patterns in filetypes)
            args = [executable, "--getopenfilename" if kind == "open" else "--getsavefilename",
                    initial, filters, "--title", title]
        path = _external(args, parent)
        if path and kind != "open" and not os.path.splitext(path)[1]:
            path += extension
            if os.path.exists(path) and not messagebox.askyesno(
                    "Confirm overwrite", f"Replace the existing file?\n\n{path}", parent=parent):
                return ""
        return path
    except tk.TclError:
        if not _parent_alive(parent):
            return ""
        raise
    finally:
        _guard.release()
