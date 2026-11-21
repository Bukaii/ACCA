# ACCA — Uso

## Agregar partidas

Usá `/partida` en Discord con el ID de Riot. Cloudflare descarga los JSON completos de **match** y **timeline** de Riot y los archiva en el bucket privado de Supabase `acca-riot-json`.

**La publicación de Google Sheets nunca consulta Riot.** GitHub Actions lee exclusivamente los JSON archivados en Supabase y actualiza los reportes cada cinco minutos aproximadamente (la ejecución programada puede demorarse).

## Google Sheets

- Archivo general (`GOOGLE_SHEET_ID`): RESUMEN, PARTIDAS, CAMPEONES, EQUIPOS, COMPOSICIONES y OBJETOS.
- Archivo de jugadores (`GOOGLE_SHEET_ID_PLAYERS`): JUGADORES, RENDIMIENTO y PARTICIPACIONES.
- En JUGADORES, cada cuenta requiere `ALIAS` y `PUUID` correctos para reconocer sus partidas. `RIOT_ID` y `REGION` son descriptivos; el publicador no resuelve PUUID por Riot.

La pestaña REGISTRO puede mostrar el estado de IDs, pero **no descarga partidas**. Los IDs nuevos se incorporan por Discord. Los datos originales no se almacenan en pestañas RAW.

## Automatización

- [Despliegue de Cloudflare](https://github.com/Bukaii/ACCA/actions/workflows/deploy-cloudflare.yml): actualiza el Worker al cambiar su código.
- [Sincronizar ACCA](https://github.com/Bukaii/ACCA/actions/workflows/sync.yml): publica desde Supabase cada cinco minutos o manualmente.
- [Tests](https://github.com/Bukaii/ACCA/actions/workflows/tests.yml): valida cálculos y procesamiento.

GitHub Actions necesita `SUPABASE_SECRET_KEY`, `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_SHEET_ID` y `GOOGLE_SHEET_ID_PLAYERS`. No necesita `RIOT_API_KEY` para publicar.

Los JSON originales se conservan en Supabase; las hojas son vistas derivadas que pueden regenerarse.
