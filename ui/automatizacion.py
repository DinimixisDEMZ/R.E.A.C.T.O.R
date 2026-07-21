"""
Pestaña de Automatización: Detección inteligente del mejor scheduler.
"""

import datetime
import random
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.benchmark import correr_benchmark
from core.hybrid import correr_hybrid
from core.scoring import calcular_scores_finales, calcular_mejores, calcular_score_categorias, media_armonica, calcular_valor_grafico, _MAPA_CHART, HYBRID_TYPES
from core.database import guardar_run, guardar_resultados_batch, consultar_runs_auto, cargar_resultados_de_run
from utils.helpers import log
from widgets.legend import crear_chip_leyenda


def _animar_sliders(win, d_pot, d_resp, d_flu, callback=None):
    s_pot = win.slider_pot.get_value()
    s_resp = win.slider_resp.get_value()
    s_flu = win.slider_flu.get_value()
    state = {"paso": 0, "total": 12}

    def tick():
        state["paso"] += 1
        t = state["paso"] / state["total"]
        t = t * (2 - t)
        win.slider_pot.set_value(s_pot + (d_pot - s_pot) * t)
        win.slider_resp.set_value(s_resp + (d_resp - s_resp) * t)
        win.slider_flu.set_value(s_flu + (d_flu - s_flu) * t)

        if state["paso"] >= state["total"]:
            if callback:
                callback()
            return False
        return True

    GLib.timeout_add(16, tick)


def _toggle_all_scheds(win, state):
    for nombre, (row, check) in win._auto_sched_checks.items():
        check.set_active(state)
    _actualizar_subtitulo_scheds(win)


def _actualizar_subtitulo_scheds(win):
    if not win._auto_sched_checks:
        return
    total = len(win._auto_sched_checks)
    checked = sum(1 for _, c in win._auto_sched_checks.values() if c.get_active())
    win._auto_expander.set_subtitle(f"{checked}/{total} seleccionados")
    win._auto_expander.set_visible(total > 0)


def _refrescar_auto_schedulers(win):
    """Actualiza la lista de schedulers seleccionables para el test automático."""
    try:
        if win.modo_desarrollador:
            nombres = win.scx.obtener_lista()
        else:
            nombres = win.scx.obtener_lista(win.compatibles) if win.compatibles is not None else []
    except Exception:
        nombres = []

    # Filas existentes
    existentes = set(win._auto_sched_checks.keys())

    # Eliminar las que ya no están
    for nombre in existentes - set(nombres):
        row, _ = win._auto_sched_checks.pop(nombre)
        win._auto_expander.remove(row)

    # Añadir las nuevas
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


