"""
Motor de puntuación y diagnóstico.
Calcula scores ponderados con media armónica para evaluar schedulers.
"""

import math
from collections.abc import Mapping
from numbers import Real


HYBRID_TYPES = {"fork", "compile", "loaded"}

_TYPE_ALIASES = {
    "cpu": "cpu",
    "threads": "threads",
    "memory": "memory",
    "fork": "latencia_fork",
    "compile": "latencia_compile",
    "loaded": "latencia_loaded",
    "latencia_fork": "latencia_fork",
    "latencia_compile": "latencia_compile",
    "latencia_loaded": "latencia_loaded",
}


def _numero_finito(valor, positivo=False):
    """Devuelve un float válido o None sin aceptar booleanos."""
    if isinstance(valor, bool) or not isinstance(valor, Real):
        return None
    numero = float(valor)
    if not math.isfinite(numero) or (positivo and numero <= 0):
        return None
    return numero


def _normalizar_pesos(pesos):
    """Valida tres pesos no negativos y los normaliza a suma uno."""
    try:
        valores = tuple(pesos)
    except TypeError as exc:
        raise ValueError("pesos debe contener exactamente 3 valores") from exc

    if len(valores) != 3:
        raise ValueError("pesos debe contener exactamente 3 valores")

    normalizados = []
    for peso in valores:
        numero = _numero_finito(peso)
        if numero is None or numero < 0:
            raise ValueError("los pesos deben ser finitos y no negativos")
        normalizados.append(numero)

    escala = max(normalizados)
    if escala <= 0:
        raise ValueError("la suma de los pesos debe ser positiva")

    escalados = [peso / escala for peso in normalizados]
    total = sum(escalados)
    return tuple(peso / total for peso in escalados)


def _acotar(valor, minimo=0.0, maximo=1.0):
    return min(maximo, max(minimo, valor))


def _canonicalizar_tipo(tipo):
    if not isinstance(tipo, str):
        return None
    tipo = tipo.strip()
    if not tipo:
        return None
    return _TYPE_ALIASES.get(tipo.casefold(), tipo)


def _tipo_de_entrada(clave, item):
    if isinstance(item, Mapping) and item.get("tipo") is not None:
        return _canonicalizar_tipo(item.get("tipo"))
    return _canonicalizar_tipo(clave)


def _extraer_valor(item):
    """Extrae el valor principal sin ocultar un campo explícito corrupto."""
    for clave in ("valor", "val", "ops_real"):
        if clave in item and item.get(clave) is not None:
            return _numero_finito(item.get(clave), positivo=True)

    metricas = item.get("metrics")
    if isinstance(metricas, Mapping):
        return _numero_finito(
            metricas.get("bogo-ops-per-second-real-time"), positivo=True
        )
    return None


def _extraer_respuesta(item):
    """Prioriza response; p95 queda como compatibilidad con datos históricos."""
    if "response" in item and item.get("response") is not None:
        return _numero_finito(item.get("response"), positivo=True)
    if "p95" in item and item.get("p95") is not None:
        return _numero_finito(item.get("p95"), positivo=True)
    return None


def _extraer_fairness(item):
    if "fairness" in item and item.get("fairness") is not None:
        fairness = _numero_finito(item.get("fairness"))
    elif "fair" in item and item.get("fair") is not None:
        fairness = _numero_finito(item.get("fair"))
    else:
        return None

    if fairness is None or not 0.0 <= fairness <= 1.0:
        return None
    return fairness


def _normalizar_entrada(item, tipo):
    """Convierte una entrada válida al contrato interno común."""
    if not isinstance(item, Mapping):
        return None

    valor = _extraer_valor(item)
    respuesta = None if tipo == "threads" else _extraer_respuesta(item)
    fairness = _extraer_fairness(item)
    if (
        valor is None
        or fairness is None
        or (tipo != "threads" and respuesta is None)
    ):
        return None

    return {
        "val": valor,
        "response": respuesta,
        "fair": fairness,
    }


def _canonicalizar_datos_scheduler(data):
    """Canonicaliza tipos y marca como inválidas las colisiones de aliases."""
    if not isinstance(data, Mapping):
        return {}

    canonical = {}
    for clave, item in data.items():
        tipo = _tipo_de_entrada(clave, item)
        if tipo is None:
            continue
        if tipo in canonical:
            canonical[tipo] = None
        else:
            canonical[tipo] = _normalizar_entrada(item, tipo)
    return canonical


