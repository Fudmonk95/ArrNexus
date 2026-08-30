# ArrNexus v10.4.1-beta — Selective RAR Recovery Hotfix

v10.4.1 is a focused field hotfix for Archived Media Recovery. It was driven by a real RAR where 7-Zip could enumerate 17 Queen's Nose video members while the archive itself returned `Unexpected end of archive`; 16 members tested clean and one (`Season 1.mp4`) failed CRC.

## What changed

### Member-level verification

RAR health and media-member health are now separate concepts. A non-zero archive exit code no longer automatically discards a usable catalogue. ArrNexus can present the video members, run an explicit background verification job, and classify each member as:

- **Verified** — eligible for recovery.
- **Failed** — CRC/data failure; recovery checkbox disabled.
- **Unverified** — never trusted automatically.

### Media-only extraction

The normal recovery workflow passes only administrator-selected, independently verified video member paths to 7-Zip. It does not extract torrent padding, XML metadata, SQLite metadata, thumbnails/artwork, nested archives or unrelated files.

After extraction every candidate must:

1. remain inside the staging root;
2. be a regular non-symlink video file;
3. match the archive-listed member size when that size is available;
4. pass `ffprobe`;
5. contain a real video stream.

Only then is the file committed to the persistent recovered-media source root. The original provider RAR remains untouched.

### Better inspection UX

- Torrent padding is hidden and counted rather than dumped into the contents table.
- Support files are counted as ignored.
- Useful partial listings are cached against the exact archive fingerprint.
- Archive warnings/errors are displayed as concise diagnostics.
- Integrity verification is a background job with a return link to the same archive.
- The settings label now says **Recovered media source root** and explicitly warns not to use the final library path.

## Upgrade

This is an application-code hotfix on top of the v10.4 container image. If you already rebuilt the v10.4 Docker image and `7z` is available, no additional system package is introduced by v10.4.1.

## Safety

No archive is extracted automatically. Failed/unverified media cannot be selected. The source fingerprint is rechecked before both verification and recovery, storage limits remain enforced, path traversal/symlink protections remain active, and provider-source deletion is not part of this workflow.
