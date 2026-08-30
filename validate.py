#!/usr/bin/env python3
"""Install-safe ArrNexus v10.3 validator.

The native updater executes this entry point inside the staged release. Legacy
v7-v10.2 validators are still retained and are run separately by the release
packaging gate, but some historical FastAPI TestClient suites can leave worker
threads alive when chained from one long-lived Python parent. Running only the
current release layer here keeps native updates deterministic while preserving
all historical validators in the package for release certification.
"""
import os

os.environ["ARRNEXUS_VALIDATE_V103_ONLY"] = "1"

from validate_v103 import main

if __name__ == "__main__":
    raise SystemExit(main())
