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
from utils.helpers import log
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


def iniciar_auto_test(win, btn):
    """Inicia el ciclo completo de detección automatizada."""
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
            return False
        return True

    GLib.timeout_add(INTERVALO_FRAME_MS, animar_ui)

    win.grafico.datos_raw = {}
    win.grafico.num_categorias = 6
    win.grafico.categorias = [
        *(traducir(c) for c in CATEGORIAS_RADAR),
    ]
    win.grafico.valores_animados = {}
    while (c := win.box_leyenda.get_first_child()):
        win.box_leyenda.remove(c)
    while (c := win.box_resultados.get_first_child()):
        win.box_resultados.remove(c)

    def motor():
        lista_sc = [n for n, (r, c) in win._auto_sched_checks.items() if c.get_active()]
        lista = [SISTEMA_BASE] + lista_sc
        total_steps = len(lista) * 6
        brutos = {}

        for s in lista:
            GLib.idle_add(win.grafico.registrar_scheduler, s)
            GLib.idle_add(crear_chip_leyenda, s, win.grafico, win.box_leyenda)

        log(win.text_view_logs_auto, "Calibrando base térmica del sistema...", nivel="title")
        temp_base = win.sensor.calibrar(muestras=3, intervalo=0.5)

        if not win.auto_state.en_proceso:
            return

        if temp_base < 10:
            log(win.text_view_logs_auto, "Sensor térmico no disponible. Omitiendo gestión de calor.")
            umbral_enfriamiento = 999
        else:
            umbral_enfriamiento = temp_base + 5.0
            log(win.text_view_logs_auto, f"Calibración completa: Base {temp_base:.1f}°C | Umbral: {umbral_enfriamiento:.1f}°C")

        for sc in lista:
            if not win.auto_state.en_proceso:
                break

            temp_actual = win.sensor.obtener_temp()
            if temp_actual > umbral_enfriamiento:
                GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Estabilizando térmica... ({:.1f}°C)").format(temp_actual))
                log(win.text_view_logs_auto, f"Enfriando: {temp_actual:.1f}°C -> objetivo {umbral_enfriamiento:.1f}°C")

                timeout_cool = 0
                max_wait = 15
                while temp_actual > umbral_enfriamiento and win.auto_state.en_proceso and timeout_cool < max_wait:
                    time.sleep(1)
                    timeout_cool += 1
                    temp_actual = win.sensor.obtener_temp()
                    GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Enfriando... {:.1f}°C (Límite: {:.1f}°C)").format(temp_actual, umbral_enfriamiento))

                if timeout_cool >= max_wait:
                    log(win.text_view_logs_auto, "Continuando análisis (umbral térmico no alcanzado).", nivel="error")

            if not win.auto_state.en_proceso:
                break

            if sc == SISTEMA_BASE:
                log(win.text_view_logs_auto, "Evaluando rendimiento nativo del Kernel...", nivel="title")
                win.scx.ejecutar_con_sudo(["scxctl", "stop"])
                time.sleep(1)
            else:
                log(win.text_view_logs_auto, f"Limpiando para {sc}...")
                win.scx.detener_todos()
                time.sleep(2)

                log(win.text_view_logs_auto, f"Activando {sc}...", nivel="title")
                res_switch = win.scx.ejecutar_con_sudo(["scxctl", "start", "-s", sc, "-m", "auto"])
                if res_switch.returncode != 0:
                    err_kernel = res_switch.stderr.strip() or traducir("El planificador no respondió a la señal de inicio.")
                    err_safe = GLib.markup_escape_text(err_kernel)
                    log(win.text_view_logs_auto, f"FALLO: No se pudo activar {sc}.\nDetalle: {err_kernel}", nivel="error")
                    GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Error: {}...").format(err_safe[:50]))
                    win.scx.ejecutar_con_sudo(["scxctl", "stop"])
                    time.sleep(1)
                    continue

            time.sleep(1.5)

            brutos[sc] = {}
            tests_prog = ["cpu", "threads", "memory", "fork", "compile", "loaded"]
            nombres_test = [
                traducir("Latencia"), traducir("Multitarea"), traducir("Eficiencia"),
                traducir("Creación de Procesos"), traducir("Compilación"), traducir("Bajo Carga"),
            ]

            for idx, t in enumerate(tests_prog):
                if not win.auto_state.en_proceso:
                    break

                puntos_progreso = (lista.index(sc) * 6) + (idx + 1)
                win.auto_state.progreso_objetivo = puntos_progreso / total_steps

                scheds_restantes = len(lista) - (lista.index(sc) + 1)
                tests_restantes_en_este = 6 - idx
                win.auto_state.segundos_objetivos = (scheds_restantes * 20) + (tests_restantes_en_este * 5) + 2

                GLib.idle_add(win.fila_ganador.set_subtitle, traducir("Analizando {}: {} ({}/6)...").format(sc, nombres_test[idx], idx+1))

                if t in claves_hibridas():
                    res = correr_hybrid(t, win.scx, win.text_view_logs_auto, tiempo=5, logs=True, modo_dev=win.modo_desarrollador)
                else:
                    res = correr_benchmark(t, win.scx, win.text_view_logs_auto, tiempo=5, logs=True, modo_dev=win.modo_desarrollador)
                if res:
                    if sc not in brutos:
                        brutos[sc] = {}
                    tipo_real = res["tipo"]
                    brutos[sc][tipo_real] = res

                    chart_idx = MAPA_CHART.get(tipo_real)
                    if chart_idx is not None:
                        val_v = valor_para_grafico(res, tipo_real)
                        GLib.idle_add(win.grafico.actualizar_dato, sc, chart_idx, val_v)

            if len(brutos) >= 2:
                mejores_vuelo = calcular_mejores(brutos, tipos=("cpu", "threads", "memory", "latencia_fork", "latencia_compile", "latencia_loaded"))
                scores_parciales = {}

                for s_name, s_data in brutos.items():
                    cat_scores, _, _, _ = calcular_score_categorias(s_data, mejores_vuelo)
                    if cat_scores:
                        scores_parciales[s_name] = media_armonica(cat_scores)

                if scores_parciales:
                    lider_vuelo = max(scores_parciales, key=scores_parciales.get)
                    GLib.idle_add(win.fila_ganador.set_title, traducir("Mejor Equilibrio: {}").format(traducir(lider_vuelo)))

        all_results = []
        if brutos:
            run_type = "auto" if win.auto_state.en_proceso else "auto_parcial"
            run_id = guardar_run(win.versiones, run_type=run_type)
            for sc_name, sc_tests in brutos.items():
                for test_type, res in sc_tests.items():
                    all_results.append(res)
            guardar_resultados_batch(run_id, all_results)
        if win.auto_state.en_proceso:
            if brutos:
                log(win.text_view_logs_auto, "-" * 50)
                log(win.text_view_logs_auto, "ANÁLISIS FINAL (Potencia 45% | Respuesta 45% | Fluidez 10%)", nivel="title")
                log(win.text_view_logs_auto, "Buscando el mejor equilibrio entre rendimiento bruto y agilidad.")

                scores_finales = calcular_scores_finales(brutos)

                for sc, data in scores_finales.items():
                    log(win.text_view_logs_auto, f"• {sc.upper().ljust(8)} | Score: {data['score']:.1f}% (Pot: {data['pot']/100:.2f}, Resp: {data['resp']/100:.2f}, Flz: {data['flu']/100:.2f})")

                win.auto_state.ganador_final = max(scores_finales.keys(), key=lambda s: scores_finales[s]["score"])
                winner_score = scores_finales[win.auto_state.ganador_final]["score"]
                win.auto_state.scores_finales = scores_finales
                with win.auto_state.brutos_lock:
                    win.auto_state.brutos_finales = brutos

                GLib.idle_add(win.fila_ganador.set_title, traducir("Mejor Planificador: {}").format(traducir(win.auto_state.ganador_final)))
                win.auto_state.desc_final = traducir("'{}' ofrece la mejor propuesta integral con un {:.1f}% de eficacia de sistema.").format(win.auto_state.ganador_final, winner_score)
                def _guardar_final():
                    buf = win.text_view_logs_auto.get_buffer()
                    actualizar_log_run(run_id, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
                    return False
                GLib.idle_add(_guardar_final)
                GLib.idle_add(lambda: finalizar_auto_test_success(win))
            else:
                GLib.idle_add(lambda: finalizar_auto_test(win, None))
        else:
            log(win.text_view_logs_auto, f"ANÁLISIS DETENIDO — {len(all_results)} resultado(s) guardado(s)", nivel="title")
            if brutos:
                def _guardar_interrumpido():
                    buf = win.text_view_logs_auto.get_buffer()
                    actualizar_log_run(run_id, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
                    return False
                GLib.idle_add(_guardar_interrumpido)
            GLib.idle_add(lambda: finalizar_auto_test(win, None))

    threading.Thread(target=motor, daemon=True).start()


def finalizar_auto_test_success(win):
    """Finaliza el test automático con éxito y muestra los resultados."""
    finalizar_auto_test(win, win.auto_state.ganador_final)
    win.fila_ganador.set_subtitle(win.auto_state.desc_final)

    while (c := win.box_resultados.get_first_child()):
        win.box_resultados.remove(c)

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
            GLib.idle_add(lambda: win.toast_overlay.add_toast(
                Adw.Toast.new(traducir("Aplicado automáticamente: {}").format(win.auto_state.ganador_final))
            ))
        except (subprocess.SubprocessError, OSError) as e:
            err_msg = str(e)
            GLib.idle_add(lambda m=err_msg: win.toast_overlay.add_toast(
                Adw.Toast.new(traducir("Error al aplicar: {}").format(m))
            ))

    threading.Thread(target=_aplicar_ganador, daemon=True).start()


def finalizar_auto_test(win, winner):
    """Restaura la UI tras finalizar la detección automática."""
    win.auto_state.en_proceso = False
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
        win.fila_ganador.set_title(traducir("Recomendado: {}").format(traducir(winner)))
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

    from ui.automatizacion.pesos import sincronizar_estado_pesos
    sincronizar_estado_pesos(win, False)
    win.grafico.queue_draw()
