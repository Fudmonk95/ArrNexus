# v12 recovery engine

## Ownership

### NeutArr / Swaparr

When NeutArr coexistence is enabled:

- NeutArr owns periodic missing-media search cadence.
- Swaparr can own stalled/no-progress download replacement.
- ArrNexus does not run a competing automatic missing-search loop.

### ArrNexus Missing Media Orchestrator

ArrNexus:

- inventories missing movies/episodes/seasons/albums;
- tracks search attempts and cooldown;
- suppresses duplicate searches when Arr/Zurg activity already exists;
- offers controlled manual search actions while NeutArr coexistence is enabled; and
- can become the automatic search scheduler when coexistence is disabled.

### ArrNexus Queue Janitor

The Janitor owns queue decisions that need more context than a simple search scheduler:

1. classify the Arr queue warning/failure;
2. preserve a potentially valid ID-matched download by trying explicit ManualImport first;
3. never force-import invalid season/episode/album mappings;
4. inspect uncertain samples with ffprobe when a readable path is available;
5. remove and blocklist confirmed bad releases;
6. request another Arr search under the hard retry limit; and
7. stop at Needs Attention instead of looping forever.

## Retry defaults

```text
Failed release limit: 3
Cooldown:             6 hours when attention state is reached
Queue scan interval:  30 seconds
Warning grace:         5 minutes
```

The Missing Media scheduler has separate controls for searches-without-acquisition, delay, batch size, active acquisitions and daily search volume.

## Safety

- Queue Janitor and Missing Media automation are disabled by default.
- Dry-run is enabled by default.
- Zurg and its cache are read-only from ArrNexus.
- Raw queue payloads are kept inside the action engine and are not returned by the Queue Janitor API/UI state.
- Manual import is only submitted when the candidate contains the required Arr IDs and does not have dangerous rejections.
- Janitor action history remains after a retry counter is reset.
