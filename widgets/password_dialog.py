"""
Diálogo de autenticación por contraseña.
Solicita permisos sudo al usuario con estilo Adwaita.
"""

import inspect

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


def backend_no_requiere_password(backend):
    """Indica si la autorización se delega al sistema, sin contraseña propia."""
    return str(backend or "").casefold() in {"run0", "direct"}


def _callback_acepta_respuesta(callback):
    """Detecta el contrato asíncrono ``callback(password, complete)``."""
    try:
        inspect.signature(callback).bind("password", lambda *_args: None)
    except (TypeError, ValueError):
        return False
    return True


class DialogoPassword(Adw.Window):
    """Ventana modal para solicitar contraseña de administrador."""

    def __init__(self, parent_window, on_success):
        super().__init__()
        self.set_transient_for(parent_window)
        self.set_modal(True)
        self.set_title("Autenticación Requerida")
        self.set_default_size(350, 220)
        self.on_success = on_success
        self._callback_asincrono = _callback_acepta_respuesta(on_success)
        self._validando = False
        self._cerrado = False
        self._exito_aceptado = False
        self.connect("close-request", self._al_cerrar)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=24, margin_bottom=24, margin_start=24, margin_end=24
        )

        icon = Gtk.Image(icon_name="dialog-password-symbolic", pixel_size=48)
        icon.add_css_class("accent")
        box.append(icon)

        lbl = Gtk.Label(
            label="Se requieren permisos de administrador\npara gestionar el kernel.",
            justify=Gtk.Justification.CENTER
        )
        lbl.add_css_class("title-4")
        box.append(lbl)

        self.entry = Gtk.PasswordEntry(placeholder_text="Contraseña de root/sudo")
        self.entry.connect("activate", self.validar)
        self.entry.connect("changed", self.on_buffer_changed)
        box.append(self.entry)

        self.spinner = Adw.Spinner(visible=False)
        box.append(self.spinner)

        self.btn = Gtk.Button(label="Cancelar", css_classes=["pill"])
        self.btn.connect("clicked", self.validar)
        box.append(self.btn)

        self.lbl_error = Gtk.Label(label="", css_classes=["error"])
        self.lbl_error.set_visible(False)
        box.append(self.lbl_error)

        self.set_content(box)

    @property
    def cerrado(self):
        return self._cerrado

    @property
    def cancelado(self):
        return self._cerrado and not self._exito_aceptado

    def aceptar_exito(self):
        """Reclama una validación correcta una sola vez mientras siga abierto."""
        if self._cerrado or self._exito_aceptado:
            return False
        self._exito_aceptado = True
        return True

    def on_buffer_changed(self, *args):
        if self._validando:
            return
        self.lbl_error.set_visible(False)
        if self.entry.get_text():
            self.btn.set_label("Desbloquear")
            self.btn.add_css_class("suggested-action")
        else:
            self.btn.set_label("Cancelar")
            self.btn.remove_css_class("suggested-action")

    def _mostrar_espera(self):
        self._validando = True
        self.entry.set_sensitive(False)
        self.btn.set_sensitive(False)
        self.spinner.set_visible(True)
        self.lbl_error.remove_css_class("error")
        self.lbl_error.add_css_class("dim-label")
        self.lbl_error.set_label("Validando credenciales...")
        self.lbl_error.set_visible(True)

    def completar_validacion(self, valido, error=None):
        """Completa una validación asíncrona desde el hilo GTK."""
        if self._cerrado:
            return False
        if valido and not self._exito_aceptado:
            if not self.aceptar_exito():
                return False
        self._validando = False
        self.spinner.set_visible(False)
        if valido:
            self.close()
            return True

        self.entry.set_sensitive(True)
        self.btn.set_sensitive(True)
        self.btn.set_label("Cancelar")
        self.btn.remove_css_class("suggested-action")
        self.lbl_error.remove_css_class("dim-label")
        self.lbl_error.add_css_class("error")
        self.lbl_error.set_label(error or "No se pudo validar la contraseña.")
        self.lbl_error.set_visible(True)
        self.entry.grab_focus()
        return True

    def validar(self, *args):
        if self._validando:
            return

        password = self.entry.get_text()
        if not password:
            self.close()
            return

        self.entry.set_text("")
        self._mostrar_espera()
        try:
            if self._callback_asincrono:
                resultado = self.on_success(password, self.completar_validacion)
                if isinstance(resultado, bool):
                    self.completar_validacion(resultado)
            else:
                resultado = self.on_success(password)
                self.completar_validacion(
                    resultado is not False,
                    "No se pudo validar la contraseña.",
                )
        except Exception as exc:
            self.completar_validacion(False, str(exc) or exc.__class__.__name__)
        finally:
            password = None

    def _al_cerrar(self, *_args):
        self._cerrado = True
        self.entry.set_text("")
        return False
