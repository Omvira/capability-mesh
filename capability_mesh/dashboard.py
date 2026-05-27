"""Compatibility wrapper for Capability Mesh dashboard helpers."""

from hermes_mesh.dashboard import *  # noqa: F401,F403

if __name__ == "__main__":  # pragma: no cover
    from hermes_mesh.dashboard import main

    raise SystemExit(main())
