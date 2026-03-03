-- Usage Data Ingestion & Billing Preview Tool
-- Database Schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- CUSTOMERS
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    external_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- PRICING PLANS
CREATE TABLE pricing_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- PRICING RULES
-- Each rule maps a metric to a billing structure
CREATE TABLE pricing_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plan_id UUID REFERENCES pricing_plans(id) ON DELETE CASCADE,
    metric_name VARCHAR(255) NOT NULL,       -- e.g. "api_calls"
    display_name VARCHAR(255) NOT NULL,       -- e.g. "API Calls"
    unit_label VARCHAR(100) NOT NULL,         -- e.g. "calls", "GB", "hours"
    pricing_model VARCHAR(50) NOT NULL,       -- flat_rate | tiered | volume | package
    base_price NUMERIC(12, 6) DEFAULT 0,      -- price per unit for flat_rate
    free_tier_limit BIGINT DEFAULT 0,         -- free units before billing starts
    tiers JSONB,                              -- for tiered/volume pricing
    -- Example tiers JSON:
    -- [{"up_to": 1000, "price": 0.01}, {"up_to": 10000, "price": 0.008}, {"up_to": null, "price": 0.005}]
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- CUSTOMER CONTRACTS
CREATE TABLE customer_contracts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID REFERENCES customers(id) ON DELETE CASCADE,
    plan_id UUID REFERENCES pricing_plans(id),
    start_date DATE NOT NULL,
    end_date DATE,
    billing_period VARCHAR(20) DEFAULT 'monthly', -- monthly | quarterly | annual
    custom_overrides JSONB DEFAULT '{}',          -- per-metric overrides
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- INGESTION JOBS
-- Tracks each upload/ingest operation
CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    source_type VARCHAR(50) NOT NULL,   -- csv | json | webhook | mock
    original_filename VARCHAR(500),
    raw_data JSONB,                     -- stored raw before normalization
    status VARCHAR(50) DEFAULT 'pending', -- pending | processing | mapped | committed | failed
    row_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- FIELD MAPPINGS
-- AI-suggested or user-confirmed field mappings per job
CREATE TABLE field_mappings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    source_field VARCHAR(255) NOT NULL,     -- original column name from upload
    target_field VARCHAR(255) NOT NULL,     -- normalized field name
    confidence NUMERIC(4, 3),               -- 0.000 to 1.000
    mapping_method VARCHAR(50),             -- ai | rule | manual
    is_confirmed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- USAGE RECORDS (normalized)
CREATE TABLE usage_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    metric_name VARCHAR(255) NOT NULL,      -- normalized metric name
    quantity NUMERIC(20, 6) NOT NULL,
    unit VARCHAR(100),
    recorded_at TIMESTAMPTZ NOT NULL,       -- timestamp of usage
    metadata JSONB DEFAULT '{}',            -- extra fields preserved
    is_anomaly BOOLEAN DEFAULT FALSE,
    anomaly_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- BILLING PREVIEWS
