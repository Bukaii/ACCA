"""Private, lossless Riot payload storage in Supabase Storage.

The original match and timeline JSON are gzip-compressed, byte-for-byte
reconstructable after JSON serialization. PostgreSQL holds an index only.
"""
import gzip
import hashlib
import json
import os
from urllib.parse import quote
import requests

BUCKET = "acca-riot-json"

def configured():
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"))

def _headers():
    key = os.environ["SUPABASE_SECRET_KEY"]
    return {"apikey": key, "Authorization": "Bearer " + key}

def _object_path(match_id, kind):
    if kind not in ("match", "timeline"):
        raise ValueError("Tipo de JSON desconocido")
    if not match_id or not all(c.isalnum() or c in "_-" for c in match_id):
        raise ValueError("MATCH_ID inválido")
    return match_id + "/" + kind + ".json.gz"

def _url(path):
    return os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1/object/" + BUCKET + "/" + quote(path, safe="/")

def load(match_id, kind):
    if not configured():
        return None
    response = requests.get(_url(_object_path(match_id,kind)),
                            headers=_headers(), timeout=40)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return json.loads(gzip.decompress(response.content).decode("utf-8"))

def store(match_id, kind, data):
    if not configured():
        raise RuntimeError("Configurar SUPABASE_URL y SUPABASE_SECRET_KEY en GitHub Secrets")
    path = _object_path(match_id, kind)
    raw = json.dumps(data, ensure_ascii=False, separators=(",",":")).encode("utf-8")
    payload = gzip.compress(raw)
    digest = hashlib.sha256(raw).hexdigest()
    headers = {**_headers(),"Content-Type":"application/gzip","x-upsert":"true"}
    response = requests.post(_url(path),headers=headers,data=payload,timeout=60)
    response.raise_for_status()
    # Read back and verify before treating the archive as durable.
    check = load(match_id,kind)
    if check != data:
        raise RuntimeError("Verificación de archivo Supabase falló: "+match_id)
    return path,digest

def store_pair(match_id, match, timeline):
    match_path, match_hash = store(match_id,"match",match)
    timeline_path, timeline_hash = store(match_id,"timeline",timeline)
    row = {"match_id":match_id,"match_path":match_path,
           "timeline_path":timeline_path,"match_sha256":match_hash,
           "timeline_sha256":timeline_hash}
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/acca_match_archives?on_conflict=match_id"
    response = requests.post(url,headers={**_headers(),"Content-Type":"application/json",
                             "Prefer":"resolution=merge-duplicates"},json=row,timeout=30)
    response.raise_for_status()
    return row
