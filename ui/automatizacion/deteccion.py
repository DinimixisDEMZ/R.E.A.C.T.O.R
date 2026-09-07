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
from core import sched_ext
from core.tipos import obtener_scheduler, schedulers_para_benchmark
from core.constantes import INTERVALO_FRAME_MS, SISTEMA_BASE, CATEGORIAS_RADAR, TIPOS_PRUEBA, MOTOR_REPOSO, DETERMINAR
from core.benchmark import correr_benchmark
from core.hybrid import correr_hybrid
from core.scoring import (
    calcular_scores_finales, calcular_mejores, calcular_score_categorias,
    media_armonica,
)
from core.tipos import MAPA_CHART, valor_para_grafico, claves_hibridas
from core.database import guardar_run, guardar_resultados_batch, actualizar_log_run
from utils.logging import log, mostrar_toast, registrar_error
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

        win.ejecutar_accion_privilegiada(lambda: iniciar_auto_test(win, btn))


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
    """Toma la temperatura de reposo como referencia del ciclo."""
    if not win.sensor.hay_sensor():
        log(win.text_view_logs_auto,
            "Sin sensor térmico: no se puede descartar la influencia del calor.")
        return None
    temp_base = win.sensor.calibrar(muestras=3, intervalo=0.5)
    log(win.text_view_logs_auto,
        f"Temperatura de reposo: {temp_base:.1f} °C "
        f"(límite del equipo: {win.sensor.tjmax():.0f} °C)")
    return temp_base


def _enfriar_si_necesario(win, temp_base):
    """Espera a que la temperatura se estabilice antes de la siguiente prueba.

    El criterio anterior era inalcanzable por construcción: comparaba la
    temperatura después de una prueba contra la de reposo más 5 °C, así que
    en cualquier portátil el bucle agotaba siempre sus 15 segundos y seguía
    igual, dejando en el registro un error que no lo era.

    Ahora se espera a que la temperatura *deje de bajar*, que es la condición
    que de verdad hace comparables dos mediciones consecutivas, y se avisa si
    el hardware llegó a limitar por temperatura.
    """
    if not win.sensor.hay_sensor():
        return

    temperatura = win.sensor.obtener_temp()
    GLib.idle_add(win.fila_ganador.set_subtitle,
                  traducir("Estabilizando temperatura... ({:.1f} °C)").format(temperatura))

    resultado = win.sensor.esperar_estabilizacion(
        espera_maxima=60.0,
        cancelado=lambda: not win.auto_state.en_proceso)

    if resultado["estabilizado"]:
        log(win.text_view_logs_auto,
            f"Temperatura estable en {resultado['temperatura']:.1f} °C "
            f"tras {resultado['esperado']:.0f} s")
    elif resultado["motivo"] == "tiempo_agotado":
        log(win.text_view_logs_auto,
            f"La temperatura no se estabilizó en 60 s "
            f"({resultado['temperatura']:.1f} °C). Se continúa.", nivel="warning")


def _cambiar_scheduler(win, sc):
    """Aplica un planificador y espera a que el kernel confirme que gobierna.

    Antes se esperaba 1,5 s fijos tras lanzar el comando y se empezaba a medir.
    Si el planificador tardaba más en cargar —cosa habitual en equipos lentos,
    que son justo los que más importan— la primera prueba se ejecutaba todavía
    bajo el planificador anterior y el resultado se atribuía al nuevo.
    """
    if sc == SISTEMA_BASE:
        log(win.text_view_logs_auto, "Evaluando el planificador nativo del kernel...", nivel="title")
        resultado = win.scx.detener()
        if not resultado.ok and resultado.codigo != "sin_scheduler":
            log(win.text_view_logs_auto,
                f"No se pudo liberar el planificador: {resultado.mensaje}", nivel="error")
            return False
        return _esperar_estado(win, None)

    log(win.text_view_logs_auto, f"Activando {sc}...", nivel="title")
    resultado = win.scx.aplicar(sc, "auto")
    if not resultado.ok:
        log(win.text_view_logs_auto,
            f"FALLO: no se pudo activar {sc}.\nDetalle: {resultado.mensaje}", nivel="error")
        GLib.idle_add(win.fila_ganador.set_subtitle,
                      traducir("Error: {}...").format(
                          GLib.markup_escape_text((resultado.mensaje or "")[:50])))
        return False

    if not _esperar_estado(win, sc):
        log(win.text_view_logs_auto,
            f"{sc} se lanzó pero el kernel no lo activó; se omite.", nivel="error")
        win.scx.detener()
        return False
    return True


