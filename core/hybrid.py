"""
Motor de benchmarking de latencia basado en hyperfine.

Hyperfine exporta las muestras individuales en ``times``. El contrato de este
modulo calcula p95 sobre esas muestras y usa ese mismo percentil como medida de
respuesta menor-es-mejor.
"""

import hashlib
import json
import math
import os
import shlex
import shutil
import stat
import statistics
import subprocess
import tempfile
import time

from core.operations import OperationCancelled
from core.processes import (
    check_cancelled,
    run_process,
    start_process,
    terminate_process,
    wait_cancelable,
)
from core.scx import BASE_SYSTEM_NAME, ScxState
from utils.helpers import log as _log, limpiar_texto as _limpiar_texto


_COMPILE_FUNCTIONS = 512
_LOADED_STRESS_MARGIN = 10
_MAX_LOADED_STRESS_TIMEOUT = 610
_TIPOS_HYBRID = {
    "fork": "fork",
    "compile": "compile",
    "loaded": "loaded",
    "latencia_fork": "fork",
    "latencia_compile": "compile",
    "latencia_loaded": "loaded",
}


def _planificar_hyperfine(tipo, tiempo):
    """Deriva muestras y timeout desde un presupuesto solicitado en segundos.

    El presupuesto se limita a 1..60 s. Fork usa 2000 runs/s (1000..20000),
    mientras compile y loaded usan 4 runs/s con minimos de 20 para que p95 no
    degenere siempre en el maximo. Los timeout incluyen margen por preparacion
    y se mantienen acotados por tipo.
    """
    if isinstance(tiempo, bool):
        raise ValueError("tiempo debe ser un numero positivo")
    try:
        presupuesto = float(tiempo)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("tiempo debe ser un numero positivo") from exc
    if not math.isfinite(presupuesto) or presupuesto <= 0:
        raise ValueError("tiempo debe ser un numero positivo")

    presupuesto = min(60.0, max(1.0, presupuesto))
    if tipo == "fork":
        runs = min(20_000, max(1_000, math.ceil(presupuesto * 2_000)))
        timeout = min(180, max(30, math.ceil(presupuesto * 6)))
    elif tipo == "compile":
        runs = min(60, max(20, math.ceil(presupuesto * 4)))
        timeout = min(900, max(120, math.ceil(presupuesto * 30)))
    elif tipo == "loaded":
        runs = min(100, max(20, math.ceil(presupuesto * 4)))
        timeout = min(600, max(60, math.ceil(presupuesto * 12)))
    else:
        raise ValueError(f"Tipo Hyperfine desconocido: {tipo}")
    return runs, timeout


def _calcular_timeout_carga(timeout_hyperfine):
    """Acota la vida nativa de stress-ng si el proceso padre desaparece."""
    return min(
        _MAX_LOADED_STRESS_TIMEOUT,
        max(1, math.ceil(timeout_hyperfine) + _LOADED_STRESS_MARGIN),
    )


def _a_microsegundos(valor, unidad):
    if unidad == "s":
        return valor * 1_000_000
    if unidad == "ms":
        return valor * 1_000
    return valor


def _calcular_percentil(muestras, percentil):
    """Calcula un percentil por nearest-rank sobre muestras numericas finitas."""
    if not 0 < percentil <= 100:
        raise ValueError("El percentil debe estar entre 0 y 100")
    if not muestras:
        raise ValueError("No hay muestras para calcular el percentil")

    ordenadas = []
    for muestra in muestras:
        if isinstance(muestra, bool) or not isinstance(muestra, (int, float)):
            raise ValueError("Las muestras deben ser numericas")
        muestra = float(muestra)
        if not math.isfinite(muestra) or muestra < 0:
            raise ValueError("Las muestras deben ser finitas y no negativas")
        ordenadas.append(muestra)

    ordenadas.sort()
    rango = max(1, math.ceil((percentil / 100.0) * len(ordenadas)))
    return ordenadas[rango - 1]


