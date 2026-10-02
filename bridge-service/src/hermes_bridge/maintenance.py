"""Offline maintenance commands for bridge-owned state."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import MappingRepository


async def _reset_openwebui_links(database_path: Path) -> int:
    database = await Database.open(database_path)
    try:
        return await MappingRepository(database).reset_openwebui_links()
    finally:
        await database.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m hermes_bridge.maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-openwebui-links")
    reset.add_argument("--database", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "reset-openwebui-links":
        print(asyncio.run(_reset_openwebui_links(arguments.database)))
        return 0
    raise AssertionError(f"unsupported command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