def _canonicalizar_tipos(tipos):
    if isinstance(tipos, str):
        tipos = (tipos,)
    try:
        canonical = {
            tipo_canonico
            for tipo in tipos
            if (tipo_canonico := _canonicalizar_tipo(tipo)) is not None
        }
    except TypeError:
        return ()
    return tuple(sorted(canonical))


def _tipos_union(datos_canonicos):
    return tuple(sorted({tipo for data in datos_canonicos.values() for tipo in data}))


def _calcular_mejores_canonicos(datos_canonicos, tipos):
    mejores = {}
    for tipo in tipos:
        entradas = [
            data[tipo]
            for data in datos_canonicos.values()
            if tipo in data and data[tipo] is not None
        ]
        if not entradas:
            continue

        valores = [entrada["val"] for entrada in entradas]
        respuestas = [entrada["response"] for entrada in entradas]
        min_response = (
            min(respuestas)
            if all(respuesta is not None for respuesta in respuestas)
            else None
        )
        mejores[tipo] = {
            "max_val": max(valores),
            "min_val": min(valores),
            "min_response": min_response,
            "min_p95": min_response,
        }
    return mejores


def _canonicalizar_mejores(mejores):
    if not isinstance(mejores, Mapping):
        return {}

    canonical = {}
    for tipo, referencia in mejores.items():
        tipo_canonico = _canonicalizar_tipo(tipo)
        if tipo_canonico is None or not isinstance(referencia, Mapping):
            continue
        if tipo_canonico in canonical:
            canonical[tipo_canonico] = None
        else:
            canonical[tipo_canonico] = referencia
    return canonical


def _respuesta_referencia(referencia):
    if "min_response" in referencia and referencia.get("min_response") is not None:
        return _numero_finito(referencia.get("min_response"), positivo=True)
    return _numero_finito(referencia.get("min_p95"), positivo=True)


def _puntuar_categorias(data, mejores, pesos):
    cat_scores = []
    potencia_acc = respuesta_acc = fluidez_acc = 0.0

    for tipo in sorted(mejores):
        entrada = data.get(tipo)
        referencia = mejores[tipo]
        if entrada is None or referencia is None:
            continue

        if tipo.startswith("latencia_"):
            mejor_valor = _numero_finito(referencia.get("min_val"), positivo=True)
            r_pot = mejor_valor / entrada["val"] if mejor_valor else None
        else:
            mejor_valor = _numero_finito(referencia.get("max_val"), positivo=True)
            r_pot = entrada["val"] / mejor_valor if mejor_valor else None

        if r_pot is None:
            continue

        r_pot = _acotar(r_pot)
        r_flu = _acotar(1.0 - entrada["fair"])
        mejor_respuesta = _respuesta_referencia(referencia)
        r_resp = (
            _acotar(mejor_respuesta / entrada["response"])
            if mejor_respuesta is not None and entrada["response"] is not None
            else None
        )

        potencia_acc += r_pot
        if r_resp is not None:
            respuesta_acc += r_resp
        fluidez_acc += r_flu

        dimensiones = []
        if pesos[0] > 0:
            dimensiones.append((r_pot, pesos[0]))
        if r_resp is not None and pesos[1] > 0:
            dimensiones.append((r_resp, pesos[1]))
        if pesos[2] > 0:
            dimensiones.append((r_flu, pesos[2]))
        if not dimensiones:
            continue

        peso_disponible = math.fsum(peso for _, peso in dimensiones)
        score_categoria = math.fsum(
            valor * peso for valor, peso in dimensiones
        ) / peso_disponible
        score_categoria = _acotar(score_categoria)

        cat_scores.append(score_categoria)

    return cat_scores, potencia_acc, respuesta_acc, fluidez_acc


def calcular_score_categorias(data, mejores, pesos=(0.45, 0.45, 0.10)):
    """Calcula el score de un scheduler por categoría.
    
    Args:
        data: dict {tipo: {valor, response/p95, fairness}}
        mejores: referencias calculadas por :func:`calcular_mejores`
        pesos: tuple (peso_potencia, peso_respuesta, peso_fluidez)
        
    Returns:
        tuple: (cat_scores, potencia_acc, respuesta_acc, fluidez_acc)
    """
    pesos = _normalizar_pesos(pesos)
    data = _canonicalizar_datos_scheduler(data)
    mejores = _canonicalizar_mejores(mejores)
    return _puntuar_categorias(data, mejores, pesos)


