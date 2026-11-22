"""Allow ``python -m icsel``."""

import sys

from .cli import main

sys.exit(main())
