#!/usr/bin/env python3
"""Launch PermGuard from the project root."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from permguard.main import main
main()
