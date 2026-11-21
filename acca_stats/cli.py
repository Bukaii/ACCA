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
from .analytics import EXTRA_TABS, archive, detailed_rows, timeline_rows, associate_builds, replace_by_match, unarchive
from . import supabase_archive
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
    # Restore complete historical player analytics from the untouched original
    # J_* reports in the general workbook. Append current match history below.
    # Never replace the multi-section source with a reduced match-only table.
    player_header = ["MATCH_ID","FECHA_UTC","MODO","RESULTADO","CAMPEON","ROL",
                     "KILLS","DEATHS","ASSISTS","KDA","CS_MIN","ORO","DANO","VISION","ITEMS"]
    by_match = {str(m.get("MATCH_ID")): m for m in matches}
    for source in book.worksheets():
        if not source.title.startswith("J_"):
            continue
        alias = source.title[2:]
        historical = source.get_all_values()
        if not historical:
            continue
        player_rows = []
        for p in participants:
            if str(p.get("ALIAS","")) != alias or p.get("ES_ACCA") != "SI":
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
        # Keep every original analytical section; add new history after it.
        rows = [list(row) for row in historical]
        rows.extend([[], ["HISTORIAL DE PARTIDAS REGISTRADAS"], player_header])
        rows.extend(player_rows)
        ws = ensure_tab(players_book,source.title,[])
        width = max(18, max(map(len,rows)))
        if ws.col_count < width:
            ws.add_cols(width-ws.col_count)
        if ws.row_count < len(rows):
            ws.add_rows(len(rows)-ws.row_count)
        from gspread.utils import rowcol_to_a1
        # Unmerge old report cells and remove stale section bars before writing.
        players_book.batch_update({"requests":[
            {"repeatCell":{"range":{"sheetId":ws.id},
                "cell":{"userEnteredFormat":{}},"fields":"userEnteredFormat"}}
        ]})
        ws.update(range_name="A1",values=rows,value_input_option="RAW")
        old_length = len(ws.get_all_values())
        if old_length > len(rows):
            ws.batch_clear([f"A{len(rows)+1}:{rowcol_to_a1(old_length,ws.col_count)}"])
        # Dashboard-like visual hierarchy: a legible identity banner,
        # distinct section cards, separated table headers and breathing room.
        # Formatting only: never delete, move or truncate historical metrics.
        # Classify every section by its position and surrounding table,
        # not by a short hardcoded list of examples.
        def filled(row):
            return [str(v).strip() for v in row if str(v).strip()]
        def single(i):
            return i < len(rows) and len(filled(rows[i])) == 1
        def blank(i):
            return i < 0 or i >= len(rows) or not filled(rows[i])
        def looks_header(row):
            cells = filled(row)
            if len(cells)<2:
                return False
            first = cells[0]
            return (first == first.upper() and any(c.isalpha() for c in first)
                    and all(len(c)<65 for c in cells[:5]))
        section_rows = []
        description_rows = []
        empty_state_rows = []
        header_rows = []
        for i,row in enumerate(rows):
            if i < 3 or not filled(row):
                continue
            label = str(row[0]).strip()
            if looks_header(row):
                header_rows.append(i)
            elif single(i) and label.casefold().startswith(("sin datos","no hay datos")):
                empty_state_rows.append(i)
            elif single(i) and blank(i-1):
                section_rows.append(i)
            elif single(i) and i-1 in section_rows:
                description_rows.append(i)
            elif single(i) and i+1<len(rows) and looks_header(rows[i+1]):
                description_rows.append(i)
            elif single(i):
                description_rows.append(i)
        navy = {"red":0.12,"green":0.19,"blue":0.30}
        blue = {"red":0.19,"green":0.32,"blue":0.49}
        pale = {"red":0.91,"green":0.94,"blue":0.98}
        white = {"red":1,"green":1,"blue":1}
        ink = {"red":0.12,"green":0.18,"blue":0.27}
        requests = [
            {"updateSheetProperties":{"properties":{"sheetId":ws.id,
                "gridProperties":{"frozenRowCount":0,"hideGridlines":True}},
                "fields":"gridProperties.frozenRowCount,gridProperties.hideGridlines"}},
            {"updateDimensionProperties":{"range":{"sheetId":ws.id,
                "dimension":"COLUMNS","startIndex":0,"endIndex":1},
                "properties":{"pixelSize":350},"fields":"pixelSize"}},
            {"updateDimensionProperties":{"range":{"sheetId":ws.id,
                "dimension":"COLUMNS","startIndex":1,"endIndex":2},
                "properties":{"pixelSize":260},"fields":"pixelSize"}},
            {"repeatCell":{"range":{"sheetId":ws.id},
                "cell":{"userEnteredFormat":{"backgroundColor":white,
                    "verticalAlignment":"MIDDLE","wrapStrategy":"WRAP",
                    "textFormat":{"fontSize":10,"foregroundColor":ink}}},
                "fields":"userEnteredFormat"}},
        ]
        if width>2:
            requests.append({"updateDimensionProperties":{"range":{"sheetId":ws.id,
                "dimension":"COLUMNS","startIndex":2,"endIndex":width},
                "properties":{"pixelSize":145},"fields":"pixelSize"}})
        def paint(i, color, font_color, size, height, bold=True):
            requests.append({"repeatCell":{"range":{"sheetId":ws.id,
                "startRowIndex":i,"endRowIndex":i+1,"startColumnIndex":0,
                "endColumnIndex":width},
                "cell":{"userEnteredFormat":{"backgroundColor":color,
                    "verticalAlignment":"MIDDLE","wrapStrategy":"WRAP",
                    "textFormat":{"bold":bold,"fontSize":size,
                        "foregroundColor":font_color}}},
                "fields":"userEnteredFormat"}})
            requests.append({"updateDimensionProperties":{"range":{"sheetId":ws.id,
                "dimension":"ROWS","startIndex":i,"endIndex":i+1},
                "properties":{"pixelSize":height},"fields":"pixelSize"}})
        # Name is the primary page heading, not a tiny cell label.
        paint(0,navy,white,15,68)
        # Region and report explanation form a separate, readable intro.
        for i in (1,2):
            if i < len(rows):
                paint(i,pale,ink,10,36 if i==1 else 58,False)
        for i in section_rows:
            paint(i,blue,white,15,58)
            requests.append({"updateBorders":{"range":{"sheetId":ws.id,
                "startRowIndex":i,"endRowIndex":i+1,
                "startColumnIndex":0,"endColumnIndex":width},
                "top":{"style":"SOLID_MEDIUM","color":navy}}})
        for i in description_rows:
            paint(i,{"red":0.95,"green":0.97,"blue":0.99},ink,10,46,False)
        for i in empty_state_rows:
            paint(i,{"red":0.96,"green":0.97,"blue":0.98},
                  {"red":0.40,"green":0.46,"blue":0.53},10,46,False)
        for i in header_rows:
            paint(i,pale,ink,10,46)
        # White margins between cards and softly striped data rows.
        for i,row in enumerate(rows):
            if i in section_rows or i in description_rows or i in empty_state_rows or i in header_rows or i<3:
                continue
            if not any(str(v).strip() for v in row):
                requests.append({"updateDimensionProperties":{"range":{"sheetId":ws.id,
                    "dimension":"ROWS","startIndex":i,"endIndex":i+1},
                    "properties":{"pixelSize":22},"fields":"pixelSize"}})
            elif len(row)>1:
                requests.append({"repeatCell":{"range":{"sheetId":ws.id,
                    "startRowIndex":i,"endRowIndex":i+1,"startColumnIndex":0,
                    "endColumnIndex":width},
                    "cell":{"userEnteredFormat":{"backgroundColor":
                        white if i%2 else {"red":0.97,"green":0.98,"blue":0.99},
                        "verticalAlignment":"MIDDLE","wrapStrategy":"WRAP",
                        "textFormat":{"fontSize":10,"foregroundColor":ink}}},
                    "fields":"userEnteredFormat"}})
                requests.append({"updateDimensionProperties":{"range":{"sheetId":ws.id,
                    "dimension":"ROWS","startIndex":i,"endIndex":i+1},
                    "properties":{"pixelSize":34},"fields":"pixelSize"}})
        # Existing legacy merges (often A:G) truncate the visible color and
        # the player's name. Remove them before merging full-width section bars.
        meta = players_book.fetch_sheet_metadata(
            params={"fields":"sheets(properties(sheetId),merges)"})
        old_merges = next((sh.get("merges",[]) for sh in meta.get("sheets",[])
                           if sh["properties"]["sheetId"]==ws.id),[])
        for merged in old_merges:
            requests.insert(0,{"unmergeCells":{"range":merged}})
        # Merge only rows known to contain a single value in column A.
        # Never merge table headers or statistics, so no data is lost.
        for i in [0,1,2]+section_rows+description_rows+empty_state_rows:
            if i < len(rows) and len(rows[i])==1:
                requests.append({"mergeCells":{"range":{"sheetId":ws.id,
                    "startRowIndex":i,"endRowIndex":i+1,
                    "startColumnIndex":0,"endColumnIndex":width},
                    "mergeType":"MERGE_ALL"}})
        players_book.batch_update({"requests":requests})
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
    # Keep internal identifiers unchanged for data ingestion and rebuilding.
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
    """Google Sheets is an input and reporting UI; Supabase stores Riot data."""
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
    for row in queue:
        mid = row["MATCH_ID"]
        if row.get("ESTADO") == "PROCESADA" and not refresh_ids:
            continue
        route = PLATFORMS.get(mid.split("_",1)[0])
        if not route:
            row["ESTADO"],row["MENSAJE"]="ERROR","Plataforma desconocida"
            continue
        try:
            data = supabase_archive.load(mid,"match")
            timeline = supabase_archive.load(mid,"timeline")
            if data is None:
                data = riot_get(f"https://{route}.api.riotgames.com/lol/match/v5/matches/{mid}")
            if timeline is None:
                timeline = riot_get(f"https://{route}.api.riotgames.com/lol/match/v5/matches/{mid}/timeline")
            supabase_archive.store_pair(mid,data,timeline)
            row["ESTADO"],row["MENSAJE"]="PROCESADA",""
            row["PROCESADA_UTC"]=datetime.now(timezone.utc).isoformat()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                row["ESTADO"],row["MENSAJE"]="ERROR","Partida no encontrada en Riot"
            else:
                raise
        except ValueError as exc:
            row["ESTADO"],row["MENSAJE"]="ERROR",str(exc)[:150]
    write_rows(book,"REGISTRO",GENERAL["REGISTRO"],
               [[r.get(k,"") for k in GENERAL["REGISTRO"]] for r in queue])
    matches,participants,details = [],[],[]
    for mid in supabase_archive.list_match_ids():
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
