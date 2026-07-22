# Runtime configuration

WorkHub separates fixed process settings from runtime settings.

Compose supplies `WORKHUB_DATA_DIR`, `WORKHUB_STATIC_DIR`, `WORKHUB_HOST`, `WORKHUB_PORT`, the Mock OA URL and demo shared secret. The only expected environment values are secure administrator bootstrap credentials. The Session HMAC secret is generated once and persisted in `app.db`.

Chat, Embedding, Feishu credentials and runtime values are stored in `app.db` and edited under the Web Settings page. Runtime values include Provider/Agent/MCP timeouts, Agent iteration and context limits, confirmation TTL, and Feishu reconnect settings.

Every write uses an optimistic revision, writes an audit event, and never returns a secret. Model factories use the latest saved configuration for later runs. Feishu changes rebuild the long connection; runtime changes update later runs without interrupting work already in progress.

The service remains startable when providers or Feishu are not configured. Configure them after administrator login. The legacy `WORKHUB_CHAT_*`, `WORKHUB_EMBEDDING_*`, and `WORKHUB_FEISHU_*` environment variables are intentionally ignored.
