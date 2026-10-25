"""Session-only themes for STEGO's classic Tk widgets (Tk thread only).

Integration, owned by the GUI:
    # Keep the original Cyber globals. After StegoApp has built every widget:
    themes = ThemeManager(root, sys.modules[__name__])
    themes.set_theme("Light")             # "Cyber", "Dark", or "Light"
    themes.apply_current(dialog)          # after construction, before wait_window

set_theme updates GUI module globals, then recolors the existing widget tree.
apply_current also recognizes original/default and previously used palettes.
Recognized colors get remembered roles; changed colors are re-inferred on the
next application (so status WARN/ERROR/success changes are not frozen).
Unknown custom colors and empty/inherited options are left alone. Tk's default
color options get sensible roles. register disambiguates colors explicitly:
    themes.register(label, fg="KEY_FG", bg="KEY_BG")
    themes.register(canvas, item=text_id, fill="BTN_FG")
    themes.register(menu, item=0, foreground="WARN_FG")
    themes.register(button, _accent="ACCENT3")
Registration assigns a role, not a permanent lock against later config changes.
Re-register menu indices after inserting/deleting entries; canvas IDs are stable.
GlowButton owns/redraws its items: register its _accent, not individual items.

Python defaults/closures cannot be fixed by changing globals. In constructors,
map captured defaults with themes.color(accent), themes.color(fg), etc., or pass
current globals explicitly. color(value, role="...") forces a known role.
Call apply_current on each completed new subtree to snapshot its roles. Replace
CyberEntry's captured FocusIn accent with self._accent, initialized from the
mapped accent; the manager updates this attribute. Existing GlowButton._accent
is supported and its tiny vector drawing is refreshed, including hover state.
Other canvas items are recolored in place; no image pixels are read or changed.
Use BTN_FG rather than BG for selection/menu active text. Map _set_status's
captured default immediately and replace hard-coded error red with ERROR_FG.

There is no polling, Tk monkeypatch, persistence, or widget/layout rebuild.
Native dialogs, ttk styles, custom Text tags, and arbitrary callback-local
colors are deliberately outside this module. Do not use multiple managers for
one GUI module. Equal green roles stay equal in every palette by design.
"""

import tkinter as tk
from types import MappingProxyType
from weakref import WeakKeyDictionary


THEMES = MappingProxyType({
    "Cyber": MappingProxyType({
        "BG": "#0a0e1a", "SURFACE": "#0f1628", "SURFACE2": "#141d35",
        "PANEL_BD": "#1e2d50", "ACCENT": "#00d4ff", "ACCENT3": "#06ffa5",
        "BTN_FG": "#0a0e1a", "BTN_HOV": "#00b8e0", "LABEL_FG": "#e2e8f0",
        "MUTED_FG": "#8ba3c7", "ENTRY_BG": "#0d1526", "ENTRY_BD": "#1e3a5f",
        "KEY_BG": "#0a1f1a", "KEY_FG": "#06ffa5", "RESULT_BG": "#0a1f14",
        "RESULT_FG": "#06ffa5", "STATUS_BG": "#07090f", "STATUS_FG": "#4a7a9a",
        "WARN_FG": "#f59e0b", "ERROR_FG": "#ff7373", "DISABLED_FG": "#91a0b8",
    }),
    "Dark": MappingProxyType({
        "BG": "#12161e", "SURFACE": "#1b2230", "SURFACE2": "#253043",
        "PANEL_BD": "#40516b", "ACCENT": "#8ac7ff", "ACCENT3": "#72e0b0",
        "BTN_FG": "#12161e", "BTN_HOV": "#a6d5ff", "LABEL_FG": "#f8fafc",
        "MUTED_FG": "#b2bfd1", "ENTRY_BG": "#171e2b", "ENTRY_BD": "#536781",
        "KEY_BG": "#142c25", "KEY_FG": "#72e0b0", "RESULT_BG": "#182b25",
        "RESULT_FG": "#72e0b0", "STATUS_BG": "#0e1219", "STATUS_FG": "#9fadc3",
        "WARN_FG": "#ffc36b", "ERROR_FG": "#ff9292", "DISABLED_FG": "#9aa9bf",
    }),
    "Light": MappingProxyType({
        "BG": "#f3f6fb", "SURFACE": "#ffffff", "SURFACE2": "#e7edf5",
        "PANEL_BD": "#9aaac0", "ACCENT": "#005f91", "ACCENT3": "#087447",
        "BTN_FG": "#ffffff", "BTN_HOV": "#004d78", "LABEL_FG": "#172033",
        "MUTED_FG": "#536177", "ENTRY_BG": "#fafcff", "ENTRY_BD": "#778ba5",
        "KEY_BG": "#e5f5ed", "KEY_FG": "#087447", "RESULT_BG": "#eaf6ed",
        "RESULT_FG": "#087447", "STATUS_BG": "#dde6f1", "STATUS_FG": "#465770",
        "WARN_FG": "#8a4b00", "ERROR_FG": "#b42332", "DISABLED_FG": "#59677a",
    }),
})

