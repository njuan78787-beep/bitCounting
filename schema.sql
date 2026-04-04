-- =============================================================================
-- SYSTEM:      Bit-Counting
-- DESCRIPTION: Autonomous AI Accounting System for Puerto Rico
--              Schema de base de datos para el sistema autónomo de contabilidad
--              con supervisión de CPA, auditoría de decisiones de agentes IA,
--              y cumplimiento con las regulaciones fiscales de Puerto Rico.
-- DATE:        2026-04-04
-- DATABASE:    PostgreSQL 14+
-- =============================================================================

-- Enable pgcrypto for UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- SECTION 1: TRIGGER FUNCTION — updated_at auto-update
-- =============================================================================

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- SECTION 2: TABLE — cpa_partners
-- =============================================================================

CREATE TABLE cpa_partners (
    id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
    name                    VARCHAR(255)    NOT NULL,
    license_number          VARCHAR(100)    NOT NULL,
    email                   VARCHAR(255)    NOT NULL,
    phone                   VARCHAR(50),
    firm_name               VARCHAR(255),
    specializations         TEXT[],
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    pr_cpa_board_status     VARCHAR(50),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT cpa_partners_pkey            PRIMARY KEY (id),
    CONSTRAINT cpa_partners_license_uq      UNIQUE (license_number),
    CONSTRAINT cpa_partners_email_uq        UNIQUE (email)
);

COMMENT ON TABLE cpa_partners IS
    'Socios contadores públicos autorizados (CPA) que supervisan y validan las '
    'decisiones del sistema autónomo de contabilidad Bit-Counting en Puerto Rico.';

CREATE TRIGGER trg_cpa_partners_updated_at
    BEFORE UPDATE ON cpa_partners
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 3: TABLE — clients
-- =============================================================================

CREATE TABLE clients (
    id                          UUID            NOT NULL DEFAULT gen_random_uuid(),
    name                        VARCHAR(255)    NOT NULL,
    legal_name                  VARCHAR(255)    NOT NULL,
    ein                         VARCHAR(20),
    pr_merchant_registration    VARCHAR(50),
    business_type               VARCHAR(50)     NOT NULL,
    industry_code               VARCHAR(20),
    fiscal_year_end             DATE,
    currency                    CHAR(3)         NOT NULL DEFAULT 'USD',
    timezone                    VARCHAR(50)     NOT NULL DEFAULT 'America/Puerto_Rico',
    address_line1               VARCHAR(255),
    address_line2               VARCHAR(255),
    city                        VARCHAR(100),
    zip_code                    VARCHAR(20),
    contact_email               VARCHAR(255),
    contact_phone               VARCHAR(50),
    assigned_cpa_partner_id     UUID,
    status                      VARCHAR(20)     NOT NULL DEFAULT 'active',
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT clients_pkey                 PRIMARY KEY (id),
    CONSTRAINT clients_ein_uq               UNIQUE (ein),
    CONSTRAINT clients_business_type_chk    CHECK (business_type IN (
                                                'LLC', 'Corp', 'S-Corp', 'Sole Proprietor',
                                                'Partnership', 'Non-Profit', 'Other'
                                            )),
    CONSTRAINT clients_status_chk           CHECK (status IN ('active', 'inactive', 'suspended')),
    CONSTRAINT clients_cpa_partner_fk       FOREIGN KEY (assigned_cpa_partner_id)
                                                REFERENCES cpa_partners (id)
                                                ON DELETE SET NULL
);

COMMENT ON TABLE clients IS
    'Clientes empresariales registrados en el sistema Bit-Counting. Incluye '
    'entidades con distintas estructuras legales que operan bajo las regulaciones '
    'fiscales de Puerto Rico y están asignadas a un socio CPA supervisor.';

CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 4: TABLE — accounts (Chart of Accounts; self-referential parent)
-- =============================================================================

CREATE TABLE accounts (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    client_id           UUID            NOT NULL,
    code                VARCHAR(20)     NOT NULL,
    name                VARCHAR(255)    NOT NULL,
    account_type        VARCHAR(20)     NOT NULL,
    normal_balance      VARCHAR(10)     NOT NULL,
    parent_account_id   UUID,
    description         TEXT,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT accounts_pkey                PRIMARY KEY (id),
    CONSTRAINT accounts_client_code_uq      UNIQUE (client_id, code),
    CONSTRAINT accounts_account_type_chk    CHECK (account_type IN (
                                                'asset', 'liability', 'equity', 'revenue', 'expense'
                                            )),
    CONSTRAINT accounts_normal_balance_chk  CHECK (normal_balance IN ('debit', 'credit')),
    CONSTRAINT accounts_client_fk           FOREIGN KEY (client_id)
                                                REFERENCES clients (id)
                                                ON DELETE RESTRICT,
    CONSTRAINT accounts_parent_fk           FOREIGN KEY (parent_account_id)
                                                REFERENCES accounts (id)
                                                ON DELETE RESTRICT
);

