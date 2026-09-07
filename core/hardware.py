"""
Detección de hardware y perfilado de la máquina.

Todo lo que la aplicación necesita saber del equipo donde corre se resuelve acá:
topología de CPU (incluidos los núcleos híbridos P/E), memoria, energía, chasis y
virtualización. El módulo es deliberadamente defensivo: ninguna función lanza
excepciones y todas devuelven un valor seguro cuando la fuente no existe, porque
el parque de equipos va desde una netbook de 2010 hasta un portátil híbrido de 2026.

Reglas de diseño:
  - Sólo stdlib. Nada de dependencias externas.
  - Se lee de /sys y /proc, nunca de la salida de herramientas traducibles como
    `lscpu` (sus claves cambian con el idioma del sistema).
  - Cada dato tiene un origen declarado y un valor por defecto explícito.
"""

import os
import glob
import platform
import re

RUTA_CPU = "/sys/devices/system/cpu"
RUTA_NODO = "/sys/devices/system/node"
RUTA_DMI = "/sys/class/dmi/id"
RUTA_ENERGIA = "/sys/class/power_supply"

# Tipos de chasis DMI que corresponden a un equipo portátil.
# Referencia: SMBIOS 3.x, tabla 3.3.4.1 (System Enclosure or Chassis Types).
_CHASIS_PORTATIL = {8, 9, 10, 11, 14, 30, 31, 32}
# Tipos de chasis que corresponden a un equipo de escritorio o torre.
_CHASIS_ESCRITORIO = {3, 4, 5, 6, 7, 13, 15, 16, 24, 35}


# ─────────────────────────── lectores base ───────────────────────────


def leer_texto(ruta, defecto=None):
    """Lee un archivo de sysfs/procfs. Nunca lanza; devuelve `defecto` si falla."""
    try:
        with open(ruta, "r", errors="replace") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return defecto


def leer_entero(ruta, defecto=None):
    """Lee un entero de sysfs. Acepta decimal y hexadecimal (0x...)."""
    crudo = leer_texto(ruta)
    if not crudo:
        return defecto
    try:
        return int(crudo, 0)
    except ValueError:
        try:
            return int(float(crudo))
        except ValueError:
            return defecto


def expandir_lista_cpus(texto):
    """Convierte '0-3,8,10-11' en [0,1,2,3,8,10,11]. Cadena vacía o nula → []."""
    resultado = []
    if not texto:
        return resultado
    for trozo in texto.replace("\n", "").split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        if "-" in trozo:
            try:
                inicio, fin = trozo.split("-", 1)
                resultado.extend(range(int(inicio), int(fin) + 1))
            except ValueError:
                continue
        else:
            try:
                resultado.append(int(trozo))
            except ValueError:
                continue
    return sorted(set(resultado))


def _tamano_a_kib(texto):
    """Convierte '24576K', '1280K', '32M' o '48' (KiB implícitos) a KiB."""
    if not texto:
        return 0
    coincidencia = re.match(r"\s*(\d+(?:\.\d+)?)\s*([KMG])?", texto.strip(), re.IGNORECASE)
    if not coincidencia:
        return 0
    valor = float(coincidencia.group(1))
    unidad = (coincidencia.group(2) or "K").upper()
    if unidad == "M":
        valor *= 1024
    elif unidad == "G":
        valor *= 1024 * 1024
    return int(valor)


# ─────────────────────────── topología de CPU ───────────────────────────


def cpus_online():
    """CPUs que el kernel tiene en línea. Fallback: os.cpu_count()."""
    lista = expandir_lista_cpus(leer_texto(f"{RUTA_CPU}/online"))
    if lista:
        return lista
    return list(range(os.cpu_count() or 1))


def cpus_utilizables():
    """CPUs que este proceso puede usar realmente.

    Respeta la afinidad heredada (taskset, cpuset, contenedores). Es el número
    correcto para dimensionar una carga de trabajo; `os.cpu_count()` no lo es.
    """
    try:
        afinidad = sorted(os.sched_getaffinity(0))
        if afinidad:
            return afinidad
    except (AttributeError, OSError):
        pass
    return cpus_online()


