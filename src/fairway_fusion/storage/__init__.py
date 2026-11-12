"""Storage package: everything Fairway Fusion saves to disk.

Other modules should import from here, not from storage.database:

    from fairway_fusion.storage import ShotStore, StorageError

That keeps the server independent of how this folder is organized inside.
If database.py is ever split or renamed, only this file needs to change.
"""

# Re-export the public pieces of database.py one level up.
# ShotStore is the class the server uses. The errors are exported so
# the server can catch them by name and send the right response to the UI.
# Internal helpers (names starting with "_") are intentionally not exported.
from fairway_fusion.storage.database import (
    CaptureExistsError,
    CaptureRecords,
    NotFoundError,
    SchemaVersionError,
    SessionStateError,
    ShotStore,
    StorageError,
)

# The official list of public names. It controls what
# "from fairway_fusion.storage import *" brings in, and tells editors and
# teammates which names are the intended interface.
__all__ = [
    "ShotStore",
    "StorageError",
    "SessionStateError",
    "NotFoundError",
    "SchemaVersionError",
    "CaptureExistsError",
    "CaptureRecords",
]