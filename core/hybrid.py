"""
Motor de Benchmarking basado en hyperfine.
Mide latencia real de fork+exec y compilación paralela.

hyperfine ofrece:
  - Estadísticas rigurosas (media, desviación, percentiles)
  - Warmup automático para descartar cold starts
  - Múltiples runs con intervalos de confianza
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import json

from utils.helpers import log as _log, limpiar_texto as _limpiar_texto


def _parsear_hybrid_output(stdout):
    """Extrae tiempo medio y desviación estándar de la salida de hyperfine.
    
    Busca líneas como:
      Time (mean ± σ):     340.0 µs ± 140.5 µs
      Time (mean ± σ):       1.234 s ±   0.056 s
    
    Returns:
        dict con mean_us, std_us, min_us, max_us — o None si falla.
    """
    resultado = {}
    
    for linea in stdout.splitlines():
        m = re.search(r'Time\s*\(mean\s*±\s*[σs]\):\s*([\d.]+)\s*(µs|ms|s)\s*±\s*([\d.]+)\s*(µs|ms|s)', linea)
        if m:
            resultado['mean'] = _a_microsegundos(float(m.group(1)), m.group(2))
            resultado['std'] = _a_microsegundos(float(m.group(3)), m.group(4))
        
        m2 = re.search(r'Range\s*\(min\s*[.…]+\s*max\):\s*([\d.]+)\s*(µs|ms|s)\s*[.…]+\s*([\d.]+)\s*(µs|ms|s)', linea)
        if m2:
            resultado['min'] = _a_microsegundos(float(m2.group(1)), m2.group(2))
            resultado['max'] = _a_microsegundos(float(m2.group(3)), m2.group(4))
        
        m3 = re.search(r'(\d+)\s+runs', linea)
        if m3:
            resultado['runs'] = int(m3.group(1))
    
    return resultado if resultado.get('mean') is not None else None


def _a_microsegundos(valor, unidad):
    """Convierte un valor con unidad a microsegundos."""
    if unidad == 'µs':
        return valor
    elif unidad == 'ms':
        return valor * 1000.0
    elif unidad == 's':
        return valor * 1_000_000.0
    return valor


def _ejecutar_hybrid_cmd(cmd, tv_log=None, logs=True, timeout=60):
    """Ejecuta un comando hyperfine y devuelve (resultado_dict, elapsed)."""
    if logs and tv_log:
        _log(tv_log, f"Ejecutando: {' '.join(cmd)}")
    
    start_t = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    elapsed = time.time() - start_t
    
    if logs and tv_log:
        _log(tv_log, f"Finalizado (exit={res.returncode}) en {elapsed:.2f}s")
        if res.stdout:
            for linea in _limpiar_texto(res.stdout).splitlines()[:30]:
                _log(tv_log, f"STDOUT: {linea}")
        if res.stderr:
            for linea in _limpiar_texto(res.stderr).splitlines()[:30]:
                _log(tv_log, f"STDERR: {linea}")
    
    if res.returncode != 0:
        return None, elapsed
    
    # Parsear JSON exportado — buscar la ruta en los args del comando
    metricas = {}
    json_path = None
    for i, arg in enumerate(cmd):
        if arg == "--export-json" and i + 1 < len(cmd):
            json_path = cmd[i + 1]
            break
    
    if json_path:
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
                if 'results' in data and len(data['results']) > 0:
                    r = data['results'][0]
                    metricas = {
                        'mean_us': _a_microsegundos(r.get('mean', 0), 's'),
                        'std_us': _a_microsegundos(r.get('stddev', 0), 's'),
                        'min_us': _a_microsegundos(r.get('min', 0), 's'),
                        'max_us': _a_microsegundos(r.get('max', 0), 's'),
                        'runs': r.get('runs', 0),
                    }
        except Exception as e:
            if logs and tv_log:
                _log(tv_log, f"Error leyendo JSON: {e}", es_error=True)
    
    return metricas, elapsed


def correr_hybrid(tipo, scx_manager, tv_log=None, tiempo=5, logs=True, modo_dev=False):
    """Ejecuta una prueba de latencia con hyperfine.
    
    Args:
        tipo: "fork" (fork+exec), "compile" (compilación paralela), "loaded" (latencia bajo carga)
        scx_manager: Instancia de ScxManager
        tv_log: TextView para logging
        tiempo: No usado directamente (hyperfine maneja sus propios tiempos)
        logs: Si True, escribe en el log
        modo_dev: Si True, genera datos simulados
    
    Returns:
        dict con: tipo, valor (media µs), p95 (std µs), fairness, sched, modo — o None.
    """
    try:
        time.sleep(0.3)
        sc_act, modo_act = scx_manager.obtener_estado()
        sc_act = sc_act or "Sistema Base"
        modo_act = modo_act or "default"
        
        if logs and tv_log:
            _log(tv_log, f"INICIANDO: LATENCIA {tipo.upper()} ({sc_act} [{modo_act}])", True)
        
        # ── Modo Desarrollador ──
        if modo_dev:
            import random
            time.sleep(0.5)
            fake_val = random.uniform(50, 500)
            fake_std = random.uniform(20, 200)
            return {
                "tipo": f"latencia_{tipo}",
                "valor": fake_val,
                "p95": fake_std,
                "fairness": random.uniform(0.01, 0.15),
                "sched": sc_act if sc_act != "Sistema Base" else "scx_rusty",
                "modo": modo_act,
                "mean_us": fake_val,
                "std_us": fake_std,
                "runs": 100
            }
        
        # ── Construir Comando hyperfine ──
        tmpdir = tempfile.mkdtemp(prefix="scxctl_")
        json_path = os.path.join(tmpdir, "result.json")
        
        if tipo == "fork":
            # Fork+exec: rápido y preciso
            cmd = [
                "hyperfine",
                "--warmup", "3",
                "-n", "fork-exec",
                "-r", "100",
                "--export-json", json_path,
                "/bin/true"
            ]
            nombre_test = "fork+exec latency"
            timeout = 30
        
        elif tipo == "compile":
            # Compilación: warmup reduce cold cache, 3 runs bastan
            cmd = [
                "hyperfine",
                "--warmup", "1",
                "-n", "parallel-make",
                "-r", "3",
                "--export-json", json_path,
                "make -C /tmp/rt-tests clean 2>/dev/null && make -C /tmp/rt-tests cyclictest -j$(nproc) 2>/dev/null"
            ]
            nombre_test = "compilación paralela"
            timeout = 120
        
        elif tipo == "loaded":
            # Interactividad bajo carga: mide capacidad del scheduler de priorizar
            # tareas foreground cuando la CPU está saturada por background workers.
            cmd = [
                "hyperfine",
                "--warmup", "1",
                "-n", "foreground-under-load",
                "-r", "15",
                "--export-json", json_path,
                "-p", "stress-ng --cpu $(nproc) --timeout 1s --quiet &",
                "python3 -c \"import hashlib;[hashlib.sha256(str(i).encode()).hexdigest() for i in range(5000)]\""
            ]
            nombre_test = "interactividad bajo carga"
            timeout = 45
        
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)
            if logs and tv_log:
                _log(tv_log, f"Tipo de prueba híbrida desconocido: {tipo}", es_error=True)
            return None
        
        # ── Ejecutar ──
        if logs and tv_log:
            _log(tv_log, f"Test: {nombre_test}")
        
        try:
            metricas, elapsed = _ejecutar_hybrid_cmd(cmd, tv_log, logs, timeout)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        
        if not metricas or metricas.get('mean_us', 0) <= 0:
            if logs and tv_log:
                _log(tv_log, "Resultado inválido (media <= 0)", es_error=True)
            return None
        
        mean_us = metricas['mean_us']
        std_us = metricas.get('std_us', 0)
        runs = metricas.get('runs', 0)
        
        # ── Calcular fairness ──
        cv = (std_us / mean_us) if mean_us > 0 else 1.0
        fairness = min(1.0, cv)
        
        # ── Log de Resultados ──
        if logs and tv_log:
            _log(tv_log, f"Media: {mean_us:.1f} µs | Desv: {std_us:.1f} µs | Runs: {runs}")
            _log(tv_log, f"Rango: {metricas.get('min_us', 0):.1f} - {metricas.get('max_us', 0):.1f} µs")
            _log(tv_log, f"RESUMEN: {mean_us:.1f} µs (media) | σ {std_us:.1f} µs | Consistencia: {(1-fairness)*100:.1f}%")
            _log(tv_log, f"Scheduler: {sc_act} | Modo: {modo_act}")
            _log(tv_log, "-" * 50)
        
        return {
            "tipo": f"latencia_{tipo}",
            "valor": mean_us,
            "p95": std_us,
            "fairness": fairness,
            "sched": sc_act,
            "modo": modo_act,
            "mean_us": mean_us,
            "std_us": std_us,
            "min_us": metricas.get('min_us', 0),
            "max_us": metricas.get('max_us', 0),
            "runs": runs,
            "timestamp": time.time()
        }
    
    except FileNotFoundError:
        if logs and tv_log:
            _log(tv_log, "Error: hyperfine no encontrado. Instálalo con: sudo eopkg install hyperfine", es_error=True)
        return None
    except subprocess.TimeoutExpired:
        if logs and tv_log:
            _log(tv_log, f"Prueba excedió el tiempo límite ({timeout}s).", es_error=True)
        return None
    except Exception as e:
        if logs and tv_log:
            _log(tv_log, f"Error: {e}", es_error=True)
        return None
