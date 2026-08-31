# ArrNexus v10.5.0-beta Validation

`python3 validate.py` runs the deterministic v10.5 current-layer validator. Release certification also runs historical compatibility validators, compiles all Python, validates Jinja templates/JavaScript, migrates a legacy SQLite schema, exercises Language Checks ON/OFF, grouped TV source selection, recovered-link indexing, cancellation/job-history operations, CRC local staging, `.arrnexus-originals` exclusion and clean FastAPI route rendering.

Historical chain retained: v7 → v8 → v9 → v9.1 → v9.2 → v9.3 → v9.4 → v10 → v10.1 → v10.2 → v10.3 → v10.4 → v10.4.1 → v10.4.2 → v10.4.3 → v10.4.4 → v10.5.

A real temporary Uvicorn process is also used for `/api/health`, `/`, `/setup`, Settings and Import Jobs. DMM Inbox itself depends on the DUMB/Radarr mount namespace (`pid: host` + `/proc` visibility); the isolated validator renders and exercises the Inbox route with a clean mocked inventory so certification does not weaken or fake that production namespace requirement.
