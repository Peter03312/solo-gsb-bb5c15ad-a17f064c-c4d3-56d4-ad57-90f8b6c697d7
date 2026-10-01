-- 铁路区间临时限速编排服务结构（幂等）

CREATE TABLE IF NOT EXISTS line_config (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    total_km    NUMERIC(14, 3) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT line_config_singleton CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS base_segments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY,
    line_id     INTEGER NOT NULL DEFAULT 1
                    REFERENCES line_config(id),
    start_km    NUMERIC(14, 3) NOT NULL,
    end_km      NUMERIC(14, 3) NOT NULL,
    speed       NUMERIC(10, 3) NOT NULL,
    CONSTRAINT base_segments_pk PRIMARY KEY (id)
);

-- 基础段互不重叠（左闭右开 numrange '[)'）；连续覆盖由应用层校验。
-- btree_gist 为 gist 索引提供标量类型支持。
CREATE EXTENSION IF NOT EXISTS btree_gist;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'base_segments_no_overlap'
    ) THEN
        ALTER TABLE base_segments
            ADD CONSTRAINT base_segments_no_overlap
            EXCLUDE USING gist (
                line_id WITH =,
                numrange(start_km, end_km, '[)') WITH &&
            ) DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS commands (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    command_no    VARCHAR(64) NOT NULL UNIQUE,
    start_km      NUMERIC(14, 3) NOT NULL,
    end_km        NUMERIC(14, 3) NOT NULL,
    speed         NUMERIC(10, 3) NOT NULL,
    valid_from    TIMESTAMPTZ NOT NULL,
    valid_to      TIMESTAMPTZ NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'draft',
    version       BIGINT NOT NULL DEFAULT 1,
    published_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT commands_status_chk
        CHECK (status IN ('draft', 'published', 'revoked')),
    CONSTRAINT commands_version_positive CHECK (version >= 1),
    CONSTRAINT commands_time_window_chk CHECK (valid_from < valid_to),
    CONSTRAINT commands_publish_revoke_order
        CHECK (
            (status = 'draft' AND published_at IS NULL AND revoked_at IS NULL)
            OR (status = 'published' AND published_at IS NOT NULL
                AND revoked_at IS NULL)
            OR (status = 'revoked' AND published_at IS NOT NULL
                AND revoked_at IS NOT NULL AND revoked_at >= published_at)
        )
);

-- 时刻查询主路径：已发布（含已撤销，供历史重现）且时间窗覆盖的命令
CREATE INDEX IF NOT EXISTS idx_commands_query
    ON commands (valid_from, valid_to)
    WHERE status IN ('published', 'revoked');
