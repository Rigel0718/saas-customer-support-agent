BEGIN;

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations (id) ON DELETE RESTRICT,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    actor_user_id UUID,
    event_type VARCHAR NOT NULL,
    target_user_id UUID,
    payload JSONB NOT NULL,
    CONSTRAINT audit_event_type CHECK (
        event_type IN (
            'role_changed',
            'subscription_status_changed',
            'member_invited',
            'user_login',
            'document_viewed',
            'document_edited'
        )
    )
);
CREATE INDEX ix_audit_events_org_occurred ON audit_events (organization_id, occurred_at);

COMMIT;
