"""
Prototipo: Rediseño de la pestaña Resultados en Historial.
3 opciones: Agrupado, Chips, Refinado.
Ejecutar: python design/historial_results.py
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

MOCK_DATA = [
    ("scx_bpfland", "Context Switching", "12,500.0 µs", "MANUAL", "15/07 09:45"),
    ("scx_bpfland", "Carga Mixta", "88.3 pts", "AUTO", "15/07 09:45"),
    ("scx_bpfland", "Sincronización", "45.2 pts", "MANUAL", "15/07 09:45"),
    ("scx_lavd", "Fork+Exec", "1,234.5 µs", "AUTO", "14/07 18:00"),
    ("scx_lavd", "Compilación Paralela", "567.8 pts", "AUTO", "14/07 18:00"),
    ("scx_lavd", "Bajo Carga", "890.1 pts", "AUTO", "14/07 18:00"),
    ("scx_rusty", "Context Switching", "9,876.0 µs", "MANUAL", "13/07 11:00"),
    ("scx_rusty", "Carga Mixta", "76.5 pts", "AUTO", "13/07 11:00"),
    ("scx_central", "Sincronización", "55.0 pts", "AUTO", "12/07 16:40"),
    ("scx_central", "Fork+Exec", "2,100.3 µs", "MANUAL", "12/07 16:40"),
    ("scx_ghost", "Bajo Carga", "920.0 pts", "AUTO", "11/07 22:30"),
    ("scx_ghost", "Compilación Paralela", "450.2 pts", "MANUAL", "11/07 22:30"),
]

SCHED_COLORS = {
    "scx_bpfland": (0.18, 0.76, 0.49),
    "scx_lavd": (0.96, 0.76, 0.07),
    "scx_rusty": (0.90, 0.33, 0.30),
    "scx_central": (0.40, 0.50, 0.90),
    "scx_ghost": (0.70, 0.40, 0.90),
}


class PrototypeWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Prototipo: Resultados Historial")
        self.set_default_size(600, 550)
        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()

        self.stack = Adw.ViewStack()

        self.stack.add_titled(self._build_option_a(), "a", "Opción A: Agrupado")
        self.stack.add_titled(self._build_option_b(), "b", "Opción B: Chips")
        self.stack.add_titled(self._build_option_c(), "c", "Opción C: Refinado")

        opt_box = Gtk.Box(spacing=0, css_classes=["linked"])
        a_btn = Gtk.ToggleButton(label="A – Agrupado", css_classes=["flat"])
        b_btn = Gtk.ToggleButton(label="B – Chips", css_classes=["flat"])
        c_btn = Gtk.ToggleButton(label="C – Refinado", css_classes=["flat"])
        a_btn.connect("toggled", lambda b: self.stack.set_visible_child_name("a") if b.get_active() else None)
        b_btn.connect("toggled", lambda b: self.stack.set_visible_child_name("b") if b.get_active() else None)
        c_btn.connect("toggled", lambda b: self.stack.set_visible_child_name("c") if b.get_active() else None)
        opt_box.append(a_btn)
        opt_box.append(b_btn)
        opt_box.append(c_btn)
        header.set_title_widget(opt_box)

        a_btn.set_active(True)

        # Footer buttons
        footer = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12)
        approve_btn = Gtk.Button(
            label="Aprobar",
            css_classes=["suggested-action", "pill"],
        )

        # Easter egg: different messages per option
        jokes = {
            "a": "Buena elección. Los grupos son más fáciles de leer.",
            "b": "Los chips son modernos. Buena intuición.",
            "c": "Lo clásico bien hecho funciona. Respeto.",
        }

        def _on_approve(*_args):
            visible = self.stack.get_visible_child_name()
            self.toast(jokes.get(visible, "✅ Aprobado"))
        approve_btn.connect("clicked", _on_approve)

        other_btn = Gtk.Button(
            label="Otro",
            css_classes=["pill"],
        )

        def _on_other(*_args):
            current = list("abc")
            visible = self.stack.get_visible_child_name()
            current.remove(visible)
            next_opt = current[0]
            self.stack.set_visible_child_name(next_opt)
            for b, key in [(a_btn, "a"), (b_btn, "b"), (c_btn, "c")]:
                b.set_active(key == next_opt)
            self.toast("Probá esta variante...")
        other_btn.connect("clicked", _on_other)

        footer.append(approve_btn)
        footer.append(other_btn)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.stack)
        content.append(footer)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)

        view = Adw.ToolbarView(content=self.toast_overlay)
        view.add_top_bar(header)
        self.set_content(view)

    def toast(self, msg):
        toast = Adw.Toast.new(msg)
        self.toast_overlay.add_toast(toast)

    def _build_option_a(self):
        """Opción A: Agrupado por scheduler con ExpanderRow."""
        page = Adw.PreferencesPage()

        grupo_filtros = Adw.PreferencesGroup()
        search_row = Adw.ActionRow(title="Buscar")
        search = Gtk.SearchEntry(placeholder_text="Filtrar scheduler o prueba...")
        search_row.add_suffix(search)
        grupo_filtros.add(search_row)

        combo_box = Gtk.Box(spacing=6, margin_start=12, margin_end=12)
        modelo = Gtk.StringList()
        for t in ["Últimos 7 días", "Últimos 30 días", "Últimos 90 días", "Todo"]:
            modelo.append(t)
        combo = Gtk.DropDown(model=modelo, css_classes=["flat"])
        combo.set_selected(1)
        combo_box.append(Gtk.Label(label="Rango:", css_classes=["dim-label", "caption"]))
        combo_box.append(combo)
        grupo_filtros.add(combo_box)

        page.add(grupo_filtros)

        scheds = sorted(set(d[0] for d in MOCK_DATA))
        for sched in scheds:
            items = [d for d in MOCK_DATA if d[0] == sched]
            r, g, b = SCHED_COLORS.get(sched, (0.5, 0.5, 0.5))

            grupo = Adw.PreferencesGroup()
            expander = Adw.ExpanderRow(
                title=sched,
                subtitle=f"{len(items)} pruebas",
            )
            dot = Gtk.DrawingArea()
            dot.set_content_width(10)
            dot.set_content_height(10)
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_draw_func(lambda a, cr, w, h, cr_r=r, cr_g=g, cr_b=b: (
                cr.set_source_rgb(cr_r, cr_g, cr_b),
                cr.arc(w / 2, h / 2, 4, 0, 2 * 3.14159),
                cr.fill(),
            ))
            expander.add_prefix(dot)

            for _, test, val, badge, date in items:
                row = Adw.ActionRow(title=test, subtitle=date)
                row.add_suffix(Gtk.Label(label=val, css_classes=["monospace", "dim-label"], valign=Gtk.Align.CENTER))
                badge_lbl = Gtk.Label(label=badge, css_classes=["caption", "dim-label"], valign=Gtk.Align.CENTER)
                row.add_suffix(badge_lbl)
                expander.add_row(row)

            grupo.add(expander)
            page.add(grupo)

        return page

    def _build_option_b(self):
        """Opción B: Chips de filtro + lista plana."""
        page = Adw.PreferencesPage()

        grupo_chips = Adw.PreferencesGroup(title="Filtros activos")
        chips = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            row_spacing=4, column_spacing=4,
            max_children_per_line=8,
            margin_start=6, margin_end=6, margin_top=6, margin_bottom=6,
        )
        for sched in sorted(set(d[0] for d in MOCK_DATA))[:4]:
            r, g, b = SCHED_COLORS.get(sched, (0.5, 0.5, 0.5))
            chip = Gtk.Box(spacing=6, css_classes=["card", "pill"])
            dot = Gtk.DrawingArea()
            dot.set_content_width(10)
            dot.set_content_height(10)
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_margin_start(6)
            dot.set_draw_func(lambda a, cr, w, h, cr_r=r, cr_g=g, cr_b=b: (
                cr.set_source_rgb(cr_r, cr_g, cr_b),
                cr.arc(w / 2, h / 2, 4, 0, 2 * 3.14159),
                cr.fill(),
            ))
            chip.append(dot)
            chip.append(Gtk.Label(label=sched, css_classes=["caption"], margin_end=8))
            chips.append(chip)
        grupo_chips.add(chips)
        page.add(grupo_chips)

        grupo_lista = Adw.PreferencesGroup(title="Resultados Históricos")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css_classes=["boxed-list"])
        for sched, test, val, badge, date in MOCK_DATA:
            r, g, b = SCHED_COLORS.get(sched, (0.5, 0.5, 0.5))
            row = Gtk.Box(spacing=8, margin_start=10, margin_end=10, margin_top=6, margin_bottom=6)
            dot = Gtk.DrawingArea()
            dot.set_content_width(8)
            dot.set_content_height(8)
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_draw_func(lambda a, cr, w, h, cr_r=r, cr_g=g, cr_b=b: (
                cr.set_source_rgb(cr_r, cr_g, cr_b),
                cr.arc(w / 2, h / 2, 3.5, 0, 2 * 3.14159),
                cr.fill(),
            ))
            row.append(dot)
            row.append(Gtk.Label(label=sched, css_classes=["heading"], xalign=0))
            row.append(Gtk.Label(label=test, css_classes=["dim-label"], xalign=0, hexpand=True))
            row.append(Gtk.Label(label=val, css_classes=["monospace"]))
            row.append(Gtk.Label(label=badge, css_classes=["caption", "dim-label"]))
            box.append(row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(box)
        scroller.set_min_content_height(300)
        grupo_lista.add(scroller)
        page.add(grupo_lista)

        return page

    def _build_option_c(self):
        """Opción C: Refinamiento del diseño actual."""
        page = Adw.PreferencesPage()

        grupo_filtros = Adw.PreferencesGroup()
        expander = Adw.ExpanderRow(title="Filtros", subtitle="Todos los resultados  —  12 encontrados")
        grupo_filtros.add(expander)

        modelo = Gtk.StringList()
        for s in ["Todos"] + sorted(set(d[0] for d in MOCK_DATA)):
            modelo.append(s)
        expander.add_row(Adw.ComboRow(title="Scheduler", model=modelo))

        modelo2 = Gtk.StringList()
        for t in ["Todos", "Context Switching", "Carga Mixta", "Sincronización", "Fork+Exec", "Compilación Paralela", "Bajo Carga"]:
            modelo2.append(t)
        expander.add_row(Adw.ComboRow(title="Tipo de Prueba", model=modelo2))

        modelo3 = Gtk.StringList()
        for d in ["Últimos 7 días", "Últimos 30 días", "Últimos 90 días", "Todo"]:
            modelo3.append(d)
        r = Adw.ComboRow(title="Rango de Fechas", model=modelo3)
        r.set_selected(1)
        expander.add_row(r)

        page.add(grupo_filtros)

        grupo = Adw.PreferencesGroup(title="Resultados Históricos")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css_classes=["boxed-list"])
        for sched, test, val, badge, date in MOCK_DATA:
            row = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            title_row = Gtk.Box(spacing=6)
            title_row.append(Gtk.Label(label=sched, css_classes=["heading"], xalign=0))
            title_row.append(Gtk.Label(label=test, css_classes=["dim-label"], xalign=0))
            col.append(title_row)
            col.append(Gtk.Label(label=date, css_classes=["caption", "dim-label"], xalign=0))
            row.append(col)
            row.append(Gtk.Label(label=val, css_classes=["monospace"], valign=Gtk.Align.CENTER))
            badge_lbl = Gtk.Label(label=badge, css_classes=["caption", "dim-label"], valign=Gtk.Align.CENTER)
            badge_lbl.set_width_chars(6)
            row.append(badge_lbl)
            box.append(row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(box)
        scroller.set_min_content_height(300)
        grupo.add(scroller)
        page.add(grupo)

        return page


class PrototypeApp(Adw.Application):

    def __init__(self):
        super().__init__()
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        self.win = PrototypeWindow(app)
        self.win.present()


if __name__ == "__main__":
    PrototypeApp().run()
