#!/usr/bin/env python3
"""Default Sayri Orb + Cajita UI Plugin Entrypoint."""
import sys
import os

# Ensure sayri lib is available in sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
sayri_lib = os.path.normpath(os.path.join(script_dir, "..", "..", "lib"))
if os.path.isdir(sayri_lib) and sayri_lib not in sys.path:
    sys.path.insert(0, sayri_lib)

from sayri.app import main

if __name__ == "__main__":
    sys.exit(main())
