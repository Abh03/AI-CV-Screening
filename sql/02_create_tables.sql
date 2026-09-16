-- Core candidate repository (Raw encrypted data)
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    track VARCHAR(32) NOT NULL,
    auth_status VARCHAR(16) NOT NULL CHECK (auth_status IN ('eligible', 'ineligible', 'unknown')),
    total_yoe NUMERIC(4, 2) NOT NULL DEFAULT 0.0,
    track_relevant_yoe NUMERIC(4, 2) NOT NULL DEFAULT 0.0,
    date_confidence VARCHAR(8) NOT NULL CHECK (date_confidence IN ('high', 'low')),
    raw_encrypted_json BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Candidate section chunks for keyword and vector search
CREATE TABLE IF NOT EXISTS candidate_sections (
    section_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id UUID REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    source_field VARCHAR(32) NOT NULL,
    redacted_content TEXT NOT NULL,
    tsv_content TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', redacted_content)) STORED,
    embedding VECTOR(384) -- Sized for MiniLM-L6-v2 / bge-small
);

-- Work experiences for interval merging
CREATE TABLE IF NOT EXISTS candidate_experiences (
    experience_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id UUID REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    company_name TEXT,
    role_title TEXT,
    matched_track VARCHAR(32),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL
);

-- Stage 3 LLM Evaluations & audit trails
CREATE TABLE IF NOT EXISTS candidate_evaluations (
    evaluation_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_id UUID REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    jd_id UUID NOT NULL,
    model_version VARCHAR(64) NOT NULL,
    weighted_total NUMERIC(5, 2) NOT NULL,
    category_scores JSONB NOT NULL,
    hallucination_flags TEXT[] DEFAULT '{}',
    requires_human_review BOOLEAN DEFAULT FALSE,
    recruiter_signoff BOOLEAN DEFAULT FALSE,
    evaluated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- High-performance indexes
CREATE INDEX IF NOT EXISTS idx_candidates_track ON candidates(track);
CREATE INDEX IF NOT EXISTS idx_candidate_sections_tsv ON candidate_sections USING GIN(tsv_content);
CREATE INDEX IF NOT EXISTS idx_candidate_sections_vector ON candidate_sections USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_evaluations_ranking ON candidate_evaluations(jd_id, weighted_total DESC);