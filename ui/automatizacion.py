"""
Pestaña de Automatización: Detección inteligente del mejor scheduler.
"""

import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.benchmark import correr_benchmark
from core.hybrid import correr_hybrid
from core.scoring import calcular_scores_finales, calcular_mejores, calcular_score_categorias, media_armonica, calcular_valor_grafico, _MAPA_CHART, HYBRID_TYPES
from core.database import guardar_run, guardar_resultados_batch
from utils.helpers import log
from widgets.legend import crear_chip_leyenda


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

    win.btn_auto = Gtk.Button(label="Determinar", css_classes=["suggested-action"], margin_top=6, margin_bottom=12)
    win.btn_auto.connect("clicked", lambda b: gestionar_click_auto(win, b))
    grupo_auto.add(win.btn_auto)

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

    pref_page.add(grupo_auto)
    pref_page.add(grupo_visual)
    pref_page.add(win.grupo_ganador)
    pref_page.add(grupo_logs_auto)

    header = Adw.HeaderBar()

    btn_info = Gtk.MenuButton(icon_name="dialog-information-symbolic")
    popover = Gtk.Popover()
    lbl_info = Gtk.Label(label="Procure no tener nada abierto\npara no afectar el análisis.", margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    popover.set_child(lbl_info)
    btn_info.set_popover(popover)
    header.pack_start(btn_info)

    btn_borrar = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Limpiar Análisis")
    btn_borrar.connect("clicked", lambda b: limpiar_ranking_auto(win, b))
    header.pack_end(btn_borrar)

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
        if win.compatibles is None:
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
        lista_sc = win.compatibles if win.compatibles is not None else []
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


def finalizar_auto_test_success(win):
    """Finaliza el test automático con éxito y muestra los resultados."""
    finalizar_auto_test(win, win.ganador_final)
    win.fila_ganador.set_subtitle(win.desc_final)

    while (c := win.box_resultados.get_first_child()):
        win.box_resultados.remove(c)

    if hasattr(win, '_scores_finales'):
        for f in win._filas_ranking:
            win.fila_ganador.remove(f)
        win._filas_ranking.clear()

        ordenados = sorted(win._scores_finales.items(), key=lambda x: x[1]['score'], reverse=True)

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
    win.grafico.queue_draw()
