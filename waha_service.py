"""
waha_service.py - Módulo de integração com a API WAHA (WhatsApp HTTP API)
Gerencia conexão, sessões, envio de mensagens, recepção de webhooks e
criação automática de pedidos com atualização no site e logs no banco.
"""

import os
import re
import json
import uuid
import base64
import logging
import datetime
import unicodedata
from typing import Optional, Dict, Any, List, Tuple
import requests

logger = logging.getLogger("waha_service")

# ── Helper de remoção de acentos para matching ──────────────────────────────
def remover_acentos(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def agora_iso() -> str:
    return datetime.datetime.now().isoformat()


def agora_br() -> str:
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M")


def formatar_moeda(valor: float) -> str:
    try:
        val = float(valor or 0.0)
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ {valor}"


def normalizar_chat_id(telefone_ou_chat: str) -> str:
    """Garante que o destinatário termine em @c.us e contenha apenas dígitos."""
    s = str(telefone_ou_chat or "").strip()
    if "@" in s:
        return s
    digitos = re.sub(r"\D", "", s)
    if not digitos:
        return s
    # Se começar com 0, remove
    if digitos.startswith("0"):
        digitos = digitos[1:]
    # Se for número brasileiro com 10 ou 11 dígitos, adiciona o DDI 55
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    return f"{digitos}@c.us"


def formatar_telefone_legivel(telefone_ou_chat: str) -> str:
    """Converte 5511999998888 ou 5511999998888@c.us em (11) 99999-8888."""
    raw = re.sub(r"\D", "", str(telefone_ou_chat or "").split("@")[0])
    if len(raw) >= 12 and raw.startswith("55"):
        raw = raw[2:]
    if len(raw) == 11:
        return f"({raw[:2]}) {raw[2:7]}-{raw[7:]}"
    elif len(raw) == 10:
        return f"({raw[:2]}) {raw[2:6]}-{raw[6:]}"
    return raw or telefone_ou_chat


# ── CONFIGURAÇÕES (BANCO DE DADOS E AMBIENTE) ─────────────────────────────────

def obter_configuracoes_waha(conn=None) -> Dict[str, Any]:
    """Recupera as configurações salvas da API WAHA no banco de dados ou variáveis de ambiente."""
    default_cfg = {
        "id": "waha_config",
        "api_url": os.environ.get("WAHA_API_URL", "http://localhost:3000").rstrip("/"),
        "session_name": os.environ.get("WAHA_SESSION", "default").strip(),
        "api_key": os.environ.get("WAHA_API_KEY", "").strip(),
        "webhook_secret": os.environ.get("WAHA_WEBHOOK_SECRET", "").strip(),
        "ativo": 1 if os.environ.get("WAHA_ATIVO", "1").lower() in ("1", "true", "yes", "on") else 0,
        "auto_reply": 1 if os.environ.get("WAHA_AUTO_REPLY", "1").lower() in ("1", "true", "yes", "on") else 0,
        "notificar_admin": 0,
        "status_padrao": os.environ.get("WAHA_STATUS_PADRAO", "Pendente").strip(),
        "updated_at": agora_iso(),
    }

    should_close = False
    try:
        if conn is None:
            from db import get_db_connection
            conn = get_db_connection()
            should_close = True

        cur = conn.cursor()
        cur.execute("SELECT * FROM configuracoes_waha WHERE id='waha_config' LIMIT 1")
        row = cur.fetchone()

        if row:
            # Compatível com Row do SQLite e Dict do Postgres Wrapper
            keys = [col[0] for col in cur.description] if cur.description else list(row.keys()) if hasattr(row, 'keys') else []
            r_dict = dict(zip(keys, row)) if isinstance(row, (tuple, list)) else dict(row)

            cfg = {
                "id": "waha_config",
                "api_url": (r_dict.get("api_url") or default_cfg["api_url"]).rstrip("/"),
                "session_name": (r_dict.get("session_name") or default_cfg["session_name"]).strip(),
                "api_key": r_dict.get("api_key") or "",
                "webhook_secret": r_dict.get("webhook_secret") or "",
                "ativo": int(r_dict["ativo"]) if r_dict.get("ativo") is not None else default_cfg["ativo"],
                "auto_reply": int(r_dict["auto_reply"]) if r_dict.get("auto_reply") is not None else default_cfg["auto_reply"],
                "notificar_admin": int(r_dict.get("notificar_admin") or 0),
                "status_padrao": r_dict.get("status_padrao") or "Pendente",
                "updated_at": r_dict.get("updated_at") or agora_iso(),
            }
            # Atualiza variáveis em memória/ambiente
            os.environ["WAHA_API_URL"] = cfg["api_url"]
            os.environ["WAHA_SESSION"] = cfg["session_name"]
            if cfg["api_key"]:
                os.environ["WAHA_API_KEY"] = cfg["api_key"]
            return cfg
    except Exception as ex:
        logger.warning(f"Não foi possível ler configuracoes_waha do banco: {ex}")
    finally:
        if should_close and conn:
            try:
                conn.close()
            except Exception:
                pass

    return default_cfg


def salvar_configuracoes_waha(cfg: Dict[str, Any], conn=None) -> bool:
    """Persiste as configurações de integração com WAHA no banco de dados."""
    api_url = (cfg.get("api_url") or "http://localhost:3000").rstrip("/")
    session_name = (cfg.get("session_name") or "default").strip()
    api_key = (cfg.get("api_key") or "").strip()
    webhook_secret = (cfg.get("webhook_secret") or "").strip()
    ativo = 1 if str(cfg.get("ativo")) in ("1", "true", "True", "on") else 0
    auto_reply = 1 if str(cfg.get("auto_reply")) in ("1", "true", "True", "on") else 0
    notificar_admin = 1 if str(cfg.get("notificar_admin")) in ("1", "true", "True", "on") else 0
    status_padrao = (cfg.get("status_padrao") or "Pendente").strip()
    now_str = agora_iso()

    # Atualiza variáveis de ambiente imediatamente
    os.environ["WAHA_API_URL"] = api_url
    os.environ["WAHA_SESSION"] = session_name
    os.environ["WAHA_API_KEY"] = api_key
    os.environ["WAHA_ATIVO"] = str(ativo)
    os.environ["WAHA_AUTO_REPLY"] = str(auto_reply)

    should_close = False
    try:
        if conn is None:
            from db import get_db_connection, init_db
            init_db()
            conn = get_db_connection()
            should_close = True

        cur = conn.cursor()
        # Garante tabela criada
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

        cur.execute("SELECT id FROM configuracoes_waha WHERE id='waha_config'")
        existe = cur.fetchone()

        if existe:
            cur.execute(
                """
                UPDATE configuracoes_waha
                SET api_url=?, session_name=?, api_key=?, webhook_secret=?, ativo=?, auto_reply=?, notificar_admin=?, status_padrao=?, updated_at=?
                WHERE id='waha_config'
                """,
                (api_url, session_name, api_key, webhook_secret, ativo, auto_reply, notificar_admin, status_padrao, now_str),
            )
        else:
            cur.execute(
                """
                INSERT INTO configuracoes_waha
                (id, api_url, session_name, api_key, webhook_secret, ativo, auto_reply, notificar_admin, status_padrao, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("waha_config", api_url, session_name, api_key, webhook_secret, ativo, auto_reply, notificar_admin, status_padrao, now_str),
            )

        if hasattr(conn, "commit"):
            conn.commit()
        return True
    except Exception as ex:
        logger.error(f"Erro ao salvar configuracoes_waha: {ex}")
        return False
    finally:
        if should_close and conn:
            try:
                conn.close()
            except Exception:
                pass


# ── CLIENTE HTTP WAHA ────────────────────────────────────────────────────────

def _headers(api_key: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    key = api_key if api_key is not None else os.environ.get("WAHA_API_KEY", "")
    if key:
        headers["X-Api-Key"] = key
        headers["waha-api-key"] = key
    return headers


def testar_conexao_waha(api_url: Optional[str] = None, api_key: Optional[str] = None, session_name: Optional[str] = None) -> Dict[str, Any]:
    """Testa a conectividade com o servidor WAHA e inspeciona o status da sessão configurada."""
    cfg = obter_configuracoes_waha()
    url = (api_url or cfg.get("api_url") or "http://localhost:3000").rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    session = (session_name or cfg.get("session_name") or "default").strip()

    res_diagnostico = {
        "ok": False,
        "waha_online": False,
        "session_status": "OFFLINE",
        "session_name": session,
        "message": "",
        "sessions": [],
        "session_info": None,
        "api_url": url,
    }

    try:
        headers = _headers(key)
        # 1. Tenta listar sessões
        resp = requests.get(f"{url}/api/sessions?all=true", headers=headers, timeout=6)
        if resp.status_code in (401, 403):
            res_diagnostico["message"] = "Servidor WAHA acessível, porém a chave de API (X-Api-Key) foi recusada (401/403 Não Autorizado)."
            res_diagnostico["session_status"] = "AUTH_ERROR"
            return res_diagnostico

        if resp.status_code == 200:
            res_diagnostico["waha_online"] = True
            sessions_data = resp.json() if resp.text else []
            if isinstance(sessions_data, dict) and "sessions" in sessions_data:
                sessions_data = sessions_data["sessions"]
            if not isinstance(sessions_data, list):
                sessions_data = [sessions_data] if sessions_data else []

            res_diagnostico["sessions"] = sessions_data

            # Busca a sessão configurada
            target_sess = next((s for s in sessions_data if isinstance(s, dict) and s.get("name") == session), None)

            if target_sess:
                st = (target_sess.get("status") or "UNKNOWN").upper()
                res_diagnostico["session_status"] = st
                res_diagnostico["session_info"] = target_sess

                if st == "WORKING":
                    res_diagnostico["ok"] = True
                    phone_info = target_sess.get("me", {}).get("id", "") or target_sess.get("id", "")
                    nome_conectado = target_sess.get("me", {}).get("pushName", "")
                    info_extra = f" (Conectado: {nome_conectado or phone_info})" if phone_info else ""
                    res_diagnostico["message"] = f"✅ Conexão estabelecida! Sessão '{session}' está operacional (WORKING){info_extra}."
                elif st in ("SCAN_QR_CODE", "SCAN_QR"):
                    res_diagnostico["ok"] = True
                    res_diagnostico["message"] = f"🟡 Servidor WAHA ativo. A sessão '{session}' está aguardando leitura do QR Code."
                elif st in ("STARTING", "INITIALIZING"):
                    res_diagnostico["ok"] = True
                    res_diagnostico["message"] = f"🔄 A sessão '{session}' está iniciando..."
                elif st == "STOPPED":
                    res_diagnostico["message"] = f"⏸️ A sessão '{session}' existe mas está parada (STOPPED)."
                else:
                    res_diagnostico["message"] = f"Sessão '{session}' no estado: {st}."
            else:
                res_diagnostico["session_status"] = "NOT_FOUND"
                res_diagnostico["message"] = f"🟢 Servidor WAHA online, mas a sessão '{session}' ainda não foi criada. Você pode iniciá-la abaixo."
        else:
            res_diagnostico["message"] = f"Servidor WAHA respondeu com status HTTP {resp.status_code}: {resp.text[:120]}"

    except requests.exceptions.ConnectionError:
        res_diagnostico["message"] = f"Não foi possível conectar ao servidor WAHA em '{url}'. Verifique se o container/serviço WAHA está em execução."
    except requests.exceptions.Timeout:
        res_diagnostico["message"] = f"Tempo esgotado ao tentar conectar com '{url}'. Verifique a rede e se o WAHA está respondendo."
    except Exception as ex:
        res_diagnostico["message"] = f"Erro ao testar comunicação com WAHA: {ex}"

    return res_diagnostico


def obter_status_sessao(api_url: Optional[str] = None, api_key: Optional[str] = None, session_name: Optional[str] = None) -> Dict[str, Any]:
    """Obtém detalhes em tempo real da sessão do WhatsApp no WAHA."""
    cfg = obter_configuracoes_waha()
    url = (api_url or cfg.get("api_url") or "http://localhost:3000").rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    session = (session_name or cfg.get("session_name") or "default").strip()

    try:
        resp = requests.get(f"{url}/api/sessions/{session}", headers=_headers(key), timeout=5)
        if resp.status_code == 200:
            return {"ok": True, "session": resp.json()}
        elif resp.status_code == 404:
            return {"ok": False, "status": "NOT_FOUND", "error": "Sessão não encontrada"}
        return {"ok": False, "status": f"HTTP_{resp.status_code}", "error": resp.text}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def obter_qr_code(api_url: Optional[str] = None, api_key: Optional[str] = None, session_name: Optional[str] = None) -> Dict[str, Any]:
    """Tenta obter o QR Code da sessão do WAHA para exibição direta na tela."""
    cfg = obter_configuracoes_waha()
    url = (api_url or cfg.get("api_url") or "http://localhost:3000").rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    session = (session_name or cfg.get("session_name") or "default").strip()
    headers = _headers(key)

    # Tenta endpoints suportados pelo WAHA Core / Plus
    tentativas = [
        f"{url}/api/{session}/auth/qr",
        f"{url}/api/sessions/{session}/auth/qr",
        f"{url}/api/screenshot?session={session}",
    ]

    for endpoint in tentativas:
        try:
            r = requests.get(endpoint, headers=headers, timeout=6)
            if r.status_code == 200:
                content_type = r.headers.get("content-type", "").lower()
                if "image" in content_type:
                    b64 = base64.b64encode(r.content).decode("ascii")
                    mime = content_type.split(";")[0] or "image/png"
                    return {
                        "ok": True,
                        "data_url": f"data:{mime};base64,{b64}",
                        "raw_type": "image",
                        "endpoint": endpoint,
                    }
                elif "json" in content_type:
                    j = r.json()
                    if isinstance(j, dict):
                        b64 = j.get("base64") or j.get("image") or j.get("qr") or j.get("data")
                        if b64:
                            if not b64.startswith("data:"):
                                b64 = f"data:image/png;base64,{b64}"
                            return {"ok": True, "data_url": b64, "raw_type": "json_base64"}
                        elif j.get("raw"):
                            return {"ok": True, "raw_code": j.get("raw"), "raw_type": "raw_qr"}
        except Exception:
            pass

    return {"ok": False, "error": "QR Code indisponível no momento. A sessão já pode estar conectada ou iniciando."}


def controlar_sessao(acao: str, api_url: Optional[str] = None, api_key: Optional[str] = None, session_name: Optional[str] = None) -> Dict[str, Any]:
    """Inicia, reinicia, para ou efetua logout da sessão do WhatsApp no WAHA."""
    cfg = obter_configuracoes_waha()
    url = (api_url or cfg.get("api_url") or "http://localhost:3000").rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    session = (session_name or cfg.get("session_name") or "default").strip()
    headers = _headers(key)
    acao = acao.lower().strip()

    endpoints = {
        "start": f"{url}/api/sessions/{session}/start",
        "restart": f"{url}/api/sessions/{session}/restart",
        "stop": f"{url}/api/sessions/{session}/stop",
        "logout": f"{url}/api/sessions/{session}/logout",
    }

    target_endpoint = endpoints.get(acao)
    if not target_endpoint:
        return {"ok": False, "error": f"Ação desconhecida: {acao}"}

    try:
        r = requests.post(target_endpoint, headers=headers, json={"name": session}, timeout=10)
        # Se for start e retornar 404 (sessão não existe), tenta criar
        if acao == "start" and r.status_code == 404:
            r_create = requests.post(f"{url}/api/sessions", headers=headers, json={"name": session}, timeout=10)
            if r_create.status_code in (200, 201):
                return {"ok": True, "message": f"Sessão '{session}' criada e iniciada com sucesso!"}
            return {"ok": False, "error": f"Falha ao criar sessão: {r_create.text}"}

        if r.status_code in (200, 201, 204):
            return {"ok": True, "message": f"Comando '{acao}' enviado com sucesso para a sessão '{session}'!"}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text}"}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def enviar_mensagem_whatsapp(
    destinatario: str,
    texto: str,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    session_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Envia uma mensagem de texto pelo WhatsApp via WAHA POST /api/sendText."""
    cfg = obter_configuracoes_waha()
    url = (api_url or cfg.get("api_url") or "http://localhost:3000").rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    session = (session_name or cfg.get("session_name") or "default").strip()

    chat_id = normalizar_chat_id(destinatario)
    if not chat_id:
        return {"ok": False, "error": "Número de destinatário inválido ou vazio."}

    payload = {
        "chatId": chat_id,
        "text": texto,
        "session": session,
    }

    try:
        r = requests.post(f"{url}/api/sendText", headers=_headers(key), json=payload, timeout=12)
        if r.status_code in (200, 201):
            return {"ok": True, "res": r.json() if r.text else {}}
        return {"ok": False, "status_code": r.status_code, "error": r.text}
    except Exception as ex:
        logger.error(f"Erro ao enviar mensagem WhatsApp via WAHA: {ex}")
        return {"ok": False, "error": str(ex)}


# ── AUDITORIA & REGISTRO DE MENSAGENS NO BANCO ───────────────────────────────

def salvar_log_mensagem(
    chat_id: str,
    telefone: str,
    nome_contato: str,
    direcao: str,
    conteudo: str,
    tipo_evento: str = "mensagem",
    pedido_id: Optional[str] = None,
    raw_payload: Optional[Any] = None,
    conn=None,
) -> str:
    """Registra uma mensagem recebida ou enviada na tabela waha_mensagens."""
    msg_id = str(uuid.uuid4())
    now_str = agora_iso()
    raw_str = json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else ""
    should_close = False

    try:
        if conn is None:
            from db import get_db_connection
            conn = get_db_connection()
            should_close = True

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO waha_mensagens
            (id, chat_id, telefone, nome_contato, direcao, conteudo, tipo_evento, pedido_id, raw_payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, chat_id, telefone, nome_contato, direcao, conteudo, tipo_evento, pedido_id, raw_str, now_str),
        )
        if hasattr(conn, "commit"):
            conn.commit()
    except Exception as ex:
        logger.warning(f"Erro ao registrar log de mensagem WAHA: {ex}")
    finally:
        if should_close and conn:
            try:
                conn.close()
            except Exception:
                pass

    return msg_id


def obter_mensagens_recentes(limit: int = 50, conn=None) -> List[Dict[str, Any]]:
    """Recupera as mensagens mais recentes para visualização no Developer Hub."""
    should_close = False
    mensagens = []
    try:
        if conn is None:
            from db import get_db_connection
            conn = get_db_connection()
            should_close = True

        cur = conn.cursor()
        cur.execute("SELECT * FROM waha_mensagens ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()

        keys = [col[0] for col in cur.description] if cur.description else []
        for r in rows:
            if isinstance(r, (tuple, list)):
                d = dict(zip(keys, r))
            else:
                d = dict(r)
            mensagens.append(d)
    except Exception as ex:
        logger.warning(f"Erro ao buscar mensagens recentes WAHA: {ex}")
    finally:
        if should_close and conn:
            try:
                conn.close()
            except Exception:
                pass

    return mensagens


# ── PROCESSAMENTO DE INTENÇÃO & CRIAÇÃO AUTOMÁTICA DE PEDIDOS ───────────────

def identificar_intencao_e_produto(texto: str, produtos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analisa a mensagem do cliente no WhatsApp e determina se há intenção de pedido,
    catálogo, consulta de status ou dúvidas gerais.
    """
    t_clean = remover_acentos(texto)
    res = {
        "is_order": False,
        "is_catalog": False,
        "is_status": False,
        "is_greeting": False,
        "produto": None,
        "quantidade": 1,
        "confidence": 0.0,
    }

    if not texto or not texto.strip():
        return res

    # 1. Catálogo / Preços
    gatilhos_catalogo = [
        "catalogo", "cardapio", "precos", "quanto custa", "quais bolsas",
        "o que voces tem", "quais modelos", "tabela de precos", "ver produtos",
        "modelos disponiveis", "fotos", "quais tem", "quais tem pronta entrega"
    ]
    if any(g in t_clean for g in gatilhos_catalogo):
        res["is_catalog"] = True
        return res

    # 2. Consulta de Status
    gatilhos_status = [
        "status", "meu pedido", "como ta meu pedido", "como esta meu pedido",
        "ja ficou pronta", "ja enviou", "rastreio", "quando fica pronta",
        "situacao do pedido", "meu andamento"
    ]
    if any(g in t_clean for g in gatilhos_status):
        res["is_status"] = True
        return res

    # 3. Saudação
    gatilhos_saudacao = [
        "ola", "oi", "bom dia", "boa tarde", "boa noite", "ola tudo bem",
        "oi tudo bem", "opa", "oie"
    ]
    if t_clean in gatilhos_saudacao or any(t_clean.startswith(s) and len(t_clean.split()) <= 3 for s in ["ola", "oi", "bom dia", "boa tarde", "boa noite"]):
        res["is_greeting"] = True

    # 4. Extração de Quantidade
    match_qtd = re.search(r"(\d+)\s*(?:unidades?|pecas?|bolsas?|un|x)?", t_clean)
    qtd = int(match_qtd.group(1)) if match_qtd else 1
    if qtd <= 0:
        qtd = 1
    res["quantidade"] = qtd

    # 5. Matching com Produtos do Catálogo
    if produtos:
        produto_alvo = None

        # a) Busca por GTIN
        gtin_m = re.search(r"\b(\d{8,14})\b", texto)
        if gtin_m:
            g_num = gtin_m.group(1)
            produto_alvo = next((p for p in produtos if (p.get("gtin") or "").strip() == g_num), None)

        # b) Busca por correspondência exata ou substring direta
        if not produto_alvo:
            for p in produtos:
                p_clean = remover_acentos(p.get("nome", ""))
                if p_clean and (p_clean in t_clean or t_clean in p_clean):
                    produto_alvo = p
                    break

        # c) Busca inteligente por sobreposição de palavras-chave significativas
        if not produto_alvo:
            stopwords = {"bolsa", "bolsas", "modelo", "modelos", "para", "com", "uma", "um", "de", "da", "do", "em", "quero", "pedir", "encomendar", "comprar"}
            t_palavras = set(w for w in t_clean.split() if len(w) >= 3 and w not in stopwords)
            melhor_score = 0

            for p in produtos:
                p_clean = remover_acentos(p.get("nome", ""))
                p_palavras = set(w for w in p_clean.split() if len(w) >= 3 and w not in stopwords)
                intersec = t_palavras.intersection(p_palavras)
                score = len(intersec)
                if score > melhor_score:
                    melhor_score = score
                    produto_alvo = p

        # Gatilhos de pedido explícitos
        gatilhos_pedido = [
            "quero", "gostaria de", "pedir", "fazer pedido", "encomendar", "comprar",
            "encomenda", "manda", "envia", "separa", "fazer um pedido", "quero encomendar",
            "vou querer", "tem como fazer", "reserva", "anotar", "pedido de", "solicitar"
        ]
        tem_gatilho_pedido = any(g in t_clean for g in gatilhos_pedido)

        # Se encontrou produto ou se há forte indício de pedido
        if produto_alvo and (tem_gatilho_pedido or match_qtd or len(t_clean.split()) <= 4):
            res["is_order"] = True
            res["produto"] = produto_alvo
            res["confidence"] = 0.95
            return res
        elif tem_gatilho_pedido and produtos:
            # Caso o cliente diga "quero encomendar" e tenha citado algo parecido
            res["is_order"] = True
            res["produto"] = produto_alvo or produtos[0]
            res["confidence"] = 0.75
            return res

    return res


def criar_pedido_whatsapp(
    cliente_nome: str,
    cliente_telefone: str,
    produto: Dict[str, Any],
    quantidade: int = 1,
    status_padrao: Optional[str] = None,
    observacao_extra: str = "",
    conn=None,
) -> Dict[str, Any]:
    """Cria e persiste um novo pedido de cliente originado pelo WhatsApp."""
    qtd = max(1, int(quantidade or 1))
    preco_unit = float(produto.get("preco_venda") or 0.0)
    valor_total = round(preco_unit * qtd, 2)
    dt = datetime.datetime.now()
    dt_pedido = dt.strftime("%d/%m/%Y")
    dt_iso = dt.strftime("%Y-%m-%d %H:%M:%S")
    status = status_padrao or "Pendente"

    obs = f"📱 Pedido via WhatsApp · Tel: {formatar_telefone_legivel(cliente_telefone)}"
    if observacao_extra:
        obs += f" · {observacao_extra}"

    novo_pedido = {
        "id": str(uuid.uuid4()),
        "cliente": cliente_nome or f"Cliente WhatsApp ({formatar_telefone_legivel(cliente_telefone)})",
        "produto_id": produto.get("id"),
        "produto_nome": produto.get("nome"),
        "produto_emoji": produto.get("emoji", "👜"),
        "quantidade": qtd,
        "preco_unitario": preco_unit,
        "valor_total": valor_total,
        "status": status,
        "materiais_baixados": 0,
        "usou_estoque_pronto": 0,
        "data_pedido": dt_pedido,
        "data_pedido_iso": dt_iso,
        "observacoes": obs,
        "origem": "whatsapp",
        "telefone_cliente": cliente_telefone,
        "created_at": dt.isoformat(),
        "updated_at": dt.isoformat(),
    }

    should_close = False
    try:
        if conn is None:
            from db import get_db_connection, init_db
            init_db()
            conn = get_db_connection()
            should_close = True

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pedidos
            (id, cliente, produto_id, produto_nome, produto_emoji, quantidade, preco_unitario, valor_total,
             status, materiais_baixados, usou_estoque_pronto, data_pedido, data_pedido_iso, observacoes,
             origem, telefone_cliente, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                novo_pedido["id"],
                novo_pedido["cliente"],
                novo_pedido["produto_id"],
                novo_pedido["produto_nome"],
                novo_pedido["produto_emoji"],
                novo_pedido["quantidade"],
                novo_pedido["preco_unitario"],
                novo_pedido["valor_total"],
                novo_pedido["status"],
                novo_pedido["materiais_baixados"],
                novo_pedido["usou_estoque_pronto"],
                novo_pedido["data_pedido"],
                novo_pedido["data_pedido_iso"],
                novo_pedido["observacoes"],
                novo_pedido["origem"],
                novo_pedido["telefone_cliente"],
                novo_pedido["created_at"],
                novo_pedido["updated_at"],
            ),
        )
        if hasattr(conn, "commit"):
            conn.commit()
    except Exception as ex:
        logger.error(f"Erro ao inserir pedido via WhatsApp no banco: {ex}")
        try:
            from app import carregar_json, salvar_json
            pedidos_json = carregar_json("pedidos.json")
            pedidos_json.append(novo_pedido)
            salvar_json("pedidos.json", pedidos_json)
        except Exception:
            pass
    finally:
        if should_close and conn:
            try:
                conn.close()
            except Exception:
                pass

    return novo_pedido


# ── PROCESSAMENTO DO WEBHOOK WAHA ────────────────────────────────────────────

def processar_webhook_waha(payload: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Ponto central de recepção de eventos do WAHA.
    Processa mensagens de clientes, cadastra pedidos automáticos,
    dispara respostas personalizadas pelo WhatsApp e audita todas as ações.
    """
    cfg = obter_configuracoes_waha()
    if not cfg.get("ativo"):
        return {"ignored": True, "reason": "Integração WAHA desativada nas configurações."}

    # Validação opcional de Secret no header
    secret_esperado = cfg.get("webhook_secret")
    if secret_esperado and headers:
        token_recebido = headers.get("X-Webhook-Secret") or headers.get("x-webhook-secret") or headers.get("Authorization", "")
        if token_recebido.replace("Bearer ", "").strip() != secret_esperado:
            return {"error": "Unauthorized webhook token", "code": 401}

    event = (payload.get("event") or "").strip()
    data = payload.get("payload") or payload.get("data") or {}

    # Ignora eventos que não sejam mensagens recebidas
    if event not in ("message", "message.any", "message:in", "") and "body" not in data and "body" not in payload:
        return {"ignored": True, "event": event}

    # Se os campos de mensagem vierem no primeiro nível
    msg_data = data if data.get("body") is not None or data.get("from") is not None else payload

    # Ignora mensagens enviadas por nós mesmos (fromMe)
    if msg_data.get("fromMe") is True:
        return {"ignored": True, "reason": "Mensagem enviada pelo próprio ateliê (fromMe=true)."}

    chat_id = msg_data.get("from") or msg_data.get("chatId") or ""
    # Ignora mensagens de grupos para não poluir os pedidos
    if chat_id.endswith("@g.us"):
        return {"ignored": True, "reason": "Mensagem em grupo ignorada."}

    telefone_limpo = re.sub(r"\D", "", chat_id.split("@")[0])
    corpo_mensagem = str(msg_data.get("body") or msg_data.get("text") or "").strip()

    if not corpo_mensagem:
        return {"ignored": True, "reason": "Mensagem sem texto legível."}

    # Identifica o nome do contato
    _d = msg_data.get("_data") or {}
    nome_contato = (
        msg_data.get("notifyName")
        or msg_data.get("pushName")
        or _d.get("notifyName")
        or _d.get("pushname")
        or formatar_telefone_legivel(telefone_limpo)
    )

    # Carrega catálogo de produtos para análise
    from app import carregar_produtos
    produtos = carregar_produtos()

    intencao = identificar_intencao_e_produto(corpo_mensagem, produtos)
    auto_reply = bool(cfg.get("auto_reply"))
    status_padrao = cfg.get("status_padrao", "Pendente")

    # ── CASO 1: CLIENTE SOLICITOU UM PEDIDO ──────────────────────────────────
    if intencao.get("is_order") and intencao.get("produto"):
        prod = intencao["produto"]
        qtd = intencao["quantidade"]
        novo_pedido = criar_pedido_whatsapp(
            cliente_nome=nome_contato,
            cliente_telefone=telefone_limpo,
            produto=prod,
            quantidade=qtd,
            status_padrao=status_padrao,
            observacao_extra=f"Mensagem: '{corpo_mensagem}'",
        )

        salvar_log_mensagem(
            chat_id=chat_id,
            telefone=telefone_limpo,
            nome_contato=nome_contato,
            direcao="incoming",
            conteudo=corpo_mensagem,
            tipo_evento="pedido_criado",
            pedido_id=novo_pedido["id"],
            raw_payload=payload,
        )

        resposta_texto = ""
        if auto_reply:
            resposta_texto = (
                f"🎉 Olá, *{nome_contato}*! Seu pedido foi recebido com sucesso no *Ateliê*! 🧾✨\n\n"
                f"• *Produto*: {prod.get('emoji', '👜')} *{qtd}x {prod['nome']}*\n"
                f"• *Valor Total*: *{formatar_moeda(novo_pedido['valor_total'])}*\n"
                f"• *Status*: `{novo_pedido['status']}`\n"
                f"• *Nº do Pedido*: `#{novo_pedido['id'][:8]}`\n\n"
                f"Nossa artesã já registrou sua encomenda no sistema e em breve entraremos em contato com você por aqui. Agradecemos a confiança! 💖"
            )
            enviar_mensagem_whatsapp(chat_id, resposta_texto)
            salvar_log_mensagem(
                chat_id=chat_id,
                telefone=telefone_limpo,
                nome_contato=nome_contato,
                direcao="outgoing",
                conteudo=resposta_texto,
                tipo_evento="confirmacao_pedido",
                pedido_id=novo_pedido["id"],
            )

        return {
            "success": True,
            "action": "pedido_criado",
            "pedido": novo_pedido,
            "replied": auto_reply,
        }

    # ── CASO 2: CLIENTE PEDIU CATÁLOGO / PREÇOS ──────────────────────────────
    if intencao.get("is_catalog"):
        salvar_log_mensagem(
            chat_id=chat_id,
            telefone=telefone_limpo,
            nome_contato=nome_contato,
            direcao="incoming",
            conteudo=corpo_mensagem,
            tipo_evento="consulta_catalogo",
            raw_payload=payload,
        )

        if auto_reply:
            linhas = []
            for p in produtos[:8]:
                prontas = int(p.get("estoque_pronto") or 0)
                tag = f" _(🛍️ {prontas} pronta-entrega)_" if prontas > 0 else " _(Sob encomenda)_"
                linhas.append(f"• {p.get('emoji','👜')} *{p['nome']}* — {formatar_moeda(p.get('preco_venda'))}{tag}")

            msg_catalogo = (
                f"👜 *Catálogo de Bolsas do Ateliê:*\n\n"
                + "\n".join(linhas) + "\n\n"
                f"💡 *Como fazer um pedido:* Basta responder informando a bolsa e a quantidade desejada!\n"
                f"Exemplo: _\"Quero 2 {produtos[0]['nome'] if produtos else 'Bolsas'}\"_"
            )
            enviar_mensagem_whatsapp(chat_id, msg_catalogo)
            salvar_log_mensagem(
                chat_id=chat_id,
                telefone=telefone_limpo,
                nome_contato=nome_contato,
                direcao="outgoing",
                conteudo=msg_catalogo,
                tipo_evento="envio_catalogo",
            )

        return {"success": True, "action": "catalogo_enviado", "replied": auto_reply}

    # ── CASO 3: CONSULTA DE STATUS DO PEDIDO ─────────────────────────────────
    if intencao.get("is_status"):
        salvar_log_mensagem(
            chat_id=chat_id,
            telefone=telefone_limpo,
            nome_contato=nome_contato,
            direcao="incoming",
            conteudo=corpo_mensagem,
            tipo_evento="consulta_status",
            raw_payload=payload,
        )

        if auto_reply:
            from app import carregar_pedidos
            todos_pedidos = carregar_pedidos()
            # Busca pedidos pelo telefone ou nome aproximado
            pedidos_cliente = [
                p for p in todos_pedidos
                if telefone_limpo in str(p.get("telefone_cliente") or p.get("observacoes") or "")
                or (nome_contato and len(nome_contato) >= 3 and remover_acentos(nome_contato) in remover_acentos(p.get("cliente", "")))
            ]

            if pedidos_cliente:
                linhas = [f"• {p.get('produto_emoji','👜')} *{p['produto_nome']}* (Qtd: {p['quantidade']}) — Status: *{p['status']}* (Criado em: {p.get('data_pedido','')})" for p in pedidos_cliente[:4]]
                msg_st = f"🧾 *Seus Pedidos no Ateliê:*\n\n" + "\n".join(linhas) + "\n\nSe precisar de mais informações, nossa equipe está à disposição!"
            else:
                msg_st = f"Olá, *{nome_contato}*! Não encontramos nenhum pedido em andamento associado ao seu número. Para fazer um novo pedido, você pode nos mandar o modelo desejado ou pedir o *Catálogo*! ✨"

            enviar_mensagem_whatsapp(chat_id, msg_st)
            salvar_log_mensagem(
                chat_id=chat_id,
                telefone=telefone_limpo,
                nome_contato=nome_contato,
                direcao="outgoing",
                conteudo=msg_st,
                tipo_evento="resposta_status",
            )

        return {"success": True, "action": "status_consultado", "replied": auto_reply}

    # ── CASO 4: SAUDAÇÃO OU OUTRA MENSAGEM ───────────────────────────────────
    salvar_log_mensagem(
        chat_id=chat_id,
        telefone=telefone_limpo,
        nome_contato=nome_contato,
        direcao="incoming",
        conteudo=corpo_mensagem,
        tipo_evento="duvida_ou_saudacao",
        raw_payload=payload,
    )

    if auto_reply:
        msg_saudacao = (
            f"Olá, *{nome_contato}*! 👋 Seja bem-vindo(a) ao *Ateliê*!\n\n"
            f"Sou a assistente virtual do ateliê. Como posso te ajudar hoje?\n\n"
            f"• Para fazer um pedido: Envie *\"Quero [quantidade] [nome da bolsa]\"*\n"
            f"• Para ver nossos produtos: Envie *\"Catálogo\"*\n"
            f"• Para consultar seu pedido: Envie *\"Status\"*\n\n"
            f"Estamos prontos para confeccionar sua peça com todo carinho! ✨"
        )
        enviar_mensagem_whatsapp(chat_id, msg_saudacao)
        salvar_log_mensagem(
            chat_id=chat_id,
            telefone=telefone_limpo,
            nome_contato=nome_contato,
            direcao="outgoing",
            conteudo=msg_saudacao,
            tipo_evento="resposta_saudacao",
        )

    return {"success": True, "action": "saudacao_enviada", "replied": auto_reply}


# ── FERRAMENTA DE SIMULAÇÃO RÁPIDA (PARA TESTES DO DESENVOLVEDOR) ───────────

def simular_pedido_whatsapp(
    cliente_nome: str,
    cliente_telefone: str,
    produto_id: str,
    quantidade: int = 1,
    mensagem_simulada: str = "",
) -> Dict[str, Any]:
    """
    Permite ao desenvolvedor simular uma mensagem de pedido do WhatsApp
    diretamente pelo Developer Hub, testando a criação e o aparecimento imediato no site.
    """
    from app import carregar_produtos
    produtos = carregar_produtos()
    produto = next((p for p in produtos if str(p.get("id")) == str(produto_id)), None)
    if not produto and produtos:
        produto = produtos[0]

    if not produto:
        return {"success": False, "error": "Nenhum produto cadastrado no ateliê para simulação."}

    cfg = obter_configuracoes_waha()
    qtd = max(1, int(quantidade or 1))
    telefone = re.sub(r"\D", "", cliente_telefone or "5511999998888")
    nome = (cliente_nome or "Cliente Simulado WhatsApp").strip()
    msg = mensagem_simulada or f"Olá, gostaria de encomendar {qtd} {produto['nome']} por favor!"

    # 1. Registra mensagem de entrada simulada
    salvar_log_mensagem(
        chat_id=f"{telefone}@c.us",
        telefone=telefone,
        nome_contato=nome,
        direcao="incoming",
        conteudo=msg,
        tipo_evento="pedido_criado",
    )

    # 2. Cria o pedido
    pedido = criar_pedido_whatsapp(
        cliente_nome=nome,
        cliente_telefone=telefone,
        produto=produto,
        quantidade=qtd,
        status_padrao=cfg.get("status_padrao", "Pendente"),
        observacao_extra="🧪 Simulação via Developer Hub",
    )

    # 3. Registra confirmação enviada
    msg_conf = f"🎉 Pedido #{pedido['id'][:8]} confirmado! {qtd}x {produto.get('emoji','👜')} {produto['nome']} para {nome}."
    salvar_log_mensagem(
        chat_id=f"{telefone}@c.us",
        telefone=telefone,
        nome_contato=nome,
        direcao="outgoing",
        conteudo=msg_conf,
        tipo_evento="confirmacao_pedido",
        pedido_id=pedido["id"],
    )

    return {
        "success": True,
        "pedido": pedido,
        "message": f"Pedido #{pedido['id'][:8]} criado com sucesso para {nome}! Ele já está disponível na aba Pedidos.",
    }
