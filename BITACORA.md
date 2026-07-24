# Bitácora de Desarrollo — R.E.A.C.T.O.R

## 2026-07-22 — Refactor masivo y ColumnView

### Objetivo
Modernizar la interfaz y reorganizar el proyecto en módulos con nombres en español.

### Logros
- Dividí `historial.py` y `diagnostico.py` en subdirectorios modulares (`ui/historial/`, `ui/diagnostico/`)
- Extraje `RadarChart` a `widgets/radar.py` (compartido entre Monitor y Entorno)
- Traduje ~200+ nombres de funciones, variables y constantes a español
- Reemplacé tabla comparativa (Gtk.Grid con botones) por `Gtk.ColumnView` nativo

### Problemas encontrados
1. **Sort del ColumnView no funcionaba**: `Gtk.CustomSorter.new()` con lambda se rompía porque PyGObject pasa `*user_data` como tercer argumento. Solución: usar `Gtk.CustomSorter()` + `set_sort_func()` con `*args` en el callback.
2. **Click en header no ordenaba**: faltaba el pipeline `ListStore → SortListModel(ColumnView.sorter) → MultiSelection`.
3. **Lógica de "mejor"**: cambié de flags pre-computados en cada fila a cálculo dinámico en `_on_bind`.

### Pendiente
- Rediseñar la tabla comparativa (bug de botones que crecen al hacer sort) → resuelto con ColumnView
- Centrar chips verticalmente → resuelto

---

## 2026-07-23 — AppImage, verificador y pulidos

### Objetivo
Crear build AppImage, mejorar verificador de herramientas al inicio, centrar chips.

### Logros
- **AppImage funcional** (4.8MB): empaqueta app + scxctl/stress-ng/hyperfine, usa GTK del sistema
- **Verificador mejorado**: chequea scxctl, stress-ng, hyperfine, run0/sudo; separa críticos de advertencias
- **Chips centrados**: `valign=Gtk.Align.CENTER` en todos los chips `card`+`pill`
- **Build script**: `scripts/build-appimage.sh` — build reproducible

### Problemas con AppImage
- `linuxdeploy-plugin-gtk` no empaqueta correctamente los GResources de GTK4 en Solus
- Los `.gresource` sections existen en las .so pero no se registran al cargar desde el AppDir
- Solución: usar GTK del sistema, solo empaquetar app + binaries. Funciona perfecto.

### GitHub Action + Release
- `.github/workflows/build-appimage.yml` — CI para main y tags v*
- Usa `ARCH=x86_64` para appimagetool
- Release creado con `gh release create` (CLI de GitHub, compatible Node 24)
- `permissions: contents: write` necesario para crear releases
- Primer release exitoso: `v0.7.0` 🎉

### v0.7.4
- Centrados chips de resumen en tendencia

### v0.7.3
- Incluido `data/` en build script del AppImage para empaquetar iconos del IDK
- Version bump a 0.7.3

### v0.7.2
- `data/icons/Adwaita/` — Bundle completo de 44 iconos desde `icon-development-kit`
- `utils/iconos.py` — Módulo centralizado con constantes + `establecer_iconos_idk()`
- Toggle en Controles para elegir entre iconos del IDK o del sistema
- Reemplazados iconos no estándar (`org.gnome.Settings-*`)
- Eliminado logo de distribución en Entorno
- Actualizadas actions de CI a Node.js 24

### v0.7.1
- `data/icons/Adwaita/` — Bundle completo de 44 iconos desde `icon-development-kit`
- `utils/iconos.py` — Módulo centralizado con constantes + `establecer_iconos_idk()`
- Toggle en Controles para elegir entre iconos del IDK o del sistema
- Reemplazados iconos no estándar (`org.gnome.Settings-*`)

### Pendiente
- (ninguno por ahora)
