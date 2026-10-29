BEGIN;

ALTER TABLE agent_traces ADD COLUMN llm_metadata JSONB;

COMMIT;
