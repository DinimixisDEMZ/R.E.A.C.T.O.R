"""
Control de planificadores sched_ext.

La forma de gestionar schedulers en Linux cambió: `scx_loader` es hoy un
servicio D-Bus del sistema autorizado por **polkit**, y `scxctl` es apenas un
cliente de ese servicio que además viaja en un paquete distinto al de los
schedulers (`scx-tools` frente a `scx-scheds`). Dar por sentado que `scxctl`
existe deja la aplicación inservible en equipos que tienen el kernel preparado
y los schedulers instalados.

Por eso el control se resuelve con **backends intercambiables**, elegidos según
lo que el equipo ofrezca:

  1. `BackendDbus`     — habla directo con `org.scx.Loader`. Es el camino
                         preferido: polkit muestra el diálogo del escritorio y
                         la aplicación nunca ve la contraseña.
  2. `BackendScxctl`   — usa el binario `scxctl` contra el mismo servicio.
  3. `BackendDirecto`  — ejecuta el binario del scheduler como unidad transitoria
                         de systemd. Es el único camino cuando no hay loader.
  4. `BackendSimulado` — modo de simulación para probar la interfaz.

Ninguno pide la contraseña dentro de la aplicación. El diálogo propio que
canalizaba la contraseña a `sudo -S` se eliminó: no era una ruta de confianza,
`del pwd` no borraba nada, y bloqueaba la interfaz mientras `sudo` respondía.
"""

import shutil
import subprocess

from gi.repository import Gio, GLib

from core import sched_ext
from core.sched_ext import canonizar

# Modos del loader. El orden importa: el valor entero es el que viaja por D-Bus
# y no coincide con el orden alfabético ni con el que mostraba la interfaz vieja.
MODOS = (
    ("auto", 0),
    ("gaming", 1),
    ("powersave", 2),
    ("lowlatency", 3),
    ("server", 4),
)
MODOS_POR_NOMBRE = {nombre: valor for nombre, valor in MODOS}
MODOS_POR_VALOR = {valor: nombre for nombre, valor in MODOS}

NOMBRE_BUS = "org.scx.Loader"
RUTA_BUS = "/org/scx/Loader"
INTERFAZ_BUS = "org.scx.Loader"

# Un cambio de scheduler puede quedar esperando a que el usuario escriba la
# contraseña en el diálogo de polkit: el tiempo de espera debe ser generoso.
TIEMPO_PRIVILEGIADO_MS = 120_000
TIEMPO_CONSULTA_MS = 3_000

# Prefijo de las unidades transitorias que crea el backend directo. Permite
# encontrarlas y detenerlas aunque la aplicación se haya cerrado por el medio.
UNIDAD_TRANSITORIA = "reactor-scx"


class Resultado:
    """Resultado uniforme de una operación de control.

    `codigo` permite que la interfaz distinga entre causas de fallo que exigen
    mensajes muy distintos: no es lo mismo "el usuario canceló el diálogo de
    autenticación" que "el kernel no soporta sched_ext".
    """

    __slots__ = ("ok", "mensaje", "detalle", "codigo")

    def __init__(self, ok, mensaje="", detalle="", codigo="ok"):
        self.ok = ok
        self.mensaje = mensaje
        self.detalle = detalle
        self.codigo = codigo if not ok or codigo != "ok" else "ok"

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"Resultado(ok={self.ok}, codigo={self.codigo!r}, mensaje={self.mensaje!r})"


def _clasificar_error_dbus(excepcion):
    """Traduce un error de D-Bus a un código estable de la aplicación."""
    texto = str(excepcion)
    if "ServiceUnknown" in texto or "was not provided by any .service" in texto:
        return "sin_servicio"
    if "NotAuthorized" in texto or "AccessDenied" in texto or "not authorized" in texto.lower():
        return "no_autorizado"
    if "Dismissed" in texto or "cancelled" in texto.lower() or "canceled" in texto.lower():
        return "cancelado"
    if "Timeout" in texto or "TimedOut" in texto:
        return "tiempo_agotado"
    return "fallo"


