"""
Entry point for WallDance.

The runtime is defined in `app.WallDanceApp`; this file simply wires the CLI
to the orchestrator. Run via `./run.sh`.
"""

from app import main


if __name__ == "__main__":
    main()
