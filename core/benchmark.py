"""
Motor de Benchmarking basado en stress-ng.
Reemplaza sysbench con métricas específicas para evaluación de schedulers.

stress-ng ofrece:
  - Salida YAML parseable (sin regex frágiles)
  - Métricas de context-switch nativas del kernel
  - Stressors diseñados para evaluar el scheduler (switch, mutex, cpu)
"""

import os
import subprocess
import time
import tempfile

from core.constantes import SISTEMA_BASE

from utils.logging import log as _log, log_subprocess_output, log_error_benchmark
from utils.helpers import limpiar_texto as _limpiar_texto, resultado_base


def _parsear_yaml_simple(contenido):
    """Parser YAML minimalista para la sección 'metrics:' de stress-ng.
    
    Evita depender de PyYAML. Solo extrae los campos numéricos que necesitamos.
    """
    metricas = {}
    en_metricas = False
    
    for linea in contenido.splitlines():
        stripped = linea.strip()
        
        if stripped == "metrics:":
            en_metricas = True
            continue
        
        if en_metricas and stripped.startswith("- stressor:"):
            metricas["stressor"] = stripped.split(":", 1)[1].strip()
            continue
        
        if en_metricas and ":" in stripped and not stripped.startswith("-"):
            # Si encontramos una sección nueva de nivel superior, dejamos de parsear
            if not stripped.startswith(" ") and not linea.startswith("      "):
                if stripped not in ("...",):
                    # Puede ser el fin de la sección metrics
                    if not any(c.isdigit() for c in stripped.split(":")[1]) and "'" not in stripped:
                        break
            
            clave, valor = stripped.split(":", 1)
            clave = clave.strip()
            valor = valor.strip().strip("'\"")
            
            try:
                metricas[clave] = float(valor)
            except ValueError:
                metricas[clave] = valor
    
    return metricas