-- Generated preview invoices before commitment
CREATE TABLE billing_previews (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    contract_id UUID REFERENCES customer_contracts(id) ON DELETE SET NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    subtotal NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total NUMERIC(12, 2) NOT NULL DEFAULT 0,
    line_items JSONB NOT NULL DEFAULT '[]',
    warnings JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(50) DEFAULT 'preview',   -- preview | approved | exported
    exported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- VALIDATION WARNINGS
CREATE TABLE validation_warnings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    record_id UUID REFERENCES usage_records(id) ON DELETE CASCADE,
    severity VARCHAR(20) NOT NULL,          -- info | warning | critical
    warning_type VARCHAR(100) NOT NULL,     -- spike | missing_field | unknown_metric | negative_value
    message TEXT NOT NULL,
    metric_name VARCHAR(255),
    affected_value NUMERIC,
    expected_range_low NUMERIC,
    expected_range_high NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- SEED DATA
-- Sample pricing plan
INSERT INTO pricing_plans (id, name, description) VALUES
    ('a1b2c3d4-0000-0000-0000-000000000001', 'Startup Plan', 'Usage-based pricing for growing companies'),
    ('a1b2c3d4-0000-0000-0000-000000000002', 'Enterprise Plan', 'Volume discounts for large-scale usage');

-- Pricing rules for Startup Plan
INSERT INTO pricing_rules (plan_id, metric_name, display_name, unit_label, pricing_model, base_price, free_tier_limit, tiers) VALUES
(
    'a1b2c3d4-0000-0000-0000-000000000001',
    'api_calls', 'API Calls', 'calls', 'tiered', 0, 10000,
    '[{"up_to": 100000, "price": 0.0001}, {"up_to": 1000000, "price": 0.00008}, {"up_to": null, "price": 0.00005}]'
),
(
    'a1b2c3d4-0000-0000-0000-000000000001',
    'compute_hours', 'Compute Hours', 'hours', 'flat_rate', 0.08, 10, null
),
(
    'a1b2c3d4-0000-0000-0000-000000000001',
    'storage_gb', 'Storage', 'GB', 'tiered', 0, 5,
    '[{"up_to": 100, "price": 0.023}, {"up_to": 1000, "price": 0.02}, {"up_to": null, "price": 0.015}]'
),
(
    'a1b2c3d4-0000-0000-0000-000000000001',
    'active_seats', 'Active Seats', 'seats', 'flat_rate', 15.00, 0, null
),
(
    'a1b2c3d4-0000-0000-0000-000000000001',
    'data_transfer_gb', 'Data Transfer', 'GB', 'volume', 0, 10,
    '[{"up_to": 100, "price": 0.09}, {"up_to": null, "price": 0.085}]'
);

-- Pricing rules for Enterprise Plan
INSERT INTO pricing_rules (plan_id, metric_name, display_name, unit_label, pricing_model, base_price, free_tier_limit, tiers) VALUES
(
    'a1b2c3d4-0000-0000-0000-000000000002',
    'api_calls', 'API Calls', 'calls', 'tiered', 0, 100000,
    '[{"up_to": 1000000, "price": 0.00007}, {"up_to": 10000000, "price": 0.00005}, {"up_to": null, "price": 0.00003}]'
),
(
    'a1b2c3d4-0000-0000-0000-000000000002',
    'compute_hours', 'Compute Hours', 'hours', 'flat_rate', 0.065, 100, null
),
(
    'a1b2c3d4-0000-0000-0000-000000000002',
    'storage_gb', 'Storage', 'GB', 'tiered', 0, 100,
    '[{"up_to": 1000, "price": 0.018}, {"up_to": null, "price": 0.012}]'
),
(
    'a1b2c3d4-0000-0000-0000-000000000002',
    'active_seats', 'Active Seats', 'seats', 'flat_rate', 12.00, 0, null
),
(
    'a1b2c3d4-0000-0000-0000-000000000002',
    'data_transfer_gb', 'Data Transfer', 'GB', 'flat_rate', 0.07, 50, null
);

-- Sample customers
INSERT INTO customers (id, name, email, external_id) VALUES
    ('b2c3d4e5-0000-0000-0000-000000000001', 'Acme Corp', 'billing@acme.com', 'acme-001'),
    ('b2c3d4e5-0000-0000-0000-000000000002', 'TechStartup Inc', 'finance@techstartup.io', 'ts-002'),
    ('b2c3d4e5-0000-0000-0000-000000000003', 'GlobalEnterprises LLC', 'ap@globalent.com', 'ge-003');

-- Sample contracts
INSERT INTO customer_contracts (customer_id, plan_id, start_date, billing_period) VALUES
    ('b2c3d4e5-0000-0000-0000-000000000001', 'a1b2c3d4-0000-0000-0000-000000000001', '2024-01-01', 'monthly'),
    ('b2c3d4e5-0000-0000-0000-000000000002', 'a1b2c3d4-0000-0000-0000-000000000001', '2024-03-01', 'monthly'),
    ('b2c3d4e5-0000-0000-0000-000000000003', 'a1b2c3d4-0000-0000-0000-000000000002', '2023-06-01', 'monthly');

-- INDEXES
CREATE INDEX idx_usage_records_job_id ON usage_records(job_id);
CREATE INDEX idx_usage_records_customer_id ON usage_records(customer_id);
CREATE INDEX idx_usage_records_metric ON usage_records(metric_name);
CREATE INDEX idx_usage_records_recorded_at ON usage_records(recorded_at);
CREATE INDEX idx_field_mappings_job_id ON field_mappings(job_id);
CREATE INDEX idx_billing_previews_job_id ON billing_previews(job_id);
CREATE INDEX idx_validation_warnings_job_id ON validation_warnings(job_id);
CREATE INDEX idx_ingestion_jobs_customer_id ON ingestion_jobs(customer_id);