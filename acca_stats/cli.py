"""Google Sheets reporting from archived Supabase match data."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
import gspread
from dotenv import load_dotenv
from .core import TABS, HEADERS_MATCH, HEADERS_PLAYER, parse_match, upsert, summary
from .analytics import EXTRA_TABS, archive, detailed_rows, timeline_rows, associate_builds, replace_by_match, unarchive
from . import supabase_archive
from .presentation import GENERAL, PLAYERS, ensure_tab, write_view, style, hide_legacy, build_general, build_players, display_bans

ROUTES = {"LAS":"americas","LAN":"americas","NA":"americas","BR":"americas",
          "EUW":"europe","EUNE":"europe","TR":"europe","RU":"europe",
          "KR":"asia","JP":"asia","OCE":"sea","PH":"sea","SG":"sea","TH":"sea","TW":"sea","VN":"sea"}
def connect(key="GOOGLE_SHEET_ID"):
    sheet_id = os.environ[key]
    if "/spreadsheets/d/" in sheet_id:
        sheet_id = sheet_id.split("/spreadsheets/d/",1)[1].split("/",1)[0]
    raw = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if raw:
        gc = gspread.service_account_from_dict(json.loads(raw), http_client=gspread.BackOffHTTPClient)
    else:
        gc = gspread.service_account(filename=os.environ["GOOGLE_CREDENTIALS_FILE"], http_client=gspread.BackOffHTTPClient)
    try:
        return gc.open_by_key(sheet_id)
    except Exception as exc:
        raise RuntimeError(f"No se puede abrir {key}: {type(exc).__name__}: {exc!r}. Verificá el ID y compartí el archivo con la cuenta de servicio de Google.") from exc

def ensure(book, players_book=None):
    """Add missing columns without moving or deleting existing user data."""
    for name, header in {**{k:v for k,v in TABS.items() if not k.startswith("RAW_")}, **{k:v for k,v in EXTRA_TABS.items() if not k.startswith("RAW_")}}.items():
        try:
            ws = book.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = book.add_worksheet(title=name, rows=100, cols=max(20,len(header)))
            ws.freeze(rows=1)
            ws.format("1:1", {"backgroundColor":{"red":0.10,"green":0.16,"blue":0.27},
                                "textFormat":{"foregroundColor":{"red":1,"green":1,"blue":1},"bold":True},
                                "horizontalAlignment":"CENTER"})
        existing = ws.row_values(1)
        if not existing:
            ws.update(range_name="A1", values=[header])
            if name.startswith("RAW_"):
                ws.set_basic_filter()
            continue
        missing = [col for col in header if col not in existing]
        if missing:
            needed = len(existing) + len(missing)
            if ws.col_count < needed:
                ws.add_cols(needed - ws.col_count)
            from gspread.utils import rowcol_to_a1
            ws.update(range_name=rowcol_to_a1(1,len(existing)+1),
                      values=[missing])
            print(f"{name}: agregadas columnas {', '.join(missing)}; datos preservados")

def setup_players(book, players_book):
    """Copy player registry once; never discard a populated destination registry."""
    dest = ensure_tab(players_book,"JUGADORES",TABS["JUGADORES"])
    source = records(book,"JUGADORES")
    existing = dest.get_all_records()
    if source and not any(str(r.get("ALIAS","")).strip() for r in existing):
        write_rows(players_book,"JUGADORES",TABS["JUGADORES"],
                   [[r.get(k,"") for k in TABS["JUGADORES"]] for r in source])

def present(book, players_book, matches=None, participants=None, details=None):
    matches = matches if matches is not None else []
    participants = participants if participants is not None else []
    details = details if details is not None else []
    write_view(book,"RESUMEN",GENERAL["RESUMEN"],build_general(matches))
    write_view(book,"PARTIDAS",GENERAL["PARTIDAS"],[[display_bans(m.get(k,""),m.get("VERSION","")) if k in ("BANS_ACCA","BANS_RIVAL") else m.get(k,"") for k in GENERAL["PARTIDAS"]] for m in matches])
    # REGISTRO is the user-facing submission queue; do not overwrite it from a legacy tab.
    # Existing entries and statuses must survive every presentation refresh.
    ranking,history=build_players(matches,participants,details)
    # CAMPEONES is a generated, normalized table: never mix legacy captions
    # and secondary headers with the actual champion rows.
    champion_rows = summary([
        {"RESULTADO":m["RESULTADO"],"KEY":str(p.get("ALIAS",""))+" | "+str(p.get("CAMPEON",""))}
        for p in participants for m in matches
        if str(p.get("MATCH_ID")) == str(m.get("MATCH_ID"))
        and p.get("ES_ACCA") == "SI" and p.get("EQUIPO_ACCA") == "SI"
        and p.get("ALIAS") and p.get("CAMPEON")],lambda r:r["KEY"])
    champion_header = ["ALIAS","CAMPEON","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"]
    champion_clean = [[r[0].split(" | ",1)[0],r[0].split(" | ",1)[-1]]+r[1:] for r in champion_rows]
    write_view(book,"CAMPEONES",champion_header,champion_clean)
    champion_ws = book.worksheet("CAMPEONES")
    from gspread.utils import rowcol_to_a1
    if champion_ws.col_count > len(champion_header):
        first_extra = rowcol_to_a1(1,len(champion_header)+1).replace("1","")
        last_extra = rowcol_to_a1(champion_ws.row_count,champion_ws.col_count)
        champion_ws.batch_clear([f"{first_extra}1:{last_extra}"])
    write_view(players_book,"RENDIMIENTO",PLAYERS["RENDIMIENTO"],ranking)
    write_view(players_book,"PARTICIPACIONES",PLAYERS["PARTICIPACIONES"],history)
    # Render player analytics from Supabase-derived records; no historical RAW source.
    # J_* reports in the general workbook. Append current match history below.
    # Never replace the multi-section source with a reduced match-only table.
    player_header = ["MATCH_ID","FECHA_UTC","MODO","RESULTADO","CAMPEON","ROL",
                     "KILLS","DEATHS","ASSISTS","KDA","CS_MIN","ORO","DANO","VISION","ITEMS"]
    by_match = {str(m.get("MATCH_ID")): m for m in matches}
    guide = [
        ["CAMPO","SIGNIFICADO"],
        ["MATCH_ID","Identificador único de partida de Riot; LA2_ seguido del número"],
        ["REGISTRO","Ingresar MATCH_ID; PENDIENTE = por procesar, PROCESADA = importada, ERROR = requiere revisión"],
        ["PARTIDAS_PENDIENTES","Historial técnico de la cola; incluye partidas ya procesadas"],
        ["N_ACCA","Cantidad de miembros ACCA del lado seleccionado (entre 1 y 5)"],
        ["EQUIPO_ACCA","Miembros ACCA que participaron juntos; no es la composición de cinco campeones"],
        ["COMPOSICION_ACCA","Los cinco campeones del lado elegido, sean o no jugadores ACCA"],
        ["WINRATE_%","Porcentaje de victorias: VICTORIAS dividido por PARTIDAS"],
        ["WR_MIN_95_%","Límite inferior estadístico de Wilson al 95%; ranking conservador, no otro porcentaje de victorias"],
        ["CONFIANZA BAJA","Pocas partidas observadas: el resultado todavía no es fiable"],
        ["CONFIANZA MEDIA / ALTA","Más partidas observadas; los umbrales dependen de la configuración del análisis"],
        ["SUPABASE","Los JSON completos de Riot se guardan exclusivamente en Supabase"],
        ["JUGADORES","Una fila por Riot ID, repetí ALIAS para agregar cuentas adicionales del mismo jugador"],
    ]
    for destination in (book, players_book):
        write_view(destination,"GUIA",guide[0],guide[1:])
    player_names = set(PLAYERS) | {"GUIA"} | {ws.title for ws in players_book.worksheets()
                                   if ws.title.startswith("J_")}
    style(players_book,{name for name in player_names if not name.startswith("J_")})
    # Published summaries are views, not a source of truth.
    # Expose complete, readable copies of the major analytics tables.
    friendly = {
        "STATS_EQUIPOS": "EQUIPOS",
        "STATS_OBJETOS": "OBJETOS",
        "STATS_COMPOSICIONES": "COMPOSICIONES",
    }
    for source, destination in friendly.items():
        source_ws = book.worksheet(source)
        values = source_ws.get_all_values()
        if values:
            write_view(book, destination, values[0], values[1:])
    visible = set(GENERAL) | {"CAMPEONES", "GUIA"} | set(friendly.values())
    style(book, visible)
    hide_legacy(book, visible)
    for ws in book.worksheets():
        if ws.title in visible and ws._properties.get("hidden", False):
            book.batch_update({"requests": [{"updateSheetProperties": {"properties": {"sheetId": ws.id, "hidden": False}, "fields": "hidden"}}]})
    # Display the most useful views first, keeping all technical tables intact.
    ordered = ["REGISTRO", "GUIA", "RESUMEN", "PARTIDAS",
               "CAMPEONES", "EQUIPOS", "COMPOSICIONES", "OBJETOS"]
    book.batch_update({"requests": [
        {"updateSheetProperties": {
            "properties": {"sheetId": book.worksheet(name).id, "index": index},
            "fields": "index"
        }} for index, name in enumerate(ordered)
    ]})
    # Put navigation and instructions next to their corresponding editable tables.
    player_order = ["JUGADORES", "GUIA", "RENDIMIENTO", "PARTICIPACIONES"]
    players_book.batch_update({"requests":[{"updateSheetProperties":{"properties":
        {"sheetId":players_book.worksheet(name).id,"index":i},"fields":"index"}}
        for i,name in enumerate(player_order)]})
    for ws in players_book.worksheets():
        if ws.title == "IDENTIDADES" and not ws._properties.get("hidden",False):
            players_book.batch_update({"requests":[{"updateSheetProperties":{
                "properties":{"sheetId":ws.id,"hidden":True},"fields":"hidden"}}]})
    # All individual player reports must remain visible after every sync.
    for ws in players_book.worksheets():
        if ws.title.startswith("J_") and ws._properties.get("hidden", False):
            players_book.batch_update({"requests": [{"updateSheetProperties": {"properties": {"sheetId": ws.id, "hidden": False}, "fields": "hidden"}}]})

def records(book, tab):
    return book.worksheet(tab).get_all_records()

def write_rows(book, tab, header, rows):
    ws = book.worksheet(tab)
    existing = ws.row_values(1)
    # Keep original column order and any user-defined extra columns.
    columns = existing or header
    indexed = {name:i for i,name in enumerate(header)}
    old = ws.get_all_values()[1:]
    output = []
    for n,row in enumerate(rows):
        values = list(old[n]) if n < len(old) else []
        values += [""] * (len(columns)-len(values))
        for j,name in enumerate(columns):
            if name in indexed:
                values[j] = row[indexed[name]] if indexed[name] < len(row) else ""
        output.append(values)
    # No clear(): avoid erasing existing input data before successful writes.
    ws.update(range_name="A1", values=[columns]+output, value_input_option="RAW")
    if len(old) > len(output):
        from gspread.utils import rowcol_to_a1
        ws.batch_clear([f"A{len(output)+2}:{rowcol_to_a1(len(old)+1,len(columns))}"])

def resolve_players(book):
    """Each row in JUGADORES is a Riot account; repeated aliases are supported."""
    from urllib.parse import quote
    rows = records(book, "JUGADORES")
    mapping = {}
    for row in rows:
        alias = str(row.get("ALIAS", "")).strip()
        riot_id = str(row.get("RIOT_ID", "")).strip()
        region = str(row.get("REGION", "")).upper().strip()
        if not alias and not riot_id:
            continue
        if not alias or "#" not in riot_id or region not in ROUTES:
            row["ESTADO"], row["MENSAJE"] = "ERROR", "Completá ALIAS, RIOT_ID y REGION"
            continue
        if str(row.get("ACTIVO", "SI")).upper() == "NO":
            continue
        # PUUID is a stable identifier already registered in the spreadsheet.
        # Reporting never calls Riot; resolve missing PUUIDs outside this pipeline.
        if not str(row.get("PUUID", "")).strip():
            row["ESTADO"], row["MENSAJE"] = "ERROR", "Falta PUUID; completalo para asociar partidas"
            continue
        row["ESTADO"], row["MENSAJE"] = "OK", ""
        puuid = str(row.get("PUUID", "")).strip()
        if puuid and row.get("ESTADO") == "OK":
            if puuid in mapping and mapping[puuid] != alias:
                row["ESTADO"], row["MENSAJE"] = "ERROR", "Cuenta vinculada a otro alias"
            else:
                mapping[puuid] = alias
    write_rows(book, "JUGADORES", TABS["JUGADORES"],
               [[r.get(k, "") for k in TABS["JUGADORES"]] for r in rows])
    return mapping


def rebuild(book, matches=None, participants=None):
    matches = matches if matches is not None else []
    participants = participants if participants is not None else []
    write_rows(book,"DASHBOARD_STATS",TABS["DASHBOARD_STATS"],summary(matches,lambda r:"ACCA"))
    write_rows(book,"STATS_EQUIPOS",TABS["STATS_EQUIPOS"],summary([m for m in matches if m.get("EQUIPO_ACCA") or m.get("INTEGRANTES")],lambda r:str(r.get("EQUIPO_ACCA") or r.get("INTEGRANTES"))))
    by_id = {str(r["MATCH_ID"]):r for r in matches}
    entries = []
    for p in participants:
        if p.get("ES_ACCA") != "SI" or p.get("EQUIPO_ACCA") != "SI":
            continue
        m = by_id.get(str(p["MATCH_ID"]))
        if m:
            entries.append({"RESULTADO":m["RESULTADO"],"KEY":str(p.get("ALIAS",""))+" | "+str(p.get("CAMPEON",""))})
    write_rows(book,"CAMPEONES",TABS["CAMPEONES"],summary(entries,lambda r:r["KEY"]))
    player_entries,role_entries=[],[]
    for p in participants:
        if p.get("ES_ACCA") != "SI" or p.get("EQUIPO_ACCA") != "SI":
            continue
        m = by_id.get(str(p.get("MATCH_ID","")))
        if not m:
            continue
        player_entries.append({"RESULTADO":m["RESULTADO"],"KEY":str(p.get("ALIAS",""))})
        role_entries.append({"RESULTADO":m["RESULTADO"],"KEY":str(p.get("ALIAS",""))+" | "+str(p.get("ROL",""))})
    write_rows(book,"STATS_JUGADORES",EXTRA_TABS["STATS_JUGADORES"],summary(player_entries,lambda r:r["KEY"]))
    write_rows(book,"STATS_ROLES",EXTRA_TABS["STATS_ROLES"],summary(role_entries,lambda r:r["KEY"]))
    item_entries, comp_entries = [], []
    champions_by_match = {}
    for p in participants:
        if p.get("ES_ACCA") != "SI" or p.get("EQUIPO_ACCA") != "SI":
            continue
        mid = str(p.get("MATCH_ID",""))
        m = by_id.get(mid)
        if not m:
            continue
        alias = str(p.get("ALIAS",""))
        champion = str(p.get("CAMPEON",""))
        champions_by_match.setdefault(mid, []).append(champion)
        items = {item for item in str(p.get("ITEMS","")).split("|") if item.isdigit() and int(item)>0}
        for item in items:
            item_entries.append({"RESULTADO":m["RESULTADO"],"KEY":alias+" | "+champion+" | "+item})
    # Use the complete five-champion draft from match data when available.
    # Player-derived champions are only a fallback for complete ACCA rosters.
    for mid, match in by_id.items():
        full = str(match.get("COMPOSICION_ACCA", "")).strip()
        if full:
            key = " | ".join(sorted(part.strip() for part in full.split("+") if part.strip()))
            if len([part for part in full.split("+") if part.strip()]) == 5:
                comp_entries.append({"RESULTADO":match["RESULTADO"], "KEY":key})
        elif len(champions_by_match.get(mid, [])) == 5:
            comp_entries.append({"RESULTADO":match["RESULTADO"],
                                 "KEY":" | ".join(sorted(champions_by_match[mid]))})
    write_rows(book,"STATS_OBJETOS",EXTRA_TABS["STATS_OBJETOS"],summary(item_entries,lambda r:r["KEY"]))
    write_rows(book,"STATS_COMPOSICIONES",EXTRA_TABS["STATS_COMPOSICIONES"],summary(comp_entries,lambda r:r["KEY"]))

def normalize_match_id(value):
    """Accept the numeric match ID copied from the LAS client."""
    mid = str(value).strip().upper()
    # Google Sheets may display a pasted numeric ID with thousands separators.
    if mid.count(".") and all(part.isdigit() for part in mid.split(".")) and all(len(part) == 3 for part in mid.split(".")[1:]):
        mid = mid.replace(".", "")
    if mid.isdigit():
        return "LA2_" + mid
    return mid


def merge_submission_rows(*sources):
    """Merge submissions without dropping newly entered rows or prior statuses."""
    merged = {}
    order = []
    for source in sources:
        for raw in source:
            row = dict(raw)
            mid = normalize_match_id(row.get("MATCH_ID", ""))
            if not mid:
                continue
            row["MATCH_ID"] = mid
            if mid not in merged:
                merged[mid] = row
                order.append(mid)
            else:
                prior = merged[mid]
                for key, value in row.items():
                    if value not in ("", None) and (not prior.get(key) or
                            (key in ("ESTADO", "MENSAJE", "PROCESADA_UTC") and
                             str(value).upper() not in ("PENDIENTE",))):
                        prior[key] = value
    return [merged[mid] for mid in order]


def save_submission_queue(book, queue):
    """Re-read both sheets immediately before saving to preserve manual additions."""
    current = merge_submission_rows(records(book, "PARTIDAS_PENDIENTES"),
                                    records(book, "REGISTRO"))
    merged = merge_submission_rows(current, queue)
    updated = {row["MATCH_ID"]: row for row in queue}
    for row in merged:
        latest = updated.get(row["MATCH_ID"])
        if latest and str(latest.get("ESTADO", "")).upper() not in ("", "PENDIENTE"):
            for key in ("ESTADO", "MENSAJE", "PROCESADA_UTC"):
                row[key] = latest.get(key, "")
    for tab, header in (("PARTIDAS_PENDIENTES", TABS["PARTIDAS_PENDIENTES"]),
                        ("REGISTRO", GENERAL["REGISTRO"])):
        write_rows(book, tab, header,
                   [[row.get(key, "") for key in header] for row in merged])


def expanded_match(data, match, members):
    """Populate the existing full RAW_PARTIDAS schema from Riot's payload."""
    info = data["info"]
    ps = info["participants"]
    side = 100 if match[4] == "AZUL" else 200
    own = [p for p in ps if p["teamId"] == side]
    rivals = [p for p in ps if p["teamId"] != side]
    teams = {t["teamId"]: t for t in info.get("teams", [])}
    def objective(team, name):
        return (teams.get(team, {}).get("objectives", {}).get(name) or {}).get("kills", 0)
    def bans(team):
        return "|".join(str(b.get("championId", 0)) for b in teams.get(team, {}).get("bans", []))
    duration = info.get("gameDuration", 0)
    if duration > 100000:
        duration /= 1000
    from datetime import datetime, timezone
    created = info.get("gameCreation", 0)
    date = datetime.fromtimestamp(created / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if created else ""
    version = str(info.get("gameVersion", ""))
    values = {
        "MATCH_ID": match[0], "FUENTE": "RIOT", "FECHA_UTC": date,
        "DURACION_SEG": int(duration), "MODO": info.get("gameMode", ""),
        "TIPO": info.get("gameType", ""), "COLA_ID": info.get("queueId", ""),
        "VERSION": ".".join(version.split(".")[:2]), "PARCHE": ".".join(version.split(".")[:2]),
        "PLATAFORMA": str(match[0]).split("_")[0], "LADO_ACCA": match[4],
        "RESULTADO": match[5], "EQUIPO_ACCA": " + ".join(sorted(members[p["puuid"]] for p in own if p.get("puuid") in members)),
        "INTEGRANTES": " + ".join(sorted(members[p["puuid"]] for p in own if p.get("puuid") in members)),
        "N_ACCA": match[7],
        "COMPOSICION_ACCA": " + ".join(sorted(p.get("championName", "") for p in own)),
        "COMPOSICION_RIVAL": " + ".join(sorted(p.get("championName", "") for p in rivals)),
        "BANS_ACCA": bans(side), "BANS_RIVAL": bans(300-side),
        "KILLS_ACCA": sum(p.get("kills", 0) for p in own),
        "KILLS_RIVAL": sum(p.get("kills", 0) for p in rivals),
        "ORO_ACCA": sum(p.get("goldEarned", 0) for p in own),
        "ORO_RIVAL": sum(p.get("goldEarned", 0) for p in rivals),
        "TIMELINE": "SI", "ACTUALIZADA_UTC": datetime.now(timezone.utc).isoformat(),
    }
    for key, objective_name in (("TORRES", "tower"), ("DRAGONES", "dragon"),
                                 ("BARONES", "baron"), ("HERALDOS", "riftHerald"),
                                 ("INHIBIDORES", "inhibitor")):
        values[key + "_ACCA"] = objective(side, objective_name)
        values[key + "_RIVAL"] = objective(300-side, objective_name)
    return values


def expanded_players(data, members, match):
    """Populate all supported RAW_JUGADORES columns by name."""
    info = data["info"]
    side = 100 if match[4] == "AZUL" else 200
    duration = info.get("gameDuration", 0)
    if duration > 100000:
        duration /= 1000
    minutes = max(duration / 60, 1)
    team_kills = {team: sum(p.get("kills", 0) for p in info["participants"] if p["teamId"] == team)
                  for team in (100, 200)}
    output = []
    for p in info["participants"]:
        puuid = p.get("puuid", "")
        kills, deaths, assists = (p.get(k, 0) for k in ("kills", "deaths", "assists"))
        cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
        items = "|".join(str(p.get(f"item{i}", 0)) for i in range(7))
        values = {
            "MATCH_ID": match[0], "PARTICIPANTE_ID": p.get("participantId", ""),
            "ES_ACCA": "SI" if puuid in members else "NO",
            "ALIAS": members.get(puuid, ""), "RIOT_ID": p.get("riotIdGameName", "") + "#" + p.get("riotIdTagline", ""),
            "PUUID": puuid, "LADO": "AZUL" if p["teamId"] == 100 else "ROJO",
            "EQUIPO_ACCA": "SI" if p["teamId"] == side else "NO",
            "RESULTADO": match[5] if p["teamId"] == side else ("DERROTA" if match[5] == "VICTORIA" else "VICTORIA"),
            "CAMPEON": p.get("championName", ""), "ROL": p.get("teamPosition", ""),
            "KILLS": kills, "DEATHS": deaths, "ASSISTS": assists,
            "KDA": round((kills + assists) / max(deaths, 1), 4),
            "DANO_CAMPEONES": p.get("totalDamageDealtToChampions", 0),
            "DANO": p.get("totalDamageDealtToChampions", 0),
            "DANO_RECIBIDO": p.get("totalDamageTaken", 0),
            "ORO": p.get("goldEarned", 0), "CS": cs, "CS_MIN": round(cs / minutes, 3),
            "VISION": p.get("visionScore", 0), "CENTINELAS_COLOCADOS": p.get("wardsPlaced", 0),
            "CENTINELAS_DESTRUIDOS": p.get("wardsKilled", 0),
            "CONTROL_WARDS": p.get("visionWardsBoughtInGame", 0),
            "NIVEL": p.get("champLevel", 0), "DANO_OBJETIVOS": p.get("damageDealtToObjectives", 0),
            "DANO_TORRES": p.get("damageDealtToTurrets", 0),
            "PARTICIPACION_KILLS": round(100 * (kills + assists) / max(team_kills[p["teamId"]], 1), 2),
            "HECHIZO_1": p.get("summoner1Id", 0), "HECHIZO_2": p.get("summoner2Id", 0),
            "ITEMS_FINALES": items, "ITEMS": items, "TRINKET": p.get("item6", 0),
        }
        output.append(values)
    return output


def repair_from_archive(book, members):
    """Repair incomplete imported matches from saved Riot payloads, without network calls."""
    from .analytics import unarchive
    from collections import defaultdict
    chunks = defaultdict(list)
    for row in records(book, "RAW_RIOT_ARCHIVO"):
        if row.get("TIPO") == "MATCH" and row.get("GZIP_BASE64"):
            chunks[str(row["MATCH_ID"])].append([row.get(k) for k in EXTRA_TABS["RAW_RIOT_ARCHIVO"]])
    match_columns = book.worksheet("RAW_PARTIDAS").row_values(1)
    player_columns = book.worksheet("RAW_JUGADORES").row_values(1)
    matches = [[r.get(k, "") for k in match_columns] for r in records(book, "RAW_PARTIDAS")]
    players = [[r.get(k, "") for k in player_columns] for r in records(book, "RAW_JUGADORES")]
    changed = False
    for mid, rows in chunks.items():
        current = next((dict(zip(match_columns, row)) for row in matches if row and row[0] == mid), None)
        if not current or (current.get("COMPOSICION_ACCA") and current.get("DURACION_SEG")):
            continue
        data = unarchive(rows)
        match, _ = parse_match(data, members, current.get("LADO_ACCA") or None)
        match_values = expanded_match(data, match, members)
        matches = upsert(matches, [[match_values.get(k, "") for k in match_columns]])
        players = upsert_participants(players, [[p.get(k, "") for k in player_columns]
                                                for p in expanded_players(data, members, match)])
        changed = True
    if changed:
        write_rows(book, "RAW_PARTIDAS", match_columns, matches)
        write_rows(book, "RAW_JUGADORES", player_columns, players)
    return changed


def sync(book, players_book, ids=(), force=False, refresh_ids=False):
    """Build Google Sheets reports exclusively from Supabase archives."""
    if not supabase_archive.configured():
        raise RuntimeError("Faltan credenciales de Supabase")
    ensure(book)
    setup_players(book,players_book)
    members = resolve_players(players_book)
    queue = merge_submission_rows(records(book,"REGISTRO"))
    existing = {r["MATCH_ID"] for r in queue}
    for supplied in ids:
        mid = normalize_match_id(supplied)
        if mid and mid not in existing:
            queue.append({"MATCH_ID":mid,"ESTADO":"PENDIENTE","MENSAJE":"",
                          "LADO_ACCA":"","PROCESADA_UTC":""})
            existing.add(mid)
        elif mid and refresh_ids:
            for row in queue:
                if row["MATCH_ID"] == mid:
                    row["ESTADO"] = "PENDIENTE"
    # Discord/Cloudflare is the only ingestion path. Sheets never downloads
    # from Riot and never writes original match/timeline archives.
    archived_ids = supabase_archive.list_match_ids()
    archived = set(archived_ids)
    for row in queue:
        if row["MATCH_ID"] in archived:
            row["ESTADO"], row["MENSAJE"] = "PROCESADA", ""
            row["PROCESADA_UTC"] = datetime.now(timezone.utc).isoformat()
        else:
            row["ESTADO"], row["MENSAJE"] = "PENDIENTE", "Esperando archivo en Supabase (usá /partida en Discord)"
    write_rows(book,"REGISTRO",GENERAL["REGISTRO"],
               [[r.get(k,"") for k in GENERAL["REGISTRO"]] for r in queue])
    matches,participants,details = [],[],[]
    for mid in archived_ids:
        data = supabase_archive.load(mid,"match")
        timeline = supabase_archive.load(mid,"timeline")
        if data is None or timeline is None:
            raise RuntimeError("Archivo incompleto en Supabase: "+mid)
        source = next((r for r in queue if r["MATCH_ID"] == mid),{})
        match,_ = parse_match(data,members,str(source.get("LADO_ACCA","")).strip() or None)
        matches.append(expanded_match(data,match,members))
        participants.extend(expanded_players(data,members,match))
        side = 100 if match[4] == "AZUL" else 200
        detail,_ = detailed_rows(data,members,side)
        details.extend(dict(zip(EXTRA_TABS["RAW_DETALLE"],r)) for r in detail)
    rebuild(book,matches,participants)
    present(book,players_book,matches,participants,details)


def upsert_participants(old, new):
    keyed = {(str(r[0]).upper(),str(r[1])):list(r) for r in old if len(r)>1}
    for r in new:
        keyed[(str(r[0]).upper(),str(r[1]))]=list(r)
    return list(keyed.values())

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="ACCA LoL Stats")
    sub = parser.add_subparsers(dest="command",required=True)
    for name in ("check","init","players","sync","rebuild"):
        sub.add_parser(name)
    add = sub.add_parser("add-match")
    add.add_argument("ids",nargs="+")
    refresh = sub.add_parser("refresh-match")
    refresh.add_argument("ids",nargs="+")
    args = parser.parse_args()
    required = ["GOOGLE_SHEET_ID","GOOGLE_SHEET_ID_PLAYERS","SUPABASE_URL","SUPABASE_SECRET_KEY"]
    required += [] if os.getenv("GOOGLE_CREDENTIALS_JSON") else ["GOOGLE_CREDENTIALS_FILE"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        parser.error("Faltan variables: "+", ".join(missing))
    try:
        book = connect()
        players_book = connect("GOOGLE_SHEET_ID_PLAYERS")
        if book.id == players_book.id:
            raise ValueError("GOOGLE_SHEET_ID_PLAYERS debe ser una hoja diferente de GOOGLE_SHEET_ID")
        if args.command == "check":
            print("Google Sheets: OK")
            print("Supabase: archivo de partidas es la única fuente de estadísticas")
        elif args.command == "init":
            ensure(book)
            setup_players(book,players_book)
            sync(book,players_book,force=True)
        elif args.command == "players":
            ensure(book)
            setup_players(book,players_book)
            resolve_players(players_book)
            sync(book,players_book,force=True)
        elif args.command == "rebuild":
            ensure(book)
            setup_players(book,players_book)
            members = resolve_players(players_book)
            sync(book,players_book,force=True)
        else:
            sync(book,players_book,getattr(args,"ids",()),args.command in ("sync","refresh-match"),args.command=="refresh-match")
        print("Operación terminada")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc!r}",file=sys.stderr)
        sys.exit(1)
