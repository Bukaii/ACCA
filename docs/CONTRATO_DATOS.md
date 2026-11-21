# ACCA — contrato de datos (v1)

## Principio
Las pestañas `RAW_*` son las tablas canónicas y exportables; las pestañas visibles son vistas derivadas, no una segunda fuente de verdad. Nunca guardar textos de presentación ni encabezados de sección dentro de una tabla RAW. Nunca borrar históricos al regenerar una vista.

## Claves y relaciones
- `RAW_PARTIDAS.MATCH_ID`: clave primaria estable de Riot, texto, formato `LA2_1638914973`. Un registro por partida.
- `RAW_JUGADORES.(MATCH_ID, PARTICIPANTE_ID)`: clave compuesta, un registro por participante. `PUUID` identifica la cuenta independientemente del Riot ID visible.
- `RAW_DETALLE.(MATCH_ID, PUUID)`: métricas detalladas por participante.
- `RAW_EQUIPOS.(MATCH_ID, EQUIPO_ID)`: objetivos por lado (100 o 200).
- `RAW_EVENTOS`: eventos cronológicos; para una migración SQL añadir `EVENTO_ID` estable o clave de origen (MATCH_ID + índice de evento).
- `RAW_BUILDS`: eventos de compra; para SQL añadir `EVENTO_ID` estable o índice.
- `RAW_RIOT_ARCHIVO.(MATCH_ID, TIPO, PARTE)`: payload original comprimido, reversible; nunca eliminar por regenerar reportes.
- `JUGADORES.ALIAS`: identificador lógico del miembro ACCA; `PUUID` es el identificador técnico de Riot.
- `IDENTIDADES.(ALIAS, RIOT_ID, REGION)`: Riot IDs adicionales o históricos, una fila por ID, sin duplicar la persona. `PUUID` se resuelve automáticamente. Si cambia solo el nombre de una misma cuenta, el PUUID sigue siendo el mismo. Si cambia de cuenta, cada PUUID se asocia explícitamente al alias.

## Tipos
- IDs, versiones/parches, regiones, aliases, PUUID: **texto**, nunca número decimal.
- `FECHA_UTC`: UTC en formato ISO 8601 o `YYYY-MM-DD HH:MM:SS` documentado; preferir ISO al migrar.
- Duraciones y contadores: enteros; tasas y métricas: decimales sin separadores de miles.
- `ES_ACCA`, `EQUIPO_ACCA`: `SI` / `NO`; resultados: `VICTORIA`, `DERROTA`, `REMAKE`.
- `BANS_ACCA` / `BANS_RIVAL`: IDs numéricos de campeones separados por `|` **solo en RAW**. Las vistas resuelven los nombres.
- `N_ACCA`: cantidad de miembros ACCA del lado elegido, de 1 a 5; no confundir con número de participantes de la partida (10).
- `COMPOSICION_ACCA` y `COMPOSICION_RIVAL`: cinco campeones del equipo; no confundir con el subconjunto de jugadores ACCA.

## Exportación SQL
Importar en orden: `JUGADORES` / `IDENTIDADES` → `RAW_PARTIDAS` → `RAW_JUGADORES`, `RAW_EQUIPOS` → `RAW_DETALLE`, `RAW_BUILDS`, `RAW_EVENTOS`. Usar upsert por claves estables, no por número de fila ni nombre de pestaña visible.

## Separación de interfaz
- `REGISTRO` es entrada manual de IDs de partida; `PARTIDAS_PENDIENTES` conserva el estado técnico de la cola.
- `CAMPEONES`, `RESUMEN`, `EQUIPOS`, `COMPOSICIONES`, `RENDIMIENTO` y `PARTICIPACIONES` son vistas calculadas.
- `CAMPEONES_LEGACY` es copia histórica del diseño mixto previo a su normalización.
- `PARTIDAS_MANUALES` es una pestaña heredada retirada de la navegación, no se debe usar para nuevas entradas.

## Calidad de datos
Verificar que cada partida procesada tenga exactamente diez filas en `RAW_JUGADORES`, una en `RAW_PARTIDAS`, composición de cinco campeones por lado, fecha UTC válida, duración numérica, y un archivo `MATCH` en `RAW_RIOT_ARCHIVO`. Las vistas deben actualizarse desde RAW y mantener bandas de color hasta la última fila.
