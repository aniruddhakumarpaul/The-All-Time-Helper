# Database Migrations

SQLite schema changes are applied at startup by `app/schema_migrations.py`. Applied versions are recorded in `schema_migrations`; each migration runs in its own transaction and startup fails if a migration cannot complete.

## Existing Local Databases

- Version 1 records or creates the current `users`, `chats`, and `email_send_log` schema.
- Version 2 clears every legacy `users.admin_authorized` value. Runtime authorization never reads this column.
- Existing databases retain the unused `admin_authorized` column to avoid a destructive SQLite table rebuild. New databases do not create it.
- Back up `users.db` before first startup if it contains local data. The only data mutation is resetting legacy persistent admin grants to `0`.

The legacy column can be physically removed in a later maintenance migration after deployed databases have completed version 2.
