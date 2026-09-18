import json
import os
import re
import uuid
import secrets
import sqlite3
import base64
import threading
import time
import urllib.parse
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, make_response, session, g, send_from_directory, jsonify
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import subprocess
import sys
import calendar

app = Flask(__name__)
# Use environment variable for the secret key in production


def ensure_package(pkg_name, import_name=None):
    """Try to import a package; if missing, attempt to install it via pip then import.
    Returns the imported module or raises ImportError.
    """
    import importlib
    name = import_name or pkg_name
    try:
        return importlib.import_module(name)
    except ImportError:
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg_name])
        except Exception:
            raise ImportError(f"Não foi possível instalar {pkg_name}. Instale manualmente.")
        return importlib.import_module(name)
app.secret_key = os.environ.get("APP_SECRET", "troque-esta-chave-em-producao")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# No Vercel (serverless) o disco do projeto é só-leitura; gravamos em /tmp lá.
DATA_DIR = "/tmp" if os.environ.get("VERCEL") else BASE_DIR + "/data"
DB_PATH = os.path.join(DATA_DIR, "data.db")

import db
from db import get_db_connection, get_db_stats, test_db_connection, is_postgres_active
import cloudinary_service
from cloudinary_service import upload_imagem, deletar_imagem
import waha_service

# Interceptor transparente de conexão de banco:
_orig_sqlite3_connect = sqlite3.connect
_in_smart_connect = False

def smart_db_connect(path=DB_PATH, *args, **kwargs):
    global _in_smart_connect
    if path == DB_PATH and not _in_smart_connect:
        try:
            _in_smart_connect = True
            return get_db_connection()
        finally:
            _in_smart_connect = False
    return _orig_sqlite3_connect(path, *args, **kwargs)

sqlite3.connect = smart_db_connect

USE_SQLITE = is_postgres_active() or (os.environ.get("USE_SQLITE", "1") in ("1", "true", "yes"))
DATA_FILE = os.path.join(DATA_DIR, "materiais.json")
SEED_FILE = os.path.join(BASE_DIR, "data", "materiais.json")

CATEGORIAS_EMOJI = {
    "Courino": "🟫",
    "Metal": "⚙️",
    "Aviamento": "🧵",
    "Tecido": "🧶",
    "Embalagem": "📦",
    "Outros": "🔹",
}
CATEGORIAS = list(CATEGORIAS_EMOJI.keys())
UNIDADES = ["unidades", "metros", "rolos", "kg", "gramas", "pares", "pacotes"]
MOTIVOS_BAIXA = ["Produção de bolsa", "Produção de nécessaire", "Amostra / Teste", "Desperdício"]

EMOJIS_PRODUTO = ["👜", "🎒", "👝", "💼", "🧳", "👛"]
STATUS_PEDIDO = ["Pendente", "Em produção", "Concluído", "Entregue", "Cancelado"]
STATUS_PEDIDO_BADGE = {
    "Pendente": "badge-warn",
    "Em produção": "badge-warn",
    "Concluído": "badge-ok",
    "Entregue": "badge-ok",
    "Cancelado": "badge-low",
}
STATUS_SOBRA_BADGE = {
    "Disponível": "badge-warn",
    "Reaproveitado": "badge-ok",
    "Descartado": "badge-low",
}
CATEGORIAS_DESPESA = ["Matéria-prima", "Aluguel", "Transporte", "Ferramentas", "Marketing", "Outros"]

DEFAULT_PRODUTOS = [
    {"nome": "Bolsa Tote Clássica", "emoji": "👜", "preco_venda": 180.0, "receita": [
        {"material_nome": "Courino Preto", "quantidade": 1.5},
        {"material_nome": "Zíper 30cm Preto", "quantidade": 1.0},
        {"material_nome": "Linha de Costura Preta", "quantidade": 0.05},
    ]},
    {"nome": "Necessaire Compacta", "emoji": "👝", "preco_venda": 60.0, "receita": [
        {"material_nome": "Courino Preto", "quantidade": 0.4},
        {"material_nome": "Zíper 30cm Preto", "quantidade": 1.0},
    ]},
    {"nome": "Bolsa Transversal Pequena", "emoji": "👛", "preco_venda": 120.0, "receita": [
        {"material_nome": "Courino Caramelo", "quantidade": 0.8},
        {"material_nome": "Mosquetão Dourado", "quantidade": 2.0},
        {"material_nome": "Fivela Quadrada Dourada", "quantidade": 1.0},
    ]},
]


# ── Fuso horário (datas corretas no horário de Brasília) ─────────────────────

def _fuso_brasil():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Sao_Paulo")
    except Exception:
        return timezone(timedelta(hours=-3))


FUSO_BR = _fuso_brasil()


def agora():
    """Datetime atual no fuso horário do Brasil (UTC-3)."""
    return datetime.now(FUSO_BR)


def formatar_reais(valor):
    """Formata um valor monetário no padrão brasileiro (ex.: 1.234,56)."""
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        v = 0.0
    inteiro, decimal = f"{v:,.2f}".split(".")
    return inteiro.replace(",", ".") + "," + decimal


def parse_float_ptbr(valor_str, default=0.0):
    """Converte valores monetários ou numéricos com vírgula/ponto para float."""
    if valor_str is None:
        return default
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    s = str(valor_str).replace("R$", "").replace(" ", "").strip()
    if not s:
        return default
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


@app.template_filter("moeda")
def filtro_moeda(valor):
    return "R$ " + formatar_reais(valor)


@app.template_filter("data_br")
def filtro_data_br(val):
    if not val:
        return ""
    val_str = str(val).strip()
    if re.match(r"^\d{2}/\d{2}/\d{4}", val_str):
        return val_str
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", val_str)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return val_str


# ── Categorias consistentes e sincronizadas ───────────────────────────────────

CATEGORIA_REGRA = [
    ("Courino", ["courino", "couro", "suede", "camurca", "camurça", "napa", "sintetico", "sintético"]),
    ("Metal", ["metal", "mosquetao", "mosquetão", "fivela", "argola", "corrente", "ilhos", "ilhós", "gancho", "rebite", "tachinha", "alca de metal", "alça de metal", "fecho de metal", "fecho"]),
    ("Aviamento", ["ziper", "zíper", "linha", "fio", "botao", "botão", "colchete", "elastico", "elástico", "velcro", "aviamento", "travado", "etiqueta", "laco", "laço", "renda", "passante", "gancheira", "fita", "viés", "vies"]),
    ("Tecido", ["tecido", "algodao", "algodão", "linho", "tricoline", "voal", "crepe", "sarja", "seda", "forro", "pano", "gabardine", "oxford", "jeans", "malha", "feltro", "lona"]),
    ("Embalagem", ["embalagem", "saco", "caixa", "papel", "plastico", "plástico", "polimero", "polímero", "adesivo", "fita adesiva", "tag", "sacola"]),
    ("Outros", ["tesoura", "alicate", "regua", "régua", "ferramenta", "cola", "giz", "agulha"]),
]


def sugerir_categoria(nome):
    """Sugere uma categoria canônica a partir do nome do material."""
    nome_l = (nome or "").lower()
    for cat, palavras in CATEGORIA_REGRA:
        for p in palavras:
            if p in nome_l:
                return cat
    return "Outros"


def normalizar_categoria(categoria, nome=None):
    """Mapeia categorias livres/despadronizadas para as categorias canônicas."""
    if categoria in CATEGORIAS:
        return categoria
    if nome and sugerir_categoria(nome) != "Outros":
        return sugerir_categoria(nome)
    if categoria and sugerir_categoria(categoria) != "Outros":
        return sugerir_categoria(categoria)
    return "Outros"


# ── Múltiplos papéis por usuário ──────────────────────────────────────────────

def serializar_roles(roles):
    """Converte a lista de papéis em texto JSON para persistir na coluna 'roles'."""
    if not roles:
        return ""
    if isinstance(roles, str):
        return roles
    return json.dumps([r for r in roles if r], ensure_ascii=False)


def usuario_roles_lista(user):
    """Retorna a lista de papéis do usuário (coluna 'roles' JSON; fallback para 'role')."""
    if not user:
        return []
    roles = user.get("roles")
    if isinstance(roles, list):
        return [r for r in roles if r]
    if isinstance(roles, str) and roles.strip():
        try:
            parsed = json.loads(roles)
            if isinstance(parsed, list):
                return [r for r in parsed if r]
        except Exception:
            pass
        return [r.strip() for r in re.sub(r'[\["\]\s]', "", roles).split(",") if r.strip()]
    single = user.get("role") or ""
    return [single] if single else []


def is_user_developer(user=None):
    """Verifica se o usuário fornecido (ou g.user) possui o papel Developer."""
    target = user
    if target is None:
        try:
            from flask import has_request_context
            if has_request_context():
                target = g.get("user")
        except Exception:
            target = None
    if not target or not isinstance(target, dict):
        return False
    roles = usuario_roles_lista(target)
    for r in roles:
        if str(r).strip().lower() == "developer":
            return True
    return False


@app.context_processor
def injetar_helpers_templates():
    """Disponibiliza funções utilitárias para uso direto nos templates."""
    return dict(
        usuario_roles_lista=usuario_roles_lista,
        is_user_developer=is_user_developer,
        formatar_reais=formatar_reais,
    )


# ── Persistência genérica (usada pelos módulos novos) ────────────────────────

# ── Definição Canônica de Abas e Módulos do Sistema ──────────────────────────

SYSTEM_TABS = [
    {
        "id": "estoque",
        "name": "Estoque",
        "emoji": "📦",
        "desc": "Consulta de materiais em estoque, custos e quantidades",
        "actions": ["read", "update", "delete"],
        "route": "estoque",
    },
    {
        "id": "adicionar",
        "name": "Adicionar Material",
        "emoji": "➕",
        "desc": "Cadastro de novos materiais e insumos",
        "actions": ["create"],
        "route": "adicionar",
    },
    {
        "id": "baixa",
        "name": "Dar Baixa",
        "emoji": "✂️",
        "desc": "Registro de saídas manuais e consumo de insumos",
        "actions": ["create"],
        "route": "baixa",
    },
    {
        "id": "produtos",
        "name": "Produtos & Receitas",
        "emoji": "👜",
        "desc": "Catálogo de produtos artesanais e receitas técnicas",
        "actions": ["read", "create", "update", "delete"],
        "route": "produtos",
    },
    {
        "id": "pedidos",
        "name": "Pedidos dos Clientes",
        "emoji": "🧾",
        "desc": "Gestão de pedidos, cálculo de insumos e status",
        "actions": ["read", "create", "update", "delete"],
        "route": "pedidos",
    },
    {
        "id": "sobras",
        "name": "Sobras e Reaproveitamento",
        "emoji": "♻️",
        "desc": "Controle de retalhos, reaproveitamento e descarte",
        "actions": ["read", "create", "update", "delete"],
        "route": "sobras",
    },
    {
        "id": "financeiro",
        "name": "Financeiro",
        "emoji": "💰",
        "desc": "Fluxo de caixa, receitas e despesas",
        "actions": ["read", "create", "delete"],
        "route": "financeiro",
    },
    {
        "id": "relatorios",
        "name": "Alertas e Relatórios",
        "emoji": "📊",
        "desc": "Alertas de estoque mínimo, movimentações e exportações",
        "actions": ["read"],
        "route": "alertas",
    },
    {
        "id": "usuarios",
        "name": "Usuários",
        "emoji": "👥",
        "desc": "Gestão de contas e senhas de usuários",
        "actions": ["read", "create", "update", "delete"],
        "route": "usuarios",
    },
    {
        "id": "roles",
        "name": "Papéis & Permissões",
        "emoji": "🔒",
        "desc": "Configuração de papéis e níveis de acesso por aba",
        "actions": ["read", "create", "update", "delete"],
        "route": "roles",
    },
    {
        "id": "developer",
        "name": "Developer",
        "emoji": "🛠️",
        "desc": "Developer Hub: SSO OAuth2, Ollama IA, Banco de Dados, Auditoria e Logs",
        "actions": ["read", "update"],
        "route": "developer_dashboard",
    },
]


# ── Cache em Memória para Permissões RBAC (Zero Latência) ─────────────────────

_ROLE_PERMS_CACHE = None
_ROLE_PERMS_CACHE_LOCK = threading.Lock()


def invalidate_role_permissions_cache():
    """Invalida o cache em memória de permissões RBAC."""
    global _ROLE_PERMS_CACHE
    with _ROLE_PERMS_CACHE_LOCK:
        _ROLE_PERMS_CACHE = None


def get_cached_role_permissions():
    """Retorna mapa consolidado de permissões por (role, resource) direto da memória RAM."""
    global _ROLE_PERMS_CACHE
    if _ROLE_PERMS_CACHE is not None:
        return _ROLE_PERMS_CACHE
    with _ROLE_PERMS_CACHE_LOCK:
        if _ROLE_PERMS_CACHE is not None:
            return _ROLE_PERMS_CACHE
        cache = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT role, resource, can_create, can_read, can_update, can_delete FROM role_permissions")
            for r in cur.fetchall():
                cache[(r[0], r[1])] = {
                    "can_create": int(r[2] or 0),
                    "can_read": int(r[3] or 0),
                    "can_update": int(r[4] or 0),
                    "can_delete": int(r[5] or 0),
                }
            conn.close()
        except Exception:
            pass
        _ROLE_PERMS_CACHE = cache
        return _ROLE_PERMS_CACHE


# ── Helpers de Verificação de Permissões e Decoradores ───────────────────────

def user_has_permission(resource, action):
    """Verifica se o usuário logado (g.user) tem a permissão para a ação no recurso/aba.
    Considera todos os papéis atribuídos ao usuário (múltiplos papéis são permitidos).
    - Para o recurso 'developer': estritamente exclusivo de quem possui o papel Developer (Admin não acessa).
    - Para os demais recursos: Developer e Admin têm acesso total irrestrito.
    """
    if not g.get("user"):
        return False
    roles = usuario_roles_lista(g.user)
    is_dev = is_user_developer(g.user)

    if resource == "developer":
        return is_dev

    if is_dev or "Admin" in roles:
        return True
    if not roles:
        return False

    col_map = {"create": "can_create", "read": "can_read", "update": "can_update", "delete": "can_delete"}
    col = col_map.get(action, "can_read")

    if USE_SQLITE:
        perms = get_cached_role_permissions()
        known_roles = {k[0] for k in perms.keys()}
        if any(r not in known_roles for r in roles):
            invalidate_role_permissions_cache()
            perms = get_cached_role_permissions()

        for role in roles:
            p = perms.get((role, resource))
            if p and p.get(col) == 1:
                return True
        return False
    else:
        perms_list = carregar_json("role_permissions.json", seed=[])
        for role in roles:
            entry = next((p for p in perms_list if p.get("role") == role and p.get("resource") == resource), None)
            if entry and entry.get(col) == 1:
                return True
    return False


def user_can_access_tab(tab_id):
    """Verifica se o usuário logado tem permissão para visualizar e acessar a aba no menu."""
    if not g.get("user"):
        return False
    roles = usuario_roles_lista(g.user)
    is_dev = is_user_developer(g.user)

    if tab_id == "developer":
        return is_dev

    if is_dev or "Admin" in roles:
        return True
    if not roles:
        return False
    if tab_id in ("adicionar", "baixa"):
        return user_has_permission(tab_id, "create") or user_has_permission(tab_id, "read")
    return user_has_permission(tab_id, "read")


def requires_developer(f):
    """Decorador para proteger rotas e ações restritas estritamente a Desenvolvedores."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("login", next=request.path))
        if not is_user_developer(g.user):
            flash("Acesso negado: esta área técnica é de acesso exclusivo para Desenvolvedores.")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return wrapped


def requires_roles(*allowed_roles):
    """Decorator legado para permitir acesso apenas a papéis específicos, Admin ou Developer."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not g.get("user"):
                return redirect(url_for("login", next=request.path))
            roles = usuario_roles_lista(g.user)
            if is_user_developer(g.user) or "Admin" in roles or any(r in allowed_roles for r in roles):
                return f(*args, **kwargs)
            flash("Acesso negado: você não tem permissão para acessar esta área.")
            return redirect(url_for("home"))
        return wrapped
    return decorator


