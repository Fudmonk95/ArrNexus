from __future__ import annotations

import re
from typing import Any


RULES = [
    (
        re.compile(r"failed to write to cache file:\s*404\s+Not Found", re.I),
        "Upstream content returned 404 while the VFS was filling its cache.",
        "The virtual file points at data that the upstream source can no longer provide, or a stale/missing article/object was reached during a read. This is a media-source/VFS problem rather than a normal ArrNexus UI error.",
        ["Run InfiniDysk Health & Repairs / Preflight for the affected release", "Retry or replace the affected NZB/release", "If you are on an older InfiniDysk build, update before investigating repeated missing-article failures"],
    ),
    (
        re.compile(r"could not seek to byte position", re.I),
        "The player or scanner requested a byte range that the virtual stream could not satisfy.",
        "This often follows missing/corrupt Usenet articles, an unavailable VFS object, or a failed cache fill. Repeated messages for the same title usually mean that release should be health-checked or replaced.",
        ["Open the affected title in InfiniDysk Health & Repairs", "Check whether the same title has nearby 404/cache errors", "Replace the release if repair cannot restore the missing data"],
    ),
    (
        re.compile(r"not enough free space", re.I),
        "An Arr free-space check rejected an import.",
        "Radarr/Sonarr believes the destination root does not have enough free space for the import. With virtual/symlink libraries, the reported free-space source can be misleading if the mount reports a small backing filesystem.",
        ["Check the Arr root-folder free-space value", "Verify the DUMB/virtual mount reports sensible disk space", "Confirm the import is using a symlink/virtual destination rather than copying the full file"],
    ),
    (
        re.compile(r"missing article|article.*not found|430|423", re.I),
        "Usenet article data is missing or unavailable.",
        "The NZB references one or more articles that the configured providers cannot currently retrieve.",
        ["Run Preflight/health check", "Try another Usenet provider/backbone if available", "Search for a replacement release"],
    ),
    (
        re.compile(r"401|unauthori[sz]ed|invalid api|authentication failed", re.I),
        "Authentication failed.",
        "The service rejected the configured credential/token.",
        ["Re-enter the connector credential", "Use Save & verify in Ecosystem", "Rotate the token if you are unsure whether it was exposed"],
    ),
]


def explain_log(message: str) -> dict[str, Any] | None:
    text = str(message or "")
    for regex, title, explanation, actions in RULES:
        if regex.search(text):
            return {"title": title, "explanation": explanation, "actions": actions}
    return None


def attach_explanations(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["diagnostic"] = explain_log(str(row.get("message") or ""))
    return rows
