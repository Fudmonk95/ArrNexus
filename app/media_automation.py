from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .db import db, setting_get, setting_set
from .jellyfin import JellyfinClient
from . import lists as media_lists

SOURCE_TYPES = {"manual":"Manual provider IDs", "tmdb":"TMDb", "imdb":"IMDb", "trakt_watchlist":"Trakt Watchlist", "trakt_list":"Trakt List", "rss":"RSS / Atom", "json":"Custom JSON", "simkl":"Simkl Watchlist"}

PRESETS = (
    {"slug":"trending-movies","name":"Trending Movies","media_type":"movie","source_type":"tmdb","source_ref":"https://www.themoviedb.org/trending/movie/week"},
    {"slug":"trending-tv","name":"Trending TV","media_type":"tv","source_type":"tmdb","source_ref":"https://www.themoviedb.org/trending/tv/week"},
    {"slug":"trakt-watchlist","name":"My Trakt Watchlist","media_type":"mixed","source_type":"trakt_watchlist","source_ref":"watchlist"},
)


def _utcnow(): return datetime.now(timezone.utc).isoformat()

def _loads(value, fallback):
    try: return json.loads(value or "")
    except Exception: return fallback


def list_definitions():
    with db() as conn:
        rows=conn.execute("SELECT * FROM media_automations ORDER BY name COLLATE NOCASE,id").fetchall(); out=[]
        for raw in rows:
            item=dict(raw); item["definition"]=_loads(item.pop("definition_json","{}"),{})
            item["targets"]=[dict(x) for x in conn.execute("SELECT * FROM media_automation_targets WHERE automation_id=? ORDER BY id",(int(item["id"]),)).fetchall()]
            latest=conn.execute("SELECT * FROM media_automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT 1",(int(item["id"]),)).fetchone()
            item["latest_run"]=dict(latest) if latest else None; out.append(item)
    return out


def get_definition(automation_id: int):
    return next((x for x in list_definitions() if int(x["id"])==int(automation_id)),None)


def save_definition(*, automation_id:int|None,name:str,media_type:str,source_type:str,source_ref:str,schedule_hours:int,enabled:bool,library_name:str,collection_name:str,summary:str="",manual_items:str=""):
    if not name.strip(): raise ValueError("Collection name is required")
    if media_type not in {"movie","tv","mixed"}: raise ValueError("Invalid media type")
    if source_type not in SOURCE_TYPES: raise ValueError("Unsupported source")
    definition={"summary":summary.strip(),"manual_items":manual_items.strip(),"schema":2}
    vals=(name.strip(),media_type,source_type,source_ref.strip(),json.dumps(definition),"jellyfin",int(enabled),max(1,min(720,int(schedule_hours or 24))),0,_utcnow())
    with db() as conn:
        if automation_id:
            conn.execute("UPDATE media_automations SET name=?,media_type=?,source_type=?,source_ref=?,definition_json=?,engine=?,enabled=?,schedule_hours=?,acquire_missing=?,updated_at=? WHERE id=?",vals+(int(automation_id),))
            ident=int(automation_id); conn.execute("DELETE FROM media_automation_targets WHERE automation_id=?",(ident,))
        else:
            cur=conn.execute("INSERT INTO media_automations(name,media_type,source_type,source_ref,definition_json,engine,enabled,schedule_hours,acquire_missing,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",vals); ident=int(cur.lastrowid)
        conn.execute("INSERT INTO media_automation_targets(automation_id,server_type,library_name,collection_name,engine,enabled) VALUES(?,?,?,?,?,1)",(ident,"jellyfin",library_name.strip(),collection_name.strip() or name.strip(),"jellyfin"))
    return ident


def delete_definition(automation_id:int):
    with db() as conn: conn.execute("DELETE FROM media_automations WHERE id=?",(int(automation_id),))


def _manual_items(text: str):
    out=[]
    for raw in re.split(r"[\n,]+", text or ""):
        value=raw.strip()
        if not value: continue
        if re.fullmatch(r"tt\d+",value,re.I): out.append({"media_type":"mixed","imdb_id":value.lower(),"title":value})
        elif re.fullmatch(r"movie:tmdb:\d+",value,re.I): out.append({"media_type":"movie","tmdb_id":value.rsplit(":",1)[1],"title":value})
        elif re.fullmatch(r"tv:tvdb:\d+",value,re.I): out.append({"media_type":"tv","tvdb_id":value.rsplit(":",1)[1],"title":value})
    return out


async def resolve_source(defn: dict[str,Any]):
    if defn.get("source_type") == "manual": return _manual_items((defn.get("definition") or {}).get("manual_items", ""))
    pseudo={
        "source_type":defn.get("source_type"),"source_ref":defn.get("source_ref"),"media_type":defn.get("media_type"),
    }
    items=await media_lists.fetch_items(pseudo)
    return [{"media_type":x.media_type,"title":x.title,"tmdb_id":x.tmdb_id,"tvdb_id":x.tvdb_id,"imdb_id":x.imdb_id} for x in items]