def requires_permission(resource, action):
    """Decorador para proteger rotas baseado em permissões granulares da tabela role_permissions."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not g.get("user"):
                return redirect(url_for("login", next=request.path))
            if resource == "developer":
                if is_user_developer(g.user):
                    return f(*args, **kwargs)
                flash("Acesso negado: esta área técnica é de acesso exclusivo para Desenvolvedores.")
                return redirect(url_for("home"))
            if is_user_developer(g.user) or "Admin" in usuario_roles_lista(g.user):
                return f(*args, **kwargs)
            if user_has_permission(resource, action):
                return f(*args, **kwargs)
            flash("Acesso negado: você não tem permissão para acessar esta área ou realizar esta ação.")
            return redirect(url_for("home"))
        return wrapped
    return decorator


@app.context_processor
def inject_permissions():
    return dict(
        has_permission=user_has_permission,
        can_access_tab=user_can_access_tab,
        is_user_developer=is_user_developer,
        system_tabs=SYSTEM_TABS,
    )



_db_initialized = False

def init_db(force=False):
    """Initialize SQLite database and tables used by the app when USE_SQLITE is enabled.
    Creates both a generic collections table (legacy) and proper normalized tables for
    materiais, produtos, pedidos, movimentacoes, sobras, despesas and usuarios.
    """
    global _db_initialized
    if _db_initialized and not force:
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(1) FROM roles WHERE is_system=1")
            if cur.fetchone()[0] == 0:
                seed_roles_se_necessario(conn)
            conn.close()
        except Exception:
            pass
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    cur = conn.cursor()
    # Ativa modo WAL para alta concorrência sem travamento de arquivo
    try:
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA busy_timeout = 5000;")
        cur.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    # legacy generic collection storage (used for produtos, pedidos, etc.)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            name TEXT NOT NULL,
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(name)")

    # proper materiais table with explicit columns and uniqueness on (lower(nome), gtin)
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
    # case-insensitive uniqueness on name+gtin to avoid duplicates
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_materiais_nome_gtin ON materiais (lower(nome), gtin)"
    )

    # produtos table
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

    # pedidos table
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
            data_entrega TEXT DEFAULT '',
            google_event_id TEXT DEFAULT '',
            google_calendar_synced_at TEXT DEFAULT '',
            origem TEXT DEFAULT 'web',
            telefone_cliente TEXT DEFAULT '',
            observacoes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # movimentacoes (audit/history)
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_movimentacoes_created ON movimentacoes(created_at)")

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
            nome TEXT,
            avatar TEXT,
            created_at TEXT,
            session_version INTEGER DEFAULT 0
        )
        """
    )
    # Migrations for tables (apenas para bases legadas SQLite; no PostgreSQL o esquema nasce completo)
    if not is_postgres_active():
        for col, ctype in [
            ('role', 'TEXT'),
            ('session_version', 'INTEGER DEFAULT 0'),
            ('nome', 'TEXT'),
            ('avatar', 'TEXT'),
            ('roles', 'TEXT'),
            ('email', 'TEXT'),
            ('google_id', 'TEXT'),
            ('google_refresh_token', 'TEXT'),
            ('google_access_token', 'TEXT'),
            ('google_token_expiry', 'TEXT')
        ]:
            try:
                cur.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {ctype}")
            except Exception:
                pass

        for col, ctype in [('gtin', 'TEXT'), ('estoque_pronto', 'INTEGER DEFAULT 0')]:
            try:
                cur.execute(f"ALTER TABLE produtos ADD COLUMN {col} {ctype}")
            except Exception:
                pass

        for col, ctype in [
            ('usou_estoque_pronto', 'INTEGER DEFAULT 0'),
            ('origem', "TEXT DEFAULT 'web'"),
            ('telefone_cliente', "TEXT DEFAULT ''"),
            ('data_entrega', "TEXT DEFAULT ''"),
            ('google_event_id', "TEXT DEFAULT ''"),
            ('google_calendar_synced_at', "TEXT DEFAULT ''")
        ]:
            try:
                cur.execute(f"ALTER TABLE pedidos ADD COLUMN {col} {ctype}")
            except Exception:
                pass

        for col, ctype in [('usuario_remetente_id', 'TEXT')]:
            try:
                cur.execute(f"ALTER TABLE agendamentos_email ADD COLUMN {col} {ctype}")
            except Exception:
                pass

    # roles table: defines roles and descriptions
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

    # role_permissions table: per-role per-resource CRUD flags
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

    # audits table: records actor, target, action and details for sensitive changes
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

    # relatorios_customizados table: stores user-created custom reports and chart setups
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

    # agendamentos_email table: stores automated email delivery schedules
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
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # historico_envios_email table: stores logs of sent emails
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_historico_envios_created ON historico_envios_email(created_at)")

    # configuracoes_email table: stores SMTP configuration
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

    # configuracoes_sso table: stores Google OAuth credentials and configuration
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

    # configuracoes_cloudinary table: stores Cloudinary credentials configured via Developer Hub
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS configuracoes_cloudinary (
            id TEXT PRIMARY KEY,
            cloud_name TEXT,
            api_key TEXT,
            api_secret TEXT,
            ativo INTEGER DEFAULT 1,
            updated_at TEXT
        )
        """
    )

    # configuracoes_waha table: armazena configurações da API WAHA (WhatsApp HTTP API)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS configuracoes_waha (
            id TEXT PRIMARY KEY,
            api_url TEXT DEFAULT 'http://localhost:3000',
            session_name TEXT DEFAULT 'default',
            api_key TEXT DEFAULT '',
            webhook_secret TEXT DEFAULT '',
            ativo INTEGER DEFAULT 1,
            auto_reply INTEGER DEFAULT 1,
            notificar_admin INTEGER DEFAULT 0,
            status_padrao TEXT DEFAULT 'Pendente',
            updated_at TEXT
        )
        """
    )

    # waha_mensagens table: armazena mensagens recebidas e enviadas via WhatsApp para auditoria
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS waha_mensagens (
            id TEXT PRIMARY KEY,
            chat_id TEXT,
            telefone TEXT,
            nome_contato TEXT,
            direcao TEXT,
            conteudo TEXT,
            tipo_evento TEXT,
            pedido_id TEXT,
            raw_payload TEXT,
            created_at TEXT
        )
        """
    )

    # Migração segura para colunas de rastreamento do WhatsApp na tabela pedidos
    try:
        cur.execute("ALTER TABLE pedidos ADD COLUMN origem TEXT DEFAULT 'web'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE pedidos ADD COLUMN data_entrega TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE pedidos ADD COLUMN google_event_id TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE pedidos ADD COLUMN google_calendar_synced_at TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_data_entrega ON pedidos(data_entrega)")
    except Exception:
        pass

    # Sincroniza variáveis de ambiente a partir do banco caso já existam configuradas
    try:
        cur.execute("SELECT cloud_name, api_key, api_secret FROM configuracoes_cloudinary LIMIT 1")
        r_cld = cur.fetchone()
        if r_cld and r_cld[0] and r_cld[1]:
            if not os.environ.get("CLOUDINARY_CLOUD_NAME"):
                os.environ["CLOUDINARY_CLOUD_NAME"] = r_cld[0]
            if not os.environ.get("CLOUDINARY_API_KEY"):
                os.environ["CLOUDINARY_API_KEY"] = r_cld[1]
            if not os.environ.get("CLOUDINARY_API_SECRET"):
                os.environ["CLOUDINARY_API_SECRET"] = r_cld[2] or ""
            if not os.environ.get("CLOUDINARY_URL") and r_cld[0] and r_cld[1] and r_cld[2]:
                os.environ["CLOUDINARY_URL"] = f"cloudinary://{r_cld[1]}:{r_cld[2]}@{r_cld[0]}"
    except Exception:
        pass

    conn.commit()
    seed_roles_se_necessario(conn)
    criar_usuario_padrao_se_necessario(conn)

    # Normaliza categorias dos materiais para o conjunto canônico, mantendo tudo
    # sincronizado com as categorias existentes (ex.: "Couro" -> "Courino",
    # "Metais" -> "Metal", "Polímero" -> "Outros").
    try:
        cur.execute("SELECT id, nome, categoria FROM materiais")
        for row in cur.fetchall():
            nova = normalizar_categoria(row[2], row[1])
            if nova != row[2]:
                cur.execute("UPDATE materiais SET categoria=?, emoji=? WHERE id=?", (nova, CATEGORIAS_EMOJI.get(nova, "🔹"), row[0]))
        conn.commit()
    except Exception:
        pass

    # Migração única: limpa resíduos de dados de teste e popula itens padrão do sistema
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("SELECT value FROM app_meta WHERE key='seed_padrao_v5_aplicado'")
        if not cur.fetchone():
            if not is_postgres_active():
                cur.execute("DELETE FROM pedidos")
                cur.execute("DELETE FROM sobras")
                cur.execute("DELETE FROM despesas")
                cur.execute("DELETE FROM movimentacoes")
                cur.execute("DELETE FROM relatorios_customizados")
                cur.execute("DELETE FROM produtos")
                cur.execute("DELETE FROM materiais")
                cur.execute("DELETE FROM usuarios WHERE username NOT IN ('admin', 'developer')")
                cur.execute("DELETE FROM roles WHERE is_system = 0")
                cur.execute("DELETE FROM role_permissions WHERE role NOT IN (SELECT name FROM roles WHERE is_system = 1)")
                cur.execute("UPDATE usuarios SET role='Admin', roles=? WHERE username='admin'", (serializar_roles(['Admin']),))

            # Se a tabela de materiais ficou vazia, carrega do SEED_FILE
            if os.path.exists(SEED_FILE):
                try:
                    with open(SEED_FILE, encoding="utf-8") as f:
                        seed_data = json.load(f)
                    now = agora().isoformat()
                    for m in seed_data:
                        _id = m.get("id") or str(uuid.uuid4())
                        cur.execute(
                            "INSERT OR IGNORE INTO materiais (id,nome,categoria,emoji,quantidade,unidade,quantidade_minima,custo,gtin,foto,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (_id, m.get("nome"), m.get("categoria"), m.get("emoji"), float(m.get("quantidade") or 0), m.get("unidade"), float(m.get("quantidade_minima") or 0), float(m.get("custo") or 0), m.get("gtin"), m.get("foto"), now, now)
                        )
                except Exception:
                    pass

            # Se a tabela de produtos ficou vazia, carrega produtos padrão do sistema
            try:
                cur.execute("SELECT COUNT(1) FROM produtos")
                if cur.fetchone()[0] == 0:
                    cur.execute("SELECT id, nome FROM materiais")
                    mat_map = {r[1].lower(): r[0] for r in cur.fetchall()}
                    for p in DEFAULT_PRODUTOS:
                        receita = []
                        for item in p.get("receita", []):
                            mat_id = mat_map.get((item.get("material_nome") or "").lower())
                            if mat_id:
                                receita.append({"material_id": mat_id, "quantidade": float(item.get("quantidade") or 0)})
                        if not receita:
                            continue
                        cur.execute(
                            "INSERT OR IGNORE INTO produtos (id, nome, emoji, preco_venda, receita, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (str(uuid.uuid4()), p["nome"], p["emoji"], float(p["preco_venda"] or 0), json.dumps(receita, ensure_ascii=False), now, now),
                        )
            except Exception:
                pass

            cur.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('seed_padrao_v5_aplicado', '1')")
            conn.commit()
    except Exception:
        pass

    invalidate_role_permissions_cache()
    conn.close()
    _db_initialized = True



def carregar_json(nome_arquivo, seed=None):
    """Load a list of items from JSON file or from SQLite collections table when enabled."""
    if USE_SQLITE:
        name = os.path.splitext(nome_arquivo)[0]
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT data FROM collections WHERE name=? ORDER BY rowid DESC", (name,))
        rows = cur.fetchall()
        conn.close()
        if not rows and seed is not None:
            # seed DB from provided seed value
            salvar_json(nome_arquivo, seed)
            return seed
        return [json.loads(r[0]) for r in rows]

    caminho = os.path.join(DATA_DIR, nome_arquivo)
    if not os.path.exists(caminho):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(seed if seed is not None else [], f, ensure_ascii=False, indent=2)
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def salvar_json(nome_arquivo, dados):
    """Save a list of items into JSON file or into SQLite collections table when enabled.
    Each item must be a dict and will have an id field.
    """
    if USE_SQLITE:
        name = os.path.splitext(nome_arquivo)[0]
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # remove previous collection rows
        cur.execute("DELETE FROM collections WHERE name=?", (name,))
        for item in dados:
            _id = item.get("id") or str(uuid.uuid4())
            item["id"] = _id
            cur.execute("INSERT INTO collections(name, id, data) VALUES (?, ?, ?)", (name, _id, json.dumps(item, ensure_ascii=False)))
        conn.commit()
        conn.close()
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    caminho = os.path.join(DATA_DIR, nome_arquivo)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def registrar_movimentacao(tipo, quantidade, unidade, motivo, material_nome=""):
    """Grava um evento no histórico (usado em Alertas e Relatórios). Mantém só os 200 mais recentes.
    Persiste em tabela movimentacoes quando USE_SQLITE está ativo, e mantém ainda o arquivo JSON legada
    para compatibilidade com código antigo.
    """
    usuario = session.get("user_id") if session else None
    data_text = agora().strftime("%d/%m/%Y %H:%M")

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO movimentacoes (id,tipo,material_nome,quantidade,unidade,motivo,data,usuario,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), tipo, material_nome, float(quantidade or 0), unidade, motivo or "Não informado", data_text, usuario, agora().isoformat()),
            )
            # Keep only 200 latest rows
            cur.execute("DELETE FROM movimentacoes WHERE id NOT IN (SELECT id FROM movimentacoes ORDER BY created_at DESC LIMIT 200)")
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()
        return

    # legacy JSON fallback/update (kept for templates that read movimentacoes.json)
    movs = carregar_json("movimentacoes.json")
    movs.insert(0, {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "material_nome": material_nome,
        "quantidade": float(quantidade or 0),
        "unidade": unidade,
        "motivo": motivo or "Não informado",
        "data": data_text,
        "usuario": usuario,
    })
    salvar_json("movimentacoes.json", movs[:200])


# ── Autenticação simples (usuários em collections 'usuarios') ─────────────────

def carregar_usuarios():
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios ORDER BY username")
        rows = cur.fetchall()
        conn.close()
        return [{
            "id": r["id"],
            "username": r["username"],
            "nome": r["nome"] if ("nome" in r.keys() and r["nome"]) else "",
            "avatar": r["avatar"] if ("avatar" in r.keys() and r["avatar"]) else "",
            "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
            "password_hash": r["password_hash"],
            "role": r["role"] if r["role"] is not None else "",
            "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
            "created_at": r["created_at"],
            "session_version": r["session_version"] if ("session_version" in r.keys()) else 0,
        } for r in rows]
    # legacy JSON fallback: each user dict may include role, nome, avatar, email
    users = carregar_json("usuarios.json", seed=[])
    for u in users:
        if "session_version" not in u:
            u["session_version"] = 0
        if "nome" not in u:
            u["nome"] = ""
        if "avatar" not in u:
            u["avatar"] = ""
        if "email" not in u:
            u["email"] = ""
        if "roles" not in u:
            u["roles"] = ""
    return users


def salvar_usuarios(usuarios):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("DELETE FROM usuarios")
            now = agora().isoformat()
            for u in usuarios:
                _id = u.get("id") or str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO usuarios (id, username, password_hash, role, nome, avatar, roles, email, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_id, u.get("username"), u.get("password_hash"), u.get("role") or "", u.get("nome") or "", u.get("avatar") or "", serializar_roles(u.get("roles") or u.get("role") or ""), u.get("email") or "", u.get("created_at") or now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return
    return salvar_json("usuarios.json", usuarios)


def encontrar_usuario_por_username(username):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username=?", (username,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "id": r["id"],
            "username": r["username"],
            "nome": r["nome"] if ("nome" in r.keys() and r["nome"]) else "",
            "avatar": r["avatar"] if ("avatar" in r.keys() and r["avatar"]) else "",
            "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
            "password_hash": r["password_hash"],
            "role": r["role"] if r["role"] is not None else "",
            "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
            "created_at": r["created_at"],
            "session_version": r["session_version"] if ("session_version" in r.keys()) else 0,
        }

    usuarios = carregar_usuarios()
    for u in usuarios:
        if u.get("username") == username:
            return u
    return None


def encontrar_usuario_por_id(user_id):
    if not user_id:
        return None
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE id=?", (str(user_id),))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "id": r["id"],
            "username": r["username"],
            "nome": r["nome"] if ("nome" in r.keys() and r["nome"]) else "",
            "avatar": r["avatar"] if ("avatar" in r.keys() and r["avatar"]) else "",
            "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
            "google_id": r["google_id"] if ("google_id" in r.keys() and r["google_id"]) else "",
            "google_refresh_token": r["google_refresh_token"] if ("google_refresh_token" in r.keys() and r["google_refresh_token"]) else "",
            "google_access_token": r["google_access_token"] if ("google_access_token" in r.keys() and r["google_access_token"]) else "",
            "google_token_expiry": r["google_token_expiry"] if ("google_token_expiry" in r.keys() and r["google_token_expiry"]) else "",
            "password_hash": r["password_hash"],
            "role": r["role"] if r["role"] is not None else "",
            "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
            "created_at": r["created_at"],
            "session_version": r["session_version"] if ("session_version" in r.keys()) else 0,
        }

    usuarios = carregar_usuarios()
    return next((u for u in usuarios if str(u.get("id")) == str(user_id)), None)


def encontrar_usuario_por_email(email):
    if not email:
        return None
    email_clean = email.strip().lower()
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE LOWER(email)=?", (email_clean,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "id": r["id"],
            "username": r["username"],
            "nome": r["nome"] if ("nome" in r.keys() and r["nome"]) else "",
            "avatar": r["avatar"] if ("avatar" in r.keys() and r["avatar"]) else "",
            "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
            "google_id": r["google_id"] if ("google_id" in r.keys() and r["google_id"]) else "",
            "google_refresh_token": r["google_refresh_token"] if ("google_refresh_token" in r.keys() and r["google_refresh_token"]) else "",
            "google_access_token": r["google_access_token"] if ("google_access_token" in r.keys() and r["google_access_token"]) else "",
            "google_token_expiry": r["google_token_expiry"] if ("google_token_expiry" in r.keys() and r["google_token_expiry"]) else "",
            "password_hash": r["password_hash"],
            "role": r["role"] if r["role"] is not None else "",
            "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
            "created_at": r["created_at"],
            "session_version": r["session_version"] if ("session_version" in r.keys()) else 0,
        }

    usuarios = carregar_usuarios()
    for u in usuarios:
        if (u.get("email") or "").strip().lower() == email_clean:
            return u
    return None


def encontrar_usuario_por_google_id(google_id):
    if not google_id:
        return None
    gid_str = str(google_id).strip()
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE google_id=?", (gid_str,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "id": r["id"],
            "username": r["username"],
            "nome": r["nome"] if ("nome" in r.keys() and r["nome"]) else "",
            "avatar": r["avatar"] if ("avatar" in r.keys() and r["avatar"]) else "",
            "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
            "google_id": r["google_id"] if ("google_id" in r.keys() and r["google_id"]) else "",
            "google_refresh_token": r["google_refresh_token"] if ("google_refresh_token" in r.keys() and r["google_refresh_token"]) else "",
            "google_access_token": r["google_access_token"] if ("google_access_token" in r.keys() and r["google_access_token"]) else "",
            "google_token_expiry": r["google_token_expiry"] if ("google_token_expiry" in r.keys() and r["google_token_expiry"]) else "",
            "password_hash": r["password_hash"],
            "role": r["role"] if r["role"] is not None else "",
            "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
            "created_at": r["created_at"],
            "session_version": r["session_version"] if ("session_version" in r.keys()) else 0,
        }

    usuarios = carregar_usuarios()
    for u in usuarios:
        if str(u.get("google_id") or "").strip() == gid_str:
            return u
    return None


def obter_access_token_gmail_usuario(user_id_ou_dict):
    """
    Recupera um access_token válido para a API do Gmail do usuário.
    Se o access_token estiver expirado mas houver refresh_token, renova automaticamente no Google.
    """
    if isinstance(user_id_ou_dict, dict):
        user = user_id_ou_dict
    else:
        user = encontrar_usuario_por_id(user_id_ou_dict)

    if not user:
        return {"success": False, "reason": "user_not_found"}

    user_id = user.get("id")
    email = user.get("email") or user.get("username")
    nome = user.get("nome") or user.get("username")
    access_token = user.get("google_access_token")
    refresh_token = user.get("google_refresh_token")
    expiry_str = user.get("google_token_expiry")

    # Verifica se o access_token atual ainda é válido (com margem de 60s)
    if access_token and expiry_str:
        try:
            exp_dt = datetime.fromisoformat(expiry_str)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt > datetime.now(timezone.utc) + timedelta(seconds=60):
                return {"success": True, "access_token": access_token, "email": email, "nome": nome}
        except Exception:
            pass

    # Se não temos refresh_token, não é possível renovar
    if not refresh_token:
        return {"success": False, "reason": "no_refresh_token", "email": email, "nome": nome}

    # Renova token usando refresh_token
    cfg = obter_configuracoes_sso()
    client_id = cfg.get("google_client_id")
    client_secret = cfg.get("google_client_secret")

    if not client_id or not client_secret:
        return {"success": False, "reason": "sso_not_configured", "email": email, "nome": nome}

    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            },
            timeout=15
        )
        if token_resp.status_code == 200:
            token_data = token_resp.json()
            new_access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)
            new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            if USE_SQLITE and user_id:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute(
                    "UPDATE usuarios SET google_access_token=?, google_token_expiry=? WHERE id=?",
                    (new_access_token, new_expiry, user_id)
                )
                conn.commit()
                conn.close()

            return {"success": True, "access_token": new_access_token, "email": email, "nome": nome}
        else:
            return {"success": False, "reason": f"refresh_failed: {token_resp.text}", "email": email, "nome": nome}
    except Exception as e:
        return {"success": False, "reason": str(e), "email": email, "nome": nome}


# ── Google Calendar API Integration ──────────────────────────────────────────

def obter_access_token_google_usuario(user_id_ou_dict=None):
    """
    Recupera um access_token válido para as APIs Google (Gmail e Calendar).
    Se o usuário atual não possuir token, busca o primeiro usuário do sistema
    (preferencialmente Admin/Developer) que possui Google conectado.
    Renova automaticamente o token caso expirado.
    """
    user = None
    if user_id_ou_dict:
        if isinstance(user_id_ou_dict, dict):
            user = user_id_ou_dict
        else:
            user = encontrar_usuario_por_id(user_id_ou_dict)
    elif g.get("user"):
        user = g.user
    elif session.get("user_id"):
        user = encontrar_usuario_por_id(session.get("user_id"))

    # Fallback: primeiro usuário com google_refresh_token configurado
    if not user or not (user.get("google_refresh_token") or user.get("google_access_token")):
        if USE_SQLITE:
            try:
                init_db()
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM usuarios WHERE google_refresh_token IS NOT NULL AND google_refresh_token != '' ORDER BY created_at ASC LIMIT 1")
                r = cur.fetchone()
                conn.close()
                if r:
                    user = dict(r)
            except Exception:
                pass
        else:
            for u in carregar_json("usuarios.json"):
                if u.get("google_refresh_token"):
                    user = u
                    break

    if not user:
        return {"success": False, "reason": "no_google_user_found"}

    return obter_access_token_gmail_usuario(user)


def _atualizar_pedido_sync_calendar(pedido_id, google_event_id, sync_time_iso):
    """Atualiza o google_event_id e timestamp de sincronização no pedido."""
    if not pedido_id:
        return
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "UPDATE pedidos SET google_event_id=?, google_calendar_synced_at=? WHERE id=?",
                (google_event_id, sync_time_iso, pedido_id)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    else:
        pedidos = carregar_json("pedidos.json")
        for p in pedidos:
            if p.get("id") == pedido_id:
                p["google_event_id"] = google_event_id
                p["google_calendar_synced_at"] = sync_time_iso
                break
        salvar_json("pedidos.json", pedidos)


def criar_ou_atualizar_evento_google_calendar(pedido, user_id=None):
    """
    Cria ou atualiza um evento na agenda primária do Google Calendar referente à data de entrega do pedido.
    """
    if not pedido or not pedido.get("data_entrega"):
        return {"success": False, "reason": "missing_delivery_date"}

    data_entrega = pedido.get("data_entrega").strip()
    # Converte DD/MM/YYYY para YYYY-MM-DD se necessário
    if "/" in data_entrega:
        try:
            partes = data_entrega.split("/")
            if len(partes) == 3:
                data_entrega = f"{partes[2]}-{partes[1].zfill(2)}-{partes[0].zfill(2)}"
        except Exception:
            pass

    token_info = obter_access_token_google_usuario(user_id)
    if not token_info.get("success"):
        return token_info

    access_token = token_info.get("access_token")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    cliente = pedido.get("cliente") or "Cliente"
    produto_nome = pedido.get("produto_nome") or "Bolsa"
    emoji = pedido.get("produto_emoji") or "👜"
    status = pedido.get("status") or "Pendente"
    qtd = pedido.get("quantidade") or 1
    total = float(pedido.get("valor_total") or 0.0)
    obs = pedido.get("observacoes") or ""

    status_prefix = ""
    if status == "Entregue":
        status_prefix = "✅ [Entregue] "
    elif status == "Concluído":
        status_prefix = "✨ [Concluído] "
    elif status == "Cancelado":
        status_prefix = "❌ [Cancelado] "

    summary = f"{status_prefix}{emoji} Entrega: {cliente} ({produto_nome})"
    description = (
        f"🧵 Ateliê Haiti — Entrega de Pedido\n\n"
        f"• Cliente: {cliente}\n"
        f"• Produto: {emoji} {produto_nome} (x{qtd})\n"
        f"• Valor Total: R$ {total:.2f}\n"
        f"• Status: {status}\n"
    )
    if obs:
        description += f"• Observações: {obs}\n"
    description += f"\nID do Pedido: {pedido.get('id')}"

    event_body = {
        "summary": summary,
        "description": description,
        "start": {"date": data_entrega},
        "end": {"date": data_entrega},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 24 * 60},
                {"method": "popup", "minutes": 9 * 60}
            ]
        }
    }

    google_event_id = pedido.get("google_event_id")
    endpoint = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    now_iso = agora().isoformat()

    try:
        if google_event_id:
            resp = requests.patch(
                f"{endpoint}/{google_event_id}",
                headers=headers,
                json=event_body,
                timeout=15
            )
            if resp.status_code == 200:
                event_data = resp.json()
                _atualizar_pedido_sync_calendar(pedido.get("id"), google_event_id, now_iso)
                return {"success": True, "action": "updated", "event_id": google_event_id, "htmlLink": event_data.get("htmlLink")}
            elif resp.status_code != 404:
                return {"success": False, "status_code": resp.status_code, "error": resp.text}

        resp = requests.post(
            endpoint,
            headers=headers,
            json=event_body,
            timeout=15
        )
        if resp.status_code in (200, 201):
            event_data = resp.json()
            new_event_id = event_data.get("id")
            _atualizar_pedido_sync_calendar(pedido.get("id"), new_event_id, now_iso)
            return {"success": True, "action": "created", "event_id": new_event_id, "htmlLink": event_data.get("htmlLink")}
        else:
            return {"success": False, "status_code": resp.status_code, "error": resp.text}
    except Exception as e:
        return {"success": False, "error": str(e)}


def excluir_evento_google_calendar(google_event_id, user_id=None):
    """Remove um evento da agenda primária do Google Calendar."""
    if not google_event_id:
        return {"success": False, "reason": "no_event_id"}

    token_info = obter_access_token_google_usuario(user_id)
    if not token_info.get("success"):
        return token_info

    access_token = token_info.get("access_token")
    headers = {"Authorization": f"Bearer {access_token}"}
    endpoint = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}"

    try:
        resp = requests.delete(endpoint, headers=headers, timeout=15)
        if resp.status_code in (200, 204, 404):
            return {"success": True}
        return {"success": False, "status_code": resp.status_code, "error": resp.text}
    except Exception as e:
        return {"success": False, "error": str(e)}


def sincronizar_todos_pedidos_google_calendar(user_id=None):
    """
    Sincroniza todos os pedidos ativos que possuem data de entrega com o Google Calendar.
    """
    pedidos_lista = []
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos WHERE data_entrega IS NOT NULL AND data_entrega != '' AND status != 'Cancelado'")
        rows = cur.fetchall()
        conn.close()
        for r in rows:
            pedidos_lista.append(dict(r))
    else:
        pedidos_lista = [p for p in carregar_json("pedidos.json") if p.get("data_entrega") and p.get("status") != "Cancelado"]

    total = len(pedidos_lista)
    sucessos = 0
    erros = 0
    detalhes_erros = []

    for p in pedidos_lista:
        res = criar_ou_atualizar_evento_google_calendar(p, user_id)
        if res.get("success"):
            sucessos += 1
        else:
            erros += 1
            detalhes_erros.append(f"{p.get('cliente')}: {res.get('reason') or res.get('error') or 'Falha'}")

    return {
        "total": total,
        "sucessos": sucessos,
        "erros": erros,
        "detalhes_erros": detalhes_erros
    }


def verificar_conexao_google_calendar(user_id=None):
    """Verifica se há conexão ativa com o Google Calendar."""
    token_info = obter_access_token_google_usuario(user_id)
    if not token_info.get("success"):
        return {"conectado": False, "motivo": token_info.get("reason", "Sem autorização Google")}

    access_token = token_info.get("access_token")
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        resp = requests.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary",
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            cal = resp.json()
            return {
                "conectado": True,
                "email": token_info.get("email") or cal.get("id"),
                "summary": cal.get("summary"),
                "timeZone": cal.get("timeZone")
            }
        elif resp.status_code in (401, 403):
            return {"conectado": False, "motivo": "Permissão do Google Calendar pendente. Reconecte via Google SSO."}
        else:
            return {"conectado": False, "motivo": f"Erro {resp.status_code}"}
    except Exception as e:
        return {"conectado": False, "motivo": str(e)}


def obter_configuracoes_sso():
    """Retorna as configurações atuais do Google SSO."""
    default_cfg = {
        "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        "google_client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        "ativo": 1 if os.environ.get("GOOGLE_SSO_ATIVO") == "1" or os.environ.get("GOOGLE_CLIENT_ID") else 0,
        "auto_cadastro": 1 if os.environ.get("GOOGLE_AUTO_CADASTRO", "1") == "1" else 0,
        "papel_padrao": os.environ.get("GOOGLE_PAPEL_PADRAO", "Producao").strip(),
    }
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM configuracoes_sso LIMIT 1")
        r = cur.fetchone()
        conn.close()
        if r:
            return {
                "google_client_id": r["google_client_id"] or default_cfg["google_client_id"],
                "google_client_secret": r["google_client_secret"] or default_cfg["google_client_secret"],
                "ativo": int(r["ativo"]) if r["ativo"] is not None else default_cfg["ativo"],
                "auto_cadastro": int(r["auto_cadastro"]) if r["auto_cadastro"] is not None else default_cfg["auto_cadastro"],
                "papel_padrao": r["papel_padrao"] or default_cfg["papel_padrao"],
            }
    return default_cfg


def salvar_configuracoes_sso(cfg):
    """Persiste as configurações de Google SSO no SQLite."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = agora().isoformat()
        cur.execute("DELETE FROM configuracoes_sso")
        cur.execute(
            """
            INSERT INTO configuracoes_sso 
            (id, google_client_id, google_client_secret, ativo, auto_cadastro, papel_padrao, updated_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default",
                cfg.get("google_client_id", "").strip(),
                cfg.get("google_client_secret", "").strip(),
                int(cfg.get("ativo", 0)),
                int(cfg.get("auto_cadastro", 1)),
                cfg.get("papel_padrao", "Producao").strip(),
                now
            )
        )
        conn.commit()
        conn.close()


def obter_configuracoes_cloudinary():
    """Recupera as configurações de Cloudinary do SQLite ou das variáveis de ambiente."""
    default_cfg = {
        "cloud_name": os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip(),
        "api_key": os.environ.get("CLOUDINARY_API_KEY", "").strip(),
        "api_secret": os.environ.get("CLOUDINARY_API_SECRET", "").strip(),
        "ativo": 1 if os.environ.get("CLOUDINARY_CLOUD_NAME") else 0,
    }
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM configuracoes_cloudinary LIMIT 1")
            r = cur.fetchone()
            conn.close()
            if r and (r["cloud_name"] or r["api_key"]):
                cfg = {
                    "cloud_name": r["cloud_name"] or "",
                    "api_key": r["api_key"] or "",
                    "api_secret": r["api_secret"] or "",
                    "ativo": int(r["ativo"]) if r["ativo"] is not None else 1,
                }
                if cfg["cloud_name"]:
                    os.environ["CLOUDINARY_CLOUD_NAME"] = cfg["cloud_name"]
                if cfg["api_key"]:
                    os.environ["CLOUDINARY_API_KEY"] = cfg["api_key"]
                if cfg["api_secret"]:
                    os.environ["CLOUDINARY_API_SECRET"] = cfg["api_secret"]
                if cfg["cloud_name"] and cfg["api_key"] and cfg["api_secret"]:
                    os.environ["CLOUDINARY_URL"] = f"cloudinary://{cfg['api_key']}:{cfg['api_secret']}@{cfg['cloud_name']}"
                return cfg
        except Exception:
            pass
    return default_cfg


def salvar_configuracoes_cloudinary(cfg):
    """Persiste as configurações de Cloudinary no banco de dados e atualiza o ambiente."""
    cloud_name = cfg.get("cloud_name", "").strip()
    api_key = cfg.get("api_key", "").strip()
    api_secret = cfg.get("api_secret", "").strip()
    ativo = int(cfg.get("ativo", 1))

    # Atualiza em memória e os.environ imediatamente
    os.environ["CLOUDINARY_CLOUD_NAME"] = cloud_name
    os.environ["CLOUDINARY_API_KEY"] = api_key
    os.environ["CLOUDINARY_API_SECRET"] = api_secret
    if cloud_name and api_key and api_secret:
        os.environ["CLOUDINARY_URL"] = f"cloudinary://{api_key}:{api_secret}@{cloud_name}"
    else:
        os.environ.pop("CLOUDINARY_URL", None)

    # Reconfigura o serviço
    cloudinary_service.reconfigurar_cloudinary(cloud_name, api_key, api_secret)

    # Persiste no banco de dados
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = agora().isoformat()
        cur.execute("DELETE FROM configuracoes_cloudinary")
        cur.execute(
            """
            INSERT INTO configuracoes_cloudinary (id, cloud_name, api_key, api_secret, ativo, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("default", cloud_name, api_key, api_secret, ativo, now)
        )
        conn.commit()
        conn.close()

    # Tenta salvar no arquivo .env se for gravável
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = []
            keys_seen = set()
            for line in lines:
                if line.startswith("CLOUDINARY_CLOUD_NAME="):
                    new_lines.append(f"CLOUDINARY_CLOUD_NAME={cloud_name}\n")
                    keys_seen.add("CLOUDINARY_CLOUD_NAME")
                elif line.startswith("CLOUDINARY_API_KEY="):
                    new_lines.append(f"CLOUDINARY_API_KEY={api_key}\n")
                    keys_seen.add("CLOUDINARY_API_KEY")
                elif line.startswith("CLOUDINARY_API_SECRET="):
                    new_lines.append(f"CLOUDINARY_API_SECRET={api_secret}\n")
                    keys_seen.add("CLOUDINARY_API_SECRET")
                elif line.startswith("CLOUDINARY_URL="):
                    if cloud_name and api_key and api_secret:
                        new_lines.append(f"CLOUDINARY_URL=cloudinary://{api_key}:{api_secret}@{cloud_name}\n")
                    keys_seen.add("CLOUDINARY_URL")
                else:
                    new_lines.append(line)
            if "CLOUDINARY_CLOUD_NAME" not in keys_seen and cloud_name:
                new_lines.append(f"CLOUDINARY_CLOUD_NAME={cloud_name}\n")
            if "CLOUDINARY_API_KEY" not in keys_seen and api_key:
                new_lines.append(f"CLOUDINARY_API_KEY={api_key}\n")
            if "CLOUDINARY_API_SECRET" not in keys_seen and api_secret:
                new_lines.append(f"CLOUDINARY_API_SECRET={api_secret}\n")
            if "CLOUDINARY_URL" not in keys_seen and cloud_name and api_key and api_secret:
                new_lines.append(f"CLOUDINARY_URL=cloudinary://{api_key}:{api_secret}@{cloud_name}\n")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
    except Exception:
        pass


def seed_roles_se_necessario(conn=None):
    """Garante a existência dos papéis padrão e suas permissões canônicas."""
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_at_end = True
    cur = conn.cursor()

    now = agora().isoformat()
    default_roles_data = [
        ("Developer", "Acesso irrestrito ao sistema e controle exclusivo de configurações avançadas e infraestrutura", 1, {
            "developer": (1, 1, 1, 1),
            "estoque": (1, 1, 1, 1),
            "adicionar": (1, 1, 1, 1),
            "baixa": (1, 1, 1, 1),
            "produtos": (1, 1, 1, 1),
            "pedidos": (1, 1, 1, 1),
            "sobras": (1, 1, 1, 1),
            "financeiro": (1, 1, 1, 1),
            "relatorios": (1, 1, 1, 1),
            "usuarios": (1, 1, 1, 1),
            "roles": (1, 1, 1, 1),
        }),
        ("Admin", "Acesso irrestrito a todas as áreas, abas de negócio e usuários do sistema", 1, {
            "developer": (0, 0, 0, 0),
            "estoque": (1, 1, 1, 1),
            "adicionar": (1, 1, 1, 1),
            "baixa": (1, 1, 1, 1),
            "produtos": (1, 1, 1, 1),
            "pedidos": (1, 1, 1, 1),
            "sobras": (1, 1, 1, 1),
            "financeiro": (1, 1, 1, 1),
            "relatorios": (1, 1, 1, 1),
            "usuarios": (1, 1, 1, 1),
            "roles": (1, 1, 1, 1),
        }),
        ("Estoque", "Gestão de insumos, materiais, baixas manuais e sobras", 1, {
            "estoque": (1, 1, 1, 1),
            "adicionar": (1, 1, 0, 0),
            "baixa": (1, 1, 0, 0),
            "sobras": (1, 1, 1, 1),
            "produtos": (0, 1, 0, 0),
            "relatorios": (0, 1, 0, 0),
        }),
        ("Vendas", "Gestão comercial de pedidos de clientes e catálogo de produtos", 1, {
            "pedidos": (1, 1, 1, 1),
            "produtos": (0, 1, 0, 0),
            "estoque": (0, 1, 0, 0),
            "relatorios": (0, 1, 0, 0),
        }),
        ("Producao", "Acompanhamento da confecção de pedidos, receitas e baixa de insumos", 1, {
            "pedidos": (1, 1, 1, 0),
            "produtos": (1, 1, 1, 0),
            "estoque": (0, 1, 1, 0),
            "baixa": (1, 1, 0, 0),
            "sobras": (1, 1, 1, 0),
        }),
        ("Financeiro", "Fluxo financeiro, controle de despesas, faturamento e relatórios", 1, {
            "financeiro": (1, 1, 1, 1),
            "relatorios": (0, 1, 0, 0),
            "pedidos": (0, 1, 0, 0),
            "estoque": (0, 1, 0, 0),
        }),
        ("Relatorios", "Acesso analítico a alertas, relatórios gerenciais e exportações", 1, {
            "relatorios": (0, 1, 0, 0),
            "financeiro": (0, 1, 0, 0),
            "estoque": (0, 1, 0, 0),
        }),
    ]

    for name, desc, is_sys, perms in default_roles_data:
        cur.execute("SELECT id FROM roles WHERE name=?", (name,))
        existing = cur.fetchone()
        if not existing:
            role_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO roles (id, name, description, is_system, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (role_id, name, desc, is_sys, now, now),
            )
        for res, (c, r, u, d) in perms.items():
            cur.execute(
                "INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, res, c, r, u, d, now),
            )
    conn.commit()
    invalidate_role_permissions_cache()

    if close_at_end:
        conn.close()


def carregar_papeis():
    """Retorna a lista de papéis com detalhes, permissões e contagem de usuários."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM roles ORDER BY CASE WHEN name='Developer' THEN 0 WHEN name='Admin' THEN 1 ELSE 2 END, name")
        roles_rows = cur.fetchall()

        cur.execute("SELECT role, COUNT(1) as cnt FROM usuarios GROUP BY role")
        user_counts = {r["role"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("SELECT role, resource, can_create, can_read, can_update, can_delete FROM role_permissions")
        perms_by_role = {}
        for r in cur.fetchall():
            perms_by_role.setdefault(r["role"], {})[r["resource"]] = {
                "can_create": bool(r["can_create"]),
                "can_read": bool(r["can_read"]),
                "can_update": bool(r["can_update"]),
                "can_delete": bool(r["can_delete"]),
            }
        conn.close()

        result = []
        for r in roles_rows:
            r_name = r["name"]
            perms = perms_by_role.get(r_name, {})
            allowed_tabs = []
            for tab in SYSTEM_TABS:
                t_id = tab["id"]
                p = perms.get(t_id, {})
                if r_name == "Developer":
                    allowed_tabs.append(tab)
                elif r_name == "Admin":
                    if t_id != "developer":
                        allowed_tabs.append(tab)
                elif p.get("can_read") or (t_id in ("adicionar", "baixa") and p.get("can_create")):
                    allowed_tabs.append(tab)

            result.append({
                "id": r["id"],
                "name": r["name"],
                "description": r["description"] or "",
                "is_system": bool(r["is_system"]),
                "created_at": r["created_at"],
                "user_count": user_counts.get(r_name, 0),
                "permissions": perms,
                "allowed_tabs": allowed_tabs,
                "total_tabs": len(SYSTEM_TABS),
            })
        return result

    # Fallback JSON
    roles = carregar_json("roles.json", seed=[])
    if not roles:
        roles = [
            {"id": "admin-id", "name": "Admin", "description": "Acesso total", "is_system": True, "created_at": agora().isoformat()},
            {"id": "est-id", "name": "Estoque", "description": "Estoque e insumos", "is_system": True, "created_at": agora().isoformat()},
            {"id": "fin-id", "name": "Financeiro", "description": "Financeiro", "is_system": True, "created_at": agora().isoformat()},
        ]
        salvar_json("roles.json", roles)
    return roles


def encontrar_papel(role_name):
    """Retorna o objeto do papel e seu mapa de permissões."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM roles WHERE name=?", (role_name,))
        r = cur.fetchone()
        if not r:
            conn.close()
            return None
        cur.execute("SELECT resource, can_create, can_read, can_update, can_delete FROM role_permissions WHERE role=?", (role_name,))
        perms = {}
        for p in cur.fetchall():
            perms[p["resource"]] = {
                "can_create": bool(p["can_create"]),
                "can_read": bool(p["can_read"]),
                "can_update": bool(p["can_update"]),
                "can_delete": bool(p["can_delete"]),
            }
        conn.close()
        return {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"] or "",
            "is_system": bool(r["is_system"]),
            "created_at": r["created_at"],
            "permissions": perms,
        }
    papeis = carregar_papeis()
    return next((p for p in papeis if p.get("name") == role_name), None)


def criar_usuario_padrao_se_necessario(conn=None):
    now = agora().isoformat()
    if USE_SQLITE:
        close_at_end = False
        if conn is None:
            conn = sqlite3.connect(DB_PATH)
            close_at_end = True
        cur = conn.cursor()

        # Garante usuário admin padrão
        cur.execute("SELECT id FROM usuarios WHERE username='admin'")
        if not cur.fetchone():
            senha = os.environ.get("ADMIN_PASSWORD", "admin")
            uid = str(uuid.uuid4())
            cur.execute("INSERT INTO usuarios (id,username,password_hash,role,roles,nome,created_at) VALUES (?,?,?,?,?,?,?)",
                        (uid, 'admin', generate_password_hash(senha), 'Admin', serializar_roles(['Admin']), 'Administrador', now))
            conn.commit()

        # Garante usuário developer padrão
        cur.execute("SELECT id FROM usuarios WHERE username='developer'")
        row_dev = cur.fetchone()
        dev_senha = os.environ.get("DEV_PASSWORD", "developer")
        if not row_dev:
            dev_uid = str(uuid.uuid4())
            cur.execute("INSERT INTO usuarios (id,username,password_hash,role,roles,nome,created_at) VALUES (?,?,?,?,?,?,?)",
                        (dev_uid, 'developer', generate_password_hash(dev_senha), 'Developer', serializar_roles(['Developer']), 'Desenvolvedor', now))
            conn.commit()
        else:
            cur.execute("UPDATE usuarios SET role='Developer', roles=? WHERE username='developer'", (serializar_roles(['Developer']),))
            conn.commit()

        if close_at_end:
            conn.close()
        return

    usuarios = carregar_usuarios()
    tem_admin = any(u.get("username") == "admin" for u in usuarios)
    tem_dev = any(u.get("username") == "developer" for u in usuarios)

    if not tem_admin:
        senha = os.environ.get("ADMIN_PASSWORD", "admin")
        usuarios.append({
            "id": str(uuid.uuid4()),
            "username": "admin",
            "nome": "Administrador",
            "password_hash": generate_password_hash(senha),
            "role": 'Admin',
            "roles": ['Admin'],
            "created_at": now,
        })
    if not tem_dev:
        dev_senha = os.environ.get("DEV_PASSWORD", "developer")
        usuarios.append({
            "id": str(uuid.uuid4()),
            "username": "developer",
            "nome": "Desenvolvedor",
            "password_hash": generate_password_hash(dev_senha),
            "role": 'Developer',
            "roles": ['Developer'],
            "created_at": now,
        })
    salvar_usuarios(usuarios)


criar_usuario_padrao_se_necessario()


def recreate_db_with_admin_forced():
    """If RECREATE_DB env var is set (1/true/yes), delete and recreate the SQLite DB and
    inject an 'admin' user with a generated password. Writes credentials to data/admin_credentials.txt
    so the operator can retrieve the password after starting the app.
    """
    if not USE_SQLITE:
        return
    if os.environ.get('RECREATE_DB', '').lower() not in ('1', 'true', 'yes'):
        return
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    except Exception:
        pass
    # Recreate schema
    init_db()
    pwd = secrets.token_urlsafe(10)
    uid = str(uuid.uuid4())
    now = agora().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("INSERT OR REPLACE INTO usuarios (id,username,password_hash,role,roles,created_at) VALUES (?,?,?,?,?,?)", (uid, 'admin', generate_password_hash(pwd), 'Admin', serializar_roles(['Admin']), now))
        conn.commit()
    finally:
        conn.close()
    os.makedirs(DATA_DIR, exist_ok=True)
    cred_path = os.path.join(DATA_DIR, 'admin_credentials.txt')
    try:
        with open(cred_path, 'w', encoding='utf-8') as f:
            f.write(f"username: admin\npassword: {pwd}\ncreated_at: {now}\n")
    except Exception:
        pass
    # Also print so logs/console show it when app starts
    print(f"RECREATE_DB: created {DB_PATH} and wrote admin credentials to {cred_path}")

# If the deploy/start command explicitly asks for recreation, do it now
if os.environ.get('RECREATE_DB', '').lower() in ('1','true','yes'):
    recreate_db_with_admin_forced()


@app.before_request
def require_login():
    # Allow these endpoints unauthenticated
    allowed = {"login", "static", "em_construcao", "uploaded_file", "auth_google_login", "auth_google_callback", "waha_webhook", "api_pedidos_ultimos"}
    if request.endpoint is None:
        return
    if request.endpoint in allowed:
        return
    # load user into g for template access and role checks
    if session.get("user_id"):
        user_id = session.get("user_id")
        # try to find user by id
        user = None
        if USE_SQLITE:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM usuarios WHERE id=?", (user_id,))
            r = cur.fetchone()
            conn.close()
            if r:
                user = {
                    "id": r["id"],
                    "username": r["username"],
                    "nome": r["nome"] if ("nome" in r.keys() and r["nome"]) else "",
                    "avatar": r["avatar"] if ("avatar" in r.keys() and r["avatar"]) else "",
                    "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
                    "google_id": r["google_id"] if ("google_id" in r.keys() and r["google_id"]) else "",
                    "role": r["role"] if r["role"] is not None else "",
                    "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
                    "session_version": r["session_version"] if ("session_version" in r.keys()) else 0,
                }
        else:
            usuarios = carregar_usuarios()
            user = next((u for u in usuarios if u.get("id") == user_id), None)
        if user is None:
            session.pop('user_id', None)
            session.pop('session_version', None)
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized", "denied": True, "reply": "🔒 Usuário não encontrado.", "voice_text": "Usuário não encontrado."}), 401
            flash('Sessão expirada. Por favor faça login novamente.')
            return redirect(url_for('login'))

        # session invalidation: compare session_version stored in session with DB; if mismatch, force logout
        db_ver = (user.get('session_version') if user else 0)
        sess_ver = session.get('session_version')
        if sess_ver is None:
            # Se for uma sessão ativa válida sem versionamento explícito, sincroniza suavemente sem desconectar
            session['session_version'] = db_ver
            sess_ver = db_ver

        if sess_ver != db_ver:
            # expire session
            session.pop('user_id', None)
            session.pop('session_version', None)
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized", "denied": True, "reply": "🔒 Sessão expirada. Por favor faça login novamente.", "voice_text": "Sua sessão expirou."}), 401
            flash('Sessão expirada. Por favor faça login novamente.')
            return redirect(url_for('login'))
        g.user = user
        return

    if request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized", "denied": True, "reply": "🔒 Sessão Necessária: Por favor, faça login para continuar.", "voice_text": "Por favor, faça login para utilizar a assistente."}), 401

    return redirect(url_for("login", next=request.path))


@app.after_request
def add_browser_cache_headers(response):
    """Adiciona cabeçalhos de cache para assets estáticos e uploads, acelerando navegação."""
    if response.status_code in (200, 304):
        path = request.path
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        elif path.startswith("/uploads/"):
            response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    config_sso = obter_configuracoes_sso()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        senha = request.form.get("password", "")
        user = encontrar_usuario_por_username(username)
        if user:
            stored = user.get("password_hash", "")
            # Backward/compatibility: allow a stored plaintext marker 'PLAIN:' for quick local setup
            if stored.startswith('PLAIN:'):
                if stored[len('PLAIN:'):] == senha:
                    session["user_id"] = user["id"]
                    # persist session version to allow server-side invalidation
                    session['session_version'] = user.get('session_version', 0) if isinstance(user, dict) else 0
                    flash("Autenticado com sucesso.")
                    nxt = request.args.get("next") or url_for("home")
                    return redirect(nxt)
            else:
                if check_password_hash(stored, senha):
                    session["user_id"] = user["id"]
                    session['session_version'] = user.get('session_version', 0) if isinstance(user, dict) else 0
                    flash("Autenticado com sucesso.")
                    nxt = request.args.get("next") or url_for("home")
                    return redirect(nxt)
        flash("Usuário ou senha inválidos.")
        return redirect(url_for("login"))
    return render_template("login.html", config_sso=config_sso)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Desconectado.")
    return redirect(url_for("login"))


