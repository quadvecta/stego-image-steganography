"""
STEGO v1.01  —  Steganography Suite
Changes from the supplied baseline (stego_initial_test_1.py):
  · Fixed launch crash (_show_help had been swallowed into _back_from_decode)
  · New engine (stego_engine.py): 256-bit random token, HKDF key split, keyed ChaCha20 scatter,
    authenticated header, exact embedding-position tracking, verified save-and-reload
  · New OUTPUT screen with metrics, token actions, SHOW HEATMAP and COVER vs STEGO analysis
  · Honest labels: Fernet = AES-128-CBC + HMAC-SHA256 (not AES-256)
  · All heavy work on a worker thread; results return to Tk via a polled queue
NOT compatible with images produced by v0.x.
"""

import base64
import io
import tkinter as tk
from tkinter import messagebox, Menu
import os
import sys
import subprocess
import threading
import queue
import tempfile
import stat
import cv2

import stego_engine as se
from stego_engine import StegoError
from stego_dialogs import choose_file, dialog_active
from stego_themes import ThemeManager, THEMES


# ═══════════════════════════════════════════════════════════════════════════════
#  PLATFORM HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _is_win():   return sys.platform == "win32"
def _is_mac():   return sys.platform == "darwin"
def _is_linux(): return sys.platform.startswith("linux")


# ── Native open-file dialog ───────────────────────────────────────────────────

def _native_open(title, parent=None):
    """Open-file dialog that returns a path string or ''."""
    return choose_file("open", title, parent=parent)


# ── Native save-image dialog ──────────────────────────────────────────────────

def _native_save_img(title, default_name, parent=None, mode="secure"):
    return choose_file("image", title, default_name, parent=parent, mode=mode)


# ── Native save-text dialog ───────────────────────────────────────────────────

def _native_save_txt(title, default_name, parent=None):
    """Save-file dialog for .txt. Returns path or ''."""
    return choose_file("text", title, default_name, parent=parent)


