"""Command-line entry point: ``a2a-bridge`` (or ``python -m a2a_bridge``)."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from a2a_bridge import __version__
from a2a_bridge.app import create_app
from a2a_bridge.config import ConfigError, Settings, load_dotenv


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="a2a-bridge",
        description="Serve a Microsoft Copilot Studio agent over the A2A protocol.",
    )
    parser.add_argument("--env-file", default=".env", help="dotenv file to load (default: .env)")
    parser.add_argument("--host", help="bind address (overrides BRIDGE_HOST)")
    parser.add_argument("--port", type=int, help="bind port (overrides BRIDGE_PORT)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    load_dotenv(args.env_file)
    # CLI flags win, and the port must also feed the default public URL in the agent card.
    if args.host:
        os.environ["BRIDGE_HOST"] = args.host
    if args.port:
        os.environ["BRIDGE_PORT"] = str(args.port)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        sys.exit(f"Configuration error: {exc}")

    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Direct Line is polled several times per turn; keep per-request logs out of INFO.
    if settings.log_level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
