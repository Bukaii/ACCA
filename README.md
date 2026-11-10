# ACCA LoL Stats

Implementación inicial de registro de partidas oficiales de ACCA usando Riot API, Python, Google Sheets y GitHub Actions.

> **Estado: MVP parcial.** Este repositorio ya contiene código ejecutable, pero NO implementa aún todas las funciones del README anterior. Las integraciones externas no se han validado con credenciales reales.

## Instalación

Python 3.11+:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
```

Copiá `.env.example` a `.env` y configurá:

- `RIOT_API_KEY`: clave de Riot.
- `GOOGLE_SHEET_ID`: identificador de la hoja.
- `GOOGLE_CREDENTIALS_FILE`: ruta local al JSON de la cuenta de servicio; alternativamente, `GOOGLE_CREDENTIALS_JSON` con su contenido.

Compartí la hoja con el correo de la cuenta de servicio, con permiso de edición. No subas `.env` ni archivos JSON con credenciales.

## Comandos disponibles

```bash
python -m acca_stats init
python -m acca_stats players
python -m acca_stats add-match LA2_1234567890
python -m acca_stats sync
python -m acca_stats rebuild
python -m acca_stats check
```

En `JUGADORES` cargá `ALIAS`, `RIOT_ID` (nombre#TAG), `REGION` (por ejemplo LAS) y `ACTIVO` (SI/NO). Ejecutá `players` para resolver PUUID. En `PARTIDAS_PENDIENTES` agregá IDs para registrar partidas oficiales. El sistema no descubre ni incorpora automáticamente partidas no registradas.

**Regla de oficialidad:** para aceptar una partida, debe haber exactamente cinco PUUID de integrantes registrados en el mismo equipo. El parámetro `LADO_ACCA` permite desambiguar cuando ambos equipos tienen cinco integrantes registrados. Un jugador sin PUUID resuelto no cuenta.

## Datos y resultados

- `RAW_PARTIDAS`: una fila por ID oficial.
- `RAW_JUGADORES`: diez filas por partida.
- `DASHBOARD_STATS`: resumen global.
- `STATS_EQUIPOS`: resultados por quinteto.
- `STATS_CAMPEONES`: resultados por jugador y campeón.
- `SISTEMA`: fecha y estado de la última ejecución.

Los cálculos excluyen remakes explícitamente señalados por Riot y ordenan los grupos mediante la cota inferior de Wilson. El procesamiento reemplaza filas por ID para evitar duplicados.

## Automatización

`.github/workflows/tests.yml` ejecuta pytest al subir cambios; `.github/workflows/sync.yml` ejecuta sincronización cada hora o manualmente, con IDs opcionales.

En GitHub → Settings → Secrets and variables → Actions, configurá:

- `RIOT_API_KEY`
- `GOOGLE_SHEET_ID`
- `GOOGLE_CREDENTIALS_JSON`

La clave Development de Riot caduca periódicamente; la sincronización programada necesita una clave válida.

## Limitaciones pendientes

- No hay todavía soporte de partidas manuales/históricas sin Riot, ni pestañas individuales `J_<alias>`, ni `CANDIDATAS`.
- Faltan timeline, builds por orden de compra, objetivos, estadísticas de objetos, métricas avanzadas y gráficos.
- No se implementaron aún confirmaciones por lotes, reintentos exhaustivos de cola, ni protección de edición de las hojas RAW.
- La operación `check` valida acceso a Sheets, pero no valida activamente Riot.
- No hay prueba end-to-end con Riot y Google; antes de usarlo con datos importantes, probalo sobre una hoja nueva.
- El código se publicó sin haber ejecutado pytest en un entorno local; GitHub Actions debe confirmar las pruebas.

## Seguridad

El workflow utiliza GitHub Secrets y permisos `contents: read`. Las credenciales se ignoran mediante `.gitignore`. Nunca copies claves al README ni a incidencias públicas.
