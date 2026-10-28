BEGIN;

ALTER TABLE agent_traces ADD COLUMN failure_metadata JSONB;

COMMIT;