COMMENT ON TABLE accounts IS
    'Catálogo de cuentas contables (Chart of Accounts) por cliente. Soporta '
    'jerarquía de cuentas mediante referencia auto-referencial a cuenta padre, '
    'siguiendo los principios de partida doble bajo GAAP y normas locales de PR.';

CREATE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 5: TABLE — tax_rules (self-referential supersedes)
-- =============================================================================

CREATE TABLE tax_rules (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    rule_code           VARCHAR(50)     NOT NULL,
    version             INTEGER         NOT NULL DEFAULT 1,
    rule_name           VARCHAR(255)    NOT NULL,
    description         TEXT,
    jurisdiction        VARCHAR(10)     NOT NULL DEFAULT 'PR',
    tax_type            VARCHAR(50)     NOT NULL,
    rate                NUMERIC(10,6),
    effective_date      DATE            NOT NULL,
    expiration_date     DATE,
    conditions          JSONB,
    calculation_formula TEXT,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    supersedes_rule_id  UUID,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT tax_rules_pkey               PRIMARY KEY (id),
    CONSTRAINT tax_rules_code_version_uq    UNIQUE (rule_code, version),
    CONSTRAINT tax_rules_tax_type_chk       CHECK (tax_type IN (
                                                'IVU', 'income_corporate', 'income_individual',
                                                'payroll', 'excise', 'municipal'
                                            )),
    CONSTRAINT tax_rules_supersedes_fk      FOREIGN KEY (supersedes_rule_id)
                                                REFERENCES tax_rules (id)
                                                ON DELETE SET NULL
);

COMMENT ON TABLE tax_rules IS
    'Reglas tributarias aplicables en Puerto Rico (IVU, ingreso corporativo, '
    'nómina, etc.). Soporta versionado y cadena de supersesión para mantener '
    'el historial normativo completo y auditable.';

CREATE TRIGGER trg_tax_rules_updated_at
    BEFORE UPDATE ON tax_rules
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 6: TABLE — tax_rule_metadata
-- =============================================================================