# ── Google SSO (OAuth 2.0 / OpenID Connect) ──────────────────────────────────

@app.route("/auth/google/login")
def auth_google_login():
    cfg = obter_configuracoes_sso()
    client_id = cfg.get("google_client_id")
    if not client_id or not cfg.get("ativo"):
        flash("O login com Google não está configurado ou está desativado no momento.")
        return redirect(url_for("login"))

    # State anti-CSRF token
    state = secrets.token_urlsafe(32)
    session["oauth_google_state"] = state

    if request.args.get("next"):
        session["oauth_next"] = request.args.get("next")

    redirect_uri = url_for("auth_google_callback", _external=True)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/calendar.events",
        "access_type": "offline",
        "prompt": "consent",
        "state": state
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)


@app.route("/auth/google/callback")
def auth_google_callback():
    error = request.args.get("error")
    if error:
        flash(f"Autorização cancelada ou recusada pelo Google ({error}).")
        return redirect(url_for("login"))

    state_recebido = request.args.get("state")
    state_esperado = session.pop("oauth_google_state", None)
    if not state_recebido or state_recebido != state_esperado:
        flash("Falha de validação de segurança (state token inválido). Tente novamente.")
        return redirect(url_for("login"))

    code = request.args.get("code")
    if not code:
        flash("Código de autorização não fornecido pelo Google.")
        return redirect(url_for("login"))

    cfg = obter_configuracoes_sso()
    client_id = cfg.get("google_client_id")
    client_secret = cfg.get("google_client_secret")
    redirect_uri = url_for("auth_google_callback", _external=True)

    # Troca code por access token
    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            },
            timeout=15
        )
        if token_resp.status_code != 200:
            flash("Erro ao autenticar com os servidores do Google.")
            return redirect(url_for("login"))
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token") or ""
        expires_in = tokens.get("expires_in", 3600)
        token_expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        # Busca perfil do usuário
        userinfo_resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15
        )
        if userinfo_resp.status_code != 200:
            flash("Não foi possível obter os dados do seu perfil Google.")
            return redirect(url_for("login"))
        google_profile = userinfo_resp.json()
    except Exception as e:
        flash(f"Erro de conexão com o Google: {e}")
        return redirect(url_for("login"))

    google_id = google_profile.get("sub")
    email = (google_profile.get("email") or "").strip().lower()
    nome = google_profile.get("name") or google_profile.get("given_name") or email.split("@")[0]
    avatar_url = google_profile.get("picture") or ""

    if not email:
        flash("A conta Google não forneceu um endereço de e-mail válido.")
        return redirect(url_for("login"))

    # 1. Busca por Google ID ou por Email
    user = encontrar_usuario_por_google_id(google_id)
    if not user:
        user = encontrar_usuario_por_email(email)

    if user:
        # Usuário existente: vincula google_id e tokens, atualiza nome/avatar se vazios
        user_id = user["id"]
        if USE_SQLITE:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE usuarios 
                SET google_id=?, 
                    google_access_token=?, 
                    google_token_expiry=?, 
                    google_refresh_token=COALESCE(NULLIF(?, ''), google_refresh_token),
                    nome=COALESCE(NULLIF(nome, ''), ?), 
                    avatar=COALESCE(NULLIF(avatar, ''), ?) 
                WHERE id=?
                """,
                (google_id, access_token, token_expiry, refresh_token, nome, avatar_url, user_id)
            )
            conn.commit()
            conn.close()
    else:
        # Novo usuário
        if not cfg.get("auto_cadastro"):
            flash(f"Acesso não autorizado para o e-mail '{email}'. Contate o administrador do sistema.")
            return redirect(url_for("login"))

        papel = cfg.get("papel_padrao", "Producao")
        user_id = str(uuid.uuid4())
        base_username = email.split("@")[0].lower()
        base_username = re.sub(r"[^a-z0-9_.]", "", base_username) or "usuario"
        username_final = base_username

        # Garante unicidade do username
        suffix = 1
        while encontrar_usuario_por_username(username_final):
            username_final = f"{base_username}{suffix}"
            suffix += 1

        now = agora().isoformat()
        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO usuarios 
                (id, username, password_hash, role, roles, nome, email, google_id, google_refresh_token, google_access_token, google_token_expiry, avatar, session_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (user_id, username_final, generate_password_hash(secrets.token_hex(16)), papel, serializar_roles([papel]), nome, email, google_id, refresh_token, access_token, token_expiry, avatar_url, now)
            )
            conn.commit()
            conn.close()
        user = {"id": user_id, "username": username_final, "session_version": 0}

    # Inicia a sessão
    session["user_id"] = user["id"]
    session["session_version"] = user.get("session_version", 0)

    # Auditoria
    try:
        if USE_SQLITE:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user["id"], user.get("username"), user["id"], "login_google_sso", f"email={email};google_id={google_id}", agora().isoformat())
            )
            conn.commit()
            conn.close()
    except Exception:
        pass

    flash(f"Bem-vindo(a), {nome}! Autenticado com sucesso via Google.")
    nxt = session.pop("oauth_next", None) or url_for("home")
    return redirect(nxt)


@app.route("/configuracoes/sso", methods=["GET", "POST"])
@requires_developer
def configuracoes_sso_view():
    if request.method == "POST":
        cfg = {
            "google_client_id": request.form.get("google_client_id", "").strip(),
            "google_client_secret": request.form.get("google_client_secret", "").strip(),
            "ativo": 1 if request.form.get("ativo") == "1" else 0,
            "auto_cadastro": 1 if request.form.get("auto_cadastro") == "1" else 0,
            "papel_padrao": request.form.get("papel_padrao", "Producao").strip(),
        }
        salvar_configuracoes_sso(cfg)
        flash("Configurações do Google SSO atualizadas com sucesso!")
        return redirect(url_for("developer_dashboard", tab="sso"))

    return redirect(url_for("developer_dashboard", tab="sso"))


# Minha conta (Editar Perfil, Nome, Foto e Trocar Senha)
@app.route('/minha-conta', methods=['GET', 'POST'])
def minha_conta():
    if not g.get('user'):
        return redirect(url_for('login'))

    user_id = g.user.get('id')
    user = encontrar_usuario_por_username(g.user.get('username'))
    if not user:
        flash('Usuário não encontrado.')
        return redirect(url_for('login'))

    papeis = carregar_papeis()
    papeis_map = {p["name"]: p for p in papeis}
    user_role_info = papeis_map.get(user.get("role"))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        remover_avatar = (request.form.get('remover_avatar') == '1')
        current_pwd = request.form.get('current_password', '')
        new_pwd = request.form.get('new_password', '')
        new_pwd2 = request.form.get('new_password2', '')

        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash('Por favor, informe um endereço de e-mail válido.')
            return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)

        # 1. Tratar alteração de senha (se algum campo de nova senha for preenchido)
        password_changed = False
        new_hash = None
        if new_pwd or new_pwd2 or current_pwd:
            if not current_pwd:
                flash('Para alterar a senha, informe sua senha atual.')
                return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)
            if new_pwd != new_pwd2:
                flash('A nova senha e a confirmação não conferem.')
                return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)
            if not new_pwd:
                flash('A nova senha não pode estar em branco.')
                return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)

            stored = user.get('password_hash', '')
            ok = False
            if stored.startswith('PLAIN:'):
                ok = (stored[len('PLAIN:'):] == current_pwd)
            else:
                ok = check_password_hash(stored, current_pwd)
            if not ok:
                flash('Senha atual incorreta.')
                return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)

            new_hash = generate_password_hash(new_pwd)
            password_changed = True

        # 2. Tratar Foto de Perfil (Avatar)
        current_avatar = user.get('avatar') or ''
        new_avatar = current_avatar

        if remover_avatar:
            if current_avatar:
                deletar_imagem(current_avatar, uploads_dir=os.path.join(DATA_DIR, 'uploads'))
            new_avatar = ''
        elif 'avatar' in request.files:
            f = request.files.get('avatar')
            if f and f.filename:
                uploads_dir = os.path.join(DATA_DIR, 'uploads')
                os.makedirs(uploads_dir, exist_ok=True)
                if current_avatar:
                    deletar_imagem(current_avatar, uploads_dir=uploads_dir)
                custom_id = f"avatar_{user_id}_{int(agora().timestamp())}"
                if getattr(app, 'testing', False):
                    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
                    clean_name = f"{custom_id}{ext}"
                    target_path = os.path.join(uploads_dir, clean_name)
                    f.save(target_path)
                    new_avatar = clean_name
                else:
                    url_ou_arquivo = upload_imagem(f, folder="avatares", fallback_dir=uploads_dir, custom_id=custom_id)
                    if url_ou_arquivo:
                        new_avatar = url_ou_arquivo

        # 3. Salvar no Banco
        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                if password_changed:
                    cur.execute("UPDATE usuarios SET nome=?, email=?, avatar=?, password_hash=? WHERE id=?",
                                (nome, email, new_avatar, new_hash, user_id))
                else:
                    cur.execute("UPDATE usuarios SET nome=?, email=?, avatar=? WHERE id=?",
                                (nome, email, new_avatar, user_id))
                conn.commit()
                try:
                    details = f"nome={nome};email={email};avatar={'yes' if new_avatar else 'no'};password_changed={'yes' if password_changed else 'no'}"
                    cur.execute("INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?,?,?,?,?,?,?)",
                                (str(uuid.uuid4()), user_id, user.get('username'), user_id, 'update_profile', details, agora().isoformat()))
                    conn.commit()
                except Exception:
                    pass
            except Exception as e:
                conn.rollback()
                flash('Erro ao salvar perfil: ' + str(e))
                return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)
            finally:
                conn.close()
        else:
            usuarios = carregar_usuarios()
            for u in usuarios:
                if u.get('id') == user_id:
                    u['nome'] = nome
                    u['email'] = email
                    u['avatar'] = new_avatar
                    if password_changed:
                        u['password_hash'] = new_hash
            salvar_usuarios(usuarios)

        g.user['nome'] = nome
        g.user['email'] = email
        g.user['avatar'] = new_avatar
        session['session_version'] = user.get('session_version', 0)

        if password_changed:
            flash('Perfil e senha atualizados com sucesso!')
        else:
            flash('Perfil atualizado com sucesso!')
        return redirect(url_for('minha_conta'))

    return render_template('minha_conta.html', user=user, user_role_info=user_role_info, system_tabs=SYSTEM_TABS)


# ── Usuários (Admin / Gestão de Contas) ───────────────────────────────────────
@app.route("/usuarios")
@requires_permission("usuarios", "read")
def usuarios():
    usuarios_lista = carregar_usuarios()
    papeis = carregar_papeis()
    papeis_map = {p["name"]: p for p in papeis}
    return render_template("usuarios.html", usuarios=usuarios_lista, papeis_map=papeis_map)


@app.route("/usuarios/novo", methods=["GET", "POST"])
@requires_permission("usuarios", "create")
def usuarios_novo():
    papeis = carregar_papeis()
    is_actor_dev = is_user_developer(g.user)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        raw_roles = [r for r in request.form.getlist("roles") if r.strip()]
        if not raw_roles:
            legado = request.form.get("role", "").strip()
            if legado:
                raw_roles = [legado]

        # Se o ator NÃO for Developer, não pode conceder o papel Developer
        if not is_actor_dev:
            roles = [r for r in raw_roles if r.lower() != "developer"]
        else:
            roles = raw_roles

        role = roles[0] if roles else ""

        if not username or not password:
            flash("Nome de usuário e senha são obrigatórios.")
            return render_template("usuario_form.html", papeis=papeis)
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Por favor, informe um endereço de e-mail válido.")
            return render_template("usuario_form.html", papeis=papeis)
        if password != password2:
            flash("As senhas não conferem.")
            return render_template("usuario_form.html", papeis=papeis)
        exists = encontrar_usuario_por_username(username)
        if exists:
            flash("Já existe um usuário com este nome.")
            return render_template("usuario_form.html", papeis=papeis)

        now = agora().isoformat()
        uid = str(uuid.uuid4())
        password_hash = generate_password_hash(password)

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO usuarios (id, username, password_hash, role, roles, email, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uid, username, password_hash, role, serializar_roles(roles), email, now),
                )
                conn.commit()
                try:
                    cur.execute(
                        "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, uid, "create_user", f"roles={roles};email={email}", agora().isoformat()),
                    )
                    conn.commit()
                except Exception:
                    pass
            except Exception as e:
                conn.rollback()
                flash("Erro ao criar usuário: " + str(e))
                return render_template("usuario_form.html", papeis=papeis)
            finally:
                conn.close()
        else:
            usuarios_lista = carregar_usuarios()
            usuarios_lista.append({
                "id": uid,
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "role": role,
                "roles": roles,
                "created_at": now,
            })
            salvar_usuarios(usuarios_lista)

        flash("Usuário criado com sucesso.")
        return redirect(url_for("usuarios"))

    return render_template("usuario_form.html", papeis=papeis)


@app.route("/usuarios/<user_id>/editar", methods=["GET", "POST"])
@requires_permission("usuarios", "update")
def usuarios_editar(user_id):
    papeis = carregar_papeis()
    is_actor_dev = is_user_developer(g.user)

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE id=?", (user_id,))
        r = cur.fetchone()
        conn.close()
        if not r:
            flash("Usuário não encontrado.")
            return redirect(url_for("usuarios"))
        usuario = {
            "id": r["id"],
            "username": r["username"],
            "role": r["role"] or "",
            "roles": r["roles"] if ("roles" in r.keys() and r["roles"]) else "",
            "email": r["email"] if ("email" in r.keys() and r["email"]) else "",
        }
    else:
        usuarios_lista = carregar_usuarios()
        usuario = next((u for u in usuarios_lista if u.get("id") == user_id), None)
        if not usuario:
            flash("Usuário não encontrado.")
            return redirect(url_for("usuarios"))

    # Se o usuário alvo for Developer e o ator NÃO for Developer, negar edição
    if is_user_developer(usuario) and not is_actor_dev:
        flash("Acesso negado: administradores não têm permissão para editar contas com acesso de Desenvolvedor.")
        return redirect(url_for("usuarios"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        raw_roles = [x for x in request.form.getlist("roles") if x.strip()]
        if not raw_roles:
            legado = request.form.get("role", "").strip()
            if legado:
                raw_roles = [legado]

        # Se o ator não for Developer, não pode conceder o papel Developer
        if not is_actor_dev:
            roles = [r for r in raw_roles if r.lower() != "developer"]
        else:
            roles = raw_roles

        role = roles[0] if roles else ""
        new_pwd = request.form.get("password", "")
        old_roles = usuario_roles_lista(usuario)
        roles_changed = (set(roles) != set(old_roles))

        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Por favor, informe um endereço de e-mail válido.")
            return render_template("usuario_edit.html", usuario=usuario, papeis=papeis, usuario_roles=usuario_roles_lista(usuario))

        if USE_SQLITE:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                if new_pwd:
                    if roles_changed:
                        cur.execute(
                            "UPDATE usuarios SET role=?, roles=?, email=?, password_hash=?, session_version=COALESCE(session_version,0)+1 WHERE id=?",
                            (role, serializar_roles(roles), email, generate_password_hash(new_pwd), user_id),
                        )
                    else:
                        cur.execute(
                            "UPDATE usuarios SET role=?, roles=?, email=?, password_hash=? WHERE id=?",
                            (role, serializar_roles(roles), email, generate_password_hash(new_pwd), user_id),
                        )
                else:
                    if roles_changed:
                        cur.execute(
                            "UPDATE usuarios SET role=?, roles=?, email=?, session_version=COALESCE(session_version,0)+1 WHERE id=?",
                            (role, serializar_roles(roles), email, user_id),
                        )
                    else:
                        cur.execute("UPDATE usuarios SET role=?, roles=?, email=? WHERE id=?", (role, serializar_roles(roles), email, user_id))
                
                # Se o próprio usuário editou sua conta, sincroniza a nova versão no cookie para não ser deslogado
                if roles_changed and user_id == session.get("user_id"):
                    cur.execute("SELECT session_version FROM usuarios WHERE id=?", (user_id,))
                    row_ver = cur.fetchone()
                    if row_ver:
                        session['session_version'] = row_ver[0]

                conn.commit()
                try:
                    details = f"roles={roles};email={email};password_changed={'yes' if new_pwd else 'no'}"
                    cur.execute(
                        "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, user_id, "update_user", details, agora().isoformat()),
                    )
                    conn.commit()
                except Exception:
                    pass
            except Exception as e:
                conn.rollback()
                flash("Erro ao atualizar usuário: " + str(e))
                return render_template("usuario_edit.html", usuario=usuario, papeis=papeis, usuario_roles=usuario_roles_lista(usuario))
            finally:
                conn.close()
        else:
            usuarios_lista = carregar_usuarios()
            for u in usuarios_lista:
                if u.get("id") == user_id:
                    u["role"] = role
                    u["roles"] = roles
                    u["email"] = email
                    if new_pwd:
                        u["password_hash"] = generate_password_hash(new_pwd)
            salvar_usuarios(usuarios_lista)

        flash("Usuário atualizado com sucesso.")
        return redirect(url_for("usuarios"))

    return render_template("usuario_edit.html", usuario=usuario, papeis=papeis, usuario_roles=usuario_roles_lista(usuario))


@app.route("/usuarios/<user_id>/excluir", methods=["POST"])
@requires_permission("usuarios", "delete")
def usuarios_excluir(user_id):
    usuarios_lista = carregar_usuarios()
    if len(usuarios_lista) <= 1:
        flash("Não é possível remover o último usuário do sistema.")
        return redirect(url_for("usuarios"))

    if g.get("user") and g.user.get("id") == user_id:
        flash("Você não pode excluir sua própria conta.")
        return redirect(url_for("usuarios"))

    target_user = next((u for u in usuarios_lista if u.get("id") == user_id), None)
    if not target_user and USE_SQLITE:
        target_user = encontrar_usuario_por_id(user_id)

    if target_user and is_user_developer(target_user) and not is_user_developer(g.user):
        flash("Acesso negado: administradores não têm permissão para remover contas com acesso de Desenvolvedor.")
        return redirect(url_for("usuarios"))

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM usuarios WHERE id=?", (user_id,))
            conn.commit()
            try:
                cur.execute(
                    "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, user_id, "delete_user", "", agora().isoformat()),
                )
                conn.commit()
            except Exception:
                pass
        except Exception as e:
            conn.rollback()
            flash("Erro ao remover usuário: " + str(e))
            return redirect(url_for("usuarios"))
        finally:
            conn.close()
    else:
        usuarios_lista = [u for u in usuarios_lista if u.get("id") != user_id]
        salvar_usuarios(usuarios_lista)

    flash("Usuário removido com sucesso.")
    return redirect(url_for("usuarios"))


# ── Gestão de Papéis e Permissões por Aba ─────────────────────────────────────
@app.route("/roles")
@requires_permission("roles", "read")
def roles():
    papeis = carregar_papeis()
    return render_template("roles.html", papeis=papeis, system_tabs=SYSTEM_TABS)


@app.route("/roles/novo", methods=["GET", "POST"])
@requires_permission("roles", "create")
def roles_novo():
    is_actor_dev = is_user_developer(g.user)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()

        if not name:
            flash("O nome do papel é obrigatório.")
            return render_template("role_form.html", role=None, system_tabs=SYSTEM_TABS, is_novo=True)

        if name.lower() in ("admin", "developer"):
            flash(f'O nome "{name}" é reservado para papéis do sistema.')
            return render_template("role_form.html", role=None, system_tabs=SYSTEM_TABS, is_novo=True)

        existing = encontrar_papel(name)
        if existing:
            flash(f'Já existe um papel cadastrado com o nome "{name}".')
            return render_template("role_form.html", role=None, system_tabs=SYSTEM_TABS, is_novo=True)

        now = agora().isoformat()
        role_id = str(uuid.uuid4())

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO roles (id, name, description, is_system, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
                    (role_id, name, description, now, now),
                )
                for tab in SYSTEM_TABS:
                    t_id = tab["id"]
                    if t_id == "developer" and not is_actor_dev:
                        continue
                    can_create = 1 if request.form.get(f"can_create_{t_id}") else 0
                    can_read = 1 if request.form.get(f"can_read_{t_id}") else 0
                    can_update = 1 if request.form.get(f"can_update_{t_id}") else 0
                    can_delete = 1 if request.form.get(f"can_delete_{t_id}") else 0

                    if can_create or can_read or can_update or can_delete:
                        cur.execute(
                            "INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (name, t_id, can_create, can_read, can_update, can_delete, now),
                        )
                conn.commit()
                invalidate_role_permissions_cache()
                try:
                    cur.execute(
                        "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, None, "create_role", f"role={name}", now),
                    )
                    conn.commit()
                except Exception:
                    pass
            except Exception as e:
                conn.rollback()
                flash("Erro ao salvar papel: " + str(e))
                return render_template("role_form.html", role=None, system_tabs=SYSTEM_TABS, is_novo=True)
            finally:
                conn.close()

        flash(f'Papel "{name}" criado com sucesso.')
        return redirect(url_for("roles"))

    return render_template("role_form.html", role=None, system_tabs=SYSTEM_TABS, is_novo=True)


@app.route("/roles/<role>/editar", methods=["GET", "POST"])
@app.route("/roles/<path:role>/editar", methods=["GET", "POST"])
@requires_permission("roles", "update")
def roles_editar(role):
    is_actor_dev = is_user_developer(g.user)

    if role.lower() == "developer" and not is_actor_dev:
        flash("Acesso negado: apenas Desenvolvedores podem visualizar ou editar o papel Developer.")
        return redirect(url_for("roles"))

    role_obj = encontrar_papel(role)
    if not role_obj:
        flash("Papel não encontrado.")
        return redirect(url_for("roles"))

    if request.method == "POST":
        new_name = request.form.get("name", "").strip() or role
        description = request.form.get("description", "").strip()

        now = agora().isoformat()
        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                if new_name != role and role not in ("Admin", "Developer"):
                    cur.execute("SELECT id FROM roles WHERE name=? AND name!=?", (new_name, role))
                    if cur.fetchone():
                        conn.close()
                        flash(f'Já existe outro papel com o nome "{new_name}".')
                        return render_template("role_form.html", role=role_obj, system_tabs=SYSTEM_TABS, is_novo=False)
                    cur.execute("UPDATE roles SET name=?, description=?, updated_at=? WHERE name=?", (new_name, description, now, role))
                    cur.execute("UPDATE usuarios SET role=? WHERE role=?", (new_name, role))
                    cur.execute("UPDATE role_permissions SET role=? WHERE role=?", (new_name, role))
                else:
                    cur.execute("UPDATE roles SET description=?, updated_at=? WHERE name=?", (description, now, role))

                target_role = new_name if (new_name != role and role not in ("Admin", "Developer")) else role

                if target_role == "Developer":
                    for tab in SYSTEM_TABS:
                        cur.execute(
                            "INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete, updated_at) VALUES (?, ?, 1, 1, 1, 1, ?)",
                            ("Developer", tab["id"], now),
                        )
                elif target_role == "Admin":
                    for tab in SYSTEM_TABS:
                        if tab["id"] == "developer":
                            cur.execute(
                                "INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete, updated_at) VALUES (?, ?, 0, 0, 0, 0, ?)",
                                ("Admin", tab["id"], now),
                            )
                        else:
                            cur.execute(
                                "INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete, updated_at) VALUES (?, ?, 1, 1, 1, 1, ?)",
                                ("Admin", tab["id"], now),
                            )
                else:
                    cur.execute("DELETE FROM role_permissions WHERE role=?", (target_role,))
                    for tab in SYSTEM_TABS:
                        t_id = tab["id"]
                        if t_id == "developer" and not is_actor_dev:
                            continue
                        can_create = 1 if request.form.get(f"can_create_{t_id}") else 0
                        can_read = 1 if request.form.get(f"can_read_{t_id}") else 0
                        can_update = 1 if request.form.get(f"can_update_{t_id}") else 0
                        can_delete = 1 if request.form.get(f"can_delete_{t_id}") else 0

                        if can_create or can_read or can_update or can_delete:
                            cur.execute(
                                "INSERT INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (target_role, t_id, can_create, can_read, can_update, can_delete, now),
                            )

                cur.execute("UPDATE usuarios SET session_version=COALESCE(session_version,0)+1 WHERE role=?", (target_role,))
                conn.commit()
                invalidate_role_permissions_cache()

                try:
                    cur.execute(
                        "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, None, "update_role_permissions", f"role={target_role}", now),
                    )
                    conn.commit()
                except Exception:
                    pass
            except Exception as e:
                conn.rollback()
                flash("Erro ao salvar permissões do papel: " + str(e))
                return render_template("role_form.html", role=role_obj, system_tabs=SYSTEM_TABS, is_novo=False)
            finally:
                conn.close()

        flash(f'Permissões do papel "{role}" atualizadas.')
        return redirect(url_for("roles"))

    return render_template("role_form.html", role=role_obj, system_tabs=SYSTEM_TABS, is_novo=False)


@app.route("/roles/<role>/excluir", methods=["POST"])
@app.route("/roles/<path:role>/excluir", methods=["POST"])
@requires_permission("roles", "delete")
def roles_excluir(role):
    if role in ("Admin", "Developer"):
        flash(f"O papel {role} é protegido pelo sistema e não pode ser excluído.")
        return redirect(url_for("roles"))

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute("UPDATE usuarios SET role='', session_version=COALESCE(session_version,0)+1 WHERE role=?", (role,))
            cur.execute("DELETE FROM role_permissions WHERE role=?", (role,))
            cur.execute("DELETE FROM roles WHERE name=?", (role,))
            conn.commit()
            invalidate_role_permissions_cache()
            try:
                cur.execute(
                    "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, None, "delete_role", f"role={role}", agora().isoformat()),
                )
                conn.commit()
            except Exception:
                pass
        except Exception as e:
            conn.rollback()
            flash("Erro ao excluir papel: " + str(e))
            return redirect(url_for("roles"))
        finally:
            conn.close()

    flash(f'Papel "{role}" removido com sucesso.')
    return redirect(url_for("roles"))


@app.route("/roles/assign", methods=["GET", "POST"])
@requires_permission("roles", "update")
def roles_assign():
    """Atribuição em massa de papéis a usuários."""
    papeis = carregar_papeis()
    usuarios_lista = carregar_usuarios()
    is_actor_dev = is_user_developer(g.user)

    if request.method == "POST":
        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                changed = 0
                for u in usuarios_lista:
                    # Se o usuário alvo for Developer e o ator NÃO for Developer, não alterar
                    if is_user_developer(u) and not is_actor_dev:
                        continue

                    raw_roles = [x for x in request.form.getlist("role_" + u["id"]) if x.strip()]
                    if not is_actor_dev:
                        new_roles = [x for x in raw_roles if x.lower() != "developer"]
                    else:
                        new_roles = raw_roles

                    old_roles = usuario_roles_lista(u)
                    if set(new_roles) != set(old_roles):
                        role_principal = new_roles[0] if new_roles else ""
                        cur.execute(
                            "UPDATE usuarios SET role=?, roles=?, session_version=COALESCE(session_version,0)+1 WHERE id=?",
                            (role_principal, serializar_roles(new_roles), u["id"]),
                        )
                        changed += 1
                        try:
                            cur.execute(
                                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else None, u["id"], "assign_role", f"roles={new_roles}", agora().isoformat()),
                            )
                        except Exception:
                            pass
                conn.commit()
                invalidate_role_permissions_cache()
                if changed:
                    flash("Atribuições de papéis atualizadas com sucesso.")
                else:
                    flash("Nenhuma alteração nas atribuições.")
            except Exception as e:
                conn.rollback()
                flash("Erro ao atualizar atribuições: " + str(e))
            finally:
                conn.close()
        return redirect(url_for("roles"))

    return render_template("roles_assign.html", users=usuarios_lista, papeis=papeis)



# ── Materiais (estoque) ───────────────────────────────────────────────────────
def carregar_materiais():
    # When using SQLite, read from the proper 'materiais' table with a transaction.
    if USE_SQLITE:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM materiais ORDER BY nome COLLATE NOCASE")
        rows = cur.fetchall()
        conn.close()
        return [{
            "id": r["id"],
            "nome": r["nome"],
            "categoria": r["categoria"],
            "emoji": r["emoji"],
            "quantidade": r["quantidade"],
            "unidade": r["unidade"],
            "quantidade_minima": r["quantidade_minima"],
            "custo": r["custo"],
            "gtin": r["gtin"],
            "foto": r["foto"],
        } for r in rows]

    if not os.path.exists(DATA_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEED_FILE, encoding="utf-8") as f:
            seed = json.load(f)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(seed, f, ensure_ascii=False, indent=2)
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def salvar_materiais(materiais):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            # Use a transaction to atomically replace the materials set.
            cur.execute("BEGIN IMMEDIATE")
            # We'll upsert per id to preserve uniqueness constraints
            # Clear names that are no longer present
            incoming_ids = [m.get("id") for m in materiais if m.get("id")]
            if incoming_ids:
                # delete any rows not in incoming_ids
                placeholders = ",".join(["?" for _ in incoming_ids])
                cur.execute(f"DELETE FROM materiais WHERE id NOT IN ({placeholders})", incoming_ids)
            else:
                cur.execute("DELETE FROM materiais")

            now = agora().isoformat()
            for m in materiais:
                _id = m.get("id") or str(uuid.uuid4())
                cur.execute(
                    "INSERT OR REPLACE INTO materiais (id,nome,categoria,emoji,quantidade,unidade,quantidade_minima,custo,gtin,foto,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT created_at FROM materiais WHERE id=?),?),?)",
                    (
                        _id,
                        m.get("nome"),
                        m.get("categoria"),
                        m.get("emoji"),
                        float(m.get("quantidade") or 0),
                        m.get("unidade"),
                        float(m.get("quantidade_minima") or 0),
                        float(m.get("custo") or 0),
                        m.get("gtin"),
                        m.get("foto"),
                        _id,
                        now,
                        now,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(materiais, f, ensure_ascii=False, indent=2)


def encontrar(materiais, material_id):
    for m in materiais:
        if m["id"] == material_id:
            return m
    return None


@app.route("/")
def home():
    materiais = carregar_materiais()
    total_itens = len(materiais)
    baixo_estoque = [m for m in materiais if m["quantidade"] <= m["quantidade_minima"]]
    return render_template("home.html", total_itens=total_itens, baixo_estoque=baixo_estoque)


@app.route("/estoque")
@requires_permission('estoque', 'read')
def estoque():
    materiais = carregar_materiais()
    cat = request.args.get("cat", "Todos")
    q = request.args.get("q", "").strip().lower()

    resultado = materiais
    if cat != "Todos":
        resultado = [m for m in resultado if m.get("categoria") == cat]
    if q:
        # busca por nome OU por código GTIN
        resultado = [
            m for m in resultado
            if q in m.get("nome", "").lower() or q in (m.get("gtin") or "").lower()
        ]

    # Categorias sincronizadas e dinâmicas com os materiais cadastrados
    categorias_disponiveis = list(dict.fromkeys(CATEGORIAS + [m.get("categoria") for m in materiais if m.get("categoria")]))

    return render_template(
        "estoque.html",
        materiais=resultado,
        categorias=categorias_disponiveis,
        cat_ativa=cat,
        q=request.args.get("q", ""),
        total=len(materiais),
    )


@app.route("/estoque/<material_id>/entrada", methods=["POST"])
@requires_permission('estoque', 'update')
def estoque_entrada(material_id):
    materiais = carregar_materiais()
    m = encontrar(materiais, material_id)
    if m:
        qtd = parse_float_ptbr(request.form.get("quantidade", 0))
        motivo = request.form.get("motivo", "").strip()
        if qtd <= 0:
            flash("Informe uma quantidade válida e maior que zero para a entrada.")
            return redirect(url_for("estoque"))

        unidade = (m.get("unidade") or "").lower()
        is_inteiro = unidade.startswith("unid") or unidade in ("unidades", "pares", "pacotes", "unidade")

        if is_inteiro:
            if abs(qtd - round(qtd)) > 1e-6:
                flash(f"Para o material '{m.get('nome')}' (unidade: {m.get('unidade')}), utilize apenas números inteiros.")
                return redirect(url_for("estoque"))
            qtd = int(round(qtd))
            m["quantidade"] = int(m.get("quantidade") or 0) + qtd
        else:
            m["quantidade"] = round(float(m.get("quantidade") or 0) + qtd, 3)

        salvar_materiais(materiais)
        registrar_movimentacao("entrada", qtd, m["unidade"], motivo or "Entrada manual de estoque", m["nome"])
        flash(f"Entrada de {qtd} {m['unidade']} registrada em {m['nome']}.")
    return redirect(url_for("estoque"))


@app.route("/estoque/<material_id>/excluir", methods=["POST"])
@requires_permission('estoque', 'delete')
def estoque_excluir(material_id):
    # Remove material and its photo (if present). Works with JSON or SQLite backend.
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT foto FROM materiais WHERE id=?", (material_id,))
        r = cur.fetchone()
        if r and r["foto"]:
            deletar_imagem(r["foto"], uploads_dir=os.path.join(DATA_DIR, 'uploads'))
        cur.execute("DELETE FROM materiais WHERE id=?", (material_id,))
        conn.commit()
        conn.close()
        flash("Material removido.")
        return redirect(url_for("estoque"))

    materiais = carregar_materiais()
    to_remove = next((m for m in materiais if m["id"] == material_id), None)
    if to_remove and to_remove.get("foto"):
        deletar_imagem(to_remove.get('foto'), uploads_dir=os.path.join(DATA_DIR, 'uploads'))
    materiais = [m for m in materiais if m["id"] != material_id]
    salvar_materiais(materiais)
    flash("Material removido.")
    return redirect(url_for("estoque"))


@app.route("/adicionar", methods=["GET", "POST"])
@requires_permission('adicionar', 'create')
def adicionar():
    materiais = carregar_materiais()

    if request.method == "POST":
        # Basic form token to avoid duplicate submissions
        token = request.form.get('form_token')
        expected = session.pop('form_token', None)
        if not token or token != expected:
            flash('Formulário já foi enviado ou token inválido. Por favor, tente novamente.')
            return redirect(url_for('adicionar'))

        nome = request.form.get("nome", "").strip()
        categoria = request.form.get("categoria", "Outros")
        # Se selecionou "Outros" e digitou uma categoria customizada, usar ela
        if categoria == "Outros":
            cat_custom = request.form.get("categoria_custom", "").strip()
            if cat_custom:
                categoria = cat_custom
            else:
                # Sugere automaticamente a categoria a partir do nome do material,
                # mantendo o estoque sempre consistente (courino -> Courino, etc.)
                categoria = sugerir_categoria(nome)
        # Normaliza a categoria escolhida para o conjunto canônico
        categoria = normalizar_categoria(categoria, nome)
        gtin = request.form.get("gtin", "").strip()

        quantidade = parse_float_ptbr(request.form.get("quantidade", 0))
        quantidade_minima = parse_float_ptbr(request.form.get("quantidade_minima", 5))
        custo = parse_float_ptbr(request.form.get("custo", 0))

        # Basic duplicate prevention: same name + gtin
        existe = next((m for m in materiais if m["nome"].strip().lower() == nome.lower() and (m.get("gtin") or "") == gtin), None)
        if existe:
            flash("Material com mesmo nome/GTIN já existe no estoque.")
            return redirect(url_for("adicionar"))

        novo_id = str(uuid.uuid4())
        novo = {
            "id": novo_id,
            "nome": nome,
            "categoria": categoria,
            "emoji": CATEGORIAS_EMOJI.get(categoria, "🔹"),
            "quantidade": quantidade,
            "unidade": request.form.get("unidade", "unidades"),
            "quantidade_minima": quantidade_minima,
            "custo": custo,
            "gtin": gtin,
        }

        # Handle optional photo upload
        foto = None
        if 'foto' in request.files:
            f = request.files.get('foto')
            if f and f.filename:
                uploads_dir = os.path.join(DATA_DIR, 'uploads')
                os.makedirs(uploads_dir, exist_ok=True)
                url_ou_arquivo = upload_imagem(f, folder="materiais", fallback_dir=uploads_dir, custom_id=novo_id)
                if url_ou_arquivo:
                    novo['foto'] = url_ou_arquivo

        materiais.append(novo)
        salvar_materiais(materiais)
        flash(f"{nome} adicionado ao estoque.")
        return redirect(url_for("estoque"))

    # dados usados pela busca (nome/gtin) que ajuda a evitar duplicados
    lista_busca = [
        {"id": m["id"], "nome": m["nome"], "gtin": m.get("gtin") or "", "quantidade": m["quantidade"], "unidade": m["unidade"]}
        for m in materiais
    ]
    # generate a one-time token to prevent duplicate form submits
    session['form_token'] = str(uuid.uuid4())
    return render_template(
        "adicionar.html",
        categorias=CATEGORIAS_EMOJI,
        unidades=UNIDADES,
        materiais_json=lista_busca,
        form_token=session['form_token'],
    )


@app.route("/baixa", methods=["GET", "POST"])
@requires_permission('baixa', 'create')
def baixa():
    materiais = carregar_materiais()

    if request.method == "POST":
        material_id = request.form.get("material_id", "").strip()
        if not material_id:
            flash("Selecione um material válido para dar baixa.")
            return redirect(url_for("baixa"))

        m = encontrar(materiais, material_id)
        if not m:
            flash("Material não encontrado no estoque. Ação cancelada.")
            return redirect(url_for("baixa"))

        qtd = parse_float_ptbr(request.form.get("quantidade", 0))
        motivo = request.form.get("motivo", "").strip()
        # Se selecionou "Outro" e digitou um motivo customizado, usar ele
        if motivo == "Outro":
            motivo_custom = request.form.get("motivo_custom", "").strip()
            if motivo_custom:
                motivo = motivo_custom

        unidade = (m.get("unidade") or "").lower()
        is_inteiro = unidade.startswith("unid") or unidade in ("unidades", "pares", "pacotes", "unidade", "unid")

        if is_inteiro:
            if abs(qtd - round(qtd)) > 1e-6:
                flash(f"Para o material '{m.get('nome')}' (unidade: {m.get('unidade')}), utilize apenas números inteiros.")
                return redirect(url_for("baixa"))
            qtd = int(round(qtd))

        if qtd <= 0:
            flash("Informe uma quantidade válida e maior que zero para a baixa.")
            return redirect(url_for("baixa"))

        # Checa estoque disponível
        current = float(m.get("quantidade") or 0)
        if current <= 0:
            flash(f"O material '{m.get('nome')}' está com estoque esgotado.")
            return redirect(url_for("baixa"))

        if qtd > current:
            flash(f"Quantidade insuficiente em estoque para {m.get('nome')}. Saldo atual: {current} {m.get('unidade')}.")
            return redirect(url_for("baixa"))

        # Executa dedução
        if is_inteiro:
            novo_q = max(0, int(current) - int(qtd))
        else:
            novo_q = round(max(0.0, current - float(qtd)), 3)

        m["quantidade"] = novo_q
        salvar_materiais(materiais)
        registrar_movimentacao("baixa", qtd, m.get("unidade"), motivo, m.get("nome"))
        flash(f"Baixa de {qtd} {m.get('unidade')} registrada em {m.get('nome')}.")
        return redirect(url_for("baixa"))

    mid_preselecionado = request.args.get("mid", "")
    return render_template(
        "baixa.html",
        materiais=materiais,
        motivos=MOTIVOS_BAIXA,
        mid_preselecionado=mid_preselecionado,
    )


# ── Produtos & Receitas ───────────────────────────────────────────────────────

def carregar_produtos():
    if USE_SQLITE:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM produtos ORDER BY nome COLLATE NOCASE")
        rows = cur.fetchall()
        conn.close()
        res = []
        for r in rows:
            receita = []
            try:
                receita = json.loads(r["receita"]) if r["receita"] else []
            except Exception:
                receita = []
            res.append({
                "id": r["id"],
                "nome": r["nome"],
                "emoji": r["emoji"],
                "preco_venda": r["preco_venda"],
                "receita": receita,
                "gtin": r["gtin"] if "gtin" in r.keys() else "",
                "estoque_pronto": int(r["estoque_pronto"] or 0) if "estoque_pronto" in r.keys() else 0,
            })
        return res
    return carregar_json("produtos.json")


def salvar_produtos(produtos):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("DELETE FROM produtos")
            now = agora().isoformat()
            for p in produtos:
                _id = p.get("id") or str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO produtos (id,nome,emoji,preco_venda,receita,gtin,estoque_pronto,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_id, p.get("nome"), p.get("emoji"), float(p.get("preco_venda") or 0), json.dumps(p.get("receita") or [], ensure_ascii=False), p.get("gtin") or "", int(p.get("estoque_pronto") or 0), now, now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return
    return salvar_json("produtos.json", produtos)


def carregar_pedidos():
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos ORDER BY data_pedido_iso DESC")
        rows = cur.fetchall()
        conn.close()
        res = []
        for r in rows:
            keys = r.keys() if hasattr(r, 'keys') else []
            res.append({
                "id": r["id"],
                "cliente": r["cliente"],
                "produto_id": r["produto_id"],
                "produto_nome": r["produto_nome"],
                "produto_emoji": r["produto_emoji"],
                "quantidade": r["quantidade"],
                "preco_unitario": r["preco_unitario"],
                "valor_total": r["valor_total"],
                "status": r["status"],
                "materiais_baixados": bool(r["materiais_baixados"]),
                "usou_estoque_pronto": bool(r["usou_estoque_pronto"]) if "usou_estoque_pronto" in keys else False,
                "data_pedido": r["data_pedido"],
                "data_pedido_iso": r["data_pedido_iso"],
                "data_entrega": r["data_entrega"] if "data_entrega" in keys and r["data_entrega"] else "",
                "google_event_id": r["google_event_id"] if "google_event_id" in keys and r["google_event_id"] else "",
                "google_calendar_synced_at": r["google_calendar_synced_at"] if "google_calendar_synced_at" in keys and r["google_calendar_synced_at"] else "",
                "origem": r["origem"] if "origem" in keys and r["origem"] else "web",
                "telefone_cliente": r["telefone_cliente"] if "telefone_cliente" in keys and r["telefone_cliente"] else "",
                "observacoes": r["observacoes"],
            })
        return res
    lista = carregar_json("pedidos.json")
    return sorted(lista, key=lambda p: p.get("data_pedido_iso", ""), reverse=True)


def carregar_sobras():
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM sobras ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        sobs = []
        for r in rows:
            sobs.append({
                "id": r["id"],
                "material_id": r["material_id"],
                "descricao": r["descricao"],
                "quantidade": r["quantidade"],
                "unidade": r["unidade"],
                "data": r["data"],
                "status": r["status"],
            })
        return sobs
    lista = carregar_json("sobras.json")
    return list(reversed(lista))


def carregar_despesas():
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM despesas ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        return [{
            "id": r["id"],
            "descricao": r["descricao"],
            "valor": r["valor"],
            "categoria": r["categoria"],
            "data": r["data"],
            "created_at": r["created_at"] if "created_at" in r.keys() else ""
        } for r in rows]
    return carregar_json("despesas.json")


def carregar_movimentacoes(limit=100):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM movimentacoes ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()
        return [{
            "id": r["id"],
            "tipo": r["tipo"],
            "material_nome": r["material_nome"],
            "quantidade": r["quantidade"],
            "unidade": r["unidade"],
            "motivo": r["motivo"],
            "data": r["data"],
            "usuario": r["usuario"],
        } for r in rows]
    return carregar_json("movimentacoes.json")[:limit]


# ── Relatórios Personalizados (CRUD & Engine de Gráficos) ──────────────────────
PALETA_CORES_GRAFICO = [
    "#7C3D12", "#C88242", "#D99B26", "#2E7D32", "#C62828",
    "#5C2D0E", "#A67C52", "#4A6B82", "#8D6E63", "#388E3C"
]


def carregar_relatorios_customizados():
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM relatorios_customizados ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        return [{
            "id": r["id"],
            "titulo": r["titulo"],
            "tipo": r["tipo"],
            "tipo_grafico": r["tipo_grafico"],
            "categoria_filtro": r["categoria_filtro"] or "",
            "status_filtro": r["status_filtro"] or "",
            "apenas_criticos": bool(r["apenas_criticos"]),
            "observacoes": r["observacoes"] or "",
            "criado_por": r["criado_por"] or "",
            "created_at": r["created_at"] or "",
            "updated_at": r["updated_at"] or "",
        } for r in rows]
    return carregar_json("relatorios_customizados.json")


def encontrar_relatorio_por_id(relatorio_id):
    lista = carregar_relatorios_customizados()
    for r in lista:
        if r["id"] == relatorio_id:
            return r
    return None


def salvar_relatorio_customizado(rel):
    _id = rel.get("id") or str(uuid.uuid4())
    now = agora().isoformat()
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM relatorios_customizados WHERE id=?", (_id,))
        exists = cur.fetchone()
        if exists:
            cur.execute("""
                UPDATE relatorios_customizados
                SET titulo=?, tipo=?, tipo_grafico=?, categoria_filtro=?, status_filtro=?,
                    apenas_criticos=?, observacoes=?, updated_at=?
                WHERE id=?
            """, (
                rel["titulo"], rel["tipo"], rel["tipo_grafico"], rel.get("categoria_filtro", ""),
                rel.get("status_filtro", ""), 1 if rel.get("apenas_criticos") else 0,
                rel.get("observacoes", ""), now, _id
            ))
        else:
            cur.execute("""
                INSERT INTO relatorios_customizados (id, titulo, tipo, tipo_grafico, categoria_filtro, status_filtro, apenas_criticos, observacoes, criado_por, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                _id, rel["titulo"], rel["tipo"], rel["tipo_grafico"], rel.get("categoria_filtro", ""),
                rel.get("status_filtro", ""), 1 if rel.get("apenas_criticos") else 0,
                rel.get("observacoes", ""), rel.get("criado_por", ""), now, now
            ))
        conn.commit()
        conn.close()
        rel["id"] = _id
        return rel

    lista = carregar_json("relatorios_customizados.json")
    idx = next((i for i, x in enumerate(lista) if x["id"] == _id), None)
    rel["id"] = _id
    rel["updated_at"] = now
    if idx is not None:
        lista[idx] = rel
    else:
        rel["created_at"] = now
        lista.append(rel)
    salvar_json("relatorios_customizados.json", lista)
    return rel


