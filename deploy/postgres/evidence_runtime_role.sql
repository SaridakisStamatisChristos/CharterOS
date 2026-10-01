-- Run as the schema owner/migrator after Alembic migrations.
-- The application login should be a member of this NOLOGIN role.
-- Example (DBA-managed): GRANT charteros_runtime TO charteros_app;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'charteros_runtime') THEN
        CREATE ROLE charteros_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

SELECT charteros_apply_runtime_evidence_privileges('charteros_runtime');
SELECT charteros_apply_runtime_governance_privileges('charteros_runtime');
