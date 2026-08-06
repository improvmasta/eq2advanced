"""Class metrics — the entries in the `classstats` registry.

Importing this package IS the registration: each module below decorates its
functions with `classstats.register(...)` at import time, which is why the
router imports it for its side effect. One module per class keeps the diff for
"add a troubador stat" inside one file.
"""

from . import troubador  # noqa: F401  (imported for the registration side effect)

__all__ = ["troubador"]
