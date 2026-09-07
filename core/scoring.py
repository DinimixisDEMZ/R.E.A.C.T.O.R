"""
Motor de puntuación y diagnóstico.
Calcula scores ponderados con media armónica para evaluar schedulers.
"""

from core.constantes import PESOS_POR_DEFECTO
from core.tipos import TIPOS_LATENCIA


def _first(*values):
    """Retorna el primer valor no None, o 0 si todos son None."""
    for v in values:
        if v is not None:
            return v
    return 0


def calcular_score_categorias(data, mejores, pesos=PESOS_POR_DEFECTO):
    """Calcula el score de un scheduler por categoría.

    Cada categoría se puntúa con tres razones normalizadas contra el mejor
    resultado de esa categoría entre todos los planificadores comparados:
    potencia (trabajo hecho), respuesta (latencia) y fluidez (regularidad).

    Tres correcciones importantes respecto de la versión anterior:

    - Si una entrada no trae una latencia medible (`p95_valido` en falso), el
      término de respuesta no puntúa y su peso se reparte entre los otros dos.
      Antes se le asignaba el centinela 1.0, que además de inventarse el dato
      convertía a esa entrada en la referencia del resto.
    - Un `valor` de cero en una prueba de latencia ya no propaga un cero: se
      descarta la entrada, en vez de anular la categoría para todos.
    - Se informa cuántas categorías se pudieron puntuar, para que quien agrega
      los resultados pueda penalizar al que dejó pruebas sin completar.

    Returns:
        tuple: (cat_scores, potencia_acc, respuesta_acc, fluidez_acc)
    """
    w_pot, w_lat, w_flu = pesos
    cat_scores = []
    v_p_acc, v_l_acc, v_f_acc = 0, 0, 0

    for t_tipo, m in mejores.items():
        if t_tipo not in data:
            continue

        entrada = _normalizar_entrada(data[t_tipo])

        # Una medición sin valor útil no aporta información: descartarla es
        # correcto; puntuarla con cero castigaba a toda la categoría.
        if entrada['val'] is None or entrada['val'] <= 0:
            continue

        if t_tipo in TIPOS_LATENCIA:
            r_pot = (m['min_val'] / entrada['val']) if m.get('min_val') else 0
        else:
            r_pot = (entrada['val'] / m['max_val']) if m.get('max_val') else 0

        hay_latencia = entrada['p95_valido'] and entrada['p95'] > 0 and m.get('min_p95')
        r_lat = (m['min_p95'] / entrada['p95']) if hay_latencia else 0.0
        r_flu = max(0.01, 1.0 - entrada['waste'])

        if hay_latencia:
            s_cat = (r_pot * w_pot) + (r_lat * w_lat) + (r_flu * w_flu)
        else:
            # Se reparte el peso de la latencia entre potencia y fluidez, en
            # proporción a su peso original, para no penalizar ni premiar por
            # la ausencia del dato.
            resto = w_pot + w_flu
            if resto <= 0:
                continue
            s_cat = (r_pot * (w_pot / resto)) + (r_flu * (w_flu / resto))

        cat_scores.append(max(0.01, min(1.0, s_cat)))
        v_p_acc += r_pot
        v_l_acc += r_lat
        v_f_acc += r_flu

    return cat_scores, v_p_acc, v_l_acc, v_f_acc


def media_geometrica(valores):
    """Media geométrica de una lista de razones.

    Es la media correcta para agregar razones normalizadas de benchmark: la
    aritmética depende de contra quién se normalice y la armónica sobrepondera
    los valores bajos hasta el punto de que un solo resultado flojo puede
    invertir el ranking completo.
    """
    if not valores:
        return 0.0
    acumulado = 1.0
    for valor in valores:
        acumulado *= max(0.01, valor)
    return acumulado ** (1.0 / len(valores))


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
            val = _first(item.get('valor'), item.get('val'), (item.get('metrics') or {}).get('bogo-ops-per-second-real-time'))
            # Una latencia marcada como no válida no puede convertirse en la
            # referencia contra la que se puntúa a los demás.
            p95 = item.get('p95') if item.get('p95_valido', True) else None
            if p95 is None and item.get('p95_valido', True):
                m = item.get('metrics') or {}
                ns = _first(m.get('nanosecs-per-context-switch-pipe-method'), m.get('nanosecs-per-context-switch'))
                if ns:
                    p95 = ns / 1000.0
                else:
                    ns_mutex = m.get('nanosecs-per-mutex')
                    p95 = ns_mutex / 1000.0 if ns_mutex else 1.0

            if val and val > 0:
                vals.append(val)
            if p95 and p95 > 0:
                p95s.append(p95)

        if vals:
            mejores[t_tipo] = {
                'max_val': max(vals),
                'min_val': min(vals),
                # None, no 1: sin ninguna latencia válida en la categoría el
                # término de respuesta debe quedar fuera del cálculo.
                'min_p95': min(p95s) if p95s else None,
            }
    return mejores


