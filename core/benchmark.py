"""
Motor de benchmarking basado en stress-ng.

stress-ng publica agregados, no distribuciones de muestras. Por eso estos
resultados exponen una medida de respuesta honesta y conservan ``p95`` como
``None`` en lugar de presentar una media o un proxy como percentil.
"""

import hashlib
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from core.operations import OperationCancelled
from core.processes import check_cancelled, run_process, wait_cancelable
from core.scx import BASE_SYSTEM_NAME, ScxState
from utils.helpers import log as _log, limpiar_texto as _limpiar_texto


_NUMERO_YAML_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_METRICS_RE = re.compile(r"^(?P<indent> *)metrics\s*:\s*(?:#.*)?$")
_STRESSOR_RE = re.compile(
    r"^(?P<indent> *)-\s+stressor\s*:\s*(?P<value>.*?)\s*$"
)
_FIELD_RE = re.compile(
    r"^(?P<indent> *)(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*?)\s*$"
)


def _quitar_comentario_yaml(valor):
    """Quita un comentario YAML sin cortar almohadillas entre comillas."""
    comilla = None
    escapado = False
    for indice, caracter in enumerate(valor):
        if escapado:
            escapado = False
            continue
        if caracter == "\\" and comilla == '"':
            escapado = True
            continue
        if caracter in ("'", '"'):
            if comilla is None:
                comilla = caracter
            elif comilla == caracter:
                comilla = None
            continue
        if caracter == "#" and comilla is None:
            return valor[:indice].rstrip()
    return valor.strip()


def _descomillar_yaml(valor):
    valor = _quitar_comentario_yaml(valor).strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ("'", '"'):
        return valor[1:-1]
    return valor


def _numero_yaml_finito(valor):
    """Convierte un escalar numerico YAML y rechaza NaN, Inf y overflow."""
    limpio = _descomillar_yaml(valor)
    if not _NUMERO_YAML_RE.fullmatch(limpio):
        return None
    try:
        numero = float(limpio)
    except (TypeError, ValueError, OverflowError):
        return None
    return numero if math.isfinite(numero) else None


def _parsear_yaml_simple(contenido, stressor=None):
    """Extrae un bloque de ``metrics`` de stress-ng sin depender de PyYAML.

    Solo acepta campos escalares numericos finitos situados en el nivel de
    indentacion del bloque. Si hay varios stressors o documentos, ``stressor``
    permite seleccionar el correcto sin mezclar metricas entre bloques.
    """
    lineas = contenido.splitlines()
    bloques = []
    indice = 0

    while indice < len(lineas):
        coincidencia_metricas = _METRICS_RE.match(lineas[indice])
        if not coincidencia_metricas:
            indice += 1
            continue

        indent_metricas = len(coincidencia_metricas.group("indent"))
        indice += 1
        bloque = None
        indent_bloque = None
        indent_campos = None

        while indice < len(lineas):
            linea = lineas[indice]
            stripped = linea.strip()

            if not stripped or stripped.startswith("#"):
                indice += 1
                continue
            if "\t" in linea[: len(linea) - len(linea.lstrip())]:
                indice += 1
                continue

            indent = len(linea) - len(linea.lstrip(" "))
            if indent <= indent_metricas:
                break

            coincidencia_stressor = _STRESSOR_RE.match(linea)
            if coincidencia_stressor:
                if bloque is not None:
                    bloques.append(bloque)
                nombre = _descomillar_yaml(coincidencia_stressor.group("value"))
                bloque = {"stressor": nombre}
                indent_bloque = indent
                indent_campos = None
                indice += 1
                continue

            coincidencia_campo = _FIELD_RE.match(linea)
            if bloque is not None and coincidencia_campo and indent > indent_bloque:
                if indent_campos is None:
                    indent_campos = indent
                if indent == indent_campos:
                    numero = _numero_yaml_finito(coincidencia_campo.group("value"))
                    if numero is not None:
                        bloque[coincidencia_campo.group("key")] = numero

            indice += 1

        if bloque is not None:
            bloques.append(bloque)

    if stressor is not None:
        esperado = str(stressor).casefold()
        return next(
            (
                bloque
                for bloque in bloques
                if str(bloque.get("stressor", "")).casefold() == esperado
            ),
            {},
        )
    return bloques[0] if bloques else {}