def _calcular_p95(muestras):
    """Devuelve el percentil 95 nearest-rank en las unidades de entrada."""
    return _calcular_percentil(muestras, 95)


def _segundos_validos(valor, permite_cero=False):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    valor = float(valor)
    if not math.isfinite(valor):
        return None
    if valor < 0 or (valor == 0 and not permite_cero):
        return None
    return valor


def _extraer_metricas_hyperfine(data):
    resultados = data.get("results") if isinstance(data, dict) else None
    if not isinstance(resultados, list) or not resultados:
        return None

    resultado = resultados[0]
    if not isinstance(resultado, dict):
        return None
    times = resultado.get("times")
    if not isinstance(times, list) or not times:
        return None

    times_us = []
    for muestra in times:
        segundos = _segundos_validos(muestra)
        if segundos is None:
            return None
        times_us.append(_a_microsegundos(segundos, "s"))

    mean_s = _segundos_validos(resultado.get("mean"))
    std_s = _segundos_validos(resultado.get("stddev"), permite_cero=True)
    min_s = _segundos_validos(resultado.get("min"))
    max_s = _segundos_validos(resultado.get("max"))

    mean_us = (
        _a_microsegundos(mean_s, "s")
        if mean_s is not None
        else statistics.fmean(times_us)
    )
    std_us = (
        _a_microsegundos(std_s, "s")
        if std_s is not None
        else statistics.pstdev(times_us)
    )
    min_us = _a_microsegundos(min_s, "s") if min_s is not None else min(times_us)
    max_us = _a_microsegundos(max_s, "s") if max_s is not None else max(times_us)

    runs = resultado.get("runs")
    if isinstance(runs, bool) or not isinstance(runs, int) or runs <= 0:
        runs = len(times_us)

    return {
        "mean_us": mean_us,
        "std_us": std_us,
        "min_us": min_us,
        "max_us": max_us,
        "p95_us": _calcular_p95(times_us),
        "times_us": times_us,
        "runs": runs,
    }


def _ejecutar_hybrid_cmd(
    cmd,
    tv_log=None,
    logs=True,
    timeout=60,
    cancel_token=None,
):
    """Ejecuta hyperfine y devuelve sus metricas parseadas."""
    if logs and tv_log:
        _log(tv_log, f"Ejecutando: {shlex.join(cmd)}")

    inicio = time.time()
    res = run_process(
        cmd,
        timeout=timeout,
        cancel_token=cancel_token,
    )
    elapsed = time.time() - inicio

    if logs and tv_log:
        _log(tv_log, f"Finalizado (exit={res.returncode}) en {elapsed:.2f}s")
        if res.stdout:
            bloque = _formatear_bloque(res.stdout, "STDOUT: ", 30, 20_000)
            if bloque:
                _log(tv_log, bloque)
        if res.stderr:
            bloque = _formatear_bloque(res.stderr, "STDERR: ", 30, 20_000)
            if bloque:
                _log(tv_log, bloque)

    if res.returncode != 0:
        return None

    try:
        indice_json = cmd.index("--export-json") + 1
        json_path = cmd[indice_json]
    except (ValueError, IndexError):
        if logs and tv_log:
            _log(tv_log, "Comando hyperfine sin --export-json valido", es_error=True)
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as archivo:
            metricas = _extraer_metricas_hyperfine(json.load(archivo))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if logs and tv_log:
            _log(tv_log, f"Error leyendo JSON: {exc}", es_error=True)
        return None

    check_cancelled(cancel_token)
    if metricas is None and logs and tv_log:
        _log(tv_log, "JSON de hyperfine sin muestras times validas", es_error=True)
    return metricas


