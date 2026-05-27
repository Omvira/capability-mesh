"""Compatibility wrapper for the Capability Mesh CLI."""

from hermes_mesh.cli import *  # noqa: F401,F403

if __name__ == "__main__":  # pragma: no cover
    from hermes_mesh.cli import main

    raise SystemExit(main())
