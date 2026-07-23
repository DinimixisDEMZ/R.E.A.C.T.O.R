"""
Constantes compartidas para el módulo historial.
"""

_TIPOS_PRUEBA = [
    ("cpu", "Context Switching"),
    ("threads", "Carga Mixta"),
    ("memory", "Sincronización"),
    ("latencia_fork", "Fork+Exec"),
    ("latencia_compile", "Compilación Paralela"),
    ("latencia_loaded", "Bajo Carga"),
]

_RANGOS_FECHA = [
    (7, "Últimos 7 días"),
    (30, "Últimos 30 días"),
    (90, "Últimos 90 días"),
    (365, "Último año"),
    (0, "Todo"),
]
