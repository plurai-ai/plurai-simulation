"""UTF-8 between the plugin's own processes on native Windows, and nothing anywhere else.

On Windows a pipe or a redirected file gets the ANSI code page (cp1252): a script that prints `→` dies
with `UnicodeEncodeError: 'charmap'`, and a parent decoding a child's UTF-8 output as cp1252 reads
mojibake or raises on 0x81/0x8D/0x8F/0x90/0x9D. The two halves are gated on the same predicate:

- :func:`utf8_stdio`, called first at every plugin entry point, makes the script's standard streams UTF-8;
- :func:`utf8_child_text`, spread into a ``subprocess`` call whose child is one of ours, decodes it as UTF-8.

Off Windows both are no-ops, so macOS and Linux behave as before under any locale. Nothing here sets an
environment variable: the customer's agent never inherits it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

#: Text-mode ``subprocess`` arguments for a child that writes UTF-8 (it called :func:`utf8_stdio`).
_UTF8_TEXT: dict[str, str] = {'encoding': 'utf-8', 'errors': 'replace'}


#: Native Windows, for ``utf8_child_text``; a test sets ``_stdio.WINDOWS``. ``utf8_stdio`` reads its own module's flag.
WINDOWS = os.name == 'nt'


def _hooks_utf8_stdio():  # noqa: ANN202 - the hooks' function, whatever its signature
    """``hooks/_utf8_stdio.utf8_stdio``, loaded from its file: one implementation for hooks and scripts, and no
    ``sys.path`` entry that could shadow a script's own module."""
    module = sys.modules.get('_utf8_stdio')
    if module is None:
        spec = importlib.util.spec_from_file_location('_utf8_stdio', Path(__file__).resolve().parent.parent / 'hooks' / '_utf8_stdio.py')
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules['_utf8_stdio'] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.utf8_stdio


#: On Windows, each standard stream that is not UTF-8 becomes UTF-8 with ``errors='replace'`` (stdin too: a parent of
#: ours writes it UTF-8, :func:`utf8_child_text`). Off Windows a no-op.
utf8_stdio = _hooks_utf8_stdio()


def utf8_child_text() -> dict[str, str]:
    """``subprocess`` keyword arguments for reading (and writing ``input=`` to) a child of ours:
    ``encoding='utf-8', errors='replace'`` on Windows, ``{}`` elsewhere. The call keeps its own
    ``text=True``, so off Windows it is exactly the call it was."""
    return dict(_UTF8_TEXT) if WINDOWS else {}
