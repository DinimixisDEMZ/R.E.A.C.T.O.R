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

import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.scx import GestorScx
from core.thermal import SensorTermico
from ui.grafico import GraficoComparativo
from ui.controles import configurar_ui_controles
from ui.rendimiento import configurar_ui_rendimiento, actualizar_interfaz_ranking
from ui.automatizacion import configurar_ui_automatizacion
from ui.disponibilidad import configurar_ui_disponibilidad
from ui.diagnostico import configurar_ui_diagnostico
from ui.historial import configurar_ui_historial
from core.constantes import VERSION
from utils.iconos import TEMPERATURA_CRITICA
from core.estado import EstadoPrueba, EstadoMonitor, EstadoDeteccionAuto
from core.database import inicializar_db, obtener_versiones, detectar_cambio_version, cargar_compatibilidad
from utils.i18n import traducir
from utils.logging import mostrar_toast, registrar_error
from core.sched_ext import nombre_corto
from core.verificacion import verificar_si_primera_vez, marcar_verificacion_hecha
from ui.verificacion import mostrar_verificacion


class VentanaSimple(Adw.ApplicationWindow):
    """Ventana principal de la aplicación Reactor — REACTOR"""
    REACTOR = "Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento"

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(traducir("R.E.A.C.T.O.R"))
        # El tamaño por defecto debe caber en el parque real de portátiles: una
        # ventana de 950x850 no entra en una pantalla de 1366x768, que sigue
        # siendo la más común entre los equipos viejos. Se pide un tamaño
        # cómodo pero se declara un mínimo que funcione en 1024x600.
        self.set_default_size(920, 720)
        self.set_size_request(360, 400)

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
        self.advertencias_arranque = []

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

        # La barra de advertencias vive por encima del contenido: informa sin
        # robar el foco ni impedir usar la aplicación.
        self.banner_avisos = Adw.Banner(revealed=False, button_label=traducir("Ver detalles"))
        self.banner_avisos.connect("button-clicked", lambda *_: mostrar_verificacion(self))

        self.split_view = Adw.NavigationSplitView()
        self.split_view.set_min_sidebar_width(180)
        self.split_view.set_max_sidebar_width(260)

        caja_raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja_raiz.append(self.banner_avisos)
        caja_raiz.append(self.split_view)

        # En pantallas angostas la barra lateral se pliega sobre el contenido en
        # vez de comprimirlo. Sin este punto de ruptura la ventana era
        # inutilizable por debajo de unos 700 px de ancho.
        contenedor = Adw.BreakpointBin()
        contenedor.set_size_request(360, 400)
        contenedor.set_child(caja_raiz)
        punto = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 700px"))
        punto.add_setter(self.split_view, "collapsed", True)
        contenedor.add_breakpoint(punto)

        self.toast_overlay.set_child(contenedor)

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
        self.pag_diagnostico = configurar_ui_diagnostico(self)


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

    def mostrar_advertencias_arranque(self, advertencias):
        """Muestra en la barra superior las limitaciones detectadas al arrancar."""
        self.advertencias_arranque = list(advertencias or [])
        if not self.advertencias_arranque:
            return
        cantidad = len(self.advertencias_arranque)
        self.banner_avisos.set_title(
            traducir("La aplicación funciona con limitaciones ({} avisos)").format(cantidad)
            if cantidad > 1 else self.advertencias_arranque[0])
        self.banner_avisos.set_revealed(True)

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
        """Actualiza el indicador térmico de la barra lateral.

        Se informa el margen respecto al límite que declara el propio hardware,
        no una temperatura contra un umbral fijo: 72 °C es holgado en un CPU con
        Tjmax de 100 °C y preocupante en uno de 85 °C. Con umbrales fijos la
        aplicación anunciaba limitación térmica en equipos que estaban bien.
        """
        try:
            nivel = self.sensor.nivel()
            temperatura = self.sensor.obtener_temp()
        except Exception as e:
            registrar_error("actualizar_sensor_termico", e)
            nivel, temperatura = "desconocido", 0.0

        for clase in ("error", "warning", "dim-label"):
            self.img_termica.remove_css_class(clase)

        if nivel == "desconocido":
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.img_termica.add_css_class("dim-label")
            self.btn_termica.set_tooltip_text(traducir("Sin sensor térmico disponible"))
            self.lbl_termica_detail.set_label(traducir(
                "Este equipo no expone la temperatura del CPU. Las pruebas se "
                "ejecutan igual, pero no se puede descartar el efecto del calor."))
            return True

        limite = self.sensor.tjmax()
        porcentaje = int(round(self.sensor.margen() * 100))
        detalle = f"{temperatura:.1f} °C / {limite:.0f} °C ({porcentaje}%)"

        if nivel == "estable":
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.img_termica.add_css_class("dim-label")
            self.btn_termica.set_tooltip_text(f"{traducir('Sistema estable')} — {detalle}")
            self.lbl_termica_detail.set_label(f"{traducir('Estado: Estable')}\n{detalle}")
        elif nivel == "elevado":
            self.img_termica.set_from_icon_name("temperature-symbolic")
            self.img_termica.add_css_class("warning")
            self.btn_termica.set_tooltip_text(f"{traducir('Temperatura elevada')} — {detalle}")
            self.lbl_termica_detail.set_label(f"{traducir('Estado: Elevado')}\n{detalle}")
        else:
            self.img_termica.set_from_icon_name(TEMPERATURA_CRITICA)
            self.img_termica.add_css_class("error")
            self.btn_termica.set_tooltip_text(f"{traducir('Cerca del límite térmico')} — {detalle}")
            self.lbl_termica_detail.set_label(f"{traducir('Estado: CRÍTICO')}\n{detalle}")
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
            release_notes_version=VERSION,
            release_notes=traducir("""<p>R.E.A.C.T.O.R 1.0 — Refactorización y AppImage.</p>
<ul>
<li>Refactorización completa del código</li>
<li>Aplanado de historial git</li>
<li>57 tests automatizados</li>
<li>AppImage autónomo con hyperfine, cyclictest</li>
<li>Verificación del sistema con soporte run0</li>
<li>i18n: español, inglés, francés, alemán, italiano, portugués</li>
<li>Scoring: media armónica con pesos ajustables</li>
</ul>"""),
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

    def _sincronizar_modelo(self, sc_name):
        """Rellena el combo de planificadores.

        El modelo muestra nombres cortos (`lavd`) porque es lo legible, pero
        todo lo demás de la aplicación trabaja con el nombre canónico
        (`scx_lavd`). La conversión se hace acá y sólo acá: mezclar las dos
        formas hacía que el combo nunca se sincronizara con el scheduler activo
        y que se duplicara la entrada del que estaba corriendo.
        """
        canonicos = self.scx.obtener_lista(self.compatibles)
        if sc_name and sc_name not in canonicos:
            canonicos.append(sc_name)
        canonicos.sort()
        self._schedulers_canonicos = canonicos
        lista_nombres = [nombre_corto(n) for n in canonicos]
        self.modelo_schedulers.splice(
            0, self.modelo_schedulers.get_n_items(), lista_nombres)
        return canonicos

    def _sincronizar_modos(self, sc_name, sc_mode):
        """Rellena el combo de modos con los que admite el backend activo.

        Los modos no son universales: pertenecen a `scx_loader` y dependen del
        scheduler. Sin loader sólo existe el comportamiento por defecto del
        binario, así que ofrecer cinco modos sería mentir.
        """
        referencia = sc_name or self.scheduler_seleccionado() or ""
        modos = self.scx.modos_disponibles(referencia) if referencia else ["auto"]
        modelo = self.combo_modos.get_model()
        actuales = [modelo.get_string(i) for i in range(modelo.get_n_items())]
        if actuales != modos:
            modelo.splice(0, modelo.get_n_items(), modos)
        self.combo_modos.set_sensitive(len(modos) > 1)
        if sc_mode and sc_mode in modos:
            self.combo_modos.set_selected(modos.index(sc_mode))
        return modos

    def _actualizar_ui_scheduler(self, sc_name, sc_mode, canonicos):
        self.boton_estado.remove_css_class("success")
        self.boton_estado.remove_css_class("destructive-action")
        if sc_name:
            etiqueta = nombre_corto(sc_name)
            self.boton_estado.set_label(
                f"{etiqueta} [{sc_mode}]" if sc_mode else etiqueta)
            self.boton_estado.add_css_class("success")
            self.active_sc = sc_name
            if sc_name in canonicos:
                self.combo_schedulers.set_selected(canonicos.index(sc_name))
        else:
            self.boton_estado.set_label(traducir("Planificador del Sistema"))
            self.boton_estado.add_css_class("destructive-action")
            self.active_sc = None

    def scheduler_seleccionado(self):
        """Nombre canónico del planificador elegido en el combo, o None."""
        indice = self.combo_schedulers.get_selected()
        canonicos = getattr(self, "_schedulers_canonicos", [])
        if 0 <= indice < len(canonicos):
            return canonicos[indice]
        return None

    def sincronizar_sistema(self):
        """Refleja en la interfaz el estado real del planificador del sistema.

        No cambia nada del sistema: sólo lee. El `finally` es deliberado —
        una excepción a mitad de camino dejaba `en_sincronizacion` en True para
        siempre, y con ese cerrojo puesto la interfaz ignoraba en silencio todos
        los cambios posteriores del usuario.
        """
        try:
            self.en_sincronizacion = True
            sc_name, sc_mode = self.scx.obtener_estado()
            canonicos = self._sincronizar_modelo(sc_name)
            self._sincronizar_modos(sc_name, sc_mode)
            self._actualizar_ui_scheduler(sc_name, sc_mode, canonicos)
        except Exception as e:
            registrar_error("sincronizar_sistema", e)
            self.boton_estado.set_label(traducir("Estado no disponible"))
            self.boton_estado.add_css_class("destructive-action")
        finally:
            self.en_sincronizacion = False
        actualizar_interfaz_ranking(self)

    def ejecutar_accion_privilegiada(self, callback):
        """Ejecuta una acción que requiere privilegios.

        La aplicación ya no pide la contraseña: `scx_loader` está protegido por
        polkit y `pkexec`/`run0` también, así que la autenticación la muestra el
        agente del escritorio en el momento exacto de la operación. Pedirla
        antes, en un diálogo propio, no era una ruta de confianza y obligaba a
        manipular la contraseña dentro del proceso.

        Si el equipo no tiene ningún backend de control, se avisa acá en vez de
        dejar que la acción falle en silencio más adelante.
        """
        if self.scx.backend is None:
            mostrar_toast(
                self,
                traducir("Este equipo no permite controlar planificadores. "
                         "Revisá la verificación del sistema para ver por qué."),
                prefijo=traducir("No disponible"))
            return
        callback()

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
