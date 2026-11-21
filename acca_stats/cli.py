"""Command line and Google Sheets / Riot integrations."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
import gspread
import requests
from dotenv import load_dotenv
from .core import TABS, HEADERS_MATCH, HEADERS_PLAYER, parse_match, upsert, summary
from .analytics import EXTRA_TABS, archive, detailed_rows, timeline_rows, associate_builds, replace_by_match
from .presentation import GENERAL, PLAYERS, ensure_tab, write_view, style, hide_legacy, build_general, build_players, display_bans

ROUTES = {"LAS":"americas","LAN":"americas","NA":"americas","BR":"americas",
          "EUW":"europe","EUNE":"europe","TR":"europe","RU":"europe",
          "KR":"asia","JP":"asia","OCE":"sea","PH":"sea","SG":"sea","TH":"sea","TW":"sea","VN":"sea"}
PLATFORMS = {"LA2":"americas","LA1":"americas","NA1":"americas","BR1":"americas",
             "EUW1":"europe","EUN1":"europe","TR1":"europe","RU":"europe",
             "KR":"asia","JP1":"asia","OC1":"sea","PH2":"sea","SG2":"sea","TH2":"sea","TW2":"sea","VN2":"sea"}

def riot_get(url):
    key = os.environ["RIOT_API_KEY"]
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"X-Riot-Token":key}, timeout=25)
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (401,403):
            raise RuntimeError("Riot API rechazó la clave (401/403)")
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == 3:
                raise RuntimeError(f"Riot temporalmente no disponible: {r.status_code}")
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(120, float(wait)) if wait.replace(".","",1).isdigit() else 2**attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Riot no respondió")

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
    for name, header in {**TABS, **EXTRA_TABS}.items():
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

def present(book, players_book):
    matches=records(book,"RAW_PARTIDAS")
    participants=records(book,"RAW_JUGADORES")
    details=records(book,"RAW_DETALLE")
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
    if not any(w.title == "CAMPEONES_LEGACY" for w in book.worksheets()):
        original = book.worksheet("CAMPEONES")
        if original.get_all_values():
            book.duplicate_sheet(original.id, new_sheet_name="CAMPEONES_LEGACY")
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
    # Rebuild individual player reports from canonical data, not stale J_* legacy layouts.
    player_header = ["MATCH_ID","FECHA_UTC","MODO","RESULTADO","CAMPEON","ROL",
                     "KILLS","DEATHS","ASSISTS","KDA","CS_MIN","ORO","DANO","VISION","ITEMS"]
    by_match = {str(m.get("MATCH_ID")):m for m in matches}
    player_aliases = {str(p.get("ALIAS","")) for p in participants
                      if p.get("ES_ACCA") == "SI" and p.get("ALIAS")}
    player_aliases |= {str(r.get("ALIAS","")) for r in records(players_book,"JUGADORES")
                       if r.get("ALIAS")}
    for alias in sorted(player_aliases):
        if not alias:
            continue
        name = "J_" + alias[:90]
        player_rows = []
        for p in participants:
            if p.get("ALIAS") != alias or p.get("ES_ACCA") != "SI" or p.get("EQUIPO_ACCA") != "SI":
                continue
            match = by_match.get(str(p.get("MATCH_ID")))
            if not match:
                continue
            player_rows.append([p.get("MATCH_ID",""),match.get("FECHA_UTC",""),
                match.get("MODO",""),match.get("RESULTADO",""),p.get("CAMPEON",""),
                p.get("ROL",""),p.get("KILLS",0),p.get("DEATHS",0),p.get("ASSISTS",0),
                p.get("KDA",""),p.get("CS_MIN",""),p.get("ORO",0),
                p.get("DANO_CAMPEONES",p.get("DANO",0)),
                p.get("VISION",0),p.get("ITEMS","")])
        ws = ensure_tab(players_book,name,player_header)
        # Legacy reports had merged sections and dark bars; clear those formats.
        if ws._properties.get("merges"):
            players_book.batch_update({"requests":[{"unmergeCells":{"range":{
                "sheetId":ws.id,"startRowIndex":0,"endRowIndex":ws.row_count,
                "startColumnIndex":0,"endColumnIndex":ws.col_count}}}]})
        players_book.batch_update({"requests":[{"repeatCell":{"range":{"sheetId":ws.id},
            "cell":{"userEnteredFormat":{}},"fields":"userEnteredFormat"}}]})
        write_view(players_book,name,player_header,player_rows)
        # Remove all leftover legacy values beyond the new report's columns.
        if ws.col_count > len(player_header):
            from gspread.utils import rowcol_to_a1
            col = rowcol_to_a1(1,len(player_header)+1).rstrip("1")
            ws.batch_clear([f"{col}1:{rowcol_to_a1(ws.row_count,ws.col_count)}"])
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
        ["RAW_*","Tablas originales para exportación futura; no modificar manualmente"],
        ["JUGADORES","Una fila por Riot ID, repetí ALIAS para agregar cuentas adicionales del mismo jugador"],
    ]
    for destination in (book, players_book):
        write_view(destination,"GUIA",guide[0],guide[1:])
    player_names = set(PLAYERS) | {"GUIA"} | {ws.title for ws in players_book.worksheets()
                                   if ws.title.startswith("J_")}
    style(players_book,player_names)
    # Keep internal identifiers unchanged for data ingestion and rebuilding.
    # Expose complete, readable copies of the major analytics tables.
    friendly = {
        "STATS_EQUIPOS": "EQUIPOS",
        "STATS_OBJETOS": "OBJETOS",
        "STATS_COMPOSICIONES": "COMPOSICIONES",
        "RAW_PARTIDAS": "DETALLE DE PARTIDAS",
        "RAW_EQUIPOS": "OBJETIVOS",
        "RAW_EVENTOS": "EVENTOS",
    }
    for source, destination in friendly.items():
        source_ws = book.worksheet(source)
        values = source_ws.get_all_values()
        if values:
            write_view(book, destination, values[0], values[1:])
    visible = set(GENERAL) | {"CAMPEONES", "PARTIDAS_PENDIENTES", "GUIA"} | set(friendly.values())
    style(book, visible)
    hide_legacy(book, visible)
    for ws in book.worksheets():
        if ws.title in visible and ws._properties.get("hidden", False):
            book.batch_update({"requests": [{"updateSheetProperties": {"properties": {"sheetId": ws.id, "hidden": False}, "fields": "hidden"}}]})
    # Display the most useful views first, keeping all technical tables intact.
    ordered = ["REGISTRO", "GUIA", "PARTIDAS_PENDIENTES", "RESUMEN", "PARTIDAS", "DETALLE DE PARTIDAS",
               "CAMPEONES", "EQUIPOS", "COMPOSICIONES",
               "OBJETOS", "OBJETIVOS", "EVENTOS", "REGISTRO"]
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
        if not row.get("PUUID") or row.get("ESTADO") != "OK":
            name, tag = riot_id.rsplit("#", 1)
            try:
                data = riot_get(f"https://{ROUTES[region]}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(name,safe='')}/{quote(tag,safe='')}")
                row["PUUID"], row["ESTADO"], row["MENSAJE"] = data["puuid"], "OK", ""
            except Exception as exc:
                row["ESTADO"], row["MENSAJE"] = "ERROR", str(exc)[:150]
        puuid = str(row.get("PUUID", "")).strip()
        if puuid and row.get("ESTADO") == "OK":
            if puuid in mapping and mapping[puuid] != alias:
                row["ESTADO"], row["MENSAJE"] = "ERROR", "Cuenta vinculada a otro alias"
            else:
                mapping[puuid] = alias
    write_rows(book, "JUGADORES", TABS["JUGADORES"],
               [[r.get(k, "") for k in TABS["JUGADORES"]] for r in rows])
    return mapping


def rebuild(book):
    matches = records(book,"RAW_PARTIDAS")
    participants = records(book,"RAW_JUGADORES")
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
    ensure(book)
    setup_players(book,players_book)
    members = resolve_players(players_book)
    queue = merge_submission_rows(records(book,"PARTIDAS_PENDIENTES"), records(book,"REGISTRO"))
    # A pending retry in either sheet takes precedence over a stale error.
    pending = {normalize_match_id(r.get("MATCH_ID", "")) for tab in ("PARTIDAS_PENDIENTES", "REGISTRO")
               for r in records(book, tab) if str(r.get("ESTADO", "")).upper() == "PENDIENTE"}
    for row in queue:
        if row["MATCH_ID"] in pending:
            row["ESTADO"], row["MENSAJE"] = "PENDIENTE", ""
    existing = {str(r.get("MATCH_ID","")).upper() for r in queue}
    for mid in ids:
        mid = normalize_match_id(mid)
        if mid and mid not in existing:
            queue.append({"MATCH_ID":mid,"ESTADO":"PENDIENTE","MENSAJE":"","LADO_ACCA":"","PROCESADA_UTC":""})
            existing.add(mid)
        elif mid and refresh_ids:
            for row in queue:
                if str(row.get("MATCH_ID","")).upper() == mid:
                    row["ESTADO"]="PENDIENTE"
                    row["MENSAJE"]=""
    match_columns = book.worksheet("RAW_PARTIDAS").row_values(1)
    player_columns = book.worksheet("RAW_JUGADORES").row_values(1)
    old_matches = [[r.get(k,"") for k in match_columns] for r in records(book,"RAW_PARTIDAS")]
    old_players = [[r.get(k,"") for k in player_columns] for r in records(book,"RAW_JUGADORES")]
    incoming_matches, incoming_players = [],[]
    advanced = {name: [] for name in ("RAW_RIOT_ARCHIVO","RAW_EVENTOS","RAW_EQUIPOS","RAW_DETALLE","RAW_BUILDS")}
    changed = False
    for row in queue:
        if str(row.get("ESTADO","")).upper() not in ("","PENDIENTE"):
            continue
        mid = normalize_match_id(row.get("MATCH_ID", ""))
        row["MATCH_ID"] = mid
        if not mid:
            continue
        route = PLATFORMS.get(mid.split("_",1)[0])
        if not route:
            row["ESTADO"],row["MENSAJE"]="ERROR","Plataforma de partida no reconocida"
            continue
        try:
            data = riot_get(f"https://{route}.api.riotgames.com/lol/match/v5/matches/{mid}")
            match,players = parse_match(data,members,str(row.get("LADO_ACCA","")).strip() or None)
            timeline = riot_get(f"https://{route}.api.riotgames.com/lol/match/v5/matches/{mid}/timeline")
            side = 100 if match[4] == "AZUL" else 200
            detail, teams = detailed_rows(data,members,side)
            events, builds = timeline_rows(mid,timeline)
            advanced["RAW_RIOT_ARCHIVO"].extend(archive(mid,"MATCH",data))
            advanced["RAW_RIOT_ARCHIVO"].extend(archive(mid,"TIMELINE",timeline))
            advanced["RAW_EVENTOS"].extend(events)
            advanced["RAW_EQUIPOS"].extend(teams)
            advanced["RAW_DETALLE"].extend(detail)
            advanced["RAW_BUILDS"].extend(associate_builds(builds,data))
            match_values = expanded_match(data, match, members)
            incoming_matches.append([match_values.get(k, "") for k in match_columns])
            incoming_players.extend([[p.get(k, "") for k in player_columns]
                                     for p in expanded_players(data, members, match)])
            row["ESTADO"],row["MENSAJE"]="PROCESADA",""
            row["PROCESADA_UTC"]=datetime.now(timezone.utc).isoformat()
            changed=True
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                row["ESTADO"],row["MENSAJE"]="ERROR","Partida inexistente o fuera de retención de Riot"
            else:
                row["MENSAJE"]=str(exc)[:150]
        except ValueError as exc:
            row["ESTADO"],row["MENSAJE"]="ERROR",str(exc)[:150]
        except (RuntimeError,requests.RequestException) as exc:
            raise RuntimeError(f"Sincronización detenida en {mid}: {exc}") from exc
    if changed:
        ids_processed = [m[0] for m in incoming_matches]
        for tab, rows in advanced.items():
            header = EXTRA_TABS[tab]
            existing = [[r.get(k,"") for k in header] for r in records(book,tab)]
            for processed_id in ids_processed:
                existing = replace_by_match(existing,[r for r in rows if r[0]==processed_id],processed_id)
            write_rows(book,tab,header,existing)
        write_rows(book,"RAW_PARTIDAS",match_columns,upsert(old_matches,incoming_matches))
        write_rows(book,"RAW_JUGADORES",player_columns,upsert_participants(old_players,incoming_players))
    save_submission_queue(book, queue)
    repaired = repair_from_archive(book, members)
    if changed or repaired or force:
        rebuild(book)
    write_rows(book,"SISTEMA",TABS["SISTEMA"],[["ULTIMA_EJECUCION_UTC",datetime.now(timezone.utc).isoformat()],
                                               ["RESULTADO","OK" if changed else "SIN_CAMBIOS"]])
    present(book,players_book)

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
    required = ["GOOGLE_SHEET_ID","GOOGLE_SHEET_ID_PLAYERS","RIOT_API_KEY"]
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
            print("Riot API: validación efectiva al consultar una partida/jugador")
        elif args.command == "init":
            ensure(book)
            setup_players(book,players_book)
            present(book,players_book)
        elif args.command == "players":
            ensure(book)
            setup_players(book,players_book)
            resolve_players(players_book)
            present(book,players_book)
        elif args.command == "rebuild":
            ensure(book)
            setup_players(book,players_book)
            members = resolve_players(players_book)
            repair_from_archive(book, members)
            rebuild(book)
            present(book,players_book)
        else:
            sync(book,players_book,getattr(args,"ids",()),args.command in ("sync","refresh-match"),args.command=="refresh-match")
        print("Operación terminada")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc!r}",file=sys.stderr)
        sys.exit(1)
