"""
Diálogo de autenticación por contraseña.
Solicita permisos sudo al usuario con estilo Adwaita.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


class DialogoPassword(Adw.Window):
    """Ventana modal para solicitar contraseña de administrador."""

    def __init__(self, parent_window, on_success):
        super().__init__()
        self.set_transient_for(parent_window)
        self.set_modal(True)
        self.set_title("Autenticación Requerida")
        self.set_default_size(350, 200)
        self.on_success = on_success

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

        self.btn = Gtk.Button(label="Cancelar", css_classes=["pill"])
        self.btn.connect("clicked", self.validar)
        box.append(self.btn)

        self.lbl_error = Gtk.Label(label="", css_classes=["error"])
        self.lbl_error.set_visible(False)
        box.append(self.lbl_error)

        self.set_content(box)

    def on_buffer_changed(self, *args):
        if self.entry.get_text():
            self.btn.set_label("Desbloquear")
            self.btn.add_css_class("suggested-action")
        else:
            self.btn.set_label("Cancelar")
            self.btn.remove_css_class("suggested-action")

    def validar(self, *args):
        pwd = self.entry.get_text()
        if not pwd:
            self.close()
            return
        self.on_success(pwd)
        self.close()