def _esperar_estado(win, esperado, espera_maxima=15.0):
    """Espera a que /sys/kernel/sched_ext refleje el planificador esperado.

    `esperado` es el nombre canónico, o None para esperar a que no haya ninguno.
    """
    limite = time.monotonic() + espera_maxima
    while time.monotonic() < limite:
        if not win.auto_state.en_proceso:
            return False
        info = sched_ext.estado()
        if info["en_transicion"]:
            time.sleep(0.2)
            continue
        if esperado is None and not info["activo"]:
            return True
        if esperado is not None and info["activo"] and info["scheduler"] == esperado:
            # Un instante de margen para que el planificador termine de
            # asentarse antes de empezar a medir.
            time.sleep(0.5)
            return True
        time.sleep(0.2)
    return False


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
            res = correr_hybrid(t, win.scx, win.text_view_logs_auto, tiempo=None, logs=True, modo_dev=win.modo_desarrollador)
        else:
            res = correr_benchmark(t, win.scx, win.text_view_logs_auto, tiempo=None, logs=True, modo_dev=win.modo_desarrollador)
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
        # Se recuerda qué gobernaba el equipo ANTES de empezar. Sin esto,
        # cancelar el ciclo o cerrar la ventana dejaba activo el último
        # planificador probado, que es un estado que el usuario nunca eligió.
        estado_original = sched_ext.estado()["scheduler"]
        win.auto_state.estado_original = estado_original

        try:
            seleccionados = [n for n, (r, c) in win._auto_sched_checks.items() if c.get_active()]

            # Se descartan los que no deben medirse desatendidos: scx_chaos
            # degrada el sistema a propósito, scx_layered no arranca sin su
            # archivo de capas, scx_flow se autoajusta y levanta un servidor web.
            aptos = schedulers_para_benchmark(seleccionados)
            descartados = [n for n in seleccionados if n not in aptos]
            for nombre in descartados:
                ficha = obtener_scheduler(nombre)
                motivo = (ficha.advertencia if ficha and ficha.advertencia
                          else traducir("no es apto para pruebas desatendidas"))
                log(win.text_view_logs_auto,
                    f"Se omite {nombre}: {motivo}", nivel="warning")

            todos = [SISTEMA_BASE] + aptos
            total_steps = len(todos) * 6
            brutos = {}

            _registrar_schedulers_en_grafico(win, todos)
            temp_base = _calibrar_termica(win)

            if not win.auto_state.en_proceso:
                return

            for i, sc in enumerate(todos):
                if not win.auto_state.en_proceso:
                    break

                _enfriar_si_necesario(win, temp_base)

                if not win.auto_state.en_proceso:
                    break

                if not _cambiar_scheduler(win, sc):
                    continue

                # A partir de acá se vigila si el hardware limita por calor:
                # una medición con limitación térmica no es comparable.
                win.sensor.marcar_inicio_throttling()

                brutos[sc] = {}
                _ejecutar_bateria_tests(win, sc, brutos, i, total_steps)

                if win.sensor.hubo_throttling():
                    log(win.text_view_logs_auto,
                        f"El hardware limitó por temperatura durante {sc}: "
                        f"sus resultados pueden no ser comparables.", nivel="warning")
                    for resultado in brutos[sc].values():
                        resultado["throttling"] = True

                _actualizar_scores_parciales(win, brutos)

            run_id = _guardar_resultados_db(win, brutos)
            if win.auto_state.en_proceso and brutos:
                _finalizar_con_exito(win, brutos, run_id)
            elif not brutos:
                _restaurar_estado_original(win)
                GLib.idle_add(lambda: finalizar_auto_test(win, None))
            else:
                # Ciclo interrumpido: el equipo debe volver a lo que tenía, no
                # quedarse con el último planificador que se estaba probando.
                _restaurar_estado_original(win)
                _finalizar_interrumpido(win, brutos, run_id)
        except Exception as e:
            # Sin esto, cualquier excepción en el hilo motor dejaba la pestaña
            # bloqueada para siempre y sin ningún mensaje.
            registrar_error("iniciar_auto_test", e)
            log(win.text_view_logs_auto, str(e), nivel="error")
            _restaurar_estado_original(win)
            GLib.idle_add(lambda: finalizar_auto_test(win, None))

    threading.Thread(target=motor, daemon=True).start()


def _restaurar_estado_original(win):
    """Devuelve el planificador que gobernaba antes de empezar el ciclo."""
    original = getattr(win.auto_state, "estado_original", None)
    actual = sched_ext.estado()["scheduler"]
    if actual == original:
        return
    log(win.text_view_logs_auto,
        traducir("Restaurando el estado anterior: {}").format(original or SISTEMA_BASE))
    if original:
        win.scx.aplicar(original, "auto")
    else:
        win.scx.detener()


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
        # `aplicar()` decide solo entre arrancar y conmutar. Antes se usaba
        # siempre `scxctl start`, que el loader rechaza si ya hay un
        # planificador activo — y al terminar el ciclo siempre lo había, así que
        # el ganador no llegaba a aplicarse nunca.
        ganador = win.auto_state.ganador_final
        try:
            if ganador == SISTEMA_BASE:
                resultado = win.scx.detener()
            else:
                resultado = win.scx.aplicar(ganador, "auto")

            if resultado.ok or resultado.codigo == "sin_scheduler":
                GLib.idle_add(win.sincronizar_sistema)
                mostrar_toast(win, traducir("Aplicado automáticamente: {}").format(ganador))
            else:
                mostrar_toast(win, resultado.mensaje or traducir("No se pudo aplicar"),
                              prefijo=traducir("Error al aplicar"))
        except Exception as e:
            registrar_error("aplicar_ganador", e)
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
