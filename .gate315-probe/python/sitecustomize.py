"""Temporary probe fixture — gate-20261014-45.

Python's site module imports ``sitecustomize`` from ``sys.path`` at every
interpreter startup. This file is only reachable when the fixture PYTHONPATH
declared in the probe ``mise.toml`` leaked into a Python process — i.e. a
gate-v2 platform step (gate-actions bootstrap python3, PR size preflight
python3, ...) inherited the project env that ``env: false`` on
jdx/mise-action@v3 was supposed to keep out.

On import it writes one boolean marker file into the workspace. Nothing else:
no network, no system mutation, no subprocess. Outside a platform step (no
GITHUB_WORKSPACE) it is inert. The marker file is asserted ABSENT by
scripts/gate-quality.
"""

import os
import pathlib


def _write_marker() -> None:
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        return
    marker = pathlib.Path(workspace) / ".gate315-probe" / "platform-leak.marker"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("platform python inherited fixture PYTHONPATH\n", encoding="utf-8")
    except OSError:
        # Observability only: a write failure must never break the step we are
        # leaking into. The assert on the marker's absence would then pass
        # vacuously, which the marker's own mtime/absence cannot distinguish —
        # accepted limitation of a one-shot fixture.
        pass


_write_marker()
