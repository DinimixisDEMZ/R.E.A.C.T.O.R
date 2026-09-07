"""
Pestaña de Controles: Estado actual, selección de scheduler/modo, acciones.
"""

import math
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.database import activar_db_temporal, desactivar_db_temporal, obtener_info_scheduler
from ui.disponibilidad import recargar_disponibilidad_ui
from utils.colores import generar_color_hash, dibujar_dot
from utils.logging import mostrar_toast, registrar_error
from core.sched_ext import nombre_corto
from utils.iconos import establecer_iconos_idk
from core.constantes import CARGANDO
from utils.i18n import obtener_idiomas, establecer_idioma, establecer_usar_idioma_sistema, NOMBRES_IDIOMA, IDIOMA_ACTUAL, USAR_IDIOMA_SISTEMA, traducir
from ui.verificacion import mostrar_verificacion
from core.verificacion import marcar_verificacion_hecha


def configurar_ui_controles(win):
    """Construye la interfaz de la pestaña Controles.
    
    Args:
        win: Instancia de VentanaSimple (la ventana principal)
    """
    pref_page = Adw.PreferencesPage()
    grupo_estado = Adw.PreferencesGroup(title=traducir("Estado Actual"))
    win.fila_actual = Adw.ActionRow(title=traducir("Planificador en Ejecución"))
    win.boton_estado = Gtk.Button(label=traducir(CARGANDO), valign=Gtk.Align.CENTER, css_classes=["flat"])
    win.fila_actual.add_suffix(win.boton_estado)
    grupo_estado.add(win.fila_actual)

    grupo_config = Adw.PreferencesGroup(title=traducir("Configuración de SCX"))
    win.combo_schedulers = Adw.ComboRow(title=traducir("Seleccionar Planificador"))
    win.modelo_schedulers = Gtk.StringList()
    win.combo_schedulers.set_model(win.modelo_schedulers)
    # El modelo arranca vacío a propósito. Los modos pertenecen a `scx_loader`
    # y dependen del scheduler elegido: se consultan con SchedulerModes() y se
    # rellenan en win._sincronizar_modos(). La lista fija que había acá no sólo
    # era inventada, sino que su orden no coincidía con el enum que viaja por
    # D-Bus (0=Auto 1=Gaming 2=PowerSave 3=LowLatency 4=Server).
    win.combo_modos = Adw.ComboRow(
        title=traducir("Seleccionar Modo"),
        subtitle=traducir("Perfiles preconfigurados de scx_loader"))
    win.combo_modos.set_model(Gtk.StringList.new(["auto"]))

    win.combo_schedulers.connect("notify::selected-item", lambda *_: aplicar_cambio_scheduler(win))

    grupo_config.add(win.combo_schedulers)
    grupo_config.add(win.combo_modos)

    grupo_info = Adw.PreferencesGroup(title=traducir("Información del Planificador"))
    win._sched_info_card = Gtk.Frame(css_classes=["card"])
    win._sched_info_card.set_visible(False)
    card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)

    title_row = Gtk.Box(spacing=6)
    win._sched_info_dot = Gtk.DrawingArea()
    win._sched_info_dot.set_content_width(8)
    win._sched_info_dot.set_content_height(8)
    win._sched_info_dot.set_valign(Gtk.Align.CENTER)
    win._sched_info_dot.set_draw_func(lambda *_: None)
    title_row.append(win._sched_info_dot)
    win._sched_info_title = Gtk.Label(label="", css_classes=["heading"], xalign=0)
    title_row.append(win._sched_info_title)
    card_box.append(title_row)

    win._sched_info_desc = Gtk.Label(label="", xalign=0, wrap=True, css_classes=["dim-label"])
    card_box.append(win._sched_info_desc)
    win._sched_info_link = Gtk.LinkButton(label=traducir("Abrir documentación »"), uri="https://github.com/sched-ext/scx", halign=Gtk.Align.START)
    card_box.append(win._sched_info_link)

    win._sched_info_card.set_child(card_box)
    grupo_info.add(win._sched_info_card)

    def _actualizar_info_sched(*_args):
        item = win.combo_schedulers.get_selected_item()
        if item:
            name = item.get_string()
            desc, url = obtener_info_scheduler(name)
            win._sched_info_title.set_label(name)
            if desc:
                win._sched_info_desc.set_label(traducir(desc))
                win._sched_info_link.set_uri(url or "https://github.com/sched-ext/scx")
            else:
                win._sched_info_desc.set_label(traducir("No hay información disponible para este planificador. Consultá la documentación oficial para más detalles."))
                win._sched_info_link.set_uri("https://github.com/sched-ext/scx")

            r, g, b = generar_color_hash(name)
            win._sched_info_dot.set_draw_func(lambda a, cr, w, h, cr_r=r, cr_g=g, cr_b=b:
                                              dibujar_dot(cr, w, h, cr_r, cr_g, cr_b, 3.5))
            win._sched_info_dot.queue_draw()
            win._sched_info_card.set_visible(True)
        else:
            win._sched_info_card.set_visible(False)

    win.combo_schedulers.connect("notify::selected-item", _actualizar_info_sched)
    GLib.idle_add(lambda: _actualizar_info_sched())

    pref_page.add(grupo_info)
    pref_page.add(grupo_estado)
    pref_page.add(grupo_config)

    # Sección de Depuración
    grupo_dev = Adw.PreferencesGroup(title=traducir("Herramientas de Depuración"))
    fila_verif = Adw.ActionRow(
        title=traducir("Verificar Sistema"),
        subtitle=traducir("Comprueba herramientas, comandos y dependencias")
    )
    btn_verif = Gtk.Button(label=traducir("Verificar"), valign=Gtk.Align.CENTER, css_classes=["pill"])
    btn_verif.connect("clicked", lambda b: (
        mostrar_verificacion(win),
        marcar_verificacion_hecha()
    ))
    fila_verif.add_suffix(btn_verif)
    grupo_dev.add(fila_verif)

    fila_dev = Adw.ActionRow(title=traducir("Modo Simulación"), subtitle=traducir("Prueba la UI sin hardware real ni scxctl"))
    sw_dev = Gtk.Switch(active=win.modo_desarrollador, valign=Gtk.Align.CENTER)

    def _toggle_dev(sw, ps):
        win.modo_desarrollador = sw.get_active()
        win.scx.modo_desarrollador = win.modo_desarrollador
        if win.modo_desarrollador:
            activar_db_temporal()
            est = traducir("ACTIVADO")
        else:
            desactivar_db_temporal()
            est = traducir("DESACTIVADO")
        win.compatibles = None
        win.nav_disponibilidad.remove_css_class("pulse-warning")
        img = win.nav_disponibilidad.get_child().get_first_child()
        if isinstance(img, Gtk.Image):
            for cls in ["success", "error"]:
                img.remove_css_class(cls)
            img.set_from_icon_name("dialog-question-symbolic")
        win.toast_overlay.add_toast(Adw.Toast.new(traducir("Modo Desarrollador: {}").format(est)))
        recargar_disponibilidad_ui(win)
        try:
            from ui.automatizacion import _refrescar_auto_schedulers
            _refrescar_auto_schedulers(win)
        except ImportError:
            pass
        win.sincronizar_sistema()

    sw_dev.connect("notify::active", _toggle_dev)
    fila_dev.add_suffix(sw_dev)
    grupo_dev.add(fila_dev)
    pref_page.add(grupo_dev)

    grupo_iconos = Adw.PreferencesGroup(title=traducir("Iconos"))
    fila_iconos = Adw.ActionRow(title=traducir("Usar iconos alternativos de GNOME"), subtitle=traducir("Usa iconos del kit de desarrollo de GNOME en lugar de los del sistema"))
    sw_iconos = Gtk.Switch(active=getattr(win, '_usar_idk', True), valign=Gtk.Align.CENTER)

    def _toggle_iconos(sw, ps):
        win._usar_idk = sw.get_active()
        establecer_iconos_idk(win._usar_idk)
        win.toast_overlay.add_toast(Adw.Toast.new(
            traducir("Iconos alternativos: ACTIVADO") if win._usar_idk else traducir("Iconos del sistema: ACTIVADO")
        ))

    sw_iconos.connect("notify::active", _toggle_iconos)
    fila_iconos.add_suffix(sw_iconos)
    grupo_iconos.add(fila_iconos)
    pref_page.add(grupo_iconos)

    grupo_idioma = Adw.PreferencesGroup(title=traducir("Idioma"))
    codigos_idiomas = obtener_idiomas()
    nombres_idiomas = [
        NOMBRES_IDIOMA.get(c.split("_")[0], c) for c in codigos_idiomas
    ]

    fila_usar_sistema = Adw.SwitchRow(
        title=traducir("Usar idioma del sistema"),
        subtitle=traducir("Si está activo, se usa el idioma del sistema operativo"),
        active=USAR_IDIOMA_SISTEMA,
    )

    win.combo_idioma = Adw.ComboRow(
        title=traducir("Idioma de la interfaz"),
        subtitle=traducir("Requiere reinicio"),
        model=Gtk.StringList.new(nombres_idiomas),
        sensitive=not USAR_IDIOMA_SISTEMA,
    )
    for i, codigo in enumerate(codigos_idiomas):
        if codigo == IDIOMA_ACTUAL:
            win.combo_idioma.set_selected(i)
            break

    def _mostrar_dialogo_reinicio():
        dialogo = Adw.AlertDialog(
            heading=traducir("Cambio de idioma"),
            body=traducir("Reiniciá la aplicación para que los cambios surtan efecto."),
        )
        dialogo.add_response("ok", traducir("OK"))
        dialogo.set_default_response("ok")
        dialogo.present(win)

    def _al_cambiar_switch(*_):
        usar = fila_usar_sistema.get_active()
        win.combo_idioma.set_sensitive(not usar)
        if usar:
            establecer_usar_idioma_sistema(True)
            for i, codigo in enumerate(codigos_idiomas):
                if codigo == IDIOMA_ACTUAL:
                    win.combo_idioma.set_selected(i)
                    break
            _mostrar_dialogo_reinicio()

    def _al_cambiar_idioma(*_):
        if fila_usar_sistema.get_active():
            return
        idx = win.combo_idioma.get_selected()
        if idx == -1 or codigos_idiomas[idx] == IDIOMA_ACTUAL:
            return
        establecer_idioma(codigos_idiomas[idx])
        _mostrar_dialogo_reinicio()

    fila_usar_sistema.connect("notify::active", _al_cambiar_switch)
    win.combo_idioma.connect("notify::selected", _al_cambiar_idioma)
    grupo_idioma.add(fila_usar_sistema)
    grupo_idioma.add(win.combo_idioma)
    pref_page.add(grupo_idioma)

    header = Adw.HeaderBar()

    caja_gestion = Gtk.Box(spacing=6)
    win._botones_gestion = {}
    for icono, accion, clase, ayuda, capacidad in [
        ("media-playback-start-symbolic", "aplicar", "success", traducir("Aplicar el planificador seleccionado"), "aplicar"),
        ("media-playback-stop-symbolic", "detener", "destructive-action", traducir("Detener el planificador activo"), "detener"),
        ("view-refresh-symbolic", "reiniciar", "", traducir("Reiniciar el planificador activo"), "reiniciar"),
    ]:
        b = Gtk.Button(icon_name=icono, tooltip_text=ayuda,
                       css_classes=[clase] if clase else ["flat"])
        b.connect("clicked", lambda btn, a=accion: ejecutar_mantenimiento(win, btn, a))
        # No todos los backends saben hacer todo: sin `scx_loader` no existe
        # "reiniciar". Se deshabilita el botón en vez de dejar que falle.
        b.set_sensitive(win.scx.puede(capacidad))
        win._botones_gestion[accion] = b
        caja_gestion.append(b)

    header.pack_start(caja_gestion)

    # Refrescar sólo relee el estado. Antes ejecutaba un "restart", así que el
    # botón de actualizar reiniciaba de verdad el planificador del sistema.
    btn_actualizar = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=traducir("Actualizar Estado"), css_classes=["flat"])
    btn_actualizar.connect("clicked", lambda btn: win.sincronizar_sistema())
    header.pack_end(btn_actualizar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_controles.set_child(view)


def ejecutar_mantenimiento(win, btn, accion):
    """Ejecuta aplicar / detener / reiniciar sobre el planificador del sistema."""
    def _proceder():
        def _ejecutar():
            try:
                if accion == "aplicar":
                    sched = win.scheduler_seleccionado()
                    if not sched:
                        mostrar_toast(win, traducir("Elegí un planificador primero"),
                                      prefijo=traducir("Aviso"))
                        return
                    modo_item = win.combo_modos.get_selected_item()
                    modo = modo_item.get_string() if modo_item else "auto"
                    resultado = win.scx.aplicar(sched, modo)
                elif accion == "detener":
                    resultado = win.scx.detener()
                else:
                    resultado = win.scx.reiniciar()
                _informar_resultado(win, resultado, accion)
            except Exception as e:
                registrar_error(f"ejecutar_mantenimiento({accion})", e)
                mostrar_toast(win, str(e), prefijo=traducir("Error"))
            finally:
                GLib.idle_add(win.sincronizar_sistema)

        threading.Thread(target=_ejecutar, daemon=True).start()

    win.ejecutar_accion_privilegiada(_proceder)


#: Mensajes por código de fallo. Distinguirlos importa: "cancelaste el diálogo"
#: y "el kernel no soporta sched_ext" exigen respuestas del usuario opuestas.
_MENSAJES_FALLO = {
    "no_autorizado": "No se concedieron los permisos necesarios",
    "cancelado": "Se canceló la autenticación",
    "sin_servicio": "El servicio scx_loader no está disponible en este equipo",
    "sin_backend": "Este equipo no permite controlar planificadores",
    "sin_elevacion": "No hay forma de obtener privilegios (falta pkexec o run0)",
    "no_instalado": "Ese planificador no está instalado en el sistema",
    "no_soportado": "El backend activo no soporta esta operación",
    "tiempo_agotado": "La operación excedió el tiempo de espera",
    "sin_scheduler": "No hay ningún planificador activo",
}


def _informar_resultado(win, resultado, accion):
    """Traduce un Resultado del backend a un aviso comprensible."""
    if resultado.ok:
        etiquetas = {
            "aplicar": traducir("Planificador aplicado"),
            "detener": traducir("Planificador detenido"),
            "reiniciar": traducir("Planificador reiniciado"),
        }
        mostrar_toast(win, etiquetas.get(accion, traducir("Listo")))
        return
    plantilla = _MENSAJES_FALLO.get(resultado.codigo)
    mensaje = traducir(plantilla) if plantilla else (
        resultado.mensaje or traducir("La operación falló"))
    # El detalle técnico se conserva, pero no encabeza el aviso.
    if plantilla and resultado.mensaje:
        mensaje = f"{mensaje} — {resultado.mensaje}"
    mostrar_toast(win, mensaje, prefijo=traducir("Aviso"))


def aplicar_cambio_scheduler(win, btn=None):
    """Reacciona a un cambio de selección en los combos.

    Cambiar la selección ya NO aplica el planificador: aplicar es una acción
    privilegiada e invasiva, y dispararla con el simple scroll del ratón sobre
    un `Adw.ComboRow` era un accidente esperando a ocurrir. Ahora sólo se
    actualiza la lista de modos válidos para el scheduler elegido y se resalta
    el botón de aplicar cuando la selección difiere de lo que corre.
    """
    if win.en_sincronizacion:
        return

    sched = win.scheduler_seleccionado()
    if not sched:
        return

    win.en_sincronizacion = True
    try:
        win._sincronizar_modos(None, None)
    except Exception as e:
        registrar_error("aplicar_cambio_scheduler", e)
    finally:
        win.en_sincronizacion = False

    boton = getattr(win, "_botones_gestion", {}).get("aplicar")
    if boton is not None:
        pendiente = sched != win.active_sc
        boton.set_css_classes(["suggested-action"] if pendiente else ["success"])
        boton.set_tooltip_text(
            traducir("Aplicar {}").format(nombre_corto(sched)) if pendiente
            else traducir("Ya está aplicado"))
