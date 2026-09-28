"""Central place for where the app is allowed to put temporary files.

The whole project lives on the D: drive, and per project convention every
temp file this app creates (VMAF log files, in-progress encodes, etc.) must
stay on D: as well — never the system default temp dir (usually C:\\Users\\...\\
AppData\\Local\\Temp on Windows).
"""
from __future__ import annotations

import os
from pathlib import Path

TEMP_ROOT_ENV_VAR = "VIDEO_BATCH_TEMP_ROOT"
DEFAULT_TEMP_ROOT = Path("D:/VideoBatchProcessorTemp")

CONFIG_DIR_ENV_VAR = "VIDEO_BATCH_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path("D:/VideoBatchProcessorConfig")


def get_temp_root() -> Path:
    """The directory all app temp files/dirs are created under.

    Override with the VIDEO_BATCH_TEMP_ROOT env var (still expected to point
    at a D: path); otherwise defaults to DEFAULT_TEMP_ROOT. The directory is
    created if it doesn't exist yet.
    """
    root = Path(os.environ.get(TEMP_ROOT_ENV_VAR, DEFAULT_TEMP_ROOT))
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_config_dir() -> Path:
    """Where persisted app config (settings.json) is stored.

    Kept separate from get_temp_root(): temp files are safe to delete at any
    time, but config should survive a temp-dir cleanup. Override with the
    VIDEO_BATCH_CONFIG_DIR env var (still expected to point at a D: path).
    """
    root = Path(os.environ.get(CONFIG_DIR_ENV_VAR, DEFAULT_CONFIG_DIR))
    root.mkdir(parents=True, exist_ok=True)
    return root