def media_armonica(valores):
    """Calcula la media armónica de una lista de valores.
    
    Penaliza mediciones con "valles" (valores bajos), 
    encontrando el mejor balance real.
    """
    try:
        valores = tuple(valores)
    except TypeError:
        return 0.0
    if not valores:
        return 0.0

    validos = [_numero_finito(valor, positivo=True) for valor in valores]
    if any(valor is None for valor in validos):
        return 0.0
    try:
        denominador = math.fsum(1.0 / valor for valor in validos)
    except (OverflowError, ZeroDivisionError):
        return 0.0
    if not math.isfinite(denominador) or denominador <= 0:
        return 0.0
    return len(validos) / denominador


def calcular_mejores(brutos, tipos=("cpu", "threads", "memory")):
    """Calcula los mejores valores de referencia por tipo de prueba.
    
    Args:
        brutos: dict {scheduler: {tipo: resultado}}
        tipos: tuple con los tipos de prueba
        
    Returns:
        dict {tipo: {max_val, min_val, min_response, min_p95}}
    """
    if not isinstance(brutos, Mapping):
        return {}
    datos_canonicos = {
        scheduler: _canonicalizar_datos_scheduler(data)
        for scheduler, data in brutos.items()
    }
    tipos = _tipos_union(datos_canonicos) if tipos is None else _canonicalizar_tipos(tipos)
    return _calcular_mejores_canonicos(datos_canonicos, tipos)


def calcular_scores_finales(brutos, pesos=(0.45, 0.45, 0.10), tipos=None):
    """Calcula los scores finales de todos los schedulers.
    
    Args:
        brutos: dict {scheduler: {tipo: resultado}}
        pesos: tuple (potencia, respuesta, fluidez)
        tipos: tuple con los tipos de prueba a considerar (None = automático)
        
    Returns:
        dict {scheduler: {score, pot, resp, flu}}
    """
    pesos = _normalizar_pesos(pesos)
    if not isinstance(brutos, Mapping) or not brutos:
        return {}

    datos_canonicos = {
        scheduler: _canonicalizar_datos_scheduler(data)
        for scheduler, data in brutos.items()
    }
    tipos_esperados = (
        _tipos_union(datos_canonicos)
        if tipos is None
        else _canonicalizar_tipos(tipos)
    )
    if not tipos_esperados:
        return {}

    elegibles = {
        scheduler: data
        for scheduler, data in datos_canonicos.items()
        if all(data.get(tipo) is not None for tipo in tipos_esperados)
    }
    if not elegibles:
        return {}

    mejores = _calcular_mejores_canonicos(elegibles, tipos_esperados)
    if any(tipo not in mejores for tipo in tipos_esperados):
        return {}
    cantidad_respuestas = sum(
        _respuesta_referencia(mejores[tipo]) is not None
        for tipo in tipos_esperados
    )

    scores = {}
    for scheduler, data in elegibles.items():
        cat_scores, potencia, respuesta, fluidez = _puntuar_categorias(
            data, mejores, pesos
        )

        cantidad = len(tipos_esperados)
        scores[scheduler] = {
            "score": _acotar(media_armonica(cat_scores)) * 100.0,
            "pot": _acotar(potencia / cantidad) * 100.0,
            "resp": (
                _acotar(respuesta / cantidad_respuestas) * 100.0
                if cantidad_respuestas
                else 0.0
            ),
            "flu": _acotar(fluidez / cantidad) * 100.0,
        }
    return scores


def _identificador_run(registro):
    for clave in ("run_id", "id_run", "run"):
        valor = registro.get(clave)
        if valor is None or isinstance(valor, bool):
            continue
        try:
            hash(valor)
        except TypeError:
            continue
        return type(valor).__name__, valor
    return None


def _orden_identificador_run(identificador):
    _, valor = identificador
    numero = _numero_finito(valor)
    if numero is not None:
        return 1, numero, ""
    return 0, 0.0, str(valor)


