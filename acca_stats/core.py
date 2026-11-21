"""Pure validation and aggregation; no network access."""
from collections import defaultdict
from math import sqrt

HEADERS_MATCH = ["MATCH_ID", "FECHA_UTC", "MODO", "PARCHE", "LADO_ACCA", "RESULTADO", "INTEGRANTES", "N_ACCA", "FUENTE"]
HEADERS_PLAYER = ["MATCH_ID", "PUUID", "RIOT_ID", "ALIAS", "ES_ACCA", "EQUIPO_ACCA", "CAMPEON", "ROL", "KILLS", "DEATHS", "ASSISTS", "DANO", "ORO", "CS", "VISION", "ITEMS"]
HEADERS_QUEUE = ["MATCH_ID", "ESTADO", "MENSAJE", "LADO_ACCA", "PROCESADA_UTC"]
HEADERS_USERS = ["ALIAS", "RIOT_ID", "REGION", "ACTIVO", "PUUID", "ESTADO", "MENSAJE"]
HEADERS_STATS = ["CLAVE", "PARTIDAS", "VICTORIAS", "DERROTAS", "WINRATE_%", "WR_MIN_95_%"]
TABS = {"JUGADORES": HEADERS_USERS, "PARTIDAS_PENDIENTES": HEADERS_QUEUE,
        "RAW_PARTIDAS": HEADERS_MATCH, "RAW_JUGADORES": HEADERS_PLAYER,
        "DASHBOARD_STATS": HEADERS_STATS, "CAMPEONES": HEADERS_STATS,
        "STATS_EQUIPOS": HEADERS_STATS, "SISTEMA": ["CLAVE", "VALOR"]}

def wilson(wins, total):
    if not total:
        return 0.0
    z = 1.96
    p = wins / total
    return 100 * (p + z*z/(2*total) - z*sqrt((p*(1-p)+z*z/(4*total))/total)) / (1+z*z/total)

def summary(rows, key):
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        result = row.get("RESULTADO")
        if result not in ("VICTORIA", "DERROTA"):
            continue
        group = key(row)
        if not group:
            continue
        groups[group][0] += 1
        groups[group][1] += int(result == "VICTORIA")
    return [[name, n, w, n-w, round(100*w/n, 2), round(wilson(w,n),2)]
            for name,(n,w) in sorted(groups.items(), key=lambda x: (-wilson(x[1][1],x[1][0]),x[0]))]

def parse_match(data, members, forced_side=None):
    """Accept matches with 1–5 ACCA members on the selected side."""
    info = data["info"]
    participants = info["participants"]
    if len(participants) != 10:
        raise ValueError("La partida no contiene 10 participantes")
    teams = {}
    for p in participants:
        team = p["teamId"]
        teams.setdefault(team, []).append(p)
    candidates = []
    for team, ps in teams.items():
        identified = [p for p in ps if p.get("puuid") in members]
        if identified and len(ps) == 5:
            candidates.append((team, identified))
    if forced_side:
        desired = {"AZUL": 100, "ROJO": 200}.get(forced_side.upper())
        if desired is None:
            raise ValueError("LADO_ACCA debe ser AZUL o ROJO")
        candidates = [c for c in candidates if c[0] == desired]
    if not candidates:
        raise ValueError("No se identificaron integrantes ACCA en el lado indicado")
    # Without an explicit side, choose the team with the most ACCA members.
    # A tie is ambiguous: never attribute a match to an arbitrary side.
    most = max(len(identified) for _, identified in candidates)
    candidates = [c for c in candidates if len(c[1]) == most]
    if len(candidates) != 1:
        raise ValueError("Hay integrantes ACCA en ambos equipos; indicá LADO_ACCA (AZUL o ROJO)")
    team_id, own = candidates[0]
    win = bool(own[0]["win"])
    # gameEndedInEarlySurrender is Riot's explicit remake indicator.
    remake = any(p.get("gameEndedInEarlySurrender", False) for p in participants)
    match_id = data["metadata"]["matchId"]
    match = [match_id, info.get("gameCreation",""), info.get("gameMode",""),
             info.get("gameVersion",""), "AZUL" if team_id == 100 else "ROJO",
             "REMAKE" if remake else ("VICTORIA" if win else "DERROTA"),
             "|".join(sorted(members[p["puuid"]] for p in own)), len(own), "RIOT"]
    players = []
    for p in participants:
        puuid = p.get("puuid","")
        players.append([match_id, puuid, p.get("riotIdGameName","") + "#" + p.get("riotIdTagline",""),
                        members.get(puuid,""), "SI" if puuid in members else "NO",
                        "SI" if p["teamId"] == team_id else "NO", p.get("championName",""),
                        p.get("teamPosition",""), p.get("kills",0), p.get("deaths",0),
                        p.get("assists",0), p.get("totalDamageDealtToChampions",0),
                        p.get("goldEarned",0), p.get("totalMinionsKilled",0)+p.get("neutralMinionsKilled",0),
                        p.get("visionScore",0),
                        "|".join(str(p.get("item"+str(i),0)) for i in range(7))])
    return match, players

def upsert(rows, incoming, index=0):
    """Replace by stable ID and never duplicate existing IDs."""
    output = {str(row[index]).upper(): list(row) for row in rows if len(row)>index and row[index]}
    for row in incoming:
        output[str(row[index]).upper()] = list(row)
    return list(output.values())
