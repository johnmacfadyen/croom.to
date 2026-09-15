"""
Core Croom components.

This module contains the main agent, configuration, and service management.
"""

from croom.core.config import Config, load_config
from croom.core.service import ServiceManager

__all__ = [
    "CroomAgent",
    "Config",
    "load_config",
    "ServiceManager",
]


def __getattr__(name):
    # Preserve public imports without preloading the python -m entry point.
    if name == "CroomAgent":
        from croom.core.agent import CroomAgent
        globals()[name] = CroomAgent
        return CroomAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
