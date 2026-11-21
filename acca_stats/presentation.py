"""Unified, compact presentation for ACCA's two Google workbooks."""
from collections import defaultdict
from .core import summary

GENERAL = {
    "RESUMEN": ["SECCION","CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
    "PARTIDAS": ["MATCH_ID","FECHA_UTC","MODO","VERSION","LADO_ACCA","RESULTADO","EQUIPO_ACCA","N_ACCA","COMPOSICION_ACCA","COMPOSICION_RIVAL","BANS_ACCA","BANS_RIVAL","DURACION_SEG","FUENTE"],
    "REGISTRO": ["MATCH_ID","ESTADO","MENSAJE","LADO_ACCA","PROCESADA_UTC"],
}
PLAYERS = {
    "JUGADORES": ["ALIAS","RIOT_ID","REGION","ACTIVO","PUUID","ESTADO","MENSAJE"],
    "IDENTIDADES": ["ALIAS","RIOT_ID","REGION","PUUID","ESTADO","MENSAJE"],
    "RENDIMIENTO": ["SECCION","CLAVE","PARTIDAS","VICTORIAS","DERROTAS","WINRATE_%","WR_MIN_95_%"],
    "PARTICIPACIONES": ["MATCH_ID","ALIAS","CAMPEON","ROL","RESULTADO","KILLS","DEATHS","ASSISTS","KDA","KP_%","CS_MIN","ORO_MIN","DANO_MIN","VISION","ITEMS"],
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

def build_general(matches):
    result=[]
    for label,key in (("GENERAL",lambda r:"ACCA"),
                      ("EQUIPOS",lambda r:str(r.get("EQUIPO_ACCA") or r.get("INTEGRANTES","")))):
        result += [[label]+r for r in summary(matches,key)]
    for label,field in (("COMPOSICIONES","COMPOSICION_ACCA"),("RIVALES","COMPOSICION_RIVAL")):
        populated=[m for m in matches if m.get(field)]
        result += [[label]+r for r in summary(populated,lambda m:str(m.get(field,"")))]
    return result

def build_players(matches,players,details):
    by_match={str(m.get("MATCH_ID")):m for m in matches}
    by_detail={(str(d.get("MATCH_ID")),str(d.get("PUUID"))):d for d in details}
    ranked=[]
    participations=[]
    compositions=defaultdict(list)
    for p in players:
        if p.get("ES_ACCA")!="SI" or p.get("EQUIPO_ACCA")!="SI":
            continue
        mid=str(p.get("MATCH_ID",""))
        m=by_match.get(mid)
        if not m:
            continue
        alias=str(p.get("ALIAS",""))
        champion=str(p.get("CAMPEON",""))
        role=str(p.get("ROL",""))
        outcome=m.get("RESULTADO","")
        d=by_detail.get((mid,str(p.get("PUUID",""))),{})
        participations.append([mid,alias,champion,role,outcome,
             p.get("KILLS",0),p.get("DEATHS",0),p.get("ASSISTS",0),
             d.get("KDA",p.get("KDA","")),d.get("KP_%",p.get("KP_%","")),d.get("CS_MIN",p.get("CS_MIN","")),
             d.get("ORO_MIN",p.get("ORO_MIN","")),d.get("DANO_MIN",p.get("DANO_MIN","")),p.get("VISION_SCORE",p.get("VISION",0)),p.get("ITEMS","")])
        for section,key in (("JUGADORES",alias),("CAMPEONES",alias+" | "+champion),
                            ("ROLES",alias+" | "+role)):
            ranked.append({"SECTION":section,"KEY":key,"RESULTADO":outcome})
        for item in set(str(p.get("ITEMS","")).split("|")):
            if item.isdigit() and int(item)>0:
                ranked.append({"SECTION":"OBJETOS","KEY":alias+" | "+champion+" | "+item,"RESULTADO":outcome})
        compositions[mid].append(champion)
    for mid,champs in compositions.items():
        if len(champs)==5:
            ranked.append({"SECTION":"COMPOSICIONES","KEY":" | ".join(sorted(champs)),"RESULTADO":by_match[mid].get("RESULTADO")})
    sections=["JUGADORES","CAMPEONES","ROLES","OBJETOS","COMPOSICIONES"]
    out=[]
    for section in sections:
        rows=[{"RESULTADO":r["RESULTADO"],"KEY":r["KEY"]} for r in ranked if r["SECTION"]==section]
        out.extend([[section]+r for r in summary(rows,lambda r:r["KEY"])])
    return out,participations
