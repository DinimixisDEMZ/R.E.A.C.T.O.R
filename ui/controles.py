"""
Pestaña de Controles: Estado actual, selección de scheduler/modo, acciones.
"""

import math
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.database import activar_db_temporal, desactivar_db_temporal, obtener_info_scheduler
from ui.disponibilidad import recargar_disponibilidad_ui
from utils.helpers import generar_color_hash


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

    win.combo_schedulers.connect("notify::selected-item", lambda *_: aplicar_cambio_scheduler(win))
    win.combo_modos.connect("notify::selected-item", lambda *_: aplicar_cambio_scheduler(win))

    grupo_config.add(win.combo_schedulers)
    grupo_config.add(win.combo_modos)

    grupo_info = Adw.PreferencesGroup(title="Información del Planificador")
    win._sched_info_card = Gtk.Frame(css_classes=["card"])
    win._sched_info_card.set_visible(False)
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
    win._sched_info_link = Gtk.LinkButton(label="Abrir documentación »", uri="https://github.com/sched-ext/scx", halign=Gtk.Align.START)
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
                win._sched_info_desc.set_label(desc)
                win._sched_info_link.set_uri(url or "https://github.com/sched-ext/scx")
            else:
                win._sched_info_desc.set_label("No hay información disponible para este planificador. Consultá la documentación oficial para más detalles.")
                win._sched_info_link.set_uri("https://github.com/sched-ext/scx")

            r, g, b = generar_color_hash(name)
            win._sched_info_dot.set_draw_func(lambda a, cr, w, h, cr_r=r, cr_g=g, cr_b=b: (
                cr.set_source_rgb(cr_r, cr_g, cr_b),
                cr.arc(w / 2, h / 2, 3.5, 0, 2 * math.pi),
                cr.fill(),
            ))
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
    grupo_dev = Adw.PreferencesGroup(title="Herramientas de Depuración")
    fila_dev = Adw.ActionRow(title="Modo Simulación", subtitle="Prueba la UI sin hardware real ni scxctl")
    sw_dev = Gtk.Switch(active=win.modo_desarrollador, valign=Gtk.Align.CENTER)

    def _toggle_dev(sw, ps):
        win.modo_desarrollador = sw.get_active()
        win.scx.modo_desarrollador = win.modo_desarrollador
        if win.modo_desarrollador:
            activar_db_temporal()
            est = "ACTIVADO"
        else:
            desactivar_db_temporal()
            est = "DESACTIVADO"
        win.compatibles = None
        win.nav_disponibilidad.remove_css_class("pulse-warning")
        img = win.nav_disponibilidad.get_child().get_first_child()
        if isinstance(img, Gtk.Image):
            for cls in ["success", "error"]:
                img.remove_css_class(cls)
            img.set_from_icon_name("dialog-question-symbolic")
        win.toast_overlay.add_toast(Adw.Toast.new(f"Modo Desarrollador: {est}"))
        recargar_disponibilidad_ui(win)
        if hasattr(win, '_refrescar_auto_schedulers'):
            from ui.automatizacion import _refrescar_auto_schedulers
            _refrescar_auto_schedulers(win)
        win.sincronizar_sistema()

    sw_dev.connect("notify::active", _toggle_dev)
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
    btn_actualizar.connect("clicked", lambda btn: ejecutar_mantenimiento(win, btn, "restart"))
    header.pack_end(btn_actualizar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_controles.set_child(view)


def ejecutar_mantenimiento(win, btn, acc):
    """Ejecuta start/stop/restart de scxctl."""
    def _proceder():
        def _ejecutar():
            try:
                cmd_base = ["scxctl", acc]
                if acc == "start":
                    item = win.combo_schedulers.get_selected_item()
                    if item:
                        cmd_base += ["-s", item.get_string()]
                    modo_item = win.combo_modos.get_selected_item()
                    if modo_item:
                        cmd_base += ["-m", modo_item.get_string()]

                result = win.scx.ejecutar_con_sudo(cmd_base)

                if result.returncode != 0:
                    error_msg = result.stderr.strip().lower()
                    if acc == "start" and ("already" in error_msg or "running" in error_msg or not error_msg):
                        mensaje = "Ya se está ejecutando"
                    elif acc == "stop" and ("not running" in error_msg or not error_msg):
                        mensaje = "No hay ningún planificador activo"
                    else:
                        mensaje = result.stderr.strip() or "Comando fallido"

                    msafe = GLib.markup_escape_text(mensaje)
                    GLib.idle_add(lambda m=msafe: win.toast_overlay.add_toast(Adw.Toast.new(f"Aviso: {m}")))
            except Exception as e:
                err_msg = GLib.markup_escape_text(str(e))
                GLib.idle_add(lambda m=err_msg: win.toast_overlay.add_toast(Adw.Toast.new(f"Error: {m}")))
            finally:
                GLib.idle_add(win.sincronizar_sistema)

        threading.Thread(target=_ejecutar, daemon=True).start()

    win.solicitar_sudo_si_necesario(_proceder)


def aplicar_cambio_scheduler(win, btn=None):
    """Aplica el cambio de scheduler/modo seleccionado."""
    if win.en_sincronizacion:
        return

    sched_item = win.combo_schedulers.get_selected_item()
    modo_item = win.combo_modos.get_selected_item()

    if not sched_item or not modo_item:
        return

    sched = sched_item.get_string()
    modo = modo_item.get_string()

    def _proceder():
        def _aplicar():
            try:
                sc_actual, _ = win.scx.obtener_estado()
                if sc_actual:
                    cmd = ["scxctl", "switch", "-s", sched, "-m", modo]
                else:
                    cmd = ["scxctl", "start", "-s", sched, "-m", modo]
                result = win.scx.ejecutar_con_sudo(cmd)

                if result.returncode != 0:
                    err_safe = GLib.markup_escape_text(result.stderr.strip() or 'Cambio fallido')
                    GLib.idle_add(lambda m=err_safe: win.toast_overlay.add_toast(Adw.Toast.new(f"Error: {m}")))
                else:
                    GLib.idle_add(lambda: win.toast_overlay.add_toast(Adw.Toast.new(f"Aplicado: {sched} [{modo}]")))
            except Exception as e:
                err_msg = GLib.markup_escape_text(str(e))
                GLib.idle_add(lambda m=err_msg: win.toast_overlay.add_toast(Adw.Toast.new(f"Error: {m}")))
            finally:
                GLib.idle_add(win.sincronizar_sistema)

        threading.Thread(target=_aplicar, daemon=True).start()

    win.solicitar_sudo_si_necesario(_proceder)
