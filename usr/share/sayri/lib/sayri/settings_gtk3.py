"""Settings entry point for Sayri.

Redirects to the modern GTK4 Cajita Settings Window.
"""

from __future__ import annotations

import sys
from sayri.settings_cajita import main

if __name__ == "__main__":
    sys.exit(main())
