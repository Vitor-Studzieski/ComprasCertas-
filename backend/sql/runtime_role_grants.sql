-- Run while connected to the CompraCerta database as comprascerta_migrator.
-- The runtime role must not own these tables and must not have BYPASSRLS.
GRANT USAGE ON SCHEMA public TO comprascerta_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO comprascerta_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO comprascerta_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO comprascerta_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO comprascerta_app;
