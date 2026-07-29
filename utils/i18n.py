"""
Sistema de internacionalización (i18n) con gettext.
Soporte para archivos .po/.mo en po/.
"""
import gettext
import json
import os
import locale as _locale

_DOMINIO = "reactor"
_DIR_PO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "po")
_RUTA_CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "reactor", "config.json")

_ = None
IDIOMA_ACTUAL = "es_ES"
USAR_IDIOMA_SISTEMA = True
_IDIOMA_SISTEMA = None


def _obtener_idiomas_disponibles():
    """Escanea po/ y devuelve lista de códigos de idioma con .mo disponible."""
    idiomas = []
    if not os.path.isdir(_DIR_PO):
        return ["es_ES"]
    for d in sorted(os.listdir(_DIR_PO)):
        mo = os.path.join(_DIR_PO, d, "LC_MESSAGES", f"{_DOMINIO}.mo")
        if os.path.isfile(mo):
            idiomas.append(d)
    return idiomas if idiomas else ["es_ES"]


def _cargar_config():
    try:
        if os.path.isfile(_RUTA_CONFIG):
            with open(_RUTA_CONFIG) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def _guardar_config(actualizar):
    try:
        os.makedirs(os.path.dirname(_RUTA_CONFIG), exist_ok=True)
        cfg = _cargar_config()
        cfg.update(actualizar)
        with open(_RUTA_CONFIG, "w") as f:
            json.dump(cfg, f)
    except (OSError, ValueError):
        pass


def _obtener_idioma_sistema():
    try:
        lang = _locale.getlocale()[0]
        return lang if lang else "es_ES"
    except Exception:
        return "es_ES"


def configurar_idioma(idioma=None):
    global _, IDIOMA_ACTUAL, USAR_IDIOMA_SISTEMA, _IDIOMA_SISTEMA
    _IDIOMA_SISTEMA = _obtener_idioma_sistema()
    cfg = _cargar_config()
    USAR_IDIOMA_SISTEMA = cfg.get("usar_idioma_sistema", True)
    if idioma is None:
        if USAR_IDIOMA_SISTEMA:
            idioma = _IDIOMA_SISTEMA
        else:
            idioma = cfg.get("idioma") or _IDIOMA_SISTEMA
    if not idioma:
        idioma = "es_ES"
    IDIOMA_ACTUAL = idioma
    try:
        trad = gettext.translation(_DOMINIO, localedir=_DIR_PO, languages=[idioma], fallback=True)
        _ = trad.gettext
    except Exception:
        _ = lambda x: x


def traducir(texto):
    if _ is None:
        return texto
    return _(texto)


def establecer_idioma(idioma):
    global USAR_IDIOMA_SISTEMA
    if idioma == IDIOMA_ACTUAL and not USAR_IDIOMA_SISTEMA:
        return
    USAR_IDIOMA_SISTEMA = False
    _guardar_config({"usar_idioma_sistema": False, "idioma": idioma})
    configurar_idioma(idioma)


def establecer_usar_idioma_sistema(usar):
    global USAR_IDIOMA_SISTEMA
    USAR_IDIOMA_SISTEMA = usar
    _guardar_config({"usar_idioma_sistema": usar})
    if usar:
        configurar_idioma()
    return USAR_IDIOMA_SISTEMA


def obtener_idiomas():
    return _obtener_idiomas_disponibles()


NOMBRES_IDIOMA = {
    "es": "Español",
    "en": "English",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português (Brasil)",
}
