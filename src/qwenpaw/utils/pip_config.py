# -*- coding: utf-8 -*-
"""Utility to configure pip source for intranet environments."""
from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_INDEX_URL = "http://maven.paic.com.cn/repository/pypi/simple"
DEFAULT_TRUSTED_HOST = "maven.paic.com.cn"


def _get_pip_config_path() -> Path:
    """Return the platform-specific pip configuration file path."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            appdata = str(Path.home() / "AppData" / "Roaming")
        path = Path(appdata) / "pip" / "pip.ini"
    else:
        # Linux / macOS: prefer XDG config, fallback to legacy ~/.pip
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            path = Path(xdg_config) / "pip" / "pip.conf"
        else:
            path = Path.home() / ".config" / "pip" / "pip.conf"
    return path


def _legacy_pip_config_path() -> Path | None:
    """Return the legacy pip config path if it exists (Unix only)."""
    system = platform.system()
    if system == "Windows":
        return None
    legacy = Path.home() / ".pip" / "pip.conf"
    return legacy if legacy.exists() else None


class PipConfigAlreadyExistsError(RuntimeError):
    """Raised when a different pip config already exists."""


class PipConfigAlreadySetError(RuntimeError):
    """Raised when the requested pip config is already in place."""


def configure_pip_source(
    index_url: str = DEFAULT_INDEX_URL,
    trusted_host: str = DEFAULT_TRUSTED_HOST,
) -> dict:
    """Write pip configuration file with the given index URL and trusted host.

    Returns:
        dict with ``path`` (str), ``updated`` (bool), and ``previous`` (str | None).

    Raises:
        PipConfigAlreadySetError: if the exact same config is already present.
        PipConfigAlreadyExistsError: if a *different* pip config already exists.
    """
    path = _get_pip_config_path()
    previous: str | None = None
    if path.exists():
        try:
            previous = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read existing pip config: %s", exc)

        if previous:
            if index_url in previous and trusted_host in previous:
                raise PipConfigAlreadySetError(
                    f"Pip source is already configured in {path}"
                )
            if "index-url" in previous or "extra-index-url" in previous:
                raise PipConfigAlreadyExistsError(
                    f"Another pip config already exists in {path}. "
                    "Please back it up and try again, or merge manually."
                )

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    content = f"""[global]
index-url = {index_url}
trusted-host = {trusted_host}
"""
    path.write_text(content, encoding="utf-8")
    logger.info("Pip config written to %s", path)

    return {
        "path": str(path),
        "updated": True,
        "previous": previous,
    }


def get_pip_source_status() -> dict:
    """Check whether pip source is already configured.

    Returns:
        dict with ``configured`` (bool), ``path`` (str | None), and ``content`` (str | None).
    """
    path = _get_pip_config_path()
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            configured = DEFAULT_INDEX_URL in content or "index-url" in content
            return {
                "configured": configured,
                "path": str(path),
                "content": content,
            }
        except OSError as exc:
            logger.warning("Failed to read pip config: %s", exc)

    legacy = _legacy_pip_config_path()
    if legacy:
        try:
            content = legacy.read_text(encoding="utf-8")
            configured = DEFAULT_INDEX_URL in content or "index-url" in content
            return {
                "configured": configured,
                "path": str(legacy),
                "content": content,
            }
        except OSError as exc:
            logger.warning("Failed to read legacy pip config: %s", exc)

    return {
        "configured": False,
        "path": None,
        "content": None,
    }