def correr_benchmark(tipo, scx_manager, tv_log=None, tiempo=5, logs=True, modo_dev=False):
    """Ejecuta una prueba de rendimiento con stress-ng.
    
    Args:
        tipo: "cpu" (latencia), "threads" (multitarea), "memory" (eficiencia)
        scx_manager: Instancia de GestorScx para consultar el estado
        tv_log: TextView para logging (puede ser None)
        tiempo: Duración en segundos
        logs: Si True, escribe en el log
        modo_dev: Si True, genera datos simulados
        
    Returns:
        dict con: tipo, valor, p95, fairness, sched, modo — o None si falla.
    """
    try:
        time.sleep(0.3)
        sc_act, modo_act = scx_manager.obtener_estado()
        sc_act = sc_act or SISTEMA_BASE
        modo_act = modo_act or "default"
        
        if logs and tv_log:
            _log(tv_log, f"INICIANDO: {tipo.upper()} ({sc_act} [{modo_act}])", nivel="title")
        
        cores = os.cpu_count() or 4
        
        # ── Modo Desarrollador: Datos simulados ──
        if modo_dev:
            time.sleep(0.5)
            seed = hash((sc_act, tipo)) % 1000
            base = {
                "cpu": {"val": 8500, "p95": 5.0, "fair": 0.08},
                "threads": {"val": 10000, "p95": 8.0, "fair": 0.05},
                "memory": {"val": 12000, "p95": 3.5, "fair": 0.12},
                "disk": {"val": 6000, "p95": 12.0, "fair": 0.15},
            }.get(tipo, {"val": 9000, "p95": 6.0, "fair": 0.10})
            factor = 0.9 + (seed % 200) / 1000.0
            return resultado_base(scx_manager, tipo,
                base["val"] * factor,
                base["p95"] * (0.8 + (seed % 40) / 100.0),
                base["fair"] * (0.9 + (seed % 20) / 100.0), sc_act, modo_act)
        
        # ── Construir Comando stress-ng ──
        yaml_path = tempfile.mktemp(suffix=".yaml", prefix="scxctl_bench_")
        
        # 1. Latencia (Context Switching): Mide la velocidad del scheduler para cambiar entre tareas
        _TEMP_PATH = ["--temp-path", "/tmp"]
        if tipo == "cpu":
            cmd = [
                "stress-ng",
                "--switch", str(cores),
                "--timeout", f"{tiempo}s",
                "--metrics-brief",
                "--yaml", yaml_path,
                *_TEMP_PATH,
            ]
        
        # 2. Multitarea (Carga CPU): Simula trabajo real de CPU con operaciones de matrices
        elif tipo == "threads":
            cmd = [
                "stress-ng",
                "--cpu", str(cores),
                "--cpu-method", "matrixprod",
                "--timeout", f"{tiempo}s",
                "--metrics-brief",
                "--yaml", yaml_path,
                *_TEMP_PATH,
            ]
        
        # 3. Eficiencia (Mutex/Contención): Muchos hilos compiten por recursos compartidos
        elif tipo == "memory":
            cmd = [
                "stress-ng",
                "--mutex", str(cores * 2),
                "--timeout", f"{tiempo}s",
                "--metrics-brief",
                "--yaml", yaml_path,
                *_TEMP_PATH,
            ]
        else:
            if logs and tv_log:
                _log(tv_log, f"Tipo de prueba desconocido: {tipo}", nivel="error")
            return None
        
        # ── Ejecutar ──
        # Log del comando y tiempo de ejecución
        start_t = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=tiempo + 10)
        elapsed = time.time() - start_t
        log_subprocess_output(tv_log, logs, res, elapsed, label="Ejecutando comando", max_lines=200)
        
        if res.returncode != 0 and "passed:" not in res.stderr:
            if logs and tv_log:
                _log(tv_log, f"Error en stress-ng: {res.stderr.strip()}", nivel="error")
            return None
        
        # ── Parsear Resultados YAML ──
        metricas = {}
        contenido = ""
        if os.path.exists(yaml_path):
            try:
                with open(yaml_path, 'r') as f:
                    contenido = f.read()
                metricas = _parsear_yaml_simple(contenido)
                if logs and tv_log:
                    for k, v in metricas.items():
                        if isinstance(v, float):
                            _log(tv_log, f"  {k:35s} {v:>12,.2f}")
                        else:
                            _log(tv_log, f"  {k:35s} {str(v):>12s}")
            except (OSError, ValueError) as e:
                if logs and tv_log:
                    _log(tv_log, f"Error leyendo YAML: {e}", nivel="error")
            finally:
                try:
                    os.unlink(yaml_path)
                except OSError:
                    pass
        
        # ── Extraer Métricas Clave ──
        bogo_ops = metricas.get("bogo-ops", 0)
        ops_real = metricas.get("bogo-ops-per-second-real-time", 0)
        ops_usr_sys = metricas.get("bogo-ops-per-second-usr-sys-time", 0)
        cpu_usage = metricas.get("cpu-usage-per-instance", 0)
        wall_time = metricas.get("wall-clock-time", tiempo)
        ns_per_switch = metricas.get("nanosecs-per-context-switch-pipe-method", 0)
        ns_per_mutex = metricas.get("nanosecs-per-mutex", 0)
        
        # Valor principal (ops/s real-time)
        valor = ops_real if ops_real > 0 else (bogo_ops / wall_time if wall_time > 0 else 0)

        # Log intermedio de variables extraídas
        if logs and tv_log:
            _log(tv_log, f"  bogo_ops:        {bogo_ops:>12,.2f}")
            _log(tv_log, f"  ops_real:        {ops_real:>12,.2f}")
            _log(tv_log, f"  ops_usr_sys:     {ops_usr_sys:>12,.2f}")
            _log(tv_log, f"  cpu_usage:       {cpu_usage:>12.1f}%")
            _log(tv_log, f"  wall_time:       {wall_time:>12.2f}s")
            _log(tv_log, f"  ns_per_switch:   {ns_per_switch:>12,.2f}")
        
        # P95 equivalente: latencia directa cuando está disponible
        if tipo == "cpu" and ns_per_switch > 0:
            p95 = ns_per_switch / 1000.0
        elif tipo == "memory" and ns_per_mutex > 0:
            p95 = ns_per_mutex / 1000.0
        elif ops_usr_sys > 0 and ops_real > 0:
            p95 = (ops_usr_sys / ops_real - 1.0) * 100.0 if ops_usr_sys > ops_real else 1.0
        else:
            p95 = 1.0
        
        # Waste: ratio de desperdicio de CPU (0.0 = uso perfecto, >0 = desperdicio)
        if cpu_usage > 0:
            waste = max(0.0, (100.0 - cpu_usage) / 100.0)
        else:
            waste = 0.5
        
        # Protección contra datos inválidos
        if valor <= 0:
            if logs and tv_log:
                _log(tv_log, "Resultado inválido (0 operaciones)", nivel="error")
            return None
        
        # ── Log de Resultados ──
        if logs and tv_log:
            # Mostrar resumen y algunas líneas útiles de la salida
            un_log = "ops/s"
            val_log = valor
            if tipo == "cpu":
                val_log = 1000.0 / max(0.01, p95) if p95 > 0 else valor
                un_log = "pts (Agilidad)"
            elif tipo == "memory":
                val_log = valor / max(0.1, p95) if p95 > 0 else valor
                un_log = "pts (Eficacia)"

            _log(tv_log, f"RESUMEN: {val_log:,.2f} {un_log} | Latencia: {p95:.2f}µs | Eficiencia CPU: {cpu_usage:.1f}%")
            _log(tv_log, f"Waste: {waste:.3f} | Scheduler: {sc_act} | Modo: {modo_act}")
            _log(tv_log, "-" * 50)
        
        resultado = resultado_base(scx_manager, tipo, valor, p95, waste, sc_act, modo_act)
        resultado.update({
            "metrics": metricas, "raw_yaml": contenido,
            "ops_real": ops_real, "ops_usr_sys": ops_usr_sys,
            "cpu_usage": cpu_usage, "wall_time": wall_time,
            "ns_per_switch": ns_per_switch, "cores": cores,
        })
        return resultado
    
    except FileNotFoundError:
        if logs and tv_log:
            _log(tv_log, "Error: stress-ng no encontrado. Instálalo con tu gestor de paquetes (ej: 'sudo eopkg install stress-ng').", nivel="error")
        return None
    except subprocess.TimeoutExpired:
        if logs and tv_log:
            _log(tv_log, "Prueba excedió el tiempo límite.", nivel="error")
        return None
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        if logs and tv_log:
            _log(tv_log, f"Error: {e}", nivel="error")
        return None

