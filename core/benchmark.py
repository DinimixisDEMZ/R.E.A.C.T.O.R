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
import random

from utils.helpers import log as _log, limpiar_texto as _limpiar_texto


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
        scx_manager: Instancia de ScxManager para consultar el estado
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
        sc_act = sc_act or "Sistema Base"
        modo_act = modo_act or "default"
        
        if logs and tv_log:
            _log(tv_log, f"INICIANDO: {tipo.upper()} ({sc_act} [{modo_act}])", True)
        
        cores = os.cpu_count() or 4
        
        # ── Modo Desarrollador: Datos simulados ──
        if modo_dev:
            time.sleep(0.5)
            fake_val = random.uniform(5000, 15000)
            fake_p95 = random.uniform(1.5, 25.0)
            fake_fair = random.uniform(0.01, 0.2)
            return {
                "tipo": tipo,
                "valor": fake_val,
                "p95": fake_p95,
                "fairness": fake_fair,
                "sched": sc_act if sc_act != "Sistema Base" else "scx_rusty",
                "modo": modo_act
            }
        
        # ── Construir Comando stress-ng ──
        yaml_path = tempfile.mktemp(suffix=".yaml", prefix="scxctl_bench_")
        
        # 1. Latencia (Context Switching): Mide la velocidad del scheduler para cambiar entre tareas
        if tipo == "cpu":
            cmd = [
                "stress-ng",
                "--switch", str(cores),
                "--timeout", f"{tiempo}s",
                "--metrics-brief",
                "--yaml", yaml_path
            ]
        
        # 2. Multitarea (Carga CPU): Simula trabajo real de CPU con operaciones de matrices
        elif tipo == "threads":
            cmd = [
                "stress-ng",
                "--cpu", str(cores),
                "--cpu-method", "matrixprod",
                "--timeout", f"{tiempo}s",
                "--metrics-brief",
                "--yaml", yaml_path
            ]
        
        # 3. Eficiencia (Mutex/Contención): Muchos hilos compiten por recursos compartidos
        elif tipo == "memory":
            cmd = [
                "stress-ng",
                "--mutex", str(cores * 2),
                "--timeout", f"{tiempo}s",
                "--metrics-brief",
                "--yaml", yaml_path
            ]
        else:
            if logs and tv_log:
                _log(tv_log, f"Tipo de prueba desconocido: {tipo}", es_error=True)
            return None
        
        # ── Ejecutar ──
        # Log del comando y tiempo de ejecución
        if logs and tv_log:
            _log(tv_log, f"Ejecutando comando: {' '.join(cmd)}")
        start_t = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=tiempo + 10)
        elapsed = time.time() - start_t
        if logs and tv_log:
            _log(tv_log, f"Comando finalizado (exit={res.returncode}) en {elapsed:.2f}s")
            if res.stdout:
                limpia_out = _limpiar_texto(res.stdout)
                for linea in (limpia_out or "").splitlines()[:200]:
                    _log(tv_log, f"STDOUT: {linea}")
            if res.stderr:
                limpia_err = _limpiar_texto(res.stderr)
                for linea in (limpia_err or "").splitlines()[:200]:
                    _log(tv_log, f"STDERR: {linea}")
        
        if res.returncode != 0 and "passed:" not in res.stderr:
            if logs and tv_log:
                _log(tv_log, f"Error en stress-ng: {res.stderr.strip()}", es_error=True)
            return None
        
        # ── Parsear Resultados YAML ──
        metricas = {}
        contenido = ""
        if os.path.exists(yaml_path):
            try:
                with open(yaml_path, 'r') as f:
                    contenido = f.read()
                if logs and tv_log:
                    _log(tv_log, "Contenido YAML (limpio, primeras 2000 chars):")
                    preview = _limpiar_texto(contenido)[:2000]
                    for linea in preview.splitlines()[:200]:
                        _log(tv_log, linea)
                metricas = _parsear_yaml_simple(contenido)
                if logs and tv_log:
                    _log(tv_log, f"Métricas parseadas: {metricas}")
            except Exception as e:
                if logs and tv_log:
                    _log(tv_log, f"Error leyendo YAML: {e}", es_error=True)
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
        
        # Valor principal (ops/s real-time)
        valor = ops_real if ops_real > 0 else (bogo_ops / wall_time if wall_time > 0 else 0)

        # Log intermedio de variables extraídas
        if logs and tv_log:
            _log(tv_log, f"Intermedios: bogo_ops={bogo_ops}, ops_real={ops_real}, ops_usr_sys={ops_usr_sys}, cpu_usage={cpu_usage}, wall_time={wall_time}, ns_per_switch={ns_per_switch}")
        
        # P95 equivalente: Para context switch usamos ns_per_switch, para otros estimamos
        if tipo == "cpu" and ns_per_switch > 0:
            p95 = ns_per_switch / 1000.0  # Convertir ns → µs para consistencia
        elif ops_usr_sys > 0 and ops_real > 0:
            # Ratio de overhead: cuánto tiempo se pierde en system vs trabajo real
            p95 = (ops_usr_sys / ops_real - 1.0) * 100.0 if ops_usr_sys > ops_real else 1.0
        else:
            p95 = 1.0
        
        # Fairness: Eficiencia del CPU (100% = perfecta utilización)
        # Invertido: fairness=0 es perfecto, >0 indica desperdicio
        if cpu_usage > 0:
            fairness = max(0.0, (100.0 - cpu_usage) / 100.0)
        else:
            fairness = 0.5
        
        # Protección contra datos inválidos
        if valor <= 0:
            if logs and tv_log:
                _log(tv_log, "Resultado inválido (0 operaciones)", es_error=True)
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
            _log(tv_log, f"Fairness: {fairness:.3f} | Scheduler: {sc_act} | Modo: {modo_act}")
            _log(tv_log, "-" * 50)
        
        # Añadir datos crudos y metadatos al resultado para análisis posterior
        resultado = {
            "tipo": tipo,
            "valor": valor,
            "p95": p95,
            "fairness": fairness,
            "sched": sc_act,
            "modo": modo_act,
            "metrics": metricas,
            "raw_yaml": contenido,
            "ops_real": ops_real,
            "ops_usr_sys": ops_usr_sys,
            "cpu_usage": cpu_usage,
            "wall_time": wall_time,
            "ns_per_switch": ns_per_switch,
            "cores": cores,
            "timestamp": time.time()
        }

        return resultado
    
    except FileNotFoundError:
        if logs and tv_log:
            _log(tv_log, "Error: stress-ng no encontrado. Instálalo con tu gestor de paquetes (ej: 'sudo eopkg install stress-ng').", es_error=True)
        return None
    except subprocess.TimeoutExpired:
        if logs and tv_log:
            _log(tv_log, "Prueba excedió el tiempo límite.", es_error=True)
        return None
    except Exception as e:
        if logs and tv_log:
            _log(tv_log, f"Error: {e}", es_error=True)
        return None
