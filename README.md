# ACCA — Guía de uso

ACCA se actualiza automáticamente desde GitHub. No hace falta instalar nada.

## Archivo general (`GOOGLE_SHEET_ID`)

| Pestaña visible | Uso |
|---|---|
| **RESUMEN** | Resultados globales y rendimiento de equipos de cinco integrantes |
| **PARTIDAS** | Historial de partidas oficiales y composiciones |
| **REGISTRO** | Agregar IDs de partidas oficiales y revisar el estado de importación |

### Agregar una partida
1. Abrí **REGISTRO** y agregá el ID de Riot en la columna `MATCH_ID` (ejemplo: `LA2_1234567890`).
2. Dejá `ESTADO` vacío o escribí `PENDIENTE`.
3. Si ambos equipos tienen cinco miembros ACCA, indicá `AZUL` o `ROJO` en `LADO_ACCA`.
4. Esperá la sincronización automática o pedile a un administrador que ejecute [Sincronizar ACCA](https://github.com/Bukaii/ACCA/actions/workflows/sync.yml).

`PROCESADA` significa que la partida fue incorporada; `ERROR` requiere revisar la columna `MENSAJE`, corregir el problema y volver a escribir `PENDIENTE`.

Solo se cuentan partidas ingresadas expresamente que tengan cinco integrantes registrados en el mismo equipo.

## Archivo de jugadores (`GOOGLE_SHEET_ID_PLAYERS`)

| Pestaña visible | Uso |
|---|---|
| **JUGADORES** | Registro de integrantes, Riot ID y región |
| **RENDIMIENTO** | Clasificaciones individuales por jugador, campeón, rol, objeto y composición |
| **PARTICIPACIONES** | Historial de partidas individuales, KDA, participación en kills, oro/min, CS/min, daño/min y objetos |

En **JUGADORES**, completá `ALIAS`, `RIOT_ID` (por ejemplo `Jugador#LAS`), `REGION` (`LAS`, `LAN`, etc.) y `ACTIVO` (`SI` o `NO`). Las columnas `PUUID`, `ESTADO` y `MENSAJE` las gestiona el sistema.

## Diseño y conservación

Ambos archivos usan las mismas tablas claras, encabezados y franjas alternadas. Las pestañas técnicas antiguas se **ocultan, no se eliminan**: contienen los datos originales de Riot, timelines, compras, eventos y registros históricos. No las edites.

El sistema consulta las respuestas de partida y timeline de Riot y guarda una copia comprimida para análisis futuros. Los datos antiguos pueden requerir reprocesamiento si todavía están disponibles en Riot.

## Actualizaciones

La sincronización se ejecuta desde GitHub Actions. Los usuarios solo tienen que utilizar las pestañas visibles. Los administradores pueden consultar [las ejecuciones](https://github.com/Bukaii/ACCA/actions) si una actualización falla.

**No compartas credenciales de Riot o Google en las hojas.**
