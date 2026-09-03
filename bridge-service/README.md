# Bridge service

This directory is reserved for the OpenAI-compatible bridge between OpenWebUI
and the Hermes user plugin.

It will own durable OpenWebUI-chat to Hermes-session mappings, idempotency,
queueing, event translation, and reconciliation. No bridge code should write
directly to either application's SQLite database.

Implementation starts after the bridge protocol design is approved.

