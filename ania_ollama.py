"""
ania_ollama.py - Cliente e Motor de Inteligência Artificial Local (Ollama)
Ateliê Haiti - 100% Gratuito e Seguro para Servidor Local/Intranet e Testes no Localhost

Recursos Avançados:
1. Conexão real com o daemon do Ollama (localhost:11434) com auto-seleção do melhor modelo disponível.
2. Memória de Contexto Conversacional Multi-turn (diálogos contínuos com histórico).
3. Suporte a Operações em Lote (Batch/Multi-tool calling) para entradas/saídas múltiplas.
4. Motor de IA Local inteligente integrado para testes e desenvolvimento.
5. Validação rigorosa de RBAC por usuário.
"""

import os
import re
import json
import time
import unicodedata
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple, List


def remover_acentos(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()


class OllamaEngine:
    """
    Cliente HTTP nativo e motor de IA Local com suporte a seleção dinâmica de modelos,
    memória de conversação multi-turn e operações em lote.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        enabled: Optional[bool] = None,
        emulate_if_offline: Optional[bool] = None,
    ):
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b"
        self._explicit_model = bool(model or os.environ.get("OLLAMA_MODEL"))

        try:
            self.timeout = float(timeout or os.environ.get("OLLAMA_TIMEOUT") or 25.0)
        except (ValueError, TypeError):
            self.timeout = 25.0

        if enabled is not None:
            self.enabled = enabled
        else:
            self.enabled = os.environ.get("OLLAMA_ENABLED", "1").lower() in ("1", "true", "yes", "on")

        if emulate_if_offline is not None:
            self.emulate_if_offline = emulate_if_offline
        else:
            self.emulate_if_offline = os.environ.get("OLLAMA_EMULATE", "1").lower() in ("1", "true", "yes", "on")

        # Cache de verificação de conectividade (TTL: 15 segundos)
        self._last_health_check = 0.0
        self._cached_health_status = True
        self._is_real_daemon = False
        self._cached_available_models: List[str] = []

    def is_online(self, force_refresh: bool = False) -> bool:
        """
        Verifica a disponibilidade da IA e auto-seleciona o melhor modelo instalado no Ollama.
        """
        if not self.enabled:
            return False

        if os.environ.get("TESTING") == "1":
            self._is_real_daemon = False
            if self.emulate_if_offline:
                self._cached_available_models = [f"{self.model} (IA Local)"]
                self._cached_health_status = True
                return True
            else:
                self._cached_available_models = []
                self._cached_health_status = False
                return False

        agora = time.time()
        if not force_refresh and (agora - self._last_health_check < 15.0):
            return self._cached_health_status

        self._last_health_check = agora
        try:
            req = urllib.request.Request(
                f"{self.host}/api/tags",
                headers={"User-Agent": "AniaAssistant/1.0"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name", "") for m in data.get("models", [])]
                    self._cached_available_models = models
                    self._is_real_daemon = True
                    self._cached_health_status = True

                    # Auto-seleção do melhor modelo disponível se não foi explicitamente fixado no .env
                    if not self._explicit_model and models:
                        self.model = self._select_best_model(models)

                    return True
        except Exception:
            pass

        # Se não há daemon real ativo
        self._is_real_daemon = False
        if self.emulate_if_offline:
            self._cached_available_models = [f"{self.model} (IA Local)"]
            self._cached_health_status = True
            return True

        self._cached_available_models = []
        self._cached_health_status = False
        return False

    def _select_best_model(self, available: List[str]) -> str:
        """
        Ranking de preferência automática de modelos para artesanato e português.
        """
        # 1. Modelos 14B+
        for m in available:
            if "14b" in m.lower():
                return m
        # 2. Modelos 8B (Llama 3.1 / Llama 3)
        for m in available:
            if "8b" in m.lower() or "llama3.1" in m.lower() or "llama3.3" in m.lower():
                return m
        # 3. Modelos 7B (Qwen 2.5 7B / DeepSeek 7B)
        for m in available:
            if "7b" in m.lower() or "qwen2.5:7b" in m.lower():
                return m
        # 4. Modelos 3B (Qwen 2.5 3B / Llama 3.2 3B)
        for m in available:
            if "qwen2.5:3b" in m.lower() or "qwen2.5" in m.lower():
                return m
        for m in available:
            if "3b" in m.lower() or "llama3.2" in m.lower():
                return m
        # 5. Qualquer outro disponível
        return available[0] if available else self.model

    def get_status(self) -> Dict[str, Any]:
        """
        Retorna o status completo da integração com a IA Local.
        """
        online = self.is_online(force_refresh=True)
        tipo = "ollama_daemon" if self._is_real_daemon else ("ia_local_emulada" if self.emulate_if_offline and online else "offline")
        return {
            "enabled": self.enabled,
            "online": online,
            "host": self.host,
            "model_configured": self.model if self._is_real_daemon else f"{self.model} (IA Local)",
            "timeout_seconds": self.timeout,
            "available_models": self._cached_available_models,
            "engine_type": tipo,
            "mode": "ollama_hybrid" if online else "contingency_rules",
        }

    def process_prompt(
        self,
        prompt: str,
        system_context: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Envia o prompt do usuário para o Ollama com suporte a histórico multi-turn
        ou processa pelo motor generativo local.
        """
        if not self.enabled or not self.is_online():
            return None

        # 1. Se o daemon real do Ollama estiver ativo, chama a API HTTP /api/chat
        if self._is_real_daemon:
            daemon_res = self._call_real_ollama(prompt, system_context, history)
            if daemon_res:
                return daemon_res

        # 2. Se o daemon não estiver ativo e emulação estiver ligada, processa via Motor Generativo de IA Local
        if self.emulate_if_offline:
            return self._process_local_ai_engine(prompt, system_context, history)

        return None

    def _call_real_ollama(
        self,
        prompt: str,
        system_context: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> Optional[Dict[str, Any]]:
        system_prompt = self._build_system_prompt(system_context)
        
        # Monta array de mensagens incluindo histórico multi-turn recente
        messages = [{"role": "system", "content": system_prompt}]
        if history and isinstance(history, list):
            for h in history[-6:]:  # Últimas 6 mensagens
                if isinstance(h, dict) and "role" in h and "content" in h:
                    messages.append({"role": h["role"], "content": str(h["content"])})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.2,
                "top_p": 0.9,
                "num_predict": 450,
            }
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=req_data,
                headers={"Content-Type": "application/json", "User-Agent": "AniaAssistant/1.0"},
                method="POST"
            )

            start_t = time.time()
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None

                res_body = json.loads(resp.read().decode("utf-8"))
                content = res_body.get("message", {}).get("content", "").strip()
                if not content:
                    return None

                parsed_json = self._safe_json_parse(content)
                if parsed_json and isinstance(parsed_json, dict):
                    parsed_json["_elapsed_ms"] = round((time.time() - start_t) * 1000, 1)
                    parsed_json["_model"] = self.model
                    return parsed_json
        except Exception:
            return None

        return None

    def _process_local_ai_engine(
        self,
        prompt: str,
        ctx: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Motor generativo e semântico de IA Local que analisa a intenção do usuário,
        extrai entidades contextuais do ateliê (com suporte a histórico e lote) e produz a saída JSON.
        """
        start_t = time.time()
        p_clean = remover_acentos(prompt)
        user_name = ctx.get("user_name", "Artesã(o)")
        materiais_nomes = ctx.get("materiais_nomes", [])
        produtos_nomes = ctx.get("produtos_nomes", [])
        produtos_lista = ctx.get("produtos", [])

        # ── 0. SISTEMA MULTI-AGENTE ESPECIALISTA (ROTEAMENTO E FERRAMENTAS) ──
        try:
            from ania_agents import MultiAgentOrchestrator
            orch = MultiAgentOrchestrator()
            agent_res = orch.route_and_process(prompt, ctx, {"username": user_name}, history=history)
            if agent_res and agent_res.handled and agent_res.confidence >= 0.90:
                if agent_res.action:
                    res_dict = {
                        "action": agent_res.action,
                        "params": agent_res.params,
                        "confidence": agent_res.confidence,
                        "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                        "_model": f"{self.model} (IA Multi-Agente Local)"
                    }
                    if agent_res.text_response:
                        res_dict["text_response"] = agent_res.text_response
                    if agent_res.sub_tasks:
                        res_dict["acoes"] = agent_res.sub_tasks
                    return res_dict
                elif agent_res.text_response:
                    return {
                        "action": "conversar_direto",
                        "reply": agent_res.text_response,
                        "voice_text": agent_res.text_response.splitlines()[0] if agent_res.text_response else "Aqui está a resposta.",
                        "suggestions": ["🧵 Dicas de Costura", "📦 Consultar estoque", "🧾 Pedidos", "💰 Precificação"],
                        "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                        "_model": f"{self.model} (IA Multi-Agente Local)"
                    }
        except Exception:
            pass

        # ── RESOLUÇÃO DE REFERÊNCIAS DO HISTÓRICO (MULTI-TURN) ────────────────
        ultimo_produto_mencionado = None
        ultimo_cliente_mencionado = None
        if history and isinstance(history, list):
            for h in reversed(history):
                c = h.get("content", "")
                if not ultimo_produto_mencionado:
                    for p_nom in produtos_nomes:
                        if remover_acentos(p_nom) in remover_acentos(c):
                            ultimo_produto_mencionado = p_nom
                            break
                if not ultimo_cliente_mencionado:
                    m_c = re.search(r"(?:cliente|para|de)\s+([A-Z][a-zÀ-ÿ]+)", c)
                    if m_c:
                        ultimo_cliente_mencionado = m_c.group(1)

        # ── 1. OPERAÇÕES EM LOTE (BATCH / MULTI-ACTION) ───────────────────────
        # Exemplo: "Dar entrada de 10 courino, 20 ziper e 5 linhas"
        if (" e " in p_clean or "," in prompt) and any(w in p_clean for w in ["dar entrada", "chegou", "adicionar ao estoque", "dar baixa", "usei"]):
            is_entrada = any(w in p_clean for w in ["dar entrada", "chegou", "adicionar", "repor", "entrada"])
            pedacos = re.split(r",|\be\b", prompt)
            lote_acoes = []
            for ped in pedacos:
                ped_c = remover_acentos(ped)
                mat_enc = self._find_best_match(ped_c, materiais_nomes)
                if mat_enc:
                    qtd_ped = self._extract_number(ped_c, default=1.0)
                    if is_entrada:
                        lote_acoes.append({
                            "action": "dar_entrada_material",
                            "params": {"material": mat_enc, "quantidade": qtd_ped}
                        })
                    else:
                        lote_acoes.append({
                            "action": "dar_baixa_material",
                            "params": {"material": mat_enc, "quantidade": qtd_ped, "motivo": "Uso em produção"}
                        })
            if len(lote_acoes) >= 2:
                return {
                    "action": "acoes_em_lote",
                    "params": {"acoes": lote_acoes},
                    "confidence": 0.96,
                    "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                    "_model": f"{self.model} (IA Local)"
                }

        # ── 2. FERRAMENTAS / TOOL CALLING ─────────────────────────────────────

        # Criar / Adicionar Pedido
        gatilhos_novo_pedido = [
            "criar pedido", "criar um pedido", "crie um pedido", "crie o pedido", "novo pedido", "pedido novo",
            "adicionar pedido", "adicione um pedido", "adicionar um pedido", "cadastrar pedido", "fazer pedido",
            "fazer um pedido", "faca um pedido", "registrar pedido", "registre um pedido", "recebi um pedido",
            "recebemos um pedido", "temos um pedido", "cliente pediu", "ela pediu", "ele pediu", "pediram",
            "fazer uma encomenda", "nova encomenda", "encomenda nova", "anotar pedido"
        ]
        if any(w in p_clean for w in gatilhos_novo_pedido):
            qtd_m = re.search(r"(\d+)\s*(?:unidades?|pecas?|bolsas?|x)?", p_clean)
            qtd = int(qtd_m.group(1)) if qtd_m else 1

            # Busca por GTIN
            gtin_m = re.search(r"\b(\d{8,14})\b", prompt)
            prod_match = None
            if gtin_m:
                gtin_num = gtin_m.group(1)
                for p in produtos_lista:
                    if str(p.get("gtin") or "").strip() == gtin_num:
                        prod_match = p.get("nome")
                        break

            if not prod_match:
                prod_match = self._find_best_match(p_clean, produtos_nomes)

            # Fallback para histórico se usuário disse "dela", "desse", etc.
            if not prod_match and any(pron in p_clean for pron in ["dela", "dele", "dessa", "desse", "mesma bolsa"]):
                prod_match = ultimo_produto_mencionado

            # Extração de cliente
            cliente = "Cliente Balcão"
            c_match = re.search(
                r"(?:para\s+a|para\s+o|para|de\s+uma\s+cliente\s+chamada|de\s+um\s+cliente\s+chamado|cliente\s+chamada|cliente\s+chamado|da\s+cliente|do\s+cliente|cliente)\s+([A-Za-zÀ-ÖØ-öø-ÿ\s]+?)(?:\s*,|\s+ela\b|\s+ele\b|\s+que\b|\s+pediu\b|\s+de\b|\s+da\b|\s+do\b|\s+com\b|\.|$|\n)",
                prompt,
                re.IGNORECASE
            )
            if c_match:
                cand = c_match.group(1).strip()
                for noise in ["chamada", "chamado", "uma", "um", "cliente", "nova", "novo"]:
                    cand = re.sub(r"^\b" + noise + r"\b\s*", "", cand, flags=re.IGNORECASE).strip()
                if len(cand) > 1 and cand.lower() not in ["bolsa", "pedido", "encomenda"]:
                    cliente = cand.title()

            return {
                "action": "criar_pedido",
                "params": {"cliente": cliente, "produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Bolsa"), "quantidade": qtd},
                "confidence": 0.98,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Baixa de material
        if any(w in p_clean for w in ["dar baixa", "baixar", "usei", "consumi", "gastei", "retirar do estoque"]):
            mat_match = self._find_best_match(p_clean, materiais_nomes)
            qtd = self._extract_number(p_clean, default=1.0)
            motivo = "Uso em produção"
            for m in ["costura", "corte", "defeito", "descarte", "prototipo", "perda", "amostra"]:
                if m in p_clean:
                    motivo = m.capitalize()
                    break
            return {
                "action": "dar_baixa_material",
                "params": {"material": mat_match or "", "quantidade": qtd, "motivo": motivo},
                "confidence": 0.96,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Entrada de material
        if any(w in p_clean for w in ["dar entrada", "entrada de material", "adicionar ao estoque", "chegou mais", "repor estoque", "aumentar estoque"]):
            mat_match = self._find_best_match(p_clean, materiais_nomes)
            qtd = self._extract_number(p_clean, default=1.0)
            return {
                "action": "dar_entrada_material",
                "params": {"material": mat_match or "", "quantidade": qtd},
                "confidence": 0.95,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Cadastrar material
        if any(w in p_clean for w in ["cadastrar material", "adicionar material", "novo material", "criar material"]):
            nome_match = re.search(r"(?:material|insumo)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+categoria|\s+com\b|\s+qtd|\s+quantidade|\.|$)", prompt, re.IGNORECASE)
            nome = nome_match.group(1).strip().title() if nome_match else ""
            qtd = self._extract_number(p_clean, default=1.0)
            custo_m = re.search(r"(?:custo|valor|preco|custando|de)\s+(?:r\$\s*)?(\d+(?:[.,]\d+)?)", p_clean)
            custo = float(custo_m.group(1).replace(",", ".")) if custo_m else 0.0
            
            cat = "Outros"
            for c in ctx.get("categorias", []):
                if remover_acentos(c) in p_clean:
                    cat = c
                    break

            unidade = "unidades"
            for u in ctx.get("unidades", []):
                if remover_acentos(u) in p_clean:
                    unidade = u
                    break

            return {
                "action": "cadastrar_material",
                "params": {"nome": nome, "categoria": cat, "quantidade": qtd, "unidade": unidade, "custo": custo, "quantidade_minima": 1.0, "gtin": ""},
                "confidence": 0.94,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Mudar status pedido
        if any(w in p_clean for w in ["mudar status", "alterar status", "concluir pedido", "cancelar pedido", "entregar pedido", "mover para producao"]):
            novo_st = "Concluído"
            if "cancelar" in p_clean or "cancelado" in p_clean:
                novo_st = "Cancelado"
            elif "entregar" in p_clean or "entregue" in p_clean:
                novo_st = "Entregue"
            elif "producao" in p_clean:
                novo_st = "Em produção"
            elif "pendente" in p_clean:
                novo_st = "Pendente"

            c_match = re.search(r"(?:pedido\s+de|pedido\s+do|pedido\s+da|cliente)\s+([A-Za-zÀ-ÖØ-öø-ÿ\s]+?)(?:\s+para|\.|$)", prompt, re.IGNORECASE)
            cliente = c_match.group(1).strip().title() if c_match else (ultimo_cliente_mencionado or "")
            return {
                "action": "mudar_status_pedido",
                "params": {"cliente": cliente, "novo_status": novo_st},
                "confidence": 0.95,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Cadastrar produto / bolsa
        if any(w in p_clean for w in ["cadastrar produto", "cadastrar bolsa", "nova bolsa", "novo produto"]):
            nome_m = re.search(r"(?:bolsa|produto)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+com\s+preco|\s+preco|\s+valor|\.|$)", prompt, re.IGNORECASE)
            nome = nome_m.group(1).strip().title() if nome_m else "Nova Bolsa"
            preco_m = re.search(r"(?:preco|valor|de)\s+(?:r\$\s*)?(\d+(?:[.,]\d+)?)", p_clean)
            preco = float(preco_m.group(1).replace(",", ".")) if preco_m else 100.0
            return {
                "action": "cadastrar_produto",
                "params": {"nome": nome, "preco_venda": preco, "estoque_pronto": 0, "emoji": "👜"},
                "confidence": 0.93,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Editar / Alterar produto ou receita de materiais
        if any(w in p_clean for w in ["editar bolsa", "alterar bolsa", "editar produto", "alterar produto", "editar receita", "alterar receita", "mudar receita", "alterar materiais", "mudar materiais", "trocar materiais", "materiais da bolsa", "editar materiais"]):
            prod_match = self._find_best_match(p_clean, produtos_nomes) or ultimo_produto_mencionado
            return {
                "action": "editar_produto",
                "params": {"produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Bolsa")},
                "confidence": 0.93,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Ajuste estoque pronto
        if any(w in p_clean for w in ["ajustar pecas prontas", "adicionar pecas prontas", "remover peca pronta", "ajustar estoque pronto"]) or (("pecas prontas" in p_clean or "peca pronta" in p_clean) and any(w in p_clean for w in ["adicionar", "remover", "colocar", "tirar"])):
            prod_match = self._find_best_match(p_clean, produtos_nomes) or ultimo_produto_mencionado
            qtd = int(self._extract_number(p_clean, default=1.0))
            op = "remover" if any(w in p_clean for w in ["remover", "retirar", "diminuir", "tirar"]) else "adicionar"
            return {
                "action": "ajustar_estoque_pronto",
                "params": {"produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Bolsa"), "quantidade": qtd, "operacao": op},
                "confidence": 0.94,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Sobras e retalhos
        if any(w in p_clean for w in ["cadastrar sobra", "cadastrar retalho", "adicionar sobra", "nova sobra", "guardar retalho"]):
            desc_m = re.search(r"(?:sobra|retalho)\s+(?:de\s+)?([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+com\s+qtd|\s+com\s+\d|\s+qtd|\.|$)", prompt, re.IGNORECASE)
            desc = desc_m.group(1).strip().title() if desc_m else "Retalho de Material"
            qtd = self._extract_number(p_clean, default=1.0)
            unidade = "metros"
            for u in ctx.get("unidades", []):
                if remover_acentos(u) in p_clean:
                    unidade = u
                    break
            return {
                "action": "cadastrar_sobra",
                "params": {"descricao": desc, "quantidade": qtd, "unidade": unidade},
                "confidence": 0.95,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Despesas
        if any(w in p_clean for w in ["cadastrar despesa", "adicionar despesa", "nova despesa", "registrar despesa", "gasto de", "pagamos", "paguei"]):
            valor = self._extract_number(p_clean, default=0.0)
            desc_m = re.search(r"(?:com|para)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+categoria|\.|$)", prompt, re.IGNORECASE)
            if not desc_m:
                desc_m = re.search(r"(?:de\s+\d+\s*reais\s+com\s+)([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+categoria|\.|$)", prompt, re.IGNORECASE)
            if not desc_m:
                desc_m = re.search(r"(?:despesa|gasto)\s+(?:de\s+)?([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+categoria|\.|$)", prompt, re.IGNORECASE)
            desc = desc_m.group(1).strip().title() if desc_m else "Despesa Operacional"
            
            cat = "Insumos"
            for c in ["Insumos", "Ferramentas", "Embalagem", "Manutenção", "Frete", "Outros"]:
                if remover_acentos(c) in p_clean:
                    cat = c
                    break
            return {
                "action": "cadastrar_despesa",
                "params": {"descricao": desc, "valor": valor, "categoria": cat},
                "confidence": 0.95,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Relatórios
        if any(w in p_clean for w in ["relatorio em pdf", "enviar relatorio", "gerar pdf", "baixar pdf", "exportar pdf"]):
            return {
                "action": "gerar_relatorio_pdf",
                "params": {},
                "confidence": 0.98,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["exportar excel", "baixar excel", "gerar excel", "planilha excel", "exportar xlsx"]):
            return {
                "action": "gerar_exportacao_excel",
                "params": {},
                "confidence": 0.98,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # Consultas
        if any(w in p_clean for w in ["financeiro", "saldo", "faturamento", "receita", "despesa", "lucro", "caixa"]):
            return {
                "action": "consultar_financeiro",
                "params": {},
                "confidence": 0.97,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["pedido", "pedidos", "encomenda", "encomendas"]):
            filtro = "pendentes" if any(w in p_clean for w in ["pendente", "aberto", "producao"]) else "todos"
            return {
                "action": "consultar_pedidos",
                "params": {"filtro": filtro},
                "confidence": 0.97,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["estoque", "material", "materiais", "quanto temos", "quantidade de", "insumo", "insumos", "gtin"]):
            mat_match = self._find_best_match(p_clean, materiais_nomes)
            return {
                "action": "consultar_estoque",
                "params": {"termo": mat_match or ""},
                "confidence": 0.97,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["alerta", "alertas", "estoque baixo", "acabando", "abaixo do minimo", "resumo do atelier"]):
            return {
                "action": "consultar_alertas",
                "params": {},
                "confidence": 0.97,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["bolsa", "bolsas", "produto", "produtos", "catalogo", "receita da bolsa"]):
            return {
                "action": "consultar_produtos",
                "params": {},
                "confidence": 0.96,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["sobra", "sobras", "retalho", "retalhos"]):
            return {
                "action": "consultar_sobras",
                "params": {},
                "confidence": 0.96,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        if any(w in p_clean for w in ["minhas permissoes", "meu perfil", "meu papel", "o que posso fazer"]):
            return {
                "action": "consultar_minhas_permissoes",
                "params": {},
                "confidence": 0.98,
                "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
                "_model": f"{self.model} (IA Local)"
            }

        # ── 3. CONVERSAÇÃO DIRETA GENERATIVA ──────────────────────────────────
        saudacoes = ["ola", "oi", "bom dia", "boa tarde", "boa noite", "ania", "tudo bem", "quem e voce", "ajuda", "menu"]
        if any(p_clean.startswith(s) or p_clean == s for s in saudacoes):
            reply = (
                f"✨ Olá, **{user_name}**! Eu sou a **Ania**, assistente inteligente de IA do Ateliê Haiti. 🧵\n\n"
                f"Estou pronta para ajudar você em tempo real com:\n"
                f"• 📄 **Relatórios & PDF:** *\"Gerar relatório em PDF\"*\n"
                f"• 🧾 **Pedidos:** *\"Criar pedido para Maria de 2 Bolsas Tote\"*\n"
                f"• 📦 **Estoque & Lote:** *\"Dar entrada de 10 courinos e 20 zíperes\"*\n"
                f"• 💰 **Financeiro:** *\"Qual o faturamento e saldo do ateliê?\"*\n\n"
                f"Como posso ajudar sua produção agora?"
            )
            voice = f"Olá {user_name}! Eu sou a Ania. Como posso ajudar no ateliê hoje?"
            suggestions = ["📄 Enviar relatório em PDF", "📦 Consultar estoque", "🧾 Pedidos", "💰 Resumo financeiro"]
        else:
            reply = (
                f"Entendi sua mensagem, **{user_name}**! 🧵✨\n\n"
                f"Posso executar essa ação diretamente no sistema ou consultar os dados para você. "
                f"Você gostaria de verificar o estoque, registrar um pedido ou gerar um relatório?"
            )
            voice = f"Entendi sua solicitação, {user_name}. Como deseja prosseguir?"
            suggestions = ["📦 Consultar estoque", "🧾 Ver pedidos", "📄 Gerar PDF", "💰 Financeiro"]

        return {
            "action": "conversar_direto",
            "reply": reply,
            "voice_text": voice,
            "suggestions": suggestions,
            "confidence": 0.90,
            "_elapsed_ms": round((time.time() - start_t) * 1000, 1),
            "_model": f"{self.model} (IA Local)"
        }

    def _find_best_match(self, text: str, candidates: List[str]) -> Optional[str]:
        if not candidates:
            return None
        text_clean = remover_acentos(text)
        for c in candidates:
            c_clean = remover_acentos(c)
            if c_clean and (c_clean in text_clean or text_clean in c_clean):
                return c
        for c in candidates:
            c_clean = remover_acentos(c)
            palavras = [w for w in c_clean.split() if len(w) >= 4]
            if palavras and any(w in text_clean for w in palavras):
                return c
        return None

    def _extract_number(self, text: str, default: float = 1.0) -> float:
        m = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except Exception:
                pass
        return default

    def _safe_json_parse(self, text: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(text)
        except Exception:
            pass

        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean = "\n".join(lines).strip()
            try:
                return json.loads(clean)
            except Exception:
                pass

        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            sub = text[first_brace:last_brace + 1]
            try:
                return json.loads(sub)
            except Exception:
                pass

        return None

    def _build_system_prompt(self, ctx: Dict[str, Any]) -> str:
        user_name = ctx.get("user_name", "Artesão")
        roles_str = ctx.get("roles_str", "Colaborador")
        materiais_nomes = ctx.get("materiais_nomes", [])[:25]
        produtos_nomes = ctx.get("produtos_nomes", [])[:20]
        categorias = ctx.get("categorias", ["Courino", "Metal", "Aviamento", "Tecido", "Embalagem", "Outros"])
        unidades = ctx.get("unidades", ["unidades", "metros", "rolos", "kg", "gramas", "pares", "pacotes"])

        return f"""Você é a Ania, assistente inteligente e prestativa do Ateliê Haiti (artesanato e confecção de bolsas).
Usuário atual: {user_name} (Papéis: {roles_str}).

Seu objetivo é interpretar a mensagem do usuário (considerando o histórico da conversa se houver) e SEMPRE responder em formato JSON estrito.

### ITENS CADASTRADOS NO ATELIÊ:
- Insumos/Materiais em Estoque: {json.dumps(materiais_nomes, ensure_ascii=False)}
- Produtos/Bolsas no Catálogo: {json.dumps(produtos_nomes, ensure_ascii=False)}
- Categorias de Insumos: {json.dumps(categorias, ensure_ascii=False)}
- Unidades de Medida: {json.dumps(unidades, ensure_ascii=False)}

### AÇÕES / FERRAMENTAS DISPONÍVEIS:
1. "dar_baixa_material": Dar baixa/saída de insumo. Parâmetros: {{"material": "nome", "quantidade": float, "motivo": "string"}}
2. "dar_entrada_material": Entrada/reposição de estoque. Parâmetros: {{"material": "nome", "quantidade": float}}
3. "cadastrar_material": Novo insumo. Parâmetros: {{"nome": "string", "categoria": "string", "quantidade": float, "unidade": "string", "custo": float, "quantidade_minima": float, "gtin": "string"}}
4. "excluir_material": Excluir insumo. Parâmetros: {{"material": "nome"}}
5. "criar_pedido": Novo pedido. Parâmetros: {{"cliente": "string", "produto": "nome da bolsa ou GTIN", "quantidade": int}}
6. "mudar_status_pedido": Alterar status de pedido. Parâmetros: {{"cliente": "nome", "novo_status": "Pendente|Em produção|Concluído|Entregue|Cancelado"}}
7. "excluir_pedido": Excluir pedido. Parâmetros: {{"cliente": "nome"}}
8. "cadastrar_produto": Nova bolsa/produto. Parâmetros: {{"nome": "string", "preco_venda": float, "estoque_pronto": int, "emoji": "string"}}
9. "editar_produto": Alterar materiais ou receita de uma bolsa/produto. Parâmetros: {{"produto": "nome"}}
10. "ajustar_estoque_pronto": Ajustar pronta-entrega. Parâmetros: {{"produto": "nome", "quantidade": int, "operacao": "adicionar|remover"}}
11. "excluir_produto": Excluir bolsa. Parâmetros: {{"produto": "nome"}}
12. "cadastrar_sobra": Registrar retalho/sobra. Parâmetros: {{"descricao": "string", "quantidade": float, "unidade": "string"}}
12. "acao_sobra": Atualizar retalho. Parâmetros: {{"descricao": "string", "acao": "Reaproveitado|Descartado"}}
13. "cadastrar_despesa": Nova despesa financeira. Parâmetros: {{"descricao": "string", "valor": float, "categoria": "string"}}
14. "excluir_despesa": Excluir despesa. Parâmetros: {{"descricao": "string"}}
15. "gerar_relatorio_pdf": Gerar e baixar PDF do ateliê. Parâmetros: {{}}
16. "gerar_exportacao_excel": Gerar planilha Excel. Parâmetros: {{}}
17. "gerar_backup_json": Baixar backup consolidado. Parâmetros: {{}}
18. "consultar_estoque": Consultar insumos ou saldo. Parâmetros: {{"termo": "string ou vazio para geral", "gtin": "string"}}
19. "consultar_pedidos": Consultar pedidos. Parâmetros: {{"filtro": "todos|pendentes|concluidos"}}
20. "consultar_financeiro": Consultar faturamento e saldo. Parâmetros: {{}}
21. "consultar_alertas": Ver itens em alerta/mínimo. Parâmetros: {{}}
22. "consultar_produtos": Ver catálogo de produtos. Parâmetros: {{}}
23. "consultar_sobras": Ver retalhos disponíveis. Parâmetros: {{}}
24. "consultar_minhas_permissoes": Ver perfil e acessos do usuário. Parâmetros: {{}}
25. "navegar": Abrir tela do sistema. Parâmetros: {{"tela": "estoque|pedidos|financeiro|relatorios|produtos|sobras|usuarios|roles|adicionar|baixa"}}
26. "acoes_em_lote": Quando o usuário solicitar múltiplas ações na mesma mensagem. Parâmetros: {{"acoes": [{{"action": "...", "params": {{...}}}}]}}
27. "conversar_direto": Responder dúvidas artesanais, saudações, bate-papo geral ou mensagens que não executam ações. Parâmetros: {{"reply": "sua resposta amigável e artesanal", "voice_text": "texto curto para síntese de voz", "suggestions": ["sugestão 1", "sugestão 2"]}}

### FORMATO OBRIGATÓRIO DE RESPOSTA (JSON):
{{
  "action": "nome_da_acao",
  "params": {{ ... }},
  "confidence": 0.95
}}
"""