def setup_automatizacion_ui(win):
    """Construye la interfaz de la pestaña Automatización (Detección Inteligente).
    
    Args:
        win: Instancia de VentanaSimple
    """
    pref_page = Adw.PreferencesPage()
    grupo_auto = Adw.PreferencesGroup(title="Detección Inteligente", description="Escanear rendimiendo general de cada planificador y obtener el mejor.")

    win.barra_progreso = Gtk.ProgressBar(margin_top=6, margin_bottom=6, visible=False)
    win.revealer_tiempo = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
    win.label_tiempo = Gtk.Label(label="", css_classes=["caption", "dim-label"], margin_bottom=12)
    win.revealer_tiempo.set_child(win.label_tiempo)

    grupo_auto.add(win.barra_progreso)
    grupo_auto.add(win.revealer_tiempo)

    win.btn_auto = Gtk.Button(label="Determinar", css_classes=["suggested-action"], margin_top=6, margin_bottom=6)
    win.btn_auto.connect("clicked", lambda b: gestionar_click_auto(win, b))
    grupo_auto.add(win.btn_auto)

    win._auto_sched_checks = {}
    win._auto_expander = Adw.ExpanderRow(
        title="Planificadores a analizar",
        subtitle="",
        icon_name="org.gnome.Settings-applications-symbolic",
        expanded=False
    )

    btn_select_all = Gtk.Button(label="Marcar todos", css_classes=["flat"])
    btn_select_none = Gtk.Button(label="Desmarcar todos", css_classes=["flat"])
    btn_select_all.connect("clicked", lambda b: _toggle_all_scheds(win, True))
    btn_select_none.connect("clicked", lambda b: _toggle_all_scheds(win, False))

    toggle_row = Adw.ActionRow(title="Seleccionar:")
    caja_btn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    caja_btn.append(btn_select_all)
    caja_btn.append(btn_select_none)
    toggle_row.add_suffix(caja_btn)
    win._auto_expander.add_row(toggle_row)

    grupo_auto.add(win._auto_expander)

    win.grupo_ganador = Adw.PreferencesGroup(title="Resultado del Diagnóstico")
    win.fila_ganador = Adw.ExpanderRow(title="Motor en reposo", subtitle="Esperando al escaneo...", icon_name="org.gnome.Settings-device-diagnostics-symbolic")
    win.box_resultados = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
    win.fila_ganador.add_suffix(win.box_resultados)
    win._filas_ranking = []
    win.grupo_ganador.add(win.fila_ganador)

    grupo_logs_auto = Adw.PreferencesGroup(title="Registro de Optimización")
    win.expander_logs_auto = Adw.ExpanderRow(title="Terminal de Análisis", subtitle="Registro técnico detallado", icon_name="utilities-terminal-symbolic")

    win.text_view_logs_auto = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    caja_log_auto = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    scrolled_auto = Gtk.ScrolledWindow(min_content_height=300, vexpand=True)
    scrolled_auto.set_child(win.text_view_logs_auto)
    caja_log_auto.append(scrolled_auto)
    win.expander_logs_auto.add_row(caja_log_auto)
    grupo_logs_auto.add(win.expander_logs_auto)

    grupo_visual = Adw.PreferencesGroup(title="Análisis en Tiempo Real")
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

    win._historial_runs = []
    win._indice_historial = -1
    win._ajustando_pesos = False

    # ── Ajustar Pesos: one group, header suffix = recalc + icons ──
    grupo_pesos = Adw.PreferencesGroup(title="Ajustar Pesos")
    win.revealer_recalc = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, reveal_child=False)
    win.barra_recalc = Gtk.ProgressBar(width_request=80, height_request=6, hexpand=False, valign=Gtk.Align.CENTER)
    win.barra_recalc.set_fraction(0.0)
    win.revealer_recalc.set_child(win.barra_recalc)

    preset_btns_data = [
        ("object-select-symbolic", "Balanceado", 45, 45, 10),
        ("power-profile-performance-symbolic", "Potencia", 70, 20, 10),
        ("preferences-system-time-symbolic", "Respuesta", 10, 70, 20),
        ("weather-windy-symbolic", "Fluidez", 10, 20, 70),
    ]
    win._preset_btns = []
    for icon_name, tooltip, p_pot, p_resp, p_flu in preset_btns_data:
        btn = Gtk.ToggleButton(css_classes=["flat", "circular"])
        btn.set_child(Gtk.Image(icon_name=icon_name, pixel_size=14))
        btn.set_tooltip_text(tooltip)
        btn.connect("toggled", lambda b, pp=p_pot, pr=p_resp, pf=p_flu: _aplicar_preset(win, pp, pr, pf, b))
        win._preset_btns.append(btn)
    win._info_clicks = 0
    _chistes_peso = [
        "Si ajustas los pesos y nada cambia, no es el scheduler... eres tú.",
        "45/45/10 es como pedir pizza: todos dicen que quieren lo mismo, pero nadie está conforme.",
        "Un scheduler justo no existe. Solo hay planificadores menos injustos.",
        "Si pones todo en 33%, obtienes... un scheduler que no sabe qué priorizar.",
        "La fluidez no es lo mismo que ir rápido. Es no quedarse sin gasolina a mitad de carrera.",
        "Fun fact: Linus Torvalds no ajusta sliders. Usa un stick.",
        "¿Más potencia? Tu Ryzen 7 ya está dando todo. Respira.",
        "Los pesos son como las reglas de la primera noche... siempre hay un traitor.",
        "Si el scheduler te pregunta por qué lo torturas, dile que es para su bien.",
        "Dato curioso: el 99% de los ajustes de pesos son placebo. Pero el 1% restante... también.",
        "Ajustar pesos es como arreglar un auto en marcha. Divertido hasta que algo explota.",
        "Si te gusta el botón, dale otra vez. No tengo vida.",
        "¿Otra vez? ¿Acaso esto es un benchmark de clicks?",
        "Contador oficial: ya llevas {n} clics en una bombillita. Impresionante.",
    ]
    def _on_info_click(btn):
        win._info_clicks += 1
        idx = win._info_clicks - 1
        if idx < 3:
            msg = _chistes_peso[idx]
        elif idx < len(_chistes_peso) - 1:
            msg = random.choice(_chistes_peso[3:-1])
        else:
            msg = _chistes_peso[-1].format(n=win._info_clicks)
        win.toast_overlay.add_toast(Adw.Toast.new(msg))
    info_btn = Gtk.Button(icon_name="dialog-information-symbolic", css_classes=["flat", "circular"], tooltip_text="Modifique la importancia de cada dimensión. Los resultados se recalculan al instante.")
    info_btn.connect("clicked", _on_info_click)
    suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    suffix_box.append(win.revealer_recalc)
    for btn in win._preset_btns:
        suffix_box.append(btn)
    suffix_box.append(info_btn)
    grupo_pesos.set_header_suffix(suffix_box)

    def _crear_slider_peso(nombre, icono, color_hex, default):
        row = Adw.PreferencesRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        img = Gtk.Image(icon_name=icono, pixel_size=14, css_classes=["dim-label"])
        adj = Gtk.Adjustment(value=default, lower=0, upper=100, step_increment=1, page_increment=10)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj, hexpand=True, draw_value=False)
        lbl_val = Gtk.Label(label=f"{default:.0f}%", css_classes=["dim-label"], width_chars=4, xalign=1)
        box.append(img)
        box.append(scale)
        box.append(lbl_val)
        row.set_child(box)
        return row, scale, lbl_val

    win._row_pot, win.slider_pot, win._lbl_pot = _crear_slider_peso("Potencia", "power-profile-performance-symbolic", "#e66100", 45)
    win._row_resp, win.slider_resp, win._lbl_resp = _crear_slider_peso("Respuesta", "preferences-system-time-symbolic", "#26a269", 45)
    win._row_flu, win.slider_flu, win._lbl_flu = _crear_slider_peso("Fluidez", "weather-windy-symbolic", "#9a99fa", 10)

    grupo_pesos.add(win._row_pot)
    grupo_pesos.add(win._row_resp)
    grupo_pesos.add(win._row_flu)

    win._preset_btns[0].set_active(True)

    win._peso_timer = 0
    win._recalc_timer = 0

    def _actualizar_lbls(win):
        win._lbl_pot.set_label(f"{win.slider_pot.get_value():.0f}%")
        win._lbl_resp.set_label(f"{win.slider_resp.get_value():.0f}%")
        win._lbl_flu.set_label(f"{win.slider_flu.get_value():.0f}%")

    def _finalizar_ajuste_pesos(win):
        win._peso_timer = 0

        pot = win.slider_pot.get_value()
        resp = win.slider_resp.get_value()
        flu = win.slider_flu.get_value()
        total = pot + resp + flu

        if total == 0:
            _aplicar_preset(win, 45, 45, 10, None)
            return False

        if total != 100:
            factor = 100.0 / total
            new_pot = round(pot * factor)
            new_resp = round(resp * factor)
            new_flu = round(flu * factor)

            win._ajustando_pesos = True

            def despues():
                _actualizar_lbls(win)
                win._ajustando_pesos = False
                if hasattr(win, '_brutos_finales') and win._brutos_finales:
                    _recalcular_ranking(win)

            _animar_sliders(win, new_pot, new_resp, new_flu, callback=despues)
            return False

        _actualizar_lbls(win)
        if hasattr(win, '_brutos_finales') and win._brutos_finales:
            _recalcular_ranking(win)

        return False

    def _on_peso_changed(win, slider):
        if win._ajustando_pesos:
            return

        pot = win.slider_pot.get_value()
        resp = win.slider_resp.get_value()
        flu = win.slider_flu.get_value()
        total = pot + resp + flu

        if total != 100 and total != 0:
            if slider is win.slider_pot:
                v1, v2 = resp, flu
                s1, s2 = win.slider_resp, win.slider_flu
            elif slider is win.slider_resp:
                v1, v2 = pot, flu
                s1, s2 = win.slider_pot, win.slider_flu
            else:
                v1, v2 = pot, resp
                s1, s2 = win.slider_pot, win.slider_resp

            remaining = 100 - slider.get_value()
            if v1 + v2 == 0 or remaining <= 0:
                n1 = n2 = round(remaining / 2) if remaining > 0 else 0
            else:
                n1 = round(remaining * v1 / (v1 + v2))
                n2 = remaining - n1

            win._ajustando_pesos = True
            s1.set_value(n1)
            s2.set_value(n2)
            win._ajustando_pesos = False

        _actualizar_lbls(win)

        if win._peso_timer > 0:
            GLib.source_remove(win._peso_timer)
        win._peso_timer = GLib.timeout_add(200, _finalizar_ajuste_pesos, win)

    win.slider_pot.connect("value-changed", lambda s: _on_peso_changed(win, s))
    win.slider_resp.connect("value-changed", lambda s: _on_peso_changed(win, s))
    win.slider_flu.connect("value-changed", lambda s: _on_peso_changed(win, s))

    pref_page.add(grupo_auto)
    pref_page.add(grupo_visual)
    pref_page.add(win.grupo_ganador)
    pref_page.add(grupo_pesos)
    pref_page.add(grupo_logs_auto)

    header = Adw.HeaderBar()

    btn_info = Gtk.MenuButton(icon_name="dialog-information-symbolic")
    popover = Gtk.Popover()
    lbl_info = Gtk.Label(label="Procure no tener nada abierto\npara no afectar el análisis.", margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    popover.set_child(lbl_info)
    btn_info.set_popover(popover)

    win.btn_nav_prev = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Run anterior", sensitive=False)
    win.btn_nav_next = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Run siguiente", sensitive=False)
    win.lbl_nav = Gtk.Label(label="", css_classes=["caption", "dim-label"], margin_start=6, margin_end=6)

    win.btn_nav_prev.connect("clicked", lambda b: _navegar_historial(win, -1))
    win.btn_nav_next.connect("clicked", lambda b: _navegar_historial(win, 1))

    header.pack_start(win.btn_nav_prev)
    header.pack_start(win.lbl_nav)
    header.pack_start(win.btn_nav_next)

    btn_borrar = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Limpiar Análisis")
    btn_borrar.connect("clicked", lambda b: limpiar_ranking_auto(win, b))
    header.pack_end(btn_borrar)
    header.pack_end(btn_info)

    _refrescar_historial(win)
    _refrescar_auto_schedulers(win)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_automatizacion.set_child(view)


def gestionar_click_auto(win, btn):
    """Maneja el click del botón Determinar/Detener."""
    if win.en_proceso_auto:
        win.en_proceso_auto = False
        win.btn_auto.set_label("Deteniendo...")
        win.btn_auto.set_sensitive(False)
        log(win.text_view_logs_auto, "DETENIENDO ANALISIS...", es_titulo=True)
    else:
        if win.compatibles is None and not win.modo_desarrollador:
            toast = Adw.Toast.new("Error: Primero verifique la 'Disponibilidad' para evitar bloqueos del sistema.")
            toast.set_priority(Adw.ToastPriority.HIGH)
            win.toast_overlay.add_toast(toast)
            win.split_view.set_content(win.pag_disponibilidad)
            return

        win.solicitar_sudo_si_necesario(lambda: iniciar_auto_test(win, btn))


def iniciar_auto_test(win, btn):
    """Inicia el ciclo completo de detección automatizada."""
    win.en_proceso_auto = True
    win.grafico.iniciar_pulso()
    win.btn_auto.set_label("Detener")
    win.btn_auto.add_css_class("destructive-action")
    win.btn_auto.remove_css_class("suggested-action")
    win.btn_nav_prev.set_sensitive(False)
    win.btn_nav_next.set_sensitive(False)
    win.barra_progreso.set_visible(True)
    win.revealer_tiempo.set_reveal_child(True)

    win.progreso_actual = 0.0
    win.progreso_objetivo = 0.0
    win.segundos_actuales = 0.0
    win.segundos_objetivos = 0.0
    win.barra_progreso.set_fraction(0.0)

    # Animador unificado
    def animar_ui():
        diff_p = win.progreso_objetivo - win.progreso_actual
        if abs(diff_p) > 0.001:
            win.progreso_actual += diff_p * 0.1
            win.barra_progreso.set_fraction(win.progreso_actual)

        diff_t = win.segundos_objetivos - win.segundos_actuales
        if abs(diff_t) > 0.1:
            win.segundos_actuales += diff_t * 0.08

        segs_visibles = max(0, win.segundos_actuales)
        m, s = int(segs_visibles // 60), int(segs_visibles % 60)
        win.label_tiempo.set_label(f"Tiempo estimado restante: {m:02d}:{s:02d}")

        if not win.en_proceso_auto and abs(diff_p) < 0.001 and abs(diff_t) < 0.1:
            return False
        return True

    GLib.timeout_add(16, animar_ui)

    # Resetear gráfico
    win.grafico.datos_raw = {}
    win.grafico.num_categorias = 6
    win.grafico.categorias = [
        "Context\nSwitch", "Carga\nMixta", "Mutex",
        "Fork", "Compile", "Bajo\nCarga"
    ]
    win.grafico.valores_animados = {}
    while (c := win.box_leyenda.get_first_child()):
        win.box_leyenda.remove(c)
    while (c := win.box_resultados.get_first_child()):
        win.box_resultados.remove(c)

    def motor():
        lista_sc = [n for n, (r, c) in win._auto_sched_checks.items() if c.get_active()]
        lista = ["Sistema Base"] + lista_sc
        total_steps = len(lista) * 6  # 3 stress-ng + 3 hyperfine
        brutos = {}

        for s in lista:
            GLib.idle_add(win.grafico.registrar_scheduler, s)
            GLib.idle_add(crear_chip_leyenda, s, win.grafico, win.box_leyenda)

        # Calibración Térmica
        log(win.text_view_logs_auto, "Calibrando base térmica del sistema...", es_titulo=True)
        temp_base = win.sensor.calibrar(muestras=3, intervalo=0.5)

        if not win.en_proceso_auto:
            return

        if temp_base < 10:
            log(win.text_view_logs_auto, "Sensor térmico no disponible. Omitiendo gestión de calor.")
            umbral_enfriamiento = 999
        else:
            umbral_enfriamiento = temp_base + 5.0
            log(win.text_view_logs_auto, f"Calibración completa: Base {temp_base:.1f}°C | Umbral: {umbral_enfriamiento:.1f}°C")

        for sc in lista:
            if not win.en_proceso_auto:
                break

            # Enfriamiento dinámico
            temp_actual = win.sensor.obtener_temp()
            if temp_actual > umbral_enfriamiento:
                GLib.idle_add(win.fila_ganador.set_subtitle, f"Estabilizando térmica... ({temp_actual:.1f}°C)")
                log(win.text_view_logs_auto, f"Enfriando: {temp_actual:.1f}°C -> objetivo {umbral_enfriamiento:.1f}°C")

                timeout_cool = 0
                max_wait = 15
                while temp_actual > umbral_enfriamiento and win.en_proceso_auto and timeout_cool < max_wait:
                    time.sleep(1)
                    timeout_cool += 1
                    temp_actual = win.sensor.obtener_temp()
                    GLib.idle_add(win.fila_ganador.set_subtitle, f"Enfriando... {temp_actual:.1f}°C (Límite: {umbral_enfriamiento:.1f}°C)")

                if timeout_cool >= max_wait:
                    log(win.text_view_logs_auto, "Continuando análisis (umbral térmico no alcanzado).", es_error=True)

            if not win.en_proceso_auto:
                break

            if sc == "Sistema Base":
                log(win.text_view_logs_auto, "Evaluando rendimiento nativo del Kernel...", es_titulo=True)
                win.scx.ejecutar_con_sudo(["scxctl", "stop"])
                time.sleep(1)
            else:
                log(win.text_view_logs_auto, f"Limpiando para {sc}...")
                win.scx.detener_todos()
                time.sleep(2)

                log(win.text_view_logs_auto, f"Activando {sc}...", es_titulo=True)
                res_switch = win.scx.ejecutar_con_sudo(["scxctl", "start", "-s", sc, "-m", "auto"])
                if res_switch.returncode != 0:
                    err_kernel = res_switch.stderr.strip() or "El scheduler no respondió a la señal de inicio."
                    err_safe = GLib.markup_escape_text(err_kernel)
                    log(win.text_view_logs_auto, f"FALLO: No se pudo activar {sc}.\nDetalle: {err_kernel}", es_error=True)
                    GLib.idle_add(win.fila_ganador.set_subtitle, f"Error: {err_safe[:50]}...")
                    win.scx.ejecutar_con_sudo(["scxctl", "stop"])
                    time.sleep(1)
                    continue

            time.sleep(1.5)

            # Pruebas de rendimiento (stress-ng + hyperfine)
            brutos[sc] = {}
            tests_prog = ["cpu", "threads", "memory", "fork", "compile", "loaded"]
            nombres_test = ["Latencia", "Multitarea", "Eficiencia", "Fork+Exec", "Compilación", "Bajo Carga"]

            for idx, t in enumerate(tests_prog):
                if not win.en_proceso_auto:
                    break

                puntos_progreso = (lista.index(sc) * 6) + (idx + 1)
                win.progreso_objetivo = puntos_progreso / total_steps

                scheds_restantes = len(lista) - (lista.index(sc) + 1)
                tests_restantes_en_este = 6 - idx
                win.segundos_objetivos = (scheds_restantes * 20) + (tests_restantes_en_este * 5) + 2

                GLib.idle_add(win.fila_ganador.set_subtitle, f"Analizando {sc}: {nombres_test[idx]} ({idx+1}/6)...")

                if t in HYBRID_TYPES:
                    res = correr_hybrid(t, win.scx, win.text_view_logs_auto, tiempo=5, logs=True, modo_dev=win.modo_desarrollador)
                else:
                    res = correr_benchmark(t, win.scx, win.text_view_logs_auto, tiempo=5, logs=True, modo_dev=win.modo_desarrollador)
                if res:
                    if sc not in brutos:
                        brutos[sc] = {}
                    brutos[sc][t] = res

                    chart_idx = _MAPA_CHART.get(t)
                    if chart_idx is not None:
                        val_v = calcular_valor_grafico(res, t)
                        GLib.idle_add(win.grafico.actualizar_dato, sc, chart_idx, val_v)

            # Líder al vuelo
            if len(brutos) >= 2:
                mejores_vuelo = calcular_mejores(brutos, tipos=("cpu", "threads", "memory", "latencia_fork", "latencia_compile", "latencia_loaded"))
                scores_parciales = {}

                for s_name, s_data in brutos.items():
                    cat_scores, _, _, _ = calcular_score_categorias(s_data, mejores_vuelo)
                    if cat_scores:
                        scores_parciales[s_name] = media_armonica(cat_scores)

                if scores_parciales:
                    lider_vuelo = max(scores_parciales, key=scores_parciales.get)
                    GLib.idle_add(win.fila_ganador.set_title, f"Mejor Equilibrio: {lider_vuelo}")

        # Guardar resultados siempre que haya datos (parciales o completos)
        all_results = []
        if brutos:
            run_type = "auto" if win.en_proceso_auto else "auto_parcial"
            run_id = guardar_run(win.versiones, run_type=run_type)
            for sc_name, sc_tests in brutos.items():
                for test_type, res in sc_tests.items():
                    all_results.append(res)
            guardar_resultados_batch(run_id, all_results)

        if win.en_proceso_auto:
            if brutos:
                log(win.text_view_logs_auto, "-" * 50)
                log(win.text_view_logs_auto, "ANÁLISIS FINAL (Potencia 45% | Respuesta 45% | Fluidez 10%)", True)
                log(win.text_view_logs_auto, "Buscando el mejor equilibrio entre rendimiento bruto y agilidad.")

                scores_finales = calcular_scores_finales(brutos)

                for sc, data in scores_finales.items():
                    log(win.text_view_logs_auto, f"• {sc.upper().ljust(8)} | Score: {data['score']:.1f}% (Pot: {data['pot']/100:.2f}, Resp: {data['resp']/100:.2f}, Flz: {data['flu']/100:.2f})")

                win.ganador_final = max(scores_finales.keys(), key=lambda s: scores_finales[s]["score"])
                winner_score = scores_finales[win.ganador_final]["score"]
                win._scores_finales = scores_finales
                win._brutos_finales = brutos

                GLib.idle_add(win.fila_ganador.set_title, f"Mejor Planificador: {win.ganador_final}")
                win.desc_final = f"'{win.ganador_final}' ofrece la mejor propuesta integral con un {winner_score:.1f}% de eficacia de sistema."
                GLib.idle_add(lambda: finalizar_auto_test_success(win))
            else:
                GLib.idle_add(lambda: finalizar_auto_test(win, None))
        else:
            log(win.text_view_logs_auto, f"ANÁLISIS DETENIDO — {len(all_results)} resultado(s) guardado(s)", True)
            GLib.idle_add(lambda: finalizar_auto_test(win, None))

    threading.Thread(target=motor, daemon=True).start()


def _poblar_ranking(win, pesos=None):
    """Puebla la lista de ranking en fila_ganador usando _brutos_finales con pesos opcionales."""
    if not hasattr(win, '_brutos_finales') or not win._brutos_finales:
        _sincronizar_estado_pesos(win, False)
        return

    _sincronizar_estado_pesos(win, True)

    for f in win._filas_ranking:
        win.fila_ganador.remove(f)
    win._filas_ranking.clear()

    if pesos:
        total = sum(pesos)
        if total > 0:
            pesos_norm = tuple(p / total for p in pesos)
        else:
            pesos_norm = (0.45, 0.45, 0.10)
    else:
        pesos_norm = (0.45, 0.45, 0.10)

    scores = calcular_scores_finales(win._brutos_finales, pesos=pesos_norm)
    win._scores_finales = scores

    ordenados = sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)

    for idx, (name, data) in enumerate(ordenados):
        row = Adw.ActionRow(title=name)
        row.set_subtitle(
            f"Potencia: {data['pot']/100:.2f} | "
            f"Respuesta: {data['resp']/100:.2f} | "
            f"Fluidez: {data['flu']/100:.2f}"
        )

        talla_pct = max(70, 180 - (idx * 30))
        opacidad = max(0.4, 1.0 - (idx * 0.15))

        cls = ["bold"]
        if idx < 2:
            cls.append("accent")

        lbl_score = Gtk.Label(css_classes=cls)
        lbl_score.set_markup(f"<span size='{talla_pct}%'>{data['score']:.1f}%</span>")
        lbl_score.set_opacity(opacidad)
        row.add_suffix(lbl_score)

        if idx == 0:
            win.fila_ganador.set_expanded(True)
            row.add_css_class("success")

        win.fila_ganador.add_row(row)
        win._filas_ranking.append(row)

    # Recalcular ganador
    ganador = ordenados[0][0] if ordenados else None
    if ganador:
        win.ganador_final = ganador
        score_ganador = ordenados[0][1]['score']
        win.fila_ganador.set_title(f"Mejor Planificador: {ganador}")
        win.fila_ganador.set_subtitle(
            f"'{ganador}' ofrece la mejor propuesta integral con un {score_ganador:.1f}% de eficacia de sistema."
        )


def _aplicar_preset(win, p_pot, p_resp, p_flu, btn_activo=None):
    """Anima los sliders a un preset y desactiva los demás botones."""
    if btn_activo is not None:
        parent = btn_activo.get_parent()
        if parent is not None:
            child = parent.get_first_child()
            while child:
                if child is not btn_activo and hasattr(child, 'set_active'):
                    child.set_active(False)
                child = child.get_next_sibling()

    win._ajustando_pesos = True

    def despues():
        win._lbl_pot.set_label(f"{win.slider_pot.get_value():.0f}%")
        win._lbl_resp.set_label(f"{win.slider_resp.get_value():.0f}%")
        win._lbl_flu.set_label(f"{win.slider_flu.get_value():.0f}%")
        win._ajustando_pesos = False
        if hasattr(win, '_brutos_finales') and win._brutos_finales:
            _recalcular_ranking(win)

    _animar_sliders(win, p_pot, p_resp, p_flu, callback=despues)


def _sincronizar_estado_pesos(win, hay_datos):
    """Muestra/oculta filas de sliders y bloquea/habilita según hay_datos."""
    if not hasattr(win, '_row_pot'):
        return
    win._row_pot.set_visible(hay_datos)
    win._row_resp.set_visible(hay_datos)
    win._row_flu.set_visible(hay_datos)
    win._row_pot.set_sensitive(hay_datos)
    win._row_resp.set_sensitive(hay_datos)
    win._row_flu.set_sensitive(hay_datos)


def _mostrar_banner_recalc(win):
    if not hasattr(win, 'revealer_recalc'):
        return
    win.revealer_recalc.set_reveal_child(True)
    if win._recalc_timer:
        GLib.source_remove(win._recalc_timer)
    win._recalc_timer = GLib.timeout_add(50, lambda: win.barra_recalc.pulse() or True)

    def ocultar():
        if win._recalc_timer:
            GLib.source_remove(win._recalc_timer)
            win._recalc_timer = 0
        win.barra_recalc.set_fraction(0.0)
        win.revealer_recalc.set_reveal_child(False)
        return False

    GLib.timeout_add(600, ocultar)


def _recalcular_ranking(win):
    """Lee los sliders y repuebla el ranking."""
    _mostrar_banner_recalc(win)
    pot = win.slider_pot.get_value()
    resp = win.slider_resp.get_value()
    flu = win.slider_flu.get_value()
    _poblar_ranking(win, pesos=(pot, resp, flu))


def _reconstruir_brutos(resultados):
    """Convierte lista plana de resultados DB en dict {sched: {tipo: {valor, p95, fairness, modo}}}."""
    brutos = {}
    for r in resultados:
        sc = r["scheduler_name"]
        tt = r["test_type"]
        if sc not in brutos:
            brutos[sc] = {}
        brutos[sc][tt] = {
            "tipo": tt,
            "valor": r["valor"],
            "p95": r["p95"] or 1.0,
            "fairness": r["fairness"] or 0.5,
            "sched": sc,
            "modo": r.get("modo", "auto"),
        }
    return brutos


def _refrescar_historial(win):
    """Recarga la lista de runs desde la BD y actualiza los botones de navegación."""
    runs = consultar_runs_auto()
    win._historial_runs = runs

    if not runs:
        win._indice_historial = -1
        win.lbl_nav.set_label("")
        win.btn_nav_prev.set_sensitive(False)
        win.btn_nav_next.set_sensitive(False)
        return

    # Si estamos viendo el último run, mantener la posición al final
    if win._indice_historial < 0 or win._indice_historial >= len(runs):
        win._indice_historial = len(runs) - 1
    elif win._indice_historial == len(runs) - 2 and len(runs) > 0:
        # Nuevo run añadido al final, avanzar
        win._indice_historial = len(runs) - 1

    _actualizar_botones_nav(win)


def _actualizar_botones_nav(win):
    """Actualiza el estado y etiqueta de los botones de navegación."""
    idx = win._indice_historial
    total = len(win._historial_runs)

    win.btn_nav_prev.set_sensitive(idx > 0)
    win.btn_nav_next.set_sensitive(idx < total - 1)

    if 0 <= idx < total:
        ts = win._historial_runs[idx].get("timestamp", 0)
        dt = datetime.datetime.fromtimestamp(ts)
        win.lbl_nav.set_label(f"Run {idx+1}/{total} — {dt.strftime('%d/%m %H:%M')}")
    else:
        win.lbl_nav.set_label("")


def _navegar_historial(win, direccion):
    """Navega al run anterior (-1) o siguiente (+1) en el historial."""
    nuevo = win._indice_historial + direccion
    if nuevo < 0 or nuevo >= len(win._historial_runs):
        return

    win._indice_historial = nuevo
    resultados = cargar_resultados_de_run(win._historial_runs[nuevo]["id"])
    brutos = _reconstruir_brutos(resultados)

    if not brutos:
        win.toast_overlay.add_toast(Adw.Toast.new("Este run no contiene datos válidos."))
        return

    win._brutos_finales = brutos

    scores_finales = calcular_scores_finales(brutos)
    win.ganador_final = max(scores_finales.keys(), key=lambda s: scores_finales[s]["score"])

    # Repoblar gráfico radar
    win.grafico.datos_raw = {}
    win.grafico.valores_animados = {}
    win.grafico.max_por_categoria = [1.0] * win.grafico.num_categorias

    while (c := win.box_leyenda.get_first_child()):
        win.box_leyenda.remove(c)

    for sc, sdata in brutos.items():
        win.grafico.registrar_scheduler(sc)
        crear_chip_leyenda(sc, win.grafico, win.box_leyenda)

        for tt, res in sdata.items():
            chart_idx = _MAPA_CHART.get(tt)
            if chart_idx is not None:
                val_v = calcular_valor_grafico(res, tt)
                win.grafico.actualizar_dato(sc, chart_idx, val_v)

    win.grafico.queue_draw()

    # Mostrar ranking actualizado
    win.fila_ganador.set_expanded(True)
    win.btn_auto.set_label("Determinar")
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")

    if hasattr(win, '_ajustando_pesos'):
        _recalcular_ranking(win)
    else:
        _poblar_ranking(win)

    _actualizar_botones_nav(win)

    win.toast_overlay.add_toast(Adw.Toast.new(
        f"Cargado: {win._historial_runs[nuevo].get('kernel_version', '')}"
    ))


def finalizar_auto_test_success(win):
    """Finaliza el test automático con éxito y muestra los resultados."""
    finalizar_auto_test(win, win.ganador_final)
    win.fila_ganador.set_subtitle(win.desc_final)

    while (c := win.box_resultados.get_first_child()):
        win.box_resultados.remove(c)

    _poblar_ranking(win)

    _refrescar_historial(win)

    # Aplicar ganador automáticamente
    def _aplicar_ganador():
        try:
            if win.ganador_final == "Sistema Base":
                res = win.scx.ejecutar_con_sudo(["scxctl", "stop"])
            else:
                res = win.scx.ejecutar_con_sudo(["scxctl", "start", "-s", win.ganador_final, "-m", "auto"])

            if res.returncode != 0 and "not running" not in res.stderr.lower():
                raise Exception(res.stderr)

            GLib.idle_add(win.sincronizar_sistema)
            GLib.idle_add(lambda: win.toast_overlay.add_toast(
                Adw.Toast.new(f"Aplicado automáticamente: {win.ganador_final}")
            ))
        except Exception as e:
            err_msg = str(e)
            GLib.idle_add(lambda m=err_msg: win.toast_overlay.add_toast(
                Adw.Toast.new(f"Error al aplicar: {m}")
            ))

    threading.Thread(target=_aplicar_ganador, daemon=True).start()


def finalizar_auto_test(win, winner):
    """Restaura la UI tras finalizar la detección automática."""
    win.en_proceso_auto = False
    win.grafico.detener_pulso()
    win.btn_auto.set_label("Determinar")
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")
    _actualizar_botones_nav(win)
    win.progreso_objetivo = 1.0
    win.segundos_objetivos = 0.0
    win.revealer_tiempo.set_reveal_child(False)
    GLib.timeout_add(500, lambda: win.barra_progreso.set_visible(False))
    if winner:
        win.fila_ganador.set_title(f"Recomendado: {winner}")
        win.toast_overlay.add_toast(Adw.Toast.new(f"Detección finalizada: {winner}"))
    else:
        win.fila_ganador.set_title("Motor en reposo")
        win.fila_ganador.set_subtitle("Análisis interrumpido por el usuario.")
        win.fila_ganador.set_icon_name("org.gnome.Settings-device-diagnostics-symbolic")


def limpiar_ranking_auto(win, btn):
    """Limpia los resultados de la detección automática."""
    if win.en_proceso_auto:
        return
    win.text_view_logs_auto.get_buffer().set_text("")
    win.fila_ganador.set_title("Motor en reposo")
    win.fila_ganador.set_subtitle("Esperando al escaneo...")
    while (c := win.box_resultados.get_first_child()):
        win.box_resultados.remove(c)

    win.grafico.datos_raw = {}
    win.grafico.max_por_categoria = [1.0] * win.grafico.num_categorias
    win.grafico.valores_animados = {}
    while (c := win.box_leyenda.get_first_child()):
        win.box_leyenda.remove(c)

    for f in win._filas_ranking:
        win.fila_ganador.remove(f)
    win._filas_ranking.clear()

    win.fila_ganador.set_expanded(False)
    _sincronizar_estado_pesos(win, False)
    win.grafico.queue_draw()