CREATE TABLE tax_rule_metadata (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    tax_rule_id         UUID            NOT NULL,
    source_document     VARCHAR(500),
    source_url          TEXT,
    publication_date    DATE,
    effective_from      DATE,
    last_validated_at   TIMESTAMPTZ,
    validation_status   VARCHAR(20)     NOT NULL DEFAULT 'pending',
    validated_by_cpa_id UUID,
    validation_notes    TEXT,
    version_tag         VARCHAR(50),
    checksum            VARCHAR(64),
    raw_source_data     JSONB,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT tax_rule_metadata_pkey               PRIMARY KEY (id),
    CONSTRAINT tax_rule_metadata_validation_chk     CHECK (validation_status IN (
                                                        'pending', 'validated', 'disputed', 'superseded'
                                                    )),
    CONSTRAINT tax_rule_metadata_tax_rule_fk        FOREIGN KEY (tax_rule_id)
                                                        REFERENCES tax_rules (id)
                                                        ON DELETE CASCADE,
    CONSTRAINT tax_rule_metadata_cpa_fk             FOREIGN KEY (validated_by_cpa_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL
);

COMMENT ON TABLE tax_rule_metadata IS
    'Metadatos de procedencia y validación para cada regla tributaria. Registra '
    'la fuente documental, estado de validación por CPA, y el checksum de '
    'integridad del contenido normativo importado al sistema.';

CREATE TRIGGER trg_tax_rule_metadata_updated_at
    BEFORE UPDATE ON tax_rule_metadata
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 7: TABLE — transactions
-- =============================================================================

CREATE TABLE transactions (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    client_id           UUID            NOT NULL,
    transaction_date    DATE            NOT NULL,
    description         TEXT            NOT NULL,
    reference_number    VARCHAR(100),
    external_reference  VARCHAR(255),
    amount              NUMERIC(15,2)   NOT NULL,
    currency            CHAR(3)         NOT NULL DEFAULT 'USD',
    transaction_type    VARCHAR(50)     NOT NULL,
    status              VARCHAR(20)     NOT NULL DEFAULT 'pending',
    source              VARCHAR(100),
    source_metadata     JSONB,
    tax_rule_id         UUID,
    approved_by_cpa_id  UUID,
    approved_at         TIMESTAMPTZ,
    voided_at           TIMESTAMPTZ,
    void_reason         TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT transactions_pkey                    PRIMARY KEY (id),
    CONSTRAINT transactions_transaction_type_chk    CHECK (transaction_type IN (
                                                        'invoice', 'payment', 'expense', 'payroll',
                                                        'tax_payment', 'transfer', 'adjustment', 'refund'
                                                    )),
    CONSTRAINT transactions_status_chk             CHECK (status IN (
                                                        'pending', 'posted', 'voided', 'under_review'
                                                    )),
    CONSTRAINT transactions_client_fk              FOREIGN KEY (client_id)
                                                        REFERENCES clients (id)
                                                        ON DELETE RESTRICT,
    CONSTRAINT transactions_tax_rule_fk            FOREIGN KEY (tax_rule_id)
                                                        REFERENCES tax_rules (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT transactions_cpa_fk                 FOREIGN KEY (approved_by_cpa_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL
);

COMMENT ON TABLE transactions IS
    'Transacciones financieras de los clientes procesadas por el agente autónomo. '
    'Incluye facturas, pagos, nómina, impuestos y ajustes. Cada transacción puede '
    'estar vinculada a una regla tributaria y requiere aprobación CPA en ciertos casos.';

CREATE TRIGGER trg_transactions_updated_at
    BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 8: TABLE — journal_entries
-- (Balance check trigger defined after this table)
-- =============================================================================

CREATE TABLE journal_entries (
    id              UUID            NOT NULL DEFAULT gen_random_uuid(),
    transaction_id  UUID            NOT NULL,
    client_id       UUID            NOT NULL,
    account_id      UUID            NOT NULL,
    entry_type      VARCHAR(10)     NOT NULL,
    amount          NUMERIC(15,2)   NOT NULL,
    description     TEXT,
    line_number     INTEGER         NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT journal_entries_pkey             PRIMARY KEY (id),
    CONSTRAINT journal_entries_entry_type_chk   CHECK (entry_type IN ('debit', 'credit')),
    CONSTRAINT journal_entries_amount_chk       CHECK (amount > 0),
    CONSTRAINT journal_entries_line_number_chk  CHECK (line_number > 0),
    CONSTRAINT journal_entries_transaction_fk   FOREIGN KEY (transaction_id)
                                                    REFERENCES transactions (id)
                                                    ON DELETE RESTRICT,
    CONSTRAINT journal_entries_client_fk        FOREIGN KEY (client_id)
                                                    REFERENCES clients (id)
                                                    ON DELETE RESTRICT,
    CONSTRAINT journal_entries_account_fk       FOREIGN KEY (account_id)
                                                    REFERENCES accounts (id)
                                                    ON DELETE RESTRICT
);

COMMENT ON TABLE journal_entries IS
    'Asientos contables de partida doble para cada transacción. Cada par de '
    'débitos y créditos debe cuadrar por transacción (validado por trigger). '
    'Es el registro contable fundamental del sistema de mayor general.';

CREATE TRIGGER trg_journal_entries_updated_at
    BEFORE UPDATE ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 8a: TRIGGER — journal_entries double-entry balance check
-- =============================================================================

CREATE OR REPLACE FUNCTION check_journal_entries_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_transaction_id    UUID;
    v_transaction_status VARCHAR(20);
    v_debit_sum         NUMERIC(15,2);
    v_credit_sum        NUMERIC(15,2);
BEGIN
    -- Determine which transaction_id to check
    IF TG_OP = 'DELETE' THEN
        v_transaction_id := OLD.transaction_id;
    ELSE
        v_transaction_id := NEW.transaction_id;
    END IF;

    -- Only enforce balance when the transaction is in 'posted' status
    SELECT status INTO v_transaction_status
    FROM transactions
    WHERE id = v_transaction_id;

    IF v_transaction_status IS DISTINCT FROM 'posted' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Sum debits and credits for this transaction
    SELECT
        COALESCE(SUM(amount) FILTER (WHERE entry_type = 'debit'),  0),
        COALESCE(SUM(amount) FILTER (WHERE entry_type = 'credit'), 0)
    INTO v_debit_sum, v_credit_sum
    FROM journal_entries
    WHERE transaction_id = v_transaction_id;

    IF v_debit_sum <> v_credit_sum THEN
        RAISE EXCEPTION
            'Desequilibrio contable en transacción %: débitos=% créditos=%. '
            'Los débitos y créditos deben ser iguales para transacciones publicadas.',
            v_transaction_id, v_debit_sum, v_credit_sum;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_journal_entries_balance_check
    AFTER INSERT OR UPDATE OR DELETE ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION check_journal_entries_balance();

-- =============================================================================
-- SECTION 9: TABLE — agent_decisions (APPEND-ONLY)
-- =============================================================================

CREATE TABLE agent_decisions (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    client_id           UUID,
    agent_name          VARCHAR(100)    NOT NULL,
    agent_version       VARCHAR(50),
    decision_type       VARCHAR(100)    NOT NULL,
    input_data          JSONB           NOT NULL,
    output_data         JSONB           NOT NULL,
    confidence_score    NUMERIC(5,4),
    reasoning           TEXT,
    applied_rules       JSONB,
    transaction_id      UUID,
    paused_by_centinela BOOLEAN         NOT NULL DEFAULT FALSE,
    requires_cpa_review BOOLEAN         NOT NULL DEFAULT FALSE,
    cpa_reviewed_by     UUID,
    cpa_reviewed_at     TIMESTAMPTZ,
    cpa_override        BOOLEAN,
    cpa_override_notes  TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT agent_decisions_pkey                 PRIMARY KEY (id),
    CONSTRAINT agent_decisions_confidence_chk       CHECK (confidence_score BETWEEN 0 AND 1),
    CONSTRAINT agent_decisions_client_fk            FOREIGN KEY (client_id)
                                                        REFERENCES clients (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT agent_decisions_transaction_fk       FOREIGN KEY (transaction_id)
                                                        REFERENCES transactions (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT agent_decisions_cpa_reviewer_fk      FOREIGN KEY (cpa_reviewed_by)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL
);

COMMENT ON TABLE agent_decisions IS
    'Registro inmutable (append-only) de todas las decisiones tomadas por los '
    'agentes de IA. Almacena los datos de entrada, salida, confianza y razonamiento '
    'de cada decisión autónoma, junto con la revisión CPA cuando aplica. '
    'No permite UPDATE ni DELETE para garantizar trazabilidad total.';

-- =============================================================================
-- SECTION 9a: TRIGGER — agent_decisions immutability (no UPDATE or DELETE)
-- =============================================================================

CREATE OR REPLACE FUNCTION prevent_agent_decisions_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'La tabla agent_decisions es de solo adición (append-only). '
            'No se permiten actualizaciones (UPDATE) en el registro de decisiones del agente.';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'La tabla agent_decisions es de solo adición (append-only). '
            'No se permiten eliminaciones (DELETE) en el registro de decisiones del agente.';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_decisions_immutable
    BEFORE UPDATE OR DELETE ON agent_decisions
    FOR EACH ROW EXECUTE FUNCTION prevent_agent_decisions_mutation();

-- =============================================================================
-- SECTION 10: TABLE — centinela_pauses
-- =============================================================================

CREATE TABLE centinela_pauses (
    id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
    client_id               UUID            NOT NULL,
    agent_decision_id       UUID,
    transaction_id          UUID,
    pause_reason            VARCHAR(100)    NOT NULL,
    pause_category          VARCHAR(50)     NOT NULL,
    trigger_data            JSONB,
    confidence_at_pause     NUMERIC(5,4),
    threshold_applied       NUMERIC(5,4),
    status                  VARCHAR(20)     NOT NULL DEFAULT 'active',
    assigned_to_cpa_id      UUID,
    resolution_notes        TEXT,
    resolved_by_cpa_id      UUID,
    resolved_at             TIMESTAMPTZ,
    escalated_at            TIMESTAMPTZ,
    sla_deadline            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT centinela_pauses_pkey                PRIMARY KEY (id),
    CONSTRAINT centinela_pauses_category_chk        CHECK (pause_category IN (
                                                        'automatic', 'manual', 'regulatory', 'threshold'
                                                    )),
    CONSTRAINT centinela_pauses_status_chk          CHECK (status IN (
                                                        'active', 'resolved', 'escalated', 'dismissed'
                                                    )),
    CONSTRAINT centinela_pauses_client_fk           FOREIGN KEY (client_id)
                                                        REFERENCES clients (id)
                                                        ON DELETE RESTRICT,
    CONSTRAINT centinela_pauses_agent_decision_fk   FOREIGN KEY (agent_decision_id)
                                                        REFERENCES agent_decisions (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT centinela_pauses_transaction_fk      FOREIGN KEY (transaction_id)
                                                        REFERENCES transactions (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT centinela_pauses_assigned_cpa_fk     FOREIGN KEY (assigned_to_cpa_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT centinela_pauses_resolved_cpa_fk     FOREIGN KEY (resolved_by_cpa_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL
);

COMMENT ON TABLE centinela_pauses IS
    'Pausas de supervisión generadas por el módulo Centinela cuando el agente '
    'autónomo detecta situaciones que requieren revisión humana. Incluye umbrales '
    'de confianza, categorías regulatorias y SLA de resolución por el CPA asignado.';

CREATE TRIGGER trg_centinela_pauses_updated_at
    BEFORE UPDATE ON centinela_pauses
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 11: TABLE — cpa_instructions
-- =============================================================================

CREATE TABLE cpa_instructions (
    id                  UUID            NOT NULL DEFAULT gen_random_uuid(),
    cpa_partner_id      UUID            NOT NULL,
    client_id           UUID,
    instruction_type    VARCHAR(100)    NOT NULL,
    title               VARCHAR(255)    NOT NULL,
    instruction_text    TEXT            NOT NULL,
    structured_params   JSONB,
    scope               VARCHAR(50)     NOT NULL DEFAULT 'client',
    priority            INTEGER         NOT NULL DEFAULT 50,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    valid_from          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    valid_until         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT cpa_instructions_pkey            PRIMARY KEY (id),
    CONSTRAINT cpa_instructions_scope_chk       CHECK (scope IN (
                                                    'client', 'all_clients', 'account_type', 'transaction_type'
                                                )),
    CONSTRAINT cpa_instructions_priority_chk    CHECK (priority BETWEEN 1 AND 100),
    CONSTRAINT cpa_instructions_cpa_fk          FOREIGN KEY (cpa_partner_id)
                                                    REFERENCES cpa_partners (id)
                                                    ON DELETE RESTRICT,
    CONSTRAINT cpa_instructions_client_fk       FOREIGN KEY (client_id)
                                                    REFERENCES clients (id)
                                                    ON DELETE SET NULL
);

COMMENT ON TABLE cpa_instructions IS
    'Instrucciones emitidas por los socios CPA al sistema autónomo. Pueden aplicar '
    'a un cliente específico (client_id) o a todos los clientes del CPA (NULL). '
    'Controlan el comportamiento del agente de acuerdo con criterios profesionales.';

CREATE TRIGGER trg_cpa_instructions_updated_at
    BEFORE UPDATE ON cpa_instructions
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 12: TABLE — cpa_policies (self-referential supersedes)
-- =============================================================================

CREATE TABLE cpa_policies (
    id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
    source_instruction_id   UUID            NOT NULL,
    client_id               UUID,
    policy_code             VARCHAR(100)    NOT NULL,
    policy_name             VARCHAR(255)    NOT NULL,
    policy_type             VARCHAR(100)    NOT NULL,
    rules                   JSONB           NOT NULL,
    version                 INTEGER         NOT NULL DEFAULT 1,
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    supersedes_policy_id    UUID,
    effective_from          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    effective_until         TIMESTAMPTZ,
    last_applied_at         TIMESTAMPTZ,
    application_count       INTEGER         NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT cpa_policies_pkey                PRIMARY KEY (id),
    CONSTRAINT cpa_policies_code_uq             UNIQUE (policy_code),
    CONSTRAINT cpa_policies_source_inst_fk      FOREIGN KEY (source_instruction_id)
                                                    REFERENCES cpa_instructions (id)
                                                    ON DELETE RESTRICT,
    CONSTRAINT cpa_policies_client_fk           FOREIGN KEY (client_id)
                                                    REFERENCES clients (id)
                                                    ON DELETE SET NULL,
    CONSTRAINT cpa_policies_supersedes_fk       FOREIGN KEY (supersedes_policy_id)
                                                    REFERENCES cpa_policies (id)
                                                    ON DELETE SET NULL
);

COMMENT ON TABLE cpa_policies IS
    'Políticas formalizadas derivadas de las instrucciones CPA. Definen reglas '
    'de negocio estructuradas (JSONB) que el agente autónomo consulta en tiempo '
    'de ejecución. Soporta versiones y cadena de supersesión de políticas.';

CREATE TRIGGER trg_cpa_policies_updated_at
    BEFORE UPDATE ON cpa_policies
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 13: TABLE — error_cards
-- =============================================================================

CREATE TABLE error_cards (
    id                          UUID            NOT NULL DEFAULT gen_random_uuid(),
    client_id                   UUID,
    agent_decision_id           UUID,
    transaction_id              UUID,
    error_type                  VARCHAR(100)    NOT NULL,
    error_category              VARCHAR(50)     NOT NULL,
    severity                    VARCHAR(20)     NOT NULL,
    description                 TEXT            NOT NULL,
    expected_output             JSONB,
    actual_output               JSONB,
    root_cause                  TEXT,
    corrective_action           TEXT,
    correction_applied          BOOLEAN         NOT NULL DEFAULT FALSE,
    correction_applied_at       TIMESTAMPTZ,
    corrected_by_cpa_id         UUID,
    is_anonymized               BOOLEAN         NOT NULL DEFAULT FALSE,
    contributed_to_pool         BOOLEAN         NOT NULL DEFAULT FALSE,
    pool_contribution_at        TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT error_cards_pkey                 PRIMARY KEY (id),
    CONSTRAINT error_cards_category_chk         CHECK (error_category IN (
                                                    'misclassification', 'wrong_tax_rate', 'threshold_error',
                                                    'missing_rule', 'data_quality', 'logic_error'
                                                )),
    CONSTRAINT error_cards_severity_chk         CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT error_cards_client_fk            FOREIGN KEY (client_id)
                                                    REFERENCES clients (id)
                                                    ON DELETE SET NULL,
    CONSTRAINT error_cards_agent_decision_fk    FOREIGN KEY (agent_decision_id)
                                                    REFERENCES agent_decisions (id)
                                                    ON DELETE SET NULL,
    CONSTRAINT error_cards_transaction_fk       FOREIGN KEY (transaction_id)
                                                    REFERENCES transactions (id)
                                                    ON DELETE SET NULL,
    CONSTRAINT error_cards_cpa_fk               FOREIGN KEY (corrected_by_cpa_id)
                                                    REFERENCES cpa_partners (id)
                                                    ON DELETE SET NULL
);

COMMENT ON TABLE error_cards IS
    'Tarjetas de error que documentan fallos del agente autónomo. Permiten '
    'trazabilidad de errores, acciones correctivas y contribución anónima al '
    'pool de aprendizaje colectivo (Eximia) para mejorar el sistema.';

CREATE TRIGGER trg_error_cards_updated_at
    BEFORE UPDATE ON error_cards
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 14: TABLE — behavior_patterns (no FK — privacy)
-- =============================================================================

CREATE TABLE behavior_patterns (
    id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
    pattern_type            VARCHAR(100)    NOT NULL,
    pattern_category        VARCHAR(100)    NOT NULL,
    industry_code           VARCHAR(20),
    transaction_type        VARCHAR(100),
    pattern_data            JSONB           NOT NULL,
    frequency               INTEGER         NOT NULL DEFAULT 1,
    confidence_score        NUMERIC(5,4),
    source_error_card_ids   UUID[],
    is_validated            BOOLEAN         NOT NULL DEFAULT FALSE,
    validated_at            TIMESTAMPTZ,
    validated_by            VARCHAR(100),
    contributed_to_eximia   BOOLEAN         NOT NULL DEFAULT FALSE,
    eximia_contribution_at  TIMESTAMPTZ,
    eximia_pattern_id       VARCHAR(255),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT behavior_patterns_pkey          PRIMARY KEY (id),
    CONSTRAINT behavior_patterns_frequency_chk CHECK (frequency > 0)
);

COMMENT ON TABLE behavior_patterns IS
    'Patrones de comportamiento contable derivados de error cards anonimizadas. '
    'No contiene claves foráneas para preservar la privacidad de los clientes. '
    'Los IDs de tarjetas de error se almacenan como array UUID. Los patrones '
    'validados se comparten con el pool Eximia de aprendizaje colectivo.';

CREATE TRIGGER trg_behavior_patterns_updated_at
    BEFORE UPDATE ON behavior_patterns
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 15: TABLE — confidence_calibration (no FK)
-- =============================================================================

CREATE TABLE confidence_calibration (
    id                          UUID            NOT NULL DEFAULT gen_random_uuid(),
    agent_name                  VARCHAR(100)    NOT NULL,
    decision_type               VARCHAR(100)    NOT NULL,
    calibration_period_start    DATE            NOT NULL,
    calibration_period_end      DATE            NOT NULL,
    total_decisions             INTEGER         NOT NULL,
    correct_decisions           INTEGER         NOT NULL,
    accuracy_rate               NUMERIC(5,4)    GENERATED ALWAYS AS (
                                                    CASE WHEN total_decisions = 0
                                                         THEN NULL
                                                         ELSE correct_decisions::NUMERIC / total_decisions
                                                    END
                                                ) STORED,
    average_confidence          NUMERIC(5,4)    NOT NULL,
    calibration_error           NUMERIC(5,4),
    confidence_histogram        JSONB,
    accuracy_by_confidence      JSONB,
    threshold_adjustment        NUMERIC(5,4),
    model_version               VARCHAR(50),
    notes                       TEXT,
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT confidence_calibration_pkey          PRIMARY KEY (id),
    CONSTRAINT confidence_calibration_period_uq     UNIQUE (agent_name, decision_type, calibration_period_start),
    CONSTRAINT confidence_calibration_total_chk     CHECK (total_decisions >= 0),
    CONSTRAINT confidence_calibration_correct_chk   CHECK (correct_decisions >= 0)
);

COMMENT ON TABLE confidence_calibration IS
    'Datos de calibración estadística de la confianza de cada agente por tipo '
    'de decisión. Permite detectar y corregir sobreconfianza o subconfianza del '
    'modelo. La tasa de precisión (accuracy_rate) se calcula automáticamente '
    'como columna generada. No tiene claves foráneas para permitir datos históricos.';

CREATE TRIGGER trg_confidence_calibration_updated_at
    BEFORE UPDATE ON confidence_calibration
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 16: TABLE — normative_updates
-- =============================================================================

CREATE TABLE normative_updates (
    id                      UUID            NOT NULL DEFAULT gen_random_uuid(),
    detected_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    source                  VARCHAR(255)    NOT NULL,
    source_url              TEXT,
    update_type             VARCHAR(100)    NOT NULL,
    affected_tax_rule_ids   UUID[],
    description             TEXT            NOT NULL,
    raw_content             TEXT,
    parsed_changes          JSONB,
    urgency                 VARCHAR(20)     NOT NULL DEFAULT 'normal',
    effective_date          DATE,
    review_status           VARCHAR(20)     NOT NULL DEFAULT 'pending',
    assigned_cpa_id         UUID,
    reviewed_by_cpa_id      UUID,
    reviewed_at             TIMESTAMPTZ,
    review_notes            TEXT,
    applied_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT normative_updates_pkey               PRIMARY KEY (id),
    CONSTRAINT normative_updates_type_chk           CHECK (update_type IN (
                                                        'rate_change', 'new_rule', 'rule_removal',
                                                        'rule_modification', 'deadline_change', 'form_update'
                                                    )),
    CONSTRAINT normative_updates_urgency_chk        CHECK (urgency IN ('low', 'normal', 'high', 'critical')),
    CONSTRAINT normative_updates_review_status_chk  CHECK (review_status IN (
                                                        'pending', 'under_review', 'approved', 'rejected', 'deferred'
                                                    )),
    CONSTRAINT normative_updates_assigned_cpa_fk    FOREIGN KEY (assigned_cpa_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL,
    CONSTRAINT normative_updates_reviewed_cpa_fk    FOREIGN KEY (reviewed_by_cpa_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE SET NULL
);

COMMENT ON TABLE normative_updates IS
    'Actualizaciones normativas detectadas por el sistema (cambios en tasas, '
    'nuevas leyes, modificaciones de reglas tributarias de Puerto Rico). '
    'Los IDs de reglas afectadas se almacenan como array UUID. Requieren '
    'revisión y aprobación del CPA asignado antes de aplicarse al sistema.';

CREATE TRIGGER trg_normative_updates_updated_at
    BEFORE UPDATE ON normative_updates
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 17: TABLE — approval_log
-- =============================================================================

CREATE TABLE approval_log (
    id                          UUID            NOT NULL DEFAULT gen_random_uuid(),
    cpa_partner_id              UUID            NOT NULL,
    client_id                   UUID,
    approvable_type             VARCHAR(100)    NOT NULL,
    approvable_id               UUID            NOT NULL,
    action                      VARCHAR(20)     NOT NULL,
    decision_latency_seconds    INTEGER,
    friction_score              NUMERIC(5,2),
    friction_factors            JSONB,
    confidence_at_submission    NUMERIC(5,4),
    notes                       TEXT,
    override_justification      TEXT,
    ip_address                  INET,
    session_id                  VARCHAR(255),
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT approval_log_pkey                    PRIMARY KEY (id),
    CONSTRAINT approval_log_approvable_type_chk     CHECK (approvable_type IN (
                                                        'transaction', 'agent_decision', 'normative_update',
                                                        'centinela_pause', 'cpa_policy', 'error_card'
                                                    )),
    CONSTRAINT approval_log_action_chk              CHECK (action IN (
                                                        'approved', 'rejected', 'escalated', 'deferred', 'override'
                                                    )),
    CONSTRAINT approval_log_latency_chk             CHECK (decision_latency_seconds >= 0),
    CONSTRAINT approval_log_friction_chk            CHECK (friction_score BETWEEN 0 AND 100),
    CONSTRAINT approval_log_cpa_fk                  FOREIGN KEY (cpa_partner_id)
                                                        REFERENCES cpa_partners (id)
                                                        ON DELETE RESTRICT,
    CONSTRAINT approval_log_client_fk               FOREIGN KEY (client_id)
                                                        REFERENCES clients (id)
                                                        ON DELETE SET NULL
);

COMMENT ON TABLE approval_log IS
    'Registro de auditoría completo de todas las acciones de aprobación realizadas '
    'por los socios CPA. Captura latencia de decisión, puntuación de fricción y '
    'factores contextuales para medir la eficiencia y calidad del proceso de '
    'supervisión humana sobre el agente autónomo.';

CREATE TRIGGER trg_approval_log_updated_at
    BEFORE UPDATE ON approval_log
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- SECTION 18: INDEXES
-- =============================================================================

-- clients
CREATE INDEX idx_clients_status
    ON clients (status);
CREATE INDEX idx_clients_assigned_cpa_partner_id
    ON clients (assigned_cpa_partner_id);

-- transactions
CREATE INDEX idx_transactions_client_date
    ON transactions (client_id, transaction_date);
CREATE INDEX idx_transactions_client_status
    ON transactions (client_id, status);
CREATE INDEX idx_transactions_client_date_desc
    ON transactions (client_id, transaction_date DESC);

-- journal_entries
CREATE INDEX idx_journal_entries_transaction_id
    ON journal_entries (transaction_id);
CREATE INDEX idx_journal_entries_client_account
    ON journal_entries (client_id, account_id);
CREATE INDEX idx_journal_entries_account_id
    ON journal_entries (account_id);

-- accounts
CREATE INDEX idx_accounts_client_is_active
    ON accounts (client_id, is_active);
CREATE INDEX idx_accounts_client_account_type
    ON accounts (client_id, account_type);

-- agent_decisions
CREATE INDEX idx_agent_decisions_client_created_desc
    ON agent_decisions (client_id, created_at DESC);
CREATE INDEX idx_agent_decisions_decision_type_created_desc
    ON agent_decisions (decision_type, created_at DESC);
CREATE INDEX idx_agent_decisions_transaction_id
    ON agent_decisions (transaction_id);
CREATE INDEX idx_agent_decisions_requires_cpa_review
    ON agent_decisions (requires_cpa_review)
    WHERE requires_cpa_review = TRUE;
CREATE INDEX idx_agent_decisions_paused_by_centinela
    ON agent_decisions (paused_by_centinela)
    WHERE paused_by_centinela = TRUE;

-- centinela_pauses
CREATE INDEX idx_centinela_pauses_status_client
    ON centinela_pauses (status, client_id);
CREATE INDEX idx_centinela_pauses_assigned_cpa_status
    ON centinela_pauses (assigned_to_cpa_id, status);
CREATE INDEX idx_centinela_pauses_sla_deadline_active
    ON centinela_pauses (sla_deadline)
    WHERE status = 'active';

-- tax_rules
CREATE INDEX idx_tax_rules_rule_code_version
    ON tax_rules (rule_code, version);
CREATE INDEX idx_tax_rules_tax_type_is_active
    ON tax_rules (tax_type, is_active);
CREATE INDEX idx_tax_rules_effective_expiration
    ON tax_rules (effective_date, expiration_date);

-- cpa_instructions
CREATE INDEX idx_cpa_instructions_cpa_is_active
    ON cpa_instructions (cpa_partner_id, is_active);
CREATE INDEX idx_cpa_instructions_client_is_active
    ON cpa_instructions (client_id, is_active);

-- cpa_policies
CREATE INDEX idx_cpa_policies_client_is_active
    ON cpa_policies (client_id, is_active);
CREATE INDEX idx_cpa_policies_source_instruction
    ON cpa_policies (source_instruction_id);

-- error_cards
CREATE INDEX idx_error_cards_client_severity
    ON error_cards (client_id, severity);
CREATE INDEX idx_error_cards_category_created_desc
    ON error_cards (error_category, created_at DESC);
CREATE INDEX idx_error_cards_not_contributed
    ON error_cards (contributed_to_pool)
    WHERE contributed_to_pool = FALSE;

-- behavior_patterns
CREATE INDEX idx_behavior_patterns_type_category
    ON behavior_patterns (pattern_type, pattern_category);
CREATE INDEX idx_behavior_patterns_not_eximia
    ON behavior_patterns (contributed_to_eximia)
    WHERE contributed_to_eximia = FALSE;

-- confidence_calibration
CREATE INDEX idx_confidence_calibration_agent_type_period
    ON confidence_calibration (agent_name, decision_type, calibration_period_start DESC);

-- normative_updates
CREATE INDEX idx_normative_updates_review_status_urgency
    ON normative_updates (review_status, urgency);
CREATE INDEX idx_normative_updates_effective_date_approved
    ON normative_updates (effective_date)
    WHERE review_status = 'approved';

-- approval_log
CREATE INDEX idx_approval_log_cpa_created_desc
    ON approval_log (cpa_partner_id, created_at DESC);
CREATE INDEX idx_approval_log_approvable
    ON approval_log (approvable_type, approvable_id);
CREATE INDEX idx_approval_log_client_created_desc
    ON approval_log (client_id, created_at DESC);

-- =============================================================================
-- END OF SCHEMA — Bit-Counting Autonomous AI Accounting System for Puerto Rico
-- =============================================================================
