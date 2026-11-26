# ClimaNexo — Plataforma Meteorológica Multiproveedor

**Aplicación Web en Vivo (GitHub Pages)**: https://viralpro.github.io/ClimaNexo/

**ClimaNexo** es una plataforma meteorológica profesional en español con diseño **Mobile-First**, indicador real de **confianza del pronóstico**, motor multiproveedor internacional en paralelo (**Open-Meteo Best Match, ECMWF IFS, DWD ICON, NOAA GFS, ECCC GEM Canadá, Météo-France ARPEGE, MET Norway y wttr.in**), caché multinivel inteligente sin descargas redundantes, verificación histórica empírica de precisión y radar meteorológico animado en vivo.

Toda la lógica del sistema utiliza procesamiento matemático, estadístico y meteorológico convencional en Python, **sin depender de modelos de inteligencia artificial ni LLMs**.

---

## Características Principales

- **Diseño Mobile-First con Navegación Superior Integrada**:
  - Interfaz optimizada prioritariamente para teléfonos móviles (Android / iPhone) y adaptable a tablets y computadoras de escritorio.
  - Navegación superior accesible en todo momento con desplazamiento horizontal táctil suave y 8 apartados especializados:
    1. **Resumen**: temperatura actual en Celsius (`°C`), sensación térmica, mínima/máxima diaria, viento y ráfagas, humedad, presión atmosférica, precipitación, índice UV, visibilidad, mapa interactivo compacto, pronóstico de las próximas 24 horas, gráficos de temperatura/lluvia y viento, y tarjeta de procedencia y respaldo de datos.
    2. **Horas**: curva térmica y probabilidad de lluvia a 24 h, 48 h y 72 h con tabla horaria completa.
    3. **Extendido**: pronóstico diario de 7, 14 y 16 días con barras de amplitud térmica y comparador interactivo multi-ciudad (2 a 5 ciudades en simultáneo).
    4. **Mapa**: mapa meteorológico interactivo a pantalla completa con capas animadas de radar de precipitación y satélite infrarrojo en vivo (**RainViewer**) e inspector meteorológico por clic en cualquier coordenada.
    5. **Fuentes**: comparativa transparente en tiempo real entre proveedores independientes, diferencias térmicas, pesos de ponderación y curvas superpuestas.
    6. **Precisión**: auditoría empírica de proveedores contrastando pronósticos emitidos previamente contra observaciones reales almacenadas en SQLite (`MAE`, `RMSE`, sesgo térmico y acierto de lluvia), sin porcentajes inventados.
    7. **Historial**: archivo climático histórico real (**Open-Meteo ERA5**) con filtros por rango de fechas, indicadores clave, curvas históricas y exportación directa a **CSV**.
    8. **Buscar**: buscador geográfico integrado en la navegación superior para consultar ciudades, localidades, provincias, países, coordenadas o comparativas entre múltiples ciudades.
- **Geolocalización Automática Silenciosa**:
  - Detecta la ubicación en segundo plano (GPS autorizado o geolocalización por IP) sin interrumpir la pantalla principal ni requerir configuraciones manuales.
- **Motor Meteorológico Automático (`SmartMeteorologicalEngine`)**:
  - Filtra lecturas físicamente inválidas, pondera cada proveedor según su frescura temporal, precisión histórica verificada y concordancia estadística, selecciona la mejor lectura respaldada y notifica cuando existen discrepancias entre modelos.
- **Base de Datos Meteorológica SQLite WAL y Recolector Continuo (`collector.py`)**:
  - Esquema relacional de 16 tablas normalizadas con índices e integridad referencial (`PRAGMA foreign_keys = ON`).
  - Lecturas instantáneas desde base de datos (`< 10 ms`) con actualización asíncrona no bloqueante en segundo plano cuando un registro vence.
  - Proceso recolector autónomo (`collector.py`) y planificador integrado en el servidor que mantienen actualizadas las ubicaciones registradas sin duplicar filas (control por huella `SHA-256`).

