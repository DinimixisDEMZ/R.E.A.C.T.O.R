"""
Pestaña de Automatización: Detección inteligente del mejor scheduler.

Re-exports públicos:
    setup_automatizacion_ui
    _refrescar_auto_schedulers
"""

import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from utils.i18n import traducir
from utils.iconos import POTENCIA, RESPUESTA, FLUIDEZ
from ui.automatizacion.pesos import (
    animar_sliders,
    aplicar_preset,
    sincronizar_estado_pesos,
    mostrar_banner_recalc,
    recalcular_ranking,
    poblar_ranking,
    crear_slider_peso,
    on_peso_changed,
    actualizar_lbls,
    on_info_click,
)
from ui.automatizacion.historial import (
    refrescar_historial,
    navegar_historial,
    actualizar_botones_nav,
    seleccionar_run,
)
from ui.automatizacion.deteccion import (
    gestionar_click_auto,
    iniciar_auto_test,
    finalizar_auto_test,
    finalizar_auto_test_success,
    limpiar_ranking_auto,
)


def _toggle_all_scheds(win, state):
    for nombre, (row, check) in win._auto_sched_checks.items():
        check.set_active(state)
    _actualizar_subtitulo_scheds(win)


def _actualizar_subtitulo_scheds(win):
    if not win._auto_sched_checks:
        return
    total = len(win._auto_sched_checks)
    checked = sum(1 for _, c in win._auto_sched_checks.values() if c.get_active())
    win._auto_expander.set_subtitle(traducir("{}/{} seleccionados").format(checked, total))
    win._auto_expander.set_visible(total > 0)


def _refrescar_auto_schedulers(win):
    """Actualiza la lista de schedulers seleccionables para el test automático."""
    try:
        if win.modo_desarrollador:
            nombres = win.scx.obtener_lista()
        else:
            nombres = win.scx.obtener_lista(win.compatibles) if win.compatibles is not None else []
    except (subprocess.SubprocessError, OSError):
        nombres = []

    existentes = set(win._auto_sched_checks.keys())

    for nombre in existentes - set(nombres):
        row, _ = win._auto_sched_checks.pop(nombre)
        win._auto_expander.remove(row)

    for nombre in nombres:
        if nombre in win._auto_sched_checks:
            continue
        check = Gtk.CheckButton()
        row = Adw.ActionRow(title=nombre, activatable_widget=check)
        row.add_suffix(check)
        check.set_active(True)
        check.connect("toggled", lambda c, n=nombre: _actualizar_subtitulo_scheds(win))
        win._auto_sched_checks[nombre] = (row, check)
        win._auto_expander.add_row(row)

    _actualizar_subtitulo_scheds(win)


