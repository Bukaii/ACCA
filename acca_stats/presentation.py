"""Unified, compact presentation for ACCA's two Google workbooks."""
from .core import summary

GENERAL = {
    "RESUMEN": ["SECCION","CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
    "PARTIDAS": ["MATCH_ID","FECHA_UTC","MODO","VERSION","LADO_ACCA","RESULTADO","EQUIPO_ACCA","N_ACCA","COMPOSICION_ACCA","COMPOSICION_RIVAL","BANS_ACCA","BANS_RIVAL","DURACION_SEG","FUENTE"],
    "REGISTRO": ["MATCH_ID","ESTADO","MENSAJE","LADO_ACCA","PROCESADA_UTC"],
}
PLAYERS = {
    "JUGADORES": ["ALIAS","RIOT_ID","REGION","ACTIVO","PUUID","ESTADO","MENSAJE"],
}
NAVY = {"red":0.13,"green":0.20,"blue":0.32}
PALE = {"red":0.92,"green":0.95,"blue":0.98}
WHITE = {"red":1,"green":1,"blue":1}
DARK = {"red":0.14,"green":0.19,"blue":0.26}

def ensure_tab(book,name,header):
    import gspread
    try:
        ws=book.worksheet(name)
    except gspread.WorksheetNotFound:
        ws=book.add_worksheet(title=name,rows=100,cols=max(len(header),16))
    current=ws.row_values(1)
    if not current:
        ws.update(range_name="A1",values=[header])
    elif current != header:
        # Presentation tabs are generated; only replace their header.
        ws.update(range_name="A1",values=[header])
    return ws

def write_view(book,name,header,rows):
    ws=ensure_tab(book,name,header)
    current=ws.get_all_values()
    if ws.col_count<len(header):
        ws.add_cols(len(header)-ws.col_count)
    needed=max(2,len(rows)+1)
    if ws.row_count<needed:
        ws.add_rows(needed-ws.row_count)
    ws.update(range_name="A1",values=[header]+rows,value_input_option="RAW")
    if len(current)>len(rows)+1:
        from gspread.utils import rowcol_to_a1
        ws.batch_clear([f"A{len(rows)+2}:{rowcol_to_a1(len(current),max(len(header),len(current[0])))}"])
    return ws

def style(book,names):
    """Repeatable styling: banding and separators cover the entire growing table."""
    requests=[]
    meta=book.fetch_sheet_metadata(params={"fields":"sheets(properties(sheetId),bandedRanges(bandedRangeId))"})
    existing={sh["properties"]["sheetId"]:sh.get("bandedRanges",[]) for sh in meta.get("sheets",[])}
    for name in names:
        ws=book.worksheet(name)
        gid=ws.id
        width=min(ws.col_count, max(7,len(ws.row_values(1))))
        end=ws.row_count
        requests.extend([
            {"updateSheetProperties":{"properties":{"sheetId":gid,"gridProperties":{"frozenRowCount":1,"hideGridlines":True}},"fields":"gridProperties.frozenRowCount,gridProperties.hideGridlines"}},
            {"repeatCell":{"range":{"sheetId":gid,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":width},
                "cell":{"userEnteredFormat":{"backgroundColor":NAVY,"textFormat":{"foregroundColor":WHITE,"bold":True,"fontSize":10},"verticalAlignment":"MIDDLE","wrapStrategy":"WRAP"}},
                "fields":"userEnteredFormat"}},
            {"updateDimensionProperties":{"range":{"sheetId":gid,"dimension":"ROWS","startIndex":0,"endIndex":1},"properties":{"pixelSize":38},"fields":"pixelSize"}},
            {"updateDimensionProperties":{"range":{"sheetId":gid,"dimension":"COLUMNS","startIndex":0,"endIndex":width},"properties":{"pixelSize":155},"fields":"pixelSize"}},
        ])
        # Wide identifiers and compositions must remain readable, not clipped.
        wide = {"RESUMEN":[1], "COMPOSICIONES":[0],
                "PARTIDAS":[6,8,9,10,11], "DETALLE DE PARTIDAS":[11,13,14,15,16],
                "GUIA":[1]}
        for column in wide.get(name,[]):
            if column < ws.col_count:
                requests.append({"updateDimensionProperties":{"range":{"sheetId":gid,
                    "dimension":"COLUMNS","startIndex":column,"endIndex":column+1},
                    "properties":{"pixelSize":420 if name == "GUIA" else 320},
                    "fields":"pixelSize"}})
        band={"range":{"sheetId":gid,"startRowIndex":0,"endRowIndex":end,"startColumnIndex":0,"endColumnIndex":width},
              "rowProperties":{"headerColor":NAVY,"firstBandColor":WHITE,"secondBandColor":PALE}}
        if existing.get(gid):
            requests.append({"updateBanding":{"bandedRange":{"bandedRangeId":existing[gid][0]["bandedRangeId"],**band},"fields":"range,rowProperties"}})
        else:
            requests.append({"addBanding":{"bandedRange":band}})
    if requests:
        book.batch_update({"requests":requests})


def hide_legacy(book,keep):
    """Never delete history. Hide obsolete tabs only after both workbooks are written."""
    requests=[]
    for ws in book.worksheets():
        if ws.title in keep:
            continue
        if not ws._properties.get("hidden",False):
            requests.append({"updateSheetProperties":{"properties":{"sheetId":ws.id,"hidden":True},"fields":"hidden"}})
    if requests:
        book.batch_update({"requests":requests})

_champion_names = {}

def display_bans(value, version):
    """Only presentation converts champion numeric IDs; raw values stay exportable."""
    ids = [v.strip() for v in str(value or "").split("|") if v.strip()]
    if not ids:
        return "Sin baneos"
    version = str(version or "").replace(",", ".")
    if version not in _champion_names:
        try:
            import requests
            response = requests.get(
                f"https://ddragon.leagueoflegends.com/cdn/{version}.1/data/es_ES/champion.json",
                timeout=12)
            response.raise_for_status()
            _champion_names[version] = {
                str(c["key"]):c["name"] for c in response.json()["data"].values()
            }
        except Exception:
            _champion_names[version] = {}
    return " + ".join("Sin ban" if v in ("0", "-1") else _champion_names[version].get(v, "ID "+v) for v in ids)


def build_general(matches):
    result=[]
    for label,key in (("GENERAL",lambda r:"ACCA"),
                      ("EQUIPOS",lambda r:str(r.get("EQUIPO_ACCA") or r.get("INTEGRANTES","")))):
        result += [[label]+r for r in summary(matches,key)]
    for label,field in (("COMPOSICIONES","COMPOSICION_ACCA"),("RIVALES","COMPOSICION_RIVAL")):
        populated=[m for m in matches if m.get(field)]
        result += [[label]+r for r in summary(populated,lambda m:str(m.get(field,"")))]
    return result
