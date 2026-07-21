"""
Ventana principal de REACTOR.
Orquesta los módulos de UI, core y widgets.
"""

import threading
from collections.abc import Mapping

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.operations import OperationCancelled, coordinador_operaciones
from core.scx import ScxManager, ScxState
from core.thermal import SensorTermico
from ui.grafico import GraficoComparativo
from ui.controles import setup_controles_ui
from ui.rendimiento import setup_rendimiento_ui, actualizar_interfaz_ranking
from ui.automatizacion import setup_automatizacion_ui
from ui.disponibilidad import setup_disponibilidad_ui
from ui.diagnostico import setup_diagnostico_ui
from ui.historial import setup_historial_ui
from core.database import inicializar_db, obtener_versiones, detectar_cambio_version, cargar_compatibilidad
from widgets.password_dialog import DialogoPassword, backend_no_requiere_password


def _compatibles_desde_cache(cache):
    """Distingue una caché ausente de una verificada sin compatibles."""
    if not isinstance(cache, Mapping) or not cache:
        return None
    compatibles = []
    for nombre, datos in cache.items():
        if (
            not isinstance(nombre, str)
            or not isinstance(datos, (tuple, list))
            or len(datos) < 3
            or not isinstance(datos[0], bool)
        ):
            return None
        if datos[0]:
            compatibles.append(nombre)
    return compatibles


def _cargar_datos_iniciales():
    """Prepara DB y entorno sin crear ni tocar widgets GTK."""
    inicializar_db()
    versiones_raw = obtener_versiones()
    versiones = dict(versiones_raw) if isinstance(versiones_raw, Mapping) else {}

    kernel_actual = str(versiones.get("kernel") or "")
    cache_compat = cargar_compatibilidad(kernel_actual) if kernel_actual else {}
    compatibles = _compatibles_desde_cache(cache_compat)

    cambio_raw = detectar_cambio_version(versiones)
    if isinstance(cambio_raw, (tuple, list)) and len(cambio_raw) >= 2:
        cambio = cambio_raw[0] if isinstance(cambio_raw[0], bool) else False
        componentes_raw = cambio_raw[1]
    else:
        cambio = False
        componentes_raw = ()
    if isinstance(componentes_raw, str):
        componentes_raw = (componentes_raw,)
    try:
        componentes = tuple(
            componente
            for componente in componentes_raw or ()
            if isinstance(componente, str) and componente
        )
    except TypeError:
        componentes = ()
    if not cambio:
        componentes = ()

    return {
        "versiones": versiones,
        "compatibles": compatibles,
        "cambio_version": cambio,
        "componentes": componentes,
    }


def _etiqueta_estado_scx(estado):
    """Genera la etiqueta de estado exclusivamente desde ``ScxState``."""
    if not isinstance(estado, ScxState):
        raise TypeError("El estado SCX capturado no es un ScxState válido.")
    if estado.sistema_base:
        return "STOPPED (Sistema Base)"
    return f"RUNNING {estado.scheduler} in {estado.mode or 'auto'} mode"


def _filtrar_lista_controles(nombres, compatibles):
    if compatibles is None:
        return list(nombres)
    permitidos = set(compatibles)
    return [nombre for nombre in nombres if nombre in permitidos]


def _refrescar_listas_desde_snapshot(win, nombres):
    """Actualiza vistas con una lista existente, sin consultar ``scxctl``."""
    errores = []
    snapshot = list(nombres)
    try:
        from ui.disponibilidad import recargar_disponibilidad_ui

        recargar_disponibilidad_ui(win, snapshot)
    except Exception as exc:
        errores.append(("disponibilidad", str(exc) or exc.__class__.__name__))

    try:
        try:
            from ui.automatizacion import refrescar_auto_schedulers
        except ImportError:
            from ui.automatizacion import (
                _refrescar_auto_schedulers as refrescar_auto_schedulers,
            )

        refrescar_auto_schedulers(win, snapshot)
    except Exception as exc:
        errores.append(("automatización", str(exc) or exc.__class__.__name__))
    return errores


