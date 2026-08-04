"""Test-wide setup.

``XDG_CONFIG_HOME`` is redirected before anything imports ground_control, because
reading config is not read-only: ``load_colors()`` writes the config file as a
side effect, and the app writes themes and logs next to it. Without this, running
the suite would edit the developer's real ``~/.config/ground-control``.

It is set here at import time rather than in a fixture: conftest is imported
before the test modules, while a fixture would run after they have already
imported ground_control and captured the config paths.
"""
import os
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="gc-test-config-")
# Keep any accidental cache/state writes out of the real home too.
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(
    os.environ["XDG_CONFIG_HOME"], "cache"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(
    os.environ["XDG_CONFIG_HOME"], "data"))