# ─────────────────────────── backends ───────────────────────────


class BackendBase:
    """Contrato común de los backends de control."""

    nombre = "base"
    #: Operaciones que el backend sabe hacer. La interfaz las usa para
    #: deshabilitar controles en vez de dejar que fallen al pulsarlos.
    capacidades = frozenset()

    def disponible(self):
        return False

    def lista(self):
        """Schedulers que este backend puede arrancar (nombres canónicos)."""
        return []

    def modos(self, scheduler):
        """Modos con configuración propia para ese scheduler."""
        return [nombre for nombre, _ in MODOS]

    def estado(self):
        """Devuelve (scheduler_canonico|None, modo|None)."""
        return None, None

    def aplicar(self, scheduler, modo="auto"):
        return Resultado(False, "Operación no soportada", codigo="no_soportado")

    def detener(self):
        return Resultado(False, "Operación no soportada", codigo="no_soportado")

    def reiniciar(self):
        return Resultado(False, "Operación no soportada", codigo="no_soportado")

    def restaurar_predeterminado(self):
        return Resultado(False, "Operación no soportada", codigo="no_soportado")


class BackendDbus(BackendBase):
    """Control vía el servicio D-Bus `org.scx.Loader`, autorizado por polkit."""

    nombre = "dbus"
    capacidades = frozenset({"aplicar", "detener", "reiniciar", "restaurar",
                             "modos", "argumentos"})

    def __init__(self):
        self._proxy = None
        self._fallo_conexion = False

    # ── conexión ──

    def _obtener_proxy(self):
        if self._proxy is not None or self._fallo_conexion:
            return self._proxy
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM,
                # Sin caché de propiedades: el scheduler puede cambiarlo otro
                # cliente (scxctl en una terminal) y hay que verlo.
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
                | Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS,
                None, NOMBRE_BUS, RUTA_BUS, INTERFAZ_BUS, None,
            )
        except GLib.Error:
            self._fallo_conexion = True
            self._proxy = None
        return self._proxy

    @staticmethod
    def estado_del_servicio():
        """Tri-estado del loader: 'activo', 'activable' o 'ausente'.

        Distinguirlos importa: 'ausente' significa que falta el paquete y hay
        que sugerir instalarlo; 'activable' significa que D-Bus lo levantará
        solo en la primera llamada y no hay nada que hacer.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return "ausente"
        try:
            respuesta = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", (NOMBRE_BUS,)), GLib.VariantType("(b)"),
                Gio.DBusCallFlags.NONE, TIEMPO_CONSULTA_MS, None)
            if respuesta.unpack()[0]:
                return "activo"
            respuesta = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "ListActivatableNames",
                None, GLib.VariantType("(as)"),
                Gio.DBusCallFlags.NONE, TIEMPO_CONSULTA_MS, None)
            if NOMBRE_BUS in respuesta.unpack()[0]:
                return "activable"
        except GLib.Error:
            pass
        return "ausente"

    def disponible(self):
        return self.estado_del_servicio() in ("activo", "activable")

    # ── lectura ──

    def _propiedad(self, nombre, defecto=None):
        proxy = self._obtener_proxy()
        if proxy is None:
            return defecto
        try:
            respuesta = proxy.call_sync(
                "org.freedesktop.DBus.Properties.Get",
                GLib.Variant("(ss)", (INTERFAZ_BUS, nombre)),
                Gio.DBusCallFlags.NONE, TIEMPO_CONSULTA_MS, None)
            return respuesta.unpack()[0]
        except GLib.Error:
            return defecto

    def lista(self):
        soportados = self._propiedad("SupportedSchedulers", []) or []
        return [canonizar(n) for n in soportados if n]

    def modos(self, scheduler):
        """Modos realmente configurados para ese scheduler.

        El loader expone `SchedulerModes`, que devuelve sólo los modos que
        resuelven a una lista de argumentos no vacía. `auto` siempre está.
        """
        proxy = self._obtener_proxy()
        if proxy is None:
            return [nombre for nombre, _ in MODOS]
        try:
            respuesta = proxy.call_sync(
                "SchedulerModes", GLib.Variant("(s)", (canonizar(scheduler),)),
                Gio.DBusCallFlags.NONE, TIEMPO_CONSULTA_MS, None)
            valores = respuesta.unpack()[0]
            nombres = [MODOS_POR_VALOR[v] for v in valores if v in MODOS_POR_VALOR]
            return nombres or ["auto"]
        except GLib.Error:
            return [nombre for nombre, _ in MODOS]

    def estado(self):
        actual = self._propiedad("CurrentScheduler")
        if not actual or actual == "unknown":
            return None, None
        modo = MODOS_POR_VALOR.get(self._propiedad("SchedulerMode", 0) or 0, "auto")
        return canonizar(actual), modo

    # ── escritura ──

    def _llamar_privilegiado(self, metodo, parametros=None):
        proxy = self._obtener_proxy()
        if proxy is None:
            return Resultado(False, "El servicio scx_loader no está disponible",
                             codigo="sin_servicio")
        try:
            proxy.call_sync(metodo, parametros, Gio.DBusCallFlags.NONE,
                            TIEMPO_PRIVILEGIADO_MS, None)
            return Resultado(True)
        except GLib.Error as error:
            return Resultado(False, error.message, codigo=_clasificar_error_dbus(error))

    def aplicar(self, scheduler, modo="auto"):
        scheduler = canonizar(scheduler)
        valor_modo = MODOS_POR_NOMBRE.get(modo, 0)
        # Si ya hay un scheduler corriendo hay que conmutar, no arrancar: el
        # loader rechaza StartScheduler con uno activo.
        activo, _ = self.estado()
        metodo = "SwitchScheduler" if activo else "StartScheduler"
        return self._llamar_privilegiado(
            metodo, GLib.Variant("(su)", (scheduler, valor_modo)))

    def detener(self):
        return self._llamar_privilegiado("StopScheduler", None)

    def reiniciar(self):
        return self._llamar_privilegiado("RestartScheduler", None)

    def restaurar_predeterminado(self):
        return self._llamar_privilegiado("RestoreDefault", None)


class BackendScxctl(BackendBase):
    """Control vía el binario `scxctl`, que a su vez habla con el loader."""

    nombre = "scxctl"
    capacidades = frozenset({"aplicar", "detener", "reiniciar", "restaurar", "modos"})

    def __init__(self, ruta=None):
        self.ruta = ruta or shutil.which("scxctl")

    def disponible(self):
        return bool(self.ruta) and BackendDbus.estado_del_servicio() != "ausente"

    def _ejecutar(self, argumentos, tiempo=30):
        if not self.ruta:
            return Resultado(False, "scxctl no está instalado", codigo="sin_servicio")
        try:
            proceso = subprocess.run([self.ruta] + argumentos, capture_output=True,
                                     text=True, timeout=tiempo)
        except subprocess.TimeoutExpired:
            return Resultado(False, f"scxctl no respondió en {tiempo}s",
                             codigo="tiempo_agotado")
        except (OSError, subprocess.SubprocessError) as error:
            return Resultado(False, str(error), codigo="fallo")
        salida = (proceso.stdout or "").strip()
        error = (proceso.stderr or "").strip()
        if proceso.returncode != 0:
            texto = (error or salida).lower()
            codigo = "fallo"
            if "not authorized" in texto or "denied" in texto:
                codigo = "no_autorizado"
            elif "serviceunknown" in texto or "not provided by any" in texto:
                codigo = "sin_servicio"
            return Resultado(False, error or salida or "scxctl falló", codigo=codigo)
        return Resultado(True, salida)

    def lista(self):
        resultado = self._ejecutar(["list"], tiempo=10)
        if not resultado.ok:
            return []
        # La salida es "supported schedulers: [\"scx_a\", \"scx_b\"]".
        texto = resultado.mensaje
        if "[" not in texto:
            return []
        interior = texto[texto.index("[") + 1: texto.rindex("]") if "]" in texto else None]
        nombres = [t.strip().strip('"\'') for t in interior.split(",")]
        return [canonizar(n) for n in nombres if n]

    def modos(self, scheduler):
        resultado = self._ejecutar(["modes", "-s", canonizar(scheduler)], tiempo=10)
        if not resultado.ok:
            return [nombre for nombre, _ in MODOS]
        encontrados = [n for n, _ in MODOS if n in resultado.mensaje.lower()]
        return encontrados or ["auto"]

    def estado(self):
        # El kernel es más fiable que el texto de scxctl para saber qué corre.
        info = sched_ext.estado()
        if info["scheduler"]:
            resultado = self._ejecutar(["get"], tiempo=10)
            modo = "auto"
            if resultado.ok:
                for nombre, _ in MODOS:
                    if nombre in resultado.mensaje.lower():
                        modo = nombre
                        break
            return info["scheduler"], modo
        return None, None

    def aplicar(self, scheduler, modo="auto"):
        activo, _ = self.estado()
        accion = "switch" if activo else "start"
        return self._ejecutar([accion, "-s", canonizar(scheduler), "-m", modo],
                              tiempo=TIEMPO_PRIVILEGIADO_MS // 1000)

    def detener(self):
        return self._ejecutar(["stop"])

    def reiniciar(self):
        return self._ejecutar(["restart"])

    def restaurar_predeterminado(self):
        return self._ejecutar(["restore"])


class BackendDirecto(BackendBase):
    """Ejecuta el binario del scheduler como unidad transitoria de systemd.

    Es el camino cuando no hay `scx_loader`, que es el caso en las distribuciones
    que empaquetan los schedulers por separado del loader.

    Se usa `systemd-run` en vez de lanzar el proceso a mano por tres motivos:
    el ciclo de vida queda a cargo de systemd aunque la aplicación muera, la
    parada se hace con `SIGINT` (que es como un scheduler scx se descarga
    limpiamente, no con `SIGKILL`), y se puede consultar y detener la unidad por
    nombre sin rastrear PIDs ajenos.
    """

    nombre = "directo"
    capacidades = frozenset({"aplicar", "detener", "argumentos"})

    def __init__(self):
        self._instalados = sched_ext.schedulers_instalados()
        self._elevador = self._detectar_elevador()

    @staticmethod
    def _detectar_elevador():
        """Elige cómo elevar privilegios, en orden de preferencia.

        `pkexec` y `run0` usan polkit y muestran el diálogo del escritorio.
        `sudo` sólo se usa si ya hay una sesión activa: nunca se le pide la
        contraseña al usuario dentro de la aplicación.
        """
        for herramienta in ("pkexec", "run0"):
            ruta = shutil.which(herramienta)
            if ruta:
                return [ruta]
        ruta = shutil.which("sudo")
        if ruta:
            try:
                if subprocess.run([ruta, "-n", "true"], capture_output=True,
                                  timeout=5).returncode == 0:
                    return [ruta, "-n"]
            except (OSError, subprocess.SubprocessError):
                pass
        return None

    def disponible(self):
        return bool(self._instalados) and bool(self._elevador) and bool(
            shutil.which("systemd-run"))

    def refrescar(self):
        self._instalados = sched_ext.schedulers_instalados()

    def lista(self):
        return list(self._instalados.keys())

    def modos(self, scheduler):
        # Los modos son un concepto del loader: sin loader sólo existe el
        # comportamiento por defecto del binario.
        return ["auto"]

    def estado(self):
        info = sched_ext.estado()
        return info["scheduler"], ("auto" if info["scheduler"] else None)

    def _ejecutar_elevado(self, argumentos, tiempo=TIEMPO_PRIVILEGIADO_MS // 1000):
        if not self._elevador:
            return Resultado(
                False,
                "No hay forma de obtener privilegios (falta pkexec, run0 o una sesión sudo activa)",
                codigo="sin_elevacion")
        try:
            proceso = subprocess.run(self._elevador + argumentos, capture_output=True,
                                     text=True, timeout=tiempo)
        except subprocess.TimeoutExpired:
            return Resultado(False, "La operación excedió el tiempo de espera",
                             codigo="tiempo_agotado")
        except (OSError, subprocess.SubprocessError) as error:
            return Resultado(False, str(error), codigo="fallo")
        if proceso.returncode != 0:
            error = (proceso.stderr or proceso.stdout or "").strip()
            # pkexec devuelve 126 cuando el usuario cierra el diálogo y 127 si
            # no se pudo autorizar.
            codigo = "cancelado" if proceso.returncode == 126 else (
                "no_autorizado" if proceso.returncode == 127 else "fallo")
            return Resultado(False, error or "La operación falló", codigo=codigo)
        return Resultado(True, (proceso.stdout or "").strip())

    def aplicar(self, scheduler, modo="auto", argumentos=None):
        scheduler = canonizar(scheduler)
        ruta = self._instalados.get(scheduler)
        if not ruta:
            return Resultado(False, f"{scheduler} no está instalado en el sistema",
                             codigo="no_instalado")
        # Sólo puede haber un scheduler BPF a la vez: se detiene el anterior.
        if sched_ext.estado()["activo"]:
            detencion = self.detener()
            if not detencion.ok and detencion.codigo != "sin_scheduler":
                return detencion
        orden = [
            "systemd-run", f"--unit={UNIDAD_TRANSITORIA}", "--collect",
            "--property=KillSignal=SIGINT",
            "--property=Restart=no",
            "--description=Planificador sched_ext lanzado por R.E.A.C.T.O.R",
            ruta,
        ]
        orden.extend(argumentos or [])
        return self._ejecutar_elevado(orden)

    def detener(self):
        if not sched_ext.estado()["activo"]:
            return Resultado(True, "No hay ningún planificador activo",
                             codigo="sin_scheduler")
        # Se detiene por nombre de unidad: preciso y reversible. Nunca `pkill`,
        # que mataba cualquier proceso cuyo comando contuviera "scx_" —
        # incluidos scxtop y el propio scx_loader.
        return self._ejecutar_elevado(
            ["systemctl", "stop", f"{UNIDAD_TRANSITORIA}.service"], tiempo=30)


class BackendSimulado(BackendBase):
    """Backend de simulación: permite probar la interfaz sin tocar el sistema."""

    nombre = "simulado"
    capacidades = frozenset({"aplicar", "detener", "reiniciar", "restaurar", "modos"})

    _FICTICIOS = ("scx_bpfland", "scx_lavd", "scx_rusty", "scx_flash",
                  "scx_cosmos", "scx_p2dq", "scx_cake")

    def __init__(self):
        self._scheduler = "scx_bpfland"
        self._modo = "auto"

    def disponible(self):
        return True

    def lista(self):
        return list(self._FICTICIOS)

    def modos(self, scheduler):
        return [nombre for nombre, _ in MODOS]

    def estado(self):
        return self._scheduler, self._modo

    def aplicar(self, scheduler, modo="auto"):
        self._scheduler = canonizar(scheduler)
        self._modo = modo
        return Resultado(True, "Aplicado (simulado)")

    def detener(self):
        self._scheduler = None
        self._modo = None
        return Resultado(True, "Detenido (simulado)")

    def reiniciar(self):
        return Resultado(True, "Reiniciado (simulado)")

    def restaurar_predeterminado(self):
        self._scheduler = "scx_bpfland"
        self._modo = "auto"
        return Resultado(True, "Restaurado (simulado)")


# ─────────────────────────── fachada ───────────────────────────


class GestorScx:
    """Punto único de control de planificadores para el resto de la aplicación.

    Elige el mejor backend disponible y expone una interfaz estable. La
    interfaz gráfica no debe saber si por debajo hay D-Bus, `scxctl` o un
    binario lanzado con systemd.
    """

    def __init__(self, modo_desarrollador=False):
        self._backend = None
        self._simulado = BackendSimulado()
        self.modo_desarrollador = modo_desarrollador

    # ── selección de backend ──

    @property
    def modo_desarrollador(self):
        return self._modo_desarrollador

    @modo_desarrollador.setter
    def modo_desarrollador(self, valor):
        self._modo_desarrollador = bool(valor)
        # Al cambiar de modo se vuelve a elegir backend.
        self._backend = None

    def _elegir_backend(self):
        for candidato in (BackendDbus(), BackendScxctl(), BackendDirecto()):
            try:
                if candidato.disponible():
                    return candidato
            except Exception:
                continue
        return None

    @property
    def backend(self):
        """Backend activo. `None` si el equipo no permite controlar schedulers."""
        if self._modo_desarrollador:
            return self._simulado
        if self._backend is None:
            self._backend = self._elegir_backend()
        return self._backend

    def redetectar(self):
        """Vuelve a elegir backend. Útil tras instalar paquetes sin reiniciar."""
        self._backend = None
        return self.backend

    @property
    def nombre_backend(self):
        backend = self.backend
        return backend.nombre if backend else "ninguno"

    def puede(self, operacion):
        """True si el backend activo soporta esa operación."""
        backend = self.backend
        return bool(backend) and operacion in backend.capacidades

    def diagnostico(self):
        """Resumen del entorno de control, para la pantalla de verificación."""
        info = sched_ext.estado()
        return {
            "soporte_kernel": info["soportado"],
            "estado_kernel": info["estado"],
            "servicio_loader": BackendDbus.estado_del_servicio(),
            "scxctl": shutil.which("scxctl"),
            "schedulers_instalados": len(sched_ext.schedulers_instalados()),
            "backend": self.nombre_backend,
            "capacidades": sorted(self.backend.capacidades) if self.backend else [],
        }

    # ── operaciones ──

    def obtener_estado(self):
        """(scheduler_canonico | None, modo | None) del planificador activo.

        Se prefiere siempre lo que dice el kernel: es la única fuente que no
        miente si el scheduler lo arrancó otro cliente o si el loader se cayó.
        """
        if self._modo_desarrollador:
            return self._simulado.estado()
        info = sched_ext.estado()
        if not info["activo"]:
            return None, None
        backend = self.backend
        if backend is not None:
            nombre, modo = backend.estado()
            if nombre:
                # El kernel manda para el nombre; el backend aporta el modo.
                return info["scheduler"] or nombre, modo or "auto"
        return info["scheduler"], "auto"

    def obtener_lista(self, compatibles=None):
        """Schedulers ofrecibles al usuario, en nombre canónico.

        Se combina lo que el backend declara soportar con lo que hay realmente
        instalado en el sistema: el loader lleva una lista estática que deja
        fuera schedulers presentes en el equipo.
        """
        backend = self.backend
        nombres = set(backend.lista()) if backend else set()
        if not self._modo_desarrollador:
            nombres |= set(sched_ext.schedulers_instalados().keys())
        if compatibles is not None:
            compatibles_canonicos = {canonizar(c) for c in compatibles}
            nombres &= compatibles_canonicos
        return sorted(nombres)

    def modos_disponibles(self, scheduler):
        """Modos aplicables a ese scheduler, según el backend activo."""
        backend = self.backend
        if not backend or not scheduler:
            return ["auto"]
        return backend.modos(scheduler)

    def aplicar(self, scheduler, modo="auto"):
        backend = self.backend
        if backend is None:
            return Resultado(False, "No hay forma de controlar planificadores en este equipo",
                             codigo="sin_backend")
        return backend.aplicar(scheduler, modo)

    def detener(self):
        backend = self.backend
        if backend is None:
            return Resultado(False, "No hay forma de controlar planificadores en este equipo",
                             codigo="sin_backend")
        return backend.detener()

    def reiniciar(self):
        backend = self.backend
        if backend is None:
            return Resultado(False, "No hay forma de controlar planificadores en este equipo",
                             codigo="sin_backend")
        return backend.reiniciar()

    def restaurar_predeterminado(self):
        backend = self.backend
        if backend is None:
            return Resultado(False, "No hay forma de controlar planificadores en este equipo",
                             codigo="sin_backend")
        return backend.restaurar_predeterminado()
