BEGIN;

ALTER TABLE support_requests ADD COLUMN execution_mode VARCHAR;
UPDATE support_requests SET execution_mode = 'agent' WHERE status <> 'pending';
ALTER TABLE support_requests ADD CONSTRAINT request_execution_mode CHECK (
    (status = 'pending' AND execution_mode IS NULL) OR
    (status IN ('running', 'completed', 'failed') AND execution_mode IS NOT NULL
        AND execution_mode IN ('agent', 'workflow'))
);

COMMIT;
