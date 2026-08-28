#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from app.help_catalog import TOPICS, topic_for_path


def user_guide() -> str:
    lines = [
        "# ArrNexus User Guide",
        "",
        "This guide is generated from the same documentation catalogue used by the in-app `/help` Help Centre.",
        "",
        "Every guide is structured around prerequisites, setup, normal use, success criteria, troubleshooting and safety/privacy. When third-party provider rules can change, verify the provider's current official documentation as well.",
        "",
    ]
    current = None
    for topic in TOPICS:
        if topic["category"] != current:
            current = topic["category"]
            lines += [f"# {current}", ""]
        lines += [f"## {topic['title']}", "", topic["summary"], ""]
        sections = [
            ("Before you start", "prerequisites", False),
            ("Setup", "setup", True),
            ("How to use it", "usage", False),
            ("What working looks like", "success", False),
            ("If it does not work", "troubleshooting", False),
            ("Safety / privacy", "safety", False),
        ]
        for heading, key, ordered in sections:
            values = topic.get(key) or []
            if not values:
                continue
            lines += [f"### {heading}", ""]
            for idx, value in enumerate(values, 1):
                prefix = f"{idx}." if ordered else "-"
                lines.append(f"{prefix} {value}")
            lines.append("")
        if topic.get("related"):
            lines += ["### Related guides", "", ", ".join(f"`{x}`" for x in topic["related"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def route_rows() -> list[tuple[str, str, str, str]]:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    rows = []
    pattern = re.compile(r'@app\.(get|post|put|delete|patch)\("([^"]+)"(?:,\s*response_class=([^\)]+))?\)\s*\n(?:async\s+)?def\s+([A-Za-z0-9_]+)', re.M)
    # Async defs are used almost everywhere; tolerate both async and sync.
    pattern = re.compile(r'@app\.(get|post|put|delete|patch)\("([^"]+)"[^\n]*\)\s*\n(?:async\s+)?def\s+([A-Za-z0-9_]+)', re.M)
    for method, path, fn in pattern.findall(source):
        rows.append((method.upper(), path, fn, topic_for_path(path)))
    return rows


def audit_doc() -> str:
    rows = route_rows()
    lines = [
        "# ArrNexus Documentation Audit",
        "",
        "This file is generated from `app/main.py` routes and the same `app/help_catalog.py` mapping used by the contextual Help button.",
        "",
        f"- Application routes/actions audited: **{len(rows)}**",
        f"- Help topics: **{len(TOPICS)}**",
        "- Primary private pages receive a contextual Help link from the application shell.",
        "- Public/auth/bootstrap pages are documented in the Help Centre and user guide even when they do not use the private shell.",
        "",
        "| Method | Route | Handler | Help topic |",
        "| --- | --- | --- | --- |",
    ]
    for method, path, fn, topic in rows:
        lines.append(f"| {method} | `{path}` | `{fn}` | `{topic}` |")
    lines += [
        "",
        "## Release rule",
        "",
        "A new primary page or materially new user-facing workflow must add or update Help coverage in `app/help_catalog.py`. `validate.py` checks this mapping so documentation is part of the release gate rather than a follow-up task.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "USER_GUIDE.md").write_text(user_guide(), encoding="utf-8")
    (docs / "DOCUMENTATION_AUDIT.md").write_text(audit_doc(), encoding="utf-8")
    print(f"Generated {docs / 'USER_GUIDE.md'}")
    print(f"Generated {docs / 'DOCUMENTATION_AUDIT.md'}")


if __name__ == "__main__":
    main()