def _resolver_compilador():
    """Resuelve el primer compilador C regular y ejecutable disponible."""
    for nombre in ("cc", "gcc", "clang"):
        candidato = shutil.which(nombre)
        if not candidato:
            continue
        try:
            ruta = os.path.realpath(os.path.abspath(candidato))
            info = os.stat(ruta)
        except (OSError, TypeError, ValueError):
            continue
        if stat.S_ISREG(info.st_mode) and os.access(ruta, os.X_OK):
            return ruta
    return None


def _generar_fuente_compilacion():
    """Genera una unidad C fija y amplia sin datos aleatorios ni temporales."""
    mascara = (1 << 64) - 1
    lineas = [
        "#include <stddef.h>",
        "#include <stdint.h>",
        "#include <stdio.h>",
        "",
        "typedef uint64_t (*kernel_fn)(uint64_t);",
        "",
    ]
    nombres = []
    for indice in range(_COMPILE_FUNCTIONS):
        nombre = f"mix_{indice:03d}"
        nombres.append(nombre)
        constante_a = (0x9E3779B185EBCA87 * (indice + 1)) & mascara
        constante_b = (
            0xC2B2AE3D27D4EB4F ^ (0x165667B19E3779F9 * (indice + 3))
        ) & mascara
        constante_c = (
            0x94D049BB133111EB + (0x2545F4914F6CDD1D * (indice + 5))
        ) & mascara
        lineas.extend(
            [
                f"static uint64_t {nombre}(uint64_t x)",
                "{",
                f"    x ^= UINT64_C({constante_a});",
                f"    x *= UINT64_C({constante_b | 1});",
                "    x ^= x >> 29;",
                f"    x *= UINT64_C({constante_c | 1});",
                "    return x ^ (x >> 32);",
                "}",
                "",
            ]
        )

    lineas.append("static kernel_fn const kernels[] = {")
    for inicio in range(0, len(nombres), 8):
        lineas.append("    " + ", ".join(nombres[inicio : inicio + 8]) + ",")
    lineas.extend(
        [
            "};",
            "",
            "int main(void)",
            "{",
            "    uint64_t state = UINT64_C(1469598103934665603);",
            "    size_t count = sizeof(kernels) / sizeof(kernels[0]);",
            "    for (size_t i = 0; i < count; ++i)",
            "        state ^= kernels[i](state + (uint64_t)i);",
            '    printf("%llu\\n", (unsigned long long)state);',
            "    return 0;",
            "}",
            "",
        ]
    )
    return "\n".join(lineas)


def _crear_carga_compilacion(tmpdir):
    fuente = os.path.join(tmpdir, "reactor_compile_fixture.c")
    salida = os.path.join(tmpdir, "reactor_compile_fixture")
    with open(fuente, "w", encoding="utf-8", newline="\n") as archivo:
        archivo.write(_generar_fuente_compilacion())
    return fuente, salida


def _comandos_compilacion(compilador, fuente, salida):
    limpiar = shlex.join(["rm", "-f", salida])
    compilar = shlex.join(
        [compilador, "-std=c11", "-O2", "-pipe", fuente, "-o", salida]
    )
    return limpiar, compilar


def _detener_proceso_carga(proceso):
    """Detiene solo el grupo de procesos creado para la carga del benchmark."""
    terminate_process(proceso, timeout=3)


def _semilla_estable(*partes):
    datos = "\0".join(str(parte) for parte in partes).encode("utf-8")
    return int.from_bytes(hashlib.sha256(datos).digest()[:8], "big")


