# ArrNexus v10.4.0-beta — Archived Media Recovery, Identity Resolution & Language Safety

v10.4 is built from the certified v10.3 release and focuses on the edge cases found during real DMM/Real-Debrid testing.

## Highlights

### Archived Media Recovery

RAR-packed provider media no longer silently disappears because the normal DMM scanner cannot see a video file. ArrNexus now scans `__all__` separately for first-volume RAR and multipart RAR sets.

The workflow is deliberately review-first:

1. Scan for archives.
2. Inspect contents without extraction.
3. Review volume count, media classification, estimated expanded size and free recovery storage.
4. Resolve ambiguous identity when required.
5. Explicitly select Extract.
6. Revalidate the source fingerprint/safety plan.
7. Extract into the configured DUMB-visible recovery root.
8. Open the recovered source in Item Review for Language Guard and Sonarr/Radarr import.

Safety protections block path traversal, archive-created symlinks, stale previews, passworded archives, nested-only archives, over-limit expansion and insufficient free-space margin. Original provider archives are retained.

### TMDb media identity & naming

Generic provider names such as `season-4_202405.rar` are not guessed. Administrators can configure a TMDb API key, search the correct TV/movie identity, and bind the chosen identity to that exact source fingerprint.

The resolved identity drives actual Arr matching and canonical naming. Item Review also adds a **Review naming & import** dialog where title/type/year can be corrected before a job begins.

### Language Guard false-rejection fix

Undefined or missing audio-language tags (`und`, blank, unknown/mixed metadata) are uncertainty, not proof of foreign-language media. These sources become **Manual review**, stay non-destructive, and can be manually marked English by an administrator for the exact fingerprint when the content is known to be English.

Explicit non-English tracks with no English track remain a confirmed rejection.

### Series-first DMM TV grouping

Separate season/source folders for the same Sonarr series now group under one show card. Season-pack release years no longer split one series into multiple DMM cards.

### Container dependency

Archived Media Recovery uses 7zip/unrar. v10.4 updates the Dockerfile accordingly. The native updater can stage the application release, but an existing image that does not already contain a supported extractor must be rebuilt/redeployed before RAR inspection/extraction is available.

## Validation target

The v10.4 release gate retains v7, v8, v9, v9.1, v9.2, v9.3, v9.4, v10, v10.1, v10.2 and v10.3 tests and adds v10.4-specific checks for unknown-language safety, manual English override plumbing, series-first grouping, RAR-listing/path safety, ambiguous identity detection, TMDb/canonical naming, archive UI routes, templates, fresh SQLite setup and updater version ordering.
