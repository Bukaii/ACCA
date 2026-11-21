# ACCA — Estadísticas oficiales de League of Legends

Este documento explica cómo usar la hoja de cálculo compartida de ACCA. **No es necesario instalar programas ni ejecutar comandos.** La sincronización se realiza desde GitHub.

## 1. Jugadores — pestaña `JUGADORES`

Cada integrante ocupa una fila. Completá únicamente:

| Columna | Qué escribir | Ejemplo |
|---|---|---|
| ALIAS | Nombre identificable dentro de ACCA, único | Lyan |
| RIOT_ID | Nombre y etiqueta de Riot, separados por # | Jugador#LAS |
| REGION | Servidor del jugador | LAS |
| ACTIVO | SI para incluirlo, NO para desactivarlo | SI |

Las columnas `PUUID`, `ESTADO` y `MENSAJE` las completa el sistema. **No las edites.** Si un jugador aparece con `ERROR`, revisá el Riot ID y la región.

El alias identifica al integrante en las estadísticas; evitá cambiarlo después de registrar partidas, porque los datos históricos pueden conservar el nombre anterior. Desactivar a alguien no borra sus registros históricos.

## 2. Registrar una partida oficial — pestaña `PARTIDAS_PENDIENTES`

1. Copiá el ID de partida de Riot, por ejemplo `LA2_1234567890`.
2. Agregalo en una fila nueva de la columna `MATCH_ID`.
3. Dejá `ESTADO` vacío o escribí `PENDIENTE`.
4. Esperá la próxima sincronización de GitHub.

**Solo se registran las partidas cargadas expresamente.** No se cuentan automáticamente todas las partidas de los miembros.

El sistema requiere **cinco integrantes registrados en el mismo equipo**. Si no reconoce a los cinco, la partida queda en `ERROR`. Si ambos equipos están formados por miembros de ACCA, escribí `AZUL` o `ROJO` en `LADO_ACCA` para indicar cuál se contabiliza.

### Estados

| ESTADO | Significado | Qué hacer |
|---|---|---|
| Vacío / PENDIENTE | Esperando procesamiento | No hacer nada |
| PROCESADA | Partida incorporada a las estadísticas | No hacer nada |
| ERROR | No pudo registrarse | Leer MENSAJE, corregir el dato y cambiar ESTADO a PENDIENTE |

No borres partidas procesadas de las hojas `RAW_*`. Para reprocesar una partida, volvé a poner su estado en `PENDIENTE`. El sistema intenta reemplazar el registro anterior en vez de duplicarlo.

## 3. Consultar estadísticas

| Pestaña | Qué muestra |
|---|---|
| `DASHBOARD_STATS` | Partidas oficiales, victorias, derrotas y winrate general |
| `STATS_EQUIPOS` | Resultados por combinación de cinco integrantes |
| `STATS_CAMPEONES` | Rendimiento por jugador y campeón |
| `RAW_PARTIDAS` | Registro original de las partidas oficiales |
| `RAW_JUGADORES` | Participaciones individuales, incluidos los diez jugadores de cada partida |
| `SISTEMA` | Última sincronización y su resultado |

**Winrate** es el porcentaje de victorias entre partidas válidas. `WR_MIN_95_%` es un indicador conservador para comparar resultados con muestras de distinto tamaño. Los remakes identificados por Riot no cuentan como victorias ni derrotas.

Las pestañas `RAW_*`, `DASHBOARD_STATS` y `STATS_*` las administra el sistema: no cambies sus valores manualmente.

## 4. Actualizaciones automáticas

La hoja se sincroniza desde **GitHub Actions**, normalmente una vez por hora. La ejecución puede retrasarse. No es necesario dejar ninguna computadora encendida.

Para forzarla, un administrador del repositorio puede entrar en [GitHub Actions — Sincronizar ACCA](https://github.com/Bukaii/ACCA/actions/workflows/sync.yml), seleccionar **Run workflow** y ejecutar el proceso. El campo `match_ids` es opcional: permite registrar IDs separados por espacios sin escribirlos antes en la hoja.

Si la ejecución falla, las estadísticas pueden quedar sin actualizar. Consultá `SISTEMA` y el historial de Actions. No cambies credenciales ni hojas por tu cuenta si no sos administrador.

## 5. Reglas importantes

- Solo se cuentan partidas oficiales expresamente registradas.
- El sistema debe reconocer cinco miembros de ACCA en un equipo.
- Cada partida se identifica por su ID para evitar duplicados.
- No edites encabezados, columnas automáticas ni datos `RAW_*`.
- No publiques Riot API keys ni credenciales de Google en la hoja.
- Las estadísticas avanzadas de objetos, timeline, gráficas y partidas manuales todavía no están disponibles en esta versión.

Si aparece un error, comunicá al administrador el nombre de la pestaña, el ID de partida (si corresponde) y el texto de `MENSAJE`. Nunca compartas secretos.

## Datos avanzados de partidas

El sistema genera nuevas pestañas: `RAW_DETALLE` (KDA, daño por tipo, oro/min, CS/min, runas, hechizos, visión, curación y escudos), `RAW_EQUIPOS` (objetivos y baneos), `RAW_EVENTOS` (eventos del timeline), `RAW_BUILDS` (compras y ventas), `STATS_JUGADORES` y `STATS_ROLES`.

`RAW_RIOT_ARCHIVO` conserva comprimidas las respuestas completas de Riot (detalle de partida y timeline) para no perder campos que todavía no se muestran en tablas. No edites ninguna pestaña `RAW_*`.

Las partidas procesadas antes de esta ampliación no tienen los datos avanzados automáticamente. El administrador puede ponerlas en `PENDIENTE` para reprocesarlas, siempre que Riot siga ofreciendo esos datos.

Las tablas nuevas tienen encabezados congelados y filtros para explorar registros. El procesamiento se hace exclusivamente en GitHub Actions.