def _semilla_estable(*partes):
    datos = "\0".join(str(parte) for parte in partes).encode("utf-8")
    return int.from_bytes(hashlib.sha256(datos).digest()[:8], "big")


def _numero_positivo(metricas, *claves):
    for clave in claves:
        valor = metricas.get(clave)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            continue
        if math.isfinite(valor) and valor > 0:
            return float(valor)
    return 0.0


def _log_binario_ausente(tv_log, logs, binario):
    if logs and tv_log:
        _log(
            tv_log,
            f"Error: {binario} no encontrado en PATH.",
            es_error=True,
        )


def _capturar_estado_estricto(scx_manager, cancel_token=None):
    capturar = getattr(scx_manager, "capturar_estado", None)
    if not callable(capturar):
        raise TypeError("El gestor SCX no permite capturar ScxState")
    check_cancelled(cancel_token)
    estado = capturar(cancel_token=cancel_token)
    check_cancelled(cancel_token)
    if not isinstance(estado, ScxState):
        raise TypeError("capturar_estado() debe devolver ScxState")
    return estado


def _describir_estado(estado):
    scheduler = estado.scheduler or BASE_SYSTEM_NAME
    modo = estado.mode or "default"
    return f"{scheduler} [{modo}]"


def _formatear_bloque(texto, prefijo, max_lineas, max_caracteres):
    limpio = (_limpiar_texto(texto) or "")[:max_caracteres]
    lineas = limpio.splitlines()[:max_lineas]
    return "\n".join(f"{prefijo}{linea}" for linea in lineas)


def _registrar_cambio_estado(tv_log, logs, inicial, final):
    if logs and tv_log:
        _log(
            tv_log,
            "Resultado descartado: el estado SCX cambio durante la prueba "
            f"(inicio: {_describir_estado(inicial)}; "
            f"final: {_describir_estado(final)}).",
            es_error=True,
        )


def _resultado_dev(tipo, sc_act, modo_act):
    semilla = _semilla_estable(sc_act, tipo)
    bases = {
        "cpu": {"valor": 8500.0, "response": 5.0, "fairness": 0.08},
        "threads": {"valor": 10000.0, "fairness": 0.05},
        "memory": {"valor": 12000.0, "response": 3.5, "fairness": 0.12},
    }
    base = bases[tipo]
    factor = 0.9 + (semilla % 200) / 1000.0
    valor = base["valor"] * factor

    if tipo == "cpu":
        response = base["response"] * (0.8 + (semilla % 40) / 100.0)
        response_kind = "mean_context_switch_us"
    elif tipo == "memory":
        response = base["response"] * (0.8 + (semilla % 40) / 100.0)
        response_kind = "mean_mutex_us"
    else:
        response = None
        response_kind = None

    fairness = base["fairness"] * (0.9 + (semilla % 20) / 100.0)

    return {
        "tipo": tipo,
        "valor": valor,
        "response": response,
        "response_kind": response_kind,
        "p95": None,
        "fairness": fairness,
        "fairness_kind": "cpu_idle_fraction",
        "sched": sc_act,
        "modo": modo_act,
        "metrics": {},
        "raw_yaml": "",
        "ops_real": valor,
        "ops_usr_sys": 0.0,
        "cpu_usage": (1.0 - fairness) * 100.0,
        "wall_time": 0.0,
        "ns_per_switch": 0.0,
        "ns_per_mutex": 0.0,
        "cores": os.cpu_count() or 4,
        "timestamp": time.time(),
    }


