"""
Motor de puntuación y diagnóstico.
Calcula scores ponderados con media armónica para evaluar schedulers.
"""

HYBRID_TYPES = {"fork", "compile", "loaded"}


def calcular_score_categorias(data, mejores, pesos=(0.45, 0.45, 0.10)):
    """Calcula el score de un scheduler por categoría.
    
    Args:
        data: dict {tipo: {val, p95, fair}} con los datos brutos del scheduler
        mejores: dict {tipo: {max_val, min_p95}} con los mejores valores globales
        pesos: tuple (peso_potencia, peso_respuesta, peso_fluidez)
        
    Returns:
        tuple: (cat_scores, potencia_acc, respuesta_acc, fluidez_acc)
    """
    w_pot, w_lat, w_flu = pesos
    cat_scores = []
    v_p_acc, v_l_acc, v_f_acc = 0, 0, 0

    for t_tipo, m in mejores.items():
        if t_tipo in data:
            # Normalizar entrada para usar claves consistentes
            entrada = _normalizar_entrada(data[t_tipo])
            
            if t_tipo.startswith("latencia_"):
                # Hyperfine: menor es mejor
                r_pot = (m['min_val'] / entrada['val']) if entrada['val'] > 0 else 0
                r_lat = (m['min_p95'] / entrada['p95']) if entrada['p95'] > 0 else 0
            else:
                # stress-ng: mayor es mejor
                r_pot = (entrada['val'] / m['max_val']) if m['max_val'] > 0 else 0
                r_lat = (m['min_p95'] / entrada['p95']) if entrada['p95'] > 0 else 0
            
            r_flu = max(0.01, 1.0 - entrada['fair'])

            s_cat = (r_pot * w_pot) + (r_lat * w_lat) + (r_flu * w_flu)
            cat_scores.append(max(0.01, s_cat))
            v_p_acc += r_pot
            v_l_acc += r_lat
            v_f_acc += r_flu

    return cat_scores, v_p_acc, v_l_acc, v_f_acc


def media_armonica(valores):
    """Calcula la media armónica de una lista de valores.
    
    Penaliza mediciones con "valles" (valores bajos), 
    encontrando el mejor balance real.
    """
    if not valores:
        return 0
    return len(valores) / sum(1.0 / max(0.01, x) for x in valores)


def calcular_mejores(brutos, tipos=("cpu", "threads", "memory")):
    """Calcula los mejores valores de referencia por tipo de prueba.
    
    Args:
        brutos: dict {scheduler: {tipo: {val, p95, fair}}}
        tipos: tuple con los tipos de prueba
        
    Returns:
        dict {tipo: {max_val, min_val, min_p95}}
    """
    # Normalizar entradas para extraer 'val' y 'p95' de forma consistente
    mejores = {}
    for t_tipo in tipos:
        vals = []
        p95s = []
        for sc, entry in brutos.items():
            if t_tipo not in entry:
                continue
            item = entry[t_tipo]
            # Compatibilidad: aceptar 'valor' o 'val'
            val = item.get('valor') or item.get('val') or (item.get('metrics') or {}).get('bogo-ops-per-second-real-time') or 0
            p95 = item.get('p95')
            if not p95:
                # intentar derivar de métricas crudas
                m = item.get('metrics') or {}
                ns = m.get('nanosecs-per-context-switch-pipe-method') or m.get('nanosecs-per-context-switch') or 0
                if ns:
                    p95 = ns / 1000.0
                else:
                    p95 = 1.0

            vals.append(val)
            if p95 and p95 > 0:
                p95s.append(p95)

        if vals:
            mejores[t_tipo] = {
                'max_val': max(vals),
                'min_val': min(vals),
                'min_p95': min(p95s) if p95s else 1
            }
    return mejores


def _normalizar_entrada(item):
    """Convierte una entrada de benchmark a un dict con claves estables.

    Devuelve: {val, p95, fairness, throughput_per_core, efficiency, cores, raw_metrics}
    """
    m = item.get('metrics') or {}
    val = item.get('valor') or item.get('val') or m.get('bogo-ops-per-second-real-time') or 0
    p95 = item.get('p95')
    if not p95:
        ns = m.get('nanosecs-per-context-switch-pipe-method') or m.get('nanosecs-per-context-switch') or 0
        p95 = ns / 1000.0 if ns else 1.0

    fairness = item.get('fairness')
    if fairness is None:
        cpu_usage = item.get('cpu_usage') or m.get('cpu-usage-per-instance') or 0
        fairness = max(0.0, (100.0 - cpu_usage) / 100.0) if cpu_usage else 0.5

    cores = item.get('cores') or m.get('cpus') or 1
    throughput_per_core = (val / cores) if cores else val

    ops_usr_sys = item.get('ops_usr_sys') or m.get('bogo-ops-per-second-usr-sys-time') or 0
    efficiency = (val / ops_usr_sys) if ops_usr_sys and ops_usr_sys > 0 else val

    return {
        'val': val,
        'p95': p95,
        'fair': fairness,
        'throughput_per_core': throughput_per_core,
        'efficiency': efficiency,
        'cores': cores,
        'raw': m
    }