def _resultado_dev(tipo, sc_act, modo_act):
    semilla = _semilla_estable(sc_act, tipo)
    base_us = {"fork": 150.0, "compile": 2_000_000.0, "loaded": 500.0}[tipo]
    runs = {"fork": 100, "compile": 20, "loaded": 30}[tipo]
    centro = base_us * (0.9 + (semilla % 200) / 1000.0)
    times_us = [
        centro * (0.85 + ((semilla + indice * 37) % 31) / 100.0)
        for indice in range(runs)
    ]
    mean_us = statistics.fmean(times_us)
    std_us = statistics.pstdev(times_us)
    p95_us = _calcular_p95(times_us)
    fairness = min(1.0, std_us / mean_us) if mean_us > 0 else 1.0

    return {
        "tipo": f"latencia_{tipo}",
        "valor": mean_us,
        "response": p95_us,
        "response_kind": "p95_us",
        "p95": p95_us,
        "fairness": fairness,
        "fairness_kind": "coefficient_of_variation",
        "sched": sc_act,
        "modo": modo_act,
        "mean_us": mean_us,
        "std_us": std_us,
        "min_us": min(times_us),
        "max_us": max(times_us),
        "times_us": times_us,
        "runs": runs,
        "timestamp": time.time(),
    }


def _log_binario_ausente(tv_log, logs, binario):
    if logs and tv_log:
        _log(tv_log, f"Error: {binario} no encontrado en PATH.", es_error=True)


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