def correr_benchmark(
    tipo,
    scx_manager,
    tv_log=None,
    tiempo=5,
    logs=True,
    modo_dev=False,
    cancel_token=None,
):
    """Ejecuta stress-ng sin inventar metricas que no fueron observadas."""
    check_cancelled(cancel_token)
    if tipo not in ("cpu", "threads", "memory"):
        if logs and tv_log:
            _log(tv_log, f"Tipo de prueba desconocido: {tipo}", es_error=True)
        return None

    try:
        wait_cancelable(cancel_token, 0.3)
        try:
            estado_inicial = _capturar_estado_estricto(scx_manager, cancel_token)
        except FileNotFoundError as exc:
            _log_binario_ausente(
                tv_log,
                logs,
                os.path.basename(exc.filename) if exc.filename else "scxctl",
            )
            return None

        sc_act = estado_inicial.scheduler or BASE_SYSTEM_NAME
        modo_act = estado_inicial.mode or "default"

        if logs and tv_log:
            _log(tv_log, f"INICIANDO: {tipo.upper()} ({sc_act} [{modo_act}])", True)

        if modo_dev:
            wait_cancelable(cancel_token, 0.5)
            resultado = _resultado_dev(tipo, sc_act, modo_act)
            check_cancelled(cancel_token)
            return resultado

        if shutil.which("stress-ng") is None:
            _log_binario_ausente(tv_log, logs, "stress-ng")
            return None

        cores = os.cpu_count() or 4
        stressor = {"cpu": "switch", "threads": "cpu", "memory": "mutex"}[tipo]

        with tempfile.TemporaryDirectory(prefix="scxctl_bench_") as tmpdir:
            yaml_path = os.path.join(tmpdir, "result.yaml")
            if tipo == "cpu":
                cmd = [
                    "stress-ng",
                    "--switch", str(cores),
                    "--timeout", f"{tiempo}s",
                    "--metrics-brief",
                    "--yaml", yaml_path,
                ]
            elif tipo == "threads":
                cmd = [
                    "stress-ng",
                    "--cpu", str(cores),
                    "--cpu-method", "matrixprod",
                    "--timeout", f"{tiempo}s",
                    "--metrics-brief",
                    "--yaml", yaml_path,
                ]
            else:
                cmd = [
                    "stress-ng",
                    "--mutex", str(cores * 2),
                    "--timeout", f"{tiempo}s",
                    "--metrics-brief",
                    "--yaml", yaml_path,
                ]

            if logs and tv_log:
                _log(tv_log, f"Ejecutando comando: {shlex.join(cmd)}")
            inicio = time.time()
            try:
                res = run_process(
                    cmd,
                    timeout=tiempo + 10,
                    cancel_token=cancel_token,
                )
            except FileNotFoundError as exc:
                _log_binario_ausente(
                    tv_log,
                    logs,
                    os.path.basename(exc.filename) if exc.filename else "stress-ng",
                )
                return None
            elapsed = time.time() - inicio
            check_cancelled(cancel_token)
            estado_final = _capturar_estado_estricto(scx_manager, cancel_token)

            if logs and tv_log:
                _log(tv_log, f"Comando finalizado (exit={res.returncode}) en {elapsed:.2f}s")
                if res.stdout:
                    bloque = _formatear_bloque(res.stdout, "STDOUT: ", 200, 20_000)
                    if bloque:
                        _log(tv_log, bloque)
                if res.stderr:
                    bloque = _formatear_bloque(res.stderr, "STDERR: ", 200, 20_000)
                    if bloque:
                        _log(tv_log, bloque)

            if estado_final != estado_inicial:
                _registrar_cambio_estado(
                    tv_log,
                    logs,
                    estado_inicial,
                    estado_final,
                )
                return None

            if res.returncode != 0:
                if logs and tv_log:
                    detalle = _formatear_bloque(res.stderr, "", 20, 2_000).strip()
                    sufijo = f": {detalle}" if detalle else ""
                    _log(tv_log, f"Error en stress-ng{sufijo}", es_error=True)
                return None

            try:
                with open(yaml_path, "r", encoding="utf-8") as archivo:
                    contenido = archivo.read()
            except (OSError, UnicodeError) as exc:
                if logs and tv_log:
                    _log(tv_log, f"Error leyendo YAML: {exc}", es_error=True)
                return None

            metricas = _parsear_yaml_simple(contenido, stressor=stressor)
            check_cancelled(cancel_token)
            if logs and tv_log:
                preview = _formatear_bloque(contenido, "", 200, 2_000)
                if preview:
                    _log(tv_log, preview)
                _log(tv_log, f"Metricas parseadas: {metricas}")

        bogo_ops = _numero_positivo(metricas, "bogo-ops")
        ops_real = _numero_positivo(metricas, "bogo-ops-per-second-real-time")
        ops_usr_sys = _numero_positivo(metricas, "bogo-ops-per-second-usr-sys-time")
        cpu_usage = _numero_positivo(metricas, "cpu-usage-per-instance")
        wall_time = _numero_positivo(metricas, "wall-clock-time")
        ns_per_switch = _numero_positivo(
            metricas,
            "nanosecs-per-context-switch-pipe-method",
            "nanosecs-per-context-switch",
        )
        ns_per_mutex = _numero_positivo(metricas, "nanosecs-per-mutex")

        valor = ops_real
        if valor <= 0 and bogo_ops > 0 and wall_time > 0:
            valor = bogo_ops / wall_time
        if valor <= 0 or not math.isfinite(valor):
            if logs and tv_log:
                _log(tv_log, "Resultado invalido (sin throughput medido)", es_error=True)
            return None

        if cpu_usage <= 0:
            if logs and tv_log:
                _log(tv_log, "Resultado invalido (sin cpu_usage medido)", es_error=True)
            return None

        if tipo == "cpu" and ns_per_switch > 0:
            response = ns_per_switch / 1000.0
            response_kind = "mean_context_switch_us"
        elif tipo == "memory" and ns_per_mutex > 0:
            response = ns_per_mutex / 1000.0
            response_kind = "mean_mutex_us"
        elif tipo == "threads":
            response = None
            response_kind = None
        else:
            if logs and tv_log:
                _log(tv_log, "Resultado invalido (sin medida de respuesta)", es_error=True)
            return None

        if response is not None and (not math.isfinite(response) or response <= 0):
            if logs and tv_log:
                _log(tv_log, "Resultado invalido (respuesta no positiva)", es_error=True)
            return None

        fairness = max(0.0, (100.0 - cpu_usage) / 100.0)

        if logs and tv_log:
            if response is None:
                resumen = (
                    f"RESUMEN: {valor:,.2f} ops/s | Respuesta: no aplica | "
                    f"Eficiencia CPU: {cpu_usage:.1f}%"
                )
            else:
                resumen = (
                    f"RESUMEN: {valor:,.2f} ops/s | Respuesta: {response:.3f} us "
                    f"({response_kind}) | Eficiencia CPU: {cpu_usage:.1f}%"
                )
            _log(tv_log, resumen)
            _log(tv_log, f"Fairness: {fairness:.3f} | Scheduler: {sc_act} | Modo: {modo_act}")
            _log(tv_log, "-" * 50)

        resultado = {
            "tipo": tipo,
            "valor": valor,
            "response": response,
            "response_kind": response_kind,
            "p95": None,
            "fairness": fairness,
            "fairness_kind": "cpu_idle_fraction",
            "sched": sc_act,
            "modo": modo_act,
            "metrics": metricas,
            "raw_yaml": contenido,
            "ops_real": ops_real,
            "ops_usr_sys": ops_usr_sys,
            "cpu_usage": cpu_usage,
            "wall_time": wall_time,
            "ns_per_switch": ns_per_switch,
            "ns_per_mutex": ns_per_mutex,
            "cores": cores,
            "timestamp": time.time(),
        }
        check_cancelled(cancel_token)
        return resultado

    except OperationCancelled:
        raise
    except FileNotFoundError as exc:
        _log_binario_ausente(
            tv_log,
            logs,
            os.path.basename(exc.filename) if exc.filename else "scxctl",
        )
        return None
    except subprocess.TimeoutExpired:
        if logs and tv_log:
            _log(tv_log, "Prueba excedio el tiempo limite.", es_error=True)
        return None
    except Exception as exc:
        if logs and tv_log:
            _log(tv_log, f"Error: {exc}", es_error=True)
        return None