def _write_export(path, data):
    """Commit key/preview bytes without truncating an existing file on failure."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(os.path.abspath(path)),
                                         prefix=".stego-export-", delete=False) as f:
            temp_path = f.name
            if f.write(data) != len(data):
                raise StegoError("Incomplete export write.")
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if not os.access(path, os.W_OK) or (os.name == "posix" and not mode & 0o222):
                raise StegoError("The selected destination is read-only.")
            os.chmod(temp_path, mode)
            if os.name == "posix" and stat.S_IMODE(os.stat(temp_path).st_mode) != mode:
                raise StegoError("Could not preserve destination permissions.")
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            os.unlink(temp_path)


# ── Native notification ───────────────────────────────────────────────────────

def _native_notify(title, message, parent=None):
    """Show a native OS success dialog/notification."""
    if _is_win():
        try:
            import ctypes
            hwnd = int(parent.winfo_id()) if parent else 0
            ctypes.windll.user32.MessageBoxW(
                hwnd, message, title, 0x00000040 | 0x00010000)
            return
        except Exception:
            pass

    elif _is_mac():
        safe_msg   = message.replace('"', '\\\"'  )
        safe_title = title.replace('"', '\\\"'  )
        script = (f'display dialog "{safe_msg}" with title "{safe_title}" ' +
                  'buttons {"OK"} default button "OK" with icon note')
        try:
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=30)
            return
        except Exception:
            pass

    else:
        for args in [
            ["notify-send", "--app-name=STEGO", "--icon=dialog-information",
             title, message],
            ["zenity", "--info", f"--title={title}", f"--text={message}"],
            ["kdialog", "--title", title, "--msgbox", message],
        ]:
            try:
                if args[0] == "notify-send":
                    subprocess.Popen(args)
                else:
                    subprocess.run(args, capture_output=True, timeout=30)
                return
            except FileNotFoundError:
                continue

    messagebox.showinfo(title, message)


# ── Native share ──────────────────────────────────────────────────────────────

def _native_share_file(filepath, parent=None):
    """Share a file via OS-native mechanisms."""
    if not filepath or not os.path.isfile(filepath):
        messagebox.showerror("Share", "File not found — save it first.")
        return

    if _is_win():
        # Win10/11 Share UI via WinRT through PowerShell
        safe = filepath.replace("\\", "\\\\").replace("'", "")
        cmd = (
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
            "[void][Windows.ApplicationModel.DataTransfer.DataTransferManager,"
            "Windows.ApplicationModel.DataTransfer,ContentType=WindowsRuntime]; "
            "[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]; "
            f"$f = [Windows.Storage.StorageFile]::GetFileFromPathAsync('{safe}').GetAwaiter().GetResult(); "
            "$dtm=[Windows.ApplicationModel.DataTransfer.DataTransferManager]::GetForCurrentView(); "
            "$dtm.add_DataRequested({"
            "param($s,$e); "
            "$e.Request.Data.SetStorageItems("
            "[System.Collections.Generic.List[Windows.Storage.IStorageItem]]@($f)); "
            "$e.Request.Data.Properties.Title='Share STEGO Image'}); "
            "[Windows.ApplicationModel.DataTransfer.DataTransferManager]::ShowShareUI()"
        )
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive",
                              "-WindowStyle", "Hidden", "-Command", cmd],
                             creationflags=0x08000000)
            return
        except Exception:
            pass
        try:
            subprocess.Popen(["explorer", "/select,", filepath])
        except Exception:
            pass
        return

    elif _is_mac():
        script = f'tell application "Finder" to open POSIX file "{filepath}"'
        subprocess.Popen(["osascript", "-e", script])
        return

    else:  # Linux
        for args in [
            ["xdg-email", f"--attach={filepath}"],
            ["thunderbird", "--compose", f"attachment='{filepath}'"],
            ["xdg-open", os.path.dirname(filepath)],
        ]:
            try:
                subprocess.Popen(args)
                return
            except FileNotFoundError:
                continue
        messagebox.showinfo("Share", f"Image saved at:\n{filepath}\n\nShare it manually.")


def _native_share_text(text, parent=None):
    """Copy key to clipboard and try to open a share/email target."""
    try:
        if parent:
            parent.clipboard_clear()
            parent.clipboard_append(text)
    except Exception:
        pass

    if _is_win():
        # Copy via PowerShell then try Share UI
        safe_text = text.replace("'", "''")
        cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.Clipboard]::SetText('{safe_text}'); "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
            "[void][Windows.ApplicationModel.DataTransfer.DataTransferManager,"
            "Windows.ApplicationModel.DataTransfer,ContentType=WindowsRuntime]; "
            "$dtm=[Windows.ApplicationModel.DataTransfer.DataTransferManager]::GetForCurrentView(); "
            f"$dtm.add_DataRequested({{param($s,$e); $e.Request.Data.SetText('{safe_text}'); "
            "$e.Request.Data.Properties.Title='STEGO Decryption Key'}); "
            "[Windows.ApplicationModel.DataTransfer.DataTransferManager]::ShowShareUI()"
        )
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive",
                              "-WindowStyle", "Hidden", "-Command", cmd],
                             creationflags=0x08000000)
            return
        except Exception:
            pass
        messagebox.showinfo("Key Copied",
            "Key copied to clipboard. Paste it in your messaging app.")
        return

    elif _is_mac():
        safe = text.replace('"', '\\\"'  )
        subprocess.Popen(["osascript", "-e", f'set the clipboard to "{safe}"'])
        return

    else:  # Linux
        for args in [
            ["xdg-email", f"--body={text}"],
        ]:
            try:
                subprocess.Popen(args)
                return
            except FileNotFoundError:
                continue
        messagebox.showinfo("Key Copied",
            "Key copied to clipboard. Paste it into your preferred app.")


# ═══════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE
# ═══════════════════════════════════════════════════════════════════════════════

BG        = "#0a0e1a"
SURFACE   = "#0f1628"
SURFACE2  = "#141d35"
PANEL_BD  = "#1e2d50"
ACCENT    = "#00d4ff"
ACCENT3   = "#06ffa5"
BTN_FG    = "#0a0e1a"
BTN_HOV   = "#00b8e0"
LABEL_FG  = "#e2e8f0"
MUTED_FG  = "#8ba3c7"
ENTRY_BG  = "#0d1526"
ENTRY_BD  = "#1e3a5f"
KEY_BG    = "#0a1f1a"
KEY_FG    = "#06ffa5"
RESULT_BG = "#0a1f14"
RESULT_FG = "#06ffa5"
STATUS_BG = "#07090f"
STATUS_FG = "#4a7a9a"
WARN_FG   = "#f59e0b"
ERROR_FG  = "#ff7373"
DISABLED_FG = "#91a0b8"
_themes = None


def _theme_color(value):
    return _themes.color(value) if _themes is not None else value


# ═══════════════════════════════════════════════════════════════════════════════
#  WIDGETS
# ═══════════════════════════════════════════════════════════════════════════════

class GlowButton(tk.Canvas):
    def __init__(self, parent, text, command, width=180, height=44,
                 accent=ACCENT, icon="", **kwargs):
        bg = kwargs.pop("bg", parent["bg"])
        super().__init__(parent, width=width, height=height,
                         bg=bg, highlightthickness=0, cursor="hand2", **kwargs)
        self._accent = _theme_color(accent)
        self._text   = (icon + "  " + text) if icon else text
        self._cmd    = command
        self._draw(False)
        self.bind("<Enter>",    lambda e: self._draw(True))
        self.bind("<Leave>",    lambda e: self._draw(False))
        self.bind("<Button-1>", lambda e: self._cmd())

    def _draw(self, hov):
        self.delete("all")
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        r = 6
        fill = SURFACE2 if not hov else self._accent
        self._rr(2, 2, w-2, h-2, r, fill=fill, outline="")
        brd = self._accent if hov else self._blend(self._accent, BG, 0.5)
        self._rr(1, 1, w-1, h-1, r, fill="", outline=brd, width=1 if not hov else 2)
        for ax, ay, bx, by in [
            (2,2,14,2),(2,2,2,14),(w-2,2,w-14,2),(w-2,2,w-2,14),
            (2,h-2,14,h-2),(2,h-2,2,h-14),(w-2,h-2,w-14,h-2),(w-2,h-2,w-2,h-14)]:
            self.create_line(ax, ay, bx, by, fill=self._accent, width=2)
        self.create_text(w//2, h//2, text=self._text,
                         fill=BTN_FG if hov else self._accent,
                         font=("Courier New", 12, "bold"))

    def _rr(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r,y1, x2-r,y1, x2,y1+r, x2,y2-r,
               x2-r,y2, x1+r,y2, x1,y2-r, x1,y1+r]
        self.create_polygon(pts, smooth=True, **kw)

    @staticmethod
    def _blend(h1, h2, t):
        r1,g1,b1 = int(h1[1:3],16),int(h1[3:5],16),int(h1[5:7],16)
        r2,g2,b2 = int(h2[1:3],16),int(h2[3:5],16),int(h2[5:7],16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r1*t+r2*(1-t)), int(g1*t+g2*(1-t)), int(b1*t+b2*(1-t)))

    @staticmethod
    def _darken(hx, f):
        r,g,b = int(hx[1:3],16),int(hx[3:5],16),int(hx[5:7],16)
        return "#{:02x}{:02x}{:02x}".format(int(r*(1-f)),int(g*(1-f)),int(b*(1-f)))


class SmallBtn(tk.Button):
    def __init__(self, parent, text, command, accent=ACCENT, **kw):
        accent = _theme_color(accent)
        super().__init__(parent, text=text, command=command,
                         bg=SURFACE2, fg=accent, relief="flat",
                         font=("Courier New", 9, "bold"),
                         cursor="hand2", padx=8, pady=4,
                         activebackground=PANEL_BD,
                         activeforeground=accent, bd=0, **kw)
        self.bind("<Enter>", lambda e: self.config(bg=PANEL_BD))
        self.bind("<Leave>", lambda e: self.config(bg=SURFACE2))


class CyberEntry(tk.Frame):
    def __init__(self, parent, textvariable=None, width=40, readonly=False,
                 mono=False, accent=ACCENT, fg=LABEL_FG, bg=ENTRY_BG, **kwargs):
        super().__init__(parent, bg=ENTRY_BD, padx=1, pady=1)
        self._accent = accent = _theme_color(accent)
        fg, bg = _theme_color(fg), _theme_color(bg)
        font  = ("Courier New", 11) if mono else ("Segoe UI", 11)
        state = "readonly" if readonly else "normal"
        self._e = tk.Entry(self, textvariable=textvariable,
                           font=font, bg=bg, fg=fg,
                           insertbackground=accent,
                           relief="flat", bd=4, state=state,
                           readonlybackground=bg,
                           selectbackground=accent,
                            selectforeground=BTN_FG,
                           width=width, **kwargs)
        self._e.pack(fill="both")
        self._e.bind("<FocusIn>",  lambda e: self.config(bg=self._accent))
        self._e.bind("<FocusOut>", lambda e: self.config(bg=ENTRY_BD))

    def get_entry(self): return self._e


# ═══════════════════════════════════════════════════════════════════════════════
#  APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class StegoApp:
    def __init__(self, master):
        self.master = master
        master.title("STEGO — Hidden in Pixels, Protected by Trust")
        master.configure(bg=BG)
        master.resizable(True, True)
        master.minsize(560, 560)
        self._help_win = None

        self._last_saved_image = ""
        self._last_key_token   = ""
        self._result    = None          # EncodeResult of the last verified encode
        self._counts    = None          # cached exact per-pixel hit map
        self._token_secured = False     # copied/saved at least once
        self._busy      = False
        self._q         = queue.Queue()
        self._photos    = {}            # keep PhotoImage refs alive
        self._hm_img    = None
        self._export_dlg_open = False
        self._dialog_open = False
        self._closed = False
        self.theme_name = tk.StringVar(master, value="Cyber")

        self._build_menu()
        self.container = tk.Frame(master, bg=BG)
        self.container.pack(fill="both", expand=True)

        self.home_frame   = self._build_home()
        self.encode_frame = self._build_encode()
        self.decode_frame = self._build_decode()
        self.output_frame = self._build_output()
        self.heatmap_frame = self._build_heatmap()
        self.analysis_frame = self._build_analysis()
        self._show(self.home_frame)
        self._poll_id = self.master.after(60, self._poll_queue)

        sb = tk.Frame(master, bg=STATUS_BG, height=22)
        sb.pack(fill="x", side="bottom")
        tk.Canvas(sb, bg=ACCENT, height=1, highlightthickness=0).pack(fill="x")
        self._status_lbl = tk.Label(
            sb,
            text="◈  STEGO v1.01   ·   Fernet (AES-128-CBC + HMAC) + Keyed LSB Scatter   ·   Ready",
            bg=STATUS_BG, fg=STATUS_FG, font=("Courier New", 10))
        self._status_lbl.pack(pady=3)
        global _themes
        self.themes = _themes = ThemeManager(master, sys.modules[__name__])
        master.protocol("WM_DELETE_WINDOW", self._close)
        master.bind("<Destroy>", self._on_destroy, add="+")

    # ── navigation ────────────────────────────────────────────────────────────

    def _show(self, frame, force=False):
        if not force and (self._busy or self._dialog_open):
            return
        for f in (self.home_frame, self.encode_frame, self.decode_frame,
                  self.output_frame, self.heatmap_frame, self.analysis_frame):
            f.pack_forget()
        frame.pack(fill="both", expand=True)
        # keep the window large enough for this screen (matters once the user resizes/maximises)
        self.master.update_idletasks()
        self.master.minsize(max(560, frame.winfo_reqwidth()), max(560, frame.winfo_reqheight() + 28))

    def _set_status(self, msg, color=STATUS_FG):
        self._status_lbl.config(text=f"◈  {msg}", fg=_theme_color(color))

    def _close(self):
        if self._busy or self._dialog_open:
            self._set_status("Please wait for the current operation to finish.", WARN_FG)
            return
        if self._last_key_token and not self._token_secured:
            if not messagebox.askyesno("Key token not saved", "Close without saving the key token?"):
                return
        self.master.destroy()

    def _on_destroy(self, event):
        if event.widget is self.master:
            self._closed = True
            self.master.after_cancel(self._poll_id)

    def _set_theme(self, name):
        self.themes.set_theme(name)
        self.theme_name.set(name)
        # Only the decorative logo is redrawn; source/encoded previews are untouched.
        self._draw_logo(self._logo_canvas)

    # ── menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb   = Menu(self.master, bg=SURFACE, fg=LABEL_FG,
                    activebackground=ACCENT, activeforeground=BG,
                    relief="flat", bd=0)
        hm   = Menu(mb, tearoff=0, bg=SURFACE2, fg=LABEL_FG,
                    activebackground=ACCENT, activeforeground=BG)
        hm.add_command(label="User Guide", command=self._show_help)
        mb.add_cascade(label="Help", menu=hm)
        tm = Menu(mb, tearoff=0, bg=SURFACE2, fg=LABEL_FG,
                  activebackground=ACCENT, activeforeground=BTN_FG)
        for name in THEMES:
            tm.add_radiobutton(label=name, variable=self.theme_name, value=name,
                               command=lambda n=name: self._set_theme(n))
        mb.add_cascade(label="Theme", menu=tm)
        self.master.config(menu=mb)

    def _reset_encode(self):
        """Clear all fields on the Encode screen."""
        self.src_path.set("")
        self.secret_msg.delete("1.0", tk.END)
        self._last_key_token   = ""
        self._last_saved_image = ""
        self._set_status("STEGO v1.01   ·   Fernet (AES-128-CBC + HMAC) + Keyed LSB Scatter   ·   Ready", STATUS_FG)

    def _reset_decode(self):
        """Clear all fields on the Decode screen."""
        self.encrypted_path.set("")
        self.decrypt_key.set("")
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.config(state="disabled")
        self._set_status("STEGO v1.01   ·   Fernet (AES-128-CBC + HMAC) + Keyed LSB Scatter   ·   Ready", STATUS_FG)

    def _back_from_encode(self):
        if self._busy or self._dialog_open:
            return
        self._reset_encode()
        self._show(self.home_frame)

    def _back_from_decode(self):
        if self._busy or self._dialog_open:
            return
        self._reset_decode()
        self._show(self.home_frame)

    def _show_help(self):
        """Themed, non-modal user guide (single instance)."""
        if self._help_win is not None and self._help_win.winfo_exists():
            self._help_win.lift()
            self._help_win.focus_force()
            return
        win = tk.Toplevel(self.master)
        self._help_win = win
        win.title("STEGO — User Guide")
        win.configure(bg=BG)
        win.transient(self.master)
        win.minsize(520, 420)
        win.geometry("600x640")
        win.bind("<Escape>", lambda e: win.destroy())

        tk.Canvas(win, bg=ACCENT, height=2, highlightthickness=0).pack(fill="x")
        hdr = tk.Frame(win, bg=SURFACE, pady=10, padx=20)
        hdr.pack(fill="x")
        tk.Label(hdr, text="◈  USER GUIDE", bg=SURFACE, fg=LABEL_FG,
                 font=("Courier New", 14, "bold")).pack(side="left")
        tk.Label(hdr, text="[STEGO v1.01]", bg=SURFACE, fg=MUTED_FG,
                 font=("Courier New", 9)).pack(side="right")
        tk.Frame(win, bg=ACCENT, height=1).pack(fill="x")

        # scrollable body
        host = tk.Frame(win, bg=BG)
        host.pack(fill="both", expand=True)
        cv = tk.Canvas(host, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(host, orient="vertical", command=cv.yview, bg=SURFACE2,
                          troughcolor=BG, activebackground=PANEL_BD, bd=0, relief="flat")
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG, padx=24, pady=14)
        wid = cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(wid, width=e.width))

        def _wheel(e):
            cv.yview_scroll(-1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1, "units")
        win.bind("<MouseWheel>", _wheel)
        win.bind("<Button-4>", _wheel)
        win.bind("<Button-5>", _wheel)

        def section(title, accent, rows):
            card = tk.Frame(inner, bg=SURFACE, padx=14, pady=10,
                            highlightbackground=PANEL_BD, highlightthickness=1)
            card.pack(fill="x", pady=(0, 12))
            tk.Frame(card, bg=accent, height=2).pack(fill="x", pady=(0, 8))
            tk.Label(card, text=title, bg=SURFACE, fg=accent,
                     font=("Courier New", 11, "bold")).pack(anchor="w", pady=(0, 6))
            for tag, text in rows:
                r = tk.Frame(card, bg=SURFACE)
                r.pack(fill="x", pady=2)
                tk.Label(r, text=tag, width=3, anchor="n", bg=SURFACE, fg=accent,
                         font=("Courier New", 10, "bold")).pack(side="left", anchor="n")
                tk.Label(r, text=text, bg=SURFACE, fg=LABEL_FG, justify="left", anchor="w",
                         wraplength=440, font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True)

        section("▲  ENCODE", ACCENT, [
            ("01", "Choose a source image (PNG / JPG / BMP). Transparency is dropped."),
            ("02", "Type the secret message."),
            ("03", "Click ENCRYPT & SAVE and pick an export mode. Use Lossless (PNG/BMP); JPEG destroys "
                   "the hidden data and is rejected after verification."),
            ("04", "On the result screen, copy or save the key token. It cannot be recovered."),
            ("05", "Optional: SHOW HEATMAP for where data was embedded, or COVER vs STEGO for distortion metrics."),
        ])
        section("▼  DECODE", ACCENT3, [
            ("01", "Choose the encoded image — the exact file that was saved."),
            ("02", "Paste the 44-character key token."),
            ("03", "Click DECRYPT. The message is shown only if it passes authentication."),
        ])
        section("◆  SECURITY NOTES", WARN_FG, [
            ("·", "Key token: 32 random bytes. Anyone with the token and the image can read the message."),
            ("·", "Encryption: Fernet = AES-128-CBC + HMAC-SHA256 (authenticated)."),
            ("·", "Pixel positions come from a keyed ChaCha20 permutation derived from the token."),
            ("·", "Only the 2 least-significant bits of the blue channel change; image size is unchanged."),
            ("·", "Images made by STEGO v0.x are not compatible with this version."),
            ("·", "This does NOT make an image undetectable: statistical steganalysis can still flag LSB embedding."),
        ])
        section("VIEWS AND THEMES", ACCENT, [
            ("01", "The simple heatmap shows where the encoder selected pixels. Brightness is position count per cell, not message meaning."),
            ("02", "Advanced controls retain header/message views, opacity and cell size. Edge cells may be smaller; each view has its own color scale."),
            ("03", "Cover vs Stego reads the saved file for all formats. Advanced measurements include MAE, MSE, PSNR, amplified differences and the blue-channel histogram."),
            ("04", "Choose Cyber, Dark or Light from the Theme menu. This session-only choice never changes your message, token or image."),
        ])

        foot = tk.Frame(win, bg=BG, pady=10)
        foot.pack(fill="x")
        GlowButton(foot, "CLOSE", command=win.destroy, width=140, height=38,
                   accent=ACCENT).pack()
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        self.themes.apply_current(win)

    # ── shared UI helpers ─────────────────────────────────────────────────────

    def _browse_btn(self, parent, command, accent=ACCENT):
        accent = _theme_color(accent)
        btn = tk.Button(parent, text="BROWSE ▸", command=command,
                        bg=SURFACE2, fg=accent, relief="flat",
                        font=("Courier New", 10, "bold"),
                        cursor="hand2", padx=10, pady=6,
                        activebackground=ENTRY_BD,
                        activeforeground=accent, bd=0)
        btn.bind("<Enter>", lambda e: btn.config(bg=PANEL_BD))
        btn.bind("<Leave>", lambda e: btn.config(bg=SURFACE2))
        return btn

    def _clabel(self, parent, text, row, color=MUTED_FG):
        color = _theme_color(color)
        tk.Label(parent, text=text, bg=BG, fg=color,
                 font=("Courier New", 10, "bold")).grid(
                     row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _scroll_body(self, outer):
        host = tk.Frame(outer, bg=BG)
        host.pack(fill="both", expand=True)
        canvas = tk.Canvas(host, bg=BG, width=690, height=480, highlightthickness=0)
        scrollbar = tk.Scrollbar(host, command=canvas.yview, bg=SURFACE2, troughcolor=BG)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=BG, padx=24, pady=10)
        item = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))

        def wheel(event):
            widget = event.widget
            while widget is not None and widget is not host:
                widget = getattr(widget, "master", None)
            if widget is host and canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(-1 if (getattr(event, "num", 0) == 4 or event.delta > 0)
                                   else 1, "units")

        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.master.bind(event, wheel, add="+")
        return body

    # ── HOME ──────────────────────────────────────────────────────────────────

    def _build_home(self):
        frame = tk.Frame(self.container, bg=BG)
        tk.Canvas(frame, bg=ACCENT, height=2, highlightthickness=0).pack(fill="x")

        card = tk.Frame(frame, bg=SURFACE, bd=0, padx=48, pady=36)
        card.pack(expand=True)
        tk.Frame(card, bg=PANEL_BD, height=1).pack(fill="x", pady=(0, 24))

        logo = tk.Canvas(card, width=120, height=120, bg=SURFACE, highlightthickness=0)
        self._logo_canvas = logo
        logo.pack()
        self._draw_logo(logo)

        tk.Label(card, text="S T E G O", bg=SURFACE, fg=ACCENT,
                 font=("Courier New", 30, "bold")).pack(pady=(18, 2))
        tk.Label(card, text="STEGANOGRAPHY SUITE  v1.01", bg=SURFACE, fg=MUTED_FG,
                 font=("Courier New", 11)).pack()
        tk.Label(card, text="◆───────────────────◆", bg=SURFACE,
                 fg=PANEL_BD, font=("Courier New", 10)).pack(pady=(18, 8))
        tk.Label(card, text="",
                 bg=SURFACE, fg=MUTED_FG,
                 font=("Segoe UI", 12, "italic")).pack(pady=(0, 28))

        row = tk.Frame(card, bg=SURFACE)
        row.pack()
        GlowButton(row, "ENCODE IMAGE", icon="▲",
                   command=lambda: self._show(self.encode_frame),
                   width=178, height=46, accent=ACCENT).pack(side="left", padx=10)
        GlowButton(row, "DECODE IMAGE", icon="▼",
                   command=lambda: self._show(self.decode_frame),
                   width=178, height=46, accent=ACCENT3).pack(side="left", padx=10)
        tk.Frame(card, bg=PANEL_BD, height=1).pack(fill="x", pady=(28, 0))
        return frame

    # ── logo ──────────────────────────────────────────────────────────────────

    def _draw_logo(self, c):
        c.delete("all")
        try:
            from PIL import Image, ImageTk
            data    = base64.b64decode(self._LOGO_B64)
            pil_img = Image.open(io.BytesIO(data)).convert("RGBA")
            pil_img = pil_img.resize((120, 120), Image.LANCZOS)
            bg_img  = Image.new("RGB", pil_img.size, SURFACE)
            bg_img.paste(pil_img, mask=pil_img.split()[3])
            self._logo_img = ImageTk.PhotoImage(bg_img)
            c.create_image(60, 60, image=self._logo_img)
        except Exception:
            c.create_oval(10, 10, 110, 110, outline=ACCENT, width=2)
            c.create_text(60, 60, text="STEGO", fill=ACCENT,
                          font=("Courier New", 16, "bold"))

    # ── ENCODE ────────────────────────────────────────────────────────────────

    def _build_encode(self):
        outer = tk.Frame(self.container, bg=BG)
        tk.Canvas(outer, bg=ACCENT, height=2, highlightthickness=0).pack(fill="x")

        hdr = tk.Frame(outer, bg=SURFACE, pady=10, padx=20)
        hdr.pack(fill="x")
        tk.Button(hdr, text="◄  BACK", bg=SURFACE, fg=ACCENT,
                  relief="flat", font=("Courier New", 11, "bold"),
                  bd=0, cursor="hand2", activebackground=SURFACE,
                  activeforeground=BTN_HOV,
                  command=self._back_from_encode).pack(side="left")
        tk.Label(hdr, text="▲  ENCODE IMAGE", bg=SURFACE, fg=LABEL_FG,
                 font=("Courier New", 14, "bold")).pack(side="left", padx=16)
        tk.Label(hdr, text="[FERNET + KEYED SCATTER + 2-LSB]",
                 bg=SURFACE, fg=MUTED_FG,
                 font=("Courier New", 9)).pack(side="right", padx=8)
        tk.Frame(outer, bg=ACCENT, height=1).pack(fill="x")

        body = tk.Frame(outer, bg=BG, padx=30, pady=20)
        body.pack(expand=True)

        # Source image
        self._clabel(body, "[ SOURCE IMAGE ]", 0)
        self.src_path = tk.StringVar()
        r1 = tk.Frame(body, bg=BG)
        r1.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 12))
        CyberEntry(r1, textvariable=self.src_path, width=36).pack(side="left")
        self._browse_btn(r1, self._browse_source).pack(side="left", padx=(8, 0))

        # Message
        self._clabel(body, "[ SECRET MESSAGE ]", 2)
        self.secret_msg = tk.Text(
            body, height=5, width=44, font=("Segoe UI", 11),
            bg=ENTRY_BG, fg=LABEL_FG, insertbackground=ACCENT,
            relief="flat", bd=6, selectbackground=ACCENT, selectforeground=BG,
            highlightbackground=ENTRY_BD, highlightcolor=ACCENT, highlightthickness=1)
        self.secret_msg.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 12))

        # Encrypt button
        bf = tk.Frame(body, bg=BG)
        bf.grid(row=4, column=0, columnspan=3, pady=4)
        GlowButton(bf, "ENCRYPT  &  SAVE", icon="▲",
                   command=self.encrypt, width=220, height=46, accent=ACCENT).pack()

        body.columnconfigure(0, weight=1)
        return outer

    # ── DECODE ────────────────────────────────────────────────────────────────

    def _build_decode(self):
        outer = tk.Frame(self.container, bg=BG)
        tk.Canvas(outer, bg=ACCENT3, height=2, highlightthickness=0).pack(fill="x")

        hdr = tk.Frame(outer, bg=SURFACE, pady=10, padx=20)
        hdr.pack(fill="x")
        tk.Button(hdr, text="◄  BACK", bg=SURFACE, fg=ACCENT3,
                  relief="flat", font=("Courier New", 11, "bold"),
                  bd=0, cursor="hand2", activebackground=SURFACE,
                  activeforeground=BTN_HOV,
                  command=self._back_from_decode).pack(side="left")
        tk.Label(hdr, text="▼  DECODE IMAGE", bg=SURFACE, fg=LABEL_FG,
                 font=("Courier New", 14, "bold")).pack(side="left", padx=16)
        tk.Label(hdr, text="[EXTRACT + VERIFY + FERNET DECRYPT]",
                 bg=SURFACE, fg=MUTED_FG,
                 font=("Courier New", 9)).pack(side="right", padx=8)
        tk.Frame(outer, bg=ACCENT3, height=1).pack(fill="x")

        body = tk.Frame(outer, bg=BG, padx=30, pady=20)
        body.pack(expand=True)

        # Encoded image
        self._clabel(body, "[ ENCODED IMAGE ]", 0, ACCENT3)
        self.encrypted_path = tk.StringVar()
        r1 = tk.Frame(body, bg=BG)
        r1.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 12))
        CyberEntry(r1, textvariable=self.encrypted_path, width=36,
                   accent=ACCENT3).pack(side="left")
        self._browse_btn(r1, self._browse_encoded,
                         accent=ACCENT3).pack(side="left", padx=(8, 0))

        # Key token
        self._clabel(body, "[ DECRYPTION KEY TOKEN ]", 2, ACCENT3)
        self.decrypt_key = tk.StringVar()
        CyberEntry(body, textvariable=self.decrypt_key,
                   width=44, mono=True, accent=ACCENT3,
                   fg=KEY_FG, bg=KEY_BG).grid(
                       row=3, column=0, columnspan=3, sticky="ew", pady=(4, 12))

        # Decrypt button
        bf = tk.Frame(body, bg=BG)
        bf.grid(row=4, column=0, columnspan=3, pady=4)
        GlowButton(bf, "DECRYPT  MESSAGE", icon="▼",
                   command=self.decrypt, width=220, height=46, accent=ACCENT3).pack()

        # Result
        self._clabel(body, "[ DECRYPTED MESSAGE ]", 5, ACCENT3)
        rf = tk.Frame(body, bg=ENTRY_BD, padx=1, pady=1)
        rf.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 6))
        self.result_text = tk.Text(
            rf, height=3, width=44, font=("Segoe UI", 11),
            bg=RESULT_BG, fg=RESULT_FG, insertbackground=ACCENT3,
            relief="flat", bd=4, selectbackground=ACCENT3, selectforeground=BG,
            wrap="word", state="disabled")
        self.result_text.pack(fill="both")

        rb = tk.Frame(body, bg=BG)
        rb.grid(row=7, column=0, columnspan=3, sticky="w", pady=(2, 4))
        SmallBtn(rb, "⧉ COPY MESSAGE",
                 command=self._copy_result, accent=ACCENT3).pack(side="left")

        body.columnconfigure(0, weight=1)
        return outer

    # ── browse callbacks ──────────────────────────────────────────────────────

    def _choose_file(self, picker, *args, **kwargs):
        if self._busy or self._dialog_open or dialog_active():
            return ""
        self._dialog_open = True
        try:
            return picker(*args, parent=self.master, **kwargs)
        except Exception as exc:
            messagebox.showerror("File dialog", self._err_text(exc))
            return ""
        finally:
            self._dialog_open = False

    def _browse_source(self):
        p = self._choose_file(_native_open, "Select Source Image")
        if p:
            self.src_path.set(p)

    def _browse_encoded(self):
        p = self._choose_file(_native_open, "Select Encoded Image")
        if p:
            self.encrypted_path.set(p)

    # ── background runner (worker thread -> queue -> Tk main thread) ─────────

    def _run_bg(self, fn, on_ok, on_err, busy_msg):
        """Run fn() off the UI thread; on_ok/on_err are invoked on the Tk main thread."""
        if self._busy or self._dialog_open or self._closed:
            return False
        self._busy = True
        self._set_status(busy_msg, WARN_FG)

        def work():
            try:
                self._q.put(("ok", on_ok, fn()))
            except Exception as exc:
                self._q.put(("err", on_err, exc))

        threading.Thread(target=work, daemon=True).start()
        return True

    def _poll_queue(self):
        if self._closed:
            return
        try:
            while True:
                kind, cb, payload = self._q.get_nowait()
                self._busy = False
                try:
                    cb(payload)
                except Exception as e:               # a UI callback bug must not kill polling
                    self._set_status("Internal error.", ERROR_FG)
                    messagebox.showerror("Internal error", f"{type(e).__name__}: {e}")
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_id = self.master.after(60, self._poll_queue)

    @staticmethod
    def _err_text(exc):
        if isinstance(exc, StegoError):
            return str(exc)
        if isinstance(exc, MemoryError):
            return "Out of memory — the image is too large for this machine."
        return f"{type(exc).__name__}: {exc}"

    # ── key / output actions ──────────────────────────────────────────────────

    def _set_key_text(self, token: str):
        self._last_key_token = token.strip()
        self.key_text.config(state="normal")
        self.key_text.delete("1.0", tk.END)
        self.key_text.insert("1.0", self._last_key_token)
        self.key_text.config(state="disabled")

    def _copy_key(self):
        t = self._last_key_token
        if not t:
            messagebox.showwarning("No Key", "Encrypt a message first.")
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(t)
        self._token_secured = True
        self._set_status("Key token copied to clipboard.", ACCENT3)

    def _save_key_txt(self):
        t = self._last_key_token
        if not t:
            messagebox.showwarning("No Key", "Encrypt a message first.")
            return
        path = self._choose_file(_native_save_txt, "Save Decryption Key", "stego_key.txt")
        if path:
            try:
                if os.path.splitext(path)[1].lower() != ".txt":
                    raise StegoError("Choose a .txt extension for the key. Nothing was written.")
                _write_export(path, t.encode("utf-8"))
                self._token_secured = True
                self._set_status(f"Key saved → {os.path.basename(path)}", ACCENT3)
                _native_notify("Key Saved", f"Decryption key saved:\n{path}", parent=self.master)
            except Exception as e:
                messagebox.showerror("Error", f"Could not save key:\n{e}")

    def _open_image(self):
        p = self._last_saved_image
        if not p or not os.path.isfile(p):
            messagebox.showerror("Open image", "The saved image no longer exists at that path.")
            return
        try:
            if _is_win():
                os.startfile(p)                       # noqa: S606
            elif _is_mac():
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("Open image", f"Could not open the image:\n{e}")

    def _copy_result(self):
        txt = self.result_text.get("1.0", tk.END).strip()
        if not txt:
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(txt)
        self._set_status("Decrypted message copied to clipboard.", ACCENT3)

    def _leave_output(self, target):
        """Leave the output screen for good; warn if the token was never copied/saved."""
        if self._busy or self._dialog_open:
            return
        if self._last_key_token and not self._token_secured:
            if not messagebox.askyesno(
                    "Key token not saved",
                    "You have not copied or saved the key token.\n"
                    "Without it the encoded image CANNOT be decoded.\n\nLeave anyway?"):
                return
        self._result = None
        self._counts = {}
        self._last_key_token = ""
        self._token_secured = False
        self._hm_img = None
        self._photos.clear()
        self._hm_label.config(image="", text="(no heatmap yet)", width=60, height=16)
        self._hm_bar.config(image="")
        self._an_diff.config(image="")
        self._an_hist.config(image="")
        self.key_text.config(state="normal")
        self.key_text.delete("1.0", tk.END)
        self.key_text.config(state="disabled")
        self._reset_encode()
        self.src_path.set("")
        self._show(self.home_frame if target == "home" else self.encode_frame)

    # ── ENCODE flow ───────────────────────────────────────────────────────────

    def encrypt(self):
        if self._busy or self._dialog_open or dialog_active():
            return
        img_path = self.src_path.get().strip()
        msg      = self.secret_msg.get("1.0", tk.END).strip()
        if not img_path:
            messagebox.showerror("Error", "Please select a source image.")
            return
        if not msg:
            messagebox.showerror("Error", "Please enter a secret message.")
            return

        def job():
            return se.stego_encode(se.read_image(img_path), msg)

        self._run_bg(job, self._after_encode, self._encode_failed, "Encrypting…")

    def _encode_failed(self, exc):
        self._set_status("Encryption failed.", ERROR_FG)
        messagebox.showerror("Encryption Error", self._err_text(exc))

    def _after_encode(self, res):
        self._dialog_open = True
        try:
            mode = self._ask_export_mode()
            if not mode:
                self._set_status("Save cancelled - nothing was written.")
                return
            default_name = "secret_image.jpg" if mode == "standard" else "secret_image.png"
            save_path = _native_save_img("Save Encoded Image", default_name,
                                         parent=self.master, mode=mode)
            if not save_path:
                self._set_status("Save cancelled - nothing was written.")
                return
            allowed = se.LOSSY_EXT if mode == "standard" else se.LOSSLESS_EXT
            if os.path.splitext(save_path)[1].lower() not in allowed:
                raise StegoError("The file extension does not match the export mode. "
                                 "Choose " + " or ".join(allowed) + ". Nothing was written.")
        except Exception as exc:
            self._save_failed(exc)
            return
        finally:
            self._dialog_open = False
        self._run_bg(lambda: se.save_and_verify(res, save_path),
                     self._after_save, self._save_failed, "Saving and verifying…")

    def _save_failed(self, exc):
        self._set_status("Save failed - no new output committed.", ERROR_FG)
        messagebox.showerror("Save failed", self._err_text(exc))

    def _after_save(self, res):
        self._result = res
        self._counts = {}
        self._token_secured = False
        self._last_saved_image = res.output_path
        self._populate_output(res)
        self._set_status(f"Saved & verified → {os.path.basename(res.output_path)}", ACCENT3)
        self._show(self.output_frame)
        _native_notify("Encryption Complete",
                       f"Image saved and verified:\n{res.output_path}\n\n"
                       "Copy or save the key token — it cannot be recovered.",
                       parent=self.master)

    def _ask_export_mode(self) -> str:
        """Modal export-mode chooser. Returns 'standard' (JPEG, experimental), 'secure' (PNG/BMP) or ''."""
        if self._export_dlg_open:
            return ""
        self._export_dlg_open = True
        try:
            return self._build_export_mode()
        finally:
            self._export_dlg_open = False
            dlg = getattr(self, "_export_win", None)
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()

    def _build_export_mode(self):
        result = tk.StringVar(value="")
        dlg = tk.Toplevel(self.master)
        self._export_win = dlg
        dlg.title("Export Mode")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.transient(self.master)

        def _close(val):
            result.set(val)
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", lambda: _close(""))
        self.master.update_idletasks()
        px = self.master.winfo_x() + self.master.winfo_width() // 2
        py = self.master.winfo_y() + self.master.winfo_height() // 2
        dlg.geometry(f"+{px - 220}+{py - 120}")
        tk.Canvas(dlg, bg=ACCENT, height=2, highlightthickness=0).pack(fill="x")
        body = tk.Frame(dlg, bg=BG, padx=28, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="SELECT EXPORT MODE", bg=BG, fg=ACCENT,
                 font=("Courier New", 13, "bold")).pack(anchor="w", pady=(0, 14))
        choice = tk.StringVar(value="secure")
        for val, label, desc in [
            ("secure",   "Lossless  (PNG / BMP)  ★", "Recommended — Payload remains intact"),
            ("standard", "JPEG  (experimental)",     "Lossy compression may destroy the hidden data"),
        ]:
            row = tk.Frame(body, bg=SURFACE2, padx=12, pady=8,
                           highlightbackground=PANEL_BD, highlightthickness=1)
            row.pack(fill="x", pady=4)
            tk.Radiobutton(row, variable=choice, value=val, bg=SURFACE2,
                           activebackground=SURFACE2, selectcolor=BG, fg=LABEL_FG,
                           font=("Courier New", 11, "bold"), text=label,
                           highlightthickness=0, bd=0, cursor="hand2").pack(side="left")
            tk.Label(row, text=f"  —  {desc}", bg=SURFACE2, fg=MUTED_FG,
                     font=("Courier New", 9)).pack(side="left")
        warn = tk.Label(body, bg=BG, fg=WARN_FG, font=("Courier New", 9),
                         text="The saved image must decode to the intended message.\n"
                              "If verification fails, no new file replaces your destination.\n"
                              "PNG is recommended: it preserves every encoded pixel.")

        def _on_change(*_):
            if choice.get() == "standard":
                warn.pack(anchor="w", pady=(4, 0))
            else:
                warn.pack_forget()

        choice.trace_add("write", _on_change)
        btn_row = tk.Frame(body, bg=BG)
        btn_row.pack(pady=(16, 0))
        GlowButton(btn_row, "CONFIRM", command=lambda: _close(choice.get()),
                   width=120, height=38, accent=ACCENT).pack(side="left", padx=(0, 10))
        GlowButton(btn_row, "CANCEL", command=lambda: _close(""),
                    width=100, height=38, accent=MUTED_FG).pack(side="left")
        dlg.bind("<Escape>", lambda e: _close(""))
        self.themes.apply_current(dlg)
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
        return result.get()

    # ── DECODE flow ───────────────────────────────────────────────────────────

    def decrypt(self):
        if self._busy or self._dialog_open or dialog_active():
            return
        self._set_result_text("")
        img_path = self.encrypted_path.get().strip()
        token    = self.decrypt_key.get().strip()
        if not img_path:
            messagebox.showerror("Error", "Please select an encoded image.")
            return
        if not token:
            messagebox.showerror("Error", "Please paste the decryption key token.")
            return
        def job():
            return se.stego_decode(se.read_image(img_path), token)

        self._run_bg(job, self._finish_decrypt, self._decrypt_failed, "Decrypting…")

    def _decrypt_failed(self, exc):
        self._set_result_text("")                    # never leave stale plaintext visible
        self._set_status("Decryption failed.", ERROR_FG)
        messagebox.showerror("Decryption Failed", self._err_text(exc))

    def _set_result_text(self, text):
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        if text:
            self.result_text.insert("1.0", text)
        self.result_text.config(state="disabled")

    def _finish_decrypt(self, plaintext):
        self._set_result_text(plaintext)
        self._set_status("Decryption successful — message authenticated and revealed.", ACCENT3)

    # ── OUTPUT screen ─────────────────────────────────────────────────────────

    def _build_output(self):
        outer = tk.Frame(self.container, bg=BG)
        tk.Canvas(outer, bg=ACCENT3, height=2, highlightthickness=0).pack(fill="x")
        hdr = tk.Frame(outer, bg=SURFACE, pady=10, padx=20)
        hdr.pack(fill="x")
        tk.Button(hdr, text="◄  HOME", bg=SURFACE, fg=ACCENT3, relief="flat",
                  font=("Courier New", 11, "bold"), bd=0, cursor="hand2",
                  activebackground=SURFACE, activeforeground=BTN_HOV,
                  command=lambda: self._leave_output("home")).pack(side="left")
        tk.Label(hdr, text="✔  ENCODING VERIFIED", bg=SURFACE, fg=ACCENT3,
                 font=("Courier New", 14, "bold")).pack(side="left", padx=16)
        tk.Frame(outer, bg=ACCENT3, height=1).pack(fill="x")

        body = tk.Frame(outer, bg=BG, padx=30, pady=14)
        body.pack(expand=True)

        self._out = {}
        card = tk.Frame(body, bg=SURFACE, padx=14, pady=10,
                        highlightbackground=PANEL_BD, highlightthickness=1)
        card.pack(fill="x")
        for key, label in [("file", "FILE"), ("fmt", "FORMAT"), ("dims", "DIMENSIONS"),
                           ("method", "METHOD"), ("used", "PIXELS USED"),
                           ("bits", "CHANNEL BITS"), ("payload", "PAYLOAD")]:
            r = tk.Frame(card, bg=SURFACE)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label, width=13, anchor="w", bg=SURFACE, fg=MUTED_FG,
                     font=("Courier New", 9, "bold")).pack(side="left", anchor="n")
            v = tk.Label(r, text="", anchor="w", justify="left", wraplength=400,
                         bg=SURFACE, fg=LABEL_FG, font=("Courier New", 9))
            v.pack(side="left", fill="x", expand=True)
            self._out[key] = v
        tk.Label(card, text="The saved file was re-read from disk and decoded successfully.",
                 bg=SURFACE, fg=ACCENT3, font=("Courier New", 9), anchor="w").pack(fill="x", pady=(6, 0))

        tk.Label(body, text="[ DECRYPTION KEY TOKEN  —  required to decode, not recoverable ]",
                 bg=BG, fg=WARN_FG, font=("Courier New", 10, "bold")).pack(anchor="w", pady=(12, 0))
        kf = tk.Frame(body, bg=ENTRY_BD, padx=1, pady=1)
        kf.pack(fill="x", pady=(4, 4))
        self.key_text = tk.Text(kf, height=1, width=48, font=("Courier New", 11),
                                bg=KEY_BG, fg=KEY_FG, insertbackground=ACCENT3, relief="flat",
                                bd=4, selectbackground=ACCENT3, selectforeground=BG,
                                wrap="none", state="disabled")
        self.key_text.pack(fill="both")
        kb = tk.Frame(body, bg=BG)
        kb.pack(anchor="w", pady=(2, 8))
        SmallBtn(kb, "⧉ COPY KEY", command=self._copy_key, accent=ACCENT3).pack(side="left", padx=(0, 6))
        SmallBtn(kb, "💾 SAVE KEY AS TXT", command=self._save_key_txt, accent=WARN_FG).pack(side="left")

        row = tk.Frame(body, bg=BG)
        row.pack(pady=(6, 4))
        GlowButton(row, "SHOW HEATMAP", icon="▦", command=self._show_heatmap,
                   width=200, height=44, accent=ACCENT).pack(side="left", padx=6)
        GlowButton(row, "COVER vs STEGO", icon="◧", command=self._show_analysis,
                   width=200, height=44, accent=ACCENT3).pack(side="left", padx=6)
        row2 = tk.Frame(body, bg=BG)
        row2.pack(pady=(2, 0))
        SmallBtn(row2, "↗ OPEN IMAGE", command=self._open_image, accent=ACCENT).pack(side="left", padx=4)
        SmallBtn(row2, "+ NEW ENCODING", command=lambda: self._leave_output("encode"),
                 accent=ACCENT).pack(side="left", padx=4)
        return outer

    def _populate_output(self, r):
        h, w = r.shape[:2]
        o = self._out
        o["file"].config(text=r.output_path)
        o["fmt"].config(text=f"{r.output_format}  ·  {r.output_bytes:,} bytes on disk")
        o["dims"].config(text=f"{w} × {h} px  (unchanged from cover)")
        o["method"].config(text=r.method)
        o["used"].config(text=f"{r.n_pixels_used:,} of {r.n_pixels_total:,} pixels touched "
                              f"({r.pixel_fraction*100:.2f}%)  =  {r.n_header_pixels} header "
                              f"+ {r.n_pixels_used - r.n_header_pixels:,} payload")
        o["bits"].config(text=f"{r.n_pixels_used*2:,} of {r.n_pixels_total*24:,} channel bits "
                              f"allocated ({r.channel_bit_fraction*100:.3f}%); each allocated "
                              "bit pair may or may not flip")
        o["payload"].config(text=f"{r.plaintext_bytes:,} B text → {r.ciphertext_bytes:,} B Fernet "
                                 f"ciphertext + {se.HEADER_BYTES} B header")
        self._set_key_text(r.token)

    # ── HEATMAP screen ────────────────────────────────────────────────────────

    def _build_heatmap(self):
        outer = tk.Frame(self.container, bg=BG)
        tk.Canvas(outer, bg=ACCENT, height=2, highlightthickness=0).pack(fill="x")
        hdr = tk.Frame(outer, bg=SURFACE, pady=10, padx=20)
        hdr.pack(fill="x")
        tk.Button(hdr, text="◄  OUTPUT", bg=SURFACE, fg=ACCENT, relief="flat",
                  font=("Courier New", 11, "bold"), bd=0, cursor="hand2",
                  activebackground=SURFACE, activeforeground=BTN_HOV,
                  command=lambda: self._show(self.output_frame)).pack(side="left")
        tk.Label(hdr, text="▦  EMBEDDING DENSITY", bg=SURFACE, fg=LABEL_FG,
                 font=("Courier New", 14, "bold")).pack(side="left", padx=16)
        tk.Frame(outer, bg=ACCENT, height=1).pack(fill="x")
        body = self._scroll_body(outer)

        self._hm_mode  = tk.StringVar(value="overlay")
        self._hm_scope = tk.StringVar(value="all")
        self._hm_bin   = tk.IntVar(value=0)            # 0 = auto
        self._hm_alpha = tk.IntVar(value=60)
        self._hm_advanced = False
        tk.Label(body, text="Where data was placed", bg=BG, fg=LABEL_FG,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(body, text="The color scale counts pixels selected to carry hidden data, not meaning.\n"
                           "The cover also affects brightness in an overlay; use the legend.",
                 bg=BG, fg=MUTED_FG, justify="left", font=("Segoe UI", 10)).pack(anchor="w")
        self._hm_toggle = SmallBtn(body, "Show advanced controls", self._toggle_heatmap)
        self._hm_toggle.pack(anchor="w", pady=4)
        self._hm_controls = controls = tk.Frame(body, bg=BG)

        def radios(parent, var, items):
            for val, text in items:
                tk.Radiobutton(parent, variable=var, value=val, text=text, bg=BG, fg=LABEL_FG,
                               activebackground=BG, activeforeground=ACCENT, selectcolor=SURFACE2,
                               font=("Courier New", 9), highlightthickness=0, bd=0,
                               cursor="hand2").pack(side="left", padx=(0, 8))

        c1 = tk.Frame(controls, bg=BG); c1.pack(anchor="w")
        tk.Label(c1, text="VIEW", width=7, anchor="w", bg=BG, fg=MUTED_FG,
                 font=("Courier New", 9, "bold")).pack(side="left")
        radios(c1, self._hm_mode, [("overlay", "Overlay on cover"), ("density", "Density only")])
        c2 = tk.Frame(controls, bg=BG); c2.pack(anchor="w")
        tk.Label(c2, text="SHOW", width=7, anchor="w", bg=BG, fg=MUTED_FG,
                 font=("Courier New", 9, "bold")).pack(side="left")
        radios(c2, self._hm_scope, [("all", "All positions"), ("payload", "Payload only"),
                                    ("header", "Header only")])
        c3 = tk.Frame(controls, bg=BG); c3.pack(anchor="w", pady=(2, 0))
        tk.Label(c3, text="CELL px", width=7, anchor="w", bg=BG, fg=MUTED_FG,
                 font=("Courier New", 9, "bold")).pack(side="left")
        tk.Spinbox(c3, from_=0, to=1024, width=5, textvariable=self._hm_bin, bg=ENTRY_BG,
                   fg=LABEL_FG, insertbackground=ACCENT, relief="flat", bd=2,
                   buttonbackground=SURFACE2, font=("Courier New", 9)).pack(side="left")
        tk.Label(c3, text="(0 = auto)   OPACITY", bg=BG, fg=MUTED_FG,
                 font=("Courier New", 9)).pack(side="left", padx=(6, 4))
        tk.Scale(c3, from_=20, to=90, orient="horizontal", variable=self._hm_alpha, length=110,
                 bg=BG, fg=LABEL_FG, troughcolor=SURFACE2, highlightthickness=0, bd=0,
                 font=("Courier New", 8)).pack(side="left")
        SmallBtn(c3, "APPLY", command=self._render_heatmap, accent=ACCENT).pack(side="left", padx=8)

        mid = tk.Frame(body, bg=SURFACE, highlightbackground=PANEL_BD, highlightthickness=1)
        self._hm_preview_frame = mid
        mid.pack(pady=8)
        self._hm_label = tk.Label(mid, bg=SURFACE, fg=MUTED_FG, text="(no heatmap yet)",
                                  width=60, height=16, font=("Courier New", 9))
        self._hm_label.pack(side="left", padx=6, pady=6)
        legend = tk.Frame(mid, bg=SURFACE)
        legend.pack(side="left", padx=(0, 10), pady=6, fill="y")
        self._hm_max = tk.Label(legend, text="", bg=SURFACE, fg=LABEL_FG, font=("Courier New", 8))
        self._hm_max.pack()
        self._hm_bar = tk.Label(legend, bg=SURFACE)
        self._hm_bar.pack(expand=True)
        self._hm_min = tk.Label(legend, text="", bg=SURFACE, fg=LABEL_FG, font=("Courier New", 8))
        self._hm_min.pack()

        self._hm_info = tk.Label(body, text="", bg=BG, fg=LABEL_FG, justify="left", anchor="w",
                                 wraplength=620, font=("Segoe UI", 11))
        self._hm_info.pack(anchor="w")
        tk.Label(body, justify="left", anchor="w", wraplength=520, bg=BG, fg=MUTED_FG,
                 font=("Courier New", 8),
                 text="Placement map, not a steganalysis detector or proof of stealth. "
                      "Colors run from the lowest to highest count in this view; edge cells "
                      "can be smaller. Changing cell size or scope changes this relative scale. "
                      "The original cover and encoded pixels are never changed.").pack(anchor="w", pady=(4, 4))
        SmallBtn(body, "💾 SAVE HEATMAP", command=self._save_heatmap, accent=WARN_FG).pack(anchor="w")
        return outer

    def _toggle_heatmap(self):
        if self._busy or self._dialog_open:
            return
        self._hm_advanced = not self._hm_advanced
        if self._hm_advanced:
            self._hm_controls.pack(anchor="w", before=self._hm_preview_frame)
        else:
            self._hm_controls.pack_forget()
        self._hm_toggle.config(text="Use simple view" if self._hm_advanced else "Show advanced controls")
        self._render_heatmap()

    def _show_heatmap(self):
        if self._busy or self._dialog_open:
            return
        r = self._result
        if r is None or r.positions is None or len(r.positions) == 0:
            messagebox.showerror("Heatmap", "No embedding positions are available for this "
                                            "encoding, so a heatmap cannot be shown.")
            return
        if self.heatmap_frame.winfo_ismapped():
            return
        self._show(self.heatmap_frame)
        self._render_heatmap()

    def _render_heatmap(self):
        r = self._result
        if r is None or self._busy or self._dialog_open:
            return
        mode, scope = self._hm_mode.get(), self._hm_scope.get()
        try:
            req_bin = int(self._hm_bin.get())
        except (tk.TclError, ValueError):
            req_bin = 0
        bin_px = req_bin if req_bin > 0 else se.auto_bin(r.shape)
        bin_px = max(1, min(bin_px, max(r.shape[:2])))
        alpha = self._hm_alpha.get() / 100.0
        if not self._hm_advanced:
            mode, scope, bin_px, alpha = "overlay", "all", se.auto_bin(r.shape), 0.6

        def job():
            pos = {"all": r.positions, "payload": r.payload_positions,
                   "header": r.header_positions}[scope]
            binned = se.position_density(r.shape, pos, bin_px)
            img, vmin, vmax = se.render_heatmap(r.cover, binned, bin_px,
                                                overlay=(mode == "overlay"), alpha=alpha,
                                                max_dim=560)
            # These two headlines need no full-resolution float64 error arrays.
            changed, maximum = 0, 0
            for y in range(0, r.shape[0], 128):
                delta = cv2.absdiff(r.cover[y:y+128], r.img_out[y:y+128])
                changed += cv2.countNonZero(delta.max(axis=2))
                maximum = max(maximum, int(delta.max()))
            metrics = {"changed_fraction": changed / r.n_pixels_total,
                       "max_abs_diff": maximum}
            return (img, vmin, vmax, bin_px, len(pos), scope, mode,
                    int((binned > 0).sum()), binned.size, metrics)

        self._run_bg(job, self._after_heatmap, self._heatmap_failed, "Building heatmap…")

    def _heatmap_failed(self, exc):
        self._hm_img = None
        self._hm_label.config(image="", text="(heatmap unavailable)")
        self._hm_bar.config(image="")
        self._hm_max.config(text=""); self._hm_min.config(text=""); self._hm_info.config(text="")
        self._set_status("Heatmap failed.", ERROR_FG)
        messagebox.showerror("Heatmap", f"Could not generate the heatmap:\n{self._err_text(exc)}")

    def _after_heatmap(self, out):
        img, vmin, vmax, bin_px, n_pos, scope, mode, occupied, cells, metrics = out
        self._hm_img = img
        self._photos["hm"] = tk.PhotoImage(data=se.to_png_b64(img))
        bar = se.colorbar(img.shape[0])
        if vmin == vmax:
            bar[:] = bar[0] if vmax > 0 else bar[-1]
        self._photos["bar"] = tk.PhotoImage(data=se.to_png_b64(bar))
        self._hm_label.config(image=self._photos["hm"], text="", width=img.shape[1],
                              height=img.shape[0])
        self._hm_bar.config(image=self._photos["bar"])
        self._hm_max.config(text=f"More\n{vmax:g} pixels")
        self._hm_min.config(text=f"Fewer\n{vmin:g} pixels")
        r = self._result
        self._hm_info.config(
            text=f"Pixels used / area used: {n_pos:,} of {r.n_pixels_total:,} "
                 f"({100*n_pos/r.n_pixels_total:.2f}%) in this {scope} view.\n"
                 f"Message area: {len(r.payload_positions):,} pixels; header: {r.n_header_pixels:,}.\n"
                 f"Distribution: {occupied:,} of {cells:,} cells contain selected pixels.\n"
                 f"Each cell covers up to {bin_px} x {bin_px} image pixels. "
                 f"{'Overlay on original cover' if mode == 'overlay' else 'Placement density only'}.\n"
                 f"In-memory visual difference: at most {metrics['max_abs_diff']}/255 per channel; "
                 f"{metrics['changed_fraction']*100:.2f}% of pixels changed. "
                 "Open Cover vs Stego to measure the saved file.")
        self._set_status("Heatmap ready.", ACCENT3)
        self._show(self.heatmap_frame)

    def _save_heatmap(self):
        if self._busy or self._dialog_open:
            return
        if self._hm_img is None:
            messagebox.showwarning("Save heatmap", "Generate a heatmap first.")
            return
        path = self._choose_file(_native_save_img, "Save Heatmap", "stego_heatmap.png")
        if not path:
            return
        if not path.lower().endswith((".png", ".bmp")):
            messagebox.showerror("Save heatmap", "Choose a .png or .bmp extension. Nothing was written.")
            return
        image = self._hm_img

        def job():
            ok, buf = cv2.imencode(os.path.splitext(path)[1].lower(), image)
            if not ok:
                raise StegoError("Heatmap image encoder failed.")
            _write_export(path, buf.tobytes())
            return path

        def failed(exc):
            self._set_status("Heatmap save failed.", ERROR_FG)
            messagebox.showerror("Save heatmap", self._err_text(exc))

        self._run_bg(job, lambda p: self._set_status(
            f"Heatmap saved → {os.path.basename(p)}", ACCENT3), failed, "Saving heatmap...")

    # ── ANALYSIS screen ───────────────────────────────────────────────────────

    def _build_analysis(self):
        outer = tk.Frame(self.container, bg=BG)
        tk.Canvas(outer, bg=ACCENT3, height=2, highlightthickness=0).pack(fill="x")
        hdr = tk.Frame(outer, bg=SURFACE, pady=10, padx=20)
        hdr.pack(fill="x")
        tk.Button(hdr, text="◄  OUTPUT", bg=SURFACE, fg=ACCENT3, relief="flat",
                  font=("Courier New", 11, "bold"), bd=0, cursor="hand2",
                  activebackground=SURFACE, activeforeground=BTN_HOV,
                  command=lambda: self._show(self.output_frame)).pack(side="left")
        tk.Label(hdr, text="◧  COVER vs STEGO", bg=SURFACE, fg=LABEL_FG,
                 font=("Courier New", 14, "bold")).pack(side="left", padx=16)
        tk.Frame(outer, bg=ACCENT3, height=1).pack(fill="x")
        body = self._scroll_body(outer)
        self._an_advanced = False
        tk.Label(body, text="What changed in the saved image?", bg=BG, fg=LABEL_FG,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 8))
        self._an_metrics = tk.Label(body, text="", justify="left", anchor="w", bg=SURFACE,
                                    fg=LABEL_FG, font=("Segoe UI", 11), padx=10, pady=10,
                                    wraplength=600)
        self._an_metrics.pack(fill="x")
        self._an_toggle = SmallBtn(body, "Show advanced measurements", self._toggle_analysis)
        self._an_toggle.pack(anchor="w", pady=6)
        self._an_controls = advanced = tk.Frame(body, bg=BG)
        self._an_detail = tk.Label(advanced, text="", justify="left", bg=BG, fg=LABEL_FG,
                                   font=("Courier New", 9), wraplength=600)
        self._an_detail.pack(anchor="w")
        tk.Label(advanced, text="[ AMPLIFIED DIFFERENCE  (x85, display only) ]", bg=BG, fg=MUTED_FG,
                 font=("Courier New", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self._an_diff = tk.Label(advanced, bg=SURFACE)
        self._an_diff.pack(anchor="w")
        tk.Label(advanced, text="[ BLUE-CHANNEL HISTOGRAM  dim = cover, bright = saved image ]", bg=BG,
                 fg=MUTED_FG, font=("Courier New", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self._an_hist = tk.Label(advanced, bg=SURFACE)
        self._an_hist.pack(anchor="w")
        tk.Label(body, justify="left", anchor="w", wraplength=520, bg=BG, fg=MUTED_FG,
                 font=("Courier New", 8), text=
                 "MAE / MSE / PSNR measure visual distortion only. Histograms and low pixel error "
                 "do not establish that the image is statistically undetectable; chi-square and "
                 "RS-style steganalysis can still flag LSB embedding.").pack(anchor="w", pady=(6, 0))
        return outer

    def _toggle_analysis(self):
        self._an_advanced = not self._an_advanced
        if self._an_advanced:
            self._an_controls.pack(anchor="w", after=self._an_toggle)
        else:
            self._an_controls.pack_forget()
        self._an_toggle.config(text="Use simple view" if self._an_advanced
                               else "Show advanced measurements")
        self._show(self.analysis_frame)

    def _show_analysis(self):
        if self._busy or self._dialog_open:
            return
        r = self._result
        if r is None or r.cover is None:
            messagebox.showerror("Analysis", "The original cover image is not available.")
            return

        def job():
            if not r.output_path:
                raise StegoError("No saved output is available for comparison.")
            stego = se.read_image(r.output_path)
            return (se.compare(r.cover, stego), se.diff_image(r.cover, stego, 300),
                    se.histogram_plot(r.cover, stego, (480, 130)))

        if self._run_bg(job, self._after_analysis, self._analysis_failed, "Analysing…"):
            self._an_metrics.config(text="Reading the saved file and measuring differences...")
            self._show(self.analysis_frame, force=True)

    def _analysis_failed(self, exc):
        self._an_metrics.config(text="Saved image could not be analysed.")
        self._an_detail.config(text="")
        self._an_diff.config(image="")
        self._an_hist.config(image="")
        self._set_status("Analysis failed.", ERROR_FG)
        self._show(self.output_frame)
        messagebox.showerror("Analysis", self._err_text(exc))

    def _after_analysis(self, out):
        m, diff, hist = out
        r = self._result
        psnr = "No pixel differences" if m["psnr"] == float("inf") else f"{m['psnr']:.2f} dB"
        self._an_metrics.config(text=
            f"Compared: original cover vs saved/reopened {r.output_format} file\n"
            f"{r.output_path}\n\n"
            f"Pixels used: {r.n_pixels_used:,} of {r.n_pixels_total:,} ({r.pixel_fraction*100:.2f}%)\n"
            "These are selected positions, not necessarily changed pixels.\n\n"
            f"Changed pixels: {m['changed_pixels']:,} of {r.n_pixels_total:,} "
            f"({m['changed_fraction']*100:.2f}%) differ in at least one channel.\n\n"
            f"Visual difference: at most {m['max_abs_diff']}/255 per channel; "
            f"average absolute change {m['mae']:.5f}/255 across all channels.\n\n"
            f"Image similarity (PSNR): {psnr}\n"
            "Higher PSNR means lower measured pixel error, not stronger security.")
        self._an_detail.config(text=
            f"{r.shape[1]} x {r.shape[0]} pixels; each pixel has 3 channels.\n"
            f"MAE {m['mae']:.5f} intensity levels; MSE {m['mse']:.5f} squared levels.\n"
            f"Both average over all {r.n_pixels_total*3:,} channel values (range 0-255).\n"
            f"PSNR {m['psnr']:.5f} dB, peak=255; infinite when MSE=0.\n"
            "Difference: maximum absolute channel change, amplified x85 and clipped.\n"
            "Blue histogram: intensity 0-255 left to right, counts on a shared relative scale.")
        self._photos["diff"] = tk.PhotoImage(data=se.to_png_b64(diff))
        self._photos["hist"] = tk.PhotoImage(data=se.to_png_b64(hist))
        self._an_diff.config(image=self._photos["diff"])
        self._an_hist.config(image=self._photos["hist"])
        self._set_status("Analysis ready.", ACCENT3)
        self._show(self.analysis_frame)


# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    root.configure(bg=BG)
    StegoApp(root)
    root.mainloop()