def correr_hybrid(
    tipo,
    scx_manager,
    tv_log=None,
    tiempo=5,
    logs=True,
    modo_dev=False,
    cancel_token=None,
):
    """Ejecuta Hyperfine y devuelve p95 real como respuesta menor-es-mejor."""
    check_cancelled(cancel_token)
    tipo_base = _TIPOS_HYBRID.get(tipo)
    if tipo_base is None:
        if logs and tv_log:
            _log(tv_log, f"Tipo de prueba hibrida desconocido: {tipo}", es_error=True)
        return None

    timeout = None
    binario_activo = "scxctl"
    proceso_carga = None

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
            _log(
                tv_log,
                f"INICIANDO: LATENCIA {tipo_base.upper()} ({sc_act} [{modo_act}])",
                True,
            )

        if modo_dev:
            wait_cancelable(cancel_token, 0.5)
            resultado = _resultado_dev(tipo_base, sc_act, modo_act)
            check_cancelled(cancel_token)
            return resultado

        if shutil.which("hyperfine") is None:
            _log_binario_ausente(tv_log, logs, "hyperfine")
            return None
        if tipo_base == "loaded" and shutil.which("stress-ng") is None:
            _log_binario_ausente(tv_log, logs, "stress-ng")
            return None

        compilador = None
        if tipo_base == "compile":
            compilador = _resolver_compilador()
            if compilador is None:
                if logs and tv_log:
                    _log(
                        tv_log,
                        "No se puede medir compilacion: no hay un compilador C "
                        "regular y ejecutable en PATH (cc/gcc/clang).",
                        es_error=True,
                    )
                return None

        runs, timeout = _planificar_hyperfine(tipo_base, tiempo)

        with tempfile.TemporaryDirectory(prefix="scxctl_hybrid_") as tmpdir:
            json_path = os.path.join(tmpdir, "result.json")

            if tipo_base == "fork":
                cmd = [
                    "hyperfine",
                    "--warmup", "3",
                    "-n", "fork-exec",
                    "-r", str(runs),
                    "-N",
                    "--export-json", json_path,
                    "/bin/true",
                ]
                nombre_test = "fork+exec latency"
            elif tipo_base == "compile":
                fuente, salida = _crear_carga_compilacion(tmpdir)
                preparar, compilar = _comandos_compilacion(
                    compilador,
                    fuente,
                    salida,
                )
                cmd = [
                    "hyperfine",
                    "--warmup", "2",
                    "-n", "deterministic-c-compile",
                    "-r", str(runs),
                    "--export-json", json_path,
                    "--prepare", preparar,
                    compilar,
                ]
                nombre_test = "compilacion C determinista"
            else:
                cpus = os.cpu_count() or 4
                timeout_carga = _calcular_timeout_carga(timeout)
                cmd = [
                    "hyperfine",
                    "--warmup", "2",
                    "-n", "foreground-under-load",
                    "-r", str(runs),
                    "--export-json", json_path,
                    "python3 -c \"import hashlib;"
                    "[hashlib.sha256(str(i).encode()).hexdigest() for i in range(5000)]\"",
                ]
                nombre_test = "interactividad bajo carga"

                binario_activo = "stress-ng"
                proceso_carga = start_process(
                    [
                        "stress-ng",
                        "--cpu", str(cpus),
                        "--timeout", f"{timeout_carga}s",
                        "--quiet",
                    ],
                    cancel_token=cancel_token,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                wait_cancelable(cancel_token, 0.2)
                if proceso_carga.poll() is not None:
                    if logs and tv_log:
                        _log(tv_log, "stress-ng termino antes de iniciar la medicion", es_error=True)
                    return None

            if logs and tv_log:
                _log(tv_log, f"Test: {nombre_test}")

            binario_activo = "hyperfine"
            carga_activa = True
            try:
                metricas = _ejecutar_hybrid_cmd(
                    cmd,
                    tv_log,
                    logs,
                    timeout,
                    cancel_token,
                )
                if tipo_base == "loaded" and proceso_carga.poll() is not None:
                    carga_activa = False
            finally:
                _detener_proceso_carga(proceso_carga)
                proceso_carga = None

        check_cancelled(cancel_token)
        estado_final = _capturar_estado_estricto(scx_manager, cancel_token)
        if estado_final != estado_inicial:
            _registrar_cambio_estado(
                tv_log,
                logs,
                estado_inicial,
                estado_final,
            )
            return None

        if not carga_activa:
            if logs and tv_log:
                _log(tv_log, "stress-ng termino durante la medicion", es_error=True)
            return None
        if not metricas:
            if logs and tv_log:
                _log(tv_log, "Resultado invalido (sin muestras de Hyperfine)", es_error=True)
            return None
        if tipo_base in ("compile", "loaded") and len(metricas["times_us"]) < 20:
            if logs and tv_log:
                _log(
                    tv_log,
                    f"Resultado invalido ({tipo_base} requiere al menos 20 muestras)",
                    es_error=True,
                )
            return None

        mean_us = metricas["mean_us"]
        std_us = metricas["std_us"]
        p95_us = metricas["p95_us"]
        fairness = min(1.0, std_us / mean_us) if mean_us > 0 else 1.0

        if logs and tv_log:
            _log(
                tv_log,
                f"Media: {mean_us:.1f} us | p95: {p95_us:.1f} us | "
                f"Desv: {std_us:.1f} us | Runs: {metricas['runs']}",
            )
            _log(
                tv_log,
                f"Rango: {metricas['min_us']:.1f} - {metricas['max_us']:.1f} us",
            )
            _log(tv_log, f"Scheduler: {sc_act} | Modo: {modo_act}")
            _log(tv_log, "-" * 50)

        resultado = {
            "tipo": f"latencia_{tipo_base}",
            "valor": mean_us,
            "response": p95_us,
            "response_kind": "p95_us",
            "p95": p95_us,
            "fairness": fairness,
            "fairness_kind": "coefficient_of_variation",
            "sched": sc_act,
            "modo": modo_act,
            "mean_us": mean_us,
            "std_us": std_us,
            "min_us": metricas["min_us"],
            "max_us": metricas["max_us"],
            "times_us": metricas["times_us"],
            "runs": metricas["runs"],
            "timestamp": time.time(),
        }
        check_cancelled(cancel_token)
        return resultado

    except OperationCancelled:
        raise
    except FileNotFoundError as exc:
        binario = os.path.basename(exc.filename) if exc.filename else binario_activo
        _log_binario_ausente(tv_log, logs, binario)
        return None
    except subprocess.TimeoutExpired:
        if logs and tv_log:
            limite = f" ({timeout}s)" if timeout is not None else ""
            _log(tv_log, f"Prueba excedio el tiempo limite{limite}.", es_error=True)
        return None
    except Exception as exc:
        if logs and tv_log:
            _log(tv_log, f"Error: {exc}", es_error=True)
        return None
    finally:
        _detener_proceso_carga(proceso_carga)
