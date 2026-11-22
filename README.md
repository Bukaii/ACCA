# ACCA — Bot de Discord

El bot de ACCA permite registrar jugadores y partidas de **League of Legends**. Las estadísticas se actualizan automáticamente y se pueden consultar en Google Sheets.

## Comandos

| Comando | ¿Para qué sirve? |
| --- | --- |
| `/jugador` | Registrar un jugador o agregarle otra cuenta de Riot. |
| `/lista` | Ver los jugadores registrados y sus cuentas. |
| `/partida` | Agregar una partida al historial de ACCA. |

### 1. Registrar un jugador — `/jugador`

Escribí `/jugador` en Discord y completá:

- **alias:** el nombre con el que se identificará al jugador dentro de ACCA.
- **riot_id:** su Riot ID completo, con el `#` y la etiqueta.

**Ejemplo:**

`/jugador alias:Bukai_ riot_id:ブカイだ#ちょっと`

Si alguien juega con varias cuentas, repetí el comando usando **el mismo alias** y el Riot ID de la otra cuenta. Así sus estadísticas se agrupan en un solo perfil.

El bot confirma el registro y comienza a actualizar las hojas.

### 2. Ver los jugadores — `/lista`

Escribí `/lista` para consultar quiénes están registrados y qué cuentas de Riot tienen vinculadas.

No hace falta completar ningún dato.

### 3. Agregar una partida — `/partida`

Al terminar una partida que corresponda registrar en ACCA, escribí `/partida` e indicá su **ID de partida**.

**Ejemplo:**

`/partida id:LA2_1638964338`

Para partidas del servidor LAS también podés ingresar únicamente el número del ID.

El bot primero confirma que recibió la partida. Después descarga la información, la guarda y actualiza las estadísticas de Google Sheets. **Puede tardar un poco**; no es necesario volver a enviar el comando mientras está procesando.

Si la partida ya fue registrada, el bot te lo indica y evita volver a agregarla.

## ¿Dónde veo las estadísticas?

- [**Estadísticas generales de ACCA**](https://docs.google.com/spreadsheets/d/1_QVHhGoob2hDig1JDTmJttAQIIL9CKS4ea69_ozWNQc/edit): partidas, resultados, campeones, composiciones y otras estadísticas del grupo.
- [**Perfiles de jugadores**](https://docs.google.com/spreadsheets/d/1y-1FHI4jFyAm9c1m0pz9nnfuAZ7xch0aKDvGgbLP4_A/edit): buscá tu alias en la pestaña **JUGADORES** y abrí tu perfil para consultar tu rendimiento, campeones, roles, objetos e historial.

Las estadísticas se actualizan automáticamente; no tenés que editar las hojas.

## Preguntas frecuentes

**¿Puedo registrar más de una cuenta?** Sí. Usá `/jugador` con el mismo alias y otro Riot ID.

**¿Qué pasa si escribo mal un Riot ID?** El bot te avisa si no encuentra la cuenta. Revisá el nombre y lo que aparece después del `#`.

**¿Qué hago si una partida no aparece?** Esperá a que termine el procesamiento y revisá la respuesta del bot. Si indica un error, avisá a un administrador con el ID de la partida.