# Only these options are touched, never state, text, images, geometry or commands.
_DEFAULT_ROLES = {
    "background": "BG", "foreground": "LABEL_FG",
    "activebackground": "PANEL_BD", "activeforeground": "LABEL_FG",
    "disabledbackground": "ENTRY_BG", "disabledforeground": "DISABLED_FG",
    "readonlybackground": "ENTRY_BG", "highlightbackground": "PANEL_BD",
    "highlightcolor": "ACCENT", "insertbackground": "ACCENT",
    "selectbackground": "ACCENT", "inactiveselectbackground": "ACCENT",
    "selectforeground": "BTN_FG", "selectcolor": "SURFACE2",
    "troughcolor": "BG", "buttonbackground": "SURFACE2",
}
_CANVAS_COLORS = frozenset((
    "fill", "outline", "activefill", "activeoutline", "disabledfill",
    "disabledoutline", "background", "foreground",
))
_ALIASES = {"bg": "background", "fg": "foreground"}


class ThemeManager:
    """Snapshot an already-built Cyber GUI; call all methods on its Tk thread."""

    def __init__(self, root, gui_module):
        self.root = root
        self.gui_module = gui_module
        self._original = {
            role: getattr(gui_module, role, value)
            for role, value in THEMES["Cyber"].items()
        }
        self.palette = self._original
        self._source = self._original
        self._roles = WeakKeyDictionary()
        self._rgb_cache = {}
        self.set_theme("Cyber")

    def _rgb(self, value):
        value = str(value)
        if not value:
            return None
        if value not in self._rgb_cache:
            try:
                self._rgb_cache[value] = self.root.winfo_rgb(value)
            except tk.TclError:
                self._rgb_cache[value] = None
        return self._rgb_cache[value]

    def _role(self, value, option="", preferred=None):
        rgb = self._rgb(value)
        if rgb is None:
            return None
        # The live source palette wins over historical palettes. Never reverse
        # a palette to a single-value dict: Cyber BG/BTN_FG are different roles.
        for palette in (self._source, self._original, *THEMES.values()):
            matches = [role for role, color in palette.items()
                       if self._rgb(color) == rgb]
            if not matches:
                continue
            if preferred in matches:
                return preferred
            if "foreground" in option or option == "textfill":
                if "BTN_FG" in matches or "BG" in matches:
                    return "BTN_FG"
            if "BG" in matches:
                return "BG"
            if "SURFACE" in matches:
                return "SURFACE"
            return matches[0]
        # The GUI currently uses this literal for errors, outside its palette.
        if rgb == self._rgb("#ef4444"):
            return "ERROR_FG"
        return None

    def color(self, value, *, role=None):
        """Map a captured/historical color; unknown colors pass through.

        role explicitly overrides inference, including equal-valued roles.
        """
        if role is not None:
            return self.palette[role]
        inferred = self._role(value)
        return self.palette[inferred] if inferred else value

    def set_theme(self, name):
        """Update globals and all descendants, without rebuilding the GUI."""
        palette = THEMES[name]  # Validate before modifying any state.
        self._source = self.palette
        self.name, self.palette = name, palette
        for role, value in palette.items():
            setattr(self.gui_module, role, value)
        try:
            self.apply_current(self.root)
        finally:
            self._source = palette

    def _mapped(self, widget, key, value, default=None, hint=None):
        records = self._roles.setdefault(widget, {})
        previous = records.get(key)
        rgb = self._rgb(value)
        if previous and rgb == previous[1]:
            role = previous[0]
        else:
            role = self._role(value, hint or key[2],
                              previous[0] if previous else None)
            if role is None and rgb is not None and default is not None:
                if rgb == self._rgb(default):
                    role = _DEFAULT_ROLES.get(key[2])
        if role is None:
            records.pop(key, None)
            return value
        color = self.palette[role]
        records[key] = (role, self._rgb(color))
        return color

    def _options(self, widget, item=None):
        if item is None:
            return "widget", None, widget.configure(), widget.configure
        if isinstance(widget, tk.Canvas):
            if isinstance(widget, getattr(self.gui_module, "GlowButton", ())):
                raise ValueError("Register GlowButton._accent, not its redrawn canvas items")
            if not isinstance(item, int) or not widget.type(item):
                raise ValueError("Canvas registration requires a live numeric item ID")
            return ("canvas", item, widget.itemconfigure(item),
                    lambda **kw: widget.itemconfigure(item, **kw))
        if isinstance(widget, tk.Menu):
            index = widget.index(item)
            if index is None:
                raise ValueError("Menu registration requires a live entry index")
            return ("menu", index, widget.entryconfigure(index),
                    lambda **kw: widget.entryconfigure(index, **kw))
        raise TypeError("item is only supported for Canvas and Menu")

    def register(self, widget, *, item=None, **roles):
        """Assign explicit roles now; subsequent external color changes are honored."""
        kind, item, config, _ = self._options(widget, item)
        roles = {_ALIASES.get(option, option): role for option, role in roles.items()}
        for option, role in roles.items():
            if role not in self.palette:
                raise KeyError(role)
            attribute = item is None and option == "_accent" and hasattr(widget, option)
            allowed = _CANVAS_COLORS if kind == "canvas" else _DEFAULT_ROLES
            if not attribute and (option not in allowed or option not in config):
                raise ValueError(f"Not a supported color option: {option}")
        records = self._roles.setdefault(widget, {})
        for option, role in roles.items():
            attribute = option == "_accent"
            value = getattr(widget, option) if attribute else config[option][-1]
            key = ("attribute" if attribute else kind, item, option)
            records[key] = (role, self._rgb(value))
        self.apply_current(widget)

    def _recolor(self, widget, item=None):
        kind, item, config, configure = self._options(widget, item)
        allowed = _CANVAS_COLORS if kind == "canvas" else _DEFAULT_ROLES
        text = kind == "canvas" and widget.type(item) == "text"
        changes = {}
        for option, spec in config.items():
            if option not in allowed or len(spec) != 5:
                continue
            value = str(spec[-1])
            hint = "textfill" if text and option.endswith("fill") else option
            color = self._mapped(widget, (kind, item, option), value,
                                 spec[-2] if kind == "widget" else None, hint)
            if color != value:
                changes[option] = color
        if changes:
            configure(**changes)

    def apply_current(self, widget=None):
        """Recolor/snapshot a completed subtree, including old constructor defaults."""
        for old in list(self._roles):
            if not old.winfo_exists():
                del self._roles[old]
        self._visit(self.root if widget is None else widget)

    def _visit(self, widget):
        if not widget.winfo_exists():
            return
        self._recolor(widget)
        scopes = {("widget", None)}
        glow = isinstance(widget, getattr(self.gui_module, "GlowButton", ()))
        # Read the rendered hover state before changing _accent or redrawing.
        hovered = glow and any(
            widget.type(item) == "text"
            and self._role(widget.itemcget(item, "fill"), "textfill") == "BTN_FG"
            for item in widget.find_all()
        )
        if hasattr(widget, "_accent"):
            scopes.add(("attribute", None))
            widget._accent = self._mapped(
                widget, ("attribute", None, "_accent"), widget._accent)
        if glow:
            widget._draw(hovered)
        elif isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                self._recolor(widget, item)
                scopes.add(("canvas", item))
        elif isinstance(widget, tk.Menu):
            end = widget.index("end")
            for item in range(0 if end is None else end + 1):
                self._recolor(widget, item)
                scopes.add(("menu", item))
        records = self._roles.get(widget, {})
        for key in list(records):
            if key[:2] not in scopes:
                del records[key]
        for child in widget.winfo_children():
            self._visit(child)