def excluir_relatorio_customizado(relatorio_id):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM relatorios_customizados WHERE id=?", (relatorio_id,))
        conn.commit()
        conn.close()
        return True
    lista = carregar_json("relatorios_customizados.json")
    lista = [r for r in lista if r["id"] != relatorio_id]
    salvar_json("relatorios_customizados.json", lista)
    return True


def gerar_dados_relatorio(relatorio):
    """Compila dinamicamente KPIs, dados tabulares e payloads Chart.js para qualquer relatório."""
    tipo = relatorio.get("tipo", "estoque")
    tipo_grafico = relatorio.get("tipo_grafico", "bar")
    cat_filtro = (relatorio.get("categoria_filtro") or "").strip()
    status_filtro = (relatorio.get("status_filtro") or "").strip()
    apenas_criticos = bool(relatorio.get("apenas_criticos"))

    kpis = []
    tabela = {"colunas": [], "linhas": []}
    chart_data = {"labels": [], "datasets": []}

    if tipo == "estoque":
        materiais = carregar_materiais()
        if cat_filtro:
            materiais = [m for m in materiais if m.get("categoria") == cat_filtro]
        if apenas_criticos:
            materiais = [m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)]

        total_itens = len(materiais)
        valor_total = round(sum(m.get("quantidade", 0) * m.get("custo", 0) for m in materiais), 2)
        criticos_count = len([m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)])

        kpis = [
            {"titulo": "Itens Filtrados", "valor": f"{total_itens}", "sub": "Materiais selecionados", "cor": "var(--primary)"},
            {"titulo": "Valor em Estoque", "valor": f"R$ {formatar_reais(valor_total)}", "sub": "Total imobilizado", "cor": "var(--accent)"},
            {"titulo": "Nível Crítico", "valor": f"{criticos_count}", "sub": "Itens abaixo do mínimo", "cor": "var(--danger)" if criticos_count > 0 else "var(--success)"},
        ]

        tabela["colunas"] = ["Material", "Categoria", "Qtd Atual", "Qtd Mínima", "Custo Unit.", "Valor Total", "Status"]
        tabela["linhas"] = []
        labels = []
        data_qtd = []
        data_val = []

        for m in materiais:
            val_t = round(m.get("quantidade", 0) * m.get("custo", 0), 2)
            is_crit = m.get("quantidade", 0) <= m.get("quantidade_minima", 0)
            tabela["linhas"].append([
                f"{m.get('emoji','')} {m.get('nome','')}",
                m.get("categoria", ""),
                f"{m.get('quantidade',0)} {m.get('unidade','')}",
                f"{m.get('quantidade_minima',0)} {m.get('unidade','')}",
                f"R$ {formatar_reais(float(m.get('custo',0)))}",
                f"R$ {formatar_reais(val_t)}",
                "⚠️ Crítico" if is_crit else "✅ Normal"
            ])
            labels.append(m.get("nome", "")[:18])
            data_qtd.append(m.get("quantidade", 0))
            data_val.append(val_t)

        chart_data["labels"] = labels
        if tipo_grafico in ("doughnut", "pie", "polarArea"):
            chart_data["datasets"] = [{
                "label": "Valor em Estoque (R$)",
                "data": data_val,
                "backgroundColor": PALETA_CORES_GRAFICO[:len(labels)],
                "borderWidth": 1.5,
                "borderColor": "#FFFFFF"
            }]
        else:
            chart_data["datasets"] = [
                {
                    "label": "Qtd Atual",
                    "data": data_qtd,
                    "backgroundColor": "rgba(124, 61, 18, 0.8)",
                    "borderColor": "#7C3D12",
                    "borderWidth": 1.5,
                },
                {
                    "label": "Valor Total (R$)",
                    "data": data_val,
                    "backgroundColor": "rgba(200, 130, 66, 0.8)",
                    "borderColor": "#C88242",
                    "borderWidth": 1.5,
                }
            ]

    elif tipo == "financeiro":
        despesas = carregar_despesas()
        pedidos = carregar_pedidos()
        materiais = carregar_materiais()

        if cat_filtro:
            despesas = [d for d in despesas if d.get("categoria") == cat_filtro]

        total_desp = round(sum(d.get("valor", 0) for d in despesas), 2)
        rec_entregue = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") == "Entregue"), 2)
        lucro = round(rec_entregue - total_desp, 2)

        kpis = [
            {"titulo": "Receita Recebida", "valor": f"R$ {formatar_reais(rec_entregue)}", "sub": "Pedidos entregues", "cor": "var(--success)"},
            {"titulo": "Despesas Filtradas", "valor": f"R$ {formatar_reais(total_desp)}", "sub": f"{len(despesas)} lançamentos", "cor": "var(--danger)"},
            {"titulo": "Lucro Realizado", "valor": f"R$ {formatar_reais(lucro)}", "sub": "Receita − Despesas", "cor": "var(--success)" if lucro >= 0 else "var(--danger)"},
        ]

        tabela["colunas"] = ["Data", "Descrição", "Categoria", "Valor (R$)"]
        tabela["linhas"] = [[d.get("data",""), d.get("descricao",""), d.get("categoria","Outros"), f"R$ {formatar_reais(float(d.get('valor',0)))}"] for d in despesas]

        # Agrupamento de despesas por categoria
        desp_por_cat = {}
        for d in despesas:
            c = d.get("categoria", "Outros")
            desp_por_cat[c] = round(desp_por_cat.get(c, 0.0) + float(d.get("valor", 0)), 2)

        if tipo_grafico in ("doughnut", "pie", "polarArea"):
            chart_data["labels"] = list(desp_por_cat.keys())
            chart_data["datasets"] = [{
                "label": "Despesas por Categoria (R$)",
                "data": list(desp_por_cat.values()),
                "backgroundColor": PALETA_CORES_GRAFICO[:len(desp_por_cat)],
                "borderWidth": 1.5,
                "borderColor": "#FFFFFF"
            }]
        else:
            chart_data["labels"] = ["Receita Recebida", "Despesas Totais", "Lucro Realizado"]
            chart_data["datasets"] = [{
                "label": "Comparativo Financeiro (R$)",
                "data": [rec_entregue, total_desp, max(0, lucro)],
                "backgroundColor": ["rgba(46, 125, 50, 0.8)", "rgba(198, 40, 40, 0.8)", "rgba(200, 130, 66, 0.8)"],
                "borderColor": ["#2E7D32", "#C62828", "#C88242"],
                "borderWidth": 1.5,
            }]

    elif tipo == "pedidos":
        pedidos = carregar_pedidos()
        if status_filtro:
            pedidos = [p for p in pedidos if p.get("status") == status_filtro]

        total_peds = len(pedidos)
        faturamento_tot = round(sum(p.get("valor_total", 0) for p in pedidos), 2)
        entregues = len([p for p in pedidos if p.get("status") == "Entregue"])

        kpis = [
            {"titulo": "Total de Pedidos", "valor": f"{total_peds}", "sub": "Registros filtrados", "cor": "var(--primary)"},
            {"titulo": "Faturamento", "valor": f"R$ {formatar_reais(faturamento_tot)}", "sub": "Volume total", "cor": "var(--accent)"},
            {"titulo": "Entregues", "valor": f"{entregues}", "sub": f"{round((entregues/total_peds*100) if total_peds>0 else 0)}% do total", "cor": "var(--success)"},
        ]

        tabela["colunas"] = ["Cliente", "Produto", "Qtd", "Valor Total", "Status", "Data Pedido"]
        tabela["linhas"] = [
            [p.get("cliente",""), p.get("produto_nome",""), p.get("quantidade",1), f"R$ {formatar_reais(float(p.get('valor_total',0)))}", p.get("status","Pendente"), p.get("data_pedido","")]
            for p in pedidos
        ]

        # Agrupamento por status
        ped_por_status = {}
        for p in pedidos:
            st = p.get("status", "Pendente")
            ped_por_status[st] = ped_por_status.get(st, 0) + 1

        chart_data["labels"] = list(ped_por_status.keys())
        chart_data["datasets"] = [{
            "label": "Qtd de Pedidos",
            "data": list(ped_por_status.values()),
            "backgroundColor": PALETA_CORES_GRAFICO[:len(ped_por_status)],
            "borderWidth": 1.5,
            "borderColor": "#FFFFFF"
        }]

    elif tipo == "movimentacoes":
        movs = carregar_movimentacoes(150)
        tabela["colunas"] = ["Data", "Tipo", "Material", "Qtd", "Motivo", "Usuário"]
        tabela["linhas"] = [
            [m.get("data",""), m.get("tipo",""), m.get("material_nome",""), f"{m.get('quantidade',0)} {m.get('unidade','')}", m.get("motivo",""), m.get("usuario","")]
            for m in movs
        ]

        # Agrupamento por tipo
        por_tipo = {}
        for m in movs:
            t = m.get("tipo", "Outro")
            por_tipo[t] = por_tipo.get(t, 0) + 1

        kpis = [
            {"titulo": "Total Movimentações", "valor": f"{len(movs)}", "sub": "Últimos registros", "cor": "var(--primary)"},
            {"titulo": "Saídas / Baixas", "valor": f"{por_tipo.get('Saída', por_tipo.get('Baixa', 0))}", "sub": "Consumo de produção", "cor": "var(--danger)"},
            {"titulo": "Entradas", "valor": f"{por_tipo.get('Entrada', 0)}", "sub": "Reposição de estoque", "cor": "var(--success)"},
        ]

        chart_data["labels"] = list(por_tipo.keys())
        chart_data["datasets"] = [{
            "label": "Movimentações",
            "data": list(por_tipo.values()),
            "backgroundColor": PALETA_CORES_GRAFICO[:len(por_tipo)],
            "borderWidth": 1.5,
            "borderColor": "#FFFFFF"
        }]

    elif tipo == "sobras":
        sobras = carregar_sobras()
        if status_filtro:
            sobras = [s for s in sobras if s.get("status") == status_filtro]

        total_sobras = len(sobras)
        disp = len([s for s in sobras if s.get("status") == "Disponível"])
        util = len([s for s in sobras if s.get("status") == "Utilizado"])

        kpis = [
            {"titulo": "Total de Sobras", "valor": f"{total_sobras}", "sub": "Cadastradas", "cor": "var(--primary)"},
            {"titulo": "Disponíveis", "valor": f"{disp}", "sub": "Prontas para uso", "cor": "var(--accent)"},
            {"titulo": "Reaproveitadas", "valor": f"{util}", "sub": "Peças geradas", "cor": "var(--success)"},
        ]

        tabela["colunas"] = ["Descrição", "Quantidade", "Data", "Status"]
        tabela["linhas"] = [
            [s.get("descricao",""), f"{s.get('quantidade',0)} {s.get('unidade','')}", s.get("data",""), s.get("status","Disponível")]
            for s in sobras
        ]

        chart_data["labels"] = ["Disponível", "Utilizado", "Descartado"]
        chart_data["datasets"] = [{
            "label": "Status das Sobras",
            "data": [disp, util, total_sobras - (disp + util)],
            "backgroundColor": ["#C88242", "#2E7D32", "#7A6B63"],
            "borderWidth": 1.5,
            "borderColor": "#FFFFFF"
        }]

    else: # Geral
        materiais = carregar_materiais()
        pedidos = carregar_pedidos()
        despesas = carregar_despesas()

        val_est = round(sum(m.get("quantidade", 0) * m.get("custo", 0) for m in materiais), 2)
        rec_ent = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") == "Entregue"), 2)
        rec_prev = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") != "Entregue"), 2)
        tot_desp = round(sum(d.get("valor", 0) for d in despesas), 2)
        lucro = round(rec_ent - tot_desp, 2)

        kpis = [
            {"titulo": "Valor em Estoque", "valor": f"R$ {formatar_reais(val_est)}", "sub": f"{len(materiais)} insumos", "cor": "var(--primary)"},
            {"titulo": "Receita Recebida", "valor": f"R$ {formatar_reais(rec_ent)}", "sub": "Pedidos entregues", "cor": "var(--success)"},
            {"titulo": "Lucro Operacional", "valor": f"R$ {formatar_reais(lucro)}", "sub": "Receita − Despesas", "cor": "var(--success)" if lucro >= 0 else "var(--danger)"},
        ]

        tabela["colunas"] = ["Métrica Consolidada", "Valor"]
        tabela["linhas"] = [
            ["Valor Total em Estoque", f"R$ {formatar_reais(val_est)}"],
            ["Receita Recebida", f"R$ {formatar_reais(rec_ent)}"],
            ["Receita Prevista (Em andamento)", f"R$ {formatar_reais(rec_prev)}"],
            ["Despesas Operacionais", f"R$ {formatar_reais(tot_desp)}"],
            ["Lucro Líquido", f"R$ {formatar_reais(lucro)}"],
        ]

        chart_data["labels"] = ["Estoque", "Receita Entregue", "Receita Prevista", "Despesas", "Lucro"]
        chart_data["datasets"] = [{
            "label": "Balanço Geral (R$)",
            "data": [val_est, rec_ent, rec_prev, tot_desp, max(0, lucro)],
            "backgroundColor": ["#7C3D12", "#2E7D32", "#C88242", "#C62828", "#5C2D0E"],
            "borderWidth": 1.5,
            "borderColor": "#FFFFFF"
        }]

    return {
        "kpis": kpis,
        "tabela": tabela,
        "chart_data": chart_data,
        "tipo_grafico": tipo_grafico if tipo_grafico in ("bar", "line", "doughnut", "pie", "polarArea") else "bar"
    }


def calcular_produto(produto, mat_map):
    """Anexa custo estimado, margem e a receita já resolvida com nome/emoji/unidade dos materiais."""
    custo = 0.0
    receita_detalhada = []
    for item in produto.get("receita", []):
        m = mat_map.get(item["material_id"])
        if m:
            custo += m["custo"] * item["quantidade"]
            receita_detalhada.append({
                "nome": m["nome"], "emoji": m["emoji"],
                "quantidade": item["quantidade"], "unidade": m["unidade"],
                "disponivel": m["quantidade"],
            })
        else:
            receita_detalhada.append({
                "nome": "Material removido", "emoji": "❓",
                "quantidade": item["quantidade"], "unidade": "", "disponivel": None,
            })
    p = dict(produto)
    p["custo_estimado"] = round(custo, 2)
    p["margem"] = round(produto.get("preco_venda", 0) - custo, 2)
    p["receita_detalhada"] = receita_detalhada
    return p


@app.route("/produtos")
@requires_permission('produtos', 'read')
def produtos():
    lista = carregar_produtos()
    mat_map = {m["id"]: m for m in carregar_materiais()}
    produtos_calc = [calcular_produto(p, mat_map) for p in lista]
    return render_template("produtos.html", produtos=produtos_calc)


@app.route("/produtos/novo", methods=["GET", "POST"])
@requires_permission('produtos', 'create')
def produto_novo():
    materiais = carregar_materiais()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        emoji = request.form.get("emoji", "👜").strip() or "👜"
        preco_venda = parse_float_ptbr(request.form.get("preco_venda", 0))

        mat_ids = request.form.getlist("material_id[]")
        qtds = request.form.getlist("material_qtd[]")
        receita = []
        for mid, q in zip(mat_ids, qtds):
            if not mid:
                continue
            qf = parse_float_ptbr(q)
            if qf <= 0:
                continue
            receita.append({"material_id": mid, "quantidade": qf})

        if not nome:
            flash("Informe o nome do produto.")
            return render_template("produto_form.html", produto=None, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=False)

        # must have at least one material in receita
        if not receita:
            flash("Não é possível criar um produto sem materiais na receita.")
            return render_template("produto_form.html", produto=None, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=False)

        # prevent duplicate product names (case-insensitive)
        produtos_lista = carregar_produtos()
        if any(p.get('nome', '').strip().lower() == nome.lower() for p in produtos_lista):
            flash('Já existe um produto com este nome.')
            return render_template("produto_form.html", produto=None, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=False)

        gtin = request.form.get("gtin", "").strip()
        estoque_pronto = int(parse_float_ptbr(request.form.get("estoque_pronto", 0)))
        _id = str(uuid.uuid4())
        now_iso = agora().isoformat()

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO produtos (id,nome,emoji,preco_venda,receita,gtin,estoque_pronto,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_id, nome, emoji, preco_venda, json.dumps(receita, ensure_ascii=False), gtin, max(0, estoque_pronto), now_iso, now_iso),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                flash("Erro ao cadastrar produto: " + str(e))
                return render_template("produto_form.html", produto=None, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=False)
            finally:
                conn.close()
        else:
            produtos_lista.append({
                "id": _id,
                "nome": nome,
                "emoji": emoji,
                "preco_venda": preco_venda,
                "receita": receita,
                "gtin": gtin,
                "estoque_pronto": max(0, estoque_pronto),
            })
            salvar_produtos(produtos_lista)

        flash(f"{nome} cadastrado em Produtos & Receitas.")
        return redirect(url_for("produtos"))

    return render_template("produto_form.html", produto=None, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=False)


