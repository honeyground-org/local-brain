"""Storage backends — ★one file each★ (§brain/stores.py "One file per backend" · docs/STORAGE.md).

A module here that defines `BACKEND = stores.Backend(...)` is a choice everywhere: `brain stores --set`,
its option flags, `brain stores --docker`, `brain install --vector-store/--graph-store`, the status
screen and the contract check. A module whose name starts with `_` is a helper several backends share
(a query language, a wire protocol), not a backend.
"""
