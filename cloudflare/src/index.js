import {publishPlayers,publishMatch} from "./sheets.js";
// ACCA Discord ingress + asynchronous Riot archiver.
// Secrets: DISCORD_PUBLIC_KEY, RIOT_API_KEY, SUPABASE_SECRET_KEY.
// Variables: SUPABASE_URL, DISCORD_ALLOWED_ROLE_IDS (comma-separated).
const BUCKET = "acca-riot-json";
const ROUTES = { LA2: "americas", NA1: "americas", BR1: "americas", EUW1: "europe", EUN1: "europe", TR1: "europe", RU: "europe", KR: "asia", JP1: "asia", OC1: "sea", SG2: "sea", TW2: "sea", VN2: "sea", PH2: "sea", TH2: "sea" };
const json = (body, status=200) => Response.json(body, {status});
const hex = s => Uint8Array.from(s.match(/.{2}/g)||[], x=>parseInt(x,16));
async function verified(request, body, key) {
  const sig=request.headers.get("X-Signature-Ed25519"), ts=request.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !/^[a-f0-9]{128}$/i.test(sig) || !/^[a-f0-9]{64}$/i.test(key||"")) return false;
  try {
    const publicKey=await crypto.subtle.importKey("raw",hex(key),{name:"Ed25519"},false,["verify"]);
    return crypto.subtle.verify("Ed25519",publicKey,hex(sig),new TextEncoder().encode(ts+body));
  } catch { return false; }
}
function normalize(raw) {
  let id=String(raw||"").trim().toUpperCase();
  if (/^\d{7,15}$/.test(id)) id="LA2_"+id;
  return /^[A-Z0-9]{2,5}_\d{7,15}$/.test(id) && ROUTES[id.split("_")[0]] ? id : null;
}
function headers(env,extra={}) {
  return {apikey:env.SUPABASE_SECRET_KEY,Authorization:"Bearer "+env.SUPABASE_SECRET_KEY,...extra};
}
async function supa(env,path,options={}) {
  const response=await fetch(env.SUPABASE_URL.replace(/\/$/,"")+path,{...options,headers:headers(env,options.headers)});
  if (!response.ok) throw new Error("Supabase "+response.status+" "+(await response.text()).slice(0,180));
  return response;
}
async function getJob(env,id) {
  const response=await supa(env,"/rest/v1/acca_match_jobs?select=match_id,status&match_id=eq."+encodeURIComponent(id));
  return (await response.json())[0]||null;
}
async function setJob(env,id,values) {
  await supa(env,"/rest/v1/acca_match_jobs?on_conflict=match_id",{
    method:"POST",headers:{"Content-Type":"application/json",Prefer:"resolution=merge-duplicates"},
    body:JSON.stringify({match_id:id,updated_at:new Date().toISOString(),...values})
  });
}
async function riot(env,route,id,kind) {
  const endpoint="https://"+route+".api.riotgames.com/lol/match/v5/matches/"+encodeURIComponent(id)+(kind==="timeline"?"/timeline":"");
  const res=await fetch(endpoint,{headers:{"X-Riot-Token":env.RIOT_API_KEY}});
  if (!res.ok) {
    const err=new Error("Riot "+res.status+" ("+kind+")");
    err.status=res.status;
    err.retryAfter=Number(res.headers.get("Retry-After")||0);
    throw err;
  }
  return res.json();
}
async function gzip(value) {
  const bytes=new TextEncoder().encode(JSON.stringify(value));
  const stream=new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
  return {compressed:new Uint8Array(await new Response(stream).arrayBuffer()),raw:bytes};
}
async function sha256(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))]
    .map(x=>x.toString(16).padStart(2,"0")).join("");
}
async function upload(env,path,data) {
  const res=await supa(env,"/storage/v1/object/"+BUCKET+"/"+path,{
    method:"POST",headers:{"Content-Type":"application/gzip","x-upsert":"true"},
    body:data
  });
  return res;
}
async function archive(env,id,match,timeline) {
  const m=await gzip(match),t=await gzip(timeline);
  const mp=id+"/match.json.gz",tp=id+"/timeline.json.gz";
  await upload(env,mp,m.compressed);
  await upload(env,tp,t.compressed);
  // Verify both objects before inserting the durable index.
  for (const [path,expected] of [[mp,match],[tp,timeline]]) {
    const res=await supa(env,"/storage/v1/object/"+BUCKET+"/"+path);
    const stream=res.body.pipeThrough(new DecompressionStream("gzip"));
    const actual=JSON.parse(await new Response(stream).text());
    if (JSON.stringify(actual)!==JSON.stringify(expected)) throw new Error("Supabase readback mismatch: "+path);
  }
  await supa(env,"/rest/v1/acca_match_archives?on_conflict=match_id",{
    method:"POST",headers:{"Content-Type":"application/json",Prefer:"resolution=merge-duplicates"},
    body:JSON.stringify({match_id:id,match_path:mp,timeline_path:tp,match_sha256:await sha256(m.raw),timeline_sha256:await sha256(t.raw)})
  });
}
async function registerPlayer(env,aliasInput,riotInput) {
  const alias=String(aliasInput||"").trim(), riotId=String(riotInput||"").trim();
  if (!alias || alias.length>48 || !/^[\p{L}\p{N} _.-]+$/u.test(alias)) throw new Error("Alias inválido (máximo 48 caracteres).");
  const separator=riotId.lastIndexOf("#");
  if (separator<1 || separator===riotId.length-1 || riotId.length>100) throw new Error("Riot ID inválido. Usá Nombre#TAG.");
  const gameName=riotId.slice(0,separator),tag=riotId.slice(separator+1);
  let account=null;
  for (const region of ["americas","europe","asia","sea"]) {
    const url="https://"+region+".api.riotgames.com/riot/account/v1/accounts/by-riot-id/"+encodeURIComponent(gameName)+"/"+encodeURIComponent(tag);
    const response=await fetch(url,{headers:{"X-Riot-Token":env.RIOT_API_KEY}});
    if (response.ok) { account=await response.json(); break; }
    if (response.status===404) continue;
    if (response.status===429) throw new Error("Riot limitó las consultas. Intentá más tarde.");
    if (response.status===401||response.status===403) throw new Error("Riot rechazó la API Key.");
    throw new Error("Riot HTTP "+response.status);
  }
  if (!account?.puuid) throw new Error("No se encontró ese Riot ID.");
  const aliasKey=alias.toLocaleLowerCase("es");
  const existingRes=await supa(env,"/rest/v1/acca_player_accounts?select=puuid,alias_key,riot_id,riot_ids&puuid=eq."+encodeURIComponent(account.puuid));
  const existing=(await existingRes.json())[0];
  if (existing && existing.alias_key!==aliasKey) throw new Error("Esta cuenta ya pertenece al alias "+existing.alias_key+".");
  const playerRes=await supa(env,"/rest/v1/acca_players?select=alias&alias_key=eq."+encodeURIComponent(aliasKey));
  const player=(await playerRes.json())[0];
  if (!player) await supa(env,"/rest/v1/acca_players?on_conflict=alias_key",{
    method:"POST",headers:{"Content-Type":"application/json",Prefer:"resolution=ignore-duplicates"},
    body:JSON.stringify({alias_key:aliasKey,alias})
  });
  const canonical=account.gameName&&account.tagLine?account.gameName+"#"+account.tagLine:riotId;
  const ids=[...new Set([...(existing?.riot_ids||[]),...(existing?.riot_id?[existing.riot_id]:[]),canonical])];
  await supa(env,"/rest/v1/acca_player_accounts?on_conflict=puuid",{
    method:"POST",headers:{"Content-Type":"application/json",Prefer:"resolution=merge-duplicates"},
    body:JSON.stringify({puuid:account.puuid,alias_key:aliasKey,riot_id:canonical,riot_ids:ids,updated_at:new Date().toISOString()})
  });
  const accountsRes=await supa(env,"/rest/v1/acca_player_accounts?select=puuid&alias_key=eq."+encodeURIComponent(aliasKey));
  const count=(await accountsRes.json()).length;
  return (existing?"Cuenta actualizada":player?"Cuenta agregada":"Jugador creado")+": "+(player?.alias||alias)+" — "+canonical+". Cuentas vinculadas: "+count+".";
}
async function listPlayers(env) {
  const [playersRes,accountsRes]=await Promise.all([
    supa(env,"/rest/v1/acca_players?select=alias_key,alias&order=alias.asc"),
    supa(env,"/rest/v1/acca_player_accounts?select=alias_key,riot_id")
  ]);
  const players=await playersRes.json(),accounts=await accountsRes.json();
  const byAlias=new Map(players.map(p=>[p.alias_key,{alias:p.alias,ids:new Set()}]));
  for(const account of accounts) {
    const entry=byAlias.get(account.alias_key);
    if(entry && account.riot_id) entry.ids.add(account.riot_id);
  }
  if(!players.length) return ["Todavía no hay jugadores registrados."];
  const lines=Array.from(byAlias.values()).map(({alias,ids})=>
    "**"+alias+"** — "+(ids.size?[...ids].join(", "):"Sin Riot ID vinculado"));
  const pages=[],heading="**Jugadores ACCA ("+players.length+")**";
  let page=heading;
  for(const line of lines) {
    if((page+"\n"+line).length>1900) {
      pages.push(page);
      page="**Jugadores ACCA (continuación)**";
    }
    page+="\n"+line;
  }
  pages.push(page);
  return pages;
}
async function followup(env,appId,token,message) {
  if (!appId||!token) return;
  const res=await fetch("https://discord.com/api/v10/webhooks/"+appId+"/"+token,{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({content:message,allowed_mentions:{parse:[]}})
  });
  if (!res.ok) console.error("Discord followup HTTP",res.status);
}
async function registerCommands(env) {
  if (!env.DISCORD_BOT_TOKEN||!env.DISCORD_APPLICATION_ID) throw new Error("Discord Bot Token/Application ID missing");
  const commands=[
    {name:"partida",description:"Archivar una partida oficial de ACCA",options:[{name:"id",description:"ID de Riot (ej. LA2_1638964338)",type:3,required:true}]},
    {name:"jugador",description:"Vincular una cuenta de Riot a un alias ACCA",options:[
      {name:"alias",description:"Alias estable del jugador",type:3,required:true},
      {name:"riot_id",description:"Nombre#TAG de Riot",type:3,required:true}
    ]},
    {name:"lista",description:"Ver los jugadores ACCA y sus Riot IDs"}
  ];
  const auth={"Authorization":"Bot "+env.DISCORD_BOT_TOKEN,"Content-Type":"application/json"};
  const root="https://discord.com/api/v10/applications/"+env.DISCORD_APPLICATION_ID;
  const request=async (url,method,body) => {
    const res=await fetch(url,{method,headers:auth,...(body?{body:JSON.stringify(body)}:{})});
    const result=await res.json().catch(()=>null);
    if(!res.ok) throw new Error("Discord HTTP "+res.status+": "+JSON.stringify(result).slice(0,220));
    return result;
  };
  // Guild commands only. Remove our former global copies to avoid duplicates.
  const globalCommands=await request(root+"/commands","GET");
  for (const command of globalCommands) {
    if (commands.some(c=>c.name===command.name)||command.name==="jugadores") {
      await request(root+"/commands/"+command.id,"DELETE");
    }
  }
  const guilds=await request("https://discord.com/api/v10/users/@me/guilds","GET");
  if (!Array.isArray(guilds)) throw new Error("Discord did not return guilds");
  const results=[];
  for(const guild of guilds) {
    const endpoint=root+"/guilds/"+guild.id+"/commands";
    await request(endpoint,"PUT",commands);
    const actual=await request(endpoint,"GET");
    if(!commands.every(c=>actual.some(x=>x.name===c.name)))
      throw new Error("Discord guild "+guild.id+" is missing slash commands");
    results.push({guild_id:guild.id,commands:commands.map(c=>c.name)});
  }
  if(!results.length) throw new Error("Bot is not present in any Discord guild");
  return results;
}
export default {
  async scheduled(event,env,ctx) { ctx.waitUntil(registerCommands(env)); },
  async fetch(request,env) {
    const url=new URL(request.url);
    if (request.method==="POST" && url.pathname==="/__register-commands") {
      const token=request.headers.get("X-ACCA-Registration-Key")||"";
      if (!env.ACCA_REGISTRATION_KEY || token.length<32 || token!==env.ACCA_REGISTRATION_KEY)
        return new Response("Unauthorized",{status:401});
      try {
        const guilds=await registerCommands(env);
        return json({ok:true,registered:["partida","jugador","lista"],guilds});
      } catch (error) {
        console.error("Discord command registration failed",String(error));
        return new Response(JSON.stringify({ok:false,error:String(error).slice(0,300)}),{status:502,headers:{"Content-Type":"application/json"}});
      }
    }
    if (request.method!=="POST") return new Response("ACCA Discord Bot");
    const body=await request.text();
    if (!await verified(request,body,env.DISCORD_PUBLIC_KEY)) return new Response("Unauthorized",{status:401});
    let interaction;
    try { interaction=JSON.parse(body); } catch { return new Response("Invalid JSON",{status:400}); }
    if (interaction.type===1) return json({type:1});
    if (interaction.type!==2||!["partida","jugador","lista"].includes(interaction.data?.name)) return json({type:4,data:{content:"Comando no reconocido.",flags:64}});
    const allowed=(env.DISCORD_ALLOWED_ROLE_IDS||"").split(",").map(x=>x.trim()).filter(Boolean);
    const roles=interaction.member?.roles||[];
    const administrator=(BigInt(interaction.member?.permissions||"0")&8n)!==0n;
    if (!interaction.guild_id||!(administrator||allowed.some(role=>roles.includes(role))))
      return json({type:4,data:{content:"No tenés permiso para registrar partidas.",flags:64}});
    if (interaction.data.name==="lista") {
      try {
        const pages=await listPlayers(env);
        return json({type:4,data:{
          embeds:pages.slice(0,10).map(description=>({description})),
          flags:64,allowed_mentions:{parse:[]}
        }});
      } catch(error) {
        console.error("Error listando jugadores",String(error));
        return json({type:4,data:{content:"No se pudo consultar la lista de jugadores.",flags:64}});
      }
    }
    if (interaction.data.name==="jugador") {
      const alias=interaction.data.options?.find(x=>x.name==="alias")?.value;
      const riotId=interaction.data.options?.find(x=>x.name==="riot_id")?.value;
      try {
        const result=await registerPlayer(env,alias,riotId);
        await env.ACCA_QUEUE.send({type:"players"});
        return json({type:4,data:{content:result+" Actualizando Google Sheets.",flags:64}});
      } catch(error) {
        console.error("Error registrando jugador",String(error));
        return json({type:4,data:{content:String(error.message||error).slice(0,190),flags:64}});
      }
    }
    const id=normalize(interaction.data.options?.find(x=>x.name==="id")?.value);
    if (!id) return json({type:4,data:{content:"ID inválido. Ejemplo: LA2_1638914973",flags:64}});
    try {
      const current=await getJob(env,id);
      if (current?.status==="ARCHIVADA"||current?.status==="PUBLICADA") return json({type:4,data:{content:"La partida "+id+" ya está archivada. Estado: "+current.status,flags:64}});
      if (current?.status==="PROCESANDO"||current?.status==="PENDIENTE")
        return json({type:4,data:{content:"La partida "+id+" ya está en proceso.",flags:64}});
      await setJob(env,id,{status:"PENDIENTE",requested_by:interaction.member?.user?.id||null,discord_application_id:interaction.application_id,error_message:null});
      try {
        await env.ACCA_QUEUE.send({id,appId:interaction.application_id,token:interaction.token});
      } catch (error) {
        await setJob(env,id,{status:"ERROR",error_message:"No se pudo encolar el trabajo"});
        throw error;
      }
      return json({type:4,data:{content:"Partida "+id+" recibida. Descarga en curso.",flags:64}});
    } catch(e) {
      console.error("Error encolando",String(e));
      return json({type:4,data:{content:"No se pudo encolar la partida. Intentá nuevamente.",flags:64}});
    }
  },
  async queue(batch,env) {
    for(const msg of batch.messages) {
      if(msg.body.type==="players") {
        try { await publishPlayers(env); msg.ack(); }
        catch(e) {
          console.error("Error publicando jugadores en Sheets",String(e));
          if(msg.attempts>=5) msg.ack();
          else msg.retry({delaySeconds:Math.min(120,10*msg.attempts)});
        }
        continue;
      }
      const {id,appId,token}=msg.body;
      try {
        const previous=await getJob(env,id);
        if(previous?.status!=="ARCHIVADA" && previous?.status!=="PUBLICADA") {
          await setJob(env,id,{status:"PROCESANDO",error_message:null});
          const route=ROUTES[id.split("_")[0]];
          const [match,timeline]=await Promise.all([riot(env,route,id,"match"),riot(env,route,id,"timeline")]);
          await archive(env,id,match,timeline);
          await setJob(env,id,{status:"ARCHIVADA",archived_at:new Date().toISOString(),error_message:null});
        }
        await publishMatch(env);
        await setJob(env,id,{status:"PUBLICADA",error_message:null});
        await followup(env,appId,token,"Partida "+id+" archivada y estadísticas publicadas en Google Sheets.");
        msg.ack();
      } catch(e) {
        console.error("Error procesando o publicando partida",id,String(e));
        if([400,403,404].includes(e.status)) {
          await setJob(env,id,{status:"ERROR",error_message:String(e).slice(0,160)});
          await followup(env,appId,token,"No se pudo descargar "+id+": "+String(e));
          msg.ack();
        } else if(msg.attempts>=5) {
          await setJob(env,id,{status:"ARCHIVADA",error_message:"Publicación pendiente: "+String(e).slice(0,120)});
          await followup(env,appId,token,"Partida "+id+" archivada, pero falló la publicación. La sincronización periódica volverá a intentarlo.");
          msg.ack();
        } else {
          msg.retry({delaySeconds:Math.min(120,Math.max(10,e.retryAfter||10*msg.attempts))});
        }
      }
    }
  }
};
