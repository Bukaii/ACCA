# ACCA LoL Stats

Sistema de estadísticas oficiales para el grupo **ACCA** de League of Legends: trata al grupo como un equipo competitivo y guarda el historial de **victorias y derrotas** de las partidas que se registran *expresamente* como oficiales.

- **Python** procesa los datos y habla con las APIs.
- **Riot Games API** aporta los datos de partidas y jugadores.
- **Google Sheets** es el almacenamiento consultable (y donde se administra todo: jugadores, cola de partidas, estadísticas).
- **GitHub Actions** sincroniza de forma programada o a demanda. No hay servidor, backend ni base de datos adicional.

> **Regla de oro:** una partida es oficial *solo* si alguien la registró (ID en `PARTIDAS_PENDIENTES`, comando `add-match` o fila en `PARTIDAS_MANUALES`). Las partidas que juegan los integrantes por su cuenta **nunca** se cuentan solas. La pestaña `CANDIDATAS` las puede listar, pero no entran en ninguna estadística.

---

## Índice

1. [Cómo funciona (visión general)](#1-cómo-funciona-visión-general)
2. [Requisitos previos e instalación](#2-requisitos-previos-e-instalación)
3. [Clave de Riot Games API](#3-clave-de-riot-games-api)
4. [Crear y preparar el Google Sheet](#4-crear-y-preparar-el-google-sheet)
5. [Credenciales de Google y permisos](#5-credenciales-de-google-y-permisos)
6. [Variables de entorno y GitHub Secrets](#6-variables-de-entorno-y-github-secrets)
7. [Repositorio de GitHub](#7-repositorio-de-github)
8. [GitHub Actions: activación, intervalo y ejecución forzada](#8-github-actions-activación-intervalo-y-ejecución-forzada)
9. [Registro inicial de jugadores](#9-registro-inicial-de-jugadores)
10. [Registrar una partida (incluida una histórica) por su ID](#10-registrar-una-partida-incluida-una-histórica-por-su-id)
11. [Registrar partidas desde la cola de Google Sheets](#11-registrar-partidas-desde-la-cola-de-google-sheets)
12. [Ejecución manual y actualización forzada](#12-ejecución-manual-y-actualización-forzada)
13. [Consultar estadísticas, pestañas individuales y dashboard](#13-consultar-estadísticas-pestañas-individuales-y-dashboard)
14. [Referencia de pestañas y columnas](#14-referencia-de-pestañas-y-columnas)
15. [Definiciones y fórmulas estadísticas](#15-definiciones-y-fórmulas-estadísticas)
16. [Datos de Riot: qué hay, qué requiere timeline y qué no existe](#16-datos-de-riot-qué-hay-qué-requiere-timeline-y-qué-no-existe)
17. [Solución de problemas](#17-solución-de-problemas)
18. [Pruebas, estado de verificación y límites conocidos](#18-pruebas-estado-de-verificación-y-límites-conocidos)
19. [Estructura del proyecto](#19-estructura-del-proyecto)

---

## 1. Cómo funciona (visión general)

```
 Google Sheet                         Python (local o GitHub Actions)                Riot API
┌────────────────────┐   lee        ┌──────────────────────────────────┐  consulta  ┌──────────┐
│ JUGADORES          │ ───────────► │ 1. resuelve PUUID de jugadores   │ ─────────► │ account  │
│ PARTIDAS_PENDIENTES│ ───────────► │ 2. procesa PARTIDAS_MANUALES     │            │ match-v5 │
│ PARTIDAS_MANUALES  │              │ 3. procesa la cola (ID -> datos) │ ◄───────── │ timeline │
├────────────────────┤   escribe    │ 4. confirma en RAW_* por lotes   │            └──────────┘
│ RAW_PARTIDAS       │ ◄─────────── │ 5. regenera dashboards y pestañas│
│ RAW_JUGADORES      │              └──────────────────────────────────┘
│ DASHBOARD_STATS    │  ◄── derivadas: se regeneran siempre desde RAW_*
│ STATS_CAMPEONES / STATS_EQUIPOS / STATS_OBJETOS
│ J_<alias> (una por integrante)
│ CANDIDATAS · SISTEMA
└────────────────────┘
```

**Fuente única de verdad:** `RAW_PARTIDAS` (una fila por partida) y `RAW_JUGADORES` (una fila por participación, los 10 jugadores). Todo lo demás se calcula a partir de ellas y se puede borrar y regenerar (`rebuild`).

**Idempotencia:** cada partida se identifica por su ID. Reprocesarla *reemplaza* sus filas (nunca las duplica) y, si los datos no cambiaron, ni siquiera se reescribe la hoja.

**Fallos parciales:** se confirma en la hoja cada `BATCH_SIZE` partidas (10 por defecto). Si la ejecución se corta, lo ya confirmado queda guardado y lo demás sigue `PENDIENTE`; la siguiente ejecución continúa sin duplicar nada. Los errores *permanentes* (ID inexistente, ningún integrante en la partida...) se marcan `ERROR` con un mensaje; los *transitorios* (límite de peticiones, caída de Riot, red) dejan la fila `PENDIENTE` para reintentar. Una clave de Riot inválida/caducada aborta la ejecución (falla el workflow) y lo deja escrito en `SISTEMA`.

---

## 2. Requisitos previos e instalación

- **Python 3.11 o superior** (probado con 3.12) y `git`.
- Una cuenta de Riot Games, una cuenta de Google y una cuenta de GitHub.

```bash
# 1) Descomprimí el ZIP y entrá a la carpeta
cd acca-lol-stats

# 2) Entorno virtual (recomendado)
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1

# 3) Dependencias
pip install -r requirements.txt     # para ejecutar
pip install -r requirements-dev.txt # además, para correr las pruebas

# 4) Comprobar que todo está bien instalado (no necesita credenciales)
python -m pytest
```

Todos los comandos se ejecutan como `python -m acca_stats <comando>` (ver [sección 12](#12-ejecución-manual-y-actualización-forzada)).

---

## 3. Clave de Riot Games API

1. Entrá a <https://developer.riotgames.com> e iniciá sesión con tu cuenta de Riot.
2. En el *dashboard* verás una **Development API Key** (empieza con `RGAPI-`). Sirve para probar, pero **se desactiva cada 24 horas**.
3. **Para que GitHub Actions funcione de forma permanente necesitás una *Personal API Key*, que no caduca:** en el portal usá *Register Product* → *Personal API Key*, describí el proyecto (herramienta privada de estadísticas de una comunidad) y esperá la aprobación de Riot. Hasta que la aprueben, tendrás que pegar una clave nueva en el secret cada día.
4. Límites de las claves Development y Personal: **20 peticiones por segundo y 100 cada 2 minutos**. El programa los respeta solo (limitador local + reintento ante `429` usando `Retry-After`). Se pueden ajustar con `RIOT_RATE_PER_SECOND` y `RIOT_RATE_PER_2MIN` si tu clave tiene otros límites.
5. Cada partida cuesta 1 petición (2 si se pide el timeline, que es lo normal). Con 100 peticiones cada 2 minutos, unas 40 partidas por ejecución es lo cómodo; el resto espera a la siguiente (`MAX_MATCHES_PER_RUN`).

> Respetá los [términos de uso de la Riot API](https://developer.riotgames.com/terms). Una clave Development no puede sostener un producto público.

---

## 4. Crear y preparar el Google Sheet

1. Entrá a <https://sheets.google.com> y creá una **hoja de cálculo en blanco** (por ejemplo, "ACCA LoL Stats").
2. Copiá su **ID**: es la parte larga de la URL `https://docs.google.com/spreadsheets/d/`**`ESTE_ES_EL_ID`**`/edit`. (También se acepta pegar la URL completa.)
3. No hace falta crear pestañas a mano: el sistema las crea con los encabezados correctos la primera vez (`python -m acca_stats init`, o cualquier sincronización). Si preferís crearlas vos, usá exactamente los nombres de la [sección 14](#14-referencia-de-pestañas-y-columnas).
4. **No cambies los nombres de los encabezados** de la fila 1 ni los nombres de las pestañas. Podés agregar columnas propias a la derecha en las pestañas de entrada (se respetan). No las agregues en las pestañas `RAW_*`, `DASHBOARD_STATS`, `STATS_*` ni `J_*`: se reescriben.

---

## 5. Credenciales de Google y permisos

El programa entra a la hoja con una **cuenta de servicio** (un "usuario robot"), no con tu cuenta personal.

1. Abrí <https://console.cloud.google.com> y creá un proyecto (o elegí uno).
2. **Habilitá la API:** menú *APIs y servicios → Biblioteca* → buscá **Google Sheets API** → *Habilitar*.
3. **Creá la cuenta de servicio:** *IAM y administración → Cuentas de servicio → Crear cuenta de servicio*. Ponele un nombre (p. ej. `acca-stats`). No hace falta asignarle roles del proyecto.
4. **Creá la clave:** entrá a la cuenta → pestaña *Claves* → *Agregar clave → Crear clave nueva → JSON*. Se descarga un archivo `.json`. **Es un secreto**: no lo subas a GitHub ni lo compartas.
5. **Compartí la hoja con la cuenta de servicio:** abrí el JSON, copiá el valor de `client_email` (algo como `acca-stats@tu-proyecto.iam.gserviceaccount.com`), y en tu Google Sheet pulsá *Compartir* → pegá ese correo → rol **Editor** → *Enviar*. Sin este paso verás errores de permisos (403).

Permisos que usa el programa: solo la **API de Google Sheets** sobre esa hoja (leer y escribir celdas, crear pestañas, dar formato y crear gráficos). No usa Google Drive ni accede a otros archivos.

---

## 6. Variables de entorno y GitHub Secrets

| Variable | ¿Obligatoria? | Qué es |
|---|---|---|
| `RIOT_API_KEY` | **Sí** | Clave de Riot (sección 3). |
| `GOOGLE_SHEET_ID` | **Sí** | ID (o URL) de la hoja (sección 4). |
| `GOOGLE_CREDENTIALS_JSON` | Sí\* | **Contenido completo** del JSON de la cuenta de servicio, en una sola variable. Es lo que se usa en GitHub. |
| `GOOGLE_CREDENTIALS_FILE` | Sí\* | Alternativa local: ruta al archivo `.json` (p. ej. `./service-account.json`). |
| `FETCH_TIMELINE` | No (`true`) | Pide el timeline para dif. de oro @15 y orden de compra. Cuesta 1 petición extra por partida. |
| `MIN_SAMPLE` / `HIGH_SAMPLE` | No (`5` / `20`) | Umbrales de confianza: `BAJA` < MIN, `MEDIA` < HIGH, `ALTA` desde HIGH. |
| `BATCH_SIZE` | No (`10`) | Partidas por confirmación en la hoja. |
| `MAX_MATCHES_PER_RUN` | No (`100`) | Tope de partidas de Riot por ejecución; el resto queda `PENDIENTE`. |
| `DISCOVER_COUNT` | No (`20`) | Partidas recientes por jugador al listar candidatas. |
| `RIOT_RATE_PER_SECOND` / `RIOT_RATE_PER_2MIN` | No (`20` / `100`) | Límites locales. |
| `DDRAGON_ENABLED` / `DDRAGON_LOCALE` | No (`true` / `es_MX`) | Nombres de objetos desde Data Dragon; si no se puede descargar se muestran IDs. |
| `SYNC_INTERVAL_MINUTES` | No (vacío) | Con `sync --scheduled`: omite la ejecución si la última fue hace menos de esto. |
| `LOG_LEVEL` | No (`INFO`) | Nivel de logs. |

\* Hace falta **una** de las dos.

### Ejecución local

```bash
cp .env.example .env     # Windows: copy .env.example .env
# Editá .env: completá RIOT_API_KEY, GOOGLE_SHEET_ID y GOOGLE_CREDENTIALS_FILE
# Guardá el JSON de Google en ./service-account.json (ya está en .gitignore)

python -m acca_stats check
```

`check` valida las variables (sin mostrar sus valores), el acceso a la hoja y la clave de Riot, e indica qué falta si algo falla.

### GitHub Secrets (para Actions)

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**. Creá tres secrets con **estos nombres exactos**:

| Secret | Qué pegar |
|---|---|
| `RIOT_API_KEY` | Tu clave `RGAPI-...` |
| `GOOGLE_SHEET_ID` | El ID de la hoja |
| `GOOGLE_CREDENTIALS_JSON` | Abrí el `.json` descargado con un editor de texto, seleccioná **todo** el contenido y pegalo tal cual |

Los parámetros opcionales (`MIN_SAMPLE`, `SYNC_INTERVAL_MINUTES`, etc.) se pueden definir como **Variables** (pestaña *Variables* de la misma pantalla, no *Secrets*); si no existen se usan los valores por defecto.

Las credenciales nunca se escriben en el código ni en el ZIP; `.gitignore` bloquea `.env` y los JSON de credenciales.

---

## 7. Repositorio de GitHub

1. En GitHub: **New repository** (puede ser privado). No agregues README ni .gitignore (ya vienen en el proyecto).
2. Desde la carpeta del proyecto:

```bash
git init
git add .
git status          # comprobá que NO aparecen .env ni service-account.json
git commit -m "ACCA LoL Stats"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

3. Cargá los tres secrets ([sección 6](#github-secrets-para-actions)).

> Repositorios privados: GitHub Actions tiene minutos mensuales incluidos según tu plan. Con la frecuencia por defecto (cada hora) el consumo es bajo; si lo necesitás, alargá el intervalo (sección 8).

---

## 8. GitHub Actions: activación, intervalo y ejecución forzada

El proyecto trae dos workflows en `.github/workflows/`:

- **`sync.yml` — "Sincronizar estadísticas ACCA"**: la sincronización.
- **`tests.yml` — "Pruebas"**: corre `pytest` en cada push.

### Activarlos

1. Pestaña **Actions** del repositorio. Si GitHub muestra un aviso, pulsá *"I understand my workflows, go ahead and enable them"*.
2. **Settings → Actions → General → Workflow permissions:** alcanza con el valor por defecto; el workflow solo pide `contents: read` y no escribe en el repositorio.
3. Hacé una primera ejecución manual (abajo) para comprobar que los secrets están bien.

### Ejecución programada y su intervalo (configurable)

`sync.yml` revisa la cola **una vez por hora** (cron `7 * * * *`, en UTC). Dos formas de cambiarlo:

1. **Editar el cron** en `sync.yml` (hay ejemplos comentados: cada 30 min, cada 3 h, una vez al día). El mínimo de GitHub es 5 minutos.
2. **Alargarlo sin editar archivos:** crear la *variable* de repositorio `SYNC_INTERVAL_MINUTES` (p. ej. `180`). Cada ejecución programada lee `SISTEMA → ULTIMA_EJECUCION_UTC` y se omite si pasó menos tiempo. Solo puede *alargar* el intervalo del cron, no acortarlo. Las ejecuciones manuales nunca se omiten.

La ejecución programada **no reprocesa el historial**: solo procesa filas `PENDIENTE` de las colas, y regenera estadísticas únicamente si hubo cambios (o si falta alguna pestaña, p. ej. de un jugador nuevo).

> GitHub puede retrasar los cron varios minutos, y **desactiva los workflows programados de repositorios públicos sin actividad durante 60 días** (se reactivan desde la pestaña Actions).

### Ejecución forzada (`workflow_dispatch`)

*Actions → Sincronizar estadísticas ACCA → Run workflow.* Opciones:

| Campo | Efecto |
|---|---|
| `match_ids` | IDs de partida a registrar *ya* (se agregan a la cola y se procesan, incluso si ya estaban: se actualizan sin duplicar). |
| `force_rebuild` (por defecto ✔) | Regenera dashboard y todas las pestañas aunque no haya nada nuevo. |
| `retry_errors` | Reintenta también las filas en `ERROR`. |
| `discover` | Actualiza la pestaña `CANDIDATAS`. |

El resultado se ve en el resumen del job y en la pestaña `SISTEMA` de la hoja.

---

## 9. Registro inicial de jugadores

Todo se administra en la pestaña **`JUGADORES`** (no hay que tocar código).

| ALIAS | RIOT_ID | REGION | ACTIVO | PUUID | ESTADO | MENSAJE | RIOT_ID_VERIFICADO | NOTAS |
|---|---|---|---|---|---|---|---|---|
| Ana | Ana#0001 | LAS | SI | *(auto)* | *(auto)* | *(auto)* | *(auto)* | capitana |
| Beto | Beto Pro#LAS1 | LAS | SI | | | | | |
| Cami | Cami#0003 | LAN | NO | | | | | se fue en marzo |

- **Obligatorias:** `ALIAS` (único, sin repetir), `RIOT_ID` (`nombre#TAG` completo, tal como figura en el cliente) y `REGION`.
- **Opcional:** `ACTIVO` (`SI` o `NO`; vacío = `SI`) y `NOTAS`.
- **Las rellena el sistema:** `PUUID`, `ESTADO` (`OK`, `ERROR` o `PENDIENTE`), `MENSAJE` (explicación del error) y `RIOT_ID_VERIFICADO`.
- **Regiones aceptadas:** `LAS`, `LAN`, `NA`, `BR`, `EUW`, `EUNE`, `TR`, `RU`, `KR`, `JP`, `OCE`, `PH`, `SG`, `TH`, `TW`, `VN`, `ME` (o la plataforma, como `LA2`, `EUW1`, `OC1`).

**Pasos:**

1. Escribí los integrantes (una fila cada uno).
2. Ejecutá `python -m acca_stats players` (o esperá la sincronización programada o lanzá el workflow). El sistema resuelve el **PUUID** de cada uno con Riot, escribe `ESTADO = OK` y **crea la pestaña `J_<alias>`** si no existe (`J_Ana`, `J_Beto`...). Las siguientes sincronizaciones la mantienen al día.
3. Si hay un problema (Riot ID inexistente, alias repetido, región inválida) la fila queda en `ERROR` con el motivo en `MENSAJE`. Corregí la celda y volvé a ejecutar.

**Editar / desactivar:**

- Cambiar `RIOT_ID` (p. ej. el jugador se renombró en Riot): el PUUID se vuelve a resolver solo.
- `ACTIVO = NO`: el jugador **conserva todo su historial** y sigue reconociéndose en partidas históricas y de equipo, pero deja de actualizarse su pestaña individual (que queda como está) y no se incluye en `CANDIDATAS`. Volver a poner `SI` lo reactiva.
- No borres filas de jugadores con historial (se dejaría de reconocerlos en partidas nuevas). Cambiar un `ALIAS` crea una pestaña nueva y deja la anterior sin actualizar; las filas ya guardadas en `RAW_*` conservan el alias antiguo.

> **Sobre el PUUID:** Riot lo cifra por aplicación/clave. Por eso, al identificar a los integrantes en una partida, el sistema compara primero por PUUID y, si no coincide, por **Riot ID**. Si cambiás de aplicación o de tipo de clave y algo no coincide, ejecutá `python -m acca_stats players --refresh-puuid`.

---

## 10. Registrar una partida (incluida una histórica) por su ID

**El ID de partida** es el identificador de Riot, con el formato `PLATAFORMA_NÚMERO`, por ejemplo `LA2_1234567890`. Se puede copiar del historial del cliente/OP.GG-like o de la propia API.

### Opción A: comando `add-match` (registra y procesa al instante)

```bash
python -m acca_stats add-match LA2_1234567890
python -m acca_stats add-match LA2_1234567890 LA2_1234567891   # varias, separadas por espacio o coma
python -m acca_stats add-match LA2_1234567890 --lado ROJO       # solo si hay integrantes de ACCA en ambos equipos
```

Hace tres cosas: agrega el ID a `PARTIDAS_PENDIENTES` (o lo vuelve a `PENDIENTE` si ya estaba), pide los datos a Riot y actualiza `RAW_*`, el dashboard y las pestañas. Desde GitHub se hace con *Run workflow → `match_ids`*.

Sirve igual para **partidas antiguas**: no importa cuándo se jugaron, mientras Riot todavía devuelva sus datos.

### Si Riot ya no tiene la partida (histórica sin datos)

`match-v5` no conserva partidas indefinidamente. Si Riot responde 404, la fila queda en `ERROR` y el mensaje te remite a **`PARTIDAS_MANUALES`**, donde cargás **la información esencial a mano**:

| MATCH_ID | FECHA | MODO | LADO_ACCA | RESULTADO | DURACION_MIN | ALIAS_1 | CAMPEON_1 | ROL_1 | ALIAS_2 | CAMPEON_2 | ROL_2 | … hasta ALIAS_5 | ESTADO | MENSAJE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MANUAL-001 | 2024-05-03 | Personalizada | AZUL | VICTORIA | 31 | Ana | Ahri | MID | Beto | LeeSin | JUNGLA | | *(auto)* | *(auto)* |

- **Obligatorias:** `MATCH_ID` (el de Riot o uno propio sin espacios, p. ej. `MANUAL-001`), `LADO_ACCA` (`AZUL`/`ROJO`), `RESULTADO` (`VICTORIA`/`DERROTA`) y al menos `ALIAS_1` + `CAMPEON_1`.
- **Opcionales:** `FECHA` (`AAAA-MM-DD`; sin fecha la partida cae en "N/D" en la evolución mensual), `MODO`, `DURACION_MIN`, `ROL_n` (`TOP`, `JUNGLA`, `MID`, `ADC`, `SUPPORT`) y los jugadores 2 a 5.
- **Las rellena el sistema:** `ESTADO` (`PROCESADA`/`ERROR`) y `MENSAJE`.
- Los alias deben existir en `JUGADORES`.

Las partidas manuales entran a las estadísticas de **resultado, campeón, rol, equipo y composición**, pero no tienen K/D/A, daño, oro, CS, visión ni objetos: esos datos figuran como `N/D` y los promedios solo usan participaciones que sí los tienen (columna `N_DETALLE`). Si más adelante Riot devuelve la partida y registrás su ID real, el registro de Riot **reemplaza** al manual con el mismo ID. Un registro manual nunca sobrescribe uno que ya vino de Riot.

---

## 11. Registrar partidas desde la cola de Google Sheets

La pestaña **`PARTIDAS_PENDIENTES`** es la cola. Escribí un ID por fila y dejá el resto vacío:

| MATCH_ID | ESTADO | MENSAJE | LADO_ACCA | NOTAS | PROCESADA_UTC |
|---|---|---|---|---|---|
| LA2_1234567890 | | | | final del torneo | |
| LA2_1234567891 | PENDIENTE | | ROJO | | |

- **Obligatoria:** `MATCH_ID`.
- **Opcionales:** `LADO_ACCA` (solo si hay integrantes de ACCA en **ambos** equipos; si no, se detecta solo) y `NOTAS`.
- **Las rellena el sistema:** `ESTADO`, `MENSAJE`, `PROCESADA_UTC`.

**Estados:** `PENDIENTE` (o vacío) → el próximo ciclo la toma · `PROCESADA` → registrada · `ERROR` → no se pudo; el motivo está en `MENSAJE`.

**Cómo se reintenta:**

- Un `ERROR` **no se reintenta solo** (para no gastar peticiones). Corregí la causa (por ejemplo agregá el jugador que faltaba en `JUGADORES`, o completá `LADO_ACCA`), poné el estado en `PENDIENTE` y esperá al siguiente ciclo — o ejecutá `sync --retry-errors`.
- Un `PENDIENTE` con mensaje "Se reintentará..." fue un fallo transitorio de Riot; se reintenta solo.
- Poner una fila `PROCESADA` otra vez en `PENDIENTE` vuelve a pedir los datos: se **actualiza** el registro existente (sin duplicar).
- IDs repetidos en la cola o con mayúsculas/minúsculas distintas se tratan como uno solo.

**Cómo identifica el sistema "el equipo de ACCA":** entre los 10 participantes busca a los integrantes registrados (por PUUID, o por Riot ID). El equipo con más integrantes de ACCA es "el equipo ACCA". Si hay empate entre ambos equipos hay que indicar `LADO_ACCA`. Si no participó ningún integrante registrado, la fila queda en `ERROR` (no se puede registrar una partida ajena).

**Remakes:** una partida con rendición temprana (remake) se registra con resultado `REMAKE` y **no cuenta** en victorias, derrotas ni winrate.

### Partidas candidatas (opcional)

`python -m acca_stats discover` (o `sync --discover`, o la casilla `discover` del workflow) lista en la pestaña **`CANDIDATAS`** las partidas recientes de los integrantes activos que aún no están registradas ni en cola. Es solo una ayuda para encontrar IDs: **no cuentan como oficiales** hasta que copies el ID a `PARTIDAS_PENDIENTES`. (Riot puede no incluir partidas personalizadas en esa lista; en ese caso pegá el ID directamente.)

---

## 12. Ejecución manual y actualización forzada

Desde tu computadora, con el entorno activado y `.env` configurado:

| Comando | Qué hace |
|---|---|
| `python -m acca_stats check` | Valida variables, acceso a Google y clave de Riot. |
| `python -m acca_stats init` | Crea/verifica las pestañas de entrada y datos. |
| `python -m acca_stats players [--refresh-puuid]` | Resuelve PUUID y crea las pestañas individuales que falten. |
| `python -m acca_stats add-match ID [ID…] [--lado AZUL\|ROJO]` | Registra y procesa partidas ya. |
| `python -m acca_stats sync` | Ciclo completo: jugadores → manuales → cola → estadísticas (solo si hay cambios). |
| `python -m acca_stats sync --force-rebuild` | **Actualización forzada:** además regenera dashboard y pestañas aunque no haya cambios. |
| `python -m acca_stats sync --retry-errors` | También reintenta filas en `ERROR`. |
| `python -m acca_stats sync --discover` | Además actualiza `CANDIDATAS`. |
| `python -m acca_stats sync --max-matches 20` | Limita las partidas de Riot de esta ejecución. |
| `python -m acca_stats discover` | Solo lista candidatas. |
| `python -m acca_stats rebuild` | Regenera todas las estadísticas desde `RAW_*`, **sin usar Riot** (útil tras corregir datos a mano). |

El comando imprime un resumen (`partidas nuevas/actualizadas`, `sin cambios`, `con error`, `pendientes`...). Código de salida `0` = ejecución completada (aunque haya filas en `ERROR`, que quedan anotadas en la hoja); distinto de `0` = fallo fatal (configuración, clave de Riot, acceso a la hoja).

---

## 13. Consultar estadísticas, pestañas individuales y dashboard

Todas estas pestañas son **derivadas**: no las edites, se sobrescriben. Cada tabla muestra **`PARTIDAS` (N)**, `VICTORIAS`, `DERROTAS`, `WINRATE_%`, `WR_MIN_95_%` y **`CONFIANZA`** (`BAJA`/`MEDIA`/`ALTA`).

- **`DASHBOARD_STATS`:** indicadores generales (partidas, V/D, remakes excluidos, winrate con intervalo, racha, duración media, KDA del grupo, dif. de oro @15), evolución mensual con **2 gráficos** (victorias/derrotas por mes y winrate acumulado), lado azul/rojo, ranking de integrantes, campeones más jugados, equipos, composiciones y sinergias (top 12), objetivos.
- **`STATS_CAMPEONES`:** todos los campeones usados por ACCA (con distribución de roles), **campeón × jugador**, y builds por campeón (orden real de compra).
- **`STATS_EQUIPOS`:** combinaciones de integrantes, composiciones, sinergias entre dúos, lado azul/rojo, resultados contra composiciones rivales y contra cada campeón rival, objetivos y bajas, diferencia de oro al minuto 15, evolución mensual.
- **`STATS_OBJETOS`:** objetos finales con su winrate y órdenes de compra reales.
- **`J_<alias>`** (una por integrante activo): resumen histórico, por campeón, por rol, objetos finales, builds por campeón, compañeros (con diferencia respecto a su winrate propio), evolución mensual con gráfico e historial de las últimas 50 partidas.
- **`SISTEMA`:** cuándo corrió por última vez, con qué resultado (`OK`, `OK_CON_AVISOS`, `ERROR_FATAL`) y avisos.

**Cómo leer la calidad estadística:**

- Mirá siempre `PARTIDAS` antes que el porcentaje: 1/1 es 100 % pero con confianza `BAJA`.
- Los rankings (composiciones, sinergias, dúos) se ordenan por **`WR_MIN_95_%`** (cota inferior del intervalo de Wilson al 95 %), así una única victoria no supera a 18 de 19.
- Los umbrales `BAJA`/`MEDIA`/`ALTA` son configurables (`MIN_SAMPLE`, `HIGH_SAMPLE`).
- `N/D` significa "dato no disponible", nunca cero.

---

## 14. Referencia de pestañas y columnas

| Pestaña | Quién la escribe | Contenido |
|---|---|---|
| `JUGADORES` | Usuario (+ columnas automáticas) | Registro de integrantes (sección 9). |
| `PARTIDAS_PENDIENTES` | Usuario (+ estado automático) | Cola de partidas oficiales (sección 11). |
| `PARTIDAS_MANUALES` | Usuario (+ estado automático) | Partidas sin datos en Riot (sección 10). |
| `RAW_PARTIDAS` | Sistema | Una fila por partida oficial. |
| `RAW_JUGADORES` | Sistema | Una fila por participación (10 por partida). |
| `DASHBOARD_STATS`, `STATS_CAMPEONES`, `STATS_EQUIPOS`, `STATS_OBJETOS`, `J_<alias>` | Sistema | Derivadas de `RAW_*`. |
| `CANDIDATAS` | Sistema | Partidas recientes sin registrar (no oficiales). |
| `SISTEMA` | Sistema | Estado de la última ejecución. |

**`RAW_PARTIDAS`** — `MATCH_ID`, `FUENTE` (`RIOT`/`MANUAL`), `FECHA_UTC`, `DURACION_SEG`, `MODO`, `TIPO`, `COLA_ID`, `VERSION`, `PLATAFORMA`, `LADO_ACCA`, `RESULTADO`, `EQUIPO_ACCA` (integrantes que jugaron), `N_ACCA`, `COMPOSICION_ACCA`, `COMPOSICION_RIVAL`, `BANS_ACCA`/`BANS_RIVAL` (IDs de campeón), `KILLS_*`, `ORO_*`, `TORRES_*`, `DRAGONES_*`, `BARONES_*`, `HERALDOS_*`, `INHIBIDORES_*` (cada uno para `_ACCA` y `_RIVAL`), `DIF_ORO_15`, `TIMELINE` (`SI`/`NO`), `ACTUALIZADA_UTC`, `NOTAS`.

**`RAW_JUGADORES`** — `MATCH_ID`, `PARTICIPANTE_ID`, `FECHA_UTC`, `ES_ACCA` (¿integrante registrado?), `ALIAS`, `RIOT_ID`, `PUUID`, `LADO`, `EQUIPO_ACCA` (¿juega en el equipo ACCA?), `RESULTADO`, `CAMPEON`, `ROL`, `KILLS`, `DEATHS`, `ASSISTS`, `KDA`, `DANO_CAMPEONES`, `DANO_RECIBIDO`, `ORO`, `CS`, `CS_MIN`, `VISION`, `CENTINELAS_COLOCADOS`, `CENTINELAS_DESTRUIDOS`, `CONTROL_WARDS`, `NIVEL`, `DANO_OBJETIVOS`, `DANO_TORRES`, `PARTICIPACION_KILLS` (%), `HECHIZO_1/2`, `ITEMS_FINALES` (IDs del inventario final, separados por `|`), `TRINKET`, `ORDEN_COMPRA` (solo con timeline: `itemId@segundo>…`), `ORO_15`, `CS_15`. Se guardan los **10** participantes; `ES_ACCA` identifica a los integrantes. `KDA`, `CS_MIN` y `PARTICIPACION_KILLS` son valores derivados que se calculan al ingresar la partida por comodidad de lectura.

**`CANDIDATAS`** — `MATCH_ID`, `JUGADORES_ACCA`, `DETECTADA_UTC`, `ESTADO` (`CANDIDATA`).

---

## 15. Definiciones y fórmulas estadísticas

- **Partida válida:** partida oficial con resultado `VICTORIA` o `DERROTA`. Los `REMAKE` se registran pero se excluyen de todo.
- **Winrate** = victorias ÷ (victorias + derrotas), sobre partidas válidas.
- **KDA** = (kills + asistencias) ÷ max(1, muertes). Con **cero muertes** el denominador es 1 (convención "Perfect KDA" = K + A). En promedios agregados se usa la **razón de sumas** (Σ kills + Σ asistencias) ÷ max(1, Σ muertes), no el promedio de KDAs individuales.
- **CS** = súbditos + monstruos neutrales. **CS/min** = CS ÷ (duración en minutos).
- **Participación en kills** = (K + A) ÷ kills del equipo.
- **Promedios** (kills, daño, oro, CS/min, visión): solo con participaciones que tienen ese dato. `N_DETALLE` = participaciones con K/D/A.
- **Intervalo de Wilson al 95 %** del winrate: `WR_MIN_95_%` es la cota inferior. Fórmula: centro = (p + z²/2n) ÷ (1 + z²/n), margen = z·√(p(1−p)/n + z²/4n²) ÷ (1 + z²/n), con z = 1,96.
- **Confianza:** `BAJA` si N < `MIN_SAMPLE`; `MEDIA` si N < `HIGH_SAMPLE`; `ALTA` en otro caso.
- **Sinergia de dúo:** winrate de las partidas en que ambos jugaron en el mismo equipo (un conteo por partida).
- **Compañeros (pestaña individual):** winrate del jugador cuando comparte equipo con cada otro integrante; `DIF_VS_PROPIO_pp` = diferencia con su winrate histórico en puntos porcentuales.
- **Resultados contra un campeón rival:** winrate de ACCA en las partidas en que el equipo rival incluía a ese campeón.
- **Dif. de oro @15:** oro total del equipo ACCA − oro total del rival en el frame del minuto 15 del timeline (N/D si la partida terminó antes o no hay timeline).
- **Objetos finales vs. orden de compra:** *objetos finales* es el inventario al terminar (slots 0-5; se excluyen consumibles y centinelas de control) y **no implica orden**. *Orden de compra* sale **solo** de los eventos `ITEM_PURCHASED` del timeline (descontando `ITEM_UNDO`): los "primeros 3 objetos" son los del inventario final ordenados por su compra real. Los objetos finales sin evento de compra no se ubican en el orden; nunca se deduce un orden del inventario.

---

## 16. Datos de Riot: qué hay, qué requiere timeline y qué no existe

| Dato | Fuente | Disponibilidad |
|---|---|---|
| Resultado, lado, duración, versión, modo/cola, campeones, posiciones, K/D/A, daño, oro, CS, visión, centinelas, nivel, hechizos, inventario final | `match-v5` (`/matches/{id}`) | Generalmente completo. |
| Objetivos de equipo (torres, dragones, barones, heraldos, inhibidores), bans | `match-v5` | Normalmente sí; si falta, `N/D`. |
| **Oro/CS en el minuto 15** y **orden de compra** | `match-v5` **timeline** (`/matches/{id}/timeline`) | Solo con timeline; la partida debe durar ≥ 15 min para el oro @15. Si el timeline da 404, esas métricas quedan `N/D`. |
| Posición de línea (`ROL`) | `teamPosition` | Vacía en ARAM y en algunas personalizadas: queda `N/D`. |
| **Duración** | `gameDuration` | Segundos en partidas recientes (con `gameEndTimestamp`); milisegundos en las anteriores al parche 11.20: se convierte. |
| **Datos que no existen / no se inventan** | — | Si una métrica no está disponible se escribe `N/D`, nunca un cero. Las partidas manuales solo tienen resultado, lado, campeones, roles y fecha. |

**Diferencias entre modos:** personalizadas, Flex, Clash, ARAM, etc. **no garantizan los mismos datos** (roles, objetivos, timeline). El sistema no supone nada: lee lo que venga y marca lo ausente como `N/D`. Las etiquetas de cola (`MODO`) cubren las colas más comunes; el resto se muestra con el `gameMode` de Riot.

Endpoints usados: `account-v1` (Riot ID → PUUID, ruta regional), `match-v5` (partida, timeline, lista por PUUID) y `lol-status-v4` (solo en `check`, para validar la clave). La ruta regional (`americas`, `europe`, `asia`, `sea`) se deduce del prefijo del ID de la partida o de la región del jugador.

---

## 17. Solución de problemas

| Síntoma | Causa probable y solución |
|---|---|
| `Faltan variables de entorno obligatorias: …` | Falta esa variable en `.env` o en GitHub Secrets. El mensaje nunca muestra valores. Revisá los nombres (sección 6). |
| `GOOGLE_CREDENTIALS_JSON no es un JSON válido` | Se pegó incompleto o con comillas extra. Pegá el contenido íntegro del `.json` descargado. |
| `Google denegó el acceso (403)` | La hoja no está compartida con el `client_email` (Editor) o la **Google Sheets API** no está habilitada en tu proyecto (sección 5). |
| `Google no encontró la hoja (404)` | `GOOGLE_SHEET_ID` incorrecto. |
| `Se superó la cuota de la API de Google Sheets (429)` | Demasiadas llamadas en un minuto; el programa reintenta solo. Reintentá más tarde si persiste. |
| `Riot rechazó la clave (HTTP 401/403)` / el workflow falla cada día | Clave **Development** caducada (dura 24 h). Generá otra y actualizá el secret, o conseguí una Personal API Key (sección 3). Las filas siguen `PENDIENTE`. |
| Fila de cola `ERROR`: "Riot no tiene esta partida (404)" | ID mal copiado, de otra región o demasiado antiguo. Si es histórica, usá `PARTIDAS_MANUALES`. |
| `ERROR`: "Ningún jugador registrado … participó" | Ninguno de los 10 está en `JUGADORES`. Agregá a los integrantes (con su Riot ID exacto) y reintentá. |
| `ERROR`: "misma cantidad de integrantes en ambos equipos" | Completá `LADO_ACCA` y poné `PENDIENTE`. |
| Jugador `ERROR`: "Riot no encontró…" | Riot ID, tag o región incorrectos. Copiá el Riot ID tal cual aparece en el cliente. |
| Un integrante no se reconoce en una partida | Riot ID cambiado o PUUID de otra clave. Actualizá `RIOT_ID` y/o ejecutá `players --refresh-puuid`. |
| Filas `PENDIENTE` con "Se reintentará…" | Fallo transitorio de Riot (429/5xx). Se reintenta en el siguiente ciclo. Si Riot falla 3 veces seguidas la ejecución se detiene y deja el resto en espera. |
| "N partidas quedan PENDIENTES por el límite" | Se alcanzó `MAX_MATCHES_PER_RUN`; el siguiente ciclo continúa. |
| Las estadísticas no reflejan una corrección manual en `RAW_*` | Ejecutá `rebuild` o `sync --force-rebuild` (o el workflow con `force_rebuild`). |
| El workflow programado dejó de correr | Repositorio público inactivo 60 días: reactivalo en *Actions*. O revisá que el workflow esté habilitado. |
| Una ejecución programada se "omite" | `SYNC_INTERVAL_MINUTES` está definido y la última ejecución fue reciente (comportamiento esperado). |
| Nombres de objeto como "Objeto 3089" | Data Dragon no se pudo descargar o el objeto es antiguo: solo afecta a la presentación. |

---

## 18. Pruebas, estado de verificación y límites conocidos

```bash
python -m pytest
```

**Cubren:** procesamiento de datos de partidas (equipo ACCA, lados, remakes, métricas ausentes, duraciones, timeline, orden de compra con `ITEM_UNDO`), partidas manuales, **cálculos estadísticos** (KDA con cero muertes, winrate sin remakes, Wilson, confianza, sinergias, builds), **prevención de duplicados e idempotencia** (reprocesar, ID repetido, reintentos, lotes parciales, errores transitorios y fatales), el cliente de Riot (límites, reintentos, errores), la configuración (sin filtrar credenciales), el backend de Google Sheets contra un doble de prueba, la forma de las peticiones de gráficos y la generación de todos los paneles (con y sin datos).

**Qué se verificó y qué no (sin ocultar nada):**

- ✔ Todas las pruebas automatizadas pasan (87), con datos sintéticos que imitan la estructura documentada de `match-v5`, y los dos workflows son YAML válido.
- ✔ Las firmas de la librería `gspread` que usa el backend se comprueban contra la versión instalada.
- ✘ **No se probó contra la Riot API real ni contra un Google Sheet real ni se ejecutó el workflow en GitHub:** el entorno donde se construyó no tiene credenciales ni acceso de red a esos servicios. Los endpoints y campos se implementaron según la documentación pública y ejemplos de respuesta; los nombres de campo reales podrían diferir en casos límite. **Primer paso recomendado:** `python -m acca_stats check` y luego registrar una partida conocida con `add-match`. Si algo no coincide, el mensaje de la fila en `ERROR` o el log indican dónde.
- ✘ Las peticiones de formato y gráficos de Google Sheets (`batchUpdate`) se construyeron según la documentación de la API y están probadas estructuralmente, pero no se vieron renderizadas.

**Límites conocidos:**

- Claves Development: caducan en 24 h (necesitás Personal API Key para la automatización continua).
- Riot no conserva partidas para siempre ni garantiza timeline; las partidas antiguas pueden requerir carga manual.
- La lista de `CANDIDATAS` depende de lo que Riot incluya en el historial por PUUID (puede omitir partidas personalizadas).
- Una partida con integrantes de ACCA en ambos equipos se asigna al equipo con más integrantes, o a `LADO_ACCA` si se indica.
- Los nombres de campeón se guardan como los devuelve Riot (`MonkeyKing` = Wukong, `FiddleSticks`, …); los bans se guardan como IDs.
- Cuota de Google Sheets (≈ 60 lecturas/escrituras por minuto): el programa minimiza llamadas y reintenta; con muchas pestañas individuales la regeneración total puede tardar unos minutos.
- Los datos de otros jugadores (no ACCA) que aparecen en las partidas (Riot ID, campeón, estadísticas) se guardan en `RAW_JUGADORES`; son datos de partida visibles en el juego, pero cuidá con quién compartís la hoja.

---

## 19. Estructura del proyecto

```
acca-lol-stats/
├── README.md                  ← este manual
├── .env.example               ← plantilla de configuración, sin secretos
├── requirements.txt / requirements-dev.txt / pytest.ini / .gitignore
├── .github/workflows/
│   ├── sync.yml               ← sincronización programada + manual (workflow_dispatch)
│   └── tests.yml              ← pytest en cada push
├── acca_stats/
│   ├── cli.py                 ← comandos (check, init, players, sync, add-match, discover, rebuild)
│   ├── config.py              ← variables de entorno y validación
│   ├── sync.py                ← orquestación, cola, lotes, errores, estado
│   ├── parsing.py             ← match-v5 / filas manuales → registros normalizados
│   ├── models.py              ← filas de RAW_PARTIDAS y RAW_JUGADORES (None ⇄ N/D)
│   ├── rawstore.py            ← lectura y upsert idempotente de RAW_*
│   ├── stats.py               ← cálculos estadísticos puros
│   ├── dashboards.py          ← dashboard, STATS_*, pestañas individuales
│   ├── reports.py, charts.py  ← constructor de informes y gráficos de Google Sheets
│   ├── roster.py, regions.py, schema.py, constants.py, errors.py, ddragon.py
│   ├── riot/client.py         ← cliente Riot (límites, reintentos)
│   ├── riot/timeline.py       ← oro @15 y orden de compra
│   └── sheets/                ← interfaz + backend Google (gspread) + backend en memoria
└── tests/                     ← pruebas automatizadas
```
