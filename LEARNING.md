# R.E.A.C.T.O.R — Documento de Aprendizaje

Este documento captura la arquitectura, patrones y decisiones de diseño del proyecto.
Sirve como referencia para mantener el estilo profesional en cualquier proyecto GTK/Python.

---

## Índice

1. [Arquitectura del Proyecto](#1-arquitectura-del-proyecto)
2. [GTK4 + PyGObject + Libadwaita](#2-gtk4--pygobject--libadwaita)
3. [Threading y Concurrencia](#3-threading-y-concurrencia)
4. [AppImage](#4-appimage)
5. [Benchmarking](#5-benchmarking)
6. [i18n](#6-i18n)
7. [Testing](#7-testing)
8. [DRY y Refactorización](#8-dry-y-refactorización)
9. [Control de Versiones](#9-control-de-versiones)
10. [CI/CD](#10-cicd)

---

## 1. Arquitectura del Proyecto

### Estructura de directorios

```
reactor/
├── core/             # Lógica de negocio pura (sin GTK)
│   ├── constantes.py # Constantes compartidas, VERSION
│   ├── estado.py     # Dataclasses de estado por módulo
│   ├── tipos.py      # Tipos de prueba, fórmulas de scoring
│   ├── scoring.py    # Cálculo de scores y rankings
│   ├── benchmark.py  # Benchmark stress-ng
│   ├── hybrid.py     # Benchmark hyperfine
│   ├── database.py   # Persistencia SQLite
│   ├── verificacion.py # Motor de verificación
│   ├── thermal.py    # Sensor térmico
│   └── scx.py        # Gestión de schedulers scxctl
├── ui/               # Capa de presentación GTK
│   ├── controles.py
│   ├── rendimiento.py
│   ├── grafico.py
│   ├── disponibilidad.py
│   ├── info_pruebas.py
│   ├── verificacion.py
│   ├── automatizacion/  # Subpaquete modular
│   ├── historial/       # Subpaquete modular
│   └── diagnostico/     # Subpaquete modular
├── widgets/           # Widgets reutilizables
│   ├── radar.py
│   ├── legend.py
│   └── circular_meter.py
└── utils/             # Utilidades transversales
    ├── helpers.py      # Regex, limpieza texto, bundle, formateo, contenedores
    ├── colores.py      # Color hash, tema Adwaita, dot Cairo
    ├── logging.py      # Log thread-safe, subprocess log, toasts
    ├── lscpu.py        # Parsers de salida lscpu
    ├── iconos.py       # Constantes de iconos + GResource
    └── i18n.py         # Internacionalización gettext
```

### Regla fundamental

- **`core/`** no importa de **`ui/`** ni de **`widgets/`**
- **`core/`** solo importa de **`utils/`** (y viceversa solo para helpers)
- **`ui/`** importa de `core/` y `widgets/`
- **`widgets/`** solo importa de `utils/`

### Dataclasses de estado

Cada módulo con estado mutante tiene su dataclass en `core/estado.py`:

```python
@dataclass
class EstadoDeteccionAuto:
    en_proceso: bool = False
    progreso_actual: float = 0.0
    brutos_lock: threading.Lock = field(default_factory=threading.Lock)
    peso_timer: int = 0
    anim_timer: int = 0
```

Esto evita el patrón god-object de inyectar atributos arbitrarios en `VentanaSimple`.

---

## 2. GTK4 + PyGObject + Libadwaita

### Patrones esenciales

#### Thread safety — GLib.idle_add

Nunca llamar a métodos GTK desde threads secundarios. Usar `GLib.idle_add`:

```python
# MAL: desde un thread
label.set_text("hola")  # crash

# BIEN
GLib.idle_add(label.set_text, "hola")
```

#### Batching idle_add para evitar carreras

```python
# MAL: múltiples idle_add fragmentados
for s in schedulers:
    GLib.idle_add(crear_chip, s)  # callbacks pendientes + clear = duplicados

# BIEN: un solo idle_add atómico
def _agregar():
    for s in schedulers:
        crear_chip(s)
GLib.idle_add(_agregar)
```

#### Memory leaks con GLib.timeout_add

Los timers de GLib **nunca se auto-limpian** al destruir el widget. Siempre:

```python
self._tick_source = GLib.timeout_add(16, self.tick)
self.connect("destroy", lambda w: GLib.source_remove(self._tick_source))
```

Y en callbacks que retornan `False` (timer se auto-remueve), resetear el ID:

```python
def tick():
    if terminado:
        self._timer_id = 0  # <-- crucial
        return False
    return True
```

#### Gtk.ColumnView + SortListModel

Pipeline completo para tabla ordenable:

```python
modelo = Gio.ListStore(item_type=MiFila)
# ...poblar modelo...

columna_vista = Gtk.ColumnView()
sort_model = Gtk.SortListModel(model=modelo, sorter=columna_vista.get_sorter())
columna_vista.set_model(Gtk.MultiSelection(model=sort_model))

# Sorter personalizado
sorter = Gtk.CustomSorter()
sorter.set_sort_func(mi_funcion_comparar)
col.set_sorter(sorter)
```

#### Adw.TimedAnimation

Widget lifecycle: el primer parámetro es el widget que determina la vida de la animación.

```python
# BIEN: cada animación usa su propio slider
Adw.TimedAnimation.new(win.slider_pot, ...)
Adw.TimedAnimation.new(win.slider_resp, ...)

# MAL: todas usan el mismo widget
Adw.TimedAnimation.new(win.slider_pot, ...)  # 3 veces
```

#### AboutDialog — release_notes

Usar Pango markup, NO HTML:

```python
# MAL
release_notes="<ul><li>Item</li></ul>"

# BIEN (Pango no soporta listas)
release_notes="Item 1\n• Subitem"
```

#### CSS personalizado

```python
css_provider = Gtk.CssProvider()
css_provider.load_from_data("""
    @keyframes pulse { ... }
""")
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(),
    css_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
)
```

---

## 3. Threading y Concurrencia

### SQLite desde threads secundarios

**Siempre** usar `check_same_thread=False`:

```python
conn = sqlite3.connect(str(path), check_same_thread=False)
```

Sin esto, cualquier `INSERT`/`SELECT` desde un thread secundario crashea con:
`ProgrammingError: SQLite objects created in a thread can only be used in that same thread`

### Lock para datos compartidos

Usar `threading.Lock` dentro de la dataclass de estado:

```python
brutos_lock: threading.Lock = field(default_factory=threading.Lock)

# Uso
with win.auto_state.brutos_lock:
    win.auto_state.brutos_finales = brutos
```

### Patrón thread worker + idle_add

```python
def iniciar_operacion(win):
    def worker():
        resultado = hacer_trabajo_pesado()
        GLib.idle_add(actualizar_ui, resultado)

    threading.Thread(target=worker, daemon=True).start()
```

---

## 4. AppImage

### Estructura del AppDir

```
AppDir/
├── AppRun                      # Entry point
├── R.E.A.C.T.O.R.desktop
├── reactor.svg
└── usr/
    ├── bin/
    │   ├── hyperfine           # Static musl
    │   ├── stress-ng           # Static musl
    │   └── cyclictest          # Compilado en CI
    └── share/
        └── reactor/
            ├── main.py
            ├── core/
            ├── ui/
            ├── utils/
            ├── widgets/
            └── data/
```

### AppRun

```bash
#!/bin/bash
HERE="${SELF%/*}"
export PATH="$HERE/usr/bin:$PATH"
export PYTHONPATH="$HERE/usr/share/reactor:$PYTHONPATH"
export PYTHONDONTWRITEBYTECODE=1  # No escribir __pycache__ en mount RO
cd "$HERE/usr/share/reactor"
exec python3 main.py "$@"
```

### Bundle de binarios estáticos

- **hyperfine**: desde GitHub releases (musl static)
- **stress-ng**: desde Alpine APK (musl static)
- **cyclictest**: compilado en CI desde rt-tests source
- **scxctl**: NO se bundlea (específico del kernel)

### Detección de modo AppImage

```python
def _modo_appimage():
    return "APPDIR" in os.environ and bool(os.environ.get("APPDIR"))
```

### Verificación de integridad

```python
def _check_appimage_integridad():
    required = [
        ("usr/bin/stress-ng", "stress-ng"),
        ("usr/bin/hyperfine", "hyperfine"),
        ("usr/bin/cyclictest", "cyclictest"),
        ("usr/share/reactor/rt-tests/Makefile", "rt-tests source"),
        ("usr/share/reactor/main.py", "entrada principal"),
    ]
```

### Recursos bundleados (read-only)

El AppImage se monta como read-only. Para archivos que necesitan escritura:

```python
if _modo_appimage():
    bundle = ruta_bundleada("usr/share/reactor/rt-tests")
    if bundle:
        shutil.copytree(bundle, "/tmp/rt-tests")
```

### Versión desde git tag

En el build script, inyectar la versión en el AppImage:

```bash
VERSION="${VERSION:-$(git describe --tags --abbrev=0 2>/dev/null || echo '')}"
if [ -n "$VERSION" ]; then
    sed -i "s/^VERSION = \".*\"/VERSION = \"${VERSION#v}\"/" \
        "$APP_DIR/usr/share/reactor/core/constantes.py"
fi
```

---

## 5. Benchmarking

### stress-ng (cpu, threads, memory)

- Usar `--yaml` para salida parseable
- Parsear manualmente (sin PyYAML) con `_parsear_yaml_simple()`
- El parser es frágil ante cambios de indentación de stress-ng

### hyperfine (fork, compile, loaded)

- `fork`: `hyperfine --warmup 3 -r 10000 /bin/true`
- `compile`: `hyperfine -p "make clean" "make -jN"` (requiere source writable + gcc + make)
- `loaded`: stress-ng + medición de latencia bajo carga

### Modo desarrollador

Todos los benchmarks tienen un `modo_dev` que retorna datos simulados:

```python
if modo_dev:
    seed = hash((sc_act, tipo)) % 1000
    return {"valor": base["val"] * factor, ...}
```

---

## 6. i18n

### Configuración

- Archivos `.po` en `po/{locale}/LC_MESSAGES/`
- Función `traducir()` en `utils/i18n.py`
- `xgettext` extrae strings con `--keyword=traducir`

### Reglas

1. **Todo texto visible** debe pasar por `traducir()`
2. **Identificadores técnicos** NO se traducen (scheduler names, nombres de archivo)
3. **F-strings** no son extraíbles por xgettext — usar `.format()`

```python
# BIEN
label.set_text(traducir("Bienvenido"))
toast = traducir("Error: {}").format(nombre_error)

# MAL: scheduler name traducido
label.set_text(traducir(sc_name))  # "scx_bpfland" → ""

# BIEN: scheduler name sin traducir
label.set_text(traducir("Planificador: {}").format(sc_name))
```

---

## 7. Testing

### Estructura

```
tests/
├── test_benchmark.py    # Parser YAML de stress-ng
├── test_database.py     # CRUD SQLite (DB temporal)
├── test_scoring.py      # Fórmulas de scoring
├── test_thermal.py      # Sensor térmico (mock /sys)
└── test_hybrid.py       # Helpers de hyperfine
```

### Patrones

#### DB temporal

```python
@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name
    inicializar_db(db_path)
    yield
    os.unlink(db_path)
```

#### Mock de /sys (thermal)

```python
@patch("os.listdir", return_value=["thermal_zone0"])
@patch("os.path.isfile", return_value=True)
@patch("builtins.open", mock_open(read_data="55000"))
def test_sensor(mock_file, mock_isfile, mock_listdir):
    t = SensorTermico().obtener_temp()
    assert t == 55.0
```

---

## 8. DRY y Refactorización

### Cuándo extraer una función

Cuando una función:
- Tiene más de 30 líneas
- Tiene más de 2 niveles de anidación
- Mezcla UI + lógica de negocio + persistencia
- Tiene más de 3 responsabilidades

### Técnicas de DRY

1. **Extraer a utils/helpers.py**: `format_raw_value()`, `dibujar_dot()`, `vaciar_contenedor()`, `mostrar_toast()`
2. **Generalizar widgets**: `crear_chip_leyenda()` con `color_func`, `on_toggle`, `ocultos_set`
3. **Factory functions**: `resultado_base()` para el dict común de benchmark
4. **Patrón SignalListItemFactory**: boilerplate de setup/bind/unbind

### Firmas de funciones

Al cambiar parámetros de funciones compartidas, **siempre usar kwargs**:

```python
# MAL: rompe todos los llamadores
def crear_chip(nombre, color_func, grafico):

# BIEN: compatible hacia atrás
def crear_chip(nombre, color_func=None, grafico=None, on_toggle=None):
```

---

## 9. Control de Versiones

### Versionado

- `pyproject.toml` usa `dynamic = ["version"]` con `attr = "core.constantes.VERSION"`
- `core/constantes.py` es la fuente de verdad para la versión
- El CI inyecta la versión del tag git en el AppImage via `sed`

### Tags

```bash
git tag -a v0.8 -m "v0.8: Refactorización y estabilidad"
git push --tags origin main
```

### Squash

Para limpiar el historial antes de un release:

```bash
git reset --soft <tag-anterior>
# editar versiones, limpiar
git commit -m "v0.8: mensaje"
git tag -a v0.8 -m "v0.8"
git push --force --tags origin main
```

---

## 10. CI/CD

### Workflow (build-appimage.yml)

```yaml
on:
  push:
    branches: [main]
    tags: ["v*"]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - apt-get: python3, libgtk-4-dev, libadwaita-1-dev, build-essential
      - bash scripts/build-appimage.sh
      - gh release create "v$VERSION" --notes "..." *.AppImage
```

### Dependencias del CI

Para compilar cyclictest estático:
- `build-essential` (gcc, make, libc)
- `libnuma-dev` (para NUMA support en rt-tests)

### Separación build-time vs runtime

| Dependencia | Build CI | AppImage | Sistema usuario |
|-------------|----------|----------|-----------------|
| gcc, make | ✅ | ❌ | ✅ (para compile benchmark) |
| git | ✅ | ❌ | ❌ |
| python3 | ✅ | ❌ | ✅ |
| GTK4, libadwaita | ✅ | ❌ | ✅ |
| stress-ng | ❌ | ✅ (estático) | ❌ |
| hyperfine | ❌ | ✅ (estático) | ❌ |
| cyclictest | ❌ | ✅ (estático) | ❌ |
| rt-tests source | ❌ | ✅ | ❌ |
| scxctl | ❌ | ❌ | ✅ |
