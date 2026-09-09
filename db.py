"""
db.py - Camada Centralizada de Acesso a Dados do Ateliê Haiti
Suporte Dual: PostgreSQL (Produção/Servidor) e SQLite (Local/Testes)
Gerenciamento de conexões, tradução transparente de queries e compatibilidade total de Row.
"""

import os
import re
import sys
import json
import uuid
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

# Guarda referência direta à função nativa sqlite3.connect para evitar loops de recursão
_raw_sqlite3_connect = sqlite3.connect

# ── Carregador simples de .env sem dependências externas ───────────────────────

def carregar_env():
    """Carrega variáveis do arquivo .env no os.environ se existir."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

carregar_env()


# ── Configurações de Banco ───────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "/tmp" if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "data.db")

# Chaves primárias das tabelas do sistema para conversão de UPSERT (INSERT OR REPLACE)
TABLE_PRIMARY_KEYS = {
    "collections": ["id"],
    "materiais": ["id"],
    "produtos": ["id"],
    "pedidos": ["id"],
    "movimentacoes": ["id"],
    "sobras": ["id"],
    "despesas": ["id"],
    "usuarios": ["username"],
    "roles": ["name"],
    "role_permissions": ["role", "resource"],
    "audits": ["id"],
    "relatorios_customizados": ["id"],
    "agendamentos_email": ["id"],
    "historico_envios_email": ["id"],
    "configuracoes_email": ["id"],
    "configuracoes_sso": ["id"],
    "app_meta": ["key"],
}


def get_postgres_config() -> Optional[Dict[str, Any]]:
    """Extrai configuração do PostgreSQL a partir de DATABASE_URL ou variáveis individuais."""
    carregar_env()
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRESQL_URL")
    if db_url:
        return {"dsn": db_url}

    host = os.environ.get("PGHOST") or os.environ.get("DB_HOST")
    if host:
        return {
            "host": host,
            "port": int(os.environ.get("PGPORT") or os.environ.get("DB_PORT") or 5432),
            "dbname": os.environ.get("PGDATABASE") or os.environ.get("DB_NAME") or "postgres",
            "user": os.environ.get("PGUSER") or os.environ.get("DB_USER") or "postgres",
            "password": os.environ.get("PGPASSWORD") or os.environ.get("DB_PASSWORD") or "",
            "sslmode": os.environ.get("PGSSLMODE") or os.environ.get("DB_SSLMODE") or "prefer",
        }
    return None


def is_postgres_active() -> bool:
    """Retorna True se o PostgreSQL estiver configurado e ativo como motor principal."""
    # Se explicitamente desligado ou em modo SQLite forçado sem URL
    if os.environ.get("DB_ENGINE", "").lower() == "sqlite":
        return False
    cfg = get_postgres_config()
    return cfg is not None


# ── Implementação de DbRow Compatível com sqlite3.Row ─────────────────────────

class DbRow(tuple):
    """Objeto linha compatível tanto com tupla/índice (row[0]) quanto com dicionário (row['col'])
    e conversão direta dict(row), além de desempacotamento a, b = row.
    """
    _cols: List[str]
    _col_map: Dict[str, int]

    def __new__(cls, values, description):
        obj = super(DbRow, cls).__new__(cls, values)
        cols = []
        if description:
            for d in description:
                col_name = getattr(d, "name", None)
                if col_name is None:
                    try:
                        col_name = d[0]
                    except Exception:
                        col_name = str(d)
                cols.append(str(col_name))
        obj._cols = cols
        obj._col_map = {c.lower(): i for i, c in enumerate(cols)}
        return obj

    def __getitem__(self, key):
        if isinstance(key, int):
            return super(DbRow, self).__getitem__(key)
        idx = self._col_map.get(str(key).lower())
        if idx is not None:
            return super(DbRow, self).__getitem__(idx)
        raise KeyError(f"Coluna '{key}' não encontrada. Colunas disponíveis: {self._cols}")

    def get(self, key, default=None):
        idx = self._col_map.get(str(key).lower())
        if idx is not None:
            return super(DbRow, self).__getitem__(idx)
        return default

    def keys(self) -> List[str]:
        return list(self._cols)

    def values(self) -> List[Any]:
        return list(self)

    def items(self) -> List[Tuple[str, Any]]:
        return [(c, self[i]) for i, c in enumerate(self._cols)]


# ── Tradutor de Sintaxe SQL (SQLite -> PostgreSQL) ────────────────────────────

def replace_placeholders(sql: str) -> str:
    """Substitui '?' por '%s' respeitando literais de string com aspas simples ou duplas."""
    result = []
    in_quote = None
    escaped = False
    for char in sql:
        if in_quote:
            result.append(char)
            if char == "\\" and not escaped:
                escaped = True
                continue
            if char == in_quote and not escaped:
                in_quote = None
            escaped = False
        else:
            if char in ("'", '"'):
                in_quote = char
                result.append(char)
            elif char == "?":
                result.append("%s")
            else:
                result.append(char)
    return "".join(result)


def convert_insert_or_replace(sql: str) -> str:
    """Converte 'INSERT OR REPLACE INTO table (cols) VALUES (...)' para
    'INSERT INTO table (cols) VALUES (...) ON CONFLICT (pk) DO UPDATE SET ...'
    """
    m = re.match(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z0-9_]+)\s*\((.*?)\)\s*VALUES\s*(.*)$", sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return sql
    table_name = m.group(1).lower()
    cols_raw = m.group(2)
    values_part = m.group(3)
    cols = [c.strip().lower() for c in cols_raw.split(",")]
    pks = TABLE_PRIMARY_KEYS.get(table_name, ["id"])
    non_pks = [c for c in cols if c not in pks]
    pk_str = ", ".join(pks)

    if non_pks:
        updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in non_pks])
        return f"INSERT INTO {m.group(1)} ({cols_raw}) VALUES {values_part} ON CONFLICT ({pk_str}) DO UPDATE SET {updates}"
    else:
        return f"INSERT INTO {m.group(1)} ({cols_raw}) VALUES {values_part} ON CONFLICT ({pk_str}) DO NOTHING"


def convert_sqlite_to_pg(sql: str) -> str:
    """Aplica todas as transformações de compatibilidade para PostgreSQL."""
    clean = sql.strip()
    clean_upper = clean.upper()

    # Comandos SQLite PRAGMA
    if clean_upper in ("PRAGMA JOURNAL_MODE", "PRAGMA JOURNAL_MODE;"):
        return "SELECT 'wal'::text AS journal_mode;"
    if clean_upper in ("PRAGMA INTEGRITY_CHECK", "PRAGMA INTEGRITY_CHECK;"):
        return "SELECT 'ok'::text AS integrity_check;"
    if clean_upper.startswith("PRAGMA"):
        return "-- PRAGMA ignored"

    # BEGIN IMMEDIATE é tratado nativamente pelas transações do psycopg2
    if clean_upper == "BEGIN IMMEDIATE" or clean_upper.startswith("BEGIN IMMEDIATE"):
        return "-- BEGIN IMMEDIATE ignored"

    # ORDER BY rowid DESC -> ORDER BY ctid DESC
    clean = re.sub(r"\bORDER\s+BY\s+rowid\s+DESC\b", "ORDER BY ctid DESC", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bORDER\s+BY\s+rowid\b", "ORDER BY ctid", clean, flags=re.IGNORECASE)

    # INSERT OR IGNORE INTO table (...) VALUES (...) -> INSERT INTO table (...) VALUES (...) ON CONFLICT DO NOTHING
    if re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", clean, flags=re.IGNORECASE):
        clean = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", clean, flags=re.IGNORECASE)
        clean = clean.rstrip(" ;") + " ON CONFLICT DO NOTHING"

    # INSERT OR REPLACE INTO table (...) VALUES (...) -> UPSERT ON CONFLICT DO UPDATE
    if re.match(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\b", clean, flags=re.IGNORECASE):
        clean = convert_insert_or_replace(clean)

    # ADD COLUMN -> ADD COLUMN IF NOT EXISTS
    clean = re.sub(r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)", "ADD COLUMN IF NOT EXISTS ", clean, flags=re.IGNORECASE)

    # ORDER BY col COLLATE NOCASE -> ORDER BY lower(col)
    clean = re.sub(r"\bORDER\s+BY\s+([a-zA-Z0-9_]+)\s+COLLATE\s+NOCASE\b", r"ORDER BY lower(\1)", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bCOLLATE\s+NOCASE\b", "", clean, flags=re.IGNORECASE)

    # datetime('now') / date('now') -> CURRENT_TIMESTAMP / CURRENT_DATE
    clean = re.sub(r"\bdatetime\s*\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bdate\s*\(\s*'now'\s*\)", "CURRENT_DATE", clean, flags=re.IGNORECASE)

    # Substitui marcadores de parâmetro ? -> %s
    clean = replace_placeholders(clean)

    return clean


# ── Wrappers de Conexão e Cursor para PostgreSQL ─────────────────────────────

class PgCursorWrapper:
    def __init__(self, raw_cursor, raw_conn=None, row_factory=None):
        self._cur = raw_cursor
        self._conn = raw_conn
        self.row_factory = row_factory
        try:
            self._sp_cur = raw_conn.cursor() if raw_conn else None
        except Exception:
            self._sp_cur = None

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return getattr(self._cur, "lastrowid", None)

    def execute(self, sql: str, params=None):
        converted = convert_sqlite_to_pg(sql)
        if converted.startswith("-- "):
            # Operação no-op (ex: PRAGMA, BEGIN IMMEDIATE)
            return self

        sp_name = "sp_" + uuid.uuid4().hex[:8]
        sp_active = False
        if self._sp_cur:
            try:
                self._sp_cur.execute(f"SAVEPOINT {sp_name};")
                sp_active = True
            except Exception:
                pass

        try:
            if params is not None:
                if isinstance(params, (list, tuple)):
                    self._cur.execute(converted, params)
                else:
                    self._cur.execute(converted, (params,))
            else:
                self._cur.execute(converted)
            if sp_active and self._sp_cur:
                try:
                    self._sp_cur.execute(f"RELEASE SAVEPOINT {sp_name};")
                except Exception:
                    pass
        except Exception as ex:
            if sp_active and self._sp_cur:
                try:
                    self._sp_cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                except Exception:
                    pass
            raise ex
        return self

    def executemany(self, sql: str, seq_of_params):
        converted = convert_sqlite_to_pg(sql)
        if converted.startswith("-- "):
            return self
        sp_name = "sp_" + uuid.uuid4().hex[:8]
        sp_active = False
        if self._sp_cur:
            try:
                self._sp_cur.execute(f"SAVEPOINT {sp_name};")
                sp_active = True
            except Exception:
                pass
        try:
            self._cur.executemany(converted, seq_of_params)
            if sp_active and self._sp_cur:
                try:
                    self._sp_cur.execute(f"RELEASE SAVEPOINT {sp_name};")
                except Exception:
                    pass
        except Exception as ex:
            if sp_active and self._sp_cur:
                try:
                    self._sp_cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                except Exception:
                    pass
            raise ex
        return self

    def fetchone(self):
        if not getattr(self._cur, "description", None):
            return None
        try:
            row = self._cur.fetchone()
        except Exception:
            return None
        if row is None:
            return None
        return DbRow(row, self._cur.description)

    def fetchall(self):
        if not getattr(self._cur, "description", None):
            return []
        try:
            rows = self._cur.fetchall()
        except Exception:
            return []
        desc = self._cur.description
        return [DbRow(r, desc) for r in rows]

    def fetchmany(self, size=None):
        if not getattr(self._cur, "description", None):
            return []
        try:
            rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        except Exception:
            return []
        desc = self._cur.description
        return [DbRow(r, desc) for r in rows]

    def close(self):
        try:
            if self._sp_cur:
                self._sp_cur.close()
        except Exception:
            pass
        try:
            self._cur.close()
        except Exception:
            pass

    def __iter__(self):
        desc = self._cur.description
        for row in self._cur:
            yield DbRow(row, desc)


# ── Gerenciador de Pool de Conexões PostgreSQL ───────────────────────────────

_pg_pool = None
_pg_pool_lock = threading.Lock()


def get_pg_pool(minconn=2, maxconn=20):
    """Retorna o pool de conexões ThreadedConnectionPool do PostgreSQL (singleton thread-safe)."""
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                cfg = get_postgres_config()
                if not cfg:
                    return None
                try:
                    from psycopg2 import pool
                    if "dsn" in cfg:
                        _pg_pool = pool.ThreadedConnectionPool(minconn=minconn, maxconn=maxconn, dsn=cfg["dsn"])
                    else:
                        _pg_pool = pool.ThreadedConnectionPool(
                            minconn=minconn,
                            maxconn=maxconn,
                            host=cfg["host"],
                            port=cfg["port"],
                            dbname=cfg["dbname"],
                            user=cfg["user"],
                            password=cfg["password"],
                            sslmode=cfg.get("sslmode", "prefer"),
                            connect_timeout=5,
                        )
                except Exception as ex:
                    sys.stderr.write(f"[AVISO POOL] Falha ao inicializar ThreadedConnectionPool: {ex}\n")
                    _pg_pool = None
    return _pg_pool


def close_pg_pool():
    """Encerra todas as conexões do pool para liberação de recursos."""
    global _pg_pool
    with _pg_pool_lock:
        if _pg_pool is not None:
            try:
                _pg_pool.closeall()
            except Exception:
                pass
            _pg_pool = None


class PgConnectionWrapper:
    def __init__(self, raw_conn, pool=None):
        self._conn = raw_conn
        self._pool = pool
        self._closed = False
        self.row_factory = DbRow

    def cursor(self):
        return PgCursorWrapper(self._conn.cursor(), raw_conn=self._conn, row_factory=self.row_factory)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
            try:
                self._pool.putconn(self._conn)
            except Exception:
                pass
        else:
            try:
                self._conn.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()


# ── Provedor Principal de Conexão (get_db_connection) ─────────────────────────

def _get_sqlite_connection():
    """Retorna conexão local SQLite com WAL, timeout e cache em RAM para alto desempenho."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _raw_sqlite3_connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA busy_timeout = 5000;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.execute("PRAGMA cache_size = -64000;")   # 64MB cache em RAM
        cur.execute("PRAGMA mmap_size = 268435456;")  # 256MB memória mapeada
        cur.execute("PRAGMA temp_store = MEMORY;")
    except Exception:
        pass
    return conn


