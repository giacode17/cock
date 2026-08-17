-- ============================================
-- Insurance Navigator Agent — CockroachDB Schema
-- ============================================

-- ---------------------------------------------
-- Users (kept minimal for hackathon — no real PII)
-- ---------------------------------------------
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------
-- Insurance Plans (structured plan-level facts)
-- ---------------------------------------------
CREATE TABLE insurance_plans (
    plan_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_name STRING NOT NULL,                 -- e.g. "Blue Shield PPO Gold"
    carrier STRING NOT NULL,                   -- e.g. "Blue Shield"
    plan_year INT NOT NULL,                    -- e.g. 2026
    deductible_individual DECIMAL(10,2),
    deductible_family DECIMAL(10,2),
    oop_max_individual DECIMAL(10,2),          -- out-of-pocket max
    copay_pcp DECIMAL(10,2),
    copay_urgent_care DECIMAL(10,2),
    copay_er DECIMAL(10,2),
    copay_telehealth DECIMAL(10,2),
    coinsurance_pct DECIMAL(5,2),              -- e.g. 20.00 = 20%
    network_type STRING,                       -- e.g. "PPO", "HMO"
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which plan(s) a user is currently enrolled in / has been enrolled in historically
CREATE TABLE user_plan_enrollments (
    enrollment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    plan_id UUID NOT NULL REFERENCES insurance_plans(plan_id),
    plan_year INT NOT NULL,
    is_current BOOL NOT NULL DEFAULT true,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------
-- Plan documents, chunked + embedded for semantic search
-- (This is the Vector Indexing use case — SBC text, coverage details, etc.)
-- ---------------------------------------------
CREATE TABLE plan_document_chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID NOT NULL REFERENCES insurance_plans(plan_id),
    source_doc_name STRING,                    -- e.g. "SBC_2026_BlueShieldPPO.pdf"
    chunk_text STRING NOT NULL,                 -- raw text chunk from the doc
    chunk_index INT NOT NULL,                   -- order within source doc
    embedding VECTOR(1024),                      -- Bedrock Titan Text Embeddings V2 output size
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vector index for fast similarity search (CockroachDB distributed vector indexing)
CREATE VECTOR INDEX plan_chunks_embedding_idx
    ON plan_document_chunks (embedding);

-- ---------------------------------------------
-- Visits — the agent's memory of user experiences
-- ---------------------------------------------
CREATE TABLE visits (
    visit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    plan_id UUID NOT NULL REFERENCES insurance_plans(plan_id),
    symptom_description STRING,                -- what the user told the agent
    recommended_care_type STRING,               -- 'pcp' | 'urgent_care' | 'telehealth' | 'er'
    estimated_cost_tier STRING,                 -- e.g. "$", "$$", "$$$"
    estimated_cost_low DECIMAL(10,2),
    estimated_cost_high DECIMAL(10,2),
    actual_care_type STRING,                    -- what the user actually did (if known)
    actual_cost DECIMAL(10,2),                  -- if the user reports back
    satisfaction_rating INT,                    -- 1-5, optional
    status STRING NOT NULL DEFAULT 'recommended', -- 'recommended' | 'completed' | 'cancelled'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------
-- Conversation history (raw agent memory, optional but useful for demo)
-- ---------------------------------------------
CREATE TABLE conversation_messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    visit_id UUID REFERENCES visits(visit_id),   -- nullable, links message to a visit if relevant
    role STRING NOT NULL,                        -- 'user' | 'agent'
    content STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------
-- Indexes for common query patterns
-- ---------------------------------------------
CREATE INDEX idx_visits_user ON visits (user_id, created_at DESC);
CREATE INDEX idx_enrollments_user_current ON user_plan_enrollments (user_id) WHERE is_current = true;
CREATE INDEX idx_messages_user ON conversation_messages (user_id, created_at);