def calcular_scores_finales(brutos, pesos=(0.45, 0.45, 0.10), tipos=None):
    """Calcula los scores finales de todos los schedulers.
    
    Args:
        brutos: dict {scheduler: {tipo: {val, p95, fair}}}
        pesos: tuple (potencia, respuesta, fluidez)
        tipos: tuple con los tipos de prueba a considerar (None = automático)
        
    Returns:
        dict {scheduler: {score, pot, resp, flu}}
    """
    if tipos is None:
        # Detectar automáticamente todos los tipos presentes
        todos_los_tipos = set()
        for sc_data in brutos.values():
            todos_los_tipos.update(sc_data.keys())
        tipos = tuple(todos_los_tipos)
    
    # Calcular mejores sobre los datos en bruto (compatibilidad incluida)
    mejores = calcular_mejores(brutos, tipos)
    scores = {}

    for sc, data in brutos.items():
        # permitir que `data` contenga entradas enriquecidas (benchmark retornado)
        cat_scores, v_p, v_l, v_f = calcular_score_categorias(data, mejores, pesos)

        if cat_scores:
            final_v = media_armonica(cat_scores)
            n = len(cat_scores)
            scores[sc] = {
                "score": final_v * 100,
                "pot": (v_p / n) * 100 if n else 0,
                "resp": (v_l / n) * 100 if n else 0,
                "flu": (v_f / n) * 100 if n else 0
            }

    return scores


def calcular_ranking_manual(datos_rendimiento, pesos=(0.40, 0.40, 0.20)):
    """Calcula el ranking para pruebas manuales (Pestaña de Rendimiento).
    
    Soporta tanto stress-ng (cpu, threads, memory) como hyperfine (latencia_*).
    Para hyperfine, menor latencia = mejor puntuación.
    
    Args:
        datos_rendimiento: lista de dicts con resultados individuales
        pesos: tuple (potencia, respuesta, fluidez)
        
    Returns:
        dict {scheduler: score_porcentaje} o dict vacío
    """
    if not datos_rendimiento:
        return {}

    per_sc = {}
    for d in datos_rendimiento:
        sc = d["sched"]
        tipo = d["tipo"]
        if sc not in per_sc:
            per_sc[sc] = {}
        # Para hyperfine, menor valor es mejor
        if tipo.startswith("latencia_"):
            if tipo not in per_sc[sc] or d["valor"] < per_sc[sc][tipo]:
                per_sc[sc][tipo] = d["valor"]
        else:
            if tipo not in per_sc[sc] or d["valor"] > per_sc[sc][tipo]:
                per_sc[sc][tipo] = d["valor"]

    scores_finales = {}
    for sc, tests in per_sc.items():
        cat_scores = []
        for t_tipo in tests:
            if t_tipo.startswith("latencia_"):
                # Hyperfine: menor es mejor
                min_v = min([d['valor'] for d in datos_rendimiento if d['tipo'] == t_tipo], default=1)
                min_p = min([d['p95'] for d in datos_rendimiento if d['tipo'] == t_tipo and d['p95'] > 0], default=1)
                reg = next((d for d in reversed(datos_rendimiento) if d['sched'] == sc and d['tipo'] == t_tipo), None)
                if not reg:
                    continue

                r_pot = (min_v / reg['valor']) if reg['valor'] > 0 else 0
                r_lat = (min_p / reg['p95']) if reg['p95'] > 0 else 0
                r_flu = max(0.01, 1.0 - reg['fairness'])
            else:
                # stress-ng: mayor es mejor
                max_v = max([d['valor'] for d in datos_rendimiento if d['tipo'] == t_tipo], default=1)
                min_p = min([d['p95'] for d in datos_rendimiento if d['tipo'] == t_tipo and d['p95'] > 0], default=1)
                reg = next((d for d in reversed(datos_rendimiento) if d['sched'] == sc and d['tipo'] == t_tipo), None)
                if not reg:
                    continue

                r_pot = reg['valor'] / max_v if max_v > 0 else 0
                r_lat = (min_p / reg['p95']) if reg['p95'] > 0 else 0
                r_flu = max(0.01, 1.0 - reg['fairness'])

            cat_scores.append((r_pot * pesos[0]) + (r_lat * pesos[1]) + (r_flu * pesos[2]))

        if cat_scores:
            h_mean = media_armonica(cat_scores)
            scores_finales[sc] = h_mean * 100

    return scores_finales


_MAPA_CHART = {
    "cpu": 0, "threads": 1, "memory": 2,
    "latencia_fork": 3, "latencia_compile": 4, "latencia_loaded": 5,
    "fork": 3, "compile": 4, "loaded": 5,
}


def calcular_valor_grafico(res, tipo):
    """Calcula el valor normalizado para el gráfico radar.
    
    Devuelve un float listo para pasar a grafico.actualizar_dato().
    """
    if tipo == "cpu":
        return 1000.0 / max(0.01, res.get("p95", 0))
    elif tipo == "threads":
        ops = res.get("ops_real") or res.get("valor") or 0
        cores = max(1, res.get("cores") or 1)
        return ops / cores
    elif tipo == "memory":
        ops = res.get("ops_real") or res.get("valor") or 0
        p95v = max(0.1, res.get("p95") or 1.0)
        return ops / p95v
    elif tipo in ("fork", "latencia_fork"):
        return 1000.0 / max(0.01, res.get("p95", 0))
    elif tipo in ("loaded", "latencia_loaded", "compile", "latencia_compile"):
        ops = res.get("valor") or 0
        p95v = max(0.1, res.get("p95") or 1.0)
        return ops / p95v
    else:
        return res.get("valor", 0)


def calcular_valor_ranking(res, tipo):
    """Calcula el valor normalizado para mostrar en la UI de ranking.
    
    Para stress-ng: mayor es mejor. Para hyperfine: valor directo en µs.
    """
    if tipo == "cpu":
        return 1000.0 / max(0.01, res.get("p95", 0))
    elif tipo == "memory":
        return res.get("valor", 0) / max(0.1, res.get("p95") or 1.0)
    elif tipo.startswith("latencia_"):
        return res.get("valor", 0)
    else:
        return res.get("valor", 0)