---

## Conexiones y Proveedores Meteorológicos

El sistema consulta y armoniza en paralelo múltiples fuentes meteorológicas reales:

| Proveedor | Identificador | Clave API | Cobertura y Rol |
| :--- | :--- | :---: | :--- |
| **Open-Meteo (Best Match)** | `open_meteo` | No requiere | Modelo global/regional de alta resolución, actual, horario y 16 días. |
| **MET Norway (YR.no)** | `met_norway` | No requiere | Instituto Meteorológico de Noruega (`Locationforecast 2.0`). |
| **ECMWF IFS (Europa)** | `ecmwf_ifs` | No requiere | Modelo global europeo `ecmwf_ifs025` del Centro Europeo de Previsiones. |
| **NOAA GFS (EE. UU.)** | `noaa_gfs` | No requiere | Sistema de Pronóstico Global (`gfs_seamless`) de la NOAA. |
| **DWD ICON (Alemania)** | `dwd_icon` | No requiere | Modelo global/europeo `icon_seamless` del Servicio Meteorológico Alemán. |
| **wttr.in (WorldWeather)** | `wttr_in` | No requiere | Observaciones actuales y pronóstico independiente multi-día. |
| **Open-Meteo Archive & Previous Runs** | `open_meteo_archive` | No requiere | Reanálisis histórico ERA5 y corridas históricas para auditoría real de precisión. |
| **RainViewer API** | `rainviewer` | No requiere | Mosaicos animados en vivo de radar de precipitación y satélite infrarrojo. |
| **WeatherAPI.com** | `weatherapi` | Opcional (`.env`) | Proveedor comercial adicional (se activa automáticamente si se define `WEATHERAPI_KEY`). |
| **OpenWeatherMap** | `openweather` | Opcional (`.env`) | Proveedor global adicional (se activa automáticamente si se define `OPENWEATHER_API_KEY`). |

---

## Tecnologías Utilizadas

- **Backend**: Python 3.11+, FastAPI, Uvicorn, HTTPX (consultas asíncronas concurrentes con contexto TLS reutilizable), Pydantic v2, Jinja2.
- **Base de Datos**: SQLite 3 en modo `WAL` (*Write-Ahead Logging*) con `SYNCHRONOUS = NORMAL` y caché de dos niveles (memoria + SQLite).
- **Frontend**: HTML5 semántico, CSS3 moderno con Cascade Layers (`@layer`), diseño intrínseco Mobile-First, JavaScript modular (`ES6+`), Chart.js, Leaflet y Lucide Icons (librerías vendoreadas localmente en `static/vendor/` para máxima velocidad y disponibilidad).
- **Testing**: Pytest + FastAPI `TestClient` (22 pruebas unitarias y de integración en vivo).

---

## Estructura del Proyecto

