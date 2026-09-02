# ArrNexus v10.8.1-beta

## Recovery/import route hotfix

Recovery & Import now treats `auto` as a routing request rather than a literal destination. Once recovered media has been re-indexed and its final movie/TV identity is known, ArrNexus resolves the recommended concrete Radarr/Sonarr destination, validates it against configured roots, stores it in the job, records it in the import plan and uses that same route for any retry.

This fixes the 95% final-import failure `Invalid tv destination: auto` / `Invalid movie destination: auto`. The shared `import_one` path also resolves `auto`, so normal imports receive the same protection.

A second latent v10.8 import bug is fixed at the same boundary: the advisory Language Guard policy is now loaded for result reporting, preventing a post-link `NameError` after a successful import. No automatic language ffprobe scan has been reintroduced.

## Retry and interrupted-job recovery

- Retry Stage accepts recovery/import jobs represented by the compatible terminal states `failed`, `error` and `complete_with_errors`.
- Retry clears stale cancellation state and resumes from the saved stage/current stage while retaining completed stage results.
- Startup reconciles persisted `queued`, `running` and `cancelling` jobs before new workers launch. Because workers are in-process, those rows cannot have a live owner after restart.
- Interrupted Recovery & Import jobs become safely retryable from their last stage; jobs that were already cancelling become terminal `cancelled`.

## Cancellation reliability

Cancellable POSIX tools now run in their own process groups. Cancellation and timeout handling signal the whole child tree instead of only the immediate process, using a bounded TERM grace period followed by KILL. ArrNexus also finalises a still-`cancelling` job after a bounded UI/job grace period so stale cancellation cannot remain forever.

## Preserved behaviour

- v10.8 Media Automation hub and non-destructive collection sync remain intact.
- Language Guard stays advisory-only for automatic imports.
- v10.7 provider verification, exact direct Real-Debrid fallback, verified extraction, TV analysis/splitting, persistent split state and final Arr import remain intact.
- Provider media and already-completed verified recovery work remain retained during cancellation/retry.

## Validation

The v10.8.1 validator covers concrete `auto` route resolution, interrupted-job reconciliation, retry-state compatibility, process-tree cancellation contracts, version/route/template smoke tests and retained v10.8/v10.7 behaviour. Release certification also runs the retained validator chain and Python compilation.
