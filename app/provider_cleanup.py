from __future__ import annotations

"""Dependency-protected duplicate provider cleanup for ArrNexus v10.2.

The scanner reuses Library Consolidation's KEEP/REMOVE decisions.  Provider
content is eligible only when the exact source will have no surviving managed
library links after the requested link removals.  Real-Debrid deletion then
requires the existing exact provider-ID resolver; no fuzzy title deletion is
introduced here.
"""

import hashlib
import json
import os
from typing import Any

from .consolidation import scan_consolidation
from .library import build_source_link_index, invalidate_library_cache
from .scanner import inspect_item, invalidate_scan_cache
from .namespace import view_path
from . import realdebrid as rd


def _digest(rows: list[dict[str,Any]]) -> str:
    payload=[]
    for r in rows:
        payload.append({
            "source":r["source"],
            "links":sorted(r.get("removable_links") or []),
            "dependencies":sorted(r.get("surviving_links") or []),
        })
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def scan_provider_cleanup() -> dict[str,Any]:
    consolidation=scan_consolidation()
    index=build_source_link_index(force=True)
    candidates:dict[str,dict[str,Any]]={}
    for group in consolidation.get("groups") or []:
        keep_source=str((group.get("keep") or {}).get("source") or "")
        for c in group.get("remove") or []:
            source=str(c.get("source") or "")
            link=str(c.get("link") or "")
            if not source or not link or source==keep_source:
                continue
            row=candidates.setdefault(source,{"source":source,"source_name":source.rsplit("/",1)[-1],"removable_links":[],"groups":[],"keep_sources":set()})
            row["removable_links"].append(link)
            row["groups"].append(group.get("key"))
            if keep_source:row["keep_sources"].add(keep_source)
    rows=[]
    for source,row in candidates.items():
        current=sorted(set(index.get(source) or []))
        removable=sorted(set(row["removable_links"]))
        surviving=sorted(x for x in current if x not in set(removable))
        try:item=inspect_item(source); size=item.size_bytes; media=item.media_type; quality=item.quality
        except Exception:size=0;media="unknown";quality=0
        rows.append({
            "source":source,"source_name":row["source_name"],"media_type":media,"quality":quality,"size_bytes":size,
            "removable_links":removable,"surviving_links":surviving,"current_link_count":len(current),
            "dependency_safe_after_link_removal":not surviving,
            "already_orphaned":not current,"groups":sorted(set(row["groups"])),"keep_sources":sorted(row["keep_sources"]),
        })
    rows.sort(key=lambda r:r["source_name"].casefold())
    return {
        "rows":rows,"digest":_digest(rows),"duplicate_groups":consolidation.get("duplicate_groups",0),
        "recommended_links":sum(len(r["removable_links"]) for r in rows),
        "provider_candidates":len(rows),
    }


def _remove_exact_links(row:dict[str,Any]) -> tuple[list[str],list[str]]:
    removed=[];errors=[]
    for logical in row.get("removable_links") or []:
        try:
            actual=view_path(logical)
            if not actual.is_symlink():raise RuntimeError("path is no longer a symlink")
            target=os.readlink(actual)
            source=str(row.get("source") or "").rstrip("/")+"/"
            if not str(target).startswith(source):
                raise RuntimeError("symlink dependency changed since preview")
            actual.unlink();removed.append(logical)
        except Exception as exc:errors.append(f"{logical}: {exc}")
    return removed,errors


async def apply_provider_cleanup(expected_digest:str, action:str="both") -> dict[str,Any]:
    if action not in {"links","rd","both"}:raise ValueError("Unknown cleanup action")
    preview=scan_provider_cleanup()
    if not expected_digest or preview.get("digest")!=expected_digest:
        raise RuntimeError("Library/provider dependencies changed after preview. Refresh before applying cleanup.")
    removed=[];errors=[];provider=[]
    rows=preview.get("rows") or []
    if action in {"links","both"}:
        for row in rows:
            r,e=_remove_exact_links(row);removed.extend(r);errors.extend(e)
        invalidate_library_cache()
    links_after=build_source_link_index(force=True)
    if action in {"rd","both"}:
        for row in rows:
            source=row["source"]
            dependencies=links_after.get(source) or []
            if dependencies:
                provider.append({"source":source,"ok":False,"deleted":False,"reason":f"Refused: {len(dependencies)} surviving managed link(s) still depend on this source"})
                continue
            try:
                item=inspect_item(source)
                result=await rd.delete_source_torrent_exact(source,item.size_bytes)
            except Exception as exc:
                result={"ok":False,"deleted":False,"reason":str(exc)}
            provider.append({"source":source,**result})
    if removed or any(x.get("deleted") for x in provider):
        invalidate_library_cache();invalidate_scan_cache()
    return {"removed_links":removed,"errors":errors,"provider_cleanup":provider,"action":action,"preview_digest":expected_digest}