def _backend_ausente(error):
    """Distingue un backend inexistente de una sesión sudo sin autenticar."""
    normalized = (error or "").casefold()
    return any(
        marker in normalized
        for marker in (
            "no hay un backend",
            "no se encontró el backend",
            "backend privilegiado está deshabilitado",
        )
    )


class VentanaSimple(Adw.ApplicationWindow):
    """Ventana principal de la aplicación Reactor — REACTOR"""
    REACTOR = "Reactor de Experimentación Avanzada Concurrente Telúrico para Optimización de Rendimiento"

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("R.E.A.C.T.O.R")
        self.set_default_size(950, 850)
        self._ui_alive = True
        self._thermal_timer_id = None
        self._auth_check_en_progreso = False
        self._dialogo_password = None
        self._startup_started = False
        self._startup_in_progress = False
        self._startup_ready = False
        self._startup_generation = 0
        self._startup_thread = None
        self._startup_error = None
        self._css_instalado = False
        self.connect("close-request", self._al_cerrar)

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
        self.versiones = {}
        self._mode_generation = 0
        self._sync_generation = 0
        self._sync_retry_timer_id = None
        self._manual_generation = 0
        self._manual_development_mode = None
        self._scheduler_snapshot = ()

        # ── Core ──
        self.operaciones = coordinador_operaciones
        self.scx = ScxManager(modo_desarrollador=self.modo_desarrollador)
        self.sensor = SensorTermico()
        self.grafico = GraficoComparativo()

        # La raíz se presenta inmediatamente; las migraciones arrancan después.
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self._mostrar_carga_inicial()

    def _mostrar_carga_inicial(self):
        pagina = Adw.StatusPage(
            icon_name="system-run-symbolic",
            title="Preparando R.E.A.C.T.O.R",
            description="Inicializando la base de datos y detectando el entorno...",
        )
        pagina.set_child(Adw.Spinner())
        self.toast_overlay.set_child(pagina)

    def iniciar_inicializacion(self):
        """Arranca una sola inicialización tras presentar la ventana shell."""
        if (
            not self._ui_alive
            or self._startup_in_progress
            or self._startup_ready
        ):
            return False

        self._startup_started = True
        self._startup_in_progress = True
        self._startup_error = None
        self._startup_generation += 1
        generacion = self._startup_generation
        self._mostrar_carga_inicial()

        def _worker():
            try:
                datos = _cargar_datos_iniciales()
                error = None
            except Exception as exc:
                datos = None
                error = str(exc) or exc.__class__.__name__
            self.ejecutar_en_ui(
                self._finalizar_inicializacion,
                generacion,
                datos,
                error,
            )

        hilo = threading.Thread(
            target=_worker,
            name="reactor-startup",
            daemon=False,
        )
        self._startup_thread = hilo
        try:
            hilo.start()
        except Exception as exc:
            self._startup_in_progress = False
            self._mostrar_error_inicializacion(
                f"No se pudo iniciar la inicialización: {exc}"
            )
            return False
        return True

    def _finalizar_inicializacion(self, generacion, datos, error):
        if (
            not self._ui_alive
            or generacion != self._startup_generation
        ):
            return False
        self._startup_in_progress = False

        if error is not None or not isinstance(datos, Mapping):
            self._mostrar_error_inicializacion(
                error or "La inicialización no produjo datos válidos."
            )
            return False

        try:
            self._construir_interfaz_principal(datos)
        except Exception as exc:
            self._startup_ready = False
            self._mostrar_error_inicializacion(
                str(exc) or exc.__class__.__name__
            )
            return False
        return False

    def _mostrar_error_inicializacion(self, error):
        """Mantiene una ventana visible y reintentable ante fallos de DB."""
        self._startup_in_progress = False
        self._startup_ready = False
        self._startup_error = str(error)
        pagina = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="No se pudo iniciar R.E.A.C.T.O.R",
            description="La base de datos o el entorno no pudieron prepararse.",
        )
        caja = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=24,
            margin_end=24,
        )
        detalle = Gtk.Label(label=self._startup_error, wrap=True, selectable=True)
        detalle.add_css_class("dim-label")
        boton = Gtk.Button(
            label="Reintentar",
            halign=Gtk.Align.CENTER,
            css_classes=["suggested-action", "pill"],
        )
        boton.connect("clicked", lambda _button: self.iniciar_inicializacion())
        caja.append(detalle)
        caja.append(boton)
        pagina.set_child(caja)
        self.toast_overlay.set_child(pagina)

    def _construir_interfaz_principal(self, datos):
        """Construye las páginas en GTK a partir del snapshot del worker."""
        versiones = datos.get("versiones")
        self.versiones = dict(versiones) if isinstance(versiones, Mapping) else {}
        compatibles = datos.get("compatibles")
        self.compatibles = (
            list(compatibles)
            if isinstance(compatibles, (tuple, list))
            else None
        )

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
        setup_historial_ui(self)
        # Diagnóstico
        setup_diagnostico_ui(self)

        self._instalar_css()
        self.setup_sidebar()
        actualizar_interfaz_ranking(self)
        self._iniciar_monitor_termico()
        self._startup_ready = True
        self._startup_error = None
        self.sincronizar_sistema()

        if datos.get("cambio_version") and datos.get("componentes"):
            self.toast_overlay.add_toast(Adw.Toast.new(
                f"Entorno actualizado: {', '.join(datos['componentes'])}"
            ))

    def _instalar_css(self):
        if self._css_instalado:
            return
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
        self._css_instalado = True

    def _iniciar_monitor_termico(self):
        if self._thermal_timer_id is None:
            self._thermal_timer_id = GLib.timeout_add(
                2000,
                self.actualizar_sensor_termico,
            )

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
        self.agregar_opcion(self.lista_nav, "Automatización", "org.gnome.Settings-network-proxy-symbolic", self.pag_automatizacion)
        self.nav_disponibilidad = self.agregar_opcion(self.lista_nav, "Disponibilidad", "dialog-information-symbolic", self.pag_disponibilidad)
        self.agregar_opcion(self.lista_nav, "Diagnóstico", "sonar-symbolic", self.pag_diagnostico)
        self.agregar_opcion(self.lista_nav, "Historial", "document-open-recent-symbolic", self.pag_historial)

        if self.compatibles is None:
            self.nav_disponibilidad.add_css_class("pulse-warning")
        elif not self.compatibles:
            imagen = self.nav_disponibilidad.get_child().get_first_child()
            if isinstance(imagen, Gtk.Image):
                imagen.set_from_icon_name("dialog-error-symbolic")
                imagen.add_css_class("error")

        view_side = Adw.ToolbarView(content=self.lista_nav)
        view_side.add_top_bar(header_side)
        pag_sidebar.set_child(view_side)
        self.split_view.set_sidebar(pag_sidebar)
        self.split_view.set_content(self.pag_controles)

    def actualizar_sensor_termico(self):
        """Actualiza el indicador térmico en la sidebar."""
        if not self._ui_alive:
            return False
        if self.operaciones.is_busy:
            return True
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
            version="0.7.0",
            comments=f"{VentanaSimple.REACTOR}\nHerramienta de gestión y benchmarking para schedulers sched-ext (SCX).",
            developer_name="Equipo de Desarrollo R.E.A.C.T.O.R",
            developers=[
                "DinimixisDEMZ (Lead Developer/Lead Designer)",
                "opencode (AI Software Engineer - big-pickle)",
                "Antigravity (AI Assistant)",
                "Dark Anubis (IT Technical Specialist, System Administrator, DevOps & Network Tech.)",
                "MD1000[Emedé] (Beta Tester/Designer)",
                "SunnyDeus (Beta Tester)",
                "JMX369 (Beta Tester)",
                "Ezku (Beta Tester)",
                "Gekko (Designer)"
            ],
            support_url="https://github.com/DinimixisDEMZ/R.E.A.C.T.O.R/issues",
            application_icon='application-x-firmware',
            release_notes="""<p>Novedades en la versión 0.7.0:</p>
<ul>
  <li>Motor de Detección Automática: Análisis completo de 6 pruebas por scheduler con umbral térmico, sesiones restaurables, exclusión global y cancelación cooperativa; la recomendación requiere confirmación explícita.</li>
  <li>Métricas de stress-ng: response_kind identifica la métrica independiente usada para la respuesta agregada (context switch o mutex); la carga por throughput no fabrica un p95.</li>
  <li>Gráfico de Hardware Cairo: Radar chart animado con 6 ejes (CPUs, Threads, GHz, L3, L2, Cores), pulso animado y fallback a barras.</li>
  <li>Panel de Diagnóstico en Tiempo Real: Uso de CPU por core, métricas de planificación (ctxt/s, procesos bloqueados), especificaciones avanzadas de CPU (lscpu -J), mitigaciones de seguridad.</li>
  <li>Motor de Scoring con Media Armónica: Puntuación ponderada que penaliza valleys de rendimiento, con pesos configurables (Potencia/Respuesta/Fluidez).</li>
  <li>Persistencia SQLite reproducible: Historial de benchmarks con filtros por scheduler, tipo de prueba y rango de fechas, gráfico de tendencia interactivo.</li>
  <li>Monitoreo Térmico: Sensor discovery automático, calibración, alertas por niveles (estable/elevado/crítico) en sidebar.</li>
</ul>
<p>Novedades en la versión 0.6.4:</p>
<ul>
  <li>Segundo Motor de Benchmarking: Integración de hyperfine con 3 nuevas pruebas: Fork+Exec latency (100 runs), compilación paralela, interactividad bajo carga; p95 real calculado a partir de las muestras de Hyperfine.</li>
  <li>Pestaña de Disponibilidad: Verificación de compatibilidad BPF de cada scheduler con timeout, clasificación de resultados (Verificado/Residente/Error).</li>
  <li>Gráfico Radar Comparativo: Spider chart Cairo de 6 categorías con polígonos animados, hover interactivo, leyenda clickeable para ocultar schedulers.</li>
  <li>Pestaña de Historial: Tabla de resultados con filtros, gráfico de tendencia con hover tooltip, leyenda coloreada.</li>
  <li>Backends de privilegios: Soporte para sudo y run0, con autenticación delegada al backend disponible.</li>
  <li>Modo Desarrollador: Simulación completa de datos para testing de UI sin hardware.</li>
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
        dialogo.present(self)

    def ejecutar_en_ui(self, callback, *args):
        """Programa trabajo GTK solo mientras la ventana siga viva."""
        def _ejecutar():
            if not self._ui_alive:
                return False
            callback(*args)
            return False

        return GLib.idle_add(_ejecutar)

    def mostrar_toast(self, mensaje, *, alta=False):
        """Muestra un toast únicamente desde el hilo GTK."""
        if not self._ui_alive:
            return
        toast = Adw.Toast.new(str(mensaje))
        if alta:
            toast.set_priority(Adw.ToastPriority.HIGH)
        self.toast_overlay.add_toast(toast)

    def mostrar_operacion_ocupada(self):
        """Explica qué operación impide iniciar otra tarea larga."""
        state = self.operaciones.state
        nombre = state.name if state is not None else "otra operación"
        self.mostrar_toast(
            f"Operación ocupada: '{nombre}' sigue en curso.",
            alta=True,
        )

    def _programar_sincronizacion_actual(self):
        """Relanza un snapshot obsoleto cuando el coordinador quede libre."""
        if not self._ui_alive:
            return False
        if not self.operaciones.is_busy:
            self.sincronizar_sistema()
            return False
        if getattr(self, "_sync_retry_timer_id", None) is not None:
            return False

        timeout_add = getattr(GLib, "timeout_add", None)
        if not callable(timeout_add):
            return False

        def _reintentar():
            if not self._ui_alive:
                self._sync_retry_timer_id = None
                return False
            if self.en_sincronizacion:
                self._sync_retry_timer_id = None
                return False
            if self.operaciones.is_busy:
                return True
            self._sync_retry_timer_id = None
            self.sincronizar_sistema()
            return False

        self._sync_retry_timer_id = timeout_add(100, _reintentar)
        return False

    def sincronizar_sistema(self):
        """Consulta SCX fuera de GTK y aplica el resultado en el hilo principal."""
        if not self._ui_alive:
            return
        if self.en_sincronizacion:
            self.mostrar_toast("La actualización del estado ya está en curso.")
            return

        handle = self.operaciones.try_acquire("sincronización del sistema")
        if handle is None:
            self.mostrar_operacion_ocupada()
            return

        self._sync_generation = int(
            getattr(self, "_sync_generation", 0) or 0
        ) + 1
        generacion = self._sync_generation
        modo_desarrollador = bool(
            getattr(self, "modo_desarrollador", False)
        )
        self.en_sincronizacion = True

        def _consultar():
            try:
                handle.check_cancelled()
                lista_nombres = None
                error_lista = None
                estado = None
                error_estado = None
                try:
                    lista_nombres = tuple(
                        self.scx.obtener_lista(cancel_token=handle.token)
                    )
                    error_lista = self.scx.ultimo_error
                except OperationCancelled:
                    raise
                except Exception as exc:
                    error_lista = str(exc) or exc.__class__.__name__

                try:
                    handle.check_cancelled()
                    estado = self.scx.capturar_estado(
                        cancel_token=handle.token
                    )
                except OperationCancelled:
                    raise
                except Exception as exc:
                    error_estado = str(exc) or exc.__class__.__name__

                datos = {
                    "lista": lista_nombres,
                    "error_lista": error_lista,
                    "estado": estado,
                    "error_estado": error_estado,
                }
                error = None
            except OperationCancelled as exc:
                datos = None
                error = exc
            except Exception as exc:
                datos = None
                error = str(exc) or exc.__class__.__name__
            finally:
                handle.release()
                self.ejecutar_en_ui(
                    self._finalizar_sincronizacion,
                    datos,
                    error,
                    generacion,
                    modo_desarrollador,
                )

        try:
            threading.Thread(target=_consultar, daemon=True).start()
        except Exception as exc:
            handle.release()
            self.en_sincronizacion = False
            self.mostrar_toast(
                f"No se pudo iniciar la actualización: {exc}",
                alta=True,
            )

    def _finalizar_sincronizacion(
        self,
        datos,
        error,
        generacion=None,
        modo_desarrollador=None,
    ):
        """Aplica una instantánea SCX ya calculada sin bloquear GTK."""
        if generacion is not None and (
            generacion != getattr(self, "_sync_generation", None)
            or modo_desarrollador
            != bool(getattr(self, "modo_desarrollador", False))
        ):
            self.en_sincronizacion = False
            if getattr(self, "_ui_alive", True):
                relanzar = getattr(
                    self,
                    "_programar_sincronizacion_actual",
                    None,
                )
                if callable(relanzar):
                    relanzar()
                else:
                    self.sincronizar_sistema()
            return False

        actualizando_previo = getattr(
            self,
            "_actualizando_configuracion",
            False,
        )
        pendiente_previo = getattr(self, "_configuracion_pendiente", False)
        estado_aplicado = False
        seleccionables = ()
        self._actualizando_configuracion = True
        try:
            if error is not None or datos is None:
                self.boton_estado.set_label("Error al actualizar")
                self.boton_estado.remove_css_class("success")
                self.boton_estado.add_css_class("destructive-action")
                detalle = error or "No se recibió una instantánea SCX."
                self.mostrar_toast(
                    f"Error al actualizar SCX: {detalle}",
                    alta=True,
                )
                return

            if datos["error_lista"]:
                self.mostrar_toast(
                    f"No se actualizó la lista de planificadores: "
                    f"{datos['error_lista']}",
                    alta=True,
                )
            else:
                lista_nombres = list(datos["lista"] or ())
                self._scheduler_snapshot = tuple(lista_nombres)
                for vista, detalle in _refrescar_listas_desde_snapshot(
                    self,
                    lista_nombres,
                ):
                    self.mostrar_toast(
                        f"No se refrescó {vista}: {detalle}",
                        alta=True,
                    )

                lista_controles = _filtrar_lista_controles(
                    lista_nombres,
                    getattr(self, "compatibles", None),
                )
                seleccionables = tuple(lista_controles)
                self.modelo_schedulers.splice(
                    0,
                    self.modelo_schedulers.get_n_items(),
                    lista_controles,
                )

            if datos["error_estado"]:
                self.boton_estado.set_label("Error al actualizar estado")
                self.boton_estado.remove_css_class("success")
                self.boton_estado.add_css_class("destructive-action")
                self.mostrar_toast(
                    f"No se actualizó el estado SCX: {datos['error_estado']}",
                    alta=True,
                )
                return

            estado = datos["estado"]
            self.boton_estado.set_label(_etiqueta_estado_scx(estado))
            self.boton_estado.remove_css_class("success")
            self.boton_estado.remove_css_class("destructive-action")

            self.active_sc = estado.scheduler
            if self.active_sc is None:
                self.boton_estado.add_css_class("destructive-action")
            else:
                self.boton_estado.add_css_class("success")
                for index, nombre in enumerate(seleccionables):
                    if nombre.casefold() == self.active_sc.casefold():
                        self.combo_schedulers.set_selected(index)
                        break

                modo_activo = estado.mode or "auto"
                model_modos = self.combo_modos.get_model()
                for index in range(model_modos.get_n_items()):
                    if model_modos.get_string(index).casefold() == modo_activo.casefold():
                        self.combo_modos.set_selected(index)
                        break

            estado_aplicado = True
            actualizar_interfaz_ranking(self)
        except Exception as exc:
            self.mostrar_toast(
                f"Error aplicando el estado SCX: {exc}",
                alta=True,
            )
        finally:
            self._actualizando_configuracion = actualizando_previo
            if estado_aplicado:
                self._configuracion_pendiente = False
                boton_aplicar = getattr(
                    self,
                    "btn_aplicar_configuracion",
                    None,
                )
                if boton_aplicar is not None:
                    boton_aplicar.set_sensitive(False)
            else:
                self._configuracion_pendiente = pendiente_previo
            self.en_sincronizacion = False

    def solicitar_sudo_si_necesario(self, callback):
        """Prepara privilegios sin ejecutar validaciones en el hilo GTK."""
        if not self._ui_alive:
            return
        if self.modo_desarrollador:
            callback()
            return

        backend = self.scx.backend_privilegiado
        if backend_no_requiere_password(backend):
            callback()
            return

        if self._dialogo_password is not None:
            self._dialogo_password.present()
            return
        if self._auth_check_en_progreso:
            self.mostrar_toast("La comprobación de permisos ya está en curso.")
            return

        self._auth_check_en_progreso = True

        def _comprobar_backend():
            disponible = False
            error = None
            try:
                disponible = self.scx.sudo_disponible()
                error = self.scx.ultimo_error
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
            self.ejecutar_en_ui(
                self._resolver_backend_privilegiado,
                callback,
                disponible,
                error,
            )

        try:
            threading.Thread(target=_comprobar_backend, daemon=True).start()
        except Exception as exc:
            self._auth_check_en_progreso = False
            self.mostrar_toast(
                f"No se pudo comprobar la autenticación: {exc}",
                alta=True,
            )

    def _resolver_backend_privilegiado(self, callback, disponible, error):
        self._auth_check_en_progreso = False
        backend = self.scx.backend_privilegiado
        if disponible or backend_no_requiere_password(backend):
            callback()
            return

        if _backend_ausente(error):
            self.mostrar_toast(
                error or "No hay un backend privilegiado disponible.",
                alta=True,
            )
            return

        def _validar(password, completar):
            password_pendiente = password

            def _worker():
                nonlocal password_pendiente
                valido = False
                detalle = None
                try:
                    valido = self.scx.validar_sudo(password_pendiente)
                    detalle = self.scx.ultimo_error
                except Exception as exc:
                    detalle = str(exc) or exc.__class__.__name__
                finally:
                    password_pendiente = None

                self.ejecutar_en_ui(
                    self._resolver_validacion_sudo,
                    dialog,
                    completar,
                    callback,
                    valido,
                    detalle,
                )

            try:
                threading.Thread(target=_worker, daemon=True).start()
            except Exception as exc:
                password_pendiente = None
                completar(False, f"No se pudo validar sudo: {exc}")

        dialog = DialogoPassword(self, _validar)
        self._dialogo_password = dialog

        def _olvidar_dialogo(*_args):
            if self._dialogo_password is dialog:
                self._dialogo_password = None
            return False

        dialog.connect("close-request", _olvidar_dialogo)
        dialog.present()

    def _resolver_validacion_sudo(
        self,
        dialog,
        completar,
        callback,
        valido,
        error,
    ):
        if not valido:
            completar(False, error or "Contraseña incorrecta o fallo en sudo.")
            return
        if dialog.cancelado or not dialog.aceptar_exito():
            return

        if self._dialogo_password is dialog:
            self._dialogo_password = None
        try:
            callback()
        finally:
            completar(True)

    def _al_cerrar(self, *_args):
        """Invalida callbacks y detiene fuentes UI propias al cerrar."""
        operaciones = getattr(self, "operaciones", None)
        cancelar = getattr(operaciones, "cancel_current", None)
        if callable(cancelar):
            cancelar()
        self._ui_alive = False
        self.en_sincronizacion = False
        self.en_proceso_bench = False
        self.en_proceso_auto = False
        self._auth_check_en_progreso = False
        self._startup_in_progress = False
        self._startup_generation = int(
            getattr(self, "_startup_generation", 0) or 0
        ) + 1
        self._sync_generation = int(
            getattr(self, "_sync_generation", 0) or 0
        ) + 1
        self._manual_generation = int(
            getattr(self, "_manual_generation", 0) or 0
        ) + 1

        if getattr(self, "_thermal_timer_id", None) is not None:
            try:
                GLib.source_remove(self._thermal_timer_id)
            except (AttributeError, TypeError):
                pass
            self._thermal_timer_id = None

        if getattr(self, "_sync_retry_timer_id", None) is not None:
            try:
                GLib.source_remove(self._sync_retry_timer_id)
            except (AttributeError, TypeError):
                pass
            self._sync_retry_timer_id = None

        if getattr(self, "_dialogo_password", None) is not None:
            self._dialogo_password.close()
            self._dialogo_password = None

        diagnostico_cleanup = getattr(self, "_diagnostico_cleanup", None)
        if callable(diagnostico_cleanup):
            diagnostico_cleanup()

        detener_pulso = getattr(
            getattr(self, "grafico", None),
            "detener_pulso",
            None,
        )
        if callable(detener_pulso):
            detener_pulso()
        return False

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
