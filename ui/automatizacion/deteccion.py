"""
Motor de detección automática del mejor scheduler.
"""

import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.i18n import traducir
from core.constantes import INTERVALO_FRAME_MS, SISTEMA_BASE, CATEGORIAS_RADAR, TIPOS_PRUEBA, MOTOR_REPOSO, DETERMINAR
from core.benchmark import correr_benchmark
from core.hybrid import correr_hybrid
from core.scoring import (
    calcular_scores_finales, calcular_mejores, calcular_score_categorias,
    media_armonica,
)
from core.tipos import MAPA_CHART, valor_para_grafico, claves_hibridas
from core.database import guardar_run, guardar_resultados_batch, actualizar_log_run
from utils.logging import log, mostrar_toast
from utils.helpers import vaciar_contenedor
from widgets.legend import crear_chip_leyenda


def gestionar_click_auto(win, btn):
    """Maneja el click del botón Determinar/Detener."""
    if win.auto_state.en_proceso:
        win.auto_state.en_proceso = False
        win.btn_auto.set_label(traducir("Deteniendo..."))
        win.btn_auto.set_sensitive(False)
        log(win.text_view_logs_auto, "DETENIENDO ANALISIS...", nivel="title")
    else:
        if win.compatibles is None and not win.modo_desarrollador:
            toast = Adw.Toast.new(traducir("Error: Primero verifique la 'Disponibilidad' para evitar bloqueos del sistema."))
            toast.set_priority(Adw.ToastPriority.HIGH)
            win.toast_overlay.add_toast(toast)
            win.split_view.set_content(win.pag_disponibilidad)
            return

        win.solicitar_sudo_si_necesario(lambda: iniciar_auto_test(win, btn))