@app.route("/produtos/<produto_id>/editar", methods=["GET", "POST"])
@requires_permission('produtos', 'update')
def produto_editar(produto_id):
    materiais = carregar_materiais()
    produtos_lista = carregar_produtos()
    produto = next((prod for prod in produtos_lista if prod["id"] == produto_id), None)
    if not produto:
        flash("Produto não encontrado.")
        return redirect(url_for("produtos"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        emoji = request.form.get("emoji", "👜").strip() or "👜"
        preco_venda = parse_float_ptbr(request.form.get("preco_venda", 0))

        mat_ids = request.form.getlist("material_id[]")
        qtds = request.form.getlist("material_qtd[]")
        receita = []
        for mid, q in zip(mat_ids, qtds):
            if not mid:
                continue
            qf = parse_float_ptbr(q)
            if qf <= 0:
                continue
            receita.append({"material_id": mid, "quantidade": qf})

        if not nome:
            flash("Informe o nome do produto.")
            return render_template("produto_form.html", produto=produto, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=True)

        if not receita:
            flash("Não é possível salvar um produto sem materiais na receita.")
            return render_template("produto_form.html", produto=produto, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=True)

        # Check duplicate name with other products (case-insensitive)
        if any(p.get('nome', '').strip().lower() == nome.lower() and p.get('id') != produto_id for p in produtos_lista):
            flash('Já existe outro produto com este nome.')
            return render_template("produto_form.html", produto=produto, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=True)

        gtin = request.form.get("gtin", "").strip()
        estoque_pronto = int(parse_float_ptbr(request.form.get("estoque_pronto", 0)))
        now_iso = agora().isoformat()

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute(
                    "UPDATE produtos SET nome=?, emoji=?, preco_venda=?, receita=?, gtin=?, estoque_pronto=?, updated_at=? WHERE id=?",
                    (nome, emoji, preco_venda, json.dumps(receita, ensure_ascii=False), gtin, max(0, estoque_pronto), now_iso, produto_id)
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                flash("Erro ao atualizar produto: " + str(e))
                return render_template("produto_form.html", produto=produto, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=True)
            finally:
                conn.close()
        else:
            produto["nome"] = nome
            produto["emoji"] = emoji
            produto["preco_venda"] = preco_venda
            produto["receita"] = receita
            produto["gtin"] = gtin
            produto["estoque_pronto"] = max(0, estoque_pronto)
            salvar_produtos(produtos_lista)

        flash(f"Produto '{nome}' e sua receita de materiais foram atualizados com sucesso.")
        return redirect(url_for("produtos"))

    return render_template("produto_form.html", produto=produto, materiais=materiais, emojis=EMOJIS_PRODUTO, is_edicao=True)


@app.route("/produtos/<produto_id>/ajuste_estoque", methods=["POST"])
@requires_permission('produtos', 'update')
def produto_ajuste_estoque(produto_id):
    produtos_lista = carregar_produtos()
    p = next((prod for prod in produtos_lista if prod["id"] == produto_id), None)
    if not p:
        flash("Produto não encontrado.")
        return redirect(url_for("produtos"))

    acao = request.form.get("acao", "adicionar").strip()
    qtd = int(parse_float_ptbr(request.form.get("quantidade", 1)))
    if qtd <= 0:
        qtd = 1

    atual = int(p.get("estoque_pronto") or 0)
    now = agora()
    now_str = now.strftime("%d/%m/%Y %H:%M")
    usuario_id = session.get("user_id") if session else None

    if acao == "adicionar":
        novo = atual + qtd
        motivo = request.form.get("motivo", "").strip() or "Entrada manual no estoque de peças prontas"
        p["estoque_pronto"] = novo
        salvar_produtos(produtos_lista)
        registrar_movimentacao("estoque_pronto", qtd, "unidades", motivo, p["nome"])
        flash(f"+{qtd} peça(s) de {p['nome']} adicionada(s) ao estoque de pronta-entrega (Total: {novo}).")
    elif acao == "remover":
        novo = max(0, atual - qtd)
        p["estoque_pronto"] = novo
        salvar_produtos(produtos_lista)
        registrar_movimentacao("estoque_pronto", qtd, "unidades", "Saída manual do estoque de peças prontas", p["nome"])
        flash(f"-{qtd} peça(s) de {p['nome']} retirada(s) do estoque de pronta-entrega (Total: {novo}).")

    return redirect(url_for("produtos"))


@app.route("/produtos/<produto_id>/excluir", methods=["POST"])
@requires_permission('produtos', 'delete')
def produto_excluir(produto_id):
    produtos_lista = carregar_produtos()
    produtos_lista = [p for p in produtos_lista if p["id"] != produto_id]
    salvar_produtos(produtos_lista)
    flash("Produto removido.")
    return redirect(url_for("produtos"))


# ── Pedidos dos Clientes ──────────────────────────────────────────────────────
@app.route("/pedidos")
@requires_permission('pedidos', 'read')
def pedidos():
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos ORDER BY data_pedido_iso DESC")
        rows = cur.fetchall()
        conn.close()
        res = []
        for r in rows:
            keys = r.keys() if hasattr(r, 'keys') else []
            origem = r["origem"] if "origem" in keys and r["origem"] else ("whatsapp" if "whatsapp" in str(r["observacoes"] or "").lower() else "web")
            tel = r["telefone_cliente"] if "telefone_cliente" in keys and r["telefone_cliente"] else ""
            data_entrega = r["data_entrega"] if "data_entrega" in keys and r["data_entrega"] else ""
            google_event_id = r["google_event_id"] if "google_event_id" in keys and r["google_event_id"] else ""
            google_calendar_synced_at = r["google_calendar_synced_at"] if "google_calendar_synced_at" in keys and r["google_calendar_synced_at"] else ""
            res.append({
                "id": r["id"],
                "cliente": r["cliente"],
                "produto_id": r["produto_id"],
                "produto_nome": r["produto_nome"],
                "produto_emoji": r["produto_emoji"],
                "quantidade": r["quantidade"],
                "preco_unitario": r["preco_unitario"],
                "valor_total": r["valor_total"],
                "status": r["status"],
                "materiais_baixados": bool(r["materiais_baixados"]),
                "data_pedido": r["data_pedido"],
                "data_pedido_iso": r["data_pedido_iso"],
                "observacoes": r["observacoes"],
                "origem": origem,
                "telefone_cliente": tel,
                "data_entrega": data_entrega,
                "google_event_id": google_event_id,
                "google_calendar_synced_at": google_calendar_synced_at,
            })
        return render_template("pedidos.html", pedidos=res, status_lista=STATUS_PEDIDO, status_badge=STATUS_PEDIDO_BADGE)

    lista = carregar_json("pedidos.json")
    for p in lista:
        if "origem" not in p:
            p["origem"] = "whatsapp" if "whatsapp" in str(p.get("observacoes", "")).lower() else "web"
        if "telefone_cliente" not in p:
            p["telefone_cliente"] = ""
        if "data_entrega" not in p:
            p["data_entrega"] = ""
        if "google_event_id" not in p:
            p["google_event_id"] = ""
        if "google_calendar_synced_at" not in p:
            p["google_calendar_synced_at"] = ""
    lista_ordenada = sorted(lista, key=lambda p: p.get("data_pedido_iso", ""), reverse=True)
    return render_template("pedidos.html", pedidos=lista_ordenada, status_lista=STATUS_PEDIDO,
                            status_badge=STATUS_PEDIDO_BADGE)


@app.route("/pedidos/novo", methods=["GET", "POST"])
@requires_permission('pedidos', 'create')
def pedido_novo():
    produtos_lista = carregar_produtos() if USE_SQLITE else carregar_json("produtos.json")

    if request.method == "POST":
        cliente = request.form.get("cliente", "").strip()
        produto_id = request.form.get("produto_id", "")
        try:
            quantidade = int(request.form.get("quantidade", 1) or 1)
        except ValueError:
            quantidade = 1
        data_entrega = request.form.get("data_entrega", "").strip()
        observacoes = request.form.get("observacoes", "").strip()

        produto = next((p for p in produtos_lista if p["id"] == produto_id), None)
        if not cliente or not produto or quantidade <= 0:
            flash("Preencha cliente, produto e uma quantidade válida.")
            return redirect(url_for("pedido_novo"))

        estoque_pronto_atual = int(produto.get("estoque_pronto") or 0)
        usar_pronta = estoque_pronto_atual >= quantidade

        preco_unit = produto.get("preco_venda", 0)
        dt_pedido = agora()
        novo = {
            "id": str(uuid.uuid4()),
            "cliente": cliente,
            "produto_id": produto_id,
            "produto_nome": produto["nome"],
            "produto_emoji": produto.get("emoji", "👜"),
            "quantidade": quantidade,
            "preco_unitario": preco_unit,
            "valor_total": round(preco_unit * quantidade, 2),
            "status": "Concluído" if usar_pronta else "Pendente",
            "materiais_baixados": 1 if usar_pronta else 0,
            "usou_estoque_pronto": 1 if usar_pronta else 0,
            "data_pedido": dt_pedido.strftime("%d/%m/%Y"),
            "data_pedido_iso": dt_pedido.strftime("%Y-%m-%d %H:%M:%S"),
            "data_entrega": data_entrega,
            "google_event_id": "",
            "google_calendar_synced_at": "",
            "observacoes": observacoes,
            "origem": "web",
            "telefone_cliente": "",
        }
        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            if usar_pronta:
                cur.execute("UPDATE produtos SET estoque_pronto=?, updated_at=? WHERE id=?", (estoque_pronto_atual - quantidade, dt_pedido.isoformat(), produto_id))
                cur.execute(
                    "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        "estoque_pronto",
                        produto["nome"],
                        quantidade,
                        "unidades",
                        f"Atendimento de pedido de {cliente} usando peça pronta em estoque",
                        dt_pedido.strftime("%d/%m/%Y %H:%M"),
                        session.get("user_id"),
                        dt_pedido.isoformat()
                    )
                )
            cur.execute(
                "INSERT INTO pedidos (id,cliente,produto_id,produto_nome,produto_emoji,quantidade,preco_unitario,valor_total,status,materiais_baixados,usou_estoque_pronto,data_pedido,data_pedido_iso,data_entrega,google_event_id,google_calendar_synced_at,origem,telefone_cliente,observacoes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    novo["id"], novo["cliente"], novo["produto_id"], novo["produto_nome"], novo["produto_emoji"], novo["quantidade"], novo["preco_unitario"], novo["valor_total"], novo["status"], novo["materiais_baixados"], novo["usou_estoque_pronto"], novo["data_pedido"], novo["data_pedido_iso"], novo["data_entrega"], novo["google_event_id"], novo["google_calendar_synced_at"], novo["origem"], novo["telefone_cliente"], novo["observacoes"], dt_pedido.isoformat(), dt_pedido.isoformat()
                )
            )
            conn.commit()
            conn.close()
        else:
            if usar_pronta:
                produto["estoque_pronto"] = estoque_pronto_atual - quantidade
                salvar_produtos(produtos_lista)
            pedidos_lista = carregar_json("pedidos.json")
            pedidos_lista.append(novo)
            salvar_json("pedidos.json", pedidos_lista)

        # Sincronização opcional automática com o Google Calendar
        if data_entrega:
            try:
                sync_res = criar_ou_atualizar_evento_google_calendar(novo, session.get("user_id"))
                if sync_res.get("success"):
                    novo["google_event_id"] = sync_res.get("event_id")
            except Exception:
                pass

        if usar_pronta:
            flash(f"Pedido de {cliente} registrado e atendido imediatamente com {quantidade} peça(s) pronta(s) do estoque!")
        else:
            flash(f"Pedido de {cliente} registrado com sucesso.")
        return redirect(url_for("pedidos"))

    gtin_inicial = request.args.get("gtin", "").strip()
    produto_id_inicial = request.args.get("produto_id", "").strip()

    return render_template(
        "pedido_form.html",
        produtos=produtos_lista,
        gtin_inicial=gtin_inicial,
        produto_id_inicial=produto_id_inicial
    )


@app.route("/pedidos/<pedido_id>/status", methods=["POST"])
@requires_permission('pedidos', 'update')
def pedido_status(pedido_id):
    novo_status = request.form.get("status", "").strip()
    if novo_status not in STATUS_PEDIDO:
        flash("Status inválido.")
        return redirect(url_for("pedidos"))

    now = agora()
    now_iso = now.isoformat()
    now_str = now.strftime("%d/%m/%Y %H:%M")
    usuario_id = session.get("user_id") if session else None

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
            p = cur.fetchone()
            if not p:
                conn.rollback()
                flash("Pedido não encontrado.")
                return redirect(url_for("pedidos"))

            # Se o pedido for cancelado e já houve materiais baixados ou peça pronta usada, armazena a bolsa no estoque de pronta-entrega
            if novo_status == "Cancelado" and bool(p["materiais_baixados"]):
                cur.execute("SELECT estoque_pronto FROM produtos WHERE id=?", (p["produto_id"],))
                row_prod = cur.fetchone()
                est_atual = int(row_prod[0] or 0) if row_prod else 0
                qtd_ped = int(p["quantidade"] or 1)
                cur.execute("UPDATE produtos SET estoque_pronto=?, updated_at=? WHERE id=?", (est_atual + qtd_ped, now_iso, p["produto_id"]))
                cur.execute(
                    "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        "estoque_pronto",
                        p["produto_nome"],
                        qtd_ped,
                        "unidades",
                        f"Bolsa armazenada no estoque de pronta-entrega após cancelamento do pedido de {p['cliente']}",
                        now_str,
                        usuario_id,
                        now_iso
                    )
                )
                cur.execute("UPDATE pedidos SET status=?, updated_at=? WHERE id=?", (novo_status, now_iso, pedido_id))
                conn.commit()
                flash(f'Pedido de {p["cliente"]} cancelado. {qtd_ped}x {p["produto_nome"]} foi guardada no estoque de peças prontas para outro cliente.')
                # Atualiza Google Calendar
                try:
                    cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
                    p_atualizado = dict(cur.fetchone())
                    if p_atualizado.get("google_event_id") or p_atualizado.get("data_entrega"):
                        criar_ou_atualizar_evento_google_calendar(p_atualizado, usuario_id)
                except Exception:
                    pass
                return redirect(url_for("pedidos"))

            # Se for mover para produção, concluído ou entregue e ainda não deu baixa automática dos materiais da receita
            if novo_status in ("Em produção", "Concluído", "Entregue") and not bool(p["materiais_baixados"]):
                cur.execute("SELECT * FROM produtos WHERE id=?", (p["produto_id"],))
                pr = cur.fetchone()
                if pr and pr["receita"]:
                    receita = []
                    try:
                        receita = json.loads(pr["receita"]) if isinstance(pr["receita"], str) else (pr["receita"] or [])
                    except Exception:
                        receita = []

                    if not isinstance(receita, list):
                        receita = []

                    # Pre-check disponibilidade dos materiais
                    insufficient = []
                    missing = []
                    qtd_pedido = float(p["quantidade"] or 1)

                    for item in receita:
                        mat_id = item.get("material_id")
                        qtd_por_unidade = float(item.get("quantidade") or 0)
                        total = round(qtd_por_unidade * qtd_pedido, 3)
                        cur.execute("SELECT quantidade, unidade, nome FROM materiais WHERE id=?", (mat_id,))
                        mat = cur.fetchone()
                        if not mat:
                            missing.append(str(mat_id))
                        else:
                            unidade = (mat["unidade"] or "").lower()
                            if unidade in ("unidades", "unidade", "unid") and abs(total - int(total)) > 1e-9:
                                insufficient.append(f"{mat['nome']}: quantidade precisa ser inteira (calculada {total})")
                            elif float(mat["quantidade"] or 0) < total:
                                insufficient.append(f"{mat['nome']}: estoque insuficiente ({mat['quantidade']} < {total})")

                    if missing or insufficient:
                        msg_parts = []
                        if missing:
                            msg_parts.append('Materiais ausentes: ' + ', '.join(missing))
                        if insufficient:
                            msg_parts.extend(insufficient)
                        conn.rollback()
                        flash('Não foi possível concluir a baixa automática: ' + '; '.join(msg_parts))
                        return redirect(url_for('pedidos'))

                    # Executa as deduções de materiais e registra movimentações na mesma transação atômica
                    for item in receita:
                        mat_id = item.get("material_id")
                        qtd_por_unidade = float(item.get("quantidade") or 0)
                        total = round(qtd_por_unidade * qtd_pedido, 3)
                        cur.execute("SELECT quantidade, unidade, nome FROM materiais WHERE id=?", (mat_id,))
                        mat = cur.fetchone()
                        if mat:
                            nova_qtd = round(max(0.0, float(mat["quantidade"] or 0) - total), 3)
                            cur.execute("UPDATE materiais SET quantidade=?, updated_at=? WHERE id=?", (nova_qtd, now_iso, mat_id))
                            cur.execute(
                                "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    str(uuid.uuid4()),
                                    "producao",
                                    mat["nome"],
                                    total,
                                    mat["unidade"],
                                    f"Produção — pedido de {p['cliente']}",
                                    now_str,
                                    usuario_id,
                                    now_iso
                                )
                            )

                    cur.execute("UPDATE pedidos SET materiais_baixados=1, status=?, updated_at=? WHERE id=?", (novo_status, now_iso, pedido_id))
            else:
                cur.execute("UPDATE pedidos SET status=?, updated_at=? WHERE id=?", (novo_status, now_iso, pedido_id))

            conn.commit()

            # Atualiza o Google Calendar caso o pedido possua evento vinculado ou data de entrega
            try:
                cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
                p_atualizado = dict(cur.fetchone())
                if p_atualizado.get("google_event_id") or p_atualizado.get("data_entrega"):
                    criar_ou_atualizar_evento_google_calendar(p_atualizado, usuario_id)
            except Exception:
                pass

            flash(f'Pedido de {p["cliente"]} atualizado para "{novo_status}".')
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            flash("Erro ao atualizar status do pedido: " + str(e))
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return redirect(url_for("pedidos"))

    # Legacy JSON path
    pedidos_lista = carregar_json("pedidos.json")
    pedido = next((p for p in pedidos_lista if p["id"] == pedido_id), None)
    if not pedido:
        flash("Pedido não encontrado.")
        return redirect(url_for("pedidos"))

    pedido["status"] = novo_status
    if novo_status in ("Em produção", "Concluído", "Entregue") and not pedido.get("materiais_baixados"):
        produtos_lista = carregar_json("produtos.json")
        produto = next((pr for pr in produtos_lista if pr["id"] == pedido["produto_id"]), None)
        if produto and produto.get("receita"):
            materiais = carregar_materiais()
            missing = []
            insufficient = []
            qtd_pedido = float(pedido.get("quantidade") or 1)
            for item in produto["receita"]:
                m = encontrar(materiais, item["material_id"])
                total = round(item["quantidade"] * qtd_pedido, 3)
                if not m:
                    missing.append(item.get("material_id"))
                else:
                    unidade = (m.get("unidade") or "").lower()
                    if unidade in ("unidades", "unidade", "unid") and abs(total - int(total)) > 1e-9:
                        insufficient.append(f"{m.get('nome')}: quantidade precisa ser inteira (calculada {total})")
                    elif float(m.get("quantidade") or 0) < total:
                        insufficient.append(f"{m.get('nome')}: estoque insuficiente ({m.get('quantidade')} < {total})")
            if missing or insufficient:
                msg = []
                if missing:
                    msg.append('Materiais ausentes: ' + ', '.join(missing))
                if insufficient:
                    msg.extend(insufficient)
                flash('Não foi possível concluir a baixa automática: ' + '; '.join(msg))
                return redirect(url_for("pedidos"))
            else:
                for item in produto["receita"]:
                    m = encontrar(materiais, item["material_id"])
                    total = round(item["quantidade"] * qtd_pedido, 3)
                    if m:
                        m["quantidade"] = round(max(0, m["quantidade"] - total), 3)
                        registrar_movimentacao("producao", total, m["unidade"], f"Produção — pedido de {pedido['cliente']}", m["nome"])
                salvar_materiais(materiais)
                pedido["materiais_baixados"] = True

    salvar_json("pedidos.json", pedidos_lista)

    if pedido.get("google_event_id") or pedido.get("data_entrega"):
        try:
            criar_ou_atualizar_evento_google_calendar(pedido, usuario_id)
        except Exception:
            pass

    flash(f'Pedido de {pedido["cliente"]} atualizado para "{novo_status}".')
    return redirect(url_for("pedidos"))


@app.route("/pedidos/<pedido_id>/excluir", methods=["POST"])
@requires_permission('pedidos', 'delete')
def pedido_excluir(pedido_id):
    usuario_id = session.get("user_id") if session else None
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT google_event_id FROM pedidos WHERE id=?", (pedido_id,))
        row = cur.fetchone()
        if row and row["google_event_id"]:
            try:
                excluir_evento_google_calendar(row["google_event_id"], usuario_id)
            except Exception:
                pass
        cur.execute("DELETE FROM pedidos WHERE id=?", (pedido_id,))
        conn.commit()
        conn.close()
        flash("Pedido removido.")
        return redirect(url_for("pedidos"))

    pedidos_lista = carregar_json("pedidos.json")
    p_alvo = next((p for p in pedidos_lista if p["id"] == pedido_id), None)
    if p_alvo and p_alvo.get("google_event_id"):
        try:
            excluir_evento_google_calendar(p_alvo["google_event_id"], usuario_id)
        except Exception:
            pass
    pedidos_lista = [p for p in pedidos_lista if p["id"] != pedido_id]
    salvar_json("pedidos.json", pedidos_lista)
    flash("Pedido removido.")
    return redirect(url_for("pedidos"))


# ── Agenda & Calendário de Entregas (Google Calendar) ─────────────────────────

@app.route("/calendario")
@app.route("/pedidos/calendario")
@requires_permission("pedidos", "read")
def calendario_entregas():
    mes_param = request.args.get("mes", "").strip()
    hoje = agora().date()
    ano_atual = hoje.year
    mes_atual = hoje.month

    if mes_param and re.match(r"^\d{4}-\d{2}$", mes_param):
        try:
            partes = mes_param.split("-")
            ano_atual = int(partes[0])
            mes_atual = int(partes[1])
        except Exception:
            ano_atual = hoje.year
            mes_atual = hoje.month

    if mes_atual == 1:
        mes_ant = f"{ano_atual - 1}-12"
    else:
        mes_ant = f"{ano_atual}-{str(mes_atual - 1).zfill(2)}"

    if mes_atual == 12:
        mes_prox = f"{ano_atual + 1}-01"
    else:
        mes_prox = f"{ano_atual}-{str(mes_atual + 1).zfill(2)}"

    nomes_meses = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    nome_mes = f"{nomes_meses[mes_atual]} de {ano_atual}"

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos ORDER BY data_pedido_iso DESC")
        rows = cur.fetchall()
        conn.close()
        todos_pedidos = []
        for r in rows:
            keys = r.keys() if hasattr(r, 'keys') else []
            origem = r["origem"] if "origem" in keys and r["origem"] else ("whatsapp" if "whatsapp" in str(r["observacoes"] or "").lower() else "web")
            tel = r["telefone_cliente"] if "telefone_cliente" in keys and r["telefone_cliente"] else ""
            data_entrega = r["data_entrega"] if "data_entrega" in keys and r["data_entrega"] else ""
            google_event_id = r["google_event_id"] if "google_event_id" in keys and r["google_event_id"] else ""
            google_calendar_synced_at = r["google_calendar_synced_at"] if "google_calendar_synced_at" in keys and r["google_calendar_synced_at"] else ""
            todos_pedidos.append({
                "id": r["id"],
                "cliente": r["cliente"],
                "produto_id": r["produto_id"],
                "produto_nome": r["produto_nome"],
                "produto_emoji": r["produto_emoji"],
                "quantidade": r["quantidade"],
                "preco_unitario": r["preco_unitario"],
                "valor_total": r["valor_total"],
                "status": r["status"],
                "materiais_baixados": bool(r["materiais_baixados"]),
                "data_pedido": r["data_pedido"],
                "data_pedido_iso": r["data_pedido_iso"],
                "observacoes": r["observacoes"],
                "origem": origem,
                "telefone_cliente": tel,
                "data_entrega": data_entrega,
                "google_event_id": google_event_id,
                "google_calendar_synced_at": google_calendar_synced_at,
            })
    else:
        todos_pedidos = carregar_json("pedidos.json")
        for p in todos_pedidos:
            if "data_entrega" not in p:
                p["data_entrega"] = ""
            if "google_event_id" not in p:
                p["google_event_id"] = ""
            if "google_calendar_synced_at" not in p:
                p["google_calendar_synced_at"] = ""

    pedidos_por_data = {}
    pedidos_com_entrega = []
    pedidos_sem_entrega = []
    total_agendados = 0
    total_atrasados = 0
    total_hoje = 0
    total_semana = 0

    hoje_iso = hoje.strftime("%Y-%m-%d")
    em_7_dias = (hoje + timedelta(days=7)).strftime("%Y-%m-%d")

    for p in todos_pedidos:
        dt_ent = (p.get("data_entrega") or "").strip()
        if dt_ent:
            if "/" in dt_ent:
                try:
                    pt = dt_ent.split("/")
                    if len(pt) == 3:
                        dt_ent = f"{pt[2]}-{pt[1].zfill(2)}-{pt[0].zfill(2)}"
                        p["data_entrega"] = dt_ent
                except Exception:
                    pass

            pedidos_com_entrega.append(p)
            if dt_ent not in pedidos_por_data:
                pedidos_por_data[dt_ent] = []
            pedidos_por_data[dt_ent].append(p)

            if p.get("status") not in ("Entregue", "Cancelado"):
                total_agendados += 1
                if dt_ent < hoje_iso:
                    total_atrasados += 1
                elif dt_ent == hoje_iso:
                    total_hoje += 1
                elif hoje_iso < dt_ent <= em_7_dias:
                    total_semana += 1
        else:
            if p.get("status") not in ("Entregue", "Cancelado"):
                pedidos_sem_entrega.append(p)

    pedidos_com_entrega.sort(key=lambda x: x.get("data_entrega", ""))

    cal_obj = calendar.Calendar(firstweekday=6) # Começa no Domingo
    dias_grade = []
    for d in cal_obj.itermonthdates(ano_atual, mes_atual):
        d_iso = d.strftime("%Y-%m-%d")
        dias_grade.append({
            "data": d,
            "data_iso": d_iso,
            "dia": d.day,
            "mes": d.month,
            "ano": d.year,
            "mesmo_mes": (d.month == mes_atual),
            "e_hoje": (d == hoje),
            "pedidos": pedidos_por_data.get(d_iso, [])
        })

    usuario_id = session.get("user_id") if session else None
    google_cal_info = verificar_conexao_google_calendar(usuario_id)

    return render_template(
        "calendario_entregas.html",
        nome_mes=nome_mes,
        mes_atual_iso=f"{ano_atual}-{str(mes_atual).zfill(2)}",
        mes_ant=mes_ant,
        mes_prox=mes_prox,
        dias_grade=dias_grade,
        pedidos_com_entrega=pedidos_com_entrega,
        pedidos_sem_entrega=pedidos_sem_entrega,
        hoje_iso=hoje_iso,
        total_agendados=total_agendados,
        total_atrasados=total_atrasados,
        total_hoje=total_hoje,
        total_semana=total_semana,
        google_cal_info=google_cal_info,
        status_lista=STATUS_PEDIDO,
        status_badge=STATUS_PEDIDO_BADGE
    )


@app.route("/pedidos/<pedido_id>/data-entrega", methods=["POST"])
@requires_permission('pedidos', 'update')
def pedido_data_entrega(pedido_id):
    nova_data = request.form.get("data_entrega", "").strip()
    if "/" in nova_data:
        try:
            pt = nova_data.split("/")
            if len(pt) == 3:
                nova_data = f"{pt[2]}-{pt[1].zfill(2)}-{pt[0].zfill(2)}"
        except Exception:
            pass

    now_iso = agora().isoformat()
    p_atualizado = None
    usuario_id = session.get("user_id") if session else None

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            flash("Pedido não encontrado.")
            return redirect(request.referrer or url_for("pedidos"))
        cur.execute("UPDATE pedidos SET data_entrega=?, updated_at=? WHERE id=?", (nova_data, now_iso, pedido_id))
        conn.commit()
        cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
        p_atualizado = dict(cur.fetchone())
        conn.close()
    else:
        pedidos = carregar_json("pedidos.json")
        for p in pedidos:
            if p["id"] == pedido_id:
                p["data_entrega"] = nova_data
                p["updated_at"] = now_iso
                p_atualizado = p
                break
        salvar_json("pedidos.json", pedidos)

    if not p_atualizado:
        flash("Pedido não encontrado.")
        return redirect(request.referrer or url_for("pedidos"))

    # Sincroniza com o Google Calendar
    if nova_data:
        sync_res = criar_ou_atualizar_evento_google_calendar(p_atualizado, usuario_id)
        if sync_res.get("success"):
            flash(f"Data de entrega definida para {filtro_data_br(nova_data)} e sincronizada no Google Calendar!")
        else:
            flash(f"Data de entrega salva ({filtro_data_br(nova_data)}). Google Calendar: {sync_res.get('reason') or sync_res.get('error') or 'não configurado'}")
    else:
        if p_atualizado.get("google_event_id"):
            excluir_evento_google_calendar(p_atualizado.get("google_event_id"), usuario_id)
            _atualizar_pedido_sync_calendar(pedido_id, "", "")
        flash("Data de entrega removida.")

    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<pedido_id>/sync-calendar", methods=["POST"])
@requires_permission('pedidos', 'update')
def pedido_sync_calendar(pedido_id):
    p_alvo = None
    usuario_id = session.get("user_id") if session else None

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            p_alvo = dict(row)
    else:
        pedidos = carregar_json("pedidos.json")
        p_alvo = next((p for p in pedidos if p["id"] == pedido_id), None)

    if not p_alvo:
        flash("Pedido não encontrado.")
        return redirect(request.referrer or url_for("pedidos"))

    if not p_alvo.get("data_entrega"):
        flash("Defina uma data de entrega antes de sincronizar com a agenda.")
        return redirect(request.referrer or url_for("pedidos"))

    res = criar_ou_atualizar_evento_google_calendar(p_alvo, usuario_id)
    if res.get("success"):
        flash(f"✅ Pedido de {p_alvo.get('cliente')} sincronizado com sucesso no Google Calendar!")
    else:
        motivo = res.get("reason") or res.get("error") or "Falha desconhecida"
        flash(f"Não foi possível sincronizar com o Google Calendar: {motivo}")

    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/sync-calendar-todos", methods=["POST"])
@requires_permission('pedidos', 'update')
def pedidos_sync_calendar_todos():
    usuario_id = session.get("user_id") if session else None
    res = sincronizar_todos_pedidos_google_calendar(usuario_id)
    total = res.get("total", 0)
    sucessos = res.get("sucessos", 0)
    erros = res.get("erros", 0)

    if total == 0:
        flash("Nenhum pedido ativo com data de entrega encontrado para sincronizar.")
    elif erros == 0:
        flash(f"✅ Sucesso total! Todos os {sucessos} pedido(s) foram sincronizados no Google Calendar.")
    else:
        flash(f"Sincronização: {sucessos} pedido(s) sincronizados com sucesso, {erros} com erro.")

    return redirect(request.referrer or url_for("calendario_entregas"))


# ── Sobras e Reaproveitamento ──────────────────────────────────────────────────
@app.route("/sobras")
@requires_permission('sobras', 'read')
def sobras():
    return render_template("sobras.html", sobras=carregar_sobras(), status_badge=STATUS_SOBRA_BADGE)


@app.route("/sobras/novo", methods=["GET", "POST"])
@requires_permission('sobras', 'create')
def sobra_novo():
    materiais = carregar_materiais()

    if request.method == "POST":
        material_id = request.form.get("material_id", "").strip()
        descricao = request.form.get("descricao", "").strip()
        tipo_entrada = request.form.get("tipo_entrada", "direto").strip()

        m = encontrar(materiais, material_id) if material_id else None

        if tipo_entrada == "dimensoes":
            comp = parse_float_ptbr(request.form.get("comprimento_cm", 0))
            larg = parse_float_ptbr(request.form.get("largura_cm", 0))
            if comp <= 0 or larg <= 0:
                flash("Informe comprimento e largura válidos em centímetros (maiores que zero).")
                return redirect(url_for("sobra_novo"))

            area_m2 = round((comp * larg) / 10000.0, 4)
            # Para tecidos/courino em rolo padrão (1,40m largura), calcula fração correspondente
            equiv_metros = max(0.001, round(area_m2 / 1.40, 3))
            quantidade = equiv_metros
            unidade = "metros"

            if not descricao:
                m_nome = m["nome"] if m else "Material"
                descricao = f"Retalho de {m_nome} {comp:.0f}x{larg:.0f} cm ({area_m2:.2f} m²)"
        else:
            quantidade = parse_float_ptbr(request.form.get("quantidade", 0))
            unidade = request.form.get("unidade", "unidades")

        if not descricao:
            descricao = (m["nome"] + " (Sobra)") if m else "Sobra sem descrição"

        if quantidade <= 0:
            flash("Informe uma quantidade válida para a sobra.")
            return redirect(url_for("sobra_novo"))

        # Regra: se informou material_id, ele OBRIGATORIAMENTE precisa existir no estoque
        if material_id:
            if not m:
                flash("Não foi possível registrar a sobra: o material selecionado não existe no estoque.")
                return redirect(url_for("sobra_novo"))

            unidade_mat = (m.get("unidade") or "unidades").lower()
            is_inteiro = unidade_mat.startswith("unid") or unidade_mat in ("unidades", "pares", "pacotes")
            if is_inteiro:
                if abs(quantidade - round(quantidade)) > 1e-6:
                    flash(f"Para o material '{m.get('nome')}' (unidade: {m.get('unidade')}), utilize apenas números inteiros.")
                    return redirect(url_for("sobra_novo"))
                quantidade = int(round(quantidade))
                unidade = m.get("unidade", "unidades")

            saldo = float(m.get("quantidade") or 0)
            if quantidade > saldo:
                flash(f"Não foi possível registrar a sobra: estoque insuficiente para '{m.get('nome')}'. Saldo atual: {saldo} {m.get('unidade')}.")
                return redirect(url_for("sobra_novo"))

        now = agora()
        now_iso = now.isoformat()
        now_str = now.strftime("%d/%m/%Y %H:%M")

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                sobra_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO sobras (id,material_id,descricao,quantidade,unidade,data,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sobra_id, material_id or None, descricao, quantidade, unidade, now.strftime("%d/%m/%Y"), "Disponível", now_iso, now_iso)
                )
                if material_id and m:
                    saldo_mat = float(m.get("quantidade") or 0)
                    unidade_mat = (m.get("unidade") or "").lower()
                    is_inteiro = unidade_mat.startswith("unid") or unidade_mat in ("unidades", "pares", "pacotes")
                    nova_qtd = max(0, int(saldo_mat) - int(quantidade)) if is_inteiro else round(max(0.0, saldo_mat - float(quantidade)), 3)
                    cur.execute(
                        "UPDATE materiais SET quantidade=?, updated_at=? WHERE id=?",
                        (nova_qtd, now_iso, material_id)
                    )
                    cur.execute(
                        "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), "sobra", m["nome"], quantidade, unidade, f"Sobra registrada: {descricao}", now_str, session.get("user_id"), now_iso)
                    )
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                flash('Erro ao registrar sobra: ' + str(e))
                return redirect(url_for("sobra_novo"))
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            flash(f'Sobra "{descricao}" registrada.')
            return redirect(url_for("sobras"))

        sobras_lista = carregar_json("sobras.json")
        sobras_lista.append({
            "id": str(uuid.uuid4()),
            "material_id": material_id or None,
            "descricao": descricao,
            "quantidade": quantidade,
            "unidade": unidade,
            "data": now.strftime("%d/%m/%Y"),
            "status": "Disponível",
        })
        if material_id and m:
            saldo_mat = float(m.get("quantidade") or 0)
            unidade_mat = (m.get("unidade") or "").lower()
            is_inteiro = unidade_mat.startswith("unid") or unidade_mat in ("unidades", "pares", "pacotes")
            nova_qtd = max(0, int(saldo_mat) - int(quantidade)) if is_inteiro else round(max(0.0, saldo_mat - float(quantidade)), 3)
            m["quantidade"] = nova_qtd
            salvar_materiais(materiais)
            registrar_movimentacao("sobra", quantidade, unidade, f"Sobra registrada: {descricao}", m.get("nome"))
        salvar_json("sobras.json", sobras_lista)
        flash(f'Sobra "{descricao}" registrada.')
        return redirect(url_for("sobras"))

    return render_template("sobra_form.html", materiais=materiais, unidades=UNIDADES)


