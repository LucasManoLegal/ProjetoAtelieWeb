PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS collections (
    name TEXT NOT NULL,
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(name);

CREATE TABLE IF NOT EXISTS materiais (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    categoria TEXT,
    emoji TEXT,
    quantidade REAL DEFAULT 0,
    unidade TEXT,
    quantidade_minima REAL DEFAULT 0,
    custo REAL DEFAULT 0,
    gtin TEXT,
    foto TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_materiais_nome_gtin ON materiais (lower(nome), gtin);

CREATE TABLE IF NOT EXISTS produtos (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    emoji TEXT,
    preco_venda REAL DEFAULT 0,
    receita TEXT,
    gtin TEXT,
    estoque_pronto INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS pedidos (
    id TEXT PRIMARY KEY,
    cliente TEXT,
    produto_id TEXT,
    produto_nome TEXT,
    produto_emoji TEXT,
    quantidade INTEGER,
    preco_unitario REAL,
    valor_total REAL,
    status TEXT,
    materiais_baixados INTEGER DEFAULT 0,
    usou_estoque_pronto INTEGER DEFAULT 0,
    data_pedido TEXT,
    data_pedido_iso TEXT,
    data_entrega TEXT DEFAULT '',
    google_event_id TEXT DEFAULT '',
    google_calendar_synced_at TEXT DEFAULT '',
    origem TEXT DEFAULT 'web',
    telefone_cliente TEXT DEFAULT '',
    observacoes TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS movimentacoes (
    id TEXT PRIMARY KEY,
    tipo TEXT,
    material_nome TEXT,
    quantidade REAL,
    unidade TEXT,
    motivo TEXT,
    data TEXT,
    usuario TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_movimentacoes_created ON movimentacoes(created_at);

CREATE TABLE IF NOT EXISTS sobras (
    id TEXT PRIMARY KEY,
    material_id TEXT,
    descricao TEXT,
    quantidade REAL,
    unidade TEXT,
    data TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS despesas (
    id TEXT PRIMARY KEY,
    descricao TEXT,
    valor REAL,
    categoria TEXT,
    data TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS usuarios (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE,
    password_hash TEXT,
    role TEXT,
    roles TEXT,
    nome TEXT,
    avatar TEXT,
    created_at TEXT,
    session_version INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    is_system INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role TEXT NOT NULL,
    resource TEXT NOT NULL,
    can_create INTEGER DEFAULT 0,
    can_read INTEGER DEFAULT 0,
    can_update INTEGER DEFAULT 0,
    can_delete INTEGER DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (role, resource)
);

CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    actor_username TEXT,
    target_user_id TEXT,
    action TEXT,
    details TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS relatorios_customizados (
    id TEXT PRIMARY KEY,
    titulo TEXT NOT NULL,
    tipo TEXT NOT NULL,
    tipo_grafico TEXT NOT NULL,
    categoria_filtro TEXT,
    status_filtro TEXT,
    apenas_criticos INTEGER DEFAULT 0,
    observacoes TEXT,
    criado_por TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- Materiais Padrão do Sistema
INSERT OR IGNORE INTO materiais (id, nome, categoria, emoji, quantidade, unidade, quantidade_minima, custo, gtin, foto, created_at, updated_at) VALUES
('mat-tesoura-001', 'Tesoura', 'Outros', '✂️', 5, 'unidades', 1, 25.0, '7891112004818', NULL, datetime('now'), datetime('now')),
('mat-courino-002', 'Courino', 'Courino', '🟫', 10.0, 'metros', 5, 40.0, '7890007239168', NULL, datetime('now'), datetime('now')),
('mat-mosquetao-003', 'Mosquetão Dourado', 'Metal', '⚙️', 20, 'unidades', 10, 3.5, '7891075200296', NULL, datetime('now'), datetime('now')),
('mat-ziper-004', 'Zíper', 'Aviamento', '🧵', 15, 'unidades', 5, 4.0, '7891446632541', NULL, datetime('now'), datetime('now')),
('mat-linha-005', 'Linha de Costura Preta', 'Aviamento', '🧵', 6, 'rolos', 3, 8.0, '7897977100092', NULL, datetime('now'), datetime('now')),
('mat-courino-caramelo-006', 'Courino Caramelo', 'Courino', '🟫', 8.0, 'metros', 5, 45.0, '7908723211167', NULL, datetime('now'), datetime('now')),
('mat-fivela-007', 'Fivela Quadrada Dourada', 'Metal', '⚙️', 12, 'unidades', 5, 5.0, '7890011087373', NULL, datetime('now'), datetime('now')),
('mat-courino-preto-008', 'Courino Preto', 'Courino', '🟫', 12.0, 'metros', 5, 45.0, '7890007239170', NULL, datetime('now'), datetime('now'));

COMMIT;
