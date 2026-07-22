"""
Prototipo: Posiciones de icono en filas de resultados.
Ejecutar: python design/icon_positions.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

MOCK = [
    ("scx_bpfland", "auto", "Carga Mixta", "15/07", "88.3 pts"),
    ("scx_bpfland", "manual", "Context Switching", "15/07", "12,500 µs"),
    ("scx_lavd", "auto", "Fork+Exec", "14/07", "1,234 µs"),
    ("scx_lavd", "manual", "Compilación Paralela", "14/07", "567.8 pts"),
]


class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Posiciones de Icono")
        self.set_default_size(500, 500)

        header = Adw.HeaderBar()
        stack = Adw.ViewStack()

        stack.add_titled(self._opcion_a(), "a", "A: Icono derecha")
        stack.add_titled(self._opcion_b(), "b", "B: Icono izquierda")
        stack.add_titled(self._opcion_c(), "c", "C: Combinado")
        stack.add_titled(self._opcion_d(), "d", "D: Badge color")

        btns = Gtk.Box(spacing=0, css_classes=["linked"])
        for key, label in [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]:
            b = Gtk.ToggleButton(label=label, css_classes=["flat"])
            b.connect("toggled", lambda btn, k=key: stack.set_visible_child_name(k) if btn.get_active() else None)
            btns.append(b)
        btns.get_first_child().set_active(True)
        header.set_title_widget(btns)

        view = Adw.ToolbarView(content=stack)
        view.add_top_bar(header)
        self.set_content(view)

    def _dot(self, sched):
        r, g, b = {
            "scx_bpfland": (0.18, 0.76, 0.49),
            "scx_lavd": (0.96, 0.76, 0.07),
        }.get(sched, (0.5, 0.5, 0.5))
        d = Gtk.DrawingArea()
        d.set_content_width(8); d.set_content_height(8)
        d.set_valign(Gtk.Align.CENTER)
        d.set_draw_func(lambda a, cr, w, h: (
            cr.set_source_rgb(r, g, b),
            cr.arc(w / 2, h / 2, 3.5, 0, 2 * 3.14159),
            cr.fill(),
        ))
        return d

    def _type_icon(self, run_type):
        icon = "emblem-synchronizing-symbolic" if run_type == "auto" else "applications-engineering-symbolic"
        tip = "Automático" if run_type == "auto" else "Manual"
        img = Gtk.Image(icon_name=icon, tooltip_text=tip, pixel_size=14, valign=Gtk.Align.CENTER)
        return img, tip

    def _build_group(self, title, rows_fn):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title=title)
        for r in rows_fn():
            g.add(r)
        page.add(g)
        return page

    def _opcion_a(self):
        def rows():
            for s, t, test, date, val in MOCK:
                row = Adw.ActionRow(title=s, subtitle=f"{test}  •  {date}")
                if t == "auto":
                    row.add_suffix(Gtk.Label(label=val))
                    icon, tip = self._type_icon(t)
                    icon.set_margin_start(6)
                    row.add_suffix(icon)
                else:
                    icon, tip = self._type_icon(t)
                    icon.set_margin_end(6)
                    row.add_prefix(icon)
                    row.add_suffix(Gtk.Label(label=val))
                yield row
        return self._build_group("A: Icono derecha (actual)", rows)

    def _opcion_b(self):
        def rows():
            for s, t, test, date, val in MOCK:
                row = Adw.ActionRow(title=f"• {s}", subtitle=f"{test}  •  {date}")
                icon, tip = self._type_icon(t)
                icon.set_margin_end(8)
                row.add_prefix(icon)
                row.add_suffix(Gtk.Label(label=val))
                yield row
        return self._build_group("B: Icono izquierda", rows)

    def _opcion_c(self):
        def rows():
            for s, t, test, date, val in MOCK:
                row = Adw.ActionRow(title=s, subtitle=f"{test}  •  {date}")
                box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
                dot = self._dot(s)
                icon, tip = self._type_icon(t)
                box.append(dot)
                box.append(icon)
                row.add_prefix(box)
                row.add_suffix(Gtk.Label(label=val))
                yield row
        return self._build_group("C: Combinado dot + icono izquierda", rows)

    def _opcion_d(self):
        def rows():
            for s, t, test, date, val in MOCK:
                row = Adw.ActionRow(title=s, subtitle=f"{test}  •  {date}")
                is_auto = t == "auto"
                color = "#2ec27e" if is_auto else "#f5c211"
                lbl = Gtk.Label(
                    label="AUTO" if is_auto else "MANUAL",
                    css_classes=["caption"],
                    valign=Gtk.Align.CENTER,
                )
                ctx = lbl.get_style_context()
                css = Gtk.CssProvider()
                css.load_from_string(f"label {{ color: {color}; font-weight: bold; }}")
                ctx.add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                lbl.set_tooltip_text("Automático" if is_auto else "Manual")
                lbl.set_margin_start(8)
                row.add_suffix(lbl)
                row.add_suffix(Gtk.Label(label=val, css_classes=["dim-label"]))
                yield row
        return self._build_group("D: Badge de color", rows)


class App(Adw.Application):
    def __init__(self):
        super().__init__()
        self.connect("activate", lambda a: Win(a).present())


if __name__ == "__main__":
    App().run()
