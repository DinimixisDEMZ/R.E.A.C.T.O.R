# Copyright (C) 2026 Dinimixis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Ventana principal de REACTOR.
Orquesta los módulos de UI, core y widgets.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.scx import GestorScx
from core.thermal import SensorTermico
from utils.helpers import RE_RUNNING
from ui.grafico import GraficoComparativo
from ui.controles import configurar_ui_controles
from ui.rendimiento import configurar_ui_rendimiento, actualizar_interfaz_ranking
from ui.automatizacion import configurar_ui_automatizacion
from ui.disponibilidad import configurar_ui_disponibilidad
from ui.diagnostico import configurar_ui_diagnostico
from ui.historial import configurar_ui_historial
from core.constantes import VERSION, TEMP_UMBRAL_ESTABLE, TEMP_UMBRAL_ELEVADA
from utils.iconos import TEMPERATURA_CRITICA
from core.estado import EstadoPrueba, EstadoMonitor, EstadoDeteccionAuto
from core.database import inicializar_db, obtener_versiones, detectar_cambio_version, cargar_compatibilidad
from widgets.password_dialog import DialogoPassword
from utils.i18n import traducir
from core.verificacion import verificar_si_primera_vez, marcar_verificacion_hecha
from ui.verificacion import mostrar_verificacion


