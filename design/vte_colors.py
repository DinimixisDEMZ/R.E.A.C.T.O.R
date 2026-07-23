"""
Prototipo: Pruebas de colores VTE con temas Adwaita.
Ejecutar: python design/vte_colors.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Gtk, Adw, GLib, Gdk, Vte
import subprocess

scxtop_path = subprocess.run(["which", "scxtop"], capture_output=True, text=True).stdout.strip()


def _adw_palette(is_dark):
    """Paleta de 16 colores inspirada en Adwaita."""
    if is_dark:
        return [
            Gdk.RGBA(0.0, 0.0, 0.0, 1.0),        # 0 black
            Gdk.RGBA(0.75, 0.11, 0.16, 1.0),      # 1 red
            Gdk.RGBA(0.15, 0.63, 0.41, 1.0),      # 2 green
            Gdk.RGBA(0.90, 0.65, 0.04, 1.0),      # 3 yellow
            Gdk.RGBA(0.15, 0.45, 0.80, 1.0),      # 4 blue
            Gdk.RGBA(0.60, 0.30, 0.80, 1.0),      # 5 magenta
            Gdk.RGBA(0.10, 0.60, 0.70, 1.0),      # 6 cyan
            Gdk.RGBA(0.75, 0.76, 0.78, 1.0),      # 7 white
            Gdk.RGBA(0.30, 0.30, 0.32, 1.0),      # 8 bright black
            Gdk.RGBA(0.95, 0.25, 0.25, 1.0),      # 9 bright red
            Gdk.RGBA(0.20, 0.80, 0.50, 1.0),      # 10 bright green
            Gdk.RGBA(1.0, 0.80, 0.10, 1.0),       # 11 bright yellow
            Gdk.RGBA(0.25, 0.55, 0.95, 1.0),      # 12 bright blue
            Gdk.RGBA(0.70, 0.35, 0.95, 1.0),      # 13 bright magenta
            Gdk.RGBA(0.15, 0.75, 0.85, 1.0),      # 14 bright cyan
            Gdk.RGBA(0.95, 0.96, 0.98, 1.0),      # 15 bright white
        ]
    else:
        return [
            Gdk.RGBA(0.0, 0.0, 0.0, 1.0),
            Gdk.RGBA(0.60, 0.10, 0.12, 1.0),
            Gdk.RGBA(0.12, 0.50, 0.32, 1.0),
            Gdk.RGBA(0.72, 0.52, 0.03, 1.0),
            Gdk.RGBA(0.12, 0.36, 0.64, 1.0),
            Gdk.RGBA(0.48, 0.24, 0.64, 1.0),
            Gdk.RGBA(0.08, 0.48, 0.56, 1.0),
            Gdk.RGBA(0.55, 0.57, 0.60, 1.0),
            Gdk.RGBA(0.40, 0.40, 0.42, 1.0),
            Gdk.RGBA(0.80, 0.15, 0.18, 1.0),
            Gdk.RGBA(0.16, 0.65, 0.42, 1.0),
            Gdk.RGBA(0.95, 0.70, 0.05, 1.0),
            Gdk.RGBA(0.16, 0.48, 0.85, 1.0),
            Gdk.RGBA(0.62, 0.30, 0.82, 1.0),
            Gdk.RGBA(0.10, 0.62, 0.72, 1.0),
            Gdk.RGBA(0.95, 0.96, 0.97, 1.0),
        ]


class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Colores VTE")
        self.set_default_size(700, 500)
        self.setup_ui()

    def setup_ui(self):
        header = Adw.HeaderBar()
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="VTE Terminal — Prueba de colores")

        self.term = Vte.Terminal(hexpand=True, vexpand=True)
        self.term.set_margin_top(6)
        self.term.set_margin_bottom(6)
        self.term.set_margin_start(6)
        self.term.set_margin_end(6)
        group.add(self.term)

        if scxtop_path:
            self._spawn()

        buttons = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6,
                          margin_start=12, margin_end=12)
        for label, handler in [
            ("🐼 Panda", self._panda),
            ("🌙 Adw oscuro", self._adw_dark),
            ("☀️ Adw claro", self._adw_light),
            ("🪟 Transparente", self._transparent),
            ("🔁 set_colors(theme)", self._set_colors_theme),
            ("🎨 palette + theme", self._palette_theme),
        ]:
            btn = Gtk.Button(label=label, css_classes=["pill"])
            btn.connect("clicked", handler)
            buttons.append(btn)

        info = Gtk.Label(
            label="Pulsa los botones para probar esquemas. scxtop se reinicia automáticamente.",
            css_classes=["dim-label"], margin_bottom=6,
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(buttons)
        box.append(info)
        group.add(box)

        page.add(group)
        content = Adw.ToolbarView(content=page)
        content.add_top_bar(header)
        self.set_content(content)

    def _restart_scxtop(self):
        subprocess.run(["pkill", "-f", "scxtop"], capture_output=True)
        GLib.timeout_add(300, self._spawn)

    def _spawn(self):
        GLib.timeout_add(200, lambda: self.term.spawn_async(
            Vte.PtyFlags.DEFAULT,
            working_directory=None,
            argv=[scxtop_path], envv=None,
            spawn_flags=GLib.SpawnFlags.DEFAULT,
            child_setup=None, timeout=-1,
        ))

    def _panda(self, *_):
        self._restart_scxtop()
        pal = _adw_palette(True)
        pal[0] = Gdk.RGBA(0.161, 0.165, 0.267, 1)  # bg = dark purple
        pal[7] = Gdk.RGBA(0.827, 0.808, 0.776, 1)  # fg = cream
        self.term.set_colors(pal[7], pal[0], pal)
        self.term.queue_draw()

    def _adw_dark(self, *_):
        self._restart_scxtop()
        pal = _adw_palette(True)
        self.term.set_colors(pal[7], pal[0], pal)
        self.term.queue_draw()

    def _adw_light(self, *_):
        self._restart_scxtop()
        pal = _adw_palette(False)
        self.term.set_colors(pal[7], pal[0], pal)
        self.term.queue_draw()

    def _transparent(self, *_):
        self._restart_scxtop()
        pal = _adw_palette(True)
        bg = Gdk.RGBA(0, 0, 0, 0)
        self.term.set_colors(pal[7], bg, pal)
        self.term.queue_draw()

    def _set_colors_theme(self, *_):
        self._restart_scxtop()
        ctx = self.term.get_style_context()
        ok_fg, fg = ctx.lookup_color("theme_text_color")
        ok_bg, bg = ctx.lookup_color("theme_base_color")
        print(f"  theme_text_color={fg} ok={ok_fg}")
        print(f"  theme_base_color={bg} ok={ok_bg}")
        self.term.set_colors(
            fg if ok_fg else None,
            bg if ok_bg else None,
            None,
        )
        self.term.queue_draw()

    def _palette_theme(self, *_):
        self._restart_scxtop()
        ctx = self.term.get_style_context()
        ok_fg, fg = ctx.lookup_color("theme_text_color")
        ok_bg, bg = ctx.lookup_color("theme_base_color")
        is_dark = Adw.StyleManager.get_default().get_dark()
        pal = _adw_palette(is_dark)
        print(f"  is_dark={is_dark}  fg={fg}  bg={bg}")
        self.term.set_colors(
            fg if ok_fg else pal[7],
            bg if ok_bg else pal[0],
            pal,
        )
        self.term.queue_draw()


class ProtoApp(Adw.Application):
    def __init__(self):
        super().__init__()
        self.connect("activate", lambda a: Win(self).present())


if __name__ == "__main__":
    ProtoApp().run()
