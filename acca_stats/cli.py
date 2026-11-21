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
from .presentation import GENERAL, PLAYERS, ensure_tab, write_view, style, hide_legacy, build_general, build_players

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
    write_view(book,"PARTIDAS",GENERAL["PARTIDAS"],[[m.get(k,"") for k in GENERAL["PARTIDAS"]] for m in matches])
    # REGISTRO is the user-facing submission queue; do not overwrite it from a legacy tab.
    # Existing entries and statuses must survive every presentation refresh.
    ranking,history=build_players(matches,participants,details)
    write_view(players_book,"RENDIMIENTO",PLAYERS["RENDIMIENTO"],ranking)
    write_view(players_book,"PARTICIPACIONES",PLAYERS["PARTICIPACIONES"],history)
    # Preserve the original, extensive per-player reports in the player workbook.
    # The historical J_* sheets contain champion, role, item, ordered builds,
    # teammates, monthly evolution, and other calculated sections.
    for source in book.worksheets():
        if not source.title.startswith("J_"):
            continue
        values = source.get_all_values()
        if not values:
            continue
        destination = ensure_tab(players_book, source.title, values[0])
        if destination.col_count < max(map(len, values)):
            destination.add_cols(max(map(len, values)) - destination.col_count)
        if destination.row_count < len(values):
            destination.add_rows(len(values) - destination.row_count)
        destination.update(range_name="A1", values=values, value_input_option="RAW")
        # A generated player report must not retain stale trailing rows.
        old_rows = destination.get_all_values()
        if len(old_rows) > len(values):
            destination.batch_clear([f"A{len(values)+1}:Z{len(old_rows)}"])
    player_names = set(PLAYERS) | {ws.title for ws in players_book.worksheets()
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
    visible = set(GENERAL) | {"CAMPEONES", "PARTIDAS_PENDIENTES", "PARTIDAS_MANUALES"} | set(friendly.values())
    style(book, visible)
    hide_legacy(book, visible)
    for ws in book.worksheets():
        if ws.title in visible and ws._properties.get("hidden", False):
            book.batch_update({"requests": [{"updateSheetProperties": {"properties": {"sheetId": ws.id, "hidden": False}, "fields": "hidden"}}]})
    # Display the most useful views first, keeping all technical tables intact.
    ordered = ["REGISTRO", "PARTIDAS_PENDIENTES", "PARTIDAS_MANUALES", "RESUMEN", "PARTIDAS", "DETALLE DE PARTIDAS",
               "CAMPEONES", "EQUIPOS", "COMPOSICIONES",
               "OBJETOS", "OBJETIVOS", "EVENTOS", "REGISTRO"]
    book.batch_update({"requests": [
        {"updateSheetProperties": {
            "properties": {"sheetId": book.worksheet(name).id, "index": index},
            "fields": "index"
        }} for index, name in enumerate(ordered)
    ]})
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
    rows = records(book, "JUGADORES")
    seen = set()
    for row in rows:
        alias = str(row["ALIAS"]).strip()
        if not alias:
            continue
        if alias.casefold() in seen:
            row["ESTADO"],row["MENSAJE"] = "ERROR","Alias duplicado"
            continue
        seen.add(alias.casefold())
        if str(row.get("ACTIVO","SI")).upper() == "NO":
            continue
        riot_id = str(row["RIOT_ID"]).strip()
        region = str(row["REGION"]).upper().strip()
        if "#" not in riot_id or region not in ROUTES:
            row["ESTADO"],row["MENSAJE"] = "ERROR","RIOT_ID o REGION inválidos"
            continue
        if row.get("PUUID") and row.get("ESTADO") == "OK":
            continue
        name, tag = riot_id.rsplit("#",1)
        from urllib.parse import quote
        try:
            data = riot_get(f"https://{ROUTES[region]}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(name,safe='')}/{quote(tag,safe='')}")
            row["PUUID"],row["ESTADO"],row["MENSAJE"] = data["puuid"],"OK",""
        except Exception as exc:
            row["ESTADO"],row["MENSAJE"] = "ERROR",str(exc)[:150]
    write_rows(book,"JUGADORES",TABS["JUGADORES"],
               [[r.get(k,"") for k in TABS["JUGADORES"]] for r in rows])
    return {str(r["PUUID"]):str(r["ALIAS"]) for r in rows if r.get("PUUID") and r.get("ALIAS")}

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

def sync(book, players_book, ids=(), force=False):
    ensure(book)
    setup_players(book,players_book)
    members = resolve_players(players_book)
    queue = records(book,"REGISTRO") if "REGISTRO" in [w.title for w in book.worksheets()] else records(book,"PARTIDAS_PENDIENTES")
    existing = {str(r.get("MATCH_ID","")).upper() for r in queue}
    for mid in ids:
        mid = mid.strip().upper()
        if mid and mid not in existing:
            queue.append({"MATCH_ID":mid,"ESTADO":"PENDIENTE","MENSAJE":"","LADO_ACCA":"","PROCESADA_UTC":""})
            existing.add(mid)
        elif mid:
            for row in queue:
                if str(row.get("MATCH_ID","")).upper() == mid:
                    row["ESTADO"]="PENDIENTE"
    old_matches = [[r.get(k,"") for k in HEADERS_MATCH] for r in records(book,"RAW_PARTIDAS")]
    old_players = [[r.get(k,"") for k in HEADERS_PLAYER] for r in records(book,"RAW_JUGADORES")]
    incoming_matches, incoming_players = [],[]
    advanced = {name: [] for name in ("RAW_RIOT_ARCHIVO","RAW_EVENTOS","RAW_EQUIPOS","RAW_DETALLE","RAW_BUILDS")}
    changed = False
    for row in queue:
        if str(row.get("ESTADO","")).upper() not in ("","PENDIENTE"):
            continue
        mid = str(row.get("MATCH_ID","")).upper().strip()
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
            incoming_matches.append(match)
            incoming_players.extend(players)
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
        write_rows(book,"RAW_PARTIDAS",HEADERS_MATCH,upsert(old_matches,incoming_matches))
        write_rows(book,"RAW_JUGADORES",HEADERS_PLAYER,upsert_participants(old_players,incoming_players))
    write_rows(book,"PARTIDAS_PENDIENTES",TABS["PARTIDAS_PENDIENTES"],
               [[r.get(k,"") for k in TABS["PARTIDAS_PENDIENTES"]] for r in queue])
    write_view(book,"REGISTRO",GENERAL["REGISTRO"],[[r.get(k,"") for k in GENERAL["REGISTRO"]] for r in queue])
    if changed or force:
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
            rebuild(book)
            present(book,players_book)
        else:
            sync(book,players_book,getattr(args,"ids",()),args.command=="sync")
        print("Operación terminada")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc!r}",file=sys.stderr)
        sys.exit(1)
