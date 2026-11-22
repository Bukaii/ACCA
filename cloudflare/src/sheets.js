// Immediate Google Sheets publication after Discord registration / match archival.
// The periodic Python sync remains a reconciliation job, not the primary trigger.
const GENERAL="1_QVHhGoob2hDig1JDTmJttAQIIL9CKS4ea69_ozWNQc";
const PLAYERS="1y-1FHI4jFyAm9c1m0pz9nnfuAZ7xch0aKDvGgbLP4_A";
const ROOT="https://sheets.googleapis.com/v4/spreadsheets/";
const enc=s=>encodeURIComponent(s);
const b64url=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=/g,"").replace(/\+/g,"-").replace(/\//g,"_");
async function token(env) {
  if(!env.GOOGLE_CREDENTIALS_JSON) throw Error("Google service account not configured");
  const key=JSON.parse(env.GOOGLE_CREDENTIALS_JSON);
  const now=Math.floor(Date.now()/1000);
  const h=b64url(new TextEncoder().encode(JSON.stringify({alg:"RS256",typ:"JWT"})));
  const p=b64url(new TextEncoder().encode(JSON.stringify({iss:key.client_email,scope:"https://www.googleapis.com/auth/spreadsheets",aud:"https://oauth2.googleapis.com/token",iat:now,exp:now+3300})));
  const pem=key.private_key.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g,"");
  const raw=Uint8Array.from(atob(pem),c=>c.charCodeAt(0));
  const cryptoKey=await crypto.subtle.importKey("pkcs8",raw,{name:"RSASSA-PKCS1-v1_5",hash:"SHA-256"},false,["sign"]);
  const signature=await crypto.subtle.sign("RSASSA-PKCS1-v1_5",cryptoKey,new TextEncoder().encode(h+"."+p));
  const body=new URLSearchParams({grant_type:"urn:ietf:params:oauth:grant-type:jwt-bearer",assertion:h+"."+p+"."+b64url(signature)});
  const response=await fetch("https://oauth2.googleapis.com/token",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body});
  if(!response.ok) throw Error("Google OAuth HTTP "+response.status+": "+(await response.text()).slice(0,150));
  return (await response.json()).access_token;
}
async function google(access,id,endpoint,method="GET",body) {
  const res=await fetch(ROOT+id+endpoint,{method,headers:{Authorization:"Bearer "+access,...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});
  if(!res.ok) throw Error("Google Sheets "+res.status+" "+(await res.text()).slice(0,280));
  return res.json();
}
async function read(access,id,range) {
  const data=await google(access,id,"/values/"+enc(range));
  return data.values||[];
}
async function put(access,id,ranges) {
  await google(access,id,"/values:batchUpdate","POST",{valueInputOption:"RAW",data:ranges.map(([range,values])=>({range,values}))});
}
async function metadata(access,id) {
  return google(access,id,"?fields=sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))");
}
async function ensurePlayerSheet(access,alias) {
  const title="J_"+alias.slice(0,95);
  const meta=await metadata(access,PLAYERS);
  let ws=meta.sheets.find(s=>s.properties.title===title);
  if(!ws) {
    const created=await google(access,PLAYERS,":batchUpdate","POST",{requests:[{addSheet:{properties:{title,gridProperties:{rowCount:200,columnCount:18,hideGridlines:true}}}}]});
    ws={properties:{title,sheetId:created.replies[0].addSheet.properties.sheetId}};
  }
  return ws;
}
async function linkedAlias(access,alias,row,gid) {
  const meta=await metadata(access,PLAYERS);
  const sheet=meta.sheets.find(s=>s.properties.title==="JUGADORES");
  if(!sheet) throw Error("JUGADORES tab missing");
  await google(access,PLAYERS,":batchUpdate","POST",{requests:[{updateCells:{
    start:{sheetId:sheet.properties.sheetId,rowIndex:row,columnIndex:0},
    rows:[{values:[{userEnteredValue:{stringValue:alias},userEnteredFormat:{textFormat:{
      link:{uri:"https://docs.google.com/spreadsheets/d/"+PLAYERS+"/edit#gid="+gid},
      foregroundColor:{red:0.10,green:0.33,blue:0.80},underline:true
    }}}]}],
    fields:"userEnteredValue,userEnteredFormat.textFormat"
  }}]});
}
function rowForAccount(p,accounts) {
  const account=accounts.find(a=>a.alias_key===p.alias_key);
  return [p.alias,account?.riot_id||"","","SI",account?.puuid||"","OK",""];
}
async function supabase(env,path) {
  const res=await fetch(env.SUPABASE_URL+path,{headers:{apikey:env.SUPABASE_SECRET_KEY,Authorization:"Bearer "+env.SUPABASE_SECRET_KEY}});
  if(!res.ok) throw Error("Supabase HTTP "+res.status);
  return res.json();
}
async function roster(env) {
  const [players,accounts]=await Promise.all([
    supabase(env,"/rest/v1/acca_players?select=alias,alias_key&order=alias.asc"),
    supabase(env,"/rest/v1/acca_player_accounts?select=alias_key,puuid,riot_id")
  ]);
  return {players,accounts};
}
function participantsFor(alias,accounts,archives) {
  const puuids=new Set(accounts.filter(a=>a.alias_key===alias.toLocaleLowerCase("es")).map(a=>a.puuid));
  return archives.flatMap(({id,match})=>(match.info?.participants||[]).filter(p=>puuids.has(p.puuid)).map(p=>({...p,matchId:id,match})));
}
function result(p) {
  return p.win?"VICTORIA":"DERROTA";
}
const itemCache=new Map();
async function itemNamesFor(version) {
  const patch=String(version||"").split(".").slice(0,2).join(".");
  if(!patch) return {};
  if(!itemCache.has(patch)) {
    itemCache.set(patch,(async()=>{
      const url="https://ddragon.leagueoflegends.com/cdn/"+patch+".1/data/es_MX/item.json";
      const response=await fetch(url);
      if(!response.ok) throw Error("Riot Data Dragon items HTTP "+response.status+" for "+patch);
      const data=(await response.json()).data||{};
      return Object.fromEntries(Object.entries(data).map(([id,item])=>[id,item.name]));
    })());
  }
  return itemCache.get(patch);
}
function finalItemIds(p) {
  return Array.from({length:7},(_,i)=>Number(p["item"+i])||0).filter(id=>id>0);
}
function itemLabel(id,names) {
  return names[String(id)]||"Objeto "+id;
}
async function updatePage(access,alias,accounts,archives) {
  const ws=await ensurePlayerSheet(access,alias);
  const own=participantsFor(alias,accounts,archives);
  const wins=own.filter(p=>p.win).length;
  const byChampion=new Map(),byRole=new Map(),byItem=new Map();
  const namesByPatch=new Map();
  for(const {match} of archives) {
    const version=match.info?.gameVersion||"";
    if(!namesByPatch.has(version)) namesByPatch.set(version,await itemNamesFor(version));
  }
  const namesFor=p=>namesByPatch.get(p.match.info?.gameVersion||"")||{};
  for(const p of own) {
    const c=p.championName||"Desconocido",r=p.teamPosition||p.individualPosition||"UNKNOWN";
    const cs=(p.totalMinionsKilled||0)+(p.neutralMinionsKilled||0);
    const minutes=(p.match.info.gameDuration||0)/60;
    const record=byChampion.get(c)||{count:0,wins:0,k:0,d:0,a:0};
    record.count++;record.wins+=p.win?1:0;record.k+=p.kills||0;record.d+=p.deaths||0;record.a+=p.assists||0;
    byChampion.set(c,record);
    const role=byRole.get(r)||{count:0,wins:0};role.count++;role.wins+=p.win?1:0;byRole.set(r,role);
    for(const id of finalItemIds(p)) {
      const name=itemLabel(id,namesFor(p));
      byItem.set(name,(byItem.get(name)||0)+1);
    }
  }
  const sum=(field)=>own.reduce((n,p)=>n+(Number(p[field])||0),0);
  const avg=(fn)=>own.length?Math.round(100*own.reduce((n,p)=>n+fn(p),0)/own.length)/100:0;
  const wilson=own.length?Math.max(0,100*((wins/own.length+1.96*1.96/(2*own.length)-1.96*Math.sqrt((wins/own.length)*(1-wins/own.length)/own.length+1.96*1.96/(4*own.length*own.length)))/(1+1.96*1.96/own.length))):0;
  const rate=(w,n)=>n?Math.round(w/n*10000)/100:0;
  const rows=[
    [alias+" — "+accounts.filter(a=>a.alias_key===alias.toLocaleLowerCase("es")).map(a=>a.riot_id).join(" · ")],
    ["Región: LA2 · Actualizado (UTC): "+new Date().toISOString().replace("T"," ").slice(0,19)],
    ["Informe individual de ACCA · partidas oficiales registradas, rendimiento, campeones, posiciones, objetos y detalle histórico."],
    [],
    ["Resumen histórico"],
    ["PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%","CONFIANZA","KILLS","MUERTES","ASISTENCIAS","KDA","DAÑO_PROM","ORO_PROM","CS_MIN","VISION","N_DETALLE"],
    [own.length,wins,own.length-wins,rate(wins,own.length),Math.round(wilson*100)/100,own.length<10?"BAJA":own.length<30?"MEDIA":"ALTA",
      sum("kills"),sum("deaths"),sum("assists"),own.length?Math.round((sum("kills")+sum("assists"))/Math.max(1,sum("deaths"))*100)/100:0,
      avg(p=>p.totalDamageDealtToChampions||0),avg(p=>p.goldEarned||0),
      avg(p=>((p.totalMinionsKilled||0)+(p.neutralMinionsKilled||0))/Math.max(1,(p.match.info.gameDuration||0)/60)),
      avg(p=>p.visionScore||0),own.length],
    [],
    ["Por campeón"],
    ["Rendimiento por campeón utilizado en partidas oficiales."],
    ["CAMPEON","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%","KILLS","MUERTES","ASISTENCIAS","KDA"],
    ...[...byChampion].sort((a,b)=>b[1].count-a[1].count).map(([c,x])=>[c,x.count,x.wins,x.count-x.wins,rate(x.wins,x.count),0,x.k,x.d,x.a,Math.round((x.k+x.a)/Math.max(1,x.d)*100)/100]),
    [],
    ["Por rol"],
    ["Resultados agrupados por posición jugada."],
    ["ROL","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
    ...[...byRole].sort((a,b)=>b[1].count-a[1].count).map(([r,x])=>[r,x.count,x.wins,x.count-x.wins,rate(x.wins,x.count),0]),
    [],
    ["Objetos finales más usados"],
    ["Frecuencia de objetos en las partidas registradas."],
    ["OBJETO","APARICIONES"],
    ...[...byItem].sort((a,b)=>b[1]-a[1]).slice(0,30),
    [],
    ["Historial de partidas registradas"],
    ["Detalle de cada participación oficial; incluye resultados y métricas de Riot."],
    ["MATCH_ID","FECHA_UTC","MODO","RESULTADO","CAMPEON","ROL","KILLS","DEATHS","ASSISTS","KDA","CS_MIN","ORO","DANO","VISION","ITEMS"],
    ...own.map(p=>[p.matchId,new Date(p.match.info.gameCreation||0).toISOString(),p.match.info.gameMode||"",result(p),p.championName||"",p.teamPosition||p.individualPosition||"",
      p.kills||0,p.deaths||0,p.assists||0,Math.round(((p.kills||0)+(p.assists||0))/Math.max(1,p.deaths||0)*100)/100,
      Math.round(((p.totalMinionsKilled||0)+(p.neutralMinionsKilled||0))/Math.max(1,(p.match.info.gameDuration||0)/60)*100)/100,
      p.goldEarned||0,p.totalDamageDealtToChampions||0,p.visionScore||0,finalItemIds(p).map(id=>itemLabel(id,namesFor(p))).join(" | ")])
  ];
  const width=18, navy={red:0.12,green:0.19,blue:0.30},blue={red:0.19,green:0.32,blue:0.49},
    pale={red:0.91,green:0.94,blue:0.98},white={red:1,green:1,blue:1},ink={red:0.12,green:0.18,blue:0.27};
  const meta=await google(access,PLAYERS,"?fields=sheets(properties(sheetId),merges)");
  const existing=meta.sheets.find(sh=>sh.properties.sheetId===ws.properties.sheetId);
  const requests=(existing?.merges||[]).map(range=>({unmergeCells:{range}}));
  requests.push(
    {updateCells:{range:{sheetId:ws.properties.sheetId,startRowIndex:0},fields:"userEnteredValue"}},
    {updateSheetProperties:{properties:{sheetId:ws.properties.sheetId,gridProperties:{hideGridlines:true,frozenRowCount:0}},fields:"gridProperties.hideGridlines,gridProperties.frozenRowCount"}},
    {repeatCell:{range:{sheetId:ws.properties.sheetId},cell:{userEnteredFormat:{backgroundColor:white,horizontalAlignment:"LEFT",verticalAlignment:"MIDDLE",wrapStrategy:"WRAP",textFormat:{fontSize:10,foregroundColor:ink}}},fields:"userEnteredFormat"}},
    {updateDimensionProperties:{range:{sheetId:ws.properties.sheetId,dimension:"COLUMNS",startIndex:0,endIndex:1},properties:{pixelSize:350},fields:"pixelSize"}},
    {updateDimensionProperties:{range:{sheetId:ws.properties.sheetId,dimension:"COLUMNS",startIndex:1,endIndex:2},properties:{pixelSize:260},fields:"pixelSize"}},
    {updateDimensionProperties:{range:{sheetId:ws.properties.sheetId,dimension:"COLUMNS",startIndex:2,endIndex:width},properties:{pixelSize:145},fields:"pixelSize"}}
  );
  const sections=new Set(["Resumen histórico","Por campeón","Por rol","Objetos finales más usados","Historial de partidas registradas"]);
  const headers=new Set(["PARTIDAS","CAMPEON","ROL","OBJETO","MATCH_ID"]);
  function paint(i,color,fontColor,fontSize,height,bold=true) {
    requests.push({repeatCell:{range:{sheetId:ws.properties.sheetId,startRowIndex:i,endRowIndex:i+1,startColumnIndex:0,endColumnIndex:width},
      cell:{userEnteredFormat:{backgroundColor:color,horizontalAlignment:"LEFT",verticalAlignment:"MIDDLE",wrapStrategy:"WRAP",
        textFormat:{bold,fontSize,foregroundColor:fontColor}}},fields:"userEnteredFormat"}});
    requests.push({updateDimensionProperties:{range:{sheetId:ws.properties.sheetId,dimension:"ROWS",startIndex:i,endIndex:i+1},properties:{pixelSize:height},fields:"pixelSize"}});
  }
  rows.forEach((row,i)=>{
    const first=row[0];
    if(i===0) paint(i,navy,white,15,68);
    else if(i===1||i===2) paint(i,pale,ink,10,i===1?36:58,false);
    else if(sections.has(first)) {
      paint(i,blue,white,15,58);
      requests.push({updateBorders:{range:{sheetId:ws.properties.sheetId,startRowIndex:i,endRowIndex:i+1,startColumnIndex:0,endColumnIndex:width},
        top:{style:"SOLID_MEDIUM",color:navy}}});
    } else if(headers.has(first)&&row.length>1) paint(i,pale,ink,10,46);
    else if(row.length===1&&first) paint(i,{red:0.95,green:0.97,blue:0.99},ink,10,46,false);
    else if(row.length>1) paint(i,i%2?white:{red:0.97,green:0.98,blue:0.99},ink,10,34,false);
    else requests.push({updateDimensionProperties:{range:{sheetId:ws.properties.sheetId,dimension:"ROWS",startIndex:i,endIndex:i+1},properties:{pixelSize:22},fields:"pixelSize"}});
    if(row.length===1&&first) requests.push({mergeCells:{range:{sheetId:ws.properties.sheetId,startRowIndex:i,endRowIndex:i+1,startColumnIndex:0,endColumnIndex:width},mergeType:"MERGE_ALL"}});
  });
  await google(access,PLAYERS,":batchUpdate","POST",{requests});
  await put(access,PLAYERS,[[titleRange(ws.properties.title,"A1"),rows]]);
  return ws.properties.sheetId;
}
function titleRange(title,range){return "'"+title.replace(/'/g,"''")+"'!"+range;}
async function archived(env) {
  const list=await supabase(env,"/rest/v1/acca_match_archives?select=match_id,match_path&order=stored_at.asc");
  const archives=[];
  for(const entry of list) {
    const res=await fetch(env.SUPABASE_URL+"/storage/v1/object/acca-riot-json/"+entry.match_path,{
      headers:{apikey:env.SUPABASE_SECRET_KEY,Authorization:"Bearer "+env.SUPABASE_SECRET_KEY}});
    if(!res.ok) throw Error("Archive HTTP "+res.status);
    const body=res.body.pipeThrough(new DecompressionStream("gzip"));
    archives.push({id:entry.match_id,match:JSON.parse(await new Response(body).text())});
  }
  return archives;
}
export async function publishPlayers(env) {
  const access=await token(env),{players,accounts}=await roster(env);
  const rows=players.flatMap(p=>{const ac=accounts.filter(a=>a.alias_key===p.alias_key);return ac.length?ac.map(a=>[p.alias,a.riot_id,"","SI",a.puuid,"OK",""]):[[p.alias,"","","SI","","OK",""]];});
  await put(access,PLAYERS,[["JUGADORES!A1", [["ALIAS","RIOT_ID","REGION","ACTIVO","PUUID","ESTADO","MENSAJE"],...rows]]]);
  const archiveData=await archived(env);
  let index=1;
  for(const p of players) {
    const gid=await updatePage(access,p.alias,accounts,archiveData);
    await linkedAlias(access,p.alias,index,gid);
    index+=Math.max(1,accounts.filter(a=>a.alias_key===p.alias_key).length);
  }
}
const championCache=new Map();
async function championNamesFor(version) {
  const patch=String(version||"").split(".").slice(0,2).join(".");
  if(!patch) return {};
  if(!championCache.has(patch)) championCache.set(patch,(async()=>{
    const response=await fetch("https://ddragon.leagueoflegends.com/cdn/"+patch+".1/data/es_MX/champion.json");
    if(!response.ok) throw Error("Riot Data Dragon champions HTTP "+response.status+" for "+patch);
    const data=(await response.json()).data||{};
    return Object.fromEntries(Object.values(data).map(c=>[String(c.key),c.name]));
  })());
  return championCache.get(patch);
}
export async function publishMatch(env) {
  await publishPlayers(env);
  const access=await token(env),data=await archived(env),{accounts}=await roster(env);
  const puuids=new Set(accounts.map(a=>a.puuid));
  const matchRows=await Promise.all(data.map(async ({id,match})=>{
    const info=match.info||{},own=info.participants||[];
    const registered=own.filter(p=>puuids.has(p.puuid));
    const side=registered[0]?.teamId||100;
    const acca=own.filter(p=>p.teamId===side),rival=own.filter(p=>p.teamId!==side);
    const championNames=await championNamesFor(info.gameVersion);
    const bans=team=>(info.teams||[]).find(t=>t.teamId===team)?.bans?.map(b=>b.championId<=0?"Sin ban":(championNames[String(b.championId)]||"ID "+b.championId)).join(" + ")||"Sin baneos";
    return [id,new Date(info.gameCreation||0).toISOString(),info.gameMode||"",info.gameVersion||"",side===100?"AZUL":"ROJO",registered[0]?.win?"VICTORIA":"DERROTA",
      acca.map(p=>p.championName).join(" | "),registered.length,acca.map(p=>p.championName).join(" + "),rival.map(p=>p.championName).join(" + "),bans(side),bans(side===100?200:100),info.gameDuration||0,"DISCORD"];
  }));
  const wins=matchRows.filter(r=>r[5]==="VICTORIA").length;
  await put(access,GENERAL,[
    ["PARTIDAS!A1",[["MATCH_ID","FECHA_UTC","MODO","VERSION","LADO_ACCA","RESULTADO","EQUIPO_ACCA","N_ACCA","COMPOSICION_ACCA","COMPOSICION_RIVAL","BANS_ACCA","BANS_RIVAL","DURACION_SEG","FUENTE"],...matchRows]],
    ["RESUMEN!A1",[["SECCION","CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],["ACCA","TOTAL",matchRows.length,wins,matchRows.length-wins,matchRows.length?Math.round(wins/matchRows.length*10000)/100:0,0]]],
    ["REGISTRO!A1",[["MATCH_ID","ESTADO","MENSAJE","LADO_ACCA","PROCESADA_UTC"],...matchRows.map(r=>[r[0],"PROCESADA","",r[4],new Date().toISOString()])]]
  ]);
}