async def preview(defn: dict[str,Any]):
    desired=await resolve_source(defn); target=(defn.get("targets") or [{}])[0]
    jf=JellyfinClient(); inventory=await jf.inventory(str(target.get("library_name") or ""))
    def key(x):
        if x.get("tmdb_id"): return f"tmdb:{x['tmdb_id']}"
        if x.get("tvdb_id"): return f"tvdb:{x['tvdb_id']}"
        if x.get("imdb_id"): return f"imdb:{x['imdb_id']}"
        return "title:"+str(x.get("title") or "").casefold()
    inv={key(x):x for x in inventory}; matched=[x for x in desired if key(x) in inv]
    return {"ok":True,"desired":len(desired),"matched":len(matched),"missing":len(desired)-len(matched),"items":desired[:250]}


async def sync(defn: dict[str,Any]):
    desired=await resolve_source(defn); target=(defn.get("targets") or [{}])[0]
    result=await JellyfinClient().apply_collection(str(target.get("collection_name") or defn["name"]),str(target.get("library_name") or ""),desired)
    with db() as conn:
        cur=conn.execute("INSERT INTO media_automation_runs(automation_id,preview,status,result_json,finished_at) VALUES(?,0,?,?,?)",(int(defn["id"]),"complete" if result.get("ok") else "failed",json.dumps(result,default=str),_utcnow()))
        if target.get("id"):
            conn.execute("UPDATE media_automation_targets SET last_collection_id=?,last_status=?,last_error='',last_sync_at=? WHERE id=?",(str(result.get("collection_id") or ""),"complete",_utcnow(),int(target["id"])))
    return result


def kometa_yaml(defn: dict[str,Any], desired: list[dict[str,Any]]) -> str:
    name=str(defn.get("name") or "Collection").replace('"',"'")
    lines=["collections:",f'  "{name}":',"    sync_mode: append","    collection_order: custom"]
    imdb=[str(x.get("imdb_id")) for x in desired if x.get("imdb_id")]
    tmdb=[str(x.get("tmdb_id")) for x in desired if x.get("media_type")=="movie" and x.get("tmdb_id")]
    tvdb=[str(x.get("tvdb_id")) for x in desired if x.get("media_type")=="tv" and x.get("tvdb_id")]
    if imdb: lines += ["    imdb_id:"]+[f"      - {x}" for x in imdb]
    if tmdb: lines += ["    tmdb_movie:"]+[f"      - {x}" for x in tmdb]
    if tvdb: lines += ["    tvdb_show:"]+[f"      - {x}" for x in tvdb]
    summary=str((defn.get("definition") or {}).get("summary") or "").strip()
    if summary: lines.append("    summary: "+json.dumps(summary,ensure_ascii=False))
    return "\n".join(lines)+"\n"


async def export_kometa(automation_id:int) -> str:
    defn=get_definition(automation_id)
    if not defn: raise ValueError("Collection not found")
    return kometa_yaml(defn,await resolve_source(defn))


def import_kometa_yaml(text:str, library_name:str=""):
    lines=str(text or "").replace("\t","  ").splitlines(); in_collections=False; current=""; provider=""; found={}
    for raw in lines:
        clean=raw.split("#",1)[0].rstrip()
        if not clean.strip(): continue
        indent=len(clean)-len(clean.lstrip(" ")); value=clean.strip()
        if indent==0: in_collections=value=="collections:"; current=""; provider=""; continue
        if not in_collections: continue
        if indent==2 and value.endswith(":"):
            current=value[:-1].strip().strip("'\"")[:160]; found.setdefault(current,[])
        elif current and indent==4 and value.endswith(":"):
            key=value[:-1].strip(); provider=key if key in {"imdb_id","tmdb_movie","tvdb_show"} else ""
        elif current and provider and value.startswith("-"):
            ident=value[1:].strip().strip("'\"")
            if provider=="imdb_id" and re.fullmatch(r"tt\d+",ident,re.I): found[current].append(ident.lower())
            elif provider=="tmdb_movie" and ident.isdigit(): found[current].append(f"movie:tmdb:{ident}")
            elif provider=="tvdb_show" and ident.isdigit(): found[current].append(f"tv:tvdb:{ident}")
    created=[]
    for name,items in found.items():
        if items:
            created.append(save_definition(automation_id=None,name=name,media_type="mixed",source_type="manual",source_ref="",schedule_hours=24,enabled=False,library_name=library_name,collection_name=name,manual_items="\n".join(items)))
    return created


async def run_due() -> list[dict[str, Any]]:
    """Run enabled collection jobs whose configured interval has elapsed."""
    import time
    results: list[dict[str, Any]] = []
    now = time.time()
    for defn in list_definitions():
        if not defn.get("enabled"):
            continue
        latest = defn.get("latest_run") or {}
        last = latest.get("finished_at") or latest.get("created_at")
        due = True
        if last:
            try:
                due = now - datetime.fromisoformat(str(last).replace("Z", "+00:00")).timestamp() >= int(defn.get("schedule_hours") or 24) * 3600
            except Exception:
                due = True
        if not due:
            continue
        try:
            result = await sync(defn)
            results.append({"id": int(defn["id"]), "ok": True, "result": result})
        except Exception as exc:
            with db() as conn:
                conn.execute(
                    "INSERT INTO media_automation_runs(automation_id,preview,status,result_json,finished_at) VALUES(?,0,'failed',?,?)",
                    (int(defn["id"]), json.dumps({"error": str(exc)[:1000]}), _utcnow()),
                )
            results.append({"id": int(defn["id"]), "ok": False, "error": str(exc)})
    return results


async def scheduler_loop() -> None:
    import asyncio
    while True:
        try:
            await asyncio.sleep(120)
            await run_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(300)