def get_db_connection():
    """Retorna uma conexão ativa pronta para uso:
    - Se PostgreSQL estiver ativo: obtém conexão reaproveitada do ThreadedConnectionPool (reuso ultrarrápido).
    - Se falhar e DB_FALLBACK_SQLITE estiver ativo: utiliza SQLite como contingência segura.
    - Se SQLite estiver ativo: conecta ao arquivo DB_PATH com pragmas de alto desempenho.
    """
    if is_postgres_active():
        try:
            p = get_pg_pool()
            if p is not None:
                raw_conn = p.getconn()
                # Verifica integridade da conexão reaproveitada do pool
                if getattr(raw_conn, "closed", 0) != 0:
                    try:
                        p.putconn(raw_conn, close=True)
                    except Exception:
                        pass
                    raw_conn = p.getconn()
                return PgConnectionWrapper(raw_conn, pool=p)
            else:
                import psycopg2
                cfg = get_postgres_config()
                if "dsn" in cfg:
                    conn = psycopg2.connect(cfg["dsn"], connect_timeout=5)
                else:
                    conn = psycopg2.connect(
                        host=cfg["host"],
                        port=cfg["port"],
                        dbname=cfg["dbname"],
                        user=cfg["user"],
                        password=cfg["password"],
                        sslmode=cfg.get("sslmode", "prefer"),
                        connect_timeout=5,
                    )
                return PgConnectionWrapper(conn)
        except Exception as ex:
            allow_fallback = os.environ.get("DB_FALLBACK_SQLITE", "1").lower() in ("1", "true", "yes")
            if allow_fallback:
                sys.stderr.write(f"[AVISO BANCO] Falha de conexão PostgreSQL ({ex}). Operando em contingência com SQLite.\n")
                return _get_sqlite_connection()
            raise

    return _get_sqlite_connection()


