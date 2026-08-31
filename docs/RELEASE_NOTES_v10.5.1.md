# ArrNexus v10.5.1-beta - Recovery I/O & Review UX Hotfix

## Why this hotfix exists

Live v10.5 testing exposed two separate issues that needed to be fixed together.

First, Decypharr/Real-Debrid backed RAR files can throw Linux `EIO` (`[Errno 5] Input/output error`) while ArrNexus is copying an archive to local recovery storage. v10.5.0 used a normal sequential read, so one virtual-filesystem read error could abort local staging before the CRC re-test ever reached the Toshiba recovery disk.

Second, Language Guard and Import Jobs still had frustrating review-state UX. A source such as Bernard's Watch could remain stuck in a stale `Re-check language` workflow after the user chose to bypass Language Guard, manual-review jobs did not expose a direct review queue, and bottom-right job notifications could not be dismissed without interacting with the job itself.

## Resilient local RAR staging

The local staging copier now retries the exact failed offset, reopens the provider-backed file handle, reduces the requested read range down to 64 KiB when necessary, then scales back up after healthy reads. A range is never skipped, padded or fabricated. If it remains unreadable after repeated retries, staging fails safely and reports the failing byte offset.

If mounted-file retries still cannot read the source and ArrNexus has a Real-Debrid credential, it next resolves the backing torrent by exact source-pack name and the exact selected RAR file, calls Real-Debrid's unrestrict API, and stages that file over resumable HTTPS. The signed download URL stays in memory and is never logged. Ambiguous torrent/file matches are refused.

ArrNexus now distinguishes three outcomes:

- provider member fails but a complete local copy passes: virtual/provider read-path issue;
- complete local copy reproduces the CRC failure: genuine CRC/archive damage;
- provider bytes cannot be copied completely because repeated I/O reads fail: provider I/O failure, not a CRC verdict.

The incomplete local staging directory is removed after failure. Provider media is never modified or deleted.

## Extraction safety

When a member only becomes verified after local staging, extraction uses that exact locally verified archive copy rather than returning to the unreliable provider mount. Existing verified provider members can continue to use the normal source. Cancellation still removes only operation-local partial output and never deletes provider archives or already-committed recovered episodes.

## Queen's Nose source selection

The v10.5 season-aware selector is retained and regression-tested against the live layout. Six valid recovered Season 1 episode files outrank an inferior combined `Season 1.mp4` source, including a combined source that is CRC-failed. The broken 443 MB combined member therefore does not need to be rescued to import a Season 1 already fully covered by preferred individual episodes.

## Language Checks OFF now clears stale blockers

The master Language Checks toggle is now stronger and more explicit.

When Language Checks are switched OFF, ArrNexus:

- keeps previous ffprobe scan results and exact-source overrides;
- does not delete Language Guard history;
- changes stale language-only workflow states (`language_review`, `language_rejected`, `language_issue`) back to normal waiting state;
- does not launch a new ffprobe re-check from Item Review;
- bypasses queued language-scan items if the master toggle is turned OFF after the job was created;
- prevents stale `Re-check language` state from continuing to block Inbox/import behaviour.

Turning checks back ON restores normal future Language Guard evaluation without resurrecting an obsolete workflow block.

## Import Job manual-review controls

Import Jobs now expose review actions instead of only reporting `manual review required`.

- Jobs with review items show a visible **Review all** action.
- Individual review items show **Review** / **Open source** controls.
- The manual-review queue explains why each source needs attention.
- Language-review items can be explicitly **Confirm English** from the job review queue, creating the same fingerprint-bound administrator override used by Item Review.
- TV-recovery review items remain non-destructive and direct the user back to source/season review rather than pretending the source imported successfully.

## Dismissible job notifications

The bottom-right job notification card now has an `x` dismiss button. Dismissing the notification:

- does not cancel the job;
- does not remove job history;
- does not stop the worker;
- prevents the active-job poller from immediately recreating the same notification during the current browser session.

The full job remains available under **Import Jobs**.
