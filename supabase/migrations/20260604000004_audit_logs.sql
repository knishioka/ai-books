-- Migration: audit_logs (監査ログ, append-only)
--
-- Forward-only. Do not edit after it has been applied — add a new migration.
--
-- Append-only by construction (AGENTS.md invariant #5): existing rows can never
-- be UPDATEd or DELETEd. We enforce this with triggers rather than role/grant
-- juggling so the guarantee holds for any connecting role (the single-user
-- posture means everyone connects as the same role) and is trivially testable.

CREATE TABLE audit_logs (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor      text NOT NULL,   -- 実行主体 (AI agent / user 識別子)
    tool_name  text,            -- 経由した MCP tool 名
    action     text NOT NULL,   -- 論理操作 (insert / update / delete / post ...)
    table_name text,            -- 対象テーブル
    record_id  text,            -- 対象行の識別子 (テキストで汎用化)
    before     jsonb,           -- 変更前スナップショット
    after      jsonb,           -- 変更後スナップショット
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_logs_table_record_idx ON audit_logs (table_name, record_id);
CREATE INDEX audit_logs_created_at_idx ON audit_logs (created_at);

-- Reject any mutation of existing rows. TG_OP is one of UPDATE / DELETE / TRUNCATE.
CREATE FUNCTION audit_logs_forbid_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_forbid_mutation();

CREATE TRIGGER audit_logs_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_forbid_mutation();

-- Row-level triggers do not fire for TRUNCATE; guard it at statement level too.
CREATE TRIGGER audit_logs_no_truncate
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_forbid_mutation();
