# Bridge service

This is the OpenAI-compatible bridge between OpenWebUI and the Hermes user
plugin.

It owns durable OpenWebUI-chat to Hermes-session mappings, idempotency,
queueing, event translation, and reconciliation. No bridge code should write
directly to either application's SQLite database.

Runtime endpoints are `/health/live`, `/health/ready`, `/health/status`, the
bridge-secret protected `/v1/models` and `/v1/chat/completions`, and the
HMAC-authenticated `/connector` WebSocket. See
`../docs/openwebui-bridge-setup.md` for configuration and acceptance checks.
