"""Detailed match analytics and lossless, compressed Riot payload archiving."""
import base64
import gzip
import json
from collections import defaultdict

RAW_ARCHIVE = ["MATCH_ID","TIPO","PARTE","TOTAL_PARTES","GZIP_BASE64"]
EVENT_HEADERS = ["MATCH_ID","MINUTO","SEGUNDO","TIPO","ACTOR_ID","VICTIMA_ID","ASISTENTES","EQUIPO_ID","OBJETIVO","ITEM_ID","X","Y"]
TEAM_HEADERS = ["MATCH_ID","EQUIPO_ID","ES_ACCA","VICTORIA","BANEOS","DRAGONES","BARONES","HERALDOS","TORRES","INHIBIDORES","OBJETIVOS_JSON"]
DETAIL_HEADERS = ["MATCH_ID","PUUID","EQUIPO_ID","CAMPEON","ROL","NIVEL","KDA","KP_%","CS_MIN","ORO_MIN","DANO_MIN","DANO_FISICO","DANO_MAGICO","DANO_VERDADERO","DANO_RECIBIDO","DANO_MITIGADO","CURACION","ESCUDOS","WARD_COLOCADOS","WARD_DESTRUIDOS","CONTROL_WARDS","ORO_GASTADO","TIEMPO_JUGADO_S","RUNAS_JSON","HECHIZOS","CHALLENGES_JSON"]
BUILD_HEADERS = ["MATCH_ID","PUUID","MINUTO","SEGUNDO","TIPO","ITEM_ID","ANTES_ID","DESPUES_ID"]
EXTRA_TABS = {"RAW_RIOT_ARCHIVO":RAW_ARCHIVE,"RAW_EVENTOS":EVENT_HEADERS,"RAW_EQUIPOS":TEAM_HEADERS,"RAW_DETALLE":DETAIL_HEADERS,"RAW_BUILDS":BUILD_HEADERS,
              "STATS_JUGADORES":["CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
              "STATS_ROLES":["CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
              "STATS_OBJETOS":["CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
              "STATS_COMPOSICIONES":["CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"]}

def archive(match_id, kind, data, chunk_size=28000):
    # Google Sheets limits cell contents to 50,000 characters.
    blob = base64.b64encode(gzip.compress(json.dumps(data,ensure_ascii=False,separators=(",",":")).encode("utf-8"))).decode("ascii")
    chunks = [blob[i:i+chunk_size] for i in range(0,len(blob),chunk_size)]
    return [[match_id,kind,i+1,len(chunks),chunk] for i,chunk in enumerate(chunks)]

def unarchive(rows):
    data = "".join(str(row[4]) for row in sorted(rows,key=lambda r:int(r[2])))
    return json.loads(gzip.decompress(base64.b64decode(data)).decode("utf-8"))

def detailed_rows(data, members, team_id):
    info=data["info"]
    mid=data["metadata"]["matchId"]
    duration=info.get("gameDuration",0)
    # Current match-v5 gameDuration is seconds; legacy matches may encode milliseconds.
    if duration > 100000:
        duration /= 1000
    minutes=max(duration/60,1)
    ps=info.get("participants",[])
    team_kills=defaultdict(int)
    for p in ps:
        team_kills[p.get("teamId")]+=p.get("kills",0)
    details=[]
    for p in ps:
        kills=p.get("kills",0)
        assists=p.get("assists",0)
        deaths=p.get("deaths",0)
        dealt=p.get("totalDamageDealtToChampions",0)
        cs=p.get("totalMinionsKilled",0)+p.get("neutralMinionsKilled",0)
        own_kills=team_kills[p.get("teamId")] or 0
        challenges=p.get("challenges") or {}
        perks=p.get("perks") or {}
        details.append([mid,p.get("puuid",""),p.get("teamId",""),p.get("championName",""),
                        p.get("teamPosition",""),p.get("champLevel",0),
                        round((kills+assists)/max(deaths,1),2),
                        round(100*(kills+assists)/own_kills,2) if own_kills else 0,
                        round(cs/minutes,2),round(p.get("goldEarned",0)/minutes,2),
                        round(dealt/minutes,2),
                        p.get("physicalDamageDealtToChampions",0),p.get("magicDamageDealtToChampions",0),
                        p.get("trueDamageDealtToChampions",0),p.get("totalDamageTaken",0),
                        p.get("damageSelfMitigated",0),p.get("totalHeal",0),
                        p.get("totalDamageShieldedOnTeammates",0),p.get("wardsPlaced",0),
                        p.get("wardsKilled",0),p.get("visionWardsBoughtInGame",0),
                        p.get("goldSpent",0),duration,
                        json.dumps(perks,ensure_ascii=False,separators=(",",":")),
                        f'{p.get("summoner1Id",0)}|{p.get("summoner2Id",0)}',
                        json.dumps(challenges,ensure_ascii=False,separators=(",",":"))])
    teams=[]
    for t in info.get("teams",[]):
        obj=t.get("objectives") or {}
        def count(k):
            return (obj.get(k) or {}).get("kills",0)
        teams.append([mid,t.get("teamId",""),"SI" if t.get("teamId")==team_id else "NO",
                      "SI" if t.get("win") else "NO",
                      "|".join(str(b.get("championId",0)) for b in t.get("bans",[])),
                      count("dragon"),count("baron"),count("riftHerald"),count("tower"),count("inhibitor"),
                      json.dumps(obj,ensure_ascii=False,separators=(",",":"))])
    return details,teams

def timeline_rows(match_id, timeline):
    events=[]
    builds=[]
    for frame in timeline.get("info",{}).get("frames",[]):
        for e in frame.get("events",[]):
            timestamp=e.get("timestamp",0)
            sec=int(timestamp)//1000
            pos=e.get("position") or {}
            actor=e.get("participantId",e.get("killerId",0))
            typ=e.get("type","")
            events.append([match_id,sec//60,sec%60,typ,actor,e.get("victimId",0),
                           "|".join(map(str,e.get("assistingParticipantIds") or [])),
                           e.get("teamId",0),e.get("monsterType",e.get("buildingType","")),
                           e.get("itemId",0),pos.get("x",""),pos.get("y","")])
            if typ in ("ITEM_PURCHASED","ITEM_SOLD","ITEM_DESTROYED","ITEM_UNDO"):
                builds.append([match_id,"",sec//60,sec%60,typ,e.get("itemId",0),
                               e.get("beforeId",0),e.get("afterId",0),actor])
    return events,builds

def associate_builds(builds, match):
    by_participant={p.get("participantId"):p.get("puuid","") for p in match["info"]["participants"]}
    result=[]
    for row in builds:
        result.append(row[:1]+[by_participant.get(row[-1],"")]+row[2:-1])
    return result

def replace_by_match(old,new,match_id):
    mid=str(match_id).upper()
    return [r for r in old if r and str(r[0]).upper()!=mid]+new
