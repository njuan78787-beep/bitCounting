// =============================================================================
// src/api/mock.ts
// Demo mode — patches window.fetch to return realistic Puerto Rico business data.
// Activated when VITE_DEMO_MODE=true.
// Login: username=demo password=cualquiera, MFA: cualquier 6 dígitos
// Login CPA: username=cpa.demo password=cualquiera
// =============================================================================

const DELAY_MS = 350

function ok(data: unknown): Promise<Response> {
  return new Promise((res) =>
    setTimeout(
      () => res(new Response(JSON.stringify(data), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })),
      DELAY_MS,
    ),
  )
}

function err(status: number, detail: string): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify({ detail }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

// ─── Seed data ────────────────────────────────────────────────────────────────

const NOW   = new Date()
const today = (offset = 0) => new Date(NOW.getTime() + offset * 86_400_000).toISOString()

const TRANSACTIONS = [
  {
    transaction_id: 'txn-d001',
    client_id:      'client-demo-001',
    vendor:         'Café El Morro Supplies LLC',
    amount:         '2450.00',
    date:           today(-3),
    status:         'processed',
    account_code:   '5200',
    account_name:   'Costo de Ventas — Materias Primas',
    confidence:     '0.96',
    created_at:     today(-3),
    updated_at:     today(-3),
  },
  {
    transaction_id: 'txn-d002',
    client_id:      'client-demo-001',
    vendor:         'Rivera HVAC Services',
    amount:         '850.00',
    date:           today(-5),
    status:         'processed',
    account_code:   '5400',
    account_name:   'Mantenimiento y Reparaciones',
    confidence:     '0.91',
    created_at:     today(-5),
    updated_at:     today(-5),
  },
  {
    transaction_id: 'txn-d003',
    client_id:      'client-demo-001',
    vendor:         'Autoridad de Energía Eléctrica PR',
    amount:         '1200.00',
    date:           today(-7),
    status:         'processed',
    account_code:   '5410',
    account_name:   'Servicios Públicos — Electricidad',
    confidence:     '0.99',
    created_at:     today(-7),
    updated_at:     today(-7),
  },
  {
    transaction_id: 'txn-d004',
    client_id:      'client-demo-001',
    vendor:         'Goya Foods Puerto Rico Corp.',
    amount:         '3200.00',
    date:           today(-1),
    status:         'paused',
    account_code:   null,
    account_name:   null,
    confidence:     '0.61',
    created_at:     today(-1),
    updated_at:     today(-1),
  },
  {
    transaction_id: 'txn-d005',
    client_id:      'client-demo-001',
    vendor:         'Triple-S Salud CSP',
    amount:         '1800.00',
    date:           today(-10),
    status:         'processed',
    account_code:   '5430',
    account_name:   'Beneficios a Empleados — Seguro Médico',
    confidence:     '0.97',
    created_at:     today(-10),
    updated_at:     today(-10),
  },
  {
    transaction_id: 'txn-d006',
    client_id:      'client-demo-001',
    vendor:         'Office Max Puerto Rico',
    amount:         '340.00',
    date:           today(-12),
    status:         'processed',
    account_code:   '5420',
    account_name:   'Materiales de Oficina',
    confidence:     '0.94',
    created_at:     today(-12),
    updated_at:     today(-12),
  },
  {
    transaction_id: 'txn-d007',
    client_id:      'client-demo-001',
    vendor:         'Banco Popular de Puerto Rico',
    amount:         '2000.00',
    date:           today(-14),
    status:         'processed',
    account_code:   '2100',
    account_name:   'Préstamo a Largo Plazo — Pago',
    confidence:     '0.88',
    created_at:     today(-14),
    updated_at:     today(-14),
  },
  {
    transaction_id: 'txn-d008',
    client_id:      'client-demo-001',
    vendor:         'Municipio de San Juan',
    amount:         '500.00',
    date:           today(-20),
    status:         'rejected',
    account_code:   null,
    account_name:   null,
    confidence:     '0.42',
    created_at:     today(-20),
    updated_at:     today(-20),
  },
  {
    transaction_id: 'txn-d009',
    client_id:      'client-demo-001',
    vendor:         'Amazon Business PR',
    amount:         '650.00',
    date:           null,
    status:         'pending',
    account_code:   null,
    account_name:   null,
    confidence:     null,
    created_at:     today(0),
    updated_at:     null,
  },
  {
    transaction_id: 'txn-d010',
    client_id:      'client-demo-001',
    vendor:         'Departamento de Hacienda PR',
    amount:         '5000.00',
    date:           today(-30),
    status:         'processed',
    account_code:   '2300',
    account_name:   'Impuesto sobre Ingresos por Pagar',
    confidence:     '0.98',
    created_at:     today(-30),
    updated_at:     today(-30),
  },
]

const PAUSES = [
  {
    pause_id:        'pause-d001',
    transaction_id:  'txn-d004',
    client_id:       'client-demo-001',
    reason:          'Monto inusualmente alto para proveedor de alimentos. Clasificación contable ambigua: posible inventario (1300) vs. costo de ventas (5200).',
    severity:        'HIGH',
    status:          'ACTIVE',
    confidence:      0.61,
    sla_hours:       8,
    sla_deadline:    today(0.3),
    created_at:      today(-1),
    resolved_at:     null,
    resolution_note: null,
    agent_analysis:  'CENTINELA detectó que el monto ($3,200) supera el promedio mensual del proveedor Goya Foods en un 340%. Además, el período de transacción coincide con cierre de trimestre fiscal. Se recomienda verificar si corresponde a compra de inventario estacional o gasto corriente para clasificación GAAP correcta.',
  },
  {
    pause_id:        'pause-d002',
    transaction_id:  'txn-d008',
    client_id:       'client-demo-001',
    reason:          'Licencia municipal — No aplica IVU. Verificar si aplica deducción bajo Sección 1023(a) del Código de Rentas Internas PR.',
    severity:        'MEDIUM',
    status:          'ACTIVE',
    confidence:      0.42,
    sla_hours:       24,
    sla_deadline:    today(0.8),
    created_at:      today(-20),
    resolved_at:     null,
    resolution_note: null,
    agent_analysis:  'Pago al Municipio de San Juan identificado como licencia comercial. Bajo el Código de Rentas Internas de PR, las licencias municipales pueden ser deducibles como gasto de negocio ordinario y necesario. Sin embargo, la clasificación de la cuenta contable requiere verificación: cuenta 5440 (Licencias) vs. 5800 (Gastos Misceláneos). Se rechazó provisionalmente por confianza baja.',
  },
]

const REVIEW_QUEUE = [
  {
    queue_id:       'queue-d001',
    transaction_id: 'txn-d004',
    client_id:      'client-demo-001',
    pause_id:       'pause-d001',
    severity:       'HIGH',
    reason:         'Monto inusual Goya Foods — clasificación ambigua inventario/costo ventas',
    sla_deadline:   today(0.3),
    sla_hours_left: 7,
    status:         'PENDING',
    created_at:     today(-1),
    pre_analysis:   'Monto $3,200 supera 340% del promedio mensual. Verificar inventario estacional vs. gasto corriente.',
  },
  {
    queue_id:       'queue-d002',
    transaction_id: 'txn-d008',
    client_id:      'client-demo-001',
    pause_id:       'pause-d002',
    severity:       'MEDIUM',
    reason:         'Licencia municipal — verificar deducibilidad Sección 1023(a) CRI-PR',
    sla_deadline:   today(0.8),
    sla_hours_left: 19,
    status:         'PENDING',
    created_at:     today(-20),
    pre_analysis:   'Cuenta 5440 vs. 5800 pendiente de decisión CPA.',
  },
  {
    queue_id:       'queue-d003',
    transaction_id: 'txn-d009',
    client_id:      'client-demo-001',
    pause_id:       null,
    severity:       'LOW',
    reason:         'Compra Amazon Business — verificar propósito del negocio',
    sla_deadline:   today(2),
    sla_hours_left: 48,
    status:         'PENDING',
    created_at:     today(0),
    pre_analysis:   null,
  },
]

const NORMATIVE_UPDATES = [
  {
    update_id:             'norm-d001',
    source_name:           'Departamento de Hacienda PR',
    change_type:           'IVU_RATE_CHANGE',
    impact_level:          'HIGH',
    sla_hours:             48,
    sla_deadline:          today(2),
    description:           'Boletín Informativo 25-01: Aclaración sobre aplicación de IVU en servicios digitales y plataformas de streaming bajo el Código de Rentas Internas de PR.',
    status:                'PENDING_REVIEW',
    detected_at:           today(-1),
    requires_human_review: true,
  },
  {
    update_id:             'norm-d002',
    source_name:           'FASB',
    change_type:           'GAAP_UPDATE',
    impact_level:          'MEDIUM',
    sla_hours:             72,
    sla_deadline:          today(3),
    description:           'ASU 2025-01: Actualización de ASC 842 — Arrendamientos. Modificación en el reconocimiento de activos por derecho de uso para arrendatarios con plazos inferiores a 12 meses.',
    status:                'PENDING_REVIEW',
    detected_at:           today(-2),
    requires_human_review: true,
  },
  {
    update_id:             'norm-d003',
    source_name:           'DTRH Puerto Rico',
    change_type:           'PAYROLL_TAX',
    impact_level:          'LOW',
    sla_hours:             120,
    sla_deadline:          today(5),
    description:           'Circular 2025-04: Tablas de retención actualizadas para empleados con salarios entre $50,000 y $100,000 anuales. Vigente a partir del 1ro de mayo de 2025.',
    status:                'ACTIVATED',
    detected_at:           today(-5),
    requires_human_review: false,
  },
]

// ─── Route handlers ───────────────────────────────────────────────────────────

type Handler = (url: string, init?: RequestInit) => Promise<Response>

interface Route {
  method:  string
  pattern: RegExp
  fn:      Handler
}

let _demoRole = 'CLIENT'

const ROUTES: Route[] = [
  // ── Auth ──────────────────────────────────────────────────────────────────
  {
    method: 'POST', pattern: /\/auth\/token/,
    fn: async (_url, init) => {
      const body = JSON.parse((init?.body as string) ?? '{}')
      _demoRole = body.username?.startsWith('cpa') || body.username?.startsWith('admin')
        ? body.username?.startsWith('admin') ? 'EXIMIA_ADMIN' : 'CPA_PARTNER'
        : 'CLIENT'
      return ok({ access_token: 'demo-temp-token', token_type: 'bearer', mfa_required: true, mfa_pending: true })
    },
  },
  {
    method: 'POST', pattern: /\/auth\/mfa\/verify/,
    fn: async () => ok({
      access_token:  'demo-access-token',
      refresh_token: 'demo-refresh-token',
      token_type:    'bearer',
      expires_in:    3600,
    }),
  },
  {
    method: 'GET', pattern: /\/auth\/me/,
    fn: async () => {
      const isCpa   = _demoRole === 'CPA_PARTNER'
      const isAdmin = _demoRole === 'EXIMIA_ADMIN'
      return ok({
        user_id:     isCpa ? 'user-cpa-demo' : isAdmin ? 'user-admin-demo' : 'user-client-demo',
        username:    isCpa ? 'cpa.demo' : isAdmin ? 'admin.demo' : 'demo',
        role:        _demoRole,
        client_id:   isCpa || isAdmin ? null : 'client-demo-001',
        permissions: isCpa
          ? ['read:all', 'approve:transactions', 'release:pauses']
          : isAdmin
          ? ['read:all', 'write:all', 'admin:normative']
          : ['read:own', 'upload:own'],
      })
    },
  },
  {
    method: 'POST', pattern: /\/auth\/logout/,
    fn: async () => ok({}),
  },

  // ── Transactions ──────────────────────────────────────────────────────────
  {
    method: 'GET', pattern: /\/transactions\?/,
    fn: async (url) => {
      const params = new URL(url, 'http://x').searchParams
      const status = params.get('status')
      const page   = parseInt(params.get('page') ?? '1')
      const size   = parseInt(params.get('page_size') ?? '20')
      const items  = status ? TRANSACTIONS.filter(t => t.status === status) : TRANSACTIONS
      const start  = (page - 1) * size
      return ok({ items: items.slice(start, start + size), total: items.length, page, page_size: size })
    },
  },
  {
    method: 'GET', pattern: /\/transactions$/,
    fn: async () => ok({ items: TRANSACTIONS, total: TRANSACTIONS.length, page: 1, page_size: 20 }),
  },
  {
    method: 'POST', pattern: /\/transactions\/upload/,
    fn: async () => ok({ ...TRANSACTIONS[0], transaction_id: `txn-demo-new-${Date.now()}`, status: 'pending' }),
  },

  // ── Reports ───────────────────────────────────────────────────────────────
  {
    method: 'GET', pattern: /\/reports\/summary/,
    fn: async () => ok({
      total_documents_submitted:      47,
      total_documents_processed:      44,
      active_pauses:                  2,
      resolved_pauses:                18,
      total_pauses:                   20,
      total_orchestrator_decisions:   89,
      decisions_requiring_cpa_review: 3,
      generated_at:                   today(),
    }),
  },
  {
    method: 'GET', pattern: /\/reports\/balance-sheet/,
    fn: async () => ok({
      client_id:   'client-demo-001',
      as_of_date:  today(),
      assets: {
        current_assets: {
          cash_and_equivalents: '45200.00',
          accounts_receivable:  '12800.00',
          inventory:            '8500.00',
          prepaid_expenses:     '2400.00',
          total_current_assets: '68900.00',
        },
        non_current_assets: {
          property_plant_equipment: '95000.00',
          accumulated_depreciation: '-18500.00',
          intangible_assets:        '5000.00',
          total_non_current_assets: '81500.00',
        },
      },
      liabilities: {
        current_liabilities: {
          accounts_payable:               '14200.00',
          accrued_expenses:               '6800.00',
          ivu_payable:                    '3450.00',
          current_portion_long_term_debt: '12000.00',
          total_current_liabilities:      '36450.00',
        },
        long_term_liabilities: {
          long_term_debt:              '55000.00',
          deferred_tax_liability:      '4200.00',
          total_long_term_liabilities: '59200.00',
        },
      },
      equity: {
        common_stock:      '40000.00',
        retained_earnings: '14750.00',
        total_equity:      '54750.00',
      },
      total_assets:     '150400.00',
      total_liabilities: '95650.00',
      total_equity:      '54750.00',
      generated_at:      today(),
    }),
  },
  {
    method: 'GET', pattern: /\/reports\/income-statement/,
    fn: async () => ok({
      client_id:    'client-demo-001',
      period_start: today(-30),
      period_end:   today(),
      revenue: {
        service_revenue:  '28500.00',
        product_sales:    '42000.00',
        other_revenue:    '1200.00',
        total_revenue:    '71700.00',
      },
      cost_of_goods_sold: { total_cogs: '31200.00' },
      gross_profit:       '40500.00',
      operating_expenses: {
        salaries_and_wages:        '18000.00',
        rent:                      '4200.00',
        professional_fees:         '1500.00',
        depreciation:              '925.00',
        utilities:                 '1200.00',
        total_operating_expenses:  '25825.00',
      },
      operating_income:    '14675.00',
      other_income_expense: { interest_expense: '-660.00', total_other: '-660.00' },
      net_income:          '14015.00',
      generated_at:        today(),
    }),
  },
  {
    method: 'GET', pattern: /\/reports\/cash-flow/,
    fn: async () => ok({
      client_id:    'client-demo-001',
      period_start: today(-30),
      period_end:   today(),
      operating_activities:  { net_cash_from_operations: '12400.00' },
      investing_activities:  { capital_expenditures: '-3500.00' },
      financing_activities:  { loan_repayments: '-2000.00' },
      net_change_in_cash:    '6900.00',
      beginning_cash:        '38300.00',
      ending_cash:           '45200.00',
      generated_at:          today(),
    }),
  },
  {
    method: 'GET', pattern: /\/reports\/ivu-summary/,
    fn: async () => ok({
      client_id:          'client-demo-001',
      period:             `${NOW.getFullYear()}-${String(NOW.getMonth() + 1).padStart(2, '0')}`,
      ivu_collected:      '8245.50',
      ivu_remitted:       '4795.00',
      ivu_balance:        '3450.50',
      ivu_rate_municipal: '0.01',
      ivu_rate_state:     '0.105',
      transactions_count: 29,
      form_sc2915_ready:  true,
      generated_at:       today(),
    }),
  },
  {
    method: 'GET', pattern: /\/reports\/confidence/,
    fn: async () => ok({
      total_documents_with_confidence: 44,
      average_confidence: 0.887,
      min_confidence:     0.42,
      max_confidence:     0.99,
      distribution:     { very_high: 28, high: 9, medium: 4, low: 2, very_low: 1 },
      distribution_pct: { very_high: 63.6, high: 20.5, medium: 9.1, low: 4.5, very_low: 2.3 },
      generated_at:     today(),
    }),
  },

  // ── Centinela ─────────────────────────────────────────────────────────────
  {
    method: 'GET', pattern: /\/centinela\/pauses/,
    fn: async () => ok({ items: PAUSES, total: PAUSES.length, page: 1, page_size: 20 }),
  },
  {
    method: 'GET', pattern: /\/centinela\/pauses\//,
    fn: async (url) => {
      const id = url.split('/').pop()?.split('?')[0]
      const pause = PAUSES.find(p => p.pause_id === id) ?? PAUSES[0]
      return ok(pause)
    },
  },
  {
    method: 'POST', pattern: /\/centinela\/pauses\/.*\/release/,
    fn: async (_url, init) => {
      const body = JSON.parse((init?.body as string) ?? '{}')
      return ok({
        pause_id:        'pause-d001',
        status:          'RESOLVED',
        resolution_note: body.resolution_note ?? '',
        resolved_at:     today(),
        resolved_by:     'cpa.demo',
      })
    },
  },

  // ── CPA ───────────────────────────────────────────────────────────────────
  {
    method: 'GET', pattern: /\/cpa\/review-queue/,
    fn: async () => ok({ items: REVIEW_QUEUE, total: REVIEW_QUEUE.length, page: 1, page_size: 20 }),
  },
  {
    method: 'POST', pattern: /\/cpa\/review-queue\/.*\/approve/,
    fn: async (_url, init) => {
      const body = JSON.parse((init?.body as string) ?? '{}')
      return ok({ queue_id: 'queue-d001', status: body.decision ?? 'APPROVED' })
    },
  },
  {
    method: 'POST', pattern: /\/cpa\/instructions/,
    fn: async (_url, init) => {
      const body = JSON.parse((init?.body as string) ?? '{}')
      return ok({
        instruction_id: `inst-demo-${Date.now()}`,
        client_id:      body.client_id ?? 'client-demo-001',
        content:        body.content ?? '',
        preview:        null,
        status:         'DRAFT',
        created_at:     today(),
        confirmed_at:   null,
      })
    },
  },
  {
    method: 'GET', pattern: /\/cpa\/instructions\/.*\/preview/,
    fn: async () => ok({ preview: 'Vista previa generada por el sistema de instrucciones Bit-Counting. Esta instrucción será aplicada a las próximas transacciones del cliente según las reglas GAAP-PR vigentes.' }),
  },
  {
    method: 'POST', pattern: /\/cpa\/instructions\/.*\/confirm/,
    fn: async () => ok({
      instruction_id: 'inst-demo-001',
      client_id:      'client-demo-001',
      content:        'Instrucción confirmada',
      preview:        null,
      status:         'CONFIRMED',
      created_at:     today(),
      confirmed_at:   today(),
    }),
  },
  {
    method: 'GET', pattern: /\/cpa\/metrics/,
    fn: async () => ok({
      avg_response_time_hours: 3.2,
      sla_compliance_pct:      94.7,
      total_reviewed:          47,
      pending_count:           3,
      approved_count:          39,
      rejected_count:          5,
      period_days:             30,
    }),
  },
  {
    method: 'GET', pattern: /\/cpa\/pauses/,
    fn: async () => ok({ items: PAUSES, total: PAUSES.length, page: 1, page_size: 20 }),
  },

  // ── Admin ─────────────────────────────────────────────────────────────────
  {
    method: 'GET', pattern: /\/admin\/normative-updates/,
    fn: async () => ok(NORMATIVE_UPDATES),
  },
  {
    method: 'POST', pattern: /\/admin\/normative-updates\/.*\/activate/,
    fn: async () => ok({ status: 'ACTIVATED', message: 'Actualización normativa activada correctamente.' }),
  },
]

// ─── Fetch interceptor ────────────────────────────────────────────────────────

const _orig = globalThis.fetch.bind(globalThis)

function matchRoute(url: string, method: string): Route | undefined {
  const m = method.toUpperCase()
  return ROUTES.find(r => r.method === m && r.pattern.test(url))
}

export function installMock(): void {
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url    = typeof input === 'string' ? input : input instanceof URL ? input.href : (input as Request).url
    const method = init?.method ?? (input instanceof Request ? input.method : 'GET')

    const route = matchRoute(url, method)
    if (route) {
      try {
        return await route.fn(url, init)
      } catch {
        return err(500, 'Demo mode error')
      }
    }

    // Fallthrough to real fetch
    return _orig(input, init)
  }
}