class VentanaSimple(Adw.ApplicationWindow):
    """Ventana principal de la aplicación Reactor — REACTOR"""
    REACTOR = "Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento"

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(traducir("R.E.A.C.T.O.R"))
        self.set_default_size(950, 850)

        # Libadwaita maneja el tema
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

        # ── Estado por módulo ──
        self.bench_state = EstadoPrueba()
        self.monitor_state = EstadoMonitor()
        self.auto_state = EstadoDeteccionAuto()

        # ── Estado global ──
        self.en_sincronizacion = False
        self.compatibles = None
        self.modo_desarrollador = False
        self.active_sc = None

        # ── Core ──
        self.scx = GestorScx(modo_desarrollador=self.modo_desarrollador)
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
        self.pag_controles = Adw.NavigationPage(title=traducir("Controles"), tag="page_a")
        configurar_ui_controles(self)
        self.pag_rendimiento = Adw.NavigationPage(title=traducir("Rendimiento"), tag="page_b")
        configurar_ui_rendimiento(self)
        self.pag_automatizacion = Adw.NavigationPage(title=traducir("Automatización"), tag="page_c")
        configurar_ui_automatizacion(self)
        self.pag_disponibilidad = Adw.NavigationPage(title=traducir("Disponibilidad"), tag="page_d")
        configurar_ui_disponibilidad(self)
        self.pag_historial = Adw.NavigationPage(title=traducir("Historial"), tag="page_f")
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
                f"{traducir('Entorno actualizado')}: {', '.join(componentes)}"
            ))

        # ── Verificación de primera vez ──
        if verificar_si_primera_vez():
            def _al_cerrar_verificacion(*_):
                marcar_verificacion_hecha()
            dialogo = mostrar_verificacion(self)
            dialogo.connect("closed", _al_cerrar_verificacion)

    def setup_sidebar(self):
        """Construye la barra lateral de navegación."""
        pag_sidebar = Adw.NavigationPage(title=traducir("R.E.A.C.T.O.R"), tag="sidebar")
        header_side = Adw.HeaderBar()

        # Sensor Térmico
        self.btn_termica = Gtk.MenuButton(css_classes=["flat"])
        self.img_termica = Gtk.Image(icon_name="temperature-symbolic")
        self.btn_termica.set_child(self.img_termica)
        self.img_termica.add_css_class("dim-label")
        self.btn_termica.set_tooltip_text(traducir("Monitoreo térmico del sistema"))

        popover = Gtk.Popover()
        box_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)

        lbl_info = Gtk.Label(label=traducir("<b>Integridad Térmica</b>"), use_markup=True)
        self.lbl_termica_detail = Gtk.Label(label=traducir("Analizando..."), wrap=True, max_width_chars=30, justify=Gtk.Justification.CENTER)
        self.lbl_termica_detail.add_css_class("caption")
        self.lbl_termica_detail.add_css_class("dim-label")

        lbl_desc = Gtk.Label(
            label=traducir("Este sistema permite realizar pruebas más justas sin penalizaciones por sobrecalentamiento."),
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

        self.btn_about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text=traducir("Acerca de"))
        self.btn_about.connect("clicked", self.mostrar_acerca_de)
        header_side.pack_end(self.btn_about)

        self.lista_nav = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.lista_nav.connect("row-activated", lambda l, f: self.split_view.set_content(f.destino))

        self.agregar_opcion(self.lista_nav, traducir("Controles"), "preferences-system-symbolic", self.pag_controles)
        self.agregar_opcion(self.lista_nav, traducir("Rendimiento"), "power-profile-performance-symbolic", self.pag_rendimiento)
        self.agregar_opcion(self.lista_nav, traducir("Automatización"), "network-server-symbolic", self.pag_automatizacion)
        self.nav_disponibilidad = self.agregar_opcion(self.lista_nav, traducir("Disponibilidad"), "dialog-information-symbolic", self.pag_disponibilidad)
        self.agregar_opcion(self.lista_nav, traducir("Diagnóstico"), "sonar-symbolic", self.pag_diagnostico)
        self.agregar_opcion(self.lista_nav, traducir("Historial"), "document-open-recent-symbolic", self.pag_historial)

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

        if t < TEMP_UMBRAL_ESTABLE:
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.btn_termica.set_tooltip_text(f"{traducir('Sistema estable')} ({t:.1f}°C)")
            self.lbl_termica_detail.set_label(f"{traducir('Estado: Estable')} ({t:.1f}°C)")
            self.img_termica.remove_css_class("error")
            self.img_termica.remove_css_class("warning")
            self.img_termica.add_css_class("dim-label")
        elif t < TEMP_UMBRAL_ELEVADA:
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.btn_termica.set_tooltip_text(f"{traducir('Temperatura elevada')} ({t:.1f}°C)")
            self.lbl_termica_detail.set_label(f"{traducir('Estado: Elevado')} ({t:.1f}°C)")
            self.img_termica.remove_css_class("error")
            self.img_termica.add_css_class("warning")
            self.img_termica.remove_css_class("dim-label")
        else:
            self.img_termica.set_from_icon_name(TEMPERATURA_CRITICA)
            self.btn_termica.set_tooltip_text(f"{traducir('¡LIMITADOR TÉRMICO ACTIVO!')} ({t:.1f}°C)")
            self.lbl_termica_detail.set_label(f"{traducir('Estado: CRÍTICO')} ({t:.1f}°C)")
            self.img_termica.add_css_class("error")
            self.img_termica.remove_css_class("warning")
            self.img_termica.remove_css_class("dim-label")
        return True

    def mostrar_acerca_de(self, btn):
        """Muestra el diálogo Acerca De."""
        dialogo = Adw.AboutDialog(
            application_name=traducir("R.E.A.C.T.O.R"),
            version=VERSION,
            comments=traducir("Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento\nBenchmarking y optimización de planificadores sched-ext (SCX) para Linux."),
            developer_name=traducir("DinimixisDEMZ"),
            support_url="https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R/issues",
            application_icon='reactor',
            release_notes=traducir("""<p>R.E.A.C.T.O.R 1.2 — ¡Iconos portátiles y más!</p>
<ul>
  <li>Iconos empaquetados en GResource: se ven en cualquier distribución sin depender del tema del sistema.</li>
  <li>Botones de benchmark movidos a cada fila individual — más claro y directo.</li>
  <li>Acerca de renovado: secciones limpias sin paréntesis, créditos reorganizados.</li>
  <li>Logs guardados en base de datos: al navegar el historial se restaura el registro completo.</li>
  <li>Verificación del sistema: soporte para run0 (systemd ≥256), detección de rt-tests en PATH.</li>
  <li>AppImage autónomo: incluye stress-ng, hyperfine, rt-tests source — sin dependencias externas.</li>
  <li>Gráfico radar con animación de pulso más suave.</li>
</ul>
<p>R.E.A.C.T.O.R 1.1 — Internacionalización.</p>
<ul>
  <li>Idioma: ahora podés usar la app en español, inglés, francés, alemán, italiano o portugués.</li>
  <li>Info pruebas: tocar ℹ️ al lado de cada grupo de tests explica qué mide y cómo interpretarlo.</li>
  <li>Iconos renovados para los modos preestablecidos (Potencia, Respuesta, Fluidez).</li>
  <li>Selector de idioma en Controles: desactivá "Usar idioma del sistema" para elegir otro.</li>
  <li>Gráfico radar más limpio y fácil de leer.</li>
</ul>
<p>R.E.A.C.T.O.R 1.0 — Primera versión estable.</p>
<ul>
  <li>Benchmarks manuales con stress-ng e hyperfine.</li>
  <li>Detección automática del mejor planificador para tu sistema.</li>
  <li>Monitor en vivo: CPU, memoria, temperatura, eventos sched_ext.</li>
  <li>Historial de resultados con gráficos de tendencia y tabla comparativa.</li>
  <li>Verificación de compatibilidad BPF para cada planificador.</li>
  <li>Terminal scxtop integrada para monitoreo avanzado.</li>
  <li>Radar comparativo con pesos ajustables (Potencia/Respuesta/Fluidez).</li>
</ul>""")
        )

        dialogo.add_credit_section(traducir("Desarrollo y Diseño"), [
            traducir("DinimixisDEMZ"),
        ])
        dialogo.add_credit_section(traducir("Infraestructura"), [
            traducir("Dark Anubis"),
        ])
        dialogo.add_acknowledgement_section(traducir("Beta Testers"), [
            traducir("Gekko"),
            traducir("MD1000 (Emedé)"),
            traducir("SunnyDeus"),
            traducir("JMX369"),
            traducir("Ezku"),
        ])
        dialogo.add_link(traducir("Página Web / GitHub"), "https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R")
        dialogo.add_legal_section(
            title=traducir("Licencia"),
            copyright=traducir("© 2026 El proyecto R.E.A.C.T.O.R y sus desarrolladores."),
            license_type=Gtk.License.GPL_3_0,
        )
        dialogo.add_credit_section(traducir("Proveedor"), [
            traducir("OpenCode (Anomaly)"),
        ])
        dialogo.add_credit_section(traducir("Modelos de IA"), [
            traducir("Big Pickle (GLM4.2)"),
            traducir("DeepSeek V4 Flash/Pro"),
        ])
        dialogo.present(self)

    def sincronizar_sistema(self):
        """Sincroniza el estado de la UI con el sistema real."""
        try:
            self.en_sincronizacion = True

            sc_name, sc_mode = self.scx.obtener_estado()
            sc_corto = sc_name.removeprefix("scx_") if sc_name else None

            lista_nombres = self.scx.obtener_lista()
            if sc_corto and sc_corto not in lista_nombres:
                lista_nombres.append(sc_corto)

            self.modelo_schedulers.splice(0, self.modelo_schedulers.get_n_items(), lista_nombres)

            self.boton_estado.remove_css_class("success")
            self.boton_estado.remove_css_class("destructive-action")
            if sc_name:
                self.boton_estado.set_label(f"{sc_name} [{sc_mode}]")
                self.boton_estado.add_css_class("success")
                self.active_sc = sc_name
                for i, nombre in enumerate(lista_nombres):
                    if nombre.lower() == sc_corto.lower():
                        self.combo_schedulers.set_selected(i)
                        break
                model_modos = self.combo_modos.get_model()
                for i in range(model_modos.get_n_items()):
                    if model_modos.get_string(i).lower() == sc_mode.lower():
                        self.combo_modos.set_selected(i)
                        break
            else:
                self.boton_estado.set_label(traducir("Planificador del Sistema"))
                self.boton_estado.add_css_class("destructive-action")
                self.active_sc = None
            self.en_sincronizacion = False
            actualizar_interfaz_ranking(self)
        except FileNotFoundError:
            self.boton_estado.set_label(traducir("scxctl no encontrado"))
            self.boton_estado.add_css_class("destructive-action")
            self.en_sincronizacion = False
        except (subprocess.SubprocessError, OSError, ValueError) as e:
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
