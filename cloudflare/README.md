# ACCA Discord → Riot → Supabase

## Estado
- Código del Worker y consumidor de Queue incluido en este repositorio.
- Supabase: tabla `public.acca_match_jobs` creada, RLS activado; bucket privado `acca-riot-json` y tabla `acca_match_archives` existentes.
- **Pendiente de despliegue** en Cloudflare: los conectores disponibles no administran Workers/Queues.
- **Pendiente**: publicación automática de estadísticas en Google Sheets. El Worker solo archiva los dos JSON originales; no afirma que las estadísticas se actualizaron.
- El workflow Python `Sincronizar ACCA` queda solo manual para recuperación/publicación temporal.

## Configuración Cloudflare (una vez)
1. Cloudflare Dashboard → **Workers & Pages → Queues** → crear `acca-partidas`.
2. Cloudflare Dashboard → **Workers & Pages → acca-discord-bot** → Settings → Variables and Secrets: configurar secretos `DISCORD_PUBLIC_KEY`, `RIOT_API_KEY`, `SUPABASE_SECRET_KEY`; variable `SUPABASE_URL=https://nbariaodntfhgnldusmq.supabase.co`.
3. En `cloudflare/wrangler.toml`, poner en `DISCORD_ALLOWED_ROLE_IDS` los IDs de roles de Discord autorizados, separados por comas. Los administradores del servidor también podrán usar el comando.
4. En una terminal local, dentro de `cloudflare/`: `npx wrangler login` y luego `npx wrangler deploy`. Wrangler desplegará el Worker y vinculará la cola, sin cambiar la URL actual.
5. Conservar en Discord Developer Portal → Interactions Endpoint URL la URL `https://acca-discord-bot.lyanosongbo8.workers.dev/`.
6. El comando `/partida` ya registrado sigue funcionando. Si falta registrarlo, registrarlo desde Discord Developer Portal/API; este Worker no registra comandos automáticamente en cada PING.

**Importante:** los secretos se configuran en Cloudflare y nunca se agregan a GitHub ni al código. Si `wrangler deploy` no ve los secretos del Worker actual, comprobarlos después del despliegue.

## Comportamiento
`/partida LA2_1638914973` → validación de firma y roles → `acca_match_jobs` → Queue → Riot Match-V5 (match y timeline) → Supabase Storage (gzip) → verificación de lectura → índice `acca_match_archives` → estado `ARCHIVADA` → mensaje de seguimiento en Discord.

- Idempotencia básica por ID y estado. La cola puede entregar más de una vez; los objetos e índice se sobrescriben por la misma clave.
- Ante HTTP 429/5xx o errores temporales se reintenta; los errores permanentes de Riot se marcan `ERROR`.
- Discord puede dejar de aceptar mensajes de seguimiento si el procesamiento tarda demasiado; Supabase conserva el estado.
- El Worker **no actualiza Google Sheets** todavía. No eliminar el workflow manual de reportes hasta implementar la proyección de Supabase a Sheets.
- Para evitar depender del token de interacción a largo plazo, no se persiste en PostgreSQL.

## Verificación
1. En Supabase, consultar `select match_id,status,error_message from public.acca_match_jobs order by created_at desc limit 10;`
2. Comprobar que existe la fila correspondiente en `public.acca_match_archives`.
3. En Storage, comprobar ambos archivos `<MATCH_ID>/match.json.gz` y `<MATCH_ID>/timeline.json.gz`.
4. Para actualizar temporalmente las hojas, ejecutar manualmente el workflow `Sincronizar ACCA` → `sync`.

## Restricciones conocidas
- Las consultas de Riot requieren clave válida, límites de tasa y disponibilidad de Match-V5.
- Los permisos de rol se validan con `member.roles`; la interacción debe venir de un servidor.
- Para un control de duplicados fuerte bajo concurrencia, se requiere un claim atómico de trabajo en PostgreSQL (pendiente).
- La integración con Sheets y el cálculo persistente de métricas son la siguiente fase, no están implementados en este Worker.