def _preparar_ui_auto_test(win):
    win.auto_state.en_proceso = True
    win.text_view_logs_auto.get_buffer().set_text("")
    win.grafico.iniciar_pulso()
    win.btn_auto.set_label(traducir("Detener"))
    win.btn_auto.add_css_class("destructive-action")
    win.btn_auto.remove_css_class("suggested-action")
    win.btn_hist.set_sensitive(False)
    win.barra_progreso.set_visible(True)
    win.revealer_tiempo.set_reveal_child(True)

    win.auto_state.progreso_actual = 0.0
    win.auto_state.progreso_objetivo = 0.0
    win.auto_state.segundos_actuales = 0.0
    win.auto_state.segundos_objetivos = 0.0
    win.barra_progreso.set_fraction(0.0)

    def animar_ui():
        diff_p = win.auto_state.progreso_objetivo - win.auto_state.progreso_actual
        if abs(diff_p) > 0.001:
            win.auto_state.progreso_actual += diff_p * 0.1
            win.barra_progreso.set_fraction(win.auto_state.progreso_actual)

        diff_t = win.auto_state.segundos_objetivos - win.auto_state.segundos_actuales
        if abs(diff_t) > 0.1:
            win.auto_state.segundos_actuales += diff_t * 0.08

        segs_visibles = max(0, win.auto_state.segundos_actuales)
        m, s = int(segs_visibles // 60), int(segs_visibles % 60)
        win.label_tiempo.set_label(traducir("Tiempo estimado restante: {:02d}:{:02d}").format(m, s))

        if not win.auto_state.en_proceso and abs(diff_p) < 0.001 and abs(diff_t) < 0.1:
            win.auto_state.anim_timer = 0
            return False
        return True

    win.auto_state.anim_timer = GLib.timeout_add(INTERVALO_FRAME_MS, animar_ui)

    win.grafico.datos_raw = {}
    win.grafico.num_categorias = 6
    win.grafico.categorias = [*(traducir(c) for c in CATEGORIAS_RADAR)]
    win.grafico.valores_animados = {}
    vaciar_contenedor(win.box_leyenda)
    vaciar_contenedor(win.box_resultados)


def _registrar_schedulers_en_grafico(win, schedulers):
    def _agregar():
        for s in schedulers:
            win.grafico.registrar_scheduler(s)
            crear_chip_leyenda(s, grafico=win.grafico, box_leyenda=win.box_leyenda)
    GLib.idle_add(_agregar)


def _calibrar_termica(win):
    log(win.text_view_logs_auto, "Calibrando base térmica del sistema...", nivel="title")
    temp_base = win.sensor.calibrar(muestras=3, intervalo=0.5)
    if temp_base < 10:
        log(win.text_view_logs_auto, "Sensor térmico no disponible. Omitiendo gestión de calor.")
        return 999
    umbral = temp_base + 5.0
    log(win.text_view_logs_auto, f"Calibración completa: Base {temp_base:.1f}°C | Umbral: {umbral:.1f}°C")
    return umbral


def _enfriar_si_necesario(win, temp_actual, umbral):
    if temp_actual <= umbral:
        return
    GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Estabilizando térmica... ({:.1f}°C)").format(temp_actual))
    log(win.text_view_logs_auto, f"Enfriando: {temp_actual:.1f}°C -> objetivo {umbral:.1f}°C")
    timeout_cool = 0
    while temp_actual > umbral and win.auto_state.en_proceso and timeout_cool < 15:
        time.sleep(1)
        timeout_cool += 1
        temp_actual = win.sensor.obtener_temp()
        GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Enfriando... {:.1f}°C (Límite: {:.1f}°C)").format(temp_actual, umbral))
    if timeout_cool >= 15:
        log(win.text_view_logs_auto, "Continuando análisis (umbral térmico no alcanzado).", nivel="error")


def _cambiar_scheduler(win, sc):
    if sc == SISTEMA_BASE:
        log(win.text_view_logs_auto, "Evaluando rendimiento nativo del Kernel...", nivel="title")
        win.scx.ejecutar_con_sudo(["scxctl", "stop"])
        time.sleep(1)
        return True
    log(win.text_view_logs_auto, f"Limpiando para {sc}...")
    win.scx.detener_todos()
    time.sleep(2)
    log(win.text_view_logs_auto, f"Activando {sc}...", nivel="title")
    res = win.scx.ejecutar_con_sudo(["scxctl", "start", "-s", sc, "-m", "auto"])
    if res.returncode != 0:
        err = res.stderr.strip() or traducir("El planificador no respondió a la señal de inicio.")
        log(win.text_view_logs_auto, f"FALLO: No se pudo activar {sc}.\nDetalle: {err}", nivel="error")
        GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Error: {}...").format(GLib.markup_escape_text(err[:50])))
        win.scx.ejecutar_con_sudo(["scxctl", "stop"])
        time.sleep(1)
        return False
    time.sleep(1.5)
    return True


def _ejecutar_bateria_tests(win, sc, brutos, indice_sc, total_steps):
    tests = ["cpu", "threads", "memory", "fork", "compile", "loaded"]
    nombres = [
        traducir("Latencia"), traducir("Multitarea"), traducir("Eficiencia"),
        traducir("Creación de Procesos"), traducir("Compilación"), traducir("Bajo Carga"),
    ]
    for idx, t in enumerate(tests):
        if not win.auto_state.en_proceso:
            break
        win.auto_state.progreso_objetivo = (indice_sc * 6 + idx + 1) / total_steps
        scheds_restantes = total_steps // 6 - (indice_sc + 1)
        win.auto_state.segundos_objetivos = (scheds_restantes * 20) + ((6 - idx) * 5) + 2
        GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Analizando {}: {} ({}/6)...").format(sc, nombres[idx], idx + 1))
        if t in claves_hibridas():
            res = correr_hybrid(t, win.scx, win.text_view_logs_auto, tiempo=5, logs=True, modo_dev=win.modo_desarrollador)
        else:
            res = correr_benchmark(t, win.scx, win.text_view_logs_auto, tiempo=5, logs=True, modo_dev=win.modo_desarrollador)
        if res:
            tipo_real = res["tipo"]
            brutos[sc][tipo_real] = res
            chart_idx = MAPA_CHART.get(tipo_real)
            if chart_idx is not None:
                GLib.idle_add(win.grafico.actualizar_dato, sc, chart_idx, valor_para_grafico(res, tipo_real))


def _actualizar_scores_parciales(win, brutos):
    if len(brutos) < 2:
        return
    mejores = calcular_mejores(brutos, tipos=("cpu", "threads", "memory", "latencia_fork", "latencia_compile", "latencia_loaded"))
    parciales = {}
    for s_name, s_data in brutos.items():
        cat_scores, _, _, _ = calcular_score_categorias(s_data, mejores)
        if cat_scores:
            parciales[s_name] = media_armonica(cat_scores)
    if parciales:
        lider = max(parciales, key=parciales.get)
        GLib.idle_add(win.fila_ganador.set_title, traducir("Mejor Equilibrio: {}").format(lider))


def _guardar_resultados_db(win, brutos):
    if not brutos:
        return None
    all_results = [res for sc_tests in brutos.values() for res in sc_tests.values()]
    run_type = "auto" if win.auto_state.en_proceso else "auto_parcial"
    run_id = guardar_run(win.versiones, run_type=run_type)
    guardar_resultados_batch(run_id, all_results)
    return run_id


def _finalizar_con_exito(win, brutos, run_id):
    log(win.text_view_logs_auto, "-" * 50)
    log(win.text_view_logs_auto, "ANÁLISIS FINAL (Potencia 45% | Respuesta 45% | Fluidez 10%)", nivel="title")
    log(win.text_view_logs_auto, "Buscando el mejor equilibrio entre rendimiento bruto y agilidad.")

    scores_finales = calcular_scores_finales(brutos)
    for sc, data in scores_finales.items():
        log(win.text_view_logs_auto, f"• {sc.upper().ljust(8)} | Score: {data['score']:.1f}% (Pot: {data['pot']/100:.2f}, Resp: {data['resp']/100:.2f}, Flz: {data['flu']/100:.2f})")

    ganador = max(scores_finales.keys(), key=lambda s: scores_finales[s]["score"])
    win.auto_state.ganador_final = ganador
    win.auto_state.scores_finales = scores_finales
    with win.auto_state.brutos_lock:
        win.auto_state.brutos_finales = brutos

    GLib.idle_add(win.fila_ganador.set_title, traducir("Mejor Planificador: {}").format(ganador))
    win.auto_state.desc_final = traducir("'{}' ofrece la mejor propuesta integral con un {:.1f}% de eficacia de sistema.").format(ganador, scores_finales[ganador]["score"])

    def _guardar_log():
        buf = win.text_view_logs_auto.get_buffer()
        actualizar_log_run(run_id, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
        return False
    GLib.idle_add(_guardar_log)
    GLib.idle_add(lambda: finalizar_auto_test_success(win))


def _finalizar_interrumpido(win, brutos, run_id):
    log(win.text_view_logs_auto, f"ANÁLISIS DETENIDO — {sum(len(v) for v in brutos.values())} resultado(s) guardado(s)", nivel="title")
    if brutos and run_id is not None:
        def _guardar_log():
            buf = win.text_view_logs_auto.get_buffer()
            actualizar_log_run(run_id, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
            return False
        GLib.idle_add(_guardar_log)
    GLib.idle_add(lambda: finalizar_auto_test(win, None))


def iniciar_auto_test(win, btn):
    """Inicia el ciclo completo de detección automatizada."""
    _preparar_ui_auto_test(win)

    def motor():
        schedulers = [n for n, (r, c) in win._auto_sched_checks.items() if c.get_active()]
        todos = [SISTEMA_BASE] + schedulers
        total_steps = len(todos) * 6
        brutos = {}

        _registrar_schedulers_en_grafico(win, todos)
        umbral = _calibrar_termica(win)

        if not win.auto_state.en_proceso:
            return

        for i, sc in enumerate(todos):
            if not win.auto_state.en_proceso:
                break

            _enfriar_si_necesario(win, win.sensor.obtener_temp(), umbral)

            if not win.auto_state.en_proceso:
                break

            if not _cambiar_scheduler(win, sc):
                continue

            brutos[sc] = {}
            _ejecutar_bateria_tests(win, sc, brutos, i, total_steps)
            _actualizar_scores_parciales(win, brutos)

        run_id = _guardar_resultados_db(win, brutos)
        if win.auto_state.en_proceso and brutos:
            _finalizar_con_exito(win, brutos, run_id)
        elif not brutos:
            GLib.idle_add(lambda: finalizar_auto_test(win, None))
        else:
            _finalizar_interrumpido(win, brutos, run_id)

    threading.Thread(target=motor, daemon=True).start()


def finalizar_auto_test_success(win):
    """Finaliza el test automático con éxito y muestra los resultados."""
    finalizar_auto_test(win, win.auto_state.ganador_final)
    win.fila_ganador.set_subtitle(win.auto_state.desc_final)

    vaciar_contenedor(win.box_resultados)

    from ui.automatizacion.pesos import poblar_ranking
    poblar_ranking(win)

    from ui.automatizacion.historial import refrescar_historial
    refrescar_historial(win)

    def _aplicar_ganador():
        try:
            if win.auto_state.ganador_final == SISTEMA_BASE:
                res = win.scx.ejecutar_con_sudo(["scxctl", "stop"])
            else:
                res = win.scx.ejecutar_con_sudo(["scxctl", "start", "-s", win.auto_state.ganador_final, "-m", "auto"])

            if res.returncode != 0 and "not running" not in (res.stderr or "").lower():
                err = res.stderr.strip() or "Error desconocido al aplicar scheduler"
                raise subprocess.SubprocessError(err)

            GLib.idle_add(win.sincronizar_sistema)
            mostrar_toast(win, traducir("Aplicado automáticamente: {}").format(win.auto_state.ganador_final))
        except (subprocess.SubprocessError, OSError) as e:
            mostrar_toast(win, str(e), prefijo=traducir("Error al aplicar"))

    threading.Thread(target=_aplicar_ganador, daemon=True).start()


def finalizar_auto_test(win, winner):
    """Restaura la UI tras finalizar la detección automática."""
    win.auto_state.en_proceso = False
    if win.auto_state.anim_timer:
        GLib.source_remove(win.auto_state.anim_timer)
        win.auto_state.anim_timer = 0
    win.grafico.detener_pulso()
    win.btn_auto.set_label(traducir("Determinar"))
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")

    from ui.automatizacion.historial import actualizar_botones_nav
    actualizar_botones_nav(win)

    win.auto_state.progreso_objetivo = 1.0
    win.auto_state.segundos_objetivos = 0.0
    win.revealer_tiempo.set_reveal_child(False)
    GLib.timeout_add(500, lambda: win.barra_progreso.set_visible(False))
    if winner:
        win.fila_ganador.set_title(traducir("Recomendado: {}").format(winner))
        win.toast_overlay.add_toast(Adw.Toast.new(traducir("Detección finalizada: {}").format(winner)))
    else:
        win.fila_ganador.set_title(traducir("Motor en reposo"))
        win.fila_ganador.set_subtitle(traducir("Análisis interrumpido por el usuario."))
        win.fila_ganador.set_icon_name("applications-engineering-symbolic")


def limpiar_ranking_auto(win, btn):
    """Limpia los resultados de la detección automática."""
    if win.auto_state.en_proceso:
        return
    win.text_view_logs_auto.get_buffer().set_text("")
    win.fila_ganador.set_title(traducir("Motor en reposo"))
    win.fila_ganador.set_subtitle(traducir("Esperando al escaneo..."))
    vaciar_contenedor(win.box_resultados)

    win.grafico.datos_raw = {}
    win.grafico.max_por_categoria = [1.0] * win.grafico.num_categorias
    win.grafico.valores_animados = {}
    vaciar_contenedor(win.box_leyenda)

    for f in win._filas_ranking:
        win.fila_ganador.remove(f)
    win._filas_ranking.clear()

    win.fila_ganador.set_expanded(False)

    from ui.automatizacion.pesos import sincronizar_estado_pesos
    sincronizar_estado_pesos(win, False)
    win.grafico.queue_draw()
