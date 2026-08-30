#!/usr/bin/env python3
"""Install-safe ArrNexus v10.4.1 validator.

The native updater runs only the current deterministic layer. Full retained
regression certification is performed by validate_v1041.py before packaging.
"""
import os
os.environ["ARRNEXUS_VALIDATE_V1041_ONLY"] = "1"
from validate_v1041 import main
if __name__ == "__main__":
    raise SystemExit(main())
