"""
Pestaña de Controles: Estado actual, selección de scheduler/modo, acciones.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.database import activar_db_temporal, desactivar_db_temporal
from core.operations import OperationCancelled


def _programar_ui(win, callback, *args):
    programar = getattr(win, "ejecutar_en_ui", None)
    if callable(programar):
        return programar(callback, *args)
    return GLib.idle_add(callback, *args)


def _mostrar_toast(win, mensaje, *, alta=False):
    mostrar = getattr(win, "mostrar_toast", None)
    if callable(mostrar):
        mostrar(mensaje, alta=alta)
        return
    toast = Adw.Toast.new(str(mensaje))
    if alta:
        toast.set_priority(Adw.ToastPriority.HIGH)
    win.toast_overlay.add_toast(toast)


def _mostrar_operacion_ocupada(win):
    mostrar = getattr(win, "mostrar_operacion_ocupada", None)
    if callable(mostrar):
        mostrar()
        return
    state = win.operaciones.state
    nombre = state.name if state is not None else "otra operación"
    _mostrar_toast(
        win,
        f"Operación ocupada: '{nombre}' sigue en curso.",
        alta=True,
    )


def _limpiar_automatizacion_fallback(win):
    """Invalida el ranking básico si la API pública aún no está disponible."""
    try:
        from ui.automatizacion import limpiar_ranking_auto

        limpiar_ranking_auto(win)
    except Exception:
        win._brutos_finales = {}
        win._scores_finales = {}
        win.ganador_final = None
        win._auto_permitir_aplicar = False
    win._auto_contexto_aplicable = False
    win._auto_pesos_validos = False
    win._auto_development_mode = None


def _invalidar_estado_auto(win):
    try:
        from ui.automatizacion import invalidar_estado_automatizacion
        invalidar_estado_automatizacion(win)
    except Exception:
        _limpiar_automatizacion_fallback(win)


def _invalidar_estado_manual(win):
    try:
        from ui.rendimiento import invalidar_estado_rendimiento
        invalidar_estado_rendimiento(win)
        return
    except Exception:
        win.datos_rendimiento = []
        win._manual_development_mode = None
        win._manual_generation = int(
            getattr(win, "_manual_generation", 0) or 0
        ) + 1
        reset_grafico = getattr(getattr(win, "grafico", None), "reset", None)
        if callable(reset_grafico):
            try:
                reset_grafico()
            except Exception:
                pass


def _restaurar_switch_dev(win, switch, handler_id):
    switch.handler_block(handler_id)
    try:
        switch.set_active(bool(win.modo_desarrollador))
    finally:
        switch.handler_unblock(handler_id)


def _alternar_modo_desarrollador(win, switch, handler_id):
    """Aplica el modo solicitado solo si no hay trabajo global activo."""
    if win.operaciones.is_busy:
        _restaurar_switch_dev(win, switch, handler_id)
        _mostrar_operacion_ocupada(win)
        return False

    modo_anterior = bool(win.modo_desarrollador)
    modo_solicitado = bool(switch.get_active())
    if modo_solicitado == modo_anterior:
        return False

    transicion_db = (
        activar_db_temporal if modo_solicitado else desactivar_db_temporal
    )
    rollback_db = (
        desactivar_db_temporal if modo_solicitado else activar_db_temporal
    )
    try:
        win.modo_desarrollador = modo_solicitado
        win.scx.modo_desarrollador = modo_solicitado
    except Exception as exc:
        try:
            win.modo_desarrollador = modo_anterior
            win.scx.modo_desarrollador = modo_anterior
        except Exception:
            pass
        _restaurar_switch_dev(win, switch, handler_id)
        _mostrar_toast(
            win,
            f"No se pudo preparar el modo desarrollador: {exc}",
            alta=True,
        )
        return False

    try:
        transicion_db()
    except Exception as exc:
        rollback_error = None
        try:
            rollback_db()
        except Exception as rollback_exc:
            rollback_error = str(rollback_exc) or rollback_exc.__class__.__name__
        win.modo_desarrollador = modo_anterior
        win.scx.modo_desarrollador = modo_anterior
        _restaurar_switch_dev(win, switch, handler_id)
        detalle = f"No se pudo completar el cambio de modo: {exc}"
        if rollback_error:
            detalle += f"; también falló la reversión de DB: {rollback_error}"
        _mostrar_toast(win, detalle, alta=True)
        return False

    _invalidar_estado_auto(win)
    _invalidar_estado_manual(win)

    win._mode_generation = int(
        getattr(win, "_mode_generation", 0) or 0
    ) + 1
    win._sync_generation = int(
        getattr(win, "_sync_generation", 0) or 0
    ) + 1
    win.compatibles = None
    modelo = getattr(win, "modelo_schedulers", None)
    if modelo is not None:
        get_n_items = getattr(modelo, "get_n_items", None)
        splice = getattr(modelo, "splice", None)
        if callable(get_n_items) and callable(splice):
            splice(0, get_n_items(), [])
    win.nav_disponibilidad.remove_css_class("pulse-warning")
    imagen = win.nav_disponibilidad.get_child().get_first_child()
    if isinstance(imagen, Gtk.Image):
        for css_class in ("success", "error"):
            imagen.remove_css_class(css_class)
        imagen.set_from_icon_name("dialog-question-symbolic")
    estado = "ACTIVADO" if modo_solicitado else "DESACTIVADO"
    _mostrar_toast(win, f"Modo Desarrollador: {estado}")
    if not getattr(win, "en_sincronizacion", False):
        win.sincronizar_sistema()
    return True


def _texto_seleccionado(combo):
    item = combo.get_selected_item()
    return item.get_string() if item is not None else None


def _capturar_configuracion(win):
    """Lee ambos ComboRow en GTK antes de iniciar cualquier worker."""
    return (
        _texto_seleccionado(win.combo_schedulers),
        _texto_seleccionado(win.combo_modos),
    )


def _establecer_configuracion_pendiente(win, pendiente):
    pendiente = bool(pendiente)
    win._configuracion_pendiente = pendiente
    boton = getattr(win, "btn_aplicar_configuracion", None)
    if boton is not None:
        boton.set_sensitive(pendiente)
    return pendiente


def _seleccion_configuracion_cambiada(win, *_args):
    """Marca una edición del usuario sin ejecutar comandos SCX."""
    if getattr(win, "_actualizando_configuracion", False):
        return False
    win._config_generation = int(
        getattr(win, "_config_generation", 0) or 0
    ) + 1
    scheduler, modo = _capturar_configuracion(win)
    return _establecer_configuracion_pendiente(
        win,
        bool(scheduler and modo),
    )


def _ejecutar_con_handle(handle, operacion):
    """Ejecuta una operación y garantiza la liberación de su exclusión."""
    try:
        return operacion(), None
    except OperationCancelled as exc:
        return None, exc
    except Exception as exc:
        return None, str(exc) or exc.__class__.__name__
    finally:
        handle.release()


def _comando_mantenimiento(accion, scheduler=None, modo=None):
    if accion not in {"start", "stop"}:
        raise ValueError(f"Acción de mantenimiento no válida: {accion}")
    command = ["scxctl", accion]
    if accion == "start":
        if scheduler:
            command.extend(["-s", scheduler])
        if modo:
            command.extend(["-m", modo])
    return command


def _describir_resultado(accion, result, scheduler=None, modo=None):
    """Devuelve ``(mensaje, es_error)`` para todo resultado de scxctl."""
    if result.returncode == 0:
        if accion == "start":
            destino = scheduler or "el planificador seleccionado"
            sufijo = f" [{modo}]" if modo else ""
            return f"SCX iniciado: {destino}{sufijo}.", False
        if accion == "stop":
            return "SCX detenido; el sistema base está activo.", False
        return f"Aplicado: {scheduler} [{modo}].", False

    detail = result.stderr.strip() or result.stdout.strip() or "Comando fallido"
    return f"Error de SCX: {detail}", True


def setup_controles_ui(win):
    """Construye la interfaz de la pestaña Controles.
    
    Args:
        win: Instancia de VentanaSimple (la ventana principal)
    """
    pref_page = Adw.PreferencesPage()
    grupo_estado = Adw.PreferencesGroup(title="Estado Actual")
    win.fila_actual = Adw.ActionRow(title="Planificador en Ejecución")
    win.boton_estado = Gtk.Button(label="Cargando...", valign=Gtk.Align.CENTER, css_classes=["flat"])
    win.fila_actual.add_suffix(win.boton_estado)
    grupo_estado.add(win.fila_actual)

    grupo_config = Adw.PreferencesGroup(title="Configuración de SCX")
    win.combo_schedulers = Adw.ComboRow(title="Seleccionar Planificador")
    win.modelo_schedulers = Gtk.StringList()
    win.combo_schedulers.set_model(win.modelo_schedulers)
    win.combo_modos = Adw.ComboRow(title="Seleccionar Modo")
    win.combo_modos.set_model(Gtk.StringList.new(["auto", "powersave", "gaming", "lowlatency", "server"]))

    win._actualizando_configuracion = False
    win._configuracion_pendiente = False
    win._aplicando_configuracion = False
    win._config_generation = 0
    win.btn_aplicar_configuracion = Gtk.Button(
        label="Aplicar configuración",
        css_classes=["suggested-action"],
        margin_top=6,
        margin_bottom=6,
        sensitive=False,
    )
    win.btn_aplicar_configuracion.connect(
        "clicked",
        lambda button: aplicar_cambio_scheduler(win, button),
    )

    win.combo_schedulers.connect(
        "notify::selected-item",
        lambda *_args: _seleccion_configuracion_cambiada(win),
    )
    win.combo_modos.connect(
        "notify::selected-item",
        lambda *_args: _seleccion_configuracion_cambiada(win),
    )

    grupo_config.add(win.combo_schedulers)
    grupo_config.add(win.combo_modos)
    grupo_config.add(win.btn_aplicar_configuracion)

    pref_page.add(grupo_estado)
    pref_page.add(grupo_config)

    # Sección de Depuración
    grupo_dev = Adw.PreferencesGroup(title="Herramientas de Depuración")
    fila_dev = Adw.ActionRow(title="Modo Simulación", subtitle="Prueba la UI sin hardware real ni scxctl")
    sw_dev = Gtk.Switch(active=win.modo_desarrollador, valign=Gtk.Align.CENTER)

    def _toggle_dev(sw, _pspec):
        _alternar_modo_desarrollador(win, sw, toggle_dev_handler_id)

    toggle_dev_handler_id = sw_dev.connect("notify::active", _toggle_dev)
    fila_dev.add_suffix(sw_dev)
    grupo_dev.add(fila_dev)
    pref_page.add(grupo_dev)

    header = Adw.HeaderBar()

    caja_gestion = Gtk.Box(spacing=6)
    for icon, acc, cls, tool in [
        ("media-playback-start-symbolic", "start", "success", "Iniciar"),
        ("media-playback-stop-symbolic", "stop", "destructive-action", "Detener")
    ]:
        b = Gtk.Button(icon_name=icon, tooltip_text=tool, css_classes=[cls] if cls else [])
        b.connect("clicked", lambda btn, a=acc: ejecutar_mantenimiento(win, btn, a))
        caja_gestion.append(b)

    header.pack_start(caja_gestion)

    btn_actualizar = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Actualizar Estado", css_classes=["flat"])
    btn_actualizar.connect("clicked", lambda _btn: refrescar_estado(win))
    header.pack_end(btn_actualizar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_controles.set_child(view)


def refrescar_estado(win):
    """Actualiza la UI sin mutar el estado de SCX."""
    win.sincronizar_sistema()


def _lanzar_operacion_scx(
    win,
    nombre,
    accion,
    command,
    scheduler=None,
    modo=None,
):
    handle = win.operaciones.try_acquire(nombre)
    if handle is None:
        _mostrar_operacion_ocupada(win)
        return False

    scx_manager = win.scx
    generacion_modo = int(getattr(win, "_mode_generation", 0) or 0)
    modo_desarrollador = bool(getattr(win, "modo_desarrollador", False))

    def _worker():
        def _ejecutar():
            handle.check_cancelled()
            return scx_manager.ejecutar_con_sudo(
                command,
                cancel_token=handle.token,
            )

        result, error = _ejecutar_con_handle(
            handle,
            _ejecutar,
        )
        _programar_ui(
            win,
            _finalizar_operacion_scx,
            win,
            accion,
            result,
            error,
            scheduler,
            modo,
            generacion_modo,
            modo_desarrollador,
        )

    try:
        threading.Thread(target=_worker).start()
    except Exception as exc:
        handle.release()
        _mostrar_toast(
            win,
            f"No se pudo iniciar la operación SCX: {exc}",
            alta=True,
        )
        return False
    return True


def _finalizar_operacion_scx(
    win,
    accion,
    result,
    error,
    scheduler,
    modo,
    generacion_modo=None,
    modo_desarrollador=None,
):
    contexto_obsoleto = generacion_modo is not None and (
        generacion_modo != getattr(win, "_mode_generation", 0)
        or modo_desarrollador
        != bool(getattr(win, "modo_desarrollador", False))
    )
    resultado_exitoso = (
        error is None
        and result is not None
        and result.returncode == 0
    )
    if accion == "switch":
        win._aplicando_configuracion = False
        _establecer_configuracion_pendiente(
            win,
            contexto_obsoleto or not resultado_exitoso,
        )
    if contexto_obsoleto:
        return
    if isinstance(error, OperationCancelled):
        _mostrar_toast(win, "Operación SCX cancelada.")
        return
    if error is not None:
        _mostrar_toast(win, f"Error de SCX: {error}", alta=True)
    else:
        mensaje, es_error = _describir_resultado(
            accion,
            result,
            scheduler,
            modo,
        )
        _mostrar_toast(win, mensaje, alta=es_error)
    win.sincronizar_sistema()


def ejecutar_mantenimiento(win, _btn, acc):
    """Ejecuta start/stop con datos capturados antes del worker."""
    if acc not in {"start", "stop"}:
        _mostrar_toast(win, f"Acción SCX no válida: {acc}", alta=True)
        return

    scheduler, modo = _capturar_configuracion(win)
    command = _comando_mantenimiento(acc, scheduler, modo)
    generacion_modo = int(getattr(win, "_mode_generation", 0) or 0)
    modo_desarrollador = bool(win.modo_desarrollador)

    def _proceder():
        if (
            not getattr(win, "_ui_alive", True)
            or generacion_modo != getattr(win, "_mode_generation", 0)
            or modo_desarrollador != bool(win.modo_desarrollador)
        ):
            if getattr(win, "_ui_alive", True):
                _mostrar_toast(
                    win,
                    "El modo cambió durante la autorización; repita la operación.",
                )
            return False
        _lanzar_operacion_scx(
            win,
            f"mantenimiento SCX ({acc})",
            acc,
            command,
            scheduler,
            modo,
        )

    win.solicitar_sudo_si_necesario(_proceder)


def aplicar_cambio_scheduler(win, btn=None):
    """Aplica explícitamente scheduler y modo en un único ``switch``."""
    if win.en_sincronizacion:
        return False

    scheduler, modo = _capturar_configuracion(win)
    if not scheduler or not modo:
        return False

    command = ["scxctl", "switch", "-s", scheduler, "-m", modo]
    generacion_modo = int(getattr(win, "_mode_generation", 0) or 0)
    generacion_configuracion = int(
        getattr(win, "_config_generation", 0) or 0
    )
    modo_desarrollador = bool(win.modo_desarrollador)

    def _proceder():
        if (
            not getattr(win, "_ui_alive", True)
            or generacion_modo != getattr(win, "_mode_generation", 0)
            or modo_desarrollador != bool(win.modo_desarrollador)
        ):
            if getattr(win, "_ui_alive", True):
                _mostrar_toast(
                    win,
                    "El modo cambió durante la autorización; repita la operación.",
                )
            return False
        if generacion_configuracion != getattr(win, "_config_generation", 0):
            _mostrar_toast(
                win,
                "La selección cambió durante la autorización; aplíquela de nuevo.",
            )
            return False
        if getattr(win, "_aplicando_configuracion", False):
            return False
        win._aplicando_configuracion = True
        _establecer_configuracion_pendiente(win, False)
        iniciada = _lanzar_operacion_scx(
            win,
            f"cambio de scheduler a {scheduler}",
            "switch",
            command,
            scheduler,
            modo,
        )
        if not iniciada:
            win._aplicando_configuracion = False
            _establecer_configuracion_pendiente(win, True)
        return iniciada

    win.solicitar_sudo_si_necesario(_proceder)
    return True