def _timestamp_run(registros):
    timestamps = []
    for registro in registros:
        for clave in ("run_timestamp", "run_ts", "timestamp"):
            timestamp = _numero_finito(registro.get(clave))
            if timestamp is not None:
                timestamps.append(timestamp)
                break
    return max(timestamps, default=float("-inf"))


def _seleccionar_cohorte_manual(registros):
    """Evita combinar resultados que declaran identificadores de run distintos."""
    grupos = {}
    for registro in registros:
        if not isinstance(registro, Mapping):
            continue
        identificador = _identificador_run(registro)
        if identificador is not None:
            grupos.setdefault(identificador, []).append(registro)

    if not grupos:
        return [registro for registro in registros if isinstance(registro, Mapping)]

    _, cohorte = max(
        grupos.items(),
        key=lambda item: (
            _timestamp_run(item[1]),
            _orden_identificador_run(item[0]),
        ),
    )
    return cohorte


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
    pesos = _normalizar_pesos(pesos)
    if not datos_rendimiento:
        return {}
    try:
        registros = list(datos_rendimiento)
    except TypeError:
        return {}

    seleccionados = {}
    claves_seleccion = {}
    for registro in _seleccionar_cohorte_manual(registros):
        scheduler = registro.get("sched", registro.get("scheduler_name"))
        if not isinstance(scheduler, str) or not scheduler.strip():
            continue

        tipo = _tipo_de_entrada(registro.get("test_type"), registro)
        entrada = _normalizar_entrada(registro, tipo)
        if tipo is None or entrada is None:
            continue

        clave = (
            entrada["val"] if tipo.startswith("latencia_") else -entrada["val"],
            entrada["response"] if entrada["response"] is not None else math.inf,
            entrada["fair"],
        )
        identificador = (scheduler, tipo)
        if identificador not in claves_seleccion or clave < claves_seleccion[identificador]:
            claves_seleccion[identificador] = clave
            seleccionados.setdefault(scheduler, {})[tipo] = {
                "tipo": tipo,
                "valor": entrada["val"],
                "response": entrada["response"],
                "fairness": entrada["fair"],
            }

    seleccionados = {
        scheduler: seleccionados[scheduler]
        for scheduler in sorted(seleccionados, key=lambda nombre: (nombre.casefold(), nombre))
    }
    scores = calcular_scores_finales(seleccionados, pesos=pesos)
    return {scheduler: data["score"] for scheduler, data in scores.items()}


_MAPA_CHART = {
    "cpu": 0, "threads": 1, "memory": 2,
    "latencia_fork": 3, "latencia_compile": 4, "latencia_loaded": 5,
    "fork": 3, "compile": 4, "loaded": 5,
}


def calcular_valor_grafico(res, tipo):
    """Calcula el valor normalizado para el gráfico radar.
    
    Devuelve un float listo para pasar a grafico.actualizar_dato().
    """
    if not isinstance(res, Mapping):
        return 0.0

    tipo = _canonicalizar_tipo(tipo)
    valor = _extraer_valor(res)
    respuesta = _extraer_respuesta(res)

    if tipo == "cpu":
        return 1000.0 / respuesta if respuesta else 0.0
    if tipo == "threads":
        cores = _numero_finito(res.get("cores"), positivo=True) or 1.0
        return valor / cores if valor else 0.0
    if tipo == "memory":
        return valor / respuesta if valor and respuesta else 0.0
    if tipo and tipo.startswith("latencia_"):
        denominador = valor + respuesta if valor and respuesta else 0.0
        return 2000.0 / denominador if math.isfinite(denominador) and denominador > 0 else 0.0
    return valor or 0.0


def calcular_valor_ranking(res, tipo):
    """Calcula el valor normalizado para mostrar en la UI de ranking.
    
    Para stress-ng: mayor es mejor. Para hyperfine: valor directo en µs.
    """
    if not isinstance(res, Mapping):
        return 0.0

    tipo = _canonicalizar_tipo(tipo)
    valor = _extraer_valor(res)
    respuesta = _extraer_respuesta(res)
    if tipo == "cpu":
        return 1000.0 / respuesta if respuesta else 0.0
    if tipo == "memory":
        return valor / respuesta if valor and respuesta else 0.0
    return valor or 0.0
