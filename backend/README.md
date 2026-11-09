# Backend

The backend is a Django REST API. Local settings are read from environment variables; `backend/.env.example` documents the expected names. In `DEBUG=true`, email is printed to the console. Production requires a real `DJANGO_SECRET_KEY`, `FRONTEND_BASE_URL`, sender address, and configured email delivery.

## PostgreSQL roles and RLS

The identity migrations enable and force row-level security on organization, membership, and trial tables. Use separate PostgreSQL roles:

- `comprascerta_migrator`: login role used only for schema migrations; `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, and `NOBYPASSRLS`. It owns the database/schema objects.
- `comprascerta_app`: runtime login role; `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, and `NOBYPASSRLS`. It must not own application tables and receives only DML and sequence privileges.

A database administrator creates these roles with strong passwords, creates the database owned by `comprascerta_migrator`, grants the migrator `CREATE` on the `public` schema, then ensures `comprascerta_app` has `CONNECT` on that database, connects as the migrator, and applies [runtime_role_grants.sql](sql/runtime_role_grants.sql). Run `manage.py migrate` with `POSTGRES_APP_USER` and `POSTGRES_APP_PASSWORD` temporarily set to the migrator credentials. Normal application processes use `comprascerta_app`. Do not use a database superuser for the application process: PostgreSQL superusers bypass RLS even when policies are forced.

Tenant-scoped queries must run inside a transaction and set transaction-local `app.current_organization_id`; membership discovery for the signed-in person may also set `app.current_user_id`. The helper in `apps/identity/db_context.py` enforces the transaction requirement. A missing organization context yields no tenant writes. With a user context, a person can read their own active memberships and corresponding organization names to select a network; only an organization context permits changing organization or membership rows. Browser clients should call the API through a same-origin proxy. If separate browser origins are introduced, configure both CSRF trusted origins and CORS deliberately.

## Current implementation boundary

The identity slice includes pending self-service registration, email confirmation, initial organization/admin membership creation, the 30-day trial record, CSRF bootstrap, password login, WebAuthn enrollment/authentication, one-time recovery codes, server-side Django sessions, and RLS foundations. Protected API operations use `MfaForPrivilegedUsers`: the session must record completed MFA while the account has any active administrator, store manager, or buyer membership. Keep this permission in place on privileged endpoints; replacing it with `IsAuthenticated` alone would bypass the role-promotion guard.

The current identity work does not include device authorization, store-scoped role assignment, assisted MFA recovery, authenticated passkey management, or global session revocation. The 30-minute inactivity lock is a client-side requirement; session lifetime and revocation policy still need to be aligned with the browser and desktop clients before production. Rate limits currently use Django REST Framework throttles and the default per-process cache. Configure a shared cache and a trusted reverse-proxy boundary before deploying multiple API workers; do not trust unsanitized `X-Forwarded-For` values. These throttles are an extra guard, not a complete brute-force or denial-of-service defense.

Expired pending signup rows (including password hashes) should be removed periodically with `manage.py purge_expired_registrations`.
