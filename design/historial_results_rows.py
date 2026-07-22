"""
Prototipo: Mejora de visualización de Resultados Históricos.
Compara 3 estilos usando widgets nativos de Adw.
Ejecutar: python design/historial_results_rows.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

MOCK = [
    ("scx_bpfland", "Context Switching", "12,500.0 µs", "MANUAL", "15/07 09:45"),
    ("scx_bpfland", "Carga Mixta", "88.3 pts", "AUTO", "15/07 09:45"),
    ("scx_lavd", "Fork+Exec", "1,234.5 µs", "AUTO", "14/07 18:00"),
    ("scx_lavd", "Compilación Paralela", "567.8 pts", "AUTO", "14/07 18:00"),
    ("scx_rusty", "Context Switching", "9,876.0 µs", "MANUAL", "13/07 11:00"),
    ("scx_central", "Sincronización", "55.0 pts", "AUTO", "12/07 16:40"),
    ("scx_ghost", "Bajo Carga", "920.0 pts", "AUTO", "11/07 22:30"),
]

COLORS = {
    "scx_bpfland": (0.18, 0.76, 0.49),
    "scx_lavd": (0.96, 0.76, 0.07),
    "scx_rusty": (0.90, 0.33, 0.30),
    "scx_central": (0.40, 0.50, 0.90),
    "scx_ghost": (0.70, 0.40, 0.90),
}


def _dot(r, g, b):
    d = Gtk.DrawingArea()
    d.set_content_width(8); d.set_content_height(8)
    d.set_valign(Gtk.Align.CENTER)
    d.set_margin_end(8)
    d.set_draw_func(lambda a, cr, w, h, rr=r, gg=g, bb=b: (
        cr.set_source_rgb(rr, gg, bb),
        cr.arc(w / 2, h / 2, 3.5, 0, 2 * 3.14159),
        cr.fill(),
    ))
    return d


class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Filas de Resultados")
        self.set_default_size(600, 520)

        header = Adw.HeaderBar()
        stack = Adw.ViewStack()

        stack.add_titled(self._opt_a(), "a", "A – ActionRow")
        stack.add_titled(self._opt_b(), "b", "B – Agrupado")
        stack.add_titled(self._opt_c(), "c", "C – ColumnView")

        btns = Gtk.Box(spacing=0, css_classes=["linked"])
        for key, label in [("a", "ActionRow"), ("b", "Agrupado"), ("c", "ColumnView")]:
            b = Gtk.ToggleButton(label=label, css_classes=["flat"])
            b.connect("toggled", lambda btn, k=key: stack.set_visible_child_name(k) if btn.get_active() else None)
            btns.append(b)
        btns.get_first_child().set_active(True)
        header.set_title_widget(btns)

        view = Adw.ToolbarView(content=stack)
        view.add_top_bar(header)
        self.set_content(view)

    def _opt_a(self):
        """Adw.ActionRow: title=scheduler, subtitle=test+date, suffix=value+badge."""
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Resultados Históricos — ActionRow")
        for s, t, v, b, d in MOCK:
            r, g, bcolor = COLORS.get(s, (0.5, 0.5, 0.5))
            row = Adw.ActionRow(title=s, subtitle=f"{t}  •  {d}")
            row.add_prefix(_dot(r, g, bcolor))
            row.add_suffix(Gtk.Label(label=v, css_classes=["monospace"], valign=Gtk.Align.CENTER))
            badge = Gtk.Label(label=b, css_classes=["caption", "dim-label"], valign=Gtk.Align.CENTER)
            badge.set_margin_start(8)
            row.add_suffix(badge)
            group.add(row)
        page.add(group)
        return page

    def _opt_b(self):
        """Agrupado: ExpanderRow por scheduler con items inline."""
        page = Adw.PreferencesPage()
        scheds = {}
        for s, t, v, b, d in MOCK:
            scheds.setdefault(s, []).append((t, v, b, d))
        for s, items in scheds.items():
            r, g, bcolor = COLORS.get(s, (0.5, 0.5, 0.5))
            group = Adw.PreferencesGroup()
            exp = Adw.ExpanderRow(title=s, subtitle=f"{len(items)} pruebas")
            exp.add_prefix(_dot(r, g, bcolor))
            for t, v, bdge, date in items:
                row = Adw.ActionRow(title=t, subtitle=date)
                row.add_suffix(Gtk.Label(label=v, css_classes=["monospace"], valign=Gtk.Align.CENTER))
                row.add_suffix(Gtk.Label(label=bdge, css_classes=["caption", "dim-label"], valign=Gtk.Align.CENTER, margin_start=8))
                exp.add_row(row)
            group.add(exp)
            page.add(group)
        return page

    def _opt_c(self):
        """ColumnView: tabla con columnas Scheduler, Test, Value, Badge, Date."""
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Resultados Históricos — ColumnView")

        store = Gtk.StringList()
        for s, t, v, b, d in MOCK:
            store.append(f"{s}|{t}|{v}|{b}|{d}")

        def _parse(item):
            return item.get_string().split("|", 4)

        col_sched = Gtk.ColumnViewColumn(title="Scheduler")
        col_test = Gtk.ColumnViewColumn(title="Test")
        col_val = Gtk.ColumnViewColumn(title="Valor")
        col_badge = Gtk.ColumnViewColumn(title="Tipo")

        for col, idx in [(col_sched, 0), (col_test, 1), (col_val, 2), (col_badge, 3)]:
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", lambda f, item, i=idx: item.set_child(
                Gtk.Label(xalign=0 if i == 0 else 1, css_classes=["dim-label" if i > 0 else ""])
            ))
            factory.connect("bind", lambda f, item, i=idx: item.get_child().set_label(
                _parse(item.get_item())[i]
            ))
            col.set_factory(factory)
            col.set_resizable(True)
            col.set_expand(idx == 0)

        view = Gtk.ColumnView(model=Gtk.SingleSelection(model=Gtk.NoSelection(model=store)))
        view.append_column(col_sched)
        view.append_column(col_test)
        view.append_column(col_val)
        view.append_column(col_badge)
        view.set_hexpand(True)
        view.add_css_class("data-table")

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(view)
        scroller.set_min_content_height(280)
        group.add(scroller)
        page.add(group)
        return page


class App(Adw.Application):
    def __init__(self):
        super().__init__()
        self.connect("activate", lambda a: Win(a).present())


if __name__ == "__main__":
    App().run()
