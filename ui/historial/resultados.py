"""
Página de resultados del historial — Muestra benchmarks anteriores agrupados.
"""

import time
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk

from core.database import consultar_historial, obtener_schedulers_historial
from utils.colores import generar_color_hash, dibujar_dot
from utils.helpers import vaciar_contenedor
from widgets.legend import crear_chip_leyenda
from utils.i18n import traducir
from .constantes import _TIPOS_PRUEBA, _RANGOS_FECHA


def _crear_pagina_resultados(win):
    pagina = Adw.PreferencesPage()

    grupo_chips = Adw.PreferencesGroup(title=traducir("Planificadores"))
    win._hist_chips_box = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        row_spacing=4, column_spacing=4,
        max_children_per_line=10,
        margin_start=6, margin_end=6, margin_top=6, margin_bottom=6,
    )
    grupo_chips.add(win._hist_chips_box)

    fecha_fila = Adw.ActionRow(title=traducir("Rango de Fechas"))
    modelo_fechas = Gtk.StringList()
    for _, nombre in _RANGOS_FECHA:
        modelo_fechas.append(traducir(nombre))
    win._hist_combo_fecha = Gtk.DropDown(model=modelo_fechas, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_combo_fecha.set_selected(1)
    win._hist_combo_fecha.connect("notify::selected", lambda *a: _refrescar_historial(win))
    fecha_fila.add_suffix(win._hist_combo_fecha)
    grupo_chips.add(fecha_fila)

    pagina.add(grupo_chips)

    grupo_resultados = Adw.PreferencesGroup(title=traducir("Resultados Históricos"))
    win._hist_box_resultados = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=12,
    )
    grupo_resultados.add(win._hist_box_resultados)
    pagina.add(grupo_resultados)

    return pagina


def _reconstruir_chips(win):
    """Construye los chips de filtro por scheduler."""
    caja = getattr(win, "_hist_chips_box", None)
    if caja is None:
        return

    vaciar_contenedor(caja)

    win._hist_chips_active = set()
    planificadores = sorted(obtener_schedulers_historial())

    for planif in planificadores:
        r, g, b = generar_color_hash(planif)

        crear_chip_leyenda(
            planif, color_func=lambda n: (r, g, b) if n == planif else (0.5, 0.5, 0.5),
            on_toggle=lambda s, v: _refrescar_historial(win), dot_radius=4, dot_size=10,
            opacity_hidden=0.5, ocultos_set=win._hist_chips_active,
            box_leyenda=caja
        )


def _refrescar_historial(win):
    fecha_idx = win._hist_combo_fecha.get_selected()
    days, _ = _RANGOS_FECHA[fecha_idx]
    fecha_desde = time.time() - (days * 86400) if days > 0 else None

    activos = getattr(win, "_hist_chips_active", set())
    sched = list(activos)[0] if len(activos) == 1 else None
    test = None

    resultados = consultar_historial(
        scheduler=sched, test_type=None, date_from=fecha_desde,
    )

    if activos:
        resultados = [r for r in resultados if r["scheduler_name"] in activos]

    vaciar_contenedor(win._hist_box_resultados)

    contador = 0
    grupos = {}
    for r in resultados:
        grupos.setdefault(r["scheduler_name"], []).append(r)

    for sched, items in grupos.items():
        r_sched, g_sched, b_sched = generar_color_hash(sched)
        grupo = Adw.PreferencesGroup(title=traducir(sched))

        caja_encabezado = Gtk.Box(spacing=6, margin_start=6, margin_bottom=4)
        punto = Gtk.DrawingArea()
        punto.set_content_width(8)
        punto.set_content_height(8)
        punto.set_valign(Gtk.Align.CENTER)
        punto.set_draw_func(lambda a, cr, w, h, cr_r=r_sched, cr_g=g_sched, cr_b=b_sched:
                            dibujar_dot(cr, w, h, cr_r, cr_g, cr_b, 3.5))
        caja_encabezado.append(punto)
        grupo.set_header_suffix(caja_encabezado)

        for r in items:
            marca_temporal = datetime.fromtimestamp(r["timestamp"]).strftime("%d/%m %H:%M")
            tipo_nombre = dict(_TIPOS_PRUEBA).get(r["test_type"], r["test_type"])
            valor = r["valor"]
            p95 = r.get("p95")

            if "latencia" in r["test_type"]:
                texto_valor = f"{valor:,.1f} µs"
                if p95:
                    texto_valor += f"  (p95: {p95:,.1f})"
            elif r["test_type"] == "threads":
                texto_valor = f"{valor:,.1f} ops/s"
            else:
                texto_valor = f"{valor:,.1f} pts"

            fila = Adw.ActionRow(title=traducir(tipo_nombre), subtitle=marca_temporal)
            fila.add_suffix(Gtk.Label(label=texto_valor, valign=Gtk.Align.CENTER))

            tipo_ejecucion = r.get("run_type", "manual")
            icono_insignia = "view-refresh-symbolic" if tipo_ejecucion == "auto" else "applications-engineering-symbolic"
            consejo_insignia = traducir("Detección automática") if tipo_ejecucion == "auto" else traducir("Benchmark manual")
            insignia = Gtk.Image(
                icon_name=icono_insignia, tooltip_text=consejo_insignia,
                pixel_size=14, css_classes=["dim-label"],
                valign=Gtk.Align.CENTER,
            )
            insignia.set_margin_start(8)
            fila.add_suffix(insignia)

            grupo.add(fila)
            contador += 1

        win._hist_box_resultados.append(grupo)

    grupo = win._hist_box_resultados.get_parent()
    while grupo and not isinstance(grupo, Adw.PreferencesGroup):
        grupo = grupo.get_parent()
    if grupo:
        grupo.set_title(f"{traducir('Resultados Históricos')} — {contador} {traducir('encontrado(s)')}")
