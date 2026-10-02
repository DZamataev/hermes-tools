from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import MappingRepository


async def test_reset_openwebui_links_cli_reports_count_and_detaches_mirror(tmp_path):
    database_path = tmp_path / "bridge.sqlite3"
    database = await Database.open(database_path)
    mappings = MappingRepository(database)
    mapping = await mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await mappings.attach_chat(mapping.lineage_key, "chat-1")
    await database.close()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_bridge.maintenance",
            "reset-openwebui-links",
            "--database",
            str(database_path),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    database = await Database.open(database_path)
    restored = await MappingRepository(database).by_lineage_key(mapping.lineage_key)
    await database.close()
    assert restored is not None
    assert restored.openwebui_chat_id is None
