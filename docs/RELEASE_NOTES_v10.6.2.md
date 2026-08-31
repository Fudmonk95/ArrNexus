# ArrNexus v10.6.2-beta - Real-Debrid Single-File Resolver Hotfix

## Why this hotfix exists

Live Queen's Nose testing exposed one remaining direct-original resolver mismatch after v10.6.1.

ArrNexus correctly stopped trusting the Decypharr-mounted RAR after provider CRC errors, but the Real-Debrid resolver reported:

`Exact Real-Debrid torrent candidate(s) did not contain a unique selected file matching 'season-4_202405.rar'`

That proved the correct Real-Debrid torrent candidate was being found, but its selected-file metadata did not use the exact path representation ArrNexus expected.

The known-good direct Queen's Nose archive remains the reference field case:

- direct archive size: 3,544,189,222 bytes;
- direct SHA-256: `5cd1731e57b5283abb55c651bed254f3c54e15ea562f1ec56a4f869eb47c1e61`;
- Windows 7-Zip whole archive: pass;
- Windows 7-Zip `Season 1.mp4`: pass;
- Decypharr-mounted size: 3,544,186,880 bytes;
- Decypharr-mounted SHA-256: `2eae3f0bf4be3098b8c51a070bf056748de877290a15e545229824cf7fa95d55`.

The provider mount is therefore not an authoritative archive source after a CRC/integrity anomaly.

## Real-Debrid resolver improvements

v10.6.2 keeps exact matching but handles safe single-file representations that Real-Debrid can return differently from Decypharr:

- `season-4_202405` and `season-4_202405.rar` are treated as exact archive-name equivalents;
- a selected RD file whose basename has the archive extension stripped can resolve uniquely;
- a single-file RD response with a missing or normalised `selected` flag can resolve when there is exactly one file and one generated link;
- an exact archive torrent with exactly one file and one link can resolve even if RD rewrites the internal selected-file path;
- an exact single-file torrent with one link but omitted file rows can use the torrent byte count as authoritative metadata.

These are not fuzzy title matches. Multi-file ambiguity, multiple matching torrents and multiple possible links remain hard failures.

## Better failure diagnostics

If direct resolution still fails, the error now includes the candidate torrent identity, selected file paths and generated link count where available. This makes new Real-Debrid metadata representations visible instead of collapsing them into a generic resolution failure.

## Provider archive safety retained

v10.6.2 retains the v10.6.1 authoritative-source rule:

1. provider CRC/EIO is an integrity anomaly, not proof of damage;
2. if Real-Debrid is connected, ArrNexus must resolve the exact direct original;
3. the original is downloaded over HTTPS to `/mnt/debrid/arrnexus-extracted/.arrnexus-staging`;
4. expected direct byte length is enforced;
5. local 7-Zip verification is authoritative;
6. only damage reproduced against the direct original may be called confirmed archive damage;
7. direct-resolution failure never falls back to the Decypharr-mounted RAR.

Provider media and archives remain untouched.

## Retained v10.6 functionality

- Sonarr Archive Rescue and Radarr Archive Rescue;
- dedicated Sonarr Rescue and Radarr Rescue pages;
- reviewed Prowlarr/debrid candidate handoff;
- Real-Debrid cache checks;
- updater/version-badge modal fixes;
- v10.5.1 resilient extraction/I/O handling;
- Language Checks master bypass;
- Import Jobs review controls and dismissible job notifications;
- season-aware TV source selection and recovered-media import handling.

## Validation

The v10.6.2 validator covers the live resolver gap directly:

- exact `.rar` selected-file path;
- selected file with `.rar` stripped;
- exact single-file torrent with a rewritten internal RD path;
- exact one-file/one-link response with no usable selection flag;
- exact one-link torrent with omitted file rows and authoritative torrent bytes;
- ambiguous two-file/two-link candidate remains rejected;
- provider-mounted copy is never invoked when direct resolution fails after provider CRC;
- authoritative direct-original size and verification behavior from v10.6.1 is retained.

Retained validators for v10.6.1, v10.6, v10.5.1, v10.5 and v10.4.4 also pass against the v10.6.2 tree.