def configurar_ui_automatizacion(win):
    """Construye la interfaz de la pestaña Automatización (Detección Inteligente).

    Args:
        win: Instancia de VentanaSimple
    """
    pref_page = Adw.PreferencesPage()
    grupo_auto = Adw.PreferencesGroup(title=traducir("Detección Inteligente"), description=traducir("Escanear rendimiendo general de cada planificador y obtener el mejor."))

    win.barra_progreso = Gtk.ProgressBar(margin_top=6, margin_bottom=6, visible=False)
    win.revealer_tiempo = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
    win.label_tiempo = Gtk.Label(label="", css_classes=["caption", "dim-label"], margin_bottom=12)
    win.revealer_tiempo.set_child(win.label_tiempo)

    grupo_auto.add(win.barra_progreso)
    grupo_auto.add(win.revealer_tiempo)

    win.btn_auto = Gtk.Button(label=traducir("Determinar"), css_classes=["suggested-action"], margin_top=6, margin_bottom=6)
    win.btn_auto.connect("clicked", lambda b: gestionar_click_auto(win, b))
    grupo_auto.add(win.btn_auto)

    win._auto_sched_checks = {}
    win._auto_expander = Adw.ExpanderRow(
        title=traducir("Planificadores a analizar"),
        subtitle="",
        icon_name="application-x-executable-symbolic",
        expanded=False
    )

    btn_select_all = Gtk.Button(label=traducir("Marcar todos"), css_classes=["flat"])
    btn_select_none = Gtk.Button(label=traducir("Desmarcar todos"), css_classes=["flat"])
    btn_select_all.connect("clicked", lambda b: _toggle_all_scheds(win, True))
    btn_select_none.connect("clicked", lambda b: _toggle_all_scheds(win, False))

    toggle_row = Adw.ActionRow(title=traducir("Seleccionar:"))
    caja_btn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    caja_btn.append(btn_select_all)
    caja_btn.append(btn_select_none)
    toggle_row.add_suffix(caja_btn)
    win._auto_expander.add_row(toggle_row)

    grupo_auto.add(win._auto_expander)

    win.grupo_ganador = Adw.PreferencesGroup(title=traducir("Resultado del Diagnóstico"))
    win.fila_ganador = Adw.ExpanderRow(title=traducir("Motor en reposo"), subtitle=traducir("Esperando al escaneo..."), icon_name="applications-engineering-symbolic")
    win.box_resultados = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
    win.fila_ganador.add_suffix(win.box_resultados)
    win._filas_ranking = []
    win.grupo_ganador.add(win.fila_ganador)

    grupo_logs_auto = Adw.PreferencesGroup(title=traducir("Registro de Optimización"))
    win.text_view_logs_auto = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    win._dialog_logs_auto = None

    def _abrir_logs():
        if win._dialog_logs_auto is None:
            scrolled = Gtk.ScrolledWindow(min_content_height=400, vexpand=True)
            scrolled.set_child(win.text_view_logs_auto)
            win._dialog_logs_auto = Adw.Dialog()
            ancho = win.get_width()
            win._dialog_logs_auto.set_content_width(max(ancho - 40, 400))
            win._dialog_logs_auto.set_content_height(500)
            win._dialog_logs_auto.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
            win._dialog_logs_auto.set_child(scrolled)
        win._dialog_logs_auto.present(win)

    btn_logs = Adw.ActionRow(
        title=traducir("Terminal de Análisis"),
        subtitle=traducir("Registro técnico detallado"),
        icon_name="utilities-terminal-symbolic",
    )
    btn_logs.set_activatable(True)
    btn_logs.connect("activated", lambda *_: _abrir_logs())
    grupo_logs_auto.add(btn_logs)

    grupo_visual = Adw.PreferencesGroup(title=traducir("Análisis en Tiempo Real"))
    caja_visual = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=6, margin_bottom=6)

    frame_grafico = Gtk.Frame(css_classes=["card"])
    frame_grafico.set_child(win.grafico)
    caja_visual.append(frame_grafico)

    win.box_leyenda = Adw.WrapBox(
        halign=Gtk.Align.CENTER,
        child_spacing=12,
        line_spacing=12,
        margin_top=10
    )
    caja_visual.append(win.box_leyenda)
    grupo_visual.add(caja_visual)

    def _mostrar_acerca_radar():
        dlg = Adw.Dialog(title=traducir("Acerca del Radar"))
        dlg.set_content_width(550)
        dlg.set_content_height(500)
        dlg.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
        cols = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)

        lbl_tit = Gtk.Label(
            label=traducir("Interpretación del Radar Comparativo"),
            css_classes=["title-3"], halign=Gtk.Align.START
        )
        cols.append(lbl_tit)

        desc = (
            "Cada eje representa una métrica de rendimiento. "
            "El polígono más grande indica mejor rendimiento general. "
            "El valor de cada scheduler se normaliza contra el mejor en cada categoría (100%)."
        )
        cols.append(Gtk.Label(label=traducir(desc), wrap=True, xalign=0, css_classes=["dim-label"]))

        cols.append(Gtk.Separator())

        lbl_cat_tit = Gtk.Label(
            label=traducir("Categorías"),
            css_classes=["heading"], halign=Gtk.Align.START
        )
        cols.append(lbl_cat_tit)

        from core.tipos import TIPOS
        for t in TIPOS:
            fila = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_cat = Gtk.Label(label=traducir(t.nombre_visible.replace("\n", " ")), css_classes=["accent"], xalign=0)
            lbl_cat.set_size_request(160, -1)
            fila.append(lbl_cat)
            if t.es_hibrido:
                lbl_met = Gtk.Label(
                    label=traducir("Latencia (menor = mejor)"),
                    xalign=0, css_classes=["dim-label"], wrap=True
                )
            elif t.clave == "threads":
                lbl_met = Gtk.Label(
                    label=traducir("Rendimiento por núcleo (mayor = mejor)"),
                    xalign=0, css_classes=["dim-label"], wrap=True
                )
            else:
                lbl_met = Gtk.Label(
                    label=traducir("Eficiencia combinada (mayor = mejor)"),
                    xalign=0, css_classes=["dim-label"], wrap=True
                )
            fila.append(lbl_met)
            cols.append(fila)

        cols.append(Gtk.Separator())

        lbl_score_tit = Gtk.Label(
            label=traducir("Puntuación Final"),
            css_classes=["heading"], halign=Gtk.Align.START
        )
        cols.append(lbl_score_tit)

        texto_score = (
            "El puntaje final combina 3 ejes:\n"
            "• Potencia — rendimiento bruto (throughput)\n"
            "• Respuesta — latencia y agilidad\n"
            "• Fluidez — eficiencia de CPU y consistencia\n"
        )
        cols.append(Gtk.Label(label=traducir(texto_score), wrap=True, xalign=0, css_classes=["dim-label"]))

        cols.append(Gtk.Separator())

        lbl_formula_tit = Gtk.Label(
            label=traducir("Fórmula"),
            css_classes=["heading"], halign=Gtk.Align.START
        )
        cols.append(lbl_formula_tit)

        formula = (
            "Para cada categoría:\n"
            "  r_pot = mi_valor / mejor_valor  (throughput)\n"
            "  r_pot = mejor_valor / mi_valor  (latencia)\n"
            "  r_lat = mejor_p95 / mi_p95\n"
            "  r_flu = max(0.01, 1.0 - waste)\n\n"
            "  score = r_pot × P_pot + r_lat × P_lat + r_flu × P_flu\n\n"
            "Puntaje final:\n"
            "  media_armónica(scores) × 100\n\n"
            "Pesos por defecto: Potencia 45%, Respuesta 45%, Fluidez 10%"
        )
        cols.append(Gtk.Label(label=traducir(formula), wrap=True, xalign=0,
                              css_classes=["dim-label"], margin_start=8, selectable=True))

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(cols)
        dlg.set_child(scrolled)
        dlg.present(win)

    btn_acerca = Gtk.Button(icon_name="help-about-symbolic", css_classes=["flat", "circular"],
                            tooltip_text=traducir("Cómo leer este gráfico"))
    btn_acerca.connect("clicked", lambda b: _mostrar_acerca_radar())
    grupo_visual.set_header_suffix(btn_acerca)

    # ── Ajustar Pesos ──
    grupo_pesos = Adw.PreferencesGroup(title=traducir("Ajustar Pesos"))
    win.revealer_recalc = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, reveal_child=False)
    win.barra_recalc = Gtk.ProgressBar(width_request=80, height_request=6, hexpand=False, valign=Gtk.Align.CENTER)
    win.barra_recalc.set_fraction(0.0)
    win.revealer_recalc.set_child(win.barra_recalc)

    preset_btns_data = [
        ("object-select-symbolic", traducir("Balanceado"), 45, 45, 10),
        (POTENCIA, traducir("Potencia"), 70, 20, 10),
        (RESPUESTA, traducir("Respuesta"), 10, 70, 20),
        (FLUIDEZ, traducir("Fluidez"), 10, 20, 70),
    ]
    win._preset_btns = []
    for icon_name, tooltip, p_pot, p_resp, p_flu in preset_btns_data:
        btn = Gtk.ToggleButton(css_classes=["flat", "circular"])
        btn.set_child(Gtk.Image(icon_name=icon_name, pixel_size=14))
        btn.set_tooltip_text(tooltip)
        def _on_toggled(b, pp=p_pot, pr=p_resp, pf=p_flu):
            if b.get_active():
                for otro in win._preset_btns:
                    if otro is not b:
                        otro.set_active(False)
                aplicar_preset(win, pp, pr, pf)
        btn.connect("toggled", _on_toggled)
        win._preset_btns.append(btn)
    info_btn = Gtk.Button(icon_name="dialog-information-symbolic", css_classes=["flat", "circular"], tooltip_text=traducir("Modifique la importancia de cada dimensión. Los resultados se recalculan al instante."))
    info_btn.connect("clicked", lambda b: on_info_click(win, b))
    suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    suffix_box.append(win.revealer_recalc)
    for btn in win._preset_btns:
        suffix_box.append(btn)
    suffix_box.append(info_btn)
    grupo_pesos.set_header_suffix(suffix_box)

    win._row_pot, win.slider_pot, win._lbl_pot = crear_slider_peso(traducir("Potencia"), POTENCIA, "#e66100", 45)
    win._row_resp, win.slider_resp, win._lbl_resp = crear_slider_peso(traducir("Respuesta"), RESPUESTA, "#26a269", 45)
    win._row_flu, win.slider_flu, win._lbl_flu = crear_slider_peso(traducir("Fluidez"), FLUIDEZ, "#9a99fa", 10)

    grupo_pesos.add(win._row_pot)
    grupo_pesos.add(win._row_resp)
    grupo_pesos.add(win._row_flu)

    win._preset_btns[0].set_active(True)

    win.slider_pot.connect("value-changed", lambda s: on_peso_changed(win, s))
    win.slider_resp.connect("value-changed", lambda s: on_peso_changed(win, s))
    win.slider_flu.connect("value-changed", lambda s: on_peso_changed(win, s))

    pref_page.add(grupo_auto)
    pref_page.add(grupo_visual)
    pref_page.add(win.grupo_ganador)
    pref_page.add(grupo_pesos)
    pref_page.add(grupo_logs_auto)

    header = Adw.HeaderBar()

    btn_info = Gtk.MenuButton(icon_name="dialog-information-symbolic")
    popover = Gtk.Popover()
    lbl_info = Gtk.Label(label=traducir("Procure no tener nada abierto\npara no afectar el análisis."), margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    popover.set_child(lbl_info)
    btn_info.set_popover(popover)

    win.btn_hist = Gtk.MenuButton(
        direction=Gtk.ArrowType.DOWN,
        tooltip_text=traducir("Historial de runs"),
        css_classes=["flat"],
        always_show_arrow=True,
    )
    win.popover_hist = Gtk.Popover()
    win.popover_hist.set_size_request(320, 350)
    win.btn_hist.set_popover(win.popover_hist)

    header.pack_start(btn_info)
    header.set_title_widget(win.btn_hist)

    btn_borrar = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=traducir("Limpiar Análisis"))
    btn_borrar.connect("clicked", lambda b: limpiar_ranking_auto(win, b))
    header.pack_end(btn_borrar)

    refrescar_historial(win)
    _refrescar_auto_schedulers(win)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_automatizacion.set_child(view)