def _normalizar_entrada(item):
    """Convierte una entrada de benchmark a un dict con claves estables.

    'waste' = ratio de desperdicio (0.0 = uso perfecto, 1.0 = todo desperdiciado).
      - stress-ng: (100 - cpu_usage) / 100  → bajo = buena utilización
      - hyperfine: std_dev / mean (CV)       → bajo = baja variabilidad
    """
    m = item.get('metrics') or {}
    val = _first(item.get('valor'), item.get('val'), m.get('bogo-ops-per-second-real-time'))
    p95 = item.get('p95')
    if p95 is None:
        ns = _first(m.get('nanosecs-per-context-switch-pipe-method'), m.get('nanosecs-per-context-switch'))
        if ns:
            p95 = ns / 1000.0
        else:
            ns_mutex = m.get('nanosecs-per-mutex')
            p95 = ns_mutex / 1000.0 if ns_mutex else 1.0

    waste = item.get('waste')
    if waste is None:
        cpu_usage = _first(item.get('cpu_usage'), m.get('cpu-usage-per-instance'))
        waste = max(0.0, (100.0 - cpu_usage) / 100.0) if cpu_usage else 0.5

    cores = _first(item.get('cores'), m.get('cpus')) or 1

    return {
        'val': val,
        'p95': p95,
        'p95_valido': bool(item.get('p95_valido', True)) and bool(p95) and p95 > 0,
        'waste': waste,
        'cores': cores,
        'raw': m
    }


def calcular_scores_finales(brutos, pesos=PESOS_POR_DEFECTO, tipos=None):
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

    # Cuántas categorías llegó a completar el planificador que más completó.
    # Sirve de referencia para penalizar a los que dejaron pruebas sin terminar.
    categorias_posibles = max(
        (len([t for t in mejores if t in data]) for data in brutos.values()),
        default=0)

    for sc, data in brutos.items():
        cat_scores, v_p, v_l, v_f = calcular_score_categorias(data, mejores, pesos)

        if not cat_scores:
            continue

        final_v = media_geometrica(cat_scores)

        # Un planificador que falla pruebas ya no sale beneficiado. Antes se le
        # promediaban sólo las que había completado, así que fallar la prueba
        # en la que iba peor le SUBÍA la puntuación. Ahora la cobertura entra
        # como factor: puntuar sobre la mitad de las pruebas vale la mitad.
        cobertura = (len(cat_scores) / categorias_posibles) if categorias_posibles else 1.0
        n = len(cat_scores)
        scores[sc] = {
            "score": final_v * cobertura * 100,
            "pot": (v_p / n) * 100 if n else 0,
            "resp": (v_l / n) * 100 if n else 0,
            "flu": (v_f / n) * 100 if n else 0,
            "cobertura": cobertura,
            "categorias": n,
        }

    return scores


def calcular_ranking_manual(datos_rendimiento, pesos=PESOS_POR_DEFECTO):
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
        if tipo in TIPOS_LATENCIA:
            if tipo not in per_sc[sc] or d["valor"] < per_sc[sc][tipo]:
                per_sc[sc][tipo] = d["valor"]
        else:
            if tipo not in per_sc[sc] or d["valor"] > per_sc[sc][tipo]:
                per_sc[sc][tipo] = d["valor"]

    scores_finales = {}
    for sc, tests in per_sc.items():
        cat_scores = []
        for t_tipo in tests:
            min_v = min([d['valor'] for d in datos_rendimiento if d['tipo'] == t_tipo], default=1)
            min_p = min([d['p95'] for d in datos_rendimiento
                         if d['tipo'] == t_tipo and d['p95'] > 0 and d.get('p95_valido', True)],
                        default=0)
            max_v = max([d['valor'] for d in datos_rendimiento if d['tipo'] == t_tipo], default=1)
            reg = next((d for d in reversed(datos_rendimiento) if d['sched'] == sc and d['tipo'] == t_tipo), None)
            if not reg:
                continue

            if t_tipo in TIPOS_LATENCIA:
                r_pot = (min_v / reg['valor']) if reg['valor'] > 0 else 0
            else:
                r_pot = reg['valor'] / max_v if max_v > 0 else 0
            hay_latencia = reg.get('p95_valido', True) and reg['p95'] > 0 and min_p > 0
            r_lat = (min_p / reg['p95']) if hay_latencia else 0.0
            r_flu = max(0.01, 1.0 - reg['waste'])

            if hay_latencia:
                cat_scores.append((r_pot * pesos[0]) + (r_lat * pesos[1]) + (r_flu * pesos[2]))
            else:
                resto = pesos[0] + pesos[2]
                if resto > 0:
                    cat_scores.append((r_pot * (pesos[0] / resto)) + (r_flu * (pesos[2] / resto)))

        if cat_scores:
            scores_finales[sc] = media_geometrica(cat_scores) * 100

    return scores_finales

