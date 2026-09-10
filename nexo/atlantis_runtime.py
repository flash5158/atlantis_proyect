"""Cross-platform launcher for the new authenticated Atlantis hub.

The desktop extension starts this module as a local process.  It deliberately
keeps the runtime separate from the legacy demo server so upgrades cannot
silently change a running workspace.  The first stdout line is a small JSON
handshake consumed by the extension; all diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Atlantis collaboration runtime")
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--token", default=None, help=argparse.SUPPRESS)
    p.add_argument("--name", default="Daniel")
    p.add_argument("--no-ready", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        print(f"workspace does not exist: {workspace}", file=sys.stderr)
        return 2
    data_dir = (args.data_dir or workspace / ".atlantis").expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    # Keep the owner credential stable across restarts.  The hub stores only a
    # hash, so generating a fresh token on every launch would make the previous
    # desktop connection unusable while the ready handshake advertised a token
    # that the existing database could not accept.
    token_file = data_dir / "owner.token"
    token = args.token or os.environ.get("ATLANTIS_OWNER_TOKEN")
    if not token and token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        token = secrets.token_urlsafe(32)
    if not token_file.exists() or token_file.read_text(encoding="utf-8").strip() != token:
        token_file.write_text(f"{token}\n", encoding="utf-8")
        try:
            token_file.chmod(0o600)
        except OSError:
            pass

    # Import after argument validation so ``--help`` and bad paths stay cheap.
    from atlantis_hub import create_app
    import uvicorn

    app = create_app(workspace=workspace, data_dir=data_dir, owner_token=token, owner_name=args.name)
    if not args.no_ready:
        # This stream is read only by the parent desktop process; never log it
        # to a shared terminal or include it in application diagnostics.
        print(
            json.dumps(
                {"type": "ready", "server": f"http://{args.host}:{args.port}", "name": args.name, "token": token},
                separators=(",", ":"),
            ),
            flush=True,
        )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
