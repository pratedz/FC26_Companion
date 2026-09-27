"""Windows specifics behind clean ports. Nothing above this layer lives here.

This package is the *only* place in v2 that touches the registry, the Win32
process APIs, ``ReadDirectoryChangesW`` or DPAPI. Everything above it talks to
ports (``core/ports.py``) or to the small, boring functions exported here, so
the rest of the app stays testable on a machine with no game, no Live Editor
and no registry key.

Modules:

``registry``    ``HKLM\\SOFTWARE\\Live Editor\\FC 26`` — Data Dir / Install Dir /
                Mods Dir, plus the one clearly-labelled legacy fallback.
``procs``       ``is_pid_alive`` (the canonical liveness probe), process
                enumeration via Toolhelp32, FC26.exe / LE Launcher lookup.
``fswatch``     directory watching: ``ReadDirectoryChangesW`` when it works,
                a polling watcher when it does not. Same API either way.
``secrets``     DPAPI ``CryptProtectData``/``CryptUnprotectData``, user-scoped,
                plus the one-shot migration off plaintext ``xai_credentials.json``.
``le_install``  (owned elsewhere) LE integrity + worker install.

**This ``__init__`` imports nothing eagerly.** Importing ``companion.platform``
must never load ctypes, winreg or crypt32 as a side effect: ``core`` and
``domain`` are import-clean by construction, and the platform package has to be
just as cheap for the code paths that never reach Windows-specific behaviour
(unit tests, ``--help``, the CLI's version command). Import the submodule you
actually need::

    from companion.platform import registry
    data_dir = registry.le_data_dir()

Note the package name shadows the stdlib ``platform`` module *only* for
relative imports inside ``companion``; absolute ``import platform`` anywhere
still resolves to the stdlib, because this package is never top-level.
"""

from __future__ import annotations

__all__ = ["registry", "procs", "fswatch", "secrets", "le_install"]
