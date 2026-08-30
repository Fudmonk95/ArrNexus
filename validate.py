#!/usr/bin/env python3
"""Install-safe ArrNexus v10.4 validator.

The native updater runs the current release layer deterministically. Historical
validators remain packaged and are all executed by validate_v104.py during the
release certification gate.
"""
import os
os.environ["ARRNEXUS_VALIDATE_V104_ONLY"] = "1"
from validate_v104 import main
if __name__ == "__main__":
    raise SystemExit(main())