@app.route("/sobras/<sobra_id>/reaproveitar", methods=["POST"])
@requires_permission('sobras', 'update')
def sobra_reaproveitar(sobra_id):
    now = agora()
    now_iso = now.isoformat()
    now_str = now.strftime("%d/%m/%Y %H:%M")
    usuario_id = session.get("user_id") if session else None

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT * FROM sobras WHERE id=?", (sobra_id,))
            s = cur.fetchone()
            if not s:
                conn.rollback()
                flash('Registro de sobra não encontrado.')
                return redirect(url_for('sobras'))
            if s["status"] != "Disponível":
                conn.rollback()
                flash('Esta sobra já foi processada ou não está disponível.')
                return redirect(url_for('sobras'))

            if s["material_id"]:
                cur.execute("SELECT quantidade, unidade, nome FROM materiais WHERE id=?", (s["material_id"],))
                mat = cur.fetchone()
                if not mat:
                    conn.rollback()
                    flash('Não foi possível reaproveitar: o material vinculado não existe no estoque.')
                    return redirect(url_for('sobras'))
                try:
                    unidade_mat = (mat["unidade"] or "").lower()
                    is_inteiro = unidade_mat.startswith("unid") or unidade_mat in ("unidades", "pares", "pacotes")
                    if is_inteiro:
                        nova = int(mat["quantidade"] or 0) + int(round(float(s["quantidade"] or 0)))
                    else:
                        nova = round(float(mat["quantidade"] or 0) + float(s["quantidade"] or 0), 3)
                except Exception:
                    conn.rollback()
                    flash('Quantidade inválida na sobra ou no material.')
                    return redirect(url_for('sobras'))
                cur.execute("UPDATE materiais SET quantidade=?, updated_at=? WHERE id=?", (nova, now_iso, s["material_id"]))
                cur.execute(
                    "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        "reaproveitamento",
                        mat["nome"],
                        float(s["quantidade"] or 0),
                        s["unidade"],
                        f"Sobra reaproveitada de volta ao estoque (+{s['quantidade']} {s['unidade']})",
                        now_str,
                        usuario_id,
                        now_iso
                    )
                )

            cur.execute("UPDATE sobras SET status=?, updated_at=? WHERE id=?", ("Reaproveitado", now_iso, sobra_id))
            conn.commit()
            flash("Sobra reaproveitada com sucesso.")
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            flash('Erro ao reaproveitar sobra: ' + str(e))
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return redirect(url_for('sobras'))

    sobras_lista = carregar_json("sobras.json")
    sobra = next((s for s in sobras_lista if s["id"] == sobra_id), None)
    if not sobra or sobra["status"] != "Disponível":
        flash("Sobra não disponível para reaproveitamento.")
        return redirect(url_for("sobras"))

    if sobra.get("material_id"):
        materiais = carregar_materiais()
        m = encontrar(materiais, sobra["material_id"])
        if not m:
            flash("Não foi possível reaproveitar: o material vinculado não existe no estoque.")
            return redirect(url_for("sobras"))
        unidade_mat = (m.get("unidade") or "").lower()
        is_inteiro = unidade_mat.startswith("unid") or unidade_mat in ("unidades", "pares", "pacotes")
        if is_inteiro:
            m["quantidade"] = int(m.get("quantidade") or 0) + int(round(float(sobra["quantidade"] or 0)))
        else:
            m["quantidade"] = round(float(m.get("quantidade") or 0) + float(sobra["quantidade"] or 0), 3)
        salvar_materiais(materiais)
        registrar_movimentacao("reaproveitamento", sobra["quantidade"], sobra["unidade"],
                                f"Sobra reaproveitada de volta ao estoque (+{sobra['quantidade']} {sobra['unidade']})", m["nome"])

    sobra["status"] = "Reaproveitado"
    salvar_json("sobras.json", sobras_lista)
    flash(f"{sobra['descricao']} reaproveitada com sucesso.")
    return redirect(url_for("sobras"))


@app.route("/sobras/<sobra_id>/descartar", methods=["POST"])
@requires_permission('sobras', 'update')
def sobra_descartar(sobra_id):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT status FROM sobras WHERE id=?", (sobra_id,))
        r = cur.fetchone()
        if r and r[0] == "Disponível":
            cur.execute("UPDATE sobras SET status=?, updated_at=? WHERE id=?", ("Descartado", agora().isoformat(), sobra_id))
            conn.commit()
        conn.close()
        flash("Sobra marcada como descartada.")
        return redirect(url_for("sobras"))

    sobras_lista = carregar_json("sobras.json")
    sobra = next((s for s in sobras_lista if s["id"] == sobra_id), None)
    if sobra and sobra["status"] == "Disponível":
        sobra["status"] = "Descartado"
        salvar_json("sobras.json", sobras_lista)
        flash(f"{sobra['descricao']} marcada como descartada.")
    return redirect(url_for("sobras"))


@app.route("/sobras/<sobra_id>/excluir", methods=["POST"])
@requires_permission('sobras', 'delete')
def sobra_excluir(sobra_id):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM sobras WHERE id=?", (sobra_id,))
        conn.commit()
        conn.close()
        flash("Registro removido.")
        return redirect(url_for("sobras"))

    sobras_lista = carregar_json("sobras.json")
    sobras_lista = [s for s in sobras_lista if s["id"] != sobra_id]
    salvar_json("sobras.json", sobras_lista)
    flash("Registro removido.")
    return redirect(url_for("sobras"))


# ── Financeiro ─────────────────────────────────────────────────────────────────
@app.route("/financeiro")
@requires_permission('financeiro', 'read')
def financeiro():
    materiais = carregar_materiais()
    valor_estoque = round(sum(m["quantidade"] * m["custo"] for m in materiais), 2)

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos")
        pedidos_rows = cur.fetchall()
        pedidos_lista = [{
            "id": r["id"],
            "status": r["status"],
            "valor_total": r["valor_total"],
        } for r in pedidos_rows]
        receita_entregue = round(sum(p["valor_total"] for p in pedidos_lista if p["status"] == "Entregue"), 2)
        receita_prevista = round(sum(p["valor_total"] for p in pedidos_lista if p["status"] in ("Pendente", "Em produção", "Concluído")), 2)

        cur.execute("SELECT * FROM despesas ORDER BY created_at DESC")
        despesas_rows = cur.fetchall()
        despesas = [{"id": r["id"], "descricao": r["descricao"], "valor": r["valor"], "categoria": r["categoria"], "data": r["data"]} for r in despesas_rows]
        conn.close()
        total_despesas = round(sum(d["valor"] for d in despesas), 2)
        lucro = round(receita_entregue - total_despesas, 2)

        return render_template(
            "financeiro.html",
            valor_estoque=valor_estoque,
            receita_entregue=receita_entregue,
            receita_prevista=receita_prevista,
            despesas=list(reversed(despesas)),
            total_despesas=total_despesas,
            lucro=lucro,
            categorias_despesa=CATEGORIAS_DESPESA,
        )

    # legacy JSON path
    pedidos_lista = carregar_json("pedidos.json")
    receita_entregue = round(sum(p["valor_total"] for p in pedidos_lista if p["status"] == "Entregue"), 2)
    receita_prevista = round(
        sum(p["valor_total"] for p in pedidos_lista if p["status"] in ("Pendente", "Em produção", "Concluído")), 2
    )

    despesas = carregar_json("despesas.json")
    total_despesas = round(sum(d["valor"] for d in despesas), 2)
    lucro = round(receita_entregue - total_despesas, 2)

    return render_template(
        "financeiro.html",
        valor_estoque=valor_estoque,
        receita_entregue=receita_entregue,
        receita_prevista=receita_prevista,
        despesas=list(reversed(despesas)),
        total_despesas=total_despesas,
        lucro=lucro,
        categorias_despesa=CATEGORIAS_DESPESA,
    )


@app.route("/financeiro/despesa", methods=["POST"])
@requires_permission('financeiro', 'create')
def financeiro_despesa():
    descricao = request.form.get("descricao", "").strip()
    valor = parse_float_ptbr(request.form.get("valor", 0))
    categoria = request.form.get("categoria", "Outros")

    if not descricao or valor <= 0:
        flash("Informe descrição e valor válidos para a despesa.")
        return redirect(url_for("financeiro"))

    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = agora().isoformat()
        cur.execute("INSERT INTO despesas (id,descricao,valor,categoria,data,created_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), descricao, float(valor), categoria, agora().strftime("%d/%m/%Y"), now))
        conn.commit()
        conn.close()
        flash(f'Despesa "{descricao}" registrada.')
        return redirect(url_for("financeiro"))

    despesas = carregar_json("despesas.json")
    despesas.append({
        "id": str(uuid.uuid4()),
        "descricao": descricao,
        "valor": valor,
        "categoria": categoria,
        "data": agora().strftime("%d/%m/%Y"),
    })
    salvar_json("despesas.json", despesas)
    flash(f'Despesa "{descricao}" registrada.')
    return redirect(url_for("financeiro"))


@app.route("/financeiro/despesa/<despesa_id>/excluir", methods=["POST"])
@requires_permission('financeiro', 'delete')
def financeiro_despesa_excluir(despesa_id):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM despesas WHERE id=?", (despesa_id,))
        conn.commit()
        conn.close()
        flash("Despesa removida.")
        return redirect(url_for("financeiro"))

    despesas = carregar_json("despesas.json")
    despesas = [d for d in despesas if d["id"] != despesa_id]
    salvar_json("despesas.json", despesas)
    flash("Despesa removida.")
    return redirect(url_for("financeiro"))


# ── Alertas e Relatórios ───────────────────────────────────────────────────────
@app.route("/alertas")
@app.route("/relatorios")
@requires_permission('relatorios', 'read')
def alertas():
    materiais = carregar_materiais()
    pedidos = carregar_pedidos()
    despesas = carregar_despesas()
    relatorios_personalizados = carregar_relatorios_customizados()

    baixo_estoque = sorted(
        [m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)],
        key=lambda m: m.get("quantidade", 0),
    )
    zerados = [m for m in baixo_estoque if m.get("quantidade", 0) == 0]
    valor_em_risco = round(sum(m.get("quantidade_minima", 0) * m.get("custo", 0) for m in baixo_estoque), 2)

    por_categoria = {}
    for m in materiais:
        c = por_categoria.setdefault(m.get("categoria", "Outros"), {"qtd_itens": 0, "valor": 0.0, "emoji": m.get("emoji", "📦")})
        c["qtd_itens"] += 1
        c["valor"] += m.get("quantidade", 0) * m.get("custo", 0)
    for c in por_categoria.values():
        c["valor"] = round(c["valor"], 2)

    movimentacoes = carregar_movimentacoes(30)

    # 1. Payload Gráfico: Estoque por Categoria
    chart_cat_labels = list(por_categoria.keys())
    chart_cat_values = [c["valor"] for c in por_categoria.values()]
    chart_cat_data = {
        "labels": chart_cat_labels,
        "datasets": [{
            "label": "Valor em Estoque (R$)",
            "data": chart_cat_values,
            "backgroundColor": PALETA_CORES_GRAFICO[:len(chart_cat_labels)],
            "borderWidth": 1.5,
            "borderColor": "#FFFFFF"
        }]
    }

    # 2. Payload Gráfico: Balanço Financeiro
    rec_entregue = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") == "Entregue"), 2)
    rec_prevista = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") in ("Pendente", "Em produção", "Concluído")), 2)
    tot_despesas = round(sum(d.get("valor", 0) for d in despesas), 2)
    lucro = round(rec_entregue - tot_despesas, 2)

    chart_fin_data = {
        "labels": ["Receita Recebida", "Receita Prevista", "Despesas Totais", "Lucro Realizado"],
        "datasets": [{
            "label": "Valor (R$)",
            "data": [rec_entregue, rec_prevista, tot_despesas, max(0, lucro)],
            "backgroundColor": ["#2E7D32", "#C88242", "#C62828", "#7C3D12"],
            "borderColor": ["#1B5E20", "#A05A18", "#B71C1C", "#5C2D0E"],
            "borderWidth": 1.5
        }]
    }

    # 3. Payload Gráfico: Status dos Pedidos
    status_counts = {}
    for p in pedidos:
        st = p.get("status", "Pendente")
        status_counts[st] = status_counts.get(st, 0) + 1
    
    chart_ped_labels = list(status_counts.keys())
    chart_ped_data = {
        "labels": chart_ped_labels,
        "datasets": [{
            "label": "Quantidade de Pedidos",
            "data": [status_counts[k] for k in chart_ped_labels],
            "backgroundColor": PALETA_CORES_GRAFICO[:len(chart_ped_labels)],
            "borderWidth": 1.5,
            "borderColor": "#FFFFFF"
        }]
    }

    return render_template(
        "alertas.html",
        baixo_estoque=baixo_estoque,
        zerados=zerados,
        valor_em_risco=valor_em_risco,
        por_categoria=por_categoria,
        movimentacoes=movimentacoes,
        total_materiais=len(materiais),
        relatorios_personalizados=relatorios_personalizados,
        chart_cat_data=json.dumps(chart_cat_data, ensure_ascii=False),
        chart_fin_data=json.dumps(chart_fin_data, ensure_ascii=False),
        chart_ped_data=json.dumps(chart_ped_data, ensure_ascii=False),
    )


# Criar Relatório Personalizado
@app.route("/relatorios/novo", methods=["GET", "POST"])
@requires_permission('relatorios', 'create')
def relatorio_novo():
    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        tipo = request.form.get("tipo", "estoque")
        tipo_grafico = request.form.get("tipo_grafico", "bar")
        categoria_filtro = request.form.get("categoria_filtro", "").strip()
        status_filtro = request.form.get("status_filtro", "").strip()
        apenas_criticos = bool(request.form.get("apenas_criticos"))
        observacoes = request.form.get("observacoes", "").strip()

        if not titulo:
            flash("Informe um título para o relatório.")
            return redirect(url_for("relatorio_novo"))

        criado_por = g.user.get("nome") or g.user.get("username") or "Usuário"
        novo_rel = {
            "titulo": titulo,
            "tipo": tipo,
            "tipo_grafico": tipo_grafico,
            "categoria_filtro": categoria_filtro,
            "status_filtro": status_filtro,
            "apenas_criticos": apenas_criticos,
            "observacoes": observacoes,
            "criado_por": criado_por,
        }
        salvo = salvar_relatorio_customizado(novo_rel)
        flash(f'Relatório "{titulo}" criado com sucesso!')
        return redirect(url_for("relatorio_detalhe", relatorio_id=salvo["id"]))

    return render_template(
        "relatorio_form.html",
        relatorio=None,
        categorias=CATEGORIAS,
        status_pedido=STATUS_PEDIDO,
    )


# Visualizar Relatório Personalizado
@app.route("/relatorios/<relatorio_id>")
@requires_permission('relatorios', 'read')
def relatorio_detalhe(relatorio_id):
    relatorio = encontrar_relatorio_por_id(relatorio_id)
    if not relatorio:
        flash("Relatório não encontrado.")
        return redirect(url_for("alertas"))

    dados = gerar_dados_relatorio(relatorio)
    return render_template(
        "relatorio_detalhe.html",
        relatorio=relatorio,
        dados=dados,
        chart_payload=json.dumps(dados["chart_data"], ensure_ascii=False),
    )


# Editar Relatório Personalizado
@app.route("/relatorios/<relatorio_id>/editar", methods=["GET", "POST"])
@requires_permission('relatorios', 'update')
def relatorio_editar(relatorio_id):
    relatorio = encontrar_relatorio_por_id(relatorio_id)
    if not relatorio:
        flash("Relatório não encontrado.")
        return redirect(url_for("alertas"))

    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        tipo = request.form.get("tipo", "estoque")
        tipo_grafico = request.form.get("tipo_grafico", "bar")
        categoria_filtro = request.form.get("categoria_filtro", "").strip()
        status_filtro = request.form.get("status_filtro", "").strip()
        apenas_criticos = bool(request.form.get("apenas_criticos"))
        observacoes = request.form.get("observacoes", "").strip()

        if not titulo:
            flash("Informe um título para o relatório.")
            return redirect(url_for("relatorio_editar", relatorio_id=relatorio_id))

        relatorio["titulo"] = titulo
        relatorio["tipo"] = tipo
        relatorio["tipo_grafico"] = tipo_grafico
        relatorio["categoria_filtro"] = categoria_filtro
        relatorio["status_filtro"] = status_filtro
        relatorio["apenas_criticos"] = apenas_criticos
        relatorio["observacoes"] = observacoes

        salvar_relatorio_customizado(relatorio)
        flash(f'Relatório "{titulo}" atualizado!')
        return redirect(url_for("relatorio_detalhe", relatorio_id=relatorio_id))

    return render_template(
        "relatorio_form.html",
        relatorio=relatorio,
        categorias=CATEGORIAS,
        status_pedido=STATUS_PEDIDO,
    )


# Excluir Relatório Personalizado
@app.route("/relatorios/<relatorio_id>/excluir", methods=["POST"])
@requires_permission('relatorios', 'delete')
def relatorio_excluir(relatorio_id):
    relatorio = encontrar_relatorio_por_id(relatorio_id)
    if not relatorio:
        flash("Relatório não encontrado.")
        return redirect(url_for("alertas"))

    titulo = relatorio.get("titulo", "Relatório")
    excluir_relatorio_customizado(relatorio_id)
    flash(f'Relatório "{titulo}" excluído.')
    return redirect(url_for("alertas"))


# Serve uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    if filename.startswith('http://') or filename.startswith('https://'):
        return redirect(filename)
    if filename.startswith('http%3A') or filename.startswith('https%3A'):
        return redirect(urllib.parse.unquote(filename))
    uploads_dir = os.path.join(DATA_DIR, 'uploads')
    return send_from_directory(uploads_dir, filename)


# Exportar todos os dados (backup)
@app.route("/exportar")
@requires_permission('relatorios', 'read')
def exportar_tudo():
    colecoes = [
        "materiais.json",
        "produtos.json",
        "pedidos.json",
        "movimentacoes.json",
        "sobras.json",
        "despesas.json",
    ]
    tudo = {}
    for c in colecoes:
        tudo[os.path.splitext(c)[0]] = carregar_json(c)
    resp = make_response(json.dumps(tudo, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=export_all.json"
    return resp


# ── Geração de Relatórios em Memória (PDF e Excel) ───────────────────────────

def gerar_pdf_financeiro_bytes():
    """Gera os bytes do relatório financeiro em PDF com design e paleta do site."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table as RLTable, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_decorations(num_pages)
                super().showPage()
            super().save()

        def draw_page_decorations(self, page_count):
            self.saveState()
            width, height = A4
            # Top banner
            self.setFillColor(colors.HexColor("#7C3D12"))
            self.rect(0, height - 16 * mm, width, 16 * mm, stroke=0, fill=1)

            # Header text
            self.setFillColor(colors.HexColor("#FDE9C2"))
            self.setFont("Helvetica-Bold", 11)
            self.drawString(15 * mm, height - 10.5 * mm, "ATELIE HAITI  -  GESTAO ARTESANAL")

            self.setFont("Helvetica", 8.5)
            self.setFillColor(colors.white)
            self.drawRightString(width - 15 * mm, height - 10.5 * mm, f"Emissao: {agora().strftime('%d/%m/%Y %H:%M')}")

            # Footer line
            self.setStrokeColor(colors.HexColor("#E2D2BC"))
            self.setLineWidth(0.8)
            self.line(15 * mm, 14 * mm, width - 15 * mm, 14 * mm)

            # Footer text
            self.setFont("Helvetica-Oblique", 8)
            self.setFillColor(colors.HexColor("#7A6B63"))
            self.drawString(15 * mm, 9 * mm, "Conectados pela Comunidade - Comunidade do Haiti, SP")
            self.drawRightString(width - 15 * mm, 9 * mm, f"Pagina {self._pageNumber} de {page_count}")
            self.restoreState()

    materiais = carregar_materiais()
    pedidos = carregar_pedidos()
    despesas = carregar_despesas()

    valor_estoque = round(sum(m.get("quantidade", 0) * m.get("custo", 0) for m in materiais), 2)
    receita_entregue = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") == "Entregue"), 2)
    receita_prevista = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") in ("Pendente", "Em produção", "Concluído")), 2)
    total_despesas = round(sum(d.get("valor", 0) for d in despesas), 2)
    lucro = round(receita_entregue - total_despesas, 2)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#7C3D12"),
        spaceAfter=3
    )
    subtitle_style = ParagraphStyle(
        'DocSub',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#7A6B63"),
        spaceAfter=10
    )
    h2_style = ParagraphStyle(
        'H2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#5C2D0E"),
        spaceBefore=8,
        spaceAfter=5
    )
    cell_style = ParagraphStyle(
        'Cell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#2C1810")
    )
    cell_bold = ParagraphStyle(
        'CellBold',
        parent=cell_style,
        fontName='Helvetica-Bold'
    )
    cell_right = ParagraphStyle(
        'CellRight',
        parent=cell_style,
        alignment=2
    )

    elements = []
    elements.append(Paragraph("RELATÓRIO FINANCEIRO &amp; BALANÇO GERAL", title_style))
    elements.append(Paragraph(f"Posição consolidada em {agora().strftime('%d/%m/%Y às %H:%M:%S')}", subtitle_style))
    elements.append(Spacer(1, 3 * mm))

    # Tabela de KPIs
    kpi_data = [
        [
            Paragraph("<b>RECEITA RECEBIDA</b><br/>(Pedidos Entregues)", cell_style),
            Paragraph("<b>RECEITA PREVISTA</b><br/>(Em andamento)", cell_style),
            Paragraph("<b>DESPESAS TOTAIS</b><br/>(Custos operacionais)", cell_style),
            Paragraph("<b>LUCRO LÍQUIDO</b><br/>(Realizado)", cell_style),
            Paragraph("<b>VALOR EM ESTOQUE</b><br/>(Patrimônio insumos)", cell_style),
        ],
        [
            Paragraph(f"<font size='11' color='#2E7D32'><b>R$ {formatar_reais(receita_entregue)}</b></font>", cell_style),
            Paragraph(f"<font size='11' color='#C88242'><b>R$ {formatar_reais(receita_prevista)}</b></font>", cell_style),
            Paragraph(f"<font size='11' color='#C62828'><b>R$ {formatar_reais(total_despesas)}</b></font>", cell_style),
            Paragraph(f"<font size='11' color='#7C3D12'><b>R$ {formatar_reais(lucro)}</b></font>", cell_style),
            Paragraph(f"<font size='11' color='#5C2D0E'><b>R$ {formatar_reais(valor_estoque)}</b></font>", cell_style),
        ]
    ]
    t_kpi = RLTable(kpi_data, colWidths=[36 * mm] * 5)
    t_kpi.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FDF8F0")),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2D2BC")),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_kpi)
    elements.append(Spacer(1, 5 * mm))

    # Despesas Recentes
    elements.append(Paragraph("Detalhamento de Despesas Registradas", h2_style))
    desp_hdr = [
        Paragraph("<b>Descrição</b>", cell_bold),
        Paragraph("<b>Categoria</b>", cell_bold),
        Paragraph("<b>Data</b>", cell_bold),
        Paragraph("<b>Valor (R$)</b>", cell_right),
    ]
    desp_table_data = [desp_hdr]
    for d in despesas[-15:]:
        desp_table_data.append([
            Paragraph(f"{d.get('descricao','')}", cell_style),
            Paragraph(str(d.get('categoria','Outros')), cell_style),
            Paragraph(str(d.get('data','-')), cell_style),
            Paragraph(f"R$ {formatar_reais(d.get('valor',0))}", cell_right),
        ])
    if len(desp_table_data) == 1:
        desp_table_data.append([Paragraph("Nenhuma despesa registrada", cell_style), "", "", ""])

    t_desp = RLTable(desp_table_data, colWidths=[70 * mm, 40 * mm, 35 * mm, 35 * mm])
    t_desp.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#7C3D12")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#FDF8F0"), colors.HexColor("#FFFFFF")]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2D2BC")),
    ]))
    elements.append(t_desp)
    elements.append(Spacer(1, 5 * mm))

    # Estoque Crítico
    baixo_estoque = [m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)]
    if baixo_estoque:
        elements.append(Paragraph(f"Alertas de Reposição ({len(baixo_estoque)} itens abaixo do mínimo)", h2_style))
        crit_hdr = [
            Paragraph("<b>Material</b>", cell_bold),
            Paragraph("<b>Categoria</b>", cell_bold),
            Paragraph("<b>Quantidade Atual</b>", cell_bold),
            Paragraph("<b>Mínimo</b>", cell_bold),
            Paragraph("<b>Custo Reposição</b>", cell_right),
        ]
        crit_table_data = [crit_hdr]
        for m in baixo_estoque:
            custo_rep = (m.get("quantidade_minima", 0) - m.get("quantidade", 0)) * m.get("custo", 0)
            if custo_rep < 0:
                custo_rep = 0
            crit_table_data.append([
                Paragraph(f"{m.get('nome','')}", cell_style),
                Paragraph(str(m.get('categoria','')), cell_style),
                Paragraph(f"<font color='#C62828'><b>{m.get('quantidade',0)} {m.get('unidade','')}</b></font>", cell_style),
                Paragraph(f"{m.get('quantidade_minima',0)} {m.get('unidade','')}", cell_style),
                Paragraph(f"R$ {formatar_reais(custo_rep)}", cell_right),
            ])
        t_crit = RLTable(crit_table_data, colWidths=[55 * mm, 35 * mm, 30 * mm, 25 * mm, 35 * mm])
        t_crit.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#C62828")),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#FFF8F8"), colors.HexColor("#FFFFFF")]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2D2BC")),
        ]))
        elements.append(t_crit)

    doc.build(elements, canvasmaker=NumberedCanvas)
    return buf.getvalue()


def gerar_pdf_estoque_baixo_bytes():
    """Gera os bytes do relatório de alerta de estoque crítico em PDF."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table as RLTable, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_decorations(num_pages)
                super().showPage()
            super().save()

        def draw_page_decorations(self, page_count):
            self.saveState()
            width, height = A4
            self.setFillColor(colors.HexColor("#C62828"))
            self.rect(0, height - 16 * mm, width, 16 * mm, stroke=0, fill=1)

            self.setFillColor(colors.white)
            self.setFont("Helvetica-Bold", 11)
            self.drawString(15 * mm, height - 10.5 * mm, "ATELIE HAITI  -  ALERTA DE ESTOQUE CRITICO")

            self.setFont("Helvetica", 8.5)
            self.drawRightString(width - 15 * mm, height - 10.5 * mm, f"Emissao: {agora().strftime('%d/%m/%Y %H:%M')}")

            self.setStrokeColor(colors.HexColor("#E2D2BC"))
            self.setLineWidth(0.8)
            self.line(15 * mm, 14 * mm, width - 15 * mm, 14 * mm)

            self.setFont("Helvetica-Oblique", 8)
            self.setFillColor(colors.HexColor("#7A6B63"))
            self.drawString(15 * mm, 9 * mm, "Conectados pela Comunidade - Comunidade do Haiti, SP")
            self.drawRightString(width - 15 * mm, 9 * mm, f"Pagina {self._pageNumber} de {page_count}")
            self.restoreState()

    materiais = carregar_materiais()
    baixo_estoque = sorted(
        [m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)],
        key=lambda m: m.get("quantidade", 0)
    )
    zerados = [m for m in baixo_estoque if m.get("quantidade", 0) == 0]
    valor_em_risco = round(sum(m.get("quantidade_minima", 0) * m.get("custo", 0) for m in baixo_estoque), 2)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#C62828"),
        spaceAfter=3
    )
    subtitle_style = ParagraphStyle(
        'DocSub',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#7A6B63"),
        spaceAfter=10
    )
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor("#2C1810"))
    cell_bold = ParagraphStyle('CellBold', parent=cell_style, fontName='Helvetica-Bold')
    cell_right = ParagraphStyle('CellRight', parent=cell_style, alignment=2)

    elements = []
    elements.append(Paragraph("RELATÓRIO DE MATERIAIS COM ESTOQUE CRÍTICO", title_style))
    elements.append(Paragraph(f"Lista de reposição gerada em {agora().strftime('%d/%m/%Y às %H:%M:%S')}", subtitle_style))
    elements.append(Spacer(1, 3 * mm))

    # Cards KPI
    kpi_data = [
        [
            Paragraph("<b>TOTAL DE ITENS CRÍTICOS</b>", cell_style),
            Paragraph("<b>ITENS COM ESTOQUE ZERADO</b>", cell_style),
            Paragraph("<b>ESTIMATIVA CUSTO DE REPOSIÇÃO</b>", cell_style),
        ],
        [
            Paragraph(f"<font size='13' color='#C62828'><b>{len(baixo_estoque)}</b></font>", cell_style),
            Paragraph(f"<font size='13' color='#B71C1C'><b>{len(zerados)}</b></font>", cell_style),
            Paragraph(f"<font size='13' color='#7C3D12'><b>R$ {formatar_reais(valor_em_risco)}</b></font>", cell_style),
        ]
    ]
    t_kpi = RLTable(kpi_data, colWidths=[60 * mm, 60 * mm, 60 * mm])
    t_kpi.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FFF8F8")),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2D2BC")),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(t_kpi)
    elements.append(Spacer(1, 6 * mm))

    # Tabela
    crit_hdr = [
        Paragraph("<b>Material</b>", cell_bold),
        Paragraph("<b>Categoria</b>", cell_bold),
        Paragraph("<b>Quantidade Atual</b>", cell_bold),
        Paragraph("<b>Estoque Mínimo</b>", cell_bold),
        Paragraph("<b>Custo Unitário</b>", cell_right),
        Paragraph("<b>Custo Reposição</b>", cell_right),
    ]
    crit_table_data = [crit_hdr]
    for m in baixo_estoque:
        custo_rep = (m.get("quantidade_minima", 0) - m.get("quantidade", 0)) * m.get("custo", 0)
        if custo_rep < 0:
            custo_rep = 0
        crit_table_data.append([
            Paragraph(f"<b>{m.get('nome','')}</b>", cell_style),
            Paragraph(str(m.get('categoria','')), cell_style),
            Paragraph(f"<font color='#C62828'><b>{m.get('quantidade',0)} {m.get('unidade','')}</b></font>", cell_style),
            Paragraph(f"{m.get('quantidade_minima',0)} {m.get('unidade','')}", cell_style),
            Paragraph(f"R$ {formatar_reais(m.get('custo',0))}", cell_right),
            Paragraph(f"<b>R$ {formatar_reais(custo_rep)}</b>", cell_right),
        ])

    if len(crit_table_data) == 1:
        crit_table_data.append([Paragraph("Nenhum material com estoque crítico no momento. Parabéns!", cell_style), "", "", "", "", ""])

    t_crit = RLTable(crit_table_data, colWidths=[48 * mm, 30 * mm, 28 * mm, 24 * mm, 25 * mm, 25 * mm])
    t_crit.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#C62828")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#FFF8F8"), colors.HexColor("#FFFFFF")]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2D2BC")),
    ]))
    elements.append(t_crit)

    doc.build(elements, canvasmaker=NumberedCanvas)
    return buf.getvalue()


