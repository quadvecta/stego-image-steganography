"""Run: python -m unittest discover -s outputs -p test_themes.py -v

Palette tests need no display. Tk tests skip explicitly if Tk cannot open one;
use Xvfb on headless Linux. Only the real-GUI test needs the engine dependencies.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest

from stego_themes import THEMES, ThemeManager


def contrast(first, second):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


class PaletteTests(unittest.TestCase):
    def test_complete_immutable_palettes_and_aligned_aliases(self):
        roles = set("BG SURFACE SURFACE2 PANEL_BD ACCENT ACCENT3 BTN_FG BTN_HOV "
                    "LABEL_FG MUTED_FG ENTRY_BG ENTRY_BD KEY_BG KEY_FG RESULT_BG "
                    "RESULT_FG STATUS_BG STATUS_FG WARN_FG ERROR_FG DISABLED_FG".split())
        self.assertEqual(set(THEMES), {"Cyber", "Dark", "Light"})
        for palette in THEMES.values():
            self.assertEqual(set(palette), roles)
            for color in palette.values():
                self.assertRegex(color, r"^#[0-9a-f]{6}$")
            self.assertEqual(palette["KEY_FG"], palette["ACCENT3"])
            self.assertEqual(palette["RESULT_FG"], palette["ACCENT3"])
            with self.assertRaises(TypeError):
                palette["BG"] = "#000000"
        with self.assertRaises(TypeError):
            THEMES["Other"] = {}

    def test_cyber_preserves_all_existing_gui_globals(self):
        path = Path(__file__).with_name("stego_test_file2_newuserguide.py")
        legacy_roles = set(THEMES["Cyber"]) - {"ERROR_FG", "DISABLED_FG"}
        original = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in legacy_roles:
                        original[target.id] = node.value.value
        self.assertEqual(set(original), legacy_roles)
        for role, color in original.items():
            self.assertEqual(THEMES["Cyber"][role], color, role)

    def test_main_text_contrast(self):
        for name, p in THEMES.items():
            pairs = [(fg, bg) for fg in ("LABEL_FG", "MUTED_FG", "ACCENT", "ACCENT3",
                                         "WARN_FG", "ERROR_FG", "DISABLED_FG")
                     for bg in ("BG", "SURFACE", "SURFACE2", "ENTRY_BG")]
            pairs += [("KEY_FG", "KEY_BG"), ("RESULT_FG", "RESULT_BG")]
            pairs += [("BTN_FG", bg) for bg in ("ACCENT", "ACCENT3", "BTN_HOV")]
            if name != "Cyber":
                pairs.append(("STATUS_FG", "STATUS_BG"))
            for fg, bg in pairs:
                with self.subTest(theme=name, foreground=fg, background=bg):
                    self.assertGreaterEqual(contrast(p[fg], p[bg]), 4.5)
            for role in ("ACCENT", "ACCENT3", "MUTED_FG", "WARN_FG"):
                # The existing GlowButton hover fill darkens its accent by 15%.
                hover = "#" + "".join(f"{int(int(p[role][i:i + 2], 16) * .85):02x}"
                                      for i in (1, 3, 5))
                self.assertGreaterEqual(contrast(p["BTN_FG"], hover), 4.5, (name, role))
        cyber = THEMES["Cyber"]
        # Exact compatibility exception, not a claim that all Cyber text is AA.
        self.assertAlmostEqual(contrast(cyber["STATUS_FG"], cyber["STATUS_BG"]), 4.30, places=2)


class TkThemeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable (run under Xvfb): {exc}")
        self.addCleanup(self._destroy_root)
        self.root.geometry("520x420+0+0")
        self.p = THEMES["Cyber"]
        self.gui = SimpleNamespace(**self.p)

    def _destroy_root(self):
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.root.update_idletasks()
        self.root.destroy()

    def test_entry_text_state_layout_focus_selection_and_undo_survive(self):
        root, p = self.root, self.p
        var = tk.StringVar(root, "session token")
        entry = tk.Entry(root, textvariable=var, bg=p["KEY_BG"], fg=p["KEY_FG"],
                         readonlybackground=p["KEY_BG"], state="readonly",
                         selectbackground=p["ACCENT3"], selectforeground=p["BG"],
                         exportselection=False)
        entry.grid(row=0, column=0, sticky="ew", padx=7)
        entry.selection_range(1, 5)
        text = tk.Text(root, bg=p["ENTRY_BG"], fg=p["LABEL_FG"], undo=True,
                       height=5, width=25, exportselection=False)
        text.grid(row=1, column=0)
        text.insert("1.0", "\n".join(f"line {i}" for i in range(50)))
        text.edit_separator()
        text.mark_set("insert", "12.3")
        text.tag_add("sel", "2.0", "2.4")
        text.configure(state="disabled")
        hidden = tk.Frame(root, bg=p["BG"])
        hidden.grid(row=2, column=0)
        hidden.grid_remove()
        calls = []
        button = tk.Button(root, text="Run", command=lambda: calls.append(True),
                           bg=p["SURFACE2"], fg=p["ACCENT"])
        button.grid(row=3, column=0)
        button.bind("<Enter>", lambda event: None)
        root.update()
        entry.focus_force()
        root.update()
        text.yview_moveto(.4)
        root.update_idletasks()
        snapshot = (entry.grid_info(), text.grid_info(), root.geometry(), root.focus_get(),
                    text.yview(), text.get("1.0", "end"), text.index("insert"),
                    text.tag_ranges("sel"), button.cget("command"), button.bind("<Enter>"))
        themes = ThemeManager(root, self.gui)
        for name in ("Light", "Dark", "Cyber", "Light"):
            themes.set_theme(name)
            q = THEMES[name]
            root.update_idletasks()
            self.assertEqual((entry.grid_info(), text.grid_info(), root.geometry(), root.focus_get(),
                              text.yview(), text.get("1.0", "end"), text.index("insert"),
                              text.tag_ranges("sel"), button.cget("command"), button.bind("<Enter>")),
                             snapshot)
            self.assertEqual(var.get(), "session token")
            self.assertEqual((entry.index("sel.first"), entry.index("sel.last")), (1, 5))
            self.assertEqual(entry.cget("state"), "readonly")
            self.assertEqual(text.cget("state"), "disabled")
            self.assertFalse(hidden.winfo_ismapped())
            self.assertEqual(entry.cget("readonlybackground"), q["KEY_BG"])
            self.assertEqual(entry.cget("selectforeground"), q["BTN_FG"])
            self.assertEqual(entry.cget("disabledforeground"), q["DISABLED_FG"])
            self.assertEqual(text.cget("selectbackground"), q["ACCENT"])
        button.invoke()
        self.assertEqual(calls, [True])
        text.configure(state="normal")
        text.edit_undo()
        self.assertEqual(text.get("1.0", "end-1c"), "")

    def test_canvas_items_and_images_are_not_recreated(self):
        p = self.p
        canvas = tk.Canvas(self.root, bg=p["BG"])
        canvas.pack()
        rect = canvas.create_rectangle(1, 2, 30, 40, fill=p["BG"], outline=p["ACCENT"],
                                       activefill=p["ACCENT3"], disabledoutline=p["WARN_FG"],
                                       state="disabled", tags=("keep",), width=3)
        text = canvas.create_text(10, 10, fill=p["BTN_FG"], text="button")
        empty = canvas.create_rectangle(0, 0, 5, 5, fill="", outline="#123456")
        photo = tk.PhotoImage(master=self.root, width=2, height=2)
        photo.put("#00d4ff", to=(0, 0, 2, 2))
        image = canvas.create_image(5, 5, image=photo)
        label = tk.Label(self.root, image=photo, bg=p["SURFACE"])
        label.pack()
        before = (canvas.find_all(), canvas.coords(rect), canvas.gettags(rect),
                  canvas.itemcget(image, "image"), label.cget("image"), photo.get(0, 0))
        themes = ThemeManager(self.root, self.gui)
        for name in ("Light", "Dark", "Cyber"):
            themes.set_theme(name)
            q = THEMES[name]
            self.assertEqual(canvas.itemcget(rect, "fill"), q["BG"])
            self.assertEqual(canvas.itemcget(text, "fill"), q["BTN_FG"])
            self.assertEqual(canvas.itemcget(rect, "activefill"), q["ACCENT3"])
            self.assertEqual(canvas.itemcget(rect, "disabledoutline"), q["WARN_FG"])
            self.assertEqual(canvas.itemcget(rect, "state"), "disabled")
            self.assertEqual(canvas.itemcget(empty, "fill"), "")
            self.assertEqual(canvas.itemcget(empty, "outline"), "#123456")
            self.assertEqual((canvas.find_all(), canvas.coords(rect), canvas.gettags(rect),
                              canvas.itemcget(image, "image"), label.cget("image"), photo.get(0, 0)), before)
        themes.register(canvas, item=text, fill="WARN_FG")
        self.assertEqual(canvas.itemcget(text, "fill"), p["WARN_FG"])
        canvas.delete(rect)
        themes.apply_current(canvas)
        self.assertFalse(any(key[1] == rect for key in themes._roles[canvas]))

    def test_menus_cascades_inheritance_and_disabled_entries(self):
        p = self.p
        menu = tk.Menu(self.root, tearoff=False, bg=p["SURFACE"], fg=p["LABEL_FG"],
                       activebackground=p["ACCENT"], activeforeground=p["BG"])
        sub = tk.Menu(menu, tearoff=True, bg=p["SURFACE2"], fg=p["LABEL_FG"])
        flag = tk.BooleanVar(self.root, True)
        sub.add_checkbutton(label="Selected", variable=flag, state="disabled", foreground=p["WARN_FG"])
        sub.add_separator()
        sub.add_command(label="Inherited")
        menu.add_cascade(label="Actions", menu=sub)
        self.root.configure(menu=menu)
        themes = ThemeManager(self.root, self.gui)
        for name in ("Light", "Dark", "Cyber"):
            themes.set_theme(name)
            q = THEMES[name]
            self.assertEqual(str(menu.cget("activeforeground")), q["BTN_FG"])
            self.assertEqual(str(sub.cget("disabledforeground")), q["DISABLED_FG"])
            self.assertEqual(str(sub.entrycget(1, "foreground")), q["WARN_FG"])
            self.assertEqual(sub.entrycget(1, "state"), "disabled")
            self.assertTrue(flag.get())
            self.assertEqual(sub.entrycget(3, "foreground"), "")
        themes.register(sub, item=3, foreground="ERROR_FG")
        self.assertEqual(str(sub.entrycget(3, "foreground")), p["ERROR_FG"])
        sub.entryconfigure(3, foreground=p["ACCENT3"])
        themes.set_theme("Light")
        self.assertEqual(str(sub.entrycget(3, "foreground")), THEMES["Light"]["ACCENT3"])

    def test_dynamic_status_changes_override_snapshots_and_unknown_colors_survive(self):
        label = tk.Label(self.root, bg=self.p["STATUS_BG"], fg=self.p["STATUS_FG"])
        themes = ThemeManager(self.root, self.gui)
        themes.register(label, fg="STATUS_FG")
        for color, role, name in ((self.p["WARN_FG"], "WARN_FG", "Light"),
                                  ("#ef4444", "ERROR_FG", "Dark"),
                                  (self.gui.ACCENT3, "ACCENT3", "Cyber")):
            label.configure(fg=color)
            themes.set_theme(name)
            self.assertEqual(label.cget("fg"), THEMES[name][role])
        label.configure(fg="#123456")
        themes.set_theme("Light")
        self.assertEqual(label.cget("fg"), "#123456")
        label.configure(fg=self.p["STATUS_FG"])
        themes.apply_current(label)
        self.assertEqual(label.cget("fg"), THEMES["Light"]["STATUS_FG"])

    def test_control_defaults_variables_and_scroll_position(self):
        value = tk.IntVar(self.root, 42)
        radio = tk.Radiobutton(self.root, variable=value, value=42,
                               bg=self.p["BG"], fg=self.p["LABEL_FG"])
        scale = tk.Scale(self.root, variable=value, from_=0, to=100,
                         bg=self.p["BG"], troughcolor=self.p["SURFACE2"])
        spin = tk.Spinbox(self.root, from_=0, to=100, textvariable=value,
                          bg=self.p["ENTRY_BG"], fg=self.p["LABEL_FG"], state="disabled")
        scroll = tk.Scrollbar(self.root, bg=self.p["SURFACE2"], troughcolor=self.p["BG"])
        scroll.set(.2, .6)
        for control in (radio, scale, spin, scroll):
            control.pack()
        themes = ThemeManager(self.root, self.gui)
        for name in ("Dark", "Light", "Cyber"):
            themes.set_theme(name)
            q = THEMES[name]
            self.assertEqual(vars(self.gui), dict(q))
            self.assertEqual(value.get(), 42)
            self.assertEqual(scroll.get(), (.2, .6))
            self.assertEqual(spin.cget("state"), "disabled")
            self.assertEqual(spin.cget("disabledforeground"), q["DISABLED_FG"])
            self.assertEqual(spin.cget("disabledbackground"), q["ENTRY_BG"])
            self.assertEqual(spin.cget("selectforeground"), q["BTN_FG"])
            self.assertEqual(radio.cget("activeforeground"), q["LABEL_FG"])
            self.assertEqual(scale.cget("activebackground"), q["PANEL_BD"])

    def test_new_subtrees_mix_old_defaults_current_globals_and_historical_colors(self):
        themes = ThemeManager(self.root, self.gui)
        themes.set_theme("Dark")
        old = dict(themes.palette)
        themes.set_theme("Light")
        dialog = tk.Toplevel(self.root, bg=self.gui.BG)
        label = tk.Label(dialog, bg=self.gui.SURFACE, fg=old["MUTED_FG"])
        entry = tk.Entry(dialog, bg=self.p["ENTRY_BG"], fg=self.p["LABEL_FG"],
                         selectbackground="#0000d4d4ffff", selectforeground=self.p["BG"])
        border = tk.Frame(dialog, bg=self.p["ENTRY_BD"])
        border._accent = self.p["ACCENT3"]
        entry.bind("<FocusIn>", lambda event: border.configure(bg=border._accent))
        entry.pack()
        self.root.update()
        themes.apply_current(dialog)
        self.assertEqual(entry.cget("bg"), THEMES["Light"]["ENTRY_BG"])
        self.assertEqual(label.cget("fg"), THEMES["Light"]["MUTED_FG"])
        self.assertEqual(entry.cget("selectbackground"), THEMES["Light"]["ACCENT"])
        themes.set_theme("Dark")
        entry.event_generate("<FocusIn>")
        self.assertEqual(border.cget("bg"), THEMES["Dark"]["ACCENT3"])
        self.assertEqual(themes.color(self.p["ACCENT"]), THEMES["Dark"]["ACCENT"])
        self.assertEqual(themes.color(self.p["BG"], role="BTN_FG"), THEMES["Dark"]["BTN_FG"])
        self.assertEqual(themes.color("#123456"), "#123456")
        dialog.destroy()
        themes.apply_current(dialog)  # A closed help window is harmless.
        self.assertNotIn(entry, themes._roles)

    def test_explicit_duplicate_roles_roundtrip_and_invalid_requests_are_atomic(self):
        a = tk.Label(self.root, bg=self.p["SURFACE"], fg=self.p["BG"])
        b = tk.Label(self.root, bg=self.p["SURFACE"], fg=self.p["BTN_FG"])
        themes = ThemeManager(self.root, self.gui)
        themes.register(a, fg="BG")
        themes.register(b, fg="BTN_FG")
        for name in ("Light", "Dark", "Cyber", "Light"):
            themes.set_theme(name)
            self.assertEqual(a.cget("fg"), THEMES[name]["BG"])
            self.assertEqual(b.cget("fg"), THEMES[name]["BTN_FG"])
            self.assertEqual(a.cget("bg"), THEMES[name]["SURFACE"])
        with self.assertRaises(KeyError):
            themes.set_theme("invalid")
        self.assertEqual(themes.name, "Light")
        self.assertEqual(self.gui.BG, THEMES["Light"]["BG"])
        with self.assertRaises(KeyError):
            themes.register(a, fg="ACCENT", bg="INVALID")
        with self.assertRaises(ValueError):
            themes.register(a, text="LABEL_FG")
        self.assertEqual(a.cget("fg"), THEMES["Light"]["BG"])

    def test_real_gui_glow_defaults_help_and_application_state(self):
        try:
            import stego_test_file2_newuserguide as gui
        except ImportError as exc:
            self.skipTest(f"Optional GUI dependencies unavailable: {exc}")
        original = {role: getattr(gui, role, None) for role in self.p}
        self.addCleanup(self._restore_gui_globals, gui, original)
        app = gui.StegoApp(self.root)
        app.src_path.set("cover.png")
        app.decrypt_key.set("in-memory token")
        app.secret_msg.insert("1.0", "secret text")
        app._result = result = object()
        app._busy = True
        app._show_help()
        themes = ThemeManager(self.root, gui)
        themes.set_theme("Light")
        self.assertEqual(app._help_win.cget("bg"), THEMES["Light"]["BG"])
        app._help_win.destroy()
        app._show_help()
        themes.apply_current(app._help_win)
        button = gui.GlowButton(self.root, "Test", lambda: None)  # Frozen Cyber default.
        themes.apply_current(button)
        self.assertEqual(button._accent, THEMES["Light"]["ACCENT"])
        with self.assertRaises(ValueError):
            themes.register(button, item=button.find_all()[-1], fill="WARN_FG")
        button._draw(True)
        themes.set_theme("Dark")
        self.assertEqual(button._accent, THEMES["Dark"]["ACCENT"])
        fills = [button.itemcget(i, "fill") for i in button.find_all() if button.type(i) == "text"]
        self.assertEqual(fills, [THEMES["Dark"]["BTN_FG"]])
        button._draw(False)
        themes.set_theme("Light")
        fills = [button.itemcget(i, "fill") for i in button.find_all() if button.type(i) == "text"]
        self.assertEqual(fills, [THEMES["Light"]["ACCENT"]])
        self.assertEqual(app.src_path.get(), "cover.png")
        self.assertEqual(app.decrypt_key.get(), "in-memory token")
        self.assertEqual(app.secret_msg.get("1.0", "end-1c"), "secret text")
        self.assertIs(app._result, result)
        self.assertTrue(app._busy)
        self.assertEqual(app.result_text.cget("state"), "disabled")

    @staticmethod
    def _restore_gui_globals(gui, original):
        for role, value in original.items():
            if value is None:
                delattr(gui, role)
            else:
                setattr(gui, role, value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
