"""
Constantes compartidas del proyecto R.E.A.C.T.O.R.
"""

VERSION = "1.2"

# Temperatura (°C)
TEMP_UMBRAL_ESTABLE = 60
TEMP_UMBRAL_ELEVADA = 75

# Intervalo de frame para animaciones GLib (ms) ~60fps
INTERVALO_FRAME_MS = 16

# Pesos por defecto: (potencia, respuesta, fluidez)
PESOS_POR_DEFECTO = (0.45, 0.45, 0.10)

# Nombre canónico del planificador base del sistema
SISTEMA_BASE = "Sistema Base"

# Re-exportado desde core/tipos.py (registro centralizado)
from core.tipos import CATEGORIAS_RADAR, TIPOS_PRUEBA, MAPA_CHART

# Rangos de fecha para el historial — (días, nombre visible)
RANGOS_FECHA = [
    (7, "Últimos 7 días"),
    (30, "Últimos 30 días"),
    (90, "Últimos 90 días"),
    (365, "Último año"),
    (0, "Todo"),
]

# Etiquetas de UI compartidas
CARGANDO = "Cargando..."
TERMINAL_ANALISIS = "Terminal de Análisis"
REGISTRO_DETALLADO = "Registro técnico detallado"
RANGO_FECHAS = "Rango de Fechas"
MOTOR_REPOSO = "Motor en reposo"
DETERMINAR = "Determinar"
RESULTADOS_HISTORICOS = "Resultados Históricos"
ENCONTRADOS = "encontrado(s)"