def gerar_xlsx_completo_bytes():
    """Gera os bytes da planilha Excel (XLSX) completa estruturada em tabelas."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    wb = Workbook()

    header_fill = PatternFill(start_color="7C3D12", end_color="7C3D12", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=10)
    data_bold = Font(name="Segoe UI", size=10, bold=True)
    thin_border = Border(left=Side(style='thin', color='E2D2BC'), right=Side(style='thin', color='E2D2BC'), top=Side(style='thin', color='E2D2BC'), bottom=Side(style='thin', color='E2D2BC'))
    alt_fill = PatternFill(start_color="FDF8F0", end_color="FDF8F0", fill_type="solid")
    warning_fill = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")
    warning_font = Font(name="Segoe UI", size=10, bold=True, color="C62828")

    ws_resumo = wb.active
    ws_resumo.title = "Resumo Geral"
    ws_resumo.merge_cells("A1:C1")
    ws_resumo["A1"] = "ATELIÊ HAITI — RELATÓRIO GERAL E BALANÇO"
    ws_resumo["A1"].font = Font(name="Segoe UI", size=14, bold=True, color="FFFFFF")
    ws_resumo["A1"].fill = header_fill
    ws_resumo["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws_resumo.row_dimensions[1].height = 34
    ws_resumo["A2"] = f"Relatório gerado em: {agora().strftime('%d/%m/%Y %H:%M:%S')}"
    ws_resumo["A2"].font = Font(name="Segoe UI", size=9.5, italic=True, color="7A6B63")

    materiais = carregar_materiais()
    pedidos = carregar_pedidos()
    despesas = carregar_despesas()
    produtos = carregar_produtos()

    valor_estoque = round(sum(m.get("quantidade", 0) * m.get("custo", 0) for m in materiais), 2)
    receita_entregue = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") == "Entregue"), 2)
    receita_prevista = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") in ("Pendente", "Em produção", "Concluído")), 2)
    total_despesas = round(sum(d.get("valor", 0) for d in despesas), 2)
    lucro = round(receita_entregue - total_despesas, 2)
    baixo_estoque = [m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)]

    indicadores = [
        ("Indicador Financeiro / Operacional", "Valor Consolidado", "Observação"),
        ("Receita Recebida (Pedidos Entregues)", receita_entregue, "Total faturado e entregue aos clientes"),
        ("Receita Prevista (Em Produção/Pendente)", receita_prevista, "Pedidos confirmados a serem entregues"),
        ("Despesas Totais Registradas", total_despesas, "Custos de produção e operacionais"),
        ("Lucro Líquido Realizado", lucro, "Receita Entregue menos Despesas Totais"),
        ("Valor Patrimonial em Estoque", valor_estoque, "Soma de insumos e matérias-primas"),
        ("Total de Materiais em Catálogo", len(materiais), "Tipos de insumos cadastrados"),
        ("Itens em Nível Crítico de Estoque", len(baixo_estoque), "Materiais com quantidade <= mínima"),
        ("Total de Produtos / Receitas", len(produtos), "Modelos artesanais desenvolvidos"),
        ("Total de Pedidos Realizados", len(pedidos), "Histórico de compras de clientes"),
    ]

    for r_idx, row_data in enumerate(indicadores, start=4):
        ws_resumo.row_dimensions[r_idx].height = 22
        for c_idx, val in enumerate(row_data, start=1):
            cell = ws_resumo.cell(row=r_idx, column=c_idx)
            cell.value = val
            cell.border = thin_border
            if r_idx == 4:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center" if c_idx == 2 else "left", vertical="center")
            else:
                cell.font = data_font
                if r_idx % 2 == 1:
                    cell.fill = alt_fill
                if c_idx == 2:
                    if isinstance(val, (int, float)) and r_idx <= 9:
                        cell.number_format = 'R$ #,##0.00'
                        cell.font = data_bold
                    cell.alignment = Alignment(horizontal="right", vertical="center")

    tab_resumo = Table(displayName="TabelaResumo", ref=f"A4:C{len(indicadores) + 3}")
    tab_resumo.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
    ws_resumo.add_table(tab_resumo)

    # 2. ABA ESTOQUE DE MATERIAIS
    ws_mat = wb.create_sheet(title="Estoque de Materiais")
    ws_mat.views.sheetView[0].showGridLines = True
    mat_headers = ["ID", "Nome do Material", "Categoria", "Quantidade", "Unidade", "Qtd Mínima", "Custo Unit.", "Valor Total", "Código GTIN", "Status Estoque"]
    ws_mat.append(mat_headers)
    ws_mat.row_dimensions[1].height = 24

    for r_idx, m in enumerate(materiais, start=2):
        qtd = float(m.get("quantidade", 0))
        qtd_min = float(m.get("quantidade_minima", 0))
        custo = float(m.get("custo", 0))
        val_tot = round(qtd * custo, 2)
        is_critico = (qtd <= qtd_min)
        status_txt = "CRÍTICO" if is_critico else "OK"

        row = [
            m.get("id", ""),
            m.get("nome", ""),
            m.get("categoria", ""),
            qtd,
            m.get("unidade", ""),
            qtd_min,
            custo,
            val_tot,
            m.get("gtin", "") or "-",
            status_txt
        ]
        ws_mat.append(row)
        ws_mat.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(mat_headers) + 1):
            cell = ws_mat.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            cell.font = data_font
            if c_idx in (7, 8):
                cell.number_format = 'R$ #,##0.00'
            elif c_idx in (4, 6):
                cell.number_format = '#,##0.00'
            if is_critico and c_idx in (4, 10):
                cell.fill = warning_fill
                cell.font = warning_font

    for c_idx in range(1, len(mat_headers) + 1):
        cell = ws_mat.cell(row=1, column=c_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if c_idx not in (2, 3) else "left", vertical="center")
        cell.border = thin_border

    if materiais:
        tab_mat = Table(displayName="TabelaEstoque", ref=f"A1:{get_column_letter(len(mat_headers))}{len(materiais) + 1}")
        tab_mat.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws_mat.add_table(tab_mat)

    # 3. ABA PRODUTOS
    ws_prod = wb.create_sheet(title="Produtos e Receitas")
    ws_prod.views.sheetView[0].showGridLines = True
    prod_headers = ["ID", "Produto", "Preço Venda", "Estoque Pronto", "Código GTIN", "Qtd Insumos Receita"]
    ws_prod.append(prod_headers)
    ws_prod.row_dimensions[1].height = 24

    for r_idx, p in enumerate(produtos, start=2):
        receita = p.get("receita", [])
        if isinstance(receita, str):
            try:
                receita = json.loads(receita)
            except Exception:
                receita = []
        row = [
            p.get("id", ""),
            p.get("nome", ""),
            float(p.get("preco_venda", 0)),
            int(p.get("estoque_pronto", 0)),
            p.get("gtin", "") or "-",
            len(receita)
        ]
        ws_prod.append(row)
        ws_prod.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(prod_headers) + 1):
            cell = ws_prod.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            cell.font = data_font
            if c_idx == 3:
                cell.number_format = 'R$ #,##0.00'
    
    for c_idx in range(1, len(prod_headers) + 1):
        cell = ws_prod.cell(row=1, column=c_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if c_idx != 2 else "left", vertical="center")
        cell.border = thin_border

    if produtos:
        tab_prod = Table(displayName="TabelaProdutos", ref=f"A1:{get_column_letter(len(prod_headers))}{len(produtos) + 1}")
        tab_prod.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws_prod.add_table(tab_prod)

    # 4. ABA PEDIDOS
    ws_ped = wb.create_sheet(title="Pedidos")
    ws_ped.views.sheetView[0].showGridLines = True
    ped_headers = ["ID", "Cliente", "Produto", "Quantidade", "Valor Unitário", "Valor Total", "Status", "Data Pedido", "Criado em"]
    ws_ped.append(ped_headers)
    ws_ped.row_dimensions[1].height = 24

    for r_idx, p in enumerate(pedidos, start=2):
        qtd = float(p.get("quantidade", 1))
        val_tot = float(p.get("valor_total", 0))
        val_un = round(val_tot / qtd, 2) if qtd > 0 else 0
        row = [
            p.get("id", ""),
            p.get("cliente", ""),
            p.get("produto_nome", ""),
            qtd,
            val_un,
            val_tot,
            p.get("status", "Pendente"),
            p.get("data_pedido", "") or "-",
            str(p.get("created_at") or "")[:16] if p.get("created_at") else "-"
        ]
        ws_ped.append(row)
        ws_ped.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(ped_headers) + 1):
            cell = ws_ped.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            cell.font = data_font
            if c_idx in (5, 6):
                cell.number_format = 'R$ #,##0.00'

    for c_idx in range(1, len(ped_headers) + 1):
        cell = ws_ped.cell(row=1, column=c_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if c_idx not in (2, 3) else "left", vertical="center")
        cell.border = thin_border

    if pedidos:
        tab_ped = Table(displayName="TabelaPedidos", ref=f"A1:{get_column_letter(len(ped_headers))}{len(pedidos) + 1}")
        tab_ped.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws_ped.add_table(tab_ped)

    # 5. ABA DESPESAS
    ws_desp = wb.create_sheet(title="Despesas")
    ws_desp.views.sheetView[0].showGridLines = True
    desp_headers = ["ID", "Descrição", "Categoria", "Valor", "Data", "Criado em"]
    ws_desp.append(desp_headers)
    ws_desp.row_dimensions[1].height = 24

    for r_idx, d in enumerate(despesas, start=2):
        row = [
            d.get("id", ""),
            d.get("descricao", ""),
            d.get("categoria", "Outros"),
            float(d.get("valor", 0)),
            d.get("data", ""),
            str(d.get("created_at") or "")[:16] if d.get("created_at") else "-"
        ]
        ws_desp.append(row)
        ws_desp.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(desp_headers) + 1):
            cell = ws_desp.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            cell.font = data_font
            if c_idx == 4:
                cell.number_format = 'R$ #,##0.00'
                cell.font = data_bold

    for c_idx in range(1, len(desp_headers) + 1):
        cell = ws_desp.cell(row=1, column=c_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if c_idx != 2 else "left", vertical="center")
        cell.border = thin_border

    if despesas:
        tab_desp = Table(displayName="TabelaDespesas", ref=f"A1:{get_column_letter(len(desp_headers))}{len(despesas) + 1}")
        tab_desp.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws_desp.add_table(tab_desp)

    # 6. ABA SOBRAS
    sobras = carregar_sobras()
    ws_sob = wb.create_sheet(title="Sobras")
    ws_sob.views.sheetView[0].showGridLines = True
    sob_headers = ["ID", "Descrição", "Quantidade", "Unidade", "Data", "Status"]
    ws_sob.append(sob_headers)
    ws_sob.row_dimensions[1].height = 24

    for r_idx, s in enumerate(sobras, start=2):
        row = [
            s.get("id", ""),
            s.get("descricao", ""),
            float(s.get("quantidade", 0)),
            s.get("unidade", ""),
            s.get("data", ""),
            s.get("status", "Disponível")
        ]
        ws_sob.append(row)
        ws_sob.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(sob_headers) + 1):
            cell = ws_sob.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            cell.font = data_font
            if c_idx == 3:
                cell.number_format = '#,##0.00'

    for c_idx in range(1, len(sob_headers) + 1):
        cell = ws_sob.cell(row=1, column=c_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if c_idx != 2 else "left", vertical="center")
        cell.border = thin_border

    if sobras:
        tab_sob = Table(displayName="TabelaSobras", ref=f"A1:{get_column_letter(len(sob_headers))}{len(sobras) + 1}")
        tab_sob.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        ws_sob.add_table(tab_sob)

    # Auto-ajustar largura das colunas
    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if cell.number_format and 'R$' in cell.number_format:
                    val_str = f"R$ {val_str},00"
                max_len = max(max_len, len(val_str))
            sheet.column_dimensions[col_letter].width = max(max_len + 4, 12)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Exportar relatório financeiro em PDF com design e paleta do site
@app.route('/exportar/pdf')
@requires_permission('relatorios', 'read')
def exportar_financeiro_pdf():
    try:
        from io import BytesIO
        pdf_bytes = gerar_pdf_financeiro_bytes()
        buf = BytesIO(pdf_bytes)
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name='relatorio_financeiro_atelie.pdf')
    except Exception as e:
        return str(e), 400


# Exportar todos os dados em Excel (XLSX) estruturado em Tabelas
@app.route('/exportar/xlsx')
@requires_permission('relatorios', 'read')
def exportar_tudo_xlsx():
    try:
        from io import BytesIO
        xlsx_bytes = gerar_xlsx_completo_bytes()
        buf = BytesIO(xlsx_bytes)
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='export_atelie_haiti.xlsx')
    except Exception as e:
        return str(e), 400


# ── Serviço de Disparo de E-mails via Google Gmail API (Usuário-para-Usuário) ──

def obter_configuracoes_email():
    """Retorna o status do provedor de e-mail (Google Gmail API)."""
    sso_cfg = obter_configuracoes_sso()
    return {
        "provider": "google_gmail_api",
        "ativo": sso_cfg.get("ativo", 0),
        "google_client_id": sso_cfg.get("google_client_id", ""),
        "modo_simulacao": 0 if (sso_cfg.get("ativo") and sso_cfg.get("google_client_id")) else 1,
    }


def registrar_historico_email(hist_id, agendamento_id, titulo, tipo_relatorio, destinatarios, status, mensagem_status, enviado_por, created_at):
    """Grava um registro no histórico de envios de e-mail (mantém até 300 registros)."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO historico_envios_email (id, agendamento_id, titulo, tipo_relatorio, destinatarios, status, mensagem_status, enviado_por, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hist_id, agendamento_id, titulo, tipo_relatorio, destinatarios, status, mensagem_status, enviado_por, created_at)
            )
            cur.execute("DELETE FROM historico_envios_email WHERE id NOT IN (SELECT id FROM historico_envios_email ORDER BY created_at DESC LIMIT 300)")
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()


def carregar_historico_emails(limit=100):
    """Carrega o histórico de envios ordenado do mais recente para o mais antigo."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM historico_envios_email ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    return []


def gerar_html_email_relatorio(titulo, mensagem_customizada="", anexos_nomes=None):
    """Monta o template HTML responsivo e estilizado para o corpo do e-mail."""
    materiais = carregar_materiais()
    pedidos = carregar_pedidos()
    despesas = carregar_despesas()

    baixo_estoque = [m for m in materiais if m.get("quantidade", 0) <= m.get("quantidade_minima", 0)]
    rec_entregue = round(sum(p.get("valor_total", 0) for p in pedidos if p.get("status") == "Entregue"), 2)
    tot_despesas = round(sum(d.get("valor", 0) for d in despesas), 2)
    pedidos_ativos = [p for p in pedidos if p.get("status") in ("Pendente", "Em produção", "Concluído")]

    anexos_html = ""
    if anexos_nomes:
        anexos_li = "".join([f"<li style='margin-bottom:4px;'>📎 <strong>{nome}</strong></li>" for nome in anexos_nomes])
        anexos_html = f"""
        <div style='margin-top:18px; padding:12px 16px; background:#f9f5f0; border-radius:8px; border-left:4px solid #7C3D12;'>
          <p style='margin:0 0 6px 0; font-size:14px; font-weight:bold; color:#7C3D12;'>Arquivos Anexados:</p>
          <ul style='margin:0; padding-left:20px; font-size:13.5px; color:#2C1810;'>
            {anexos_li}
          </ul>
        </div>
        """

    msg_bloco = ""
    if mensagem_customizada:
        msg_bloco = f"""
        <div style='margin-bottom:18px; padding:14px 16px; background:#fffbf4; border:1px solid #e2d2bc; border-radius:8px;'>
          <p style='margin:0; font-size:14.5px; color:#2C1810; font-style:italic;'>{mensagem_customizada}</p>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color:#f7f3ee; margin:0; padding:20px;">
  <div style="max-width:600px; margin:0 auto; background:#ffffff; border-radius:12px; overflow:hidden; border:1px solid #e2d2bc; box-shadow:0 4px 12px rgba(0,0,0,0.06);">
    <div style="background:#7C3D12; padding:24px 20px; text-align:center; color:#ffffff;">
      <h1 style="margin:0; font-size:22px; font-weight:bold; letter-spacing:0.5px;">✂️ ATELIÊ HAITI</h1>
      <p style="margin:4px 0 0 0; font-size:13px; color:#fde9c2; font-style:italic;">Gestão Artesanal &amp; Produção Comunitária</p>
    </div>

    <div style="padding:24px 20px;">
      <h2 style="margin:0 0 6px 0; font-size:18px; color:#7C3D12;">{titulo}</h2>
      <p style="font-size:13px; color:#7A6B63; margin:0 0 16px 0;">Emissão: {agora().strftime('%d/%m/%Y às %H:%M')}</p>
      
      {msg_bloco}

      <div style="display:table; width:100%; margin-bottom:12px;">
        <div style="display:table-cell; width:50%; padding-right:6px;">
          <div style="background:#fdf8f0; border:1px solid #e2d2bc; border-radius:8px; padding:12px; text-align:center;">
            <p style="margin:0; font-size:12px; color:#7A6B63;">Faturamento Entregue</p>
            <p style="margin:4px 0 0 0; font-size:18px; font-weight:bold; color:#2E7D32;">R$ {formatar_reais(rec_entregue)}</p>
          </div>
        </div>
        <div style="display:table-cell; width:50%; padding-left:6px;">
          <div style="background:#fdf8f0; border:1px solid #e2d2bc; border-radius:8px; padding:12px; text-align:center;">
            <p style="margin:0; font-size:12px; color:#7A6B63;">Despesas Registradas</p>
            <p style="margin:4px 0 0 0; font-size:18px; font-weight:bold; color:#C62828;">R$ {formatar_reais(tot_despesas)}</p>
          </div>
        </div>
      </div>

      <div style="display:table; width:100%; margin-bottom:16px;">
        <div style="display:table-cell; width:50%; padding-right:6px;">
          <div style="background:#fdf8f0; border:1px solid #e2d2bc; border-radius:8px; padding:12px; text-align:center;">
            <p style="margin:0; font-size:12px; color:#7A6B63;">Pedidos em Andamento</p>
            <p style="margin:4px 0 0 0; font-size:18px; font-weight:bold; color:#7C3D12;">{len(pedidos_ativos)}</p>
          </div>
        </div>
        <div style="display:table-cell; width:50%; padding-left:6px;">
          <div style="background:#fdf8f0; border:1px solid #e2d2bc; border-radius:8px; padding:12px; text-align:center;">
            <p style="margin:0; font-size:12px; color:#7A6B63;">Itens em Estoque Baixo</p>
            <p style="margin:4px 0 0 0; font-size:18px; font-weight:bold; color:{'#C62828' if baixo_estoque else '#2E7D32'};">{len(baixo_estoque)}</p>
          </div>
        </div>
      </div>

      {anexos_html}

      <p style="margin:20px 0 0 0; font-size:13px; color:#7A6B63; line-height:1.4;">
        Os relatórios detalhados encontram-se em anexo para download e consulta.
      </p>
    </div>

    <div style="background:#f4ece1; padding:16px 20px; text-align:center; border-top:1px solid #e2d2bc;">
      <p style="margin:0; font-size:12px; color:#7A6B63;">
        ✦ Conectados pela Comunidade — Comunidade do Haiti, SP ✦<br>
        Sistema Ateliê Haiti — Mensagem gerada automaticamente via Google Gmail API.
      </p>
    </div>
  </div>
</body>
</html>"""


def enviar_email(destinatarios, assunto, corpo_html, anexos=None, texto_puro=None, remetente_user_id=None, agendamento_id=None, tipo_relatorio="Geral", enviado_por=None):
    """
    Envia um e-mail formatado com relatórios anexos via API oficial do Gmail (Google OAuth2)
    diretamente da conta do usuário autenticado para os destinatários,
    ou registra no histórico em Modo Simulação quando não houver token Google.
    """
    if isinstance(destinatarios, str):
        dest_lista = [d.strip() for d in re.split(r"[,;]", destinatarios) if d.strip()]
    else:
        dest_lista = [str(d).strip() for d in destinatarios if str(d).strip()]

    if not dest_lista:
        return {"success": False, "error": "Nenhum destinatário válido informado."}

    # Identifica o usuário remetente
    remetente_user = None
    if remetente_user_id:
        remetente_user = encontrar_usuario_por_id(remetente_user_id)
    elif g.get("user"):
        remetente_user = g.user

    remetente_email = (remetente_user.get("email") if remetente_user else "") or "relatorios@ateliehaiti.com"
    remetente_nome = (remetente_user.get("nome") or remetente_user.get("username") if remetente_user else "") or "Ateliê Haiti"
    autor_registro = enviado_por or remetente_nome or "Sistema"

    # Monta a mensagem MIME multipart
    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"] = f"{remetente_nome} <{remetente_email}>"
    msg["To"] = ", ".join(dest_lista)

    alt_part = MIMEMultipart("alternative")
    if texto_puro:
        alt_part.attach(MIMEText(texto_puro, "plain", "utf-8"))
    alt_part.attach(MIMEText(corpo_html, "html", "utf-8"))
    msg.attach(alt_part)

    if anexos:
        for anexo in anexos:
            nome_anexo = anexo.get("nome", "relatorio.dat")
            dados_bytes = anexo.get("bytes", b"")
            mimetype = anexo.get("mimetype", "application/octet-stream")
            maintype, subtype = mimetype.split("/", 1) if "/" in mimetype else ("application", "octet-stream")

            part = MIMEBase(maintype, subtype)
            part.set_payload(dados_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{nome_anexo}"')
            msg.attach(part)

    hist_id = str(uuid.uuid4())
    now = agora().isoformat()
    dest_str = ", ".join(dest_lista)

    # Tenta recuperar o token de acesso Google do remetente
    token_res = obter_access_token_gmail_usuario(remetente_user) if remetente_user else {"success": False, "reason": "no_user"}

    if not token_res.get("success"):
        # Modo Simulação (usuário sem login Google vinculado ou ambiente de testes)
        status = "Simulado"
        msg_status = f"E-mail simulado com sucesso (Modo Simulação / Sem token Google). De: {remetente_email} Para: {len(dest_lista)} destinatário(s). Anexos: {len(anexos or [])} arquivo(s)."
        registrar_historico_email(hist_id, agendamento_id, assunto, tipo_relatorio, dest_str, status, msg_status, autor_registro, now)
        return {"success": True, "simulated": True, "message": msg_status, "historico_id": hist_id}

    # Envio Real via Gmail REST API
    try:
        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        access_token = token_res["access_token"]
        
        api_resp = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            json={"raw": raw_b64},
            timeout=25
        )

        if api_resp.status_code in (200, 201):
            status = "Sucesso"
            msg_status = f"E-mail enviado com sucesso via Gmail API a partir de {remetente_email} para {len(dest_lista)} destinatário(s)."
            registrar_historico_email(hist_id, agendamento_id, assunto, tipo_relatorio, dest_str, status, msg_status, autor_registro, now)
            return {"success": True, "simulated": False, "message": msg_status, "historico_id": hist_id}
        else:
            status = "Falha"
            msg_status = f"Erro na Gmail API ({api_resp.status_code}): {api_resp.text}"
            registrar_historico_email(hist_id, agendamento_id, assunto, tipo_relatorio, dest_str, status, msg_status, autor_registro, now)
            return {"success": False, "error": msg_status, "historico_id": hist_id}
    except Exception as e:
        status = "Falha"
        msg_status = f"Erro de conexão com a Gmail API: {str(e)}"
        registrar_historico_email(hist_id, agendamento_id, assunto, tipo_relatorio, dest_str, status, msg_status, autor_registro, now)
        return {"success": False, "error": str(e), "historico_id": hist_id}


# ── Agendamento de Envios Regulares & Background Engine ───────────────────────

def calcular_proximo_envio(frequencia, hora_envio_str, dia_semana=0, dia_mes=1, a_partir_de=None):
    """Calcula a data e hora do próximo envio no fuso horário de Brasília."""
    base = a_partir_de or agora()
    try:
        h, m = [int(x) for x in hora_envio_str.split(":")]
    except Exception:
        h, m = 8, 0

    alvo = base.replace(hour=h, minute=m, second=0, microsecond=0)

    if frequencia == "diario":
        if alvo <= base:
            alvo += timedelta(days=1)

    elif frequencia == "semanal":
        target_weekday = int(dia_semana if dia_semana is not None else 0)
        dias_a_frente = (target_weekday - base.weekday()) % 7
        alvo = base.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=dias_a_frente)
        if alvo <= base:
            alvo += timedelta(days=7)

    elif frequencia == "mensal":
        ano = base.year
        mes = base.month
        dia = min(max(1, int(dia_mes if dia_mes is not None else 1)), 28)
        try:
            alvo = datetime(ano, mes, dia, h, m, tzinfo=FUSO_BR)
        except Exception:
            alvo = datetime(ano, mes, 28, h, m, tzinfo=FUSO_BR)

        if alvo <= base:
            if mes == 12:
                ano += 1
                mes = 1
            else:
                mes += 1
            alvo = datetime(ano, mes, dia, h, m, tzinfo=FUSO_BR)

    return alvo


def carregar_agendamentos_email():
    """Retorna a lista de todas as regras de agendamento cadastradas."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM agendamentos_email ORDER BY created_at DESC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    return []


def carregar_agendamento_por_id(agendamento_id):
    """Busca uma regra de agendamento pelo seu ID único."""
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM agendamentos_email WHERE id=?", (agendamento_id,))
        r = cur.fetchone()
        conn.close()
        return dict(r) if r else None
    return None


def executar_agendamento(agendamento, disparado_por="Agendador Automático"):
    """Gera os relatórios anexos e executa o disparo do agendamento usando o token do criador."""
    tipo = agendamento.get("tipo_relatorio", "financeiro_pdf")
    titulo = agendamento.get("titulo", "Relatório Periódico")
    destinatarios = agendamento.get("destinatarios", "")
    assunto = agendamento.get("assunto") or f"📊 {titulo} — Ateliê Haiti"
    msg_custom = agendamento.get("mensagem") or f"Segue em anexo o relatório periódico '{titulo}' gerado automaticamente pelo sistema."
    remetente_uid = agendamento.get("usuario_remetente_id")

    anexos = []
    if tipo in ("financeiro_pdf", "ambos"):
        try:
            pdf_bytes = gerar_pdf_financeiro_bytes()
            anexos.append({"nome": f"relatorio_financeiro_{agora().strftime('%Y%m%d')}.pdf", "bytes": pdf_bytes, "mimetype": "application/pdf"})
        except Exception as e:
            print(f"Erro ao gerar PDF financeiro: {e}")

    if tipo in ("completo_xlsx", "ambos"):
        try:
            xlsx_bytes = gerar_xlsx_completo_bytes()
            anexos.append({"nome": f"export_atelie_completo_{agora().strftime('%Y%m%d')}.xlsx", "bytes": xlsx_bytes, "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        except Exception as e:
            print(f"Erro ao gerar XLSX: {e}")

    if tipo == "estoque_baixo_pdf":
        try:
            pdf_bytes = gerar_pdf_estoque_baixo_bytes()
            anexos.append({"nome": f"alerta_estoque_critico_{agora().strftime('%Y%m%d')}.pdf", "bytes": pdf_bytes, "mimetype": "application/pdf"})
        except Exception as e:
            print(f"Erro ao gerar PDF de estoque: {e}")

    corpo_html = gerar_html_email_relatorio(titulo, msg_custom, anexos_nomes=[a["nome"] for a in anexos])
    resultado = enviar_email(
        destinatarios=destinatarios,
        assunto=assunto,
        corpo_html=corpo_html,
        anexos=anexos,
        texto_puro=msg_custom,
        remetente_user_id=remetente_uid,
        agendamento_id=agendamento.get("id"),
        tipo_relatorio=tipo,
        enviado_por=disparado_por
    )

    now_iso = agora().isoformat()
    prox = calcular_proximo_envio(
        agendamento.get("frequencia", "diario"),
        agendamento.get("hora_envio", "08:00"),
        agendamento.get("dia_semana", 0),
        agendamento.get("dia_mes", 1),
        a_partir_de=agora()
    )

    if USE_SQLITE and agendamento.get("id"):
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE agendamentos_email SET ultimo_envio=?, proximo_envio=?, updated_at=? WHERE id=?",
                (now_iso, prox.isoformat(), now_iso, agendamento.get("id"))
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    return resultado


_SCHEDULER_RUNNING = False

def _loop_agendador_background():
    global _SCHEDULER_RUNNING
    while _SCHEDULER_RUNNING:
        try:
            agora_str = agora().isoformat()
            if USE_SQLITE:
                init_db()
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM agendamentos_email WHERE ativo=1 AND proximo_envio IS NOT NULL AND proximo_envio <= ?",
                    (agora_str,)
                )
                devidos = [dict(r) for r in cur.fetchall()]
                conn.close()
                for ag in devidos:
                    try:
                        executar_agendamento(ag, disparado_por="Agendador Automático")
                    except Exception as ex:
                        print(f"Erro ao executar agendamento regular {ag.get('id')}: {ex}")
        except Exception as e:
            print(f"Erro no ciclo do agendador: {e}")

        time.sleep(30)


def iniciar_agendador_background():
    """Inicia a thread em background que monitora agendamentos de e-mails."""
    global _SCHEDULER_RUNNING
    if not _SCHEDULER_RUNNING and os.environ.get("FLASK_ENV") != "testing":
        _SCHEDULER_RUNNING = True
        t = threading.Thread(target=_loop_agendador_background, daemon=True, name="AgendadorEmailAtelie")
        t.start()


# ── Rotas de Envio de Relatórios e Agendamentos por E-mail ────────────────────

@app.route("/emails")
@requires_permission("relatorios", "read")
def central_emails():
    return redirect(url_for("relatorios_enviar_email"))


@app.route("/relatorios/enviar-email", methods=["GET", "POST"])
@requires_permission("relatorios", "read")
def relatorios_enviar_email():
    usuarios_lista = carregar_usuarios()
    config_email = obter_configuracoes_email()

    if request.method == "POST":
        tipo_relatorio = request.form.get("tipo_relatorio", "financeiro_pdf")
        dest_usuarios = request.form.getlist("destinatarios_usuarios")
        dest_extras_raw = request.form.get("destinatarios_extras", "")
        dest_extras = [e.strip() for e in re.split(r"[,;]", dest_extras_raw) if e.strip()]
        
        todos_destinatarios = list(dict.fromkeys(dest_usuarios + dest_extras))
        if not todos_destinatarios:
            flash("Selecione ou digite ao menos um endereço de e-mail de destino.")
            return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)

        # Validação básica de formato de e-mail
        for email_check in todos_destinatarios:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_check):
                flash(f"E-mail em formato inválido: {email_check}")
                return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)

        assunto = request.form.get("assunto", "").strip() or "📊 Relatório Gerencial — Ateliê Haiti"
        mensagem = request.form.get("mensagem", "").strip()

        # Gerar anexos
        anexos = []
        if tipo_relatorio in ("financeiro_pdf", "ambos"):
            try:
                pdf_bytes = gerar_pdf_financeiro_bytes()
                anexos.append({"nome": f"relatorio_financeiro_{agora().strftime('%Y%m%d_%H%M')}.pdf", "bytes": pdf_bytes, "mimetype": "application/pdf"})
            except Exception as e:
                flash(f"Erro ao gerar PDF: {e}")
                return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)

        if tipo_relatorio in ("completo_xlsx", "ambos"):
            try:
                xlsx_bytes = gerar_xlsx_completo_bytes()
                anexos.append({"nome": f"export_atelie_completo_{agora().strftime('%Y%m%d_%H%M')}.xlsx", "bytes": xlsx_bytes, "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
            except Exception as e:
                flash(f"Erro ao gerar XLSX: {e}")
                return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)

        if tipo_relatorio == "estoque_baixo_pdf":
            try:
                pdf_bytes = gerar_pdf_estoque_baixo_bytes()
                anexos.append({"nome": f"alerta_estoque_critico_{agora().strftime('%Y%m%d_%H%M')}.pdf", "bytes": pdf_bytes, "mimetype": "application/pdf"})
            except Exception as e:
                flash(f"Erro ao gerar PDF de Estoque: {e}")
                return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)

        corpo_html = gerar_html_email_relatorio(assunto, mensagem, anexos_nomes=[a["nome"] for a in anexos])
        autor = g.user.get("nome") or g.user.get("username") if g.get("user") else "Usuário"
        remetente_uid = g.user.get("id") if g.get("user") else None

        res = enviar_email(
            destinatarios=todos_destinatarios,
            assunto=assunto,
            corpo_html=corpo_html,
            anexos=anexos,
            texto_puro=mensagem,
            remetente_user_id=remetente_uid,
            tipo_relatorio=tipo_relatorio,
            enviado_por=autor
        )

        if res.get("success"):
            if res.get("simulated"):
                flash(f"✅ Relatório preparado com sucesso! (Modo Simulação gravado no Histórico de Envios para {len(todos_destinatarios)} destinatário(s)).")
            else:
                flash(f"✅ Relatório enviado com sucesso via Gmail para {len(todos_destinatarios)} destinatário(s)!")
            return redirect(url_for("historico_emails"))
        else:
            flash(f"Falha no envio de e-mail: {res.get('error')}")
            return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)

    return render_template("relatorios_email_enviar.html", usuarios=usuarios_lista, config_email=config_email)


@app.route("/relatorios/agendamentos")
@requires_permission("relatorios", "read")
def agendamentos_email_listar():
    agendamentos = carregar_agendamentos_email()
    config_email = obter_configuracoes_email()
    return render_template("agendamentos_email.html", agendamentos=agendamentos, config_email=config_email)


