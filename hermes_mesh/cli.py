"""Legacy compatibility shim for the Capability Mesh CLI."""

from capability_mesh.cli import *  # noqa: F401,F403

if __name__ == "__main__":  # pragma: no cover
    from capability_mesh.cli import main

    raise SystemExit(main())
