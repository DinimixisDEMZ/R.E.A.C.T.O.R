"""
Ventana principal de REACTOR.
Orquesta los módulos de UI, core y widgets.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.scx import ScxManager
from core.thermal import SensorTermico
from utils.helpers import RE_RUNNING
from ui.grafico import GraficoComparativo
from ui.controles import setup_controles_ui
from ui.rendimiento import setup_rendimiento_ui, actualizar_interfaz_ranking
from ui.automatizacion import setup_automatizacion_ui
from ui.disponibilidad import setup_disponibilidad_ui
from ui.diagnostico import configurar_ui_diagnostico
from ui.historial import configurar_ui_historial
from core.database import inicializar_db, obtener_versiones, detectar_cambio_version, cargar_compatibilidad
from widgets.password_dialog import DialogoPassword


class VentanaSimple(Adw.ApplicationWindow):
    """Ventana principal de la aplicación Reactor — REACTOR"""
    REACTOR = "Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento"

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("R.E.A.C.T.O.R")
        self.set_default_size(950, 850)

        # Libadwaita maneja el tema
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

        # ── Estado ──
        self.datos_rendimiento = []
        self.en_proceso_bench = False
        self.en_proceso_auto = False
        self.en_sincronizacion = False
        self.compatibles = None
        self.progreso_actual = 0.0
        self.progreso_objetivo = 0.0
        self.modo_desarrollador = False
        self.active_sc = None

        # ── Core ──
        self.scx = ScxManager(modo_desarrollador=self.modo_desarrollador)
        self.sensor = SensorTermico()
        self.grafico = GraficoComparativo()

        # ── Historial ──
        inicializar_db()
        self.versiones = obtener_versiones()

        # ── Caché de compatibilidad ──
        kernel_actual = self.versiones.get("kernel", "")
        cache_compat = cargar_compatibilidad(kernel_actual) if kernel_actual else {}
        self.compatibles = [n for n, (ok, _, _) in cache_compat.items() if ok] or None

        # ── Layout ──
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView()
        self.toast_overlay.set_child(self.split_view)

        # ── Páginas ──
        self.pag_controles = Adw.NavigationPage(title="Controles", tag="page_a")
        setup_controles_ui(self)
        self.pag_rendimiento = Adw.NavigationPage(title="Rendimiento", tag="page_b")
        setup_rendimiento_ui(self)
        self.pag_automatizacion = Adw.NavigationPage(title="Automatización", tag="page_c")
        setup_automatizacion_ui(self)
        self.pag_disponibilidad = Adw.NavigationPage(title="Disponibilidad", tag="page_d")
        setup_disponibilidad_ui(self)
        self.pag_historial = Adw.NavigationPage(title="Historial", tag="page_f")
        configurar_ui_historial(self)
        # Diagnóstico
        configurar_ui_diagnostico(self)


        # ── CSS personalizado ──
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data("""
            @keyframes pulse_warning {
                0% { color: inherit; }
                50% { color: #f5c211; }
                100% { color: inherit; }
            }
            .pulse-warning {
                animation: pulse_warning 2.5s infinite;
            }
            .chip {
                border-radius: 12px;
                padding: 4px 10px;
                margin: 2px;
            }
            .history-group {
                padding: 0;
            }
            .chip.success {
                background: alpha(@success_color, 0.15);
                color: @success_color;
            }
            .chip.error {
                background: alpha(@error_color, 0.15);
                color: @error_color;
            }
            .chip.warning {
                background: alpha(@warning_color, 0.15);
                color: @warning_color;
            }
        """, -1)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ── Sidebar ──
        self.setup_sidebar()
        self.sincronizar_sistema()
        actualizar_interfaz_ranking(self)

        # ── Detección de cambios de versión ──
        cambio, componentes = detectar_cambio_version(self.versiones)
        if cambio and componentes:
            self.toast_overlay.add_toast(Adw.Toast.new(
                f"Entorno actualizado: {', '.join(componentes)}"
            ))

    def setup_sidebar(self):
        """Construye la barra lateral de navegación."""
        pag_sidebar = Adw.NavigationPage(title="R.E.A.C.T.O.R", tag="sidebar")
        header_side = Adw.HeaderBar()

        # Sensor Térmico
        self.btn_termica = Gtk.MenuButton(css_classes=["flat"])
        self.img_termica = Gtk.Image(icon_name="temperature-symbolic")
        self.btn_termica.set_child(self.img_termica)
        self.img_termica.add_css_class("dim-label")
        self.btn_termica.set_tooltip_text("Monitoreo térmico del sistema")

        popover = Gtk.Popover()
        box_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)

        lbl_info = Gtk.Label(label="<b>Integridad Térmica</b>", use_markup=True)
        self.lbl_termica_detail = Gtk.Label(label="Analizando...", wrap=True, max_width_chars=30, justify=Gtk.Justification.CENTER)
        self.lbl_termica_detail.add_css_class("caption")
        self.lbl_termica_detail.add_css_class("dim-label")

        lbl_desc = Gtk.Label(
            label="Este sistema permite realizar pruebas más justas sin penalizaciones por sobrecalentamiento.",
            wrap=True, max_width_chars=30, justify=Gtk.Justification.CENTER
        )
        lbl_desc.add_css_class("caption")
        lbl_desc.add_css_class("dim-label")

        box_info.append(lbl_info)
        box_info.append(self.lbl_termica_detail)
        box_info.append(lbl_desc)
        popover.set_child(box_info)
        self.btn_termica.set_popover(popover)

        header_side.pack_start(self.btn_termica)

        self.btn_about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text="Acerca de")
        self.btn_about.connect("clicked", self.mostrar_acerca_de)
        header_side.pack_end(self.btn_about)

        self.lista_nav = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.lista_nav.connect("row-activated", lambda l, f: self.split_view.set_content(f.destino))

        self.agregar_opcion(self.lista_nav, "Controles", "preferences-system-symbolic", self.pag_controles)
        self.agregar_opcion(self.lista_nav, "Rendimiento", "power-profile-performance-symbolic", self.pag_rendimiento)
        self.agregar_opcion(self.lista_nav, "Automatización", "network-server-symbolic", self.pag_automatizacion)
        self.nav_disponibilidad = self.agregar_opcion(self.lista_nav, "Disponibilidad", "dialog-information-symbolic", self.pag_disponibilidad)
        self.agregar_opcion(self.lista_nav, "Diagnóstico", "sonar-symbolic", self.pag_diagnostico)
        self.agregar_opcion(self.lista_nav, "Historial", "document-open-recent-symbolic", self.pag_historial)

        if not self.compatibles:
            self.nav_disponibilidad.add_css_class("pulse-warning")

        view_side = Adw.ToolbarView(content=self.lista_nav)
        view_side.add_top_bar(header_side)
        pag_sidebar.set_child(view_side)
        self.split_view.set_sidebar(pag_sidebar)
        self.split_view.set_content(self.pag_controles)

        # Monitoreo térmico
        GLib.timeout_add(2000, self.actualizar_sensor_termico)

    def actualizar_sensor_termico(self):
        """Actualiza el indicador térmico en la sidebar."""
        t = self.sensor.obtener_temp()
        if t == 0:
            return True

        if t < 60:
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.btn_termica.set_tooltip_text(f"Sistema estable ({t:.1f}°C)")
            self.lbl_termica_detail.set_label(f"Estado: Estable ({t:.1f}°C)")
            self.img_termica.remove_css_class("error")
            self.img_termica.remove_css_class("warning")
            self.img_termica.add_css_class("dim-label")
        elif t < 75:
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.btn_termica.set_tooltip_text(f"Temperatura elevada ({t:.1f}°C)")
            self.lbl_termica_detail.set_label(f"Estado: Elevado ({t:.1f}°C)")
            self.img_termica.remove_css_class("error")
            self.img_termica.add_css_class("warning")
            self.img_termica.remove_css_class("dim-label")
        else:
            self.img_termica.set_from_icon_name("software-update-urgent-symbolic")
            self.btn_termica.set_tooltip_text(f"¡LIMITADOR TÉRMICO ACTIVO! ({t:.1f}°C)")
            self.lbl_termica_detail.set_label(f"Estado: CRÍTICO ({t:.1f}°C)")
            self.img_termica.add_css_class("error")
            self.img_termica.remove_css_class("warning")
            self.img_termica.remove_css_class("dim-label")
        return True

    def mostrar_acerca_de(self, btn):
        """Muestra el diálogo Acerca De."""
        dialogo = Adw.AboutDialog(
            application_name="R.E.A.C.T.O.R",
            version="0.7.5",
            comments=f"{VentanaSimple.REACTOR}\nHerramienta de gestión y benchmarking para schedulers sched-ext (SCX).",
            developer_name="Equipo de Desarrollo R.E.A.C.T.O.R",
            developers=[
                "DinimixisDEMZ (Lead Developer/Lead Designer)",
                "Dark Anubis (IT Technical Specialist, System Administrator, DevOps & Network Tech.)",
                "MD1000[Emedé] (Beta Tester/Designer)",
                "SunnyDeus (Beta Tester)",
                "JMX369 (Beta Tester)",
                "Ezku (Beta Tester)",
                "Gekko (Designer)",
            ],
            support_url="https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R/issues",
            application_icon='reactor',
            release_notes="""<p>Novedades en la versión 0.7.5:</p>
<ul>
  <li>Icono de aplicación propio en el diálogo Acerca de.</li>
  <li>Modo sistema con fallback automático a los iconos alternativos cuando faltan.</li>
  <li>Iconos de navegación renovados (atrás/adelante).</li>
  <li>Correcciones menores de interfaz.</li>
</ul>"""
        )

        dialogo.add_link("Página Web / GitHub", "https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R")
        # Agrega la sección legal sin argumentos no reconocidos
        dialogo.add_legal_section(
            title="R.E.A.C.T.O.R Faircode License",
            copyright="© 2026 El proyecto R.E.A.C.T.O.R y sus desarrolladores.",
            
            license_type=Gtk.License.CUSTOM,
            license="""Se concede permiso para usar, estudiar y modificar este software para uso personal, investigación y uso interno en organizaciones.

<b>Términos de la Licencia Faircode:</b>
• <b>Uso Personal e Interno:</b> Gratuito y sin restricciones de funciones.
• <b>Servicios a Terceros (SaaS):</b> Se prohíbe explícitamente revender, redistribuir comercialmente o alojar R.E.A.C.T.O.R como un servicio de pago para terceros sin autorización.
• <b>Sin Garantía:</b> El software se proporciona "tal cual", sin garantías sobre modificaciones en el kernel o rendimiento del sistema.

Para más información sobre el modelo Faircode:
<a href="https://faircode.io">https://faircode.io</a>""",
        )
        dialogo.add_acknowledgement_section("Agentes de IA", [
            "opencode (AI Software Engineer - big-pickle)",
            "Antigravity (AI Assistant)",
            "DeepSeek V4 Flash (AI Software Engineer)",
        ])
        dialogo.present(self)

    def sincronizar_sistema(self):
        """Sincroniza el estado de la UI con el sistema real."""
        try:
            self.en_sincronizacion = True
            lista_nombres = self.scx.obtener_lista(self.compatibles)
            self.modelo_schedulers.splice(0, self.modelo_schedulers.get_n_items(), lista_nombres)

            sc_name, sc_mode = self.scx.obtener_estado()
            self.boton_estado.remove_css_class("success")
            self.boton_estado.remove_css_class("destructive-action")
            if sc_name:
                self.boton_estado.set_label(f"{sc_name} [{sc_mode}]")
                self.boton_estado.add_css_class("success")
                self.active_sc = sc_name
                for i, nombre in enumerate(lista_nombres):
                    if nombre.lower() == sc_name.lower():
                        self.combo_schedulers.set_selected(i)
                        break
                model_modos = self.combo_modos.get_model()
                for i in range(model_modos.get_n_items()):
                    if model_modos.get_string(i).lower() == sc_mode.lower():
                        self.combo_modos.set_selected(i)
                        break
            else:
                self.boton_estado.set_label("Sistema Base (Default)")
                self.boton_estado.add_css_class("destructive-action")
                self.active_sc = None
            self.en_sincronizacion = False
            actualizar_interfaz_ranking(self)
        except FileNotFoundError:
            self.boton_estado.set_label("scxctl no encontrado")
            self.boton_estado.add_css_class("destructive-action")
            self.en_sincronizacion = False
        except Exception as e:
            print(f"Error sincronizando: {e}")
            self.en_sincronizacion = False

    def solicitar_sudo_si_necesario(self, callback):
        """Solicita sudo si la sesión no está activa."""
        if self.modo_desarrollador:
            callback()
            return

        if self.scx.sudo_disponible():
            callback()
        else:
            def al_validar(pwd):
                if self.scx.validar_sudo(pwd):
                    callback()
                    return True
                return False

            dialog = DialogoPassword(self, al_validar)
            dialog.present()

    def agregar_opcion(self, lista, titulo, icono, pagina):
        """Agrega una opción a la sidebar de navegación."""
        fila = Gtk.ListBoxRow()
        caja = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        caja.append(Gtk.Image.new_from_icon_name(icono))
        caja.append(Gtk.Label(label=titulo))
        fila.set_child(caja)
        fila.destino = pagina
        lista.append(fila)
        return fila