def cuota_cgroup():
    """Cuota de CPU impuesta por cgroup v2, en CPUs enteras equivalentes.

    Devuelve None si no hay límite (lo habitual en un equipo de escritorio).
    """
    crudo = leer_texto("/sys/fs/cgroup/cpu.max")
    if not crudo:
        return None
    partes = crudo.split()
    if len(partes) != 2 or partes[0] == "max":
        return None
    try:
        cuota, periodo = int(partes[0]), int(partes[1])
        if periodo > 0 and cuota > 0:
            return max(1, cuota // periodo)
    except ValueError:
        pass
    return None


def nucleos_hibridos():
    """Separa núcleos de rendimiento (P) y de eficiencia (E).

    En x86 híbrido (Intel desde Alder Lake) las dos vías "obvias" no sirven:
    `cpu*/topology/core_type` no existe y `cpu*/cpu_capacity` vale 1024 en todos
    los núcleos. Lo que sí funciona es el subsistema perf, que registra un PMU
    distinto por tipo de núcleo: /sys/devices/cpu_core y /sys/devices/cpu_atom.

    En ARM (big.LITTLE) el discriminante correcto sí es `cpu_capacity`.

    Returns:
        tuple: (cpus_rendimiento, cpus_eficiencia). Listas vacías si es homogénea.
    """
    rendimiento = expandir_lista_cpus(leer_texto("/sys/devices/cpu_core/cpus"))
    eficiencia = expandir_lista_cpus(leer_texto("/sys/devices/cpu_atom/cpus"))
    if rendimiento and eficiencia:
        return rendimiento, eficiencia

    # Vía ARM: cpu_capacity distingue los clústeres. En x86 siempre vale 1024.
    capacidades = {}
    for cpu in cpus_online():
        valor = leer_entero(f"{RUTA_CPU}/cpu{cpu}/cpu_capacity")
        if valor:
            capacidades[cpu] = valor
    if capacidades and len(set(capacidades.values())) > 1:
        maxima = max(capacidades.values())
        rendimiento = sorted(c for c, v in capacidades.items() if v == maxima)
        eficiencia = sorted(c for c, v in capacidades.items() if v < maxima)
        return rendimiento, eficiencia

    return [], []


def hilos_por_nucleo():
    """Hilos SMT por núcleo físico. 1 si no hay SMT o no se puede determinar."""
    for cpu in cpus_online():
        hermanos = expandir_lista_cpus(
            leer_texto(f"{RUTA_CPU}/cpu{cpu}/topology/thread_siblings_list")
        )
        if hermanos:
            return max(1, len(hermanos))
    return 1


def contar_nucleos_fisicos(cpus=None):
    """Cuenta núcleos físicos agrupando por (paquete, núcleo).

    No divide CPUs entre hilos: ese cálculo es incorrecto en CPUs híbridas, donde
    los núcleos P tienen SMT y los E no. Con `cpus` se cuenta sólo ese subconjunto,
    que es como se obtienen los núcleos P y E físicos por separado.
    """
    lista = cpus_online() if cpus is None else cpus
    vistos = set()
    for cpu in lista:
        base = f"{RUTA_CPU}/cpu{cpu}/topology"
        paquete = leer_entero(f"{base}/physical_package_id", 0)
        nucleo = leer_entero(f"{base}/core_id")
        if nucleo is None:
            continue
        vistos.add((paquete, nucleo))
    if vistos:
        return len(vistos)
    return max(1, len(lista) // max(1, hilos_por_nucleo()))


def nucleos_fisicos():
    """Núcleos físicos totales del equipo."""
    return contar_nucleos_fisicos()


def caches_kib():
    """Tamaños de caché en KiB leídos de sysfs: {'l1d','l1i','l2','l3'}.

    Los valores son por instancia de caché, no agregados. Un 0 significa
    "no informado por el hardware", que es un dato válido (hay CPUs sin L3).
    """
    resultado = {"l1d": 0, "l1i": 0, "l2": 0, "l3": 0}
    primera = cpus_online()[0] if cpus_online() else 0
    for indice in sorted(glob.glob(f"{RUTA_CPU}/cpu{primera}/cache/index*")):
        nivel = leer_entero(f"{indice}/level")
        tipo = (leer_texto(f"{indice}/type") or "").lower()
        tamano = _tamano_a_kib(leer_texto(f"{indice}/size"))
        if not nivel or not tamano:
            continue
        if nivel == 1 and tipo.startswith("data"):
            resultado["l1d"] = tamano
        elif nivel == 1 and tipo.startswith("instr"):
            resultado["l1i"] = tamano
        elif nivel == 2:
            resultado["l2"] = tamano
        elif nivel == 3:
            resultado["l3"] = tamano
    return resultado


def grupos_llc():
    """Cantidad de dominios de última caché compartida (LLC).

    En AMD equivale a los CCX/CCD; es el dato que hace relevantes a los
    planificadores conscientes de topología.
    """
    dominios = set()
    for cpu in cpus_online():
        mejor = None
        for indice in glob.glob(f"{RUTA_CPU}/cpu{cpu}/cache/index*"):
            nivel = leer_entero(f"{indice}/level", 0)
            compartida = leer_texto(f"{indice}/shared_cpu_list")
            if compartida and (mejor is None or nivel > mejor[0]):
                mejor = (nivel, compartida)
        if mejor:
            dominios.add(mejor[1])
    return max(1, len(dominios))


def nodos_numa():
    """Cantidad de nodos NUMA. 1 en cualquier equipo de escritorio o portátil."""
    return max(1, len(glob.glob(f"{RUTA_NODO}/node[0-9]*")))


def modelo_cpu():
    """Nombre comercial del CPU, leído de /proc/cpuinfo (no depende del idioma)."""
    try:
        with open("/proc/cpuinfo", errors="replace") as f:
            for linea in f:
                if linea.lower().startswith(("model name", "cpu model", "hardware")):
                    return linea.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "Desconocido"


def fabricante_cpu():
    """Fabricante normalizado: 'intel', 'amd', 'arm' u 'otro'."""
    try:
        with open("/proc/cpuinfo", errors="replace") as f:
            contenido = f.read(8192).lower()
    except OSError:
        contenido = modelo_cpu().lower()
    if "genuineintel" in contenido or "intel" in contenido:
        return "intel"
    if "authenticamd" in contenido or "amd" in contenido:
        return "amd"
    if "arm" in contenido or "aarch64" in platform.machine().lower():
        return "arm"
    return "otro"


def frecuencia_maxima_khz():
    """Frecuencia máxima anunciada, en kHz. 0 si no hay cpufreq (equipos viejos)."""
    maxima = 0
    for ruta in glob.glob(f"{RUTA_CPU}/cpu*/cpufreq/cpuinfo_max_freq"):
        valor = leer_entero(ruta, 0) or 0
        maxima = max(maxima, valor)
    return maxima


# ─────────────────────────── memoria ───────────────────────────


def memoria_kib():
    """Memoria del sistema: (total_kib, disponible_kib).

    Se usa MemAvailable, no MemFree: es la estimación del kernel de cuánta
    memoria puede pedirse sin provocar swap, y es la cifra correcta para
    dimensionar una prueba de memoria sin arriesgar un OOM.
    """
    total = disponible = 0
    try:
        with open("/proc/meminfo", errors="replace") as f:
            for linea in f:
                if linea.startswith("MemTotal:"):
                    total = int(linea.split()[1])
                elif linea.startswith("MemAvailable:"):
                    disponible = int(linea.split()[1])
                if total and disponible:
                    break
    except (OSError, ValueError, IndexError):
        pass
    if total and not disponible:
        disponible = total // 2
    return total, disponible


# ─────────────────────────── energía y chasis ───────────────────────────


def baterias_del_sistema():
    """Baterías que alimentan al equipo, excluyendo periféricos.

    Un mouse o teclado inalámbrico aparece en /sys/class/power_supply como
    type=Battery. Se distingue por `scope`: los periféricos declaran
    scope=Device, mientras que la batería del equipo declara scope=System o
    directamente no publica el atributo.
    """
    resultado = []
    for ruta in sorted(glob.glob(f"{RUTA_ENERGIA}/*")):
        if (leer_texto(f"{ruta}/type") or "").lower() != "battery":
            continue
        alcance = (leer_texto(f"{ruta}/scope") or "system").lower()
        if alcance == "device":
            continue
        resultado.append(ruta)
    return resultado


def en_bateria():
    """True si el equipo está funcionando con batería.

    Devuelve False cuando no hay batería del sistema (equipo de escritorio) o
    cuando no se puede determinar: nunca bloquear por una lectura ausente.
    """
    for ruta in sorted(glob.glob(f"{RUTA_ENERGIA}/*")):
        if (leer_texto(f"{ruta}/type") or "").lower() == "mains":
            en_linea = leer_entero(f"{ruta}/online")
            if en_linea is not None:
                return en_linea == 0
    for bateria in baterias_del_sistema():
        estado = (leer_texto(f"{bateria}/status") or "").lower()
        if estado == "discharging":
            return True
    return False


def tipo_de_equipo():
    """Clasifica el equipo: 'portatil', 'escritorio', 'virtual' o 'desconocido'."""
    if virtualizacion() != "ninguna":
        return "virtual"
    chasis = leer_entero(f"{RUTA_DMI}/chassis_type")
    if chasis in _CHASIS_PORTATIL:
        return "portatil"
    if chasis in _CHASIS_ESCRITORIO:
        return "escritorio"
    # El DMI miente a menudo en equipos OEM: la batería del sistema es el
    # desempate más fiable.
    if baterias_del_sistema():
        return "portatil"
    return "escritorio" if chasis else "desconocido"


def virtualizacion():
    """Detecta si corremos en una VM o contenedor. 'ninguna' si es hardware real.

    No se invoca systemd-detect-virt: se leen las mismas fuentes que él usa,
    para no depender de systemd ni pagar el coste de un subproceso.
    """
    for ruta, etiqueta in (
        ("/proc/vz", "openvz"),
        ("/proc/xen", "xen"),
    ):
        if os.path.exists(ruta):
            return etiqueta
    if os.path.exists("/.dockerenv"):
        return "docker"
    entorno = leer_texto("/run/systemd/container")
    if entorno:
        return entorno
    proveedor = (leer_texto(f"{RUTA_DMI}/sys_vendor") or "").lower()
    producto = (leer_texto(f"{RUTA_DMI}/product_name") or "").lower()
    for aguja, etiqueta in (
        ("qemu", "qemu"), ("kvm", "kvm"), ("vmware", "vmware"),
        ("virtualbox", "virtualbox"), ("innotek", "virtualbox"),
        ("microsoft corporation", "hyperv"), ("xen", "xen"),
        ("parallels", "parallels"), ("bochs", "qemu"),
    ):
        if aguja in proveedor or aguja in producto:
            return etiqueta
    try:
        with open("/proc/cpuinfo", errors="replace") as f:
            if " hypervisor" in f.read(8192):
                return "desconocida"
    except OSError:
        pass
    return "ninguna"


def estado_energia():
    """Instantánea de la configuración de frecuencia y energía del equipo.

    Estos ajustes influyen en el rendimiento medido mucho más que el propio
    planificador, así que se registran junto a cada resultado para que dos
    mediciones sólo se comparen si se tomaron bajo el mismo régimen.
    """
    politica = f"{RUTA_CPU}/cpufreq/policy0"
    turbo = None
    valor_boost = leer_entero(f"{RUTA_CPU}/cpufreq/boost")
    if valor_boost is not None:
        turbo = bool(valor_boost)
    else:
        sin_turbo = leer_entero(f"{RUTA_CPU}/intel_pstate/no_turbo")
        if sin_turbo is not None:
            turbo = not sin_turbo
    return {
        "driver": leer_texto(f"{politica}/scaling_driver") or "ninguno",
        "governor": leer_texto(f"{politica}/scaling_governor") or "desconocido",
        "epp": leer_texto(f"{politica}/energy_performance_preference"),
        "perfil_plataforma": leer_texto("/sys/firmware/acpi/platform_profile"),
        "turbo": turbo,
        "en_bateria": en_bateria(),
    }


def throttling_termico():
    """Contadores de limitación térmica acumulados desde el arranque.

    Es la señal objetiva de que el hardware frenó por temperatura, mucho más
    fiable que comparar la temperatura absoluta contra un umbral. Comparando el
    contador antes y después de una prueba se sabe si esa medición es válida.
    """
    total_nucleo = total_paquete = 0
    disponible = False
    for cpu in cpus_online():
        base = f"{RUTA_CPU}/cpu{cpu}/thermal_throttle"
        valor = leer_entero(f"{base}/core_throttle_count")
        if valor is not None:
            disponible = True
            total_nucleo += valor
        valor = leer_entero(f"{base}/package_throttle_count")
        if valor is not None:
            disponible = True
            total_paquete += valor
    return {
        "disponible": disponible,
        "nucleo": total_nucleo,
        "paquete": total_paquete,
    }


# ─────────────────────────── perfil consolidado ───────────────────────────


class PerfilMaquina:
    """Retrato del equipo donde corre la aplicación.

    Se construye una vez al arrancar. Los datos volátiles (batería, governor,
    throttling) se releen bajo demanda con `refrescar_volatiles()`.
    """

    def __init__(self):
        utilizables = cpus_utilizables()
        rendimiento, eficiencia = nucleos_hibridos()
        cuota = cuota_cgroup()

        self.modelo = modelo_cpu()
        self.fabricante = fabricante_cpu()
        self.arquitectura = platform.machine()
        self.kernel = platform.release()

        self.cpus_online = cpus_online()
        self.cpus_utilizables = utilizables
        self.cuota_cgroup = cuota
        self.hilos = len(utilizables)
        self.hilos_por_nucleo = hilos_por_nucleo()
        self.nucleos_fisicos = nucleos_fisicos()

        self.es_hibrida = bool(rendimiento and eficiencia)
        self.cpus_rendimiento = rendimiento
        self.cpus_eficiencia = eficiencia
        # Núcleos físicos por tipo. En un Intel híbrido los P llevan SMT y los E
        # no, así que 12 CPUs de rendimiento son 6 núcleos físicos.
        self.nucleos_rendimiento = contar_nucleos_fisicos(rendimiento) if rendimiento else 0
        self.nucleos_eficiencia = contar_nucleos_fisicos(eficiencia) if eficiencia else 0

        self.caches = caches_kib()
        self.grupos_llc = grupos_llc()
        self.nodos_numa = nodos_numa()
        self.frecuencia_maxima_khz = frecuencia_maxima_khz()

        self.ram_total_kib, self.ram_disponible_kib = memoria_kib()

        self.tipo_equipo = tipo_de_equipo()
        self.virtualizacion = virtualizacion()
        self.tiene_bateria = bool(baterias_del_sistema())

        self.energia = estado_energia()
        self.throttling = throttling_termico()

    def refrescar_volatiles(self):
        """Relee lo que cambia durante la sesión: energía y throttling."""
        self.energia = estado_energia()
        self.throttling = throttling_termico()
        _, self.ram_disponible_kib = memoria_kib()

    @property
    def es_portatil(self):
        return self.tipo_equipo == "portatil"

    @property
    def es_virtual(self):
        return self.virtualizacion != "ninguna"

    @property
    def hilos_para_carga(self):
        """Hilos que conviene usar para generar carga.

        Respeta la afinidad y la cuota de cgroup, y nunca devuelve menos de 1.
        """
        limite = self.hilos
        if self.cuota_cgroup:
            limite = min(limite, self.cuota_cgroup)
        return max(1, limite)

    def descripcion_corta(self):
        """Una línea legible para la interfaz y para el registro del historial."""
        partes = [self.modelo]
        if self.es_hibrida:
            partes.append(f"{self.nucleos_rendimiento}P+{self.nucleos_eficiencia}E")
        partes.append(f"{self.nucleos_fisicos}c/{self.hilos}h")
        partes.append(f"{self.ram_total_kib // 1024 // 1024} GiB")
        if self.es_virtual:
            partes.append(f"[{self.virtualizacion}]")
        return " · ".join(partes)

    def como_diccionario(self):
        """Metadatos de entorno para guardar junto a cada resultado.

        Sin estos campos es imposible saber si dos mediciones del historial son
        comparables entre sí.
        """
        return {
            "cpu_modelo": self.modelo,
            "cpu_fabricante": self.fabricante,
            "arquitectura": self.arquitectura,
            "kernel": self.kernel,
            "hilos": self.hilos,
            "nucleos_fisicos": self.nucleos_fisicos,
            "hilos_por_nucleo": self.hilos_por_nucleo,
            "es_hibrida": self.es_hibrida,
            "nucleos_rendimiento": self.nucleos_rendimiento,
            "nucleos_eficiencia": self.nucleos_eficiencia,
            "cache_l3_kib": self.caches.get("l3", 0),
            "grupos_llc": self.grupos_llc,
            "nodos_numa": self.nodos_numa,
            "ram_total_kib": self.ram_total_kib,
            "tipo_equipo": self.tipo_equipo,
            "virtualizacion": self.virtualizacion,
            "cpufreq_driver": self.energia.get("driver"),
            "governor": self.energia.get("governor"),
            "epp": self.energia.get("epp"),
            "perfil_plataforma": self.energia.get("perfil_plataforma"),
            "turbo": self.energia.get("turbo"),
            "en_bateria": self.energia.get("en_bateria"),
        }


_PERFIL = None


def obtener_perfil(recargar=False):
    """Perfil de la máquina, cacheado. Es lo que debe usar el resto de la app."""
    global _PERFIL
    if _PERFIL is None or recargar:
        _PERFIL = PerfilMaquina()
    return _PERFIL
