// Immediate Google Sheets publication after Discord registration / match archival.
// The periodic Python sync remains a reconciliation job, not the primary trigger.
const GENERAL="1_QVHhGoob2hDig1JDTmJttAQIIL9CKS4ea69_ozWNQc";
const PLAYERS="1y-1FHI4jFyAm9c1m0pz9nnfuAZ7xch0aKDvGgbLP4_A";
const ROOT="https://sheets.googleapis.com/v4/spreadsheets/";
const enc=s=>encodeURIComponent(s);
const b64url=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=/g,"").replace(/\\+/g,"-").replace(/\\//g,"_");
async function token(env) {
  if(!env.GOOGLE_CREDENTIALS_JSON) throw Error("Google service account not configured");
  const key=JSON.parse(env.GOOGLE_CREDENTIALS_JSON);
  const now=Math.floor(Date.now()/1000);
  const h=b64url(new TextEncoder().encode(JSON.stringify({alg:"RS256",typ:"JWT"})));
  const p=b64url(new TextEncoder().encode(JSON.stringify({iss:key.client_email,scope:"https://www.googleapis.com/auth/spreadsheets",aud:"https://oauth2.googleapis.com/token",iat:now,exp:now+3300})));
  const pem=key.private_key.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\\s/g,"");
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
async function updatePage(access,alias,accounts,archives) {
  const ws=await ensurePlayerSheet(access,alias);
  const own=participantsFor(alias,accounts,archives);
  const wins=own.filter(p=>p.win).length;
  const byChampion=new Map(),byRole=new Map(),byItem=new Map();
  for(const p of own) {
    const c=p.championName||"Desconocido",r=p.teamPosition||p.individualPosition||"UNKNOWN";
    const cs=(p.totalMinionsKilled||0)+(p.neutralMinionsKilled||0);
    const minutes=(p.match.info.gameDuration||0)/60;
    const record=byChampion.get(c)||{count:0,wins:0,k:0,d:0,a:0};
    record.count++;record.wins+=p.win?1:0;record.k+=p.kills||0;record.d+=p.deaths||0;record.a+=p.assists||0;
    byChampion.set(c,record);
    const role=byRole.get(r)||{count:0,wins:0};role.count++;role.wins+=p.win?1:0;byRole.set(r,role);
    for(let i=0;i<7;i++){const item=p["item"+i];if(item>0)byItem.set(item,(byItem.get(item)||0)+1);}
  }
  const rows=[
    [alias+" — ACCA"],["Riot IDs: "+accounts.filter(a=>a.alias_key===alias.toLocaleLowerCase("es")).map(a=>a.riot_id).join(" | ")],
    [],["Resumen histórico"],["PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","KILLS","MUERTES","ASISTENCIAS","KDA","DAÑO_PROM","ORO_PROM","CS_MIN","VISION"],
    [own.length,wins,own.length-wins,own.length?Math.round(wins/own.length*10000)/100:0,
     own.reduce((n,p)=>n+(p.kills||0),0),own.reduce((n,p)=>n+(p.deaths||0),0),own.reduce((n,p)=>n+(p.assists||0),0),
     own.length?(own.reduce((n,p)=>n+(p.kills||0)+(p.assists||0),0)/Math.max(1,own.reduce((n,p)=>n+(p.deaths||0),0))).toFixed(2):0,
     own.length?Math.round(own.reduce((n,p)=>n+(p.totalDamageDealtToChampions||0),0)/own.length):0,
     own.length?Math.round(own.reduce((n,p)=>n+(p.goldEarned||0),0)/own.length):0,
     own.length?(own.reduce((n,p)=>n+((p.totalMinionsKilled||0)+(p.neutralMinionsKilled||0))/Math.max(1,(p.match.info.gameDuration||0)/60),0)/own.length).toFixed(2):0,
     own.length?Math.round(own.reduce((n,p)=>n+(p.visionScore||0),0)/own.length):0],
    [],["Por campeón"],["CAMPEON","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","KILLS","MUERTES","ASISTENCIAS"],
    ...[...byChampion].sort((a,b)=>b[1].count-a[1].count).map(([c,x])=>[c,x.count,x.wins,x.count-x.wins,Math.round(x.wins/x.count*10000)/100,x.k,x.d,x.a]),
    [],["Por rol"],["ROL","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%"],
    ...[...byRole].sort((a,b)=>b[1].count-a[1].count).map(([r,x])=>[r,x.count,x.wins,x.count-x.wins,Math.round(x.wins/x.count*10000)/100]),
    [],["Objetos finales más usados"],["ITEM_ID","APARICIONES"],
    ...[...byItem].sort((a,b)=>b[1]-a[1]).slice(0,30),
    [],["Historial de partidas"],["MATCH_ID","FECHA_UTC","RESULTADO","CAMPEON","ROL","KILLS","DEATHS","ASSISTS","KDA","CS_MIN","ORO","DANO","VISION","ITEMS"],
    ...own.map(p=>[p.matchId,new Date(p.match.info.gameCreation||0).toISOString(),result(p),p.championName||"",p.teamPosition||"",p.kills||0,p.deaths||0,p.assists||0,
      ((p.kills+p.assists)/Math.max(1,p.deaths)).toFixed(2),(((p.totalMinionsKilled||0)+(p.neutralMinionsKilled||0))/Math.max(1,(p.match.info.gameDuration||0)/60)).toFixed(2),
      p.goldEarned||0,p.totalDamageDealtToChampions||0,p.visionScore||0,Array.from({length:7},(_,i)=>p["item"+i]).filter(x=>x>0).join("|")])
  ];
  await google(access,PLAYERS,":batchUpdate","POST",{requests:[
    {updateCells:{range:{sheetId:ws.properties.sheetId,startRowIndex:0},fields:"userEnteredValue"}},
    {updateSheetProperties:{properties:{sheetId:ws.properties.sheetId,gridProperties:{hideGridlines:true}},fields:"gridProperties.hideGridlines"}}
  ]});
  await put(access,PLAYERS,[[titleRange(ws.properties.title,"A1"),rows]]);
  const headings=new Set(["Resumen histórico","Por campeón","Por rol","Objetos finales más usados","Historial de partidas"]);
  const requests=[];
  rows.forEach((r,i)=>{
    if(i===0||headings.has(r[0])||(i>0&&headings.has(rows[i-1]?.[0]))) {
      const dark=i===0||headings.has(r[0]);
      requests.push({repeatCell:{range:{sheetId:ws.properties.sheetId,startRowIndex:i,endRowIndex:i+1,startColumnIndex:0,endColumnIndex:14},
        cell:{userEnteredFormat:{backgroundColor:dark?{red:0.12,green:0.19,blue:0.30}:{red:0.91,green:0.94,blue:0.98},
          textFormat:{bold:true,fontSize:i===0?16:11,foregroundColor:dark?{red:1,green:1,blue:1}:{red:0.12,green:0.18,blue:0.27}}}},
        fields:"userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"}});
    }
  });
  requests.push({updateDimensionProperties:{range:{sheetId:ws.properties.sheetId,dimension:"COLUMNS",startIndex:0,endIndex:1},properties:{pixelSize:280},fields:"pixelSize"}});
  await google(access,PLAYERS,":batchUpdate","POST",{requests});
  return ws.properties.sheetId;
}
function titleRange(title,range){return "'"+title.replace(/'/g,"''")+"'!"+range;}
async function archived(env) {
  const list=await supabase(env,"/rest/v1/acca_match_archives?select=match_id,match_path&order=created_at.asc");
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
export async function publishMatch(env) {
  await publishPlayers(env);
  const access=await token(env),data=await archived(env),{accounts}=await roster(env);
  const puuids=new Set(accounts.map(a=>a.puuid));
  const matchRows=data.map(({id,match})=>{
    const info=match.info||{},own=info.participants||[];
    const registered=own.filter(p=>puuids.has(p.puuid));
    const side=registered[0]?.teamId||100;
    const acca=own.filter(p=>p.teamId===side),rival=own.filter(p=>p.teamId!==side);
    const bans=team=>(info.teams||[]).find(t=>t.teamId===team)?.bans?.map(b=>b.championId).join("|")||"";
    return [id,new Date(info.gameCreation||0).toISOString(),info.gameMode||"",info.gameVersion||"",side===100?"AZUL":"ROJO",registered[0]?.win?"VICTORIA":"DERROTA",
      acca.map(p=>p.championName).join(" | "),registered.length,acca.map(p=>p.championName).join(" + "),rival.map(p=>p.championName).join(" + "),bans(side),bans(side===100?200:100),info.gameDuration||0,"DISCORD"];
  });
  const wins=matchRows.filter(r=>r[5]==="VICTORIA").length;
  await put(access,GENERAL,[
    ["PARTIDAS!A1",[["MATCH_ID","FECHA_UTC","MODO","VERSION","LADO_ACCA","RESULTADO","EQUIPO_ACCA","N_ACCA","COMPOSICION_ACCA","COMPOSICION_RIVAL","BANS_ACCA","BANS_RIVAL","DURACION_SEG","FUENTE"],...matchRows]],
    ["RESUMEN!A1",[["SECCION","CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],["ACCA","TOTAL",matchRows.length,wins,matchRows.length-wins,matchRows.length?Math.round(wins/matchRows.length*10000)/100:0,0]]],
    ["REGISTRO!A1",[["MATCH_ID","ESTADO","MENSAJE","LADO_ACCA","PROCESADA_UTC"],...matchRows.map(r=>[r[0],"PROCESADA","",r[4],new Date().toISOString()])]]
  ]);
}