```text
ClimaNexo/
├── app/
│   ├── api/
│   │   └── routes.py                 # Endpoints REST JSON (/api/v1/*) y exportación CSV
│   ├── db/
│   │   ├── database.py               # Gestor SQLite WAL y esquema de 16 tablas normalizadas
│   │   └── repository.py             # Operaciones transaccionales, deduplicación y consultas
│   ├── models/
│   │   └── schemas.py                # Modelos de datos validados con Pydantic v2
│   ├── providers/
│   │   ├── base.py                   # Cliente HTTP asíncrono base y tablas de códigos WMO
│   │   ├── open_meteo.py             # Open-Meteo Best Match, ECMWF IFS, NOAA GFS y DWD ICON
│   │   ├── met_norway.py             # Conector MET Norway Locationforecast 2.0
│   │   ├── wttr_provider.py          # Conector wttr.in JSON
│   │   ├── weatherapi.py             # Conector opcional WeatherAPI.com
│   │   ├── openweather.py            # Conector opcional OpenWeatherMap
│   │   └── registry.py               # Registro y orquestador de proveedores activos
│   ├── services/
│   │   ├── smart_engine.py           # Motor de selección ponderada, validación física y discrepancias
│   │   ├── collector_service.py      # Servicio de recopilación continua y refresco no bloqueante
│   │   ├── accuracy_engine.py        # Verificación matemática real de pronósticos (MAE, RMSE, Bias)
│   │   ├── comparison_service.py     # Comparación multiproveedor y multi-ciudad
│   │   ├── history_service.py        # Archivo histórico ERA5 y generación de CSV
│   │   ├── geocoding_service.py      # Geocodificación, búsqueda inversa y geolocalización IP
│   │   ├── radar_service.py          # Integración de cuadros de radar RainViewer
│   │   ├── query_parser.py           # Intérprete determinístico de consultas en español
│   │   ├── narrative_service.py      # Síntesis meteorológica redactada algorítmicamente
│   │   ├── alerts_service.py         # Motor interno de evaluación de umbrales meteorológicos
│   │   ├── cache_service.py          # Caché de dos niveles (RAM + SQLite)
│   │   ├── scheduler.py              # Demonio asíncrono de mantenimiento periódico en servidor
│   │   └── weather_facade.py         # Fachada principal DB-first de la plataforma
│   ├── config.py                     # Configuración central y catálogo de proveedores
│   └── main.py                       # Fábrica de aplicación FastAPI y ciclo de vida
├── static/
│   ├── css/
│   │   └── styles.css                # Arquitectura CSS Mobile-First (@layer)
│   ├── js/
│   │   ├── api.js                    # Cliente HTTP frontend y formateadores numéricos
│   │   ├── charts.js                 # Gráficos adaptativos con Chart.js
│   │   ├── map.js                    # Mapas Leaflet, radar animado y ResizeObserver
│   │   └── app.js                    # Controlador principal de interfaz y pestañas
│   ├── vendor/                       # Chart.js, Leaflet y Lucide Icons locales
│   └── manifest.json                 # Web App Manifest para instalación en móviles
├── templates/
│   └── index.html                    # Plantilla principal de la aplicación
├── tests/                            # Suite completa de pruebas automatizadas (Pytest)
├── data/                             # Directorio de almacenamiento para SQLite (ignorado en Git)
├── collector.py                      # Script autónomo de recopilación continua por consola
├── run.py                            # Punto de entrada del servidor web
├── requirements.txt                  # Dependencias de Python
├── .env.example                      # Plantilla de variables de entorno
└── .gitignore                        # Exclusión de bases de datos locales, cachés y secretos
```

---

## Instalación y Puesta en Marcha

### 1. Clonar el repositorio

```bash
git clone https://github.com/ViralPro/ClimaNexo.git
cd ClimaNexo
```

### 2. Crear entorno virtual e instalar dependencias

**En Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**En Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configurar variables de entorno (Opcional)

El sistema funciona de inmediato sin claves API. Si deseás personalizar el puerto o añadir claves para proveedores comerciales opcionales:

```bash
cp .env.example .env
```

### 4. Iniciar la aplicación web

```bash
python run.py
```

Abrí en tu navegador o teléfono:
- **Aplicación Web**: `http://127.0.0.1:8050`
- **Estado de Salud API**: `http://127.0.0.1:8050/api/v1/health`

### 5. Ejecutar el recolector continuo independiente (Opcional)

El servidor web ya incluye actualización automática en segundo plano, pero también podés ejecutar el trabajador de recopilación de manera dedicada desde otra terminal o tarea programada:

```bash
# Ejecutar un ciclo de recopilación y verificación inmediata:
python collector.py --once

# Ejecutar en bucle continuo permanente:
python collector.py --interval 300
```

---

## Ejecución de Pruebas Automatizadas

Para validar la base de datos SQLite WAL, el motor inteligente multiproveedor, el intérprete de consultas, la verificación de precisión histórica y los endpoints HTTP en vivo:

```bash
python -m pytest -v
```
