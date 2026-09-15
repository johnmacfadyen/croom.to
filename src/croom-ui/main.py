#!/usr/bin/env python3
"""Compatibility launcher for the packaged, agent-backed room UI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from croom_ui.main import main

if __name__ == "__main__":
    main()