@app.route("/relatorios/agendamentos/novo", methods=["GET", "POST"])
@requires_permission("relatorios", "create")
def agendamentos_email_novo():
    usuarios_lista = carregar_usuarios()

    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        tipo_relatorio = request.form.get("tipo_relatorio", "financeiro_pdf")
        frequencia = request.form.get("frequencia", "semanal")
        hora_envio = request.form.get("hora_envio", "08:00").strip()
        dia_semana = int(request.form.get("dia_semana", 0)) if request.form.get("dia_semana") else 0
        dia_mes = int(request.form.get("dia_mes", 1)) if request.form.get("dia_mes") else 1
        
        dest_usuarios = request.form.getlist("destinatarios_usuarios")
        dest_extras_raw = request.form.get("destinatarios_extras", "")
        dest_extras = [e.strip() for e in re.split(r"[,;]", dest_extras_raw) if e.strip()]
        todos_destinatarios = list(dict.fromkeys(dest_usuarios + dest_extras))

        if not titulo:
            flash("O título da programação é obrigatório.")
            return render_template("agendamento_form.html", usuarios=usuarios_lista)
        if not todos_destinatarios:
            flash("Informe ao menos um destinatário para a programação.")
            return render_template("agendamento_form.html", usuarios=usuarios_lista)

        for email_check in todos_destinatarios:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_check):
                flash(f"E-mail inválido: {email_check}")
                return render_template("agendamento_form.html", usuarios=usuarios_lista)

        assunto = request.form.get("assunto", "").strip()
        mensagem = request.form.get("mensagem", "").strip()
        ativo = 1 if request.form.get("ativo") == "1" else 0
        
        now = agora().isoformat()
        prox = calcular_proximo_envio(frequencia, hora_envio, dia_semana, dia_mes, a_partir_de=agora())
        aid = str(uuid.uuid4())
        criador = g.user.get("username") if g.get("user") else "Admin"
        remetente_uid = g.user.get("id") if g.get("user") else None

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO agendamentos_email 
                    (id, titulo, tipo_relatorio, frequencia, hora_envio, dia_semana, dia_mes, destinatarios, assunto, mensagem, ativo, proximo_envio, criado_por, usuario_remetente_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (aid, titulo, tipo_relatorio, frequencia, hora_envio, dia_semana, dia_mes, ", ".join(todos_destinatarios), assunto, mensagem, ativo, prox.isoformat(), criador, remetente_uid, now, now)
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                flash("Erro ao cadastrar agendamento: " + str(e))
                return render_template("agendamento_form.html", usuarios=usuarios_lista)
            finally:
                conn.close()

        flash("Programação de envio regular criada com sucesso!")
        return redirect(url_for("agendamentos_email_listar"))

    return render_template("agendamento_form.html", usuarios=usuarios_lista)


@app.route("/relatorios/agendamentos/<agendamento_id>/editar", methods=["GET", "POST"])
@requires_permission("relatorios", "update")
def agendamentos_email_editar(agendamento_id):
    usuarios_lista = carregar_usuarios()
    agendamento = carregar_agendamento_por_id(agendamento_id)
    if not agendamento:
        flash("Agendamento não encontrado.")
        return redirect(url_for("agendamentos_email_listar"))

    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        tipo_relatorio = request.form.get("tipo_relatorio", "financeiro_pdf")
        frequencia = request.form.get("frequencia", "semanal")
        hora_envio = request.form.get("hora_envio", "08:00").strip()
        dia_semana = int(request.form.get("dia_semana", 0)) if request.form.get("dia_semana") else 0
        dia_mes = int(request.form.get("dia_mes", 1)) if request.form.get("dia_mes") else 1
        
        dest_usuarios = request.form.getlist("destinatarios_usuarios")
        dest_extras_raw = request.form.get("destinatarios_extras", "")
        dest_extras = [e.strip() for e in re.split(r"[,;]", dest_extras_raw) if e.strip()]
        todos_destinatarios = list(dict.fromkeys(dest_usuarios + dest_extras))

        if not titulo:
            flash("O título da programação é obrigatório.")
            return render_template("agendamento_form.html", usuarios=usuarios_lista, agendamento=agendamento)
        if not todos_destinatarios:
            flash("Informe ao menos um destinatário para a programação.")
            return render_template("agendamento_form.html", usuarios=usuarios_lista, agendamento=agendamento)

        for email_check in todos_destinatarios:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_check):
                flash(f"E-mail inválido: {email_check}")
                return render_template("agendamento_form.html", usuarios=usuarios_lista, agendamento=agendamento)

        assunto = request.form.get("assunto", "").strip()
        mensagem = request.form.get("mensagem", "").strip()
        ativo = 1 if request.form.get("ativo") == "1" else 0
        
        now = agora().isoformat()
        prox = calcular_proximo_envio(frequencia, hora_envio, dia_semana, dia_mes, a_partir_de=agora())
        remetente_uid = g.user.get("id") if g.get("user") else agendamento.get("usuario_remetente_id")

        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE agendamentos_email 
                    SET titulo=?, tipo_relatorio=?, frequencia=?, hora_envio=?, dia_semana=?, dia_mes=?, destinatarios=?, assunto=?, mensagem=?, ativo=?, proximo_envio=?, usuario_remetente_id=?, updated_at=?
                    WHERE id=?
                    """,
                    (titulo, tipo_relatorio, frequencia, hora_envio, dia_semana, dia_mes, ", ".join(todos_destinatarios), assunto, mensagem, ativo, prox.isoformat(), remetente_uid, now, agendamento_id)
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                flash("Erro ao salvar agendamento: " + str(e))
                return render_template("agendamento_form.html", usuarios=usuarios_lista, agendamento=agendamento)
            finally:
                conn.close()

        flash("Programação atualizada com sucesso!")
        return redirect(url_for("agendamentos_email_listar"))

    return render_template("agendamento_form.html", usuarios=usuarios_lista, agendamento=agendamento)


@app.route("/relatorios/agendamentos/<agendamento_id>/toggle", methods=["POST"])
@requires_permission("relatorios", "update")
def agendamentos_email_toggle(agendamento_id):
    agendamento = carregar_agendamento_por_id(agendamento_id)
    if not agendamento:
        flash("Agendamento não encontrado.")
        return redirect(url_for("agendamentos_email_listar"))

    novo_status = 0 if agendamento.get("ativo") else 1
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE agendamentos_email SET ativo=?, updated_at=? WHERE id=?", (novo_status, agora().isoformat(), agendamento_id))
        conn.commit()
        conn.close()

    msg = "🟢 Programação ativada!" if novo_status else "⏸️ Programação pausada!"
    flash(msg)
    return redirect(url_for("agendamentos_email_listar"))


@app.route("/relatorios/agendamentos/<agendamento_id>/executar", methods=["POST"])
@requires_permission("relatorios", "read")
def agendamentos_email_executar(agendamento_id):
    agendamento = carregar_agendamento_por_id(agendamento_id)
    if not agendamento:
        flash("Agendamento não encontrado.")
        return redirect(url_for("agendamentos_email_listar"))

    autor = g.user.get("nome") or g.user.get("username") if g.get("user") else "Usuário"
    res = executar_agendamento(agendamento, disparado_por=f"{autor} (Manual)")

    if res.get("success"):
        flash("✅ Agendamento executado com sucesso e e-mail registrado!")
    else:
        flash(f"Falha na execução: {res.get('error')}")

    return redirect(url_for("historico_emails"))


@app.route("/relatorios/agendamentos/<agendamento_id>/excluir", methods=["POST"])
@requires_permission("relatorios", "delete")
def agendamentos_email_excluir(agendamento_id):
    if USE_SQLITE:
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM agendamentos_email WHERE id=?", (agendamento_id,))
        conn.commit()
        conn.close()

    flash("Programação de envio regular excluída com sucesso.")
    return redirect(url_for("agendamentos_email_listar"))


@app.route("/relatorios/historico-emails")
@requires_permission("relatorios", "read")
def historico_emails():
    historico = carregar_historico_emails(100)
    return render_template("historico_emails.html", historico=historico)


@app.route("/configuracoes/email", methods=["GET", "POST"])
@requires_permission("relatorios", "create")
def configuracoes_email_view():
    return redirect(url_for("configuracoes_sso_view"))


@app.route("/configuracoes/email/testar", methods=["POST"])
@requires_permission("relatorios", "create")
def configuracoes_email_testar():
    email_teste = request.form.get("email_teste", "").strip().lower()
    if not email_teste or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_teste):
        flash("Informe um e-mail válido para receber o teste.")
        return redirect(url_for("configuracoes_sso_view"))

    try:
        pdf_bytes = gerar_pdf_financeiro_bytes()
        anexos = [{"nome": "teste_relatorio_atelie.pdf", "bytes": pdf_bytes, "mimetype": "application/pdf"}]
    except Exception:
        anexos = []

    corpo = gerar_html_email_relatorio("🧪 Teste de Conexão Gmail API", "Este é um disparo de teste para validação do serviço de e-mails do Ateliê Haiti via Gmail API.", anexos_nomes=[a["nome"] for a in anexos])
    res = enviar_email(
        destinatarios=[email_teste],
        assunto="🧪 Teste de Envio via Gmail API — Ateliê Haiti",
        corpo_html=corpo,
        anexos=anexos,
        texto_puro="Teste de envio de e-mail do sistema Ateliê Haiti via Gmail API.",
        remetente_user_id=g.user.get("id") if g.get("user") else None,
        tipo_relatorio="Teste",
        enviado_por=g.user.get("username") if g.get("user") else "Admin"
    )

    if res.get("success"):
        if res.get("simulated"):
            flash(f"✅ Teste concluído com sucesso em Modo Simulação para {email_teste}!")
        else:
            flash(f"✅ E-mail de teste enviado com sucesso via Gmail API para {email_teste}!")
    else:
        flash(f"Falha no teste de envio: {res.get('error')}")

    return redirect(url_for("configuracoes_sso_view"))


# Inicia o agendador em background na inicialização do servidor
iniciar_agendador_background()


# ── Chatbot Ania (Assistente Virtual com Voz, Texto e RBAC) ───────────────────
import ania_assistant
_ania_engine = None

def get_ania_engine():
    global _ania_engine
    if _ania_engine is None:
        _ania_engine = ania_assistant.AniaAssistant(sys.modules[__name__])
    return _ania_engine


@app.route("/api/ania/chat", methods=["POST"])
def ania_chat():
    if not g.get("user"):
        return jsonify({
            "success": False,
            "denied": True,
            "reply": "🔒 **Sessão Necessária**: Você precisa estar conectado ao sistema para conversar com a Ania.",
            "voice_text": "Por favor, faça login para utilizar a assistente.",
            "suggestions": ["Fazer login"]
        }), 401

    data = request.get_json(silent=True) or {}
    mensagem = data.get("message", "").strip()
    if not mensagem:
        return jsonify({
            "success": True,
            "reply": "Estou te ouvindo! O que você gostaria de fazer no ateliê?",
            "voice_text": "Estou te ouvindo! O que você gostaria de fazer no ateliê?",
            "suggestions": ["📦 Consultar estoque", "🧾 Pedidos", "📊 Alertas", "Minhas permissões"]
        })

    mode = (data.get("mode") or session.get("ania_mode") or "ia").lower()
    session["ania_mode"] = mode

    engine = get_ania_engine()
    chat_history = session.get("ania_chat_history", [])
    if not isinstance(chat_history, list):
        chat_history = []

    resposta = engine.processar_mensagem(mensagem, g.user, history=chat_history, mode=mode)

    # Atualiza memória da conversa (últimas 6 interações)
    chat_history.append({"role": "user", "content": mensagem})
    reply_text = resposta.get("reply", "")
    if len(reply_text) > 300:
        reply_text = reply_text[:300] + "..."
    chat_history.append({"role": "assistant", "content": reply_text})
    session["ania_chat_history"] = chat_history[-6:]

    return jsonify(resposta)


@app.route("/api/ania/status", methods=["GET"])
def ania_status():
    if not g.get("user"):
        return jsonify({"error": "unauthorized"}), 401

    engine = get_ania_engine()
    ollama_info = engine.ollama.get_status() if hasattr(engine, "ollama") and engine.ollama else {
        "enabled": False,
        "online": False,
        "mode": "contingency_rules"
    }
    return jsonify({
        "success": True,
        "ollama": ollama_info,
        "engine": "ollama_hybrid" if ollama_info.get("online") else "regras_locais",
        "timestamp": agora().isoformat()
    })


# ── Developer Hub (Gestão Técnica de Infraestrutura, SSO, IA e Banco) ─────────
_SERVER_START_TIME = agora()

@app.route("/developer")
@app.route("/dev")
@requires_developer
def developer_dashboard():
    aba_ativa = request.args.get("tab", "sso")
    config_sso = obter_configuracoes_sso()
    papeis = carregar_papeis()
    redirect_uri = url_for("auth_google_callback", _external=True)

    # Motor Ollama
    engine = get_ania_engine()
    ollama_info = engine.ollama.get_status() if hasattr(engine, "ollama") and engine.ollama else {
        "enabled": True,
        "online": False,
        "mode": "contingency_rules",
        "host": "http://localhost:11434",
        "model": "qwen2.5:3b",
        "timeout": 25.0,
        "emulate_if_offline": True,
    }
    available_models = getattr(engine.ollama, "_cached_available_models", []) if hasattr(engine, "ollama") and engine.ollama else []
    if not available_models and hasattr(engine, "ollama") and engine.ollama:
        try:
            engine.ollama.is_online(force_refresh=True)
            available_models = engine.ollama._cached_available_models
        except Exception:
            pass

    # Database Stats
    db_stats = get_db_stats()
    audits_list = []

    if USE_SQLITE:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM audits ORDER BY created_at DESC LIMIT 150")
            audits_list = [dict(r) for r in cur.fetchall()]
            conn.close()
        except Exception as ex:
            db_stats["error"] = str(ex)

    # Database Config
    db_config = {
        "engine": "postgres" if is_postgres_active() else "sqlite",
        "host": os.environ.get("PGHOST", "172.16.36.14"),
        "port": os.environ.get("PGPORT", "5432"),
        "dbname": os.environ.get("PGDATABASE", "postgres"),
        "user": os.environ.get("PGUSER", "root"),
        "password": os.environ.get("PGPASSWORD", ""),
        "fallback": os.environ.get("DB_FALLBACK_SQLITE", "1") in ("1", "true", "yes", "True"),
    }

    # Runtime Diagnostics
    import platform
    diagnostics = {
        "python_version": platform.python_version(),
        "flask_version": "3.0+",
        "platform": platform.platform(),
        "system": platform.system(),
        "timezone": "America/Sao_Paulo (UTC-3)",
        "server_start": _SERVER_START_TIME.strftime("%d/%m/%Y %H:%M:%S"),
        "scheduler_running": _SCHEDULER_RUNNING,
        "total_users": len(carregar_usuarios()),
        "total_roles": len(papeis),
        "db_mode": "PostgreSQL (Servidor)" if is_postgres_active() else ("SQLite WAL" if USE_SQLITE else "JSON Fallback"),
    }

    # WAHA WhatsApp
    config_waha = waha_service.obter_configuracoes_waha()
    waha_status = waha_service.testar_conexao_waha(
        config_waha.get("api_url"),
        config_waha.get("api_key"),
        config_waha.get("session_name")
    )
    waha_mensagens = waha_service.obter_mensagens_recentes(limit=30)

    return render_template(
        "developer.html",
        aba_ativa=aba_ativa,
        config_sso=config_sso,
        papeis=papeis,
        redirect_uri=redirect_uri,
        ollama_info=ollama_info,
        available_models=available_models,
        db_stats=db_stats,
        db_config=db_config,
        audits=audits_list,
        diagnostics=diagnostics,
        config_cloudinary=obter_configuracoes_cloudinary(),
        cloudinary_status=cloudinary_service.testar_conexao_cloudinary(),
        config_waha=config_waha,
        waha_status=waha_status,
        waha_mensagens=waha_mensagens,
        produtos=carregar_produtos(),
    )


@app.route("/developer/sso/salvar", methods=["POST"])
@requires_developer
def developer_sso_salvar():
    cfg = {
        "google_client_id": request.form.get("google_client_id", "").strip(),
        "google_client_secret": request.form.get("google_client_secret", "").strip(),
        "ativo": 1 if request.form.get("ativo") == "1" else 0,
        "auto_cadastro": 1 if request.form.get("auto_cadastro") == "1" else 0,
        "papel_padrao": request.form.get("papel_padrao", "Producao").strip(),
    }
    salvar_configuracoes_sso(cfg)

    # Audit log
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else "Developer", None, "update_sso_config", f"ativo={cfg['ativo']};client_id_set={'yes' if cfg['google_client_id'] else 'no'};secret_set={'yes' if cfg['google_client_secret'] else 'no'}", agora().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    flash("Configurações do Google SSO e credenciais salvas com sucesso no Developer Hub!")
    return redirect(url_for("developer_dashboard", tab="sso"))


@app.route("/developer/cloudinary/salvar", methods=["POST"])
@requires_developer
def developer_cloudinary_salvar():
    cloud_name = request.form.get("cloud_name", "").strip()
    api_key = request.form.get("api_key", "").strip()
    api_secret = request.form.get("api_secret", "").strip()
    ativo = 1 if request.form.get("ativo") == "1" else 0

    cfg = {
        "cloud_name": cloud_name,
        "api_key": api_key,
        "api_secret": api_secret,
        "ativo": ativo,
    }
    salvar_configuracoes_cloudinary(cfg)

    # Test connection after save
    status = cloudinary_service.testar_conexao_cloudinary(cloud_name, api_key, api_secret)

    # Audit log
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else "Developer", None, "update_cloudinary_config", f"cloud_name={cloud_name};ativo={ativo};status={status.get('ok')}", agora().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    if status.get("ok"):
        flash("Configurações do Cloudinary salvas e conexão com a nuvem validada com sucesso! ✅")
    else:
        flash(f"Configurações salvas, mas a validação com a API retornou: {status.get('error', 'Falha')} ⚠️")

    return redirect(url_for("developer_dashboard", tab="cloudinary"))


@app.route("/developer/cloudinary/testar", methods=["POST"])
@requires_developer
def developer_cloudinary_testar():
    data = request.get_json(silent=True) or {}
    cloud_name = data.get("cloud_name", "").strip()
    api_key = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()

    status = cloudinary_service.testar_conexao_cloudinary(cloud_name, api_key, api_secret)
    return jsonify({
        "success": status.get("ok", False),
        "message": status.get("message") or status.get("error", "Erro ao conectar"),
        "status": status.get("status", "")
    })


# ── ROTAS DEVELOPER: WHATSAPP (WAHA API) ──────────────────────────────────────

@app.route("/developer/waha/salvar", methods=["POST"])
@requires_developer
def developer_waha_salvar():
    cfg = {
        "api_url": request.form.get("api_url", "").strip(),
        "session_name": request.form.get("session_name", "default").strip(),
        "api_key": request.form.get("api_key", "").strip(),
        "webhook_secret": request.form.get("webhook_secret", "").strip(),
        "ativo": 1 if request.form.get("ativo") == "1" else 0,
        "auto_reply": 1 if request.form.get("auto_reply") == "1" else 0,
        "notificar_admin": 1 if request.form.get("notificar_admin") == "1" else 0,
        "status_padrao": request.form.get("status_padrao", "Pendente").strip(),
    }
    waha_service.salvar_configuracoes_waha(cfg)

    # Auditoria
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else "Developer", None, "update_waha_config", f"api_url={cfg['api_url']};session={cfg['session_name']};ativo={cfg['ativo']};auto_reply={cfg['auto_reply']}", agora().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    flash("Configurações da API WAHA (WhatsApp) salvas com sucesso no Developer Hub! ✅")
    return redirect(url_for("developer_dashboard", tab="waha"))


@app.route("/developer/waha/testar", methods=["POST"])
@requires_developer
def developer_waha_testar():
    data = request.get_json(silent=True) or {}
    api_url = data.get("api_url", "").strip() or None
    api_key = data.get("api_key", "").strip() or None
    session_name = data.get("session_name", "").strip() or None

    status = waha_service.testar_conexao_waha(api_url, api_key, session_name)
    return jsonify({
        "success": status.get("ok", False),
        "waha_online": status.get("waha_online", False),
        "session_status": status.get("session_status", "UNKNOWN"),
        "message": status.get("message", ""),
        "sessions": status.get("sessions", []),
    })


@app.route("/developer/waha/qr", methods=["GET"])
@requires_developer
def developer_waha_qr():
    qr_res = waha_service.obter_qr_code()
    return jsonify(qr_res)


@app.route("/developer/waha/sessao/acao", methods=["POST"])
@requires_developer
def developer_waha_sessao_acao():
    data = request.get_json(silent=True) or {}
    acao = data.get("acao", "start").strip()
    res = waha_service.controlar_sessao(acao)
    return jsonify(res)


@app.route("/developer/waha/enviar-teste", methods=["POST"])
@requires_developer
def developer_waha_enviar_teste():
    data = request.get_json(silent=True) or {}
    telefone = data.get("telefone", "").strip()
    mensagem = data.get("mensagem", "").strip() or "Teste de integração WAHA Ateliê Web! ✨"

    if not telefone:
        return jsonify({"success": False, "message": "Informe o número de telefone de destino."})

    res = waha_service.enviar_mensagem_whatsapp(telefone, mensagem)
    if res.get("ok"):
        waha_service.salvar_log_mensagem(
            chat_id=waha_service.normalizar_chat_id(telefone),
            telefone=re.sub(r"\D", "", telefone),
            nome_contato="Teste Developer Hub",
            direcao="outgoing",
            conteudo=mensagem,
            tipo_evento="mensagem_teste",
        )
        return jsonify({"success": True, "message": f"Mensagem enviada com sucesso para {telefone}!"})
    return jsonify({"success": False, "message": f"Falha ao enviar mensagem: {res.get('error', 'Erro desconhecido')}"})


@app.route("/developer/waha/simular-pedido", methods=["POST"])
@requires_developer
def developer_waha_simular_pedido():
    data = request.get_json(silent=True) or {}
    cliente = data.get("cliente", "Maria Teste WhatsApp").strip()
    telefone = data.get("telefone", "5511988887777").strip()
    produto_id = data.get("produto_id", "").strip()
    try:
        quantidade = int(data.get("quantidade", 1) or 1)
    except Exception:
        quantidade = 1
    msg = data.get("mensagem", "").strip()

    res = waha_service.simular_pedido_whatsapp(
        cliente_nome=cliente,
        cliente_telefone=telefone,
        produto_id=produto_id,
        quantidade=quantidade,
        mensagem_simulada=msg,
    )
    return jsonify(res)


# ── ROTAS PÚBLICAS: WEBHOOK WAHA & LIVE CHECK PEDIDOS ─────────────────────────

@app.route("/api/waha/webhook", methods=["POST"])
@app.route("/webhook/waha", methods=["POST"])
def waha_webhook():
    payload = request.get_json(silent=True, force=True) or {}
    headers = dict(request.headers)
    res = waha_service.processar_webhook_waha(payload, headers=headers)
    status_code = res.get("code", 200) if isinstance(res, dict) and "code" in res else 200
    return jsonify(res), status_code


@app.route("/api/pedidos/ultimos", methods=["GET"])
def api_pedidos_ultimos():
    """Retorna contagem total e detalhes do pedido mais recente para detecção automática em tempo real."""
    count = 0
    latest_id = ""
    latest_time = ""
    latest_cliente = ""
    latest_produto = ""
    latest_origem = "web"

    try:
        if USE_SQLITE:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS total FROM pedidos")
            r_c = cur.fetchone()
            count = r_c["total"] if r_c else 0
            cur.execute("SELECT * FROM pedidos ORDER BY created_at DESC, data_pedido_iso DESC LIMIT 1")
            r_last = cur.fetchone()
            if r_last:
                keys = r_last.keys() if hasattr(r_last, 'keys') else []
                latest_id = r_last["id"] if "id" in keys else ""
                latest_time = r_last["created_at"] if "created_at" in keys and r_last["created_at"] else (r_last["data_pedido_iso"] if "data_pedido_iso" in keys else "")
                latest_cliente = r_last["cliente"] if "cliente" in keys else ""
                latest_produto = r_last["produto_nome"] if "produto_nome" in keys else ""
                latest_origem = r_last["origem"] if "origem" in keys and r_last["origem"] else ("whatsapp" if "whatsapp" in str(r_last["observacoes"] or "").lower() else "web")
            conn.close()
        else:
            pedidos_lista = carregar_json("pedidos.json")
            count = len(pedidos_lista)
            if pedidos_lista:
                last_p = sorted(pedidos_lista, key=lambda p: p.get("created_at") or p.get("data_pedido_iso", ""), reverse=True)[0]
                latest_id = last_p.get("id", "")
                latest_time = last_p.get("created_at") or last_p.get("data_pedido_iso", "")
                latest_cliente = last_p.get("cliente", "")
                latest_produto = last_p.get("produto_nome", "")
                latest_origem = last_p.get("origem", "web")
    except Exception as ex:
        logger.warning(f"Erro em api_pedidos_ultimos: {ex}")

    return jsonify({
        "count": count,
        "latest_id": latest_id,
        "latest_time": latest_time,
        "latest_cliente": latest_cliente,
        "latest_produto": latest_produto,
        "latest_origem": latest_origem,
    })


@app.route("/developer/sso/testar-gmail", methods=["POST"])
@requires_developer
def developer_sso_testar_gmail():
    email_teste = request.form.get("email_teste", "").strip().lower()
    if not email_teste or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_teste):
        flash("Informe um e-mail válido para receber o teste.")
        return redirect(url_for("developer_dashboard", tab="sso"))

    try:
        pdf_bytes = gerar_pdf_financeiro_bytes()
        anexos = [{"nome": "teste_diagnostico_atelie.pdf", "bytes": pdf_bytes, "mimetype": "application/pdf"}]
    except Exception:
        anexos = []

    corpo = gerar_html_email_relatorio(
        "🧪 Teste de Conexão Gmail API (Developer Hub)",
        "Este é um disparo de diagnóstico técnico executado a partir do painel de Desenvolvedor para validação da Gmail REST API e credenciais Google.",
        anexos_nomes=[a["nome"] for a in anexos]
    )
    res = enviar_email(
        destinatarios=[email_teste],
        assunto="🧪 Teste de Diagnóstico Gmail API — Ateliê Haiti (Dev)",
        corpo_html=corpo,
        anexos=anexos,
        texto_puro="Disparo de diagnóstico técnico do Developer Hub.",
        remetente_user_id=g.user.get("id") if g.get("user") else None,
        tipo_relatorio="Diagnostico_Dev",
        enviado_por=g.user.get("username") if g.get("user") else "Developer"
    )

    if res.get("success"):
        if res.get("simulated"):
            flash(f"✅ Diagnóstico concluído em Modo Simulação para {email_teste}!")
        else:
            flash(f"✅ E-mail de diagnóstico enviado com sucesso via Gmail API para {email_teste}!")
    else:
        flash(f"Falha no teste de envio: {res.get('error')}")

    return redirect(url_for("developer_dashboard", tab="sso"))


@app.route("/developer/sso/testar-calendar", methods=["POST"])
@requires_developer
def developer_sso_testar_calendar():
    conn_info = verificar_conexao_google_calendar(session.get("user_id"))
    if not conn_info.get("conectado"):
        flash(f"❌ Falha ao conectar com o Google Calendar: {conn_info.get('motivo')}")
        return redirect(url_for("developer_dashboard", tab="sso"))

    dt_teste = (agora() + timedelta(days=1)).strftime("%Y-%m-%d")
    pedido_mock = {
        "id": "teste-diagnostico-" + str(uuid.uuid4())[:8],
        "cliente": "Diagnóstico Dev",
        "produto_nome": "Bolsa Diagnóstico Calendar",
        "produto_emoji": "📅",
        "quantidade": 1,
        "valor_total": 0.0,
        "status": "Pendente",
        "observacoes": "Evento de teste gerado pelo Developer Hub para validação de token OAuth2 e Google Calendar API.",
        "data_entrega": dt_teste
    }
    res = criar_ou_atualizar_evento_google_calendar(pedido_mock, session.get("user_id"))
    if res.get("success"):
        cal_nome = conn_info.get("summary") or conn_info.get("email") or "Principal"
        flash(f"✅ Conexão com o Google Calendar validada com sucesso! Evento de teste criado na agenda '{cal_nome}' para {filtro_data_br(dt_teste)}.")
    else:
        flash(f"❌ Falha ao criar evento de teste no Google Calendar: {res.get('reason') or res.get('error')}")

    return redirect(url_for("developer_dashboard", tab="sso"))


@app.route("/developer/ollama/salvar", methods=["POST"])
@requires_developer
def developer_ollama_salvar():
    host = request.form.get("ollama_host", "").strip() or "http://localhost:11434"
    model = request.form.get("ollama_model", "").strip() or "qwen2.5:3b"
    timeout_raw = request.form.get("ollama_timeout", "25").strip()
    emulate = (request.form.get("ollama_emulate") == "1")

    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 25.0

    engine = get_ania_engine()
    if hasattr(engine, "ollama") and engine.ollama:
        engine.ollama.host = host.rstrip("/")
        engine.ollama.model = model
        engine.ollama.timeout = timeout
        engine.ollama.emulate_if_offline = emulate
        engine.ollama._explicit_model = True
        try:
            engine.ollama.is_online(force_refresh=True)
        except Exception:
            pass

    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else "Developer", None, "update_ollama_config", f"host={host};model={model};timeout={timeout};emulate={emulate}", agora().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    flash("Configurações do motor Ollama IA atualizadas com sucesso!")
    return redirect(url_for("developer_dashboard", tab="ollama"))


@app.route("/developer/ollama/testar-prompt", methods=["POST"])
@requires_developer
def developer_ollama_testar_prompt():
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"success": False, "error": "Prompt não informado."}), 400

    mode = data.get("mode", "ia")
    engine = get_ania_engine()
    t0 = time.time()
    res = engine.processar_mensagem(prompt, g.user, history=[], mode=mode)
    elapsed_ms = round((time.time() - t0) * 1000, 1)

    return jsonify({
        "success": True,
        "prompt": prompt,
        "mode": mode,
        "elapsed_ms": elapsed_ms,
        "result": res,
        "timestamp": agora().isoformat()
    })


@app.route("/developer/db/backup")
@requires_developer
def developer_db_backup():
    if not USE_SQLITE or not os.path.exists(DB_PATH):
        flash("Banco de dados SQLite não encontrado para download.")
        return redirect(url_for("developer_dashboard", tab="database"))

    nome_arquivo = f"backup_atelie_haiti_{agora().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    return send_file(DB_PATH, as_attachment=True, download_name=nome_arquivo, mimetype="application/x-sqlite3")


@app.route("/developer/db/otimizar", methods=["POST"])
@requires_developer
def developer_db_otimizar():
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            if is_postgres_active():
                raw = getattr(conn, "_conn", None)
                if raw is not None:
                    raw.autocommit = True
                cur = conn.cursor()
                cur.execute("VACUUM")
                cur.execute("ANALYZE")
                if raw is not None:
                    raw.autocommit = False
            else:
                cur = conn.cursor()
                cur.execute("VACUUM")
                cur.execute("ANALYZE")
                conn.commit()
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else "Developer", None, "optimize_database", "VACUUM + ANALYZE executado", agora().isoformat())
                )
                conn.commit()
            except Exception:
                pass
            conn.close()
            flash("✅ Banco de dados otimizado com sucesso (VACUUM e ANALYZE concluídos)!")
        except Exception as e:
            flash(f"Erro ao otimizar banco: {e}")
    else:
        flash("Otimização não aplicável ao modo JSON.")

    return redirect(url_for("developer_dashboard", tab="database"))


@app.route("/developer/db/restaurar", methods=["POST"])
@requires_developer
def developer_db_restaurar():
    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("Selecione um arquivo de backup (.sqlite3 ou .db) válido.")
        return redirect(url_for("developer_dashboard", tab="database"))

    conteudo = f.read()
    if len(conteudo) < 16 or not conteudo.startswith(b"SQLite format 3\x00"):
        flash("O arquivo enviado não é um banco de dados SQLite válido.")
        return redirect(url_for("developer_dashboard", tab="database"))

    try:
        if os.path.exists(DB_PATH):
            safety_backup = f"{DB_PATH}.safety_{int(time.time())}.bak"
            with open(safety_backup, "wb") as bf:
                with open(DB_PATH, "rb") as cur_f:
                    bf.write(cur_f.read())

        with open(DB_PATH, "wb") as out_f:
            out_f.write(conteudo)

        init_db()
        flash("✅ Banco de dados restaurado com sucesso a partir do backup!")
    except Exception as e:
        flash(f"Erro ao restaurar banco de dados: {e}")

    return redirect(url_for("developer_dashboard", tab="database"))


@app.route("/developer/db/configurar", methods=["POST"])
@requires_developer
def developer_db_configurar():
    engine = request.form.get("engine", "postgres").strip().lower()
    host = request.form.get("host", "").strip()
    port = request.form.get("port", "5432").strip()
    dbname = request.form.get("dbname", "postgres").strip()
    user = request.form.get("user", "postgres").strip()
    password = request.form.get("password", "").strip()
    fallback = (request.form.get("fallback") == "1")

    import db
    db.salvar_postgres_config(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        engine=engine,
        fallback=fallback
    )

    try:
        db.init_db()
        init_db(force=True)
    except Exception as ex:
        sys.stderr.write(f"[INIT_DB ERRO] {ex}\n")

    if USE_SQLITE:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    session.get("user_id"),
                    g.user.get("username") if g.get("user") else "Developer",
                    None,
                    "configurar_banco",
                    f"engine={engine};host={host};port={port};dbname={dbname};user={user};fallback={fallback}",
                    agora().isoformat()
                )
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    if engine == "postgres":
        flash("Configurações do banco salvas com sucesso! Motor ativo: PostgreSQL.")
    else:
        flash("Configurações do banco salvas com sucesso! Motor ativo: SQLite Local.")

    return redirect(url_for("developer_dashboard", tab="database"))


@app.route("/developer/db/testar-conexao", methods=["POST"])
@requires_developer
def developer_db_testar_conexao():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    engine = data.get("engine", "postgres").strip().lower()

    if engine == "sqlite":
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            cur = conn.cursor()
            cur.execute("SELECT 1;")
            cur.fetchone()
            conn.close()
            return jsonify({
                "success": True,
                "message": f"Conexão com SQLite local (arquivo {os.path.basename(DB_PATH)}) bem-sucedida!"
            })
        except Exception as ex:
            return jsonify({
                "success": False,
                "message": f"Falha na conexão com SQLite local: {str(ex)}"
            })

    host = data.get("host", "").strip()
    port = data.get("port", "5432").strip()
    dbname = data.get("dbname", "postgres").strip()
    user = data.get("user", "postgres").strip()
    password = data.get("password", "").strip()

    import db
    ok, msg = db.test_postgres_credentials(host, port, dbname, user, password)
    return jsonify({
        "success": ok,
        "message": msg
    })


@app.route("/developer/seguranca/invalidar-sessoes", methods=["POST"])
@requires_developer
def developer_seguranca_invalidar_sessoes():
    if USE_SQLITE:
        try:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("UPDATE usuarios SET session_version = COALESCE(session_version, 0) + 1")
            cur.execute(
                "INSERT INTO audits (id, actor_id, actor_username, target_user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), session.get("user_id"), g.user.get("username") if g.get("user") else "Developer", None, "invalidate_all_sessions", "Todas as sessões ativas foram invalidadas", agora().isoformat())
            )
            conn.commit()
            conn.close()
            if g.get("user"):
                session["session_version"] = g.user.get("session_version", 0) + 1
            flash("🔒 Todas as outras sessões ativas foram desconectadas com sucesso!")
        except Exception as e:
            flash(f"Erro ao invalidar sessões: {e}")
    else:
        flash("Invalidação de sessões executada.")

    return redirect(url_for("developer_dashboard", tab="audits"))


# Fallback — mantém a navegação de pé para qualquer rota que ainda não exista.
@app.route("/<pagina>")
def em_construcao(pagina):
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)