# ── Inicialização de Banco e Tabelas (init_db) ───────────────────────────────

def init_db():
    """Garante que todas as tabelas e índices existam no banco ativo (PostgreSQL ou SQLite)."""
    conn = get_db_connection()
    cur = conn.cursor()

    # collections
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            name TEXT NOT NULL,
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
        """
    )
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(name)")
    except Exception:
        pass

    # materiais
    cur.execute(
        """
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
        )
        """
    )
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_materiais_nome_gtin ON materiais (lower(nome), gtin)")
    except Exception:
        pass

    # produtos
    cur.execute(
        """
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
        )
        """
    )

    # pedidos
    cur.execute(
        """
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
            observacoes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # movimentacoes
    cur.execute(
        """
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
        )
        """
    )
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_movimentacoes_created ON movimentacoes(created_at)")
    except Exception:
        pass

    # sobras
    cur.execute(
        """
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
        )
        """
    )

    # despesas
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS despesas (
            id TEXT PRIMARY KEY,
            descricao TEXT,
            valor REAL,
            categoria TEXT,
            data TEXT,
            created_at TEXT
        )
        """
    )

    # usuarios
    cur.execute(
        """
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
        )
        """
    )

    # Adiciona colunas extras em usuarios/produtos/pedidos caso faltem (legado SQLite)
    if not is_postgres_active():
        for col, ctype in [
            ("role", "TEXT"),
            ("session_version", "INTEGER DEFAULT 0"),
            ("nome", "TEXT"),
            ("avatar", "TEXT"),
            ("roles", "TEXT"),
            ("email", "TEXT"),
            ("google_id", "TEXT"),
            ("google_refresh_token", "TEXT"),
            ("google_access_token", "TEXT"),
            ("google_token_expiry", "TEXT"),
        ]:
            try:
                cur.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {ctype}")
                conn.commit()
            except Exception:
                conn.rollback()

        for col, ctype in [("gtin", "TEXT"), ("estoque_pronto", "INTEGER DEFAULT 0")]:
            try:
                cur.execute(f"ALTER TABLE produtos ADD COLUMN {col} {ctype}")
                conn.commit()
            except Exception:
                conn.rollback()

        for col, ctype in [("usou_estoque_pronto", "INTEGER DEFAULT 0")]:
            try:
                cur.execute(f"ALTER TABLE pedidos ADD COLUMN {col} {ctype}")
                conn.commit()
            except Exception:
                conn.rollback()

    # roles
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            is_system INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # role_permissions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS role_permissions (
            role TEXT NOT NULL,
            resource TEXT NOT NULL,
            can_create INTEGER DEFAULT 0,
            can_read INTEGER DEFAULT 0,
            can_update INTEGER DEFAULT 0,
            can_delete INTEGER DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (role, resource)
        )
        """
    )

    # audits
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audits (
            id TEXT PRIMARY KEY,
            actor_id TEXT,
            actor_username TEXT,
            target_user_id TEXT,
            action TEXT,
            details TEXT,
            created_at TEXT
        )
        """
    )

    # relatorios_customizados
    cur.execute(
        """
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
        )
        """
    )

    # agendamentos_email
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agendamentos_email (
            id TEXT PRIMARY KEY,
            titulo TEXT NOT NULL,
            tipo_relatorio TEXT NOT NULL,
            frequencia TEXT NOT NULL,
            hora_envio TEXT NOT NULL,
            dia_semana INTEGER DEFAULT 0,
            dia_mes INTEGER DEFAULT 1,
            destinatarios TEXT NOT NULL,
            assunto TEXT,
            mensagem TEXT,
            ativo INTEGER DEFAULT 1,
            ultimo_envio TEXT,
            proximo_envio TEXT,
            criado_por TEXT,
            usuario_remetente_id TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # historico_envios_email
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS historico_envios_email (
            id TEXT PRIMARY KEY,
            agendamento_id TEXT,
            titulo TEXT,
            tipo_relatorio TEXT,
            destinatarios TEXT,
            status TEXT,
            mensagem_status TEXT,
            enviado_por TEXT,
            created_at TEXT
        )
        """
    )
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_historico_envios_created ON historico_envios_email(created_at)")
    except Exception:
        pass

    # configuracoes_email
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS configuracoes_email (
            id TEXT PRIMARY KEY,
            smtp_host TEXT,
            smtp_port INTEGER DEFAULT 587,
            smtp_user TEXT,
            smtp_pass TEXT,
            smtp_from TEXT,
            smtp_security TEXT DEFAULT 'tls',
            modo_simulacao INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )

    # configuracoes_sso
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS configuracoes_sso (
            id TEXT PRIMARY KEY,
            google_client_id TEXT,
            google_client_secret TEXT,
            ativo INTEGER DEFAULT 0,
            auto_cadastro INTEGER DEFAULT 1,
            papel_padrao TEXT DEFAULT 'Producao',
            updated_at TEXT
        )
        """
    )

    # app_meta
    cur.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")

    # ── Índices Estratégicos de Alta Performance ──────────────────────────────
    indices_performance = [
        ("idx_usuarios_username", "CREATE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios(username)"),
        ("idx_usuarios_email", "CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email)"),
        ("idx_usuarios_google_id", "CREATE INDEX IF NOT EXISTS idx_usuarios_google_id ON usuarios(google_id)"),
        ("idx_pedidos_status", "CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos(status)"),
        ("idx_pedidos_created_at", "CREATE INDEX IF NOT EXISTS idx_pedidos_created_at ON pedidos(created_at)"),
        ("idx_pedidos_cliente", "CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos(cliente)"),
        ("idx_produtos_nome", "CREATE INDEX IF NOT EXISTS idx_produtos_nome ON produtos(nome)"),
        ("idx_produtos_gtin", "CREATE INDEX IF NOT EXISTS idx_produtos_gtin ON produtos(gtin)"),
        ("idx_materiais_categoria", "CREATE INDEX IF NOT EXISTS idx_materiais_categoria ON materiais(categoria)"),
        ("idx_agendamentos_ativo_proximo", "CREATE INDEX IF NOT EXISTS idx_agendamentos_ativo_proximo ON agendamentos_email(ativo, proximo_envio)"),
        ("idx_audits_created", "CREATE INDEX IF NOT EXISTS idx_audits_created ON audits(created_at)"),
        ("idx_audits_actor", "CREATE INDEX IF NOT EXISTS idx_audits_actor ON audits(actor_username)"),
        ("idx_despesas_data", "CREATE INDEX IF NOT EXISTS idx_despesas_data ON despesas(data)"),
        ("idx_despesas_categoria", "CREATE INDEX IF NOT EXISTS idx_despesas_categoria ON despesas(categoria)"),
    ]
    for _, sql_idx in indices_performance:
        try:
            cur.execute(sql_idx)
        except Exception:
            pass

    conn.commit()
    conn.close()


# ── Diagnósticos e Estatísticas (get_db_stats) ────────────────────────────────

def get_db_stats() -> Dict[str, Any]:
    """Retorna metadados e estatísticas de uso do banco de dados ativo."""
    active_pg = is_postgres_active()
    stats = {
        "engine": "PostgreSQL (Remoto)" if active_pg else "SQLite (Local)",
        "is_postgres": active_pg,
        "path": "",
        "host": "",
        "port": "",
        "dbname": "",
        "user": "",
        "size_kb": 0,
        "size_mb": 0.0,
        "version": "",
        "journal_mode": "N/A",
        "integrity": "OK",
        "connected": False,
        "tables": [],
        "error": None,
    }

    tabelas_principais = [
        ("materiais", "Materiais em Estoque"),
        ("produtos", "Produtos Artesanais"),
        ("pedidos", "Pedidos de Clientes"),
        ("movimentacoes", "Movimentações de Estoque"),
        ("sobras", "Sobras e Retalhos"),
        ("despesas", "Despesas Financeiras"),
        ("usuarios", "Usuários"),
        ("roles", "Papéis de Acesso"),
        ("role_permissions", "Matriz de Permissões"),
        ("audits", "Trilha de Auditoria"),
        ("agendamentos_email", "Agendamentos de E-mail"),
        ("historico_envios_email", "Histórico de Envios"),
        ("configuracoes_sso", "Configurações SSO"),
    ]

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        stats["connected"] = True

        if active_pg:
            cfg = get_postgres_config() or {}
            stats["host"] = cfg.get("host", "Servidor Remoto")
            stats["port"] = str(cfg.get("port", "5432"))
            stats["dbname"] = cfg.get("dbname", "PostgreSQL")
            stats["user"] = cfg.get("user", "")
            stats["path"] = f"{stats['host']}:{stats['port']}/{stats['dbname']}"
            try:
                cur.execute("SELECT version();")
                row = cur.fetchone()
                if row:
                    stats["version"] = row[0].split()[0] + " " + row[0].split()[1]
            except Exception:
                stats["version"] = "PostgreSQL"
        else:
            import sqlite3
            stats["path"] = DB_PATH
            stats["version"] = f"SQLite {sqlite3.sqlite_version}"
            if os.path.exists(DB_PATH):
                sz = os.path.getsize(DB_PATH)
                stats["size_kb"] = round(sz / 1024, 1)
                stats["size_mb"] = round(sz / (1024 * 1024), 2)
            try:
                cur.execute("PRAGMA journal_mode")
                r = cur.fetchone()
                if r: stats["journal_mode"] = str(r[0]).upper()
                cur.execute("PRAGMA integrity_check")
                r = cur.fetchone()
                if r: stats["integrity"] = str(r[0])
            except Exception:
                pass

        # Contagem de linhas por tabela
        for tname, tdesc in tabelas_principais:
            try:
                cur.execute(f"SELECT COUNT(1) FROM {tname}")
                cnt = cur.fetchone()[0]
                stats["tables"].append({"name": tname, "desc": tdesc, "count": cnt})
            except Exception:
                pass

        conn.close()
    except Exception as ex:
        stats["connected"] = False
        stats["error"] = str(ex)

    return stats


def test_db_connection() -> Tuple[bool, str]:
    """Testa a conectividade com o banco configurado e retorna (sucesso, mensagem)."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        res = cur.fetchone()
        conn.close()
        if res and res[0] == 1:
            engine = "PostgreSQL" if is_postgres_active() else "SQLite"
            return True, f"Conexão com {engine} estabelecida com sucesso!"
        return False, "Resposta inesperada do banco de dados."
    except Exception as ex:
        return False, f"Falha de conexão: {str(ex)}"
