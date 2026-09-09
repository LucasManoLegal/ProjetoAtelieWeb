"""
ania_assistant.py - Motor Inteligente Completo da Assistente Virtual Ania
Ateliê Haiti - Sistema Híbrido com IA Local (Ollama) e Contingência Determinística
Execução de TODAS as ações do site por voz e digitação com validação rigorosa de RBAC.
"""

import re
import unicodedata
import uuid
import json
import sqlite3
from datetime import datetime
from typing import Dict, Any, Optional

from ania_ollama import OllamaEngine


def remover_acentos(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()


def formatar_moeda(val: float) -> str:
    try:
        val = float(val or 0)
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


class AniaAssistant:
    def __init__(self, app_context, ollama_engine: Optional[OllamaEngine] = None):
        self.app = app_context
        self.ollama = ollama_engine or OllamaEngine()
        try:
            from ania_agents import MultiAgentOrchestrator
            self.orchestrator = MultiAgentOrchestrator(assistant_context=self, ollama_engine=self.ollama)
        except Exception:
            self.orchestrator = None

    def _get_roles(self, user: dict) -> list:
        fn = self._get_helper('usuario_roles_lista')
        if fn:
            return fn(user)
        roles = user.get('roles') or user.get('role') or []
        if isinstance(roles, str):
            try:
                return json.loads(roles)
            except Exception:
                return [roles] if roles else []
        return list(roles) if isinstance(roles, (list, tuple)) else []

    def _get_helper(self, name: str, default_ret=None):
        if hasattr(self.app, name):
            return getattr(self.app, name)
        import app as app_mod
        return getattr(app_mod, name, default_ret)

    def _carregar_materiais(self):
        fn = self._get_helper('carregar_materiais')
        return fn() if fn else []

    def _carregar_produtos(self):
        fn = self._get_helper('carregar_produtos')
        return fn() if fn else []

    def _carregar_pedidos(self):
        fn = self._get_helper('carregar_pedidos')
        return fn() if fn else []

    def _carregar_sobras(self):
        fn = self._get_helper('carregar_sobras')
        return fn() if fn else []

    def _carregar_despesas(self):
        fn = self._get_helper('carregar_despesas')
        return fn() if fn else []

    def _carregar_usuarios(self):
        fn = self._get_helper('carregar_usuarios')
        return fn() if fn else []

    def _salvar_materiais(self, data):
        fn = self._get_helper('salvar_materiais')
        if fn: fn(data)

    def _salvar_produtos(self, data):
        fn = self._get_helper('salvar_produtos')
        if fn: fn(data)

    def _salvar_pedidos(self, data):
        fn = self._get_helper('salvar_pedidos')
        if fn: fn(data)

    def _salvar_json(self, name, data):
        fn = self._get_helper('salvar_json')
        if fn: fn(name, data)

    def _carregar_json(self, name):
        fn = self._get_helper('carregar_json')
        return fn(name) if fn else []

    def _init_db(self):
        fn = self._get_helper('init_db')
        if fn: fn()

    def _agora(self):
        fn = self._get_helper('agora')
        if fn: return fn()
        return datetime.now()

    @property
    def _use_sqlite(self):
        return self._get_helper('USE_SQLITE', True)

    @property
    def _db_path(self):
        return self._get_helper('DB_PATH', 'atelie.db')

    def _get_connection(self):
        fn = self._get_helper('get_db_connection')
        if fn:
            return fn()
        try:
            import db
            return db.get_db_connection()
        except Exception:
            return sqlite3.connect(self._db_path)

    def processar_mensagem(self, prompt: str, user: dict, history: Optional[list] = None, mode: Optional[str] = None) -> dict:
        """
        Processador central configurável:
        - mode="ia": Executa estritamente via IA (Ollama). Se falhar ou estiver offline, emite mensagem de erro explícita sem fallback silencioso.
        - mode="contingencia": Executa estritamente via Motor de Regras Locais (Contingência).
        - mode=None ou "auto": Se IA estiver online usa IA; se o Ollama estiver desabilitado, usa contingência.
        """
        if not prompt or not prompt.strip():
            return {
                "success": True,
                "reply": "Olá! Estou ouvindo. Como posso te ajudar hoje no ateliê?",
                "voice_text": "Olá! Estou ouvindo. Como posso te ajudar hoje no ateliê?",
                "suggestions": ["📄 Enviar relatório em PDF", "📦 Consultar estoque", "🧾 Pedidos pendentes", "💰 Resumo financeiro"],
                "engine": "regras_locais" if mode == "contingencia" else "ollama",
            }

        prompt_orig = prompt.strip()
        user_nome = user.get("nome") or user.get("username") or "Artesã(o)"
        roles = self._get_roles(user)
        roles_str = ", ".join(roles) if roles else "Colaborador"

        # ── 1. MODO CONTINGÊNCIA SELECIONADO OU OLLAMA DESABILITADO ──────────
        if mode == "contingencia" or (not mode and (not self.ollama or not self.ollama.enabled)):
            res_regras = self._processar_com_regras(prompt_orig, user)
            if "success" not in res_regras:
                res_regras["success"] = not bool(res_regras.get("denied"))
            res_regras["engine"] = "regras_locais"
            return res_regras

        # ── 2. MODO IA (OLLAMA / MODELO GENERATIVO) ──────────────────────────
        if not self.ollama or not self.ollama.enabled or not self.ollama.is_online():
            return {
                "success": False,
                "reply": "⚠️ **IA Offline**: Não foi possível conectar ao servidor da IA local (Ollama).\n\n"
                         "Verifique se o Ollama está em execução no computador ou selecione o **Modo Contingência** no topo para continuar.",
                "voice_text": "A inteligência artificial está offline no momento. Alterne para o modo de contingência.",
                "suggestions": ["Alternar para Contingência", "Consultar estoque", "Ver pedidos"],
                "engine": "ollama_offline"
            }

        try:
            materiais = self._carregar_materiais()
            produtos = self._carregar_produtos()
            system_ctx = {
                "user_name": user_nome,
                "roles_str": roles_str,
                "materiais": [{"id": m.get("id"), "nome": m.get("nome"), "gtin": m.get("gtin")} for m in materiais],
                "produtos": [{"id": p.get("id"), "nome": p.get("nome"), "gtin": p.get("gtin"), "preco_venda": p.get("preco_venda")} for p in produtos],
                "materiais_nomes": [m.get("nome") for m in materiais if m.get("nome")],
                "produtos_nomes": [p.get("nome") for p in produtos if p.get("nome")],
                "categorias": getattr(self.app, "CATEGORIAS", ["Courino", "Metal", "Aviamento", "Tecido", "Embalagem", "Outros"]),
                "unidades": getattr(self.app, "UNIDADES", ["unidades", "metros", "rolos", "kg", "gramas", "pares", "pacotes"]),
            }

            ollama_res = self.ollama.process_prompt(prompt_orig, system_ctx, history=history)
            if ollama_res and isinstance(ollama_res, dict):
                action = ollama_res.get("action")
                params = ollama_res.get("params") or {}

                despacho = self._despachar_acao_ollama(action, params, prompt_orig, user, ollama_res)
                if despacho:
                    if "success" not in despacho:
                        despacho["success"] = not bool(despacho.get("denied"))
                    despacho["engine"] = "ollama"
                    despacho["model"] = ollama_res.get("_model", self.ollama.model)
                    if "_elapsed_ms" in ollama_res:
                        despacho["elapsed_ms"] = ollama_res["_elapsed_ms"]
                    return despacho

            return {
                "success": False,
                "reply": "⚠️ **Erro no Processamento da IA**: O modelo de inteligência artificial não conseguiu compreender a solicitação.\n\n"
                         "Tente reformular sua frase ou selecione o **Modo Contingência** no topo.",
                "voice_text": "Não foi possível gerar a resposta com a inteligência artificial.",
                "suggestions": ["Alternar para Contingência", "Consultar estoque", "🧾 Pedidos"],
                "engine": "ollama_error"
            }
        except Exception as e:
            return {
                "success": False,
                "reply": f"⚠️ **Erro na IA**: Ocorreu uma falha durante a execução do modelo: `{str(e)}`.\n\n"
                         "Você pode alternar para o **Modo Contingência** para prosseguir.",
                "voice_text": "Ocorreu um erro no processamento da inteligência artificial.",
                "suggestions": ["Alternar para Contingência"],
                "engine": "ollama_error"
            }

    # ── DESPACHO DE TOOL CALLING / AÇÕES DO OLLAMA ───────────────────────────

    def _despachar_acao_ollama(self, action: str, params: dict, prompt_orig: str, user: dict, raw_res: dict) -> Optional[dict]:
        user_nome = user.get("nome") or user.get("username") or "Artesã(o)"
        roles = self._get_roles(user)
        roles_str = ", ".join(roles) if roles else "Colaborador"

        # Operações em lote (Multi-action / Batch)
        if action == "acoes_em_lote":
            lista_acoes = params.get("acoes") or raw_res.get("acoes") or []
            if lista_acoes:
                respostas_individuais = []
                sucessos = 0
                for item in lista_acoes:
                    sub_action = item.get("action")
                    sub_params = item.get("params") or {}
                    res_sub = self._despachar_acao_ollama(sub_action, sub_params, prompt_orig, user, item)
                    if res_sub:
                        respostas_individuais.append(res_sub)
                        if res_sub.get("success"):
                            sucessos += 1

                if respostas_individuais:
                    textos = [f"• {r.get('reply', '').splitlines()[0]}" for r in respostas_individuais if r.get('reply')]
                    msg = f"✅ **Operações em Lote Concluídas ({sucessos}/{len(lista_acoes)})** 📦\n\n" + "\n".join(textos)
                    voice = f"{sucessos} operações em lote foram executadas com sucesso."
                    return {
                        "success": sucessos > 0,
                        "reply": msg,
                        "voice_text": voice,
                        "suggestions": ["Consultar estoque", "Ver alertas", "📄 Gerar PDF"]
                    }

        # Conversação direta gerada pelo LLM
        if action == "conversar_direto":
            reply = raw_res.get("reply") or params.get("reply")
            if reply:
                voice_text = raw_res.get("voice_text") or params.get("voice_text") or reply
                suggestions = raw_res.get("suggestions") or params.get("suggestions") or ["📦 Consultar estoque", "🧾 Pedidos", "💰 Resumo financeiro"]
                return {
                    "success": True,
                    "reply": reply,
                    "voice_text": voice_text,
                    "suggestions": suggestions
                }

        # Baixa de material
        if action == "dar_baixa_material":
            if not self._tem_permissao(user, "baixa", "create"):
                return self._resposta_negada(user_nome, roles_str, "baixa", "create", "dar baixa de insumos no estoque")
            mat_nome = params.get("material") or ""
            qtd = float(params.get("quantidade") or 1.0)
            motivo = params.get("motivo") or "Uso em produção"
            return self._executar_baixa_direta(mat_nome, qtd, motivo, user, prompt_orig)

        # Entrada de material
        if action == "dar_entrada_material":
            if not self._tem_permissao(user, "estoque", "update"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "update", "dar entrada de estoque")
            mat_nome = params.get("material") or ""
            qtd = float(params.get("quantidade") or 1.0)
            return self._executar_entrada_direta(mat_nome, qtd, user, prompt_orig)

        # Cadastrar material
        if action == "cadastrar_material":
            if not self._tem_permissao(user, "adicionar", "create"):
                return self._resposta_negada(user_nome, roles_str, "adicionar", "create", "cadastrar novos materiais")
            return self._executar_cadastrar_material_direto(params, prompt_orig)

        # Excluir material
        if action == "excluir_material":
            if not self._tem_permissao(user, "estoque", "delete"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "delete", "excluir materiais do estoque")
            return self._executar_excluir_material_direto(params.get("material") or "", prompt_orig)

        # Criar pedido
        if action == "criar_pedido":
            if not self._tem_permissao(user, "pedidos", "create"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "create", "criar novos pedidos de clientes")
            cliente = params.get("cliente") or "Cliente Balcão"
            produto = params.get("produto") or ""
            qtd = int(params.get("quantidade") or 1)
            return self._executar_criar_pedido_direto(cliente, produto, qtd, user, prompt_orig)

        # Mudar status pedido
        if action == "mudar_status_pedido":
            if not self._tem_permissao(user, "pedidos", "update"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "update", "atualizar o status de pedidos")
            return self._executar_mudar_status_pedido_direto(params.get("cliente") or "", params.get("novo_status") or "", user, prompt_orig)

        # Excluir pedido
        if action == "excluir_pedido":
            if not self._tem_permissao(user, "pedidos", "delete"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "delete", "excluir pedidos")
            return self._executar_excluir_pedido_direto(params.get("cliente") or "", prompt_orig)

        # Cadastrar produto
        if action == "cadastrar_produto":
            if not self._tem_permissao(user, "produtos", "create"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "create", "cadastrar novas bolsas e produtos")
            return self._executar_cadastrar_produto_direto(params, prompt_orig)

        # Editar / Alterar produto ou receita
        if action in ("editar_produto", "alterar_receita", "editar_receita", "alterar_produto"):
            if not self._tem_permissao(user, "produtos", "update"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "update", "alterar receitas e materiais de bolsas")
            return self._executar_editar_produto(remover_acentos(params.get("produto") or prompt_orig), prompt_orig)

        # Ajuste estoque pronto
        if action == "ajustar_estoque_pronto":
            if not self._tem_permissao(user, "produtos", "update"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "update", "ajustar o estoque de peças prontas")
            return self._executar_ajuste_estoque_pronto_direto(params.get("produto") or "", int(params.get("quantidade") or 1), params.get("operacao") or "adicionar", user, prompt_orig)

        # Excluir produto
        if action == "excluir_produto":
            if not self._tem_permissao(user, "produtos", "delete"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "delete", "excluir produtos do catálogo")
            return self._executar_excluir_produto_direto(params.get("produto") or "", prompt_orig)

        # Sobras e retalhos
        if action == "cadastrar_sobra":
            if not self._tem_permissao(user, "sobras", "create"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "create", "cadastrar sobras e retalhos")
            return self._executar_cadastrar_sobra_direto(params, prompt_orig)

        if action == "acao_sobra":
            if not self._tem_permissao(user, "sobras", "update"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "update", "atualizar status de sobras")
            return self._executar_acao_sobra_direto(params.get("descricao") or "", params.get("acao") or "Reaproveitado", prompt_orig)

        if action == "excluir_sobra":
            if not self._tem_permissao(user, "sobras", "delete"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "delete", "excluir registros de sobras")
            return self._executar_excluir_sobra_direto(params.get("descricao") or "", prompt_orig)

        # Despesas
        if action == "cadastrar_despesa":
            if not self._tem_permissao(user, "financeiro", "create"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "create", "cadastrar despesas no financeiro")
            return self._executar_cadastrar_despesa_direto(params, prompt_orig)

        if action == "excluir_despesa":
            if not self._tem_permissao(user, "financeiro", "delete"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "delete", "excluir registros de despesas")
            return self._executar_excluir_despesa_direto(params.get("descricao") or "", prompt_orig)

        # Relatórios e exportações
        if action == "gerar_relatorio_pdf":
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "gerar e baixar relatórios em PDF")
            return self._gerar_relatorio_pdf()

        if action in ("gerar_exportacao_excel", "exportar_excel"):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "exportar planilhas Excel")
            return self._gerar_exportacao_excel()

        if action in ("gerar_backup_json", "gerar_backup"):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "fazer backup completo dos dados")
            return self._gerar_backup_json()

        # Precificação e custos
        if action == "calcular_precificacao":
            if not self._tem_permissao(user, "financeiro", "read"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "read", "calcular custos e precificação de produtos")
            return self._executar_calcular_precificacao(params.get("produto") or "", float(params.get("margem_desejada") or 50.0), prompt_orig)

        # Cálculos de estoque e capacidade produtiva
        if action == "calcular_capacidade_producao":
            if not self._tem_permissao(user, "estoque", "read"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "read", "consultar saldo e capacidade de produção")
            return self._executar_calcular_capacidade_producao(params.get("produto") or "", prompt_orig)

        if action == "calcular_necessidade_compras":
            if not self._tem_permissao(user, "estoque", "read"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "read", "calcular lista de compras para pedidos")
            return self._executar_calcular_necessidade_compras(params.get("status") or "pendentes", prompt_orig)

        if action == "calcular_consumo_producao":
            if not self._tem_permissao(user, "estoque", "read"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "read", "calcular consumo de materiais para produção")
            return self._executar_calcular_consumo_producao(params.get("produto") or "", int(params.get("quantidade") or 1), prompt_orig)

        # Consultoria técnica de artesanato e ateliê
        if action == "consultoria_tecnica":
            text_resp = raw_res.get("text_response") or params.get("text_response") or raw_res.get("reply")
            if text_resp:
                return {
                    "success": True,
                    "reply": text_resp,
                    "voice_text": "Aqui estão as orientações técnicas para o ateliê.",
                    "suggestions": ["🧵 Dicas de Costura", "📦 Consultar estoque", "🧾 Pedidos", "💰 Precificação"]
                }

        # Gestão de usuários / RBAC
        if action == "consultar_usuarios":
            if not self._tem_permissao(user, "usuarios", "read"):
                return self._resposta_negada(user_nome, roles_str, "usuarios", "read", "visualizar usuários do sistema")
            return self._consultar_usuarios()

        # Sobras e retalhos adicionais
        if action == "usar_sobra":
            if not self._tem_permissao(user, "sobras", "update"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "update", "atualizar status de sobras")
            return self._executar_acao_sobra_direto(params.get("descricao") or "", "Reaproveitado", prompt_orig)

        if action == "descartar_sobra":
            if not self._tem_permissao(user, "sobras", "update"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "update", "descartar sobras")
            return self._executar_acao_sobra_direto(params.get("descricao") or "", "Descartado", prompt_orig)

        # Consultas
        if action == "consultar_estoque":
            if not self._tem_permissao(user, "estoque", "read"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "read", "consultar materiais e quantidades em estoque")
            return self._consultar_estoque(remover_acentos(params.get("termo") or prompt_orig), prompt_orig)

        if action == "consultar_pedidos":
            if not self._tem_permissao(user, "pedidos", "read"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "read", "visualizar a lista de pedidos de clientes")
            return self._consultar_pedidos(remover_acentos(params.get("filtro") or prompt_orig))

        if action == "consultar_financeiro":
            if not self._tem_permissao(user, "financeiro", "read"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "read", "consultar dados financeiros e faturamento")
            return self._consultar_financeiro(remover_acentos(prompt_orig))

        if action == "consultar_alertas":
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "visualizar alertas e relatórios de desempenho")
            return self._consultar_alertas_e_resumo()

        if action == "consultar_produtos":
            if not self._tem_permissao(user, "produtos", "read"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "read", "consultar catálogo de produtos e receitas")
            return self._consultar_produtos(remover_acentos(prompt_orig))

        if action == "consultar_sobras":
            if not self._tem_permissao(user, "sobras", "read"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "read", "consultar sobras e retalhos")
            return self._consultar_sobras()

        if action == "consultar_minhas_permissoes":
            return self._responder_minhas_permissoes(user, user_nome, roles)

        if action == "navegar":
            tela = params.get("tela") or ""
            mapa = {
                "estoque": ("estoque", "/estoque", "Estoque"),
                "adicionar": ("adicionar", "/adicionar", "Adicionar Material"),
                "baixa": ("baixa", "/baixa", "Dar Baixa"),
                "produtos": ("produtos", "/produtos", "Produtos & Receitas"),
                "pedidos": ("pedidos", "/pedidos", "Pedidos dos Clientes"),
                "sobras": ("sobras", "/sobras", "Sobras & Reaproveitamento"),
                "financeiro": ("financeiro", "/financeiro", "Financeiro"),
                "relatorios": ("relatorios", "/alertas", "Alertas e Relatórios"),
                "usuarios": ("usuarios", "/usuarios", "Gestão de Usuários"),
                "roles": ("roles", "/roles", "Papéis & Permissões"),
            }
            if tela in mapa:
                recurso, url_dest, nome_tela = mapa[tela]
                if not self._tem_permissao(user, recurso, "read") and not self._tem_permissao(user, recurso, "create"):
                    return self._resposta_negada(user_nome, roles_str, recurso, "read", f"navegar até a página de {nome_tela}")
                return {
                    "reply": f"🧭 Abrindo a tela de **{nome_tela}** para você...",
                    "voice_text": f"Abrindo a tela de {nome_tela}.",
                    "action": {"type": "navigate", "url": url_dest},
                    "suggestions": ["Voltar ao início", "Consultar estoque", "Ver pedidos"],
                }

        return None


    # ── MÉTODOS PARAMETRIZADOS PARA EXECUÇÃO DIRETA ──────────────────────────

    def _executar_baixa_direta(self, mat_nome: str, qtd: float, motivo: str, user: dict, prompt_orig: str) -> dict:
        materiais = self._carregar_materiais()
        if not materiais:
            return {"reply": "Não há materiais no estoque para dar baixa.", "voice_text": "Estoque vazio."}

        material_alvo = None
        if mat_nome:
            mat_clean = remover_acentos(mat_nome)
            for m in materiais:
                n_clean = remover_acentos(m["nome"])
                if n_clean == mat_clean or mat_clean in n_clean:
                    material_alvo = m
                    break

        if not material_alvo:
            return self._executar_dar_baixa(remover_acentos(prompt_orig), prompt_orig, user)

        estoque_atual = float(material_alvo.get("quantidade") or 0)
        if estoque_atual < qtd:
            return {
                "reply": f"⚠️ **Estoque Insuficiente**: O material **{material_alvo['nome']}** possui apenas **{estoque_atual} {material_alvo['unidade']}** em estoque (você solicitou baixa de {qtd}).",
                "voice_text": f"Estoque insuficiente. Temos apenas {estoque_atual} {material_alvo['unidade']} de {material_alvo['nome']}.",
                "suggestions": ["Consultar estoque", "Ver alertas"]
            }

        novo_estoque = round(estoque_atual - qtd, 3)
        dt_agora = self._agora()
        dt_iso = dt_agora.isoformat()
        dt_str = dt_agora.strftime("%d/%m/%Y %H:%M")
        user_id = user.get("id") or user.get("username")

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE materiais SET quantidade=?, updated_at=? WHERE id=?", (novo_estoque, dt_iso, material_alvo["id"]))
            cur.execute(
                "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "saida", material_alvo["nome"], qtd, material_alvo["unidade"], f"{motivo} (Assistente Ania)", dt_str, user_id, dt_iso)
            )
            conn.commit()
            conn.close()
        else:
            material_alvo["quantidade"] = novo_estoque
            self._salvar_materiais(materiais)

        msg = (
            f"✅ **Baixa Realizada com Sucesso!** ✂️\n\n"
            f"• **Material**: {material_alvo.get('emoji','📦')} **{material_alvo['nome']}**\n"
            f"• **Quantidade Baixada**: -{qtd} {material_alvo['unidade']}\n"
            f"• **Novo Saldo em Estoque**: **{novo_estoque} {material_alvo['unidade']}**\n"
            f"• **Motivo Registrado**: *{motivo}*\n"
            f"• **Operador**: {user.get('nome') or user.get('username')}"
        )
        voice = f"Baixa de {qtd} {material_alvo['unidade']} de {material_alvo['nome']} registrada com sucesso. Novo saldo: {novo_estoque}."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Consultar estoque", "Ver alertas", "Ver pedidos"]}

    def _executar_entrada_direta(self, mat_nome: str, qtd: float, user: dict, prompt_orig: str) -> dict:
        materiais = self._carregar_materiais()
        if not materiais:
            return {"reply": "Não há materiais no estoque.", "voice_text": "Estoque vazio."}

        material_alvo = None
        if mat_nome:
            mat_clean = remover_acentos(mat_nome)
            for m in materiais:
                n_clean = remover_acentos(m["nome"])
                if n_clean == mat_clean or mat_clean in n_clean:
                    material_alvo = m
                    break

        if not material_alvo:
            return self._executar_dar_entrada_material(remover_acentos(prompt_orig), prompt_orig, user)

        estoque_atual = float(material_alvo.get("quantidade") or 0)
        novo_estoque = round(estoque_atual + qtd, 3)
        dt_agora = self._agora()
        dt_iso = dt_agora.isoformat()
        dt_str = dt_agora.strftime("%d/%m/%Y %H:%M")
        user_id = user.get("id") or user.get("username")

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE materiais SET quantidade=?, updated_at=? WHERE id=?", (novo_estoque, dt_iso, material_alvo["id"]))
            cur.execute(
                "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "entrada", material_alvo["nome"], qtd, material_alvo["unidade"], "Entrada manual via Ania", dt_str, user_id, dt_iso)
            )
            conn.commit()
            conn.close()
        else:
            material_alvo["quantidade"] = novo_estoque
            self._salvar_materiais(materiais)

        msg = (
            f"✅ **Entrada no Estoque Registrada!** 📦\n\n"
            f"• **Material**: {material_alvo.get('emoji','📦')} **{material_alvo['nome']}**\n"
            f"• **Adicionado**: +{qtd} {material_alvo['unidade']}\n"
            f"• **Novo Saldo**: **{novo_estoque} {material_alvo['unidade']}**\n"
            f"• **Registrado por**: {user.get('nome') or user.get('username')}"
        )
        voice = f"Entrada de {qtd} {material_alvo['unidade']} de {material_alvo['nome']} realizada. Novo saldo: {novo_estoque}."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Consultar estoque", "Ver alertas"]}

    def _executar_cadastrar_material_direto(self, params: dict, prompt_orig: str) -> dict:
        nome = (params.get("nome") or "").strip().title()
        categoria = params.get("categoria") or "Outros"
        unidade = params.get("unidade") or "unidades"
        qtd = float(params.get("quantidade") or 1.0)
        custo = float(params.get("custo") or 0.0)
        qtd_min = float(params.get("quantidade_minima") or 1.0)
        gtin = str(params.get("gtin") or "")

        emoji = getattr(self.app, "CATEGORIAS_EMOJI", {}).get(categoria, "📦")
        now = self._agora().isoformat()
        mat_id = str(uuid.uuid4())

        materiais = self._carregar_materiais()
        if any(m["nome"].strip().lower() == nome.lower() for m in materiais):
            return {
                "success": True,
                "reply": f"⚠️ Já existe um material chamado **{nome}** no estoque. Se deseja adicionar mais unidades, diga *\"Dar entrada de {qtd} em {nome}\"*.",
                "voice_text": f"O material {nome} já existe no estoque.",
                "suggestions": [f"Dar entrada em {nome}", "Consultar estoque"]
            }

        novo_mat = {
            "id": mat_id,
            "nome": nome,
            "categoria": categoria,
            "emoji": emoji,
            "quantidade": qtd,
            "unidade": unidade,
            "quantidade_minima": qtd_min,
            "custo": custo,
            "gtin": gtin,
            "foto": "",
        }

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO materiais (id,nome,categoria,emoji,quantidade,unidade,quantidade_minima,custo,gtin,foto,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (mat_id, nome, categoria, emoji, qtd, unidade, qtd_min, custo, gtin, "", now, now)
            )
            conn.commit()
            conn.close()
        else:
            materiais.append(novo_mat)
            self._salvar_materiais(materiais)

        msg = (
            f"✅ **Novo Material Cadastrado com Sucesso!** 📦\n\n"
            f"• **Material**: {emoji} **{nome}**\n"
            f"• **Categoria**: {categoria}\n"
            f"• **Estoque Inicial**: **{qtd} {unidade}**\n"
            f"• **Custo Unitário**: {formatar_moeda(custo)}\n"
            f"• **Estoque Mínimo**: {qtd_min} {unidade}\n"
            + (f"• **GTIN**: `{gtin}`\n" if gtin else "")
        )
        voice = f"Material {nome} cadastrado com sucesso com estoque inicial de {qtd} {unidade}."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver estoque", "Dar baixa", "Alertas"]}

    def _executar_excluir_material_direto(self, mat_nome: str, prompt_orig: str) -> dict:
        materiais = self._carregar_materiais()
        mat_clean = remover_acentos(mat_nome)
        material_alvo = next((m for m in materiais if remover_acentos(m["nome"]) == mat_clean or mat_clean in remover_acentos(m["nome"])), None)

        if not material_alvo:
            return self._executar_excluir_material(remover_acentos(prompt_orig), prompt_orig)

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM materiais WHERE id=?", (material_alvo["id"],))
            conn.commit()
            conn.close()
        else:
            materiais = [m for m in materiais if m["id"] != material_alvo["id"]]
            self._salvar_materiais(materiais)

        msg = f"🗑️ Material **{material_alvo['nome']}** excluído com sucesso do estoque."
        voice = f"Material {material_alvo['nome']} excluído com sucesso."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Consultar estoque", "Cadastrar material"]}

    def _executar_criar_pedido_direto(self, cliente: str, prod_nome: str, qtd: int, user: dict, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        if not produtos:
            return {"reply": "Não há bolsas ou produtos cadastrados para registrar pedidos.", "voice_text": "Não há produtos cadastrados."}

        produto_alvo = None
        # 1. Busca por GTIN no produto ou prompt
        gtin_m = re.search(r"\b(\d{8,14})\b", f"{prod_nome} {prompt_orig}")
        if gtin_m:
            gtin_num = gtin_m.group(1)
            produto_alvo = next((p for p in produtos if (p.get("gtin") or "").strip() == gtin_num), None)

        # 2. Busca por nome do produto
        if not produto_alvo and prod_nome:
            p_clean_target = remover_acentos(prod_nome)
            for p in produtos:
                n_clean = remover_acentos(p["nome"])
                if n_clean == p_clean_target or p_clean_target in n_clean:
                    produto_alvo = p
                    break

        if not produto_alvo:
            produto_alvo = produtos[0]

        estoque_pronto = int(produto_alvo.get("estoque_pronto") or 0)
        usar_pronta = estoque_pronto >= qtd
        preco_unit = float(produto_alvo.get("preco_venda") or 0)
        valor_total = round(preco_unit * qtd, 2)
        dt_pedido = self._agora()

        novo_pedido = {
            "id": str(uuid.uuid4()),
            "cliente": cliente,
            "produto_id": produto_alvo["id"],
            "produto_nome": produto_alvo["nome"],
            "produto_emoji": produto_alvo.get("emoji", "👜"),
            "quantidade": qtd,
            "preco_unitario": preco_unit,
            "valor_total": valor_total,
            "status": "Concluído" if usar_pronta else "Pendente",
            "materiais_baixados": 1 if usar_pronta else 0,
            "usou_estoque_pronto": 1 if usar_pronta else 0,
            "data_pedido": dt_pedido.strftime("%d/%m/%Y"),
            "data_pedido_iso": dt_pedido.strftime("%Y-%m-%d %H:%M:%S"),
            "observacoes": "Registrado via Assistente Virtual Ania",
        }

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            if usar_pronta:
                cur.execute("UPDATE produtos SET estoque_pronto=?, updated_at=? WHERE id=?", (estoque_pronto - qtd, dt_pedido.isoformat(), produto_alvo["id"]))
                cur.execute(
                    "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), "estoque_pronto", produto_alvo["nome"], qtd, "unidades", f"Atendimento de pedido de {cliente} (Ania)", dt_pedido.strftime("%d/%m/%Y %H:%M"), user.get("id"), dt_pedido.isoformat())
                )
            cur.execute(
                "INSERT INTO pedidos (id,cliente,produto_id,produto_nome,produto_emoji,quantidade,preco_unitario,valor_total,status,materiais_baixados,usou_estoque_pronto,data_pedido,data_pedido_iso,observacoes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (novo_pedido["id"], novo_pedido["cliente"], novo_pedido["produto_id"], novo_pedido["produto_nome"], novo_pedido["produto_emoji"], novo_pedido["quantidade"], novo_pedido["preco_unitario"], novo_pedido["valor_total"], novo_pedido["status"], novo_pedido["materiais_baixados"], novo_pedido["usou_estoque_pronto"], novo_pedido["data_pedido"], novo_pedido["data_pedido_iso"], novo_pedido["observacoes"], dt_pedido.isoformat(), dt_pedido.isoformat())
            )
            conn.commit()
            conn.close()
        else:
            if usar_pronta:
                produto_alvo["estoque_pronto"] = estoque_pronto - qtd
                self._salvar_produtos(produtos)
            lista_pedidos = self._carregar_json("pedidos.json")
            lista_pedidos.append(novo_pedido)
            self._salvar_json("pedidos.json", lista_pedidos)

        info_status = "🛍️ **Pronta-Entrega (Concluído Imediatamente)**" if usar_pronta else "⏳ **Pedido Pendente (Fabricação sob encomenda)**"
        msg = (
            f"✅ **Pedido Registrado com Sucesso!** 🧾\n\n"
            f"• **Cliente**: **{cliente}**\n"
            f"• **Produto**: {produto_alvo.get('emoji','👜')} **{qtd}x {produto_alvo['nome']}**\n"
            f"• **Valor Total**: **{formatar_moeda(valor_total)}**\n"
            f"• **Status**: {info_status}\n\n"
            f"O pedido já foi computado no sistema."
        )
        voice = f"Pedido de {qtd} {produto_alvo['nome']} para {cliente} registrado com sucesso no valor de {formatar_moeda(valor_total)}."
        return {
            "success": True,
            "reply": msg,
            "voice_text": voice,
            "suggestions": ["Ver pedidos pendentes", "Consultar estoque", "Resumo financeiro"]
        }

    def _executar_mudar_status_pedido_direto(self, cliente: str, novo_status: str, user: dict, prompt_orig: str) -> dict:
        pedidos = self._carregar_pedidos()
        if not pedidos:
            return {"reply": "Não há pedidos registrados para atualizar status.", "voice_text": "Não há pedidos registrados."}

        pedido_alvo = None
        if cliente:
            c_clean = remover_acentos(cliente)
            for p in pedidos:
                p_c = remover_acentos(p.get("cliente", ""))
                if p_c == c_clean or c_clean in p_c or p_c in c_clean:
                    pedido_alvo = p
                    break

        if not pedido_alvo:
            if len(pedidos) == 1:
                pedido_alvo = pedidos[0]
            else:
                return self._executar_mudar_status_pedido(remover_acentos(prompt_orig), prompt_orig, user)

        if not novo_status:
            novo_status = "Concluído"

        pedido_id = pedido_alvo["id"]
        now = self._agora()
        now_iso = now.isoformat()
        now_str = now.strftime("%d/%m/%Y %H:%M")
        user_id = user.get("id") or user.get("username")

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE pedidos SET status=?, updated_at=? WHERE id=?", (novo_status, now_iso, pedido_id))
            conn.commit()
            conn.close()
        else:
            pedido_alvo["status"] = novo_status
            self._salvar_pedidos(pedidos)

        msg = (
            f"✅ **Status do Pedido Atualizado com Sucesso!** 🧾\n\n"
            f"• **Cliente**: **{pedido_alvo['cliente']}**\n"
            f"• **Produto**: {pedido_alvo.get('produto_emoji','👜')} {pedido_alvo['produto_nome']}\n"
            f"• **Novo Status**: `{novo_status}`\n"
            f"• **Atualizado por**: {user.get('nome') or user.get('username')}"
        )
        voice = f"Status do pedido de {pedido_alvo['cliente']} alterado para {novo_status} com sucesso."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver pedidos", "Consultar financeiro"]}

    def _executar_excluir_pedido_direto(self, cliente: str, prompt_orig: str) -> dict:
        pedidos = self._carregar_pedidos()
        c_clean = remover_acentos(cliente)
        pedido_alvo = next((p for p in pedidos if c_clean == remover_acentos(p.get("cliente", "")) or c_clean in remover_acentos(p.get("cliente", ""))), None)

        if not pedido_alvo:
            return self._executar_excluir_pedido(remover_acentos(prompt_orig), prompt_orig)

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM pedidos WHERE id=?", (pedido_alvo["id"],))
            conn.commit()
            conn.close()
        else:
            pedidos = [p for p in pedidos if p["id"] != pedido_alvo["id"]]
            self._salvar_pedidos(pedidos)

        msg = f"🗑️ **Pedido de {pedido_alvo['cliente']}** ({pedido_alvo['produto_nome']}) foi **removido** com sucesso do sistema."
        voice = f"Pedido de {pedido_alvo['cliente']} removido com sucesso."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver pedidos", "Criar novo pedido"]}

    def _executar_cadastrar_produto_direto(self, params: dict, prompt_orig: str) -> dict:
        nome = (params.get("nome") or "").strip().title()
        preco = float(params.get("preco_venda") or 0.0)
        est_pronto = int(params.get("estoque_pronto") or 0)
        emoji = params.get("emoji") or "👜"

        novo_prod = {
            "id": str(uuid.uuid4()),
            "nome": nome,
            "emoji": emoji,
            "preco_venda": preco,
            "receita": [],
            "gtin": "",
            "estoque_pronto": est_pronto,
        }

        produtos = self._carregar_produtos()
        existente = next((p for p in produtos if p["nome"].strip().lower() == nome.lower()), None)
        if existente:
            existente["preco_venda"] = preco
            self._salvar_produtos(produtos)
        else:
            produtos.append(novo_prod)
            self._salvar_produtos(produtos)

        msg = (
            f"✅ **Bolsa Cadastrada com Sucesso!** 👜\n\n"
            f"• **Modelo**: {emoji} **{nome}**\n"
            f"• **Preço de Venda**: **{formatar_moeda(preco)}**\n"
            f"• **Estoque Inicial de Peças Prontas**: {est_pronto} unidade(s)\n\n"
            f"Você já pode registrar pedidos deste produto ou adicionar sua receita técnica."
        )
        voice = f"Produto {nome} cadastrado com sucesso com preço de {formatar_moeda(preco)}."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Criar pedido desta bolsa", "Ver produtos"]}

    def _executar_ajuste_estoque_pronto_direto(self, prod_nome: str, qtd: int, acao: str, user: dict, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        if not produtos:
            return {"reply": "Não há produtos cadastrados.", "voice_text": "Não há produtos."}

        produto_alvo = None
        if prod_nome:
            p_clean = remover_acentos(prod_nome)
            for p in produtos:
                if p_clean in remover_acentos(p["nome"]):
                    produto_alvo = p
                    break

        if not produto_alvo:
            return self._executar_ajuste_estoque_pronto(remover_acentos(prompt_orig), prompt_orig, user)

        is_remover = acao in ("remover", "subtrair", "diminuir")
        est_atual = int(produto_alvo.get("estoque_pronto") or 0)
        novo_est = max(0, est_atual - qtd) if is_remover else (est_atual + qtd)
        dt_agora = self._agora()
        dt_iso = dt_agora.isoformat()
        dt_str = dt_agora.strftime("%d/%m/%Y %H:%M")
        user_id = user.get("id") or user.get("username")
        motivo = f"Ajuste de estoque pronto via Ania ({'remover' if is_remover else 'adicionar'} {qtd})"

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE produtos SET estoque_pronto=?, updated_at=? WHERE id=?", (novo_est, dt_iso, produto_alvo["id"]))
            cur.execute(
                "INSERT INTO movimentacoes (id, tipo, material_nome, quantidade, unidade, motivo, data, usuario, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "estoque_pronto", produto_alvo["nome"], qtd if not is_remover else -qtd, "unidades", motivo, dt_str, user_id, dt_iso)
            )
            conn.commit()
            conn.close()
        else:
            produto_alvo["estoque_pronto"] = novo_est
            self._salvar_produtos(produtos)

        msg = (
            f"✅ **Estoque de Peças Prontas Atualizado!** 🛍️\n\n"
            f"• **Produto**: {produto_alvo.get('emoji','👜')} **{produto_alvo['nome']}**\n"
            f"• **Operação**: {'Adicionadas +' if not is_remover else 'Removidas -'}{qtd} peça(s)\n"
            f"• **Novo Estoque de Pronta-Entrega**: **{novo_est} pronta(s)**"
        )
        voice = f"Estoque pronto de {produto_alvo['nome']} atualizado para {novo_est} peças."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver produtos", "Criar pedido"]}

    def _executar_excluir_produto_direto(self, prod_nome: str, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        p_clean = remover_acentos(prod_nome)
        produto_alvo = next((p for p in produtos if p_clean in remover_acentos(p["nome"])), None)

        if not produto_alvo:
            return self._executar_excluir_produto(remover_acentos(prompt_orig), prompt_orig)

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM produtos WHERE id=?", (produto_alvo["id"],))
            conn.commit()
            conn.close()
        else:
            produtos = [p for p in produtos if p["id"] != produto_alvo["id"]]
            self._salvar_produtos(produtos)

        msg = f"🗑️ Produto **{produto_alvo['nome']}** removido do catálogo com sucesso."
        voice = f"Produto {produto_alvo['nome']} removido com sucesso."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver produtos", "Criar produto"]}

    def _executar_cadastrar_sobra_direto(self, params: dict, prompt_orig: str) -> dict:
        desc = (params.get("descricao") or "Retalho de Material").strip().title()
        qtd = float(params.get("quantidade") or 1.0)
        unidade = params.get("unidade") or "metros"

        nova_sobra = {
            "id": str(uuid.uuid4()),
            "material_id": "",
            "descricao": desc,
            "quantidade": qtd,
            "unidade": unidade,
            "data": self._agora().strftime("%d/%m/%Y"),
            "status": "Disponível",
        }

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sobras (id,material_id,descricao,quantidade,unidade,data,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (nova_sobra["id"], "", desc, qtd, unidade, nova_sobra["data"], "Disponível", self._agora().isoformat(), self._agora().isoformat())
            )
            conn.commit()
            conn.close()
        else:
            sobras = self._carregar_sobras()
            sobras.append(nova_sobra)
            self._salvar_json("sobras.json", sobras)

        msg = (
            f"✅ **Sobra/Retalho Registrado para Reaproveitamento!** ♻️\n\n"
            f"• **Descrição**: **{desc}**\n"
            f"• **Quantidade**: {qtd} {unidade}\n"
            f"• **Status**: `Disponível`"
        )
        voice = f"Sobra de {desc} registrada com sucesso."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver sobras", "Consultar estoque"]}

    def _executar_acao_sobra_direto(self, desc: str, acao: str, prompt_orig: str) -> dict:
        sobras = self._carregar_sobras()
        d_clean = remover_acentos(desc)
        sobra_alvo = next((s for s in sobras if d_clean in remover_acentos(s.get("descricao", ""))), None)

        if not sobra_alvo:
            return self._executar_acao_sobra(remover_acentos(prompt_orig), prompt_orig)

        novo_status = "Descartado" if "descart" in acao.lower() else "Reaproveitado"
        now_iso = self._agora().isoformat()

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE sobras SET status=?, updated_at=? WHERE id=?", (novo_status, now_iso, sobra_alvo["id"]))
            conn.commit()
            conn.close()
        else:
            sobra_alvo["status"] = novo_status
            self._salvar_json("sobras.json", sobras)

        msg = f"♻️ A sobra **{sobra_alvo['descricao']}** foi marcada como **{novo_status}**."
        voice = f"Sobra marcada como {novo_status}."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver sobras", "Consultar estoque"]}

    def _executar_excluir_sobra_direto(self, desc: str, prompt_orig: str) -> dict:
        sobras = self._carregar_sobras()
        d_clean = remover_acentos(desc)
        sobra_alvo = next((s for s in sobras if d_clean in remover_acentos(s.get("descricao", ""))), None)

        if not sobra_alvo:
            return self._executar_excluir_sobra(remover_acentos(prompt_orig), prompt_orig)

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM sobras WHERE id=?", (sobra_alvo["id"],))
            conn.commit()
            conn.close()
        else:
            sobras = [s for s in sobras if s["id"] != sobra_alvo["id"]]
            self._salvar_json("sobras.json", sobras)

        msg = f"🗑️ Sobra **{sobra_alvo['descricao']}** excluída."
        voice = f"Sobra excluída."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Ver sobras"]}

    def _executar_cadastrar_despesa_direto(self, params: dict, prompt_orig: str) -> dict:
        valor = float(params.get("valor") or 0.0)
        desc = (params.get("descricao") or "Despesa Operacional").strip().title()
        categoria = params.get("categoria") or "Insumos"

        nova_desp = {
            "id": str(uuid.uuid4()),
            "descricao": desc,
            "valor": valor,
            "categoria": categoria,
            "data": self._agora().strftime("%d/%m/%Y"),
            "created_at": self._agora().isoformat()
        }

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO despesas (id,descricao,valor,categoria,data,created_at) VALUES (?,?,?,?,?,?)",
                (nova_desp["id"], desc, valor, categoria, nova_desp["data"], nova_desp["created_at"])
            )
            conn.commit()
            conn.close()
        else:
            despesas = self._carregar_despesas()
            despesas.append(nova_desp)
            self._salvar_json("despesas.json", despesas)

        msg = (
            f"✅ **Despesa Registrada no Financeiro!** 💰\n\n"
            f"• **Descrição**: **{desc}**\n"
            f"• **Valor**: **{formatar_moeda(valor)}**\n"
            f"• **Categoria**: {categoria}\n"
            f"• **Data**: {nova_desp['data']}"
        )
        voice = f"Despesa de {formatar_moeda(valor)} com {desc} registrada com sucesso."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Resumo financeiro", "📄 Gerar PDF"]}

    def _executar_excluir_despesa_direto(self, desc: str, prompt_orig: str) -> dict:
        despesas = self._carregar_despesas()
        d_clean = remover_acentos(desc)
        desp_alvo = next((d for d in despesas if d_clean in remover_acentos(d.get("descricao", ""))), None)

        if not desp_alvo:
            return self._executar_excluir_despesa(remover_acentos(prompt_orig), prompt_orig)

        if self._use_sqlite:
            self._init_db()
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM despesas WHERE id=?", (desp_alvo["id"],))
            conn.commit()
            conn.close()
        else:
            despesas = [d for d in despesas if d["id"] != desp_alvo["id"]]
            self._salvar_json("despesas.json", despesas)

        msg = f"🗑️ Despesa **{desp_alvo['descricao']}** ({formatar_moeda(desp_alvo['valor'])}) foi excluída."
        voice = f"Despesa de {desp_alvo['descricao']} excluída."
        return {"success": True, "reply": msg, "voice_text": voice, "suggestions": ["Resumo financeiro"]}

    def _executar_calcular_precificacao(self, prod_nome: str, margem_desejada: float, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        materiais = self._carregar_materiais()
        mat_map = {str(m.get("id")): m for m in materiais if m.get("id")}
        mat_map_name = {remover_acentos(m.get("nome", "")): m for m in materiais if m.get("nome")}

        prod = None
        p_clean = remover_acentos(prod_nome)
        if p_clean:
            for p in produtos:
                if p_clean in remover_acentos(p.get("nome", "")):
                    prod = p
                    break
        if not prod and produtos:
            prod = produtos[0]

        if not prod:
            return {
                "success": False,
                "reply": "⚠️ Nenhum produto cadastrado encontrado no catálogo para cálculo de precificação.",
                "voice_text": "Nenhum produto cadastrado encontrado.",
                "suggestions": ["Cadastrar produto", "Ver produtos"]
            }

        receita = prod.get("receita") or []
        if isinstance(receita, str):
            try:
                receita = json.loads(receita)
            except Exception:
                receita = []

        custo_materiais = 0.0
        linhas_detalhes = []
        for item in receita:
            m_id = str(item.get("material_id") or "")
            m_nome = item.get("material_nome") or item.get("nome") or "Insumo"
            qtd = float(item.get("quantidade") or item.get("qtd") or 0)
            mat = mat_map.get(m_id) or mat_map_name.get(remover_acentos(m_nome))
            preco_unit = float(mat.get("custo") or mat.get("preco_unitario") or 0) if mat else 0.0
            custo_item = qtd * preco_unit
            custo_materiais += custo_item
            if qtd > 0:
                linhas_detalhes.append(f"• {m_nome}: {qtd:g} un/m x {formatar_moeda(preco_unit)} = {formatar_moeda(custo_item)}")

        preco_venda_atual = float(prod.get("preco_venda") or 0)
        margem_fator = 1.0 + (margem_desejada / 100.0)
        preco_sugerido = custo_materiais * margem_fator if custo_materiais > 0 else (preco_venda_atual if preco_venda_atual > 0 else 50.0)
        lucro_unitario = (preco_venda_atual if preco_venda_atual > 0 else preco_sugerido) - custo_materiais

        msg = (
            f"💰 **Análise de Precificação e Custos: {prod.get('emoji', '👜')} {prod.get('nome')}**\n\n"
            f"• **Custo Total de Matéria-Prima**: `{formatar_moeda(custo_materiais)}`\n"
        )
        if linhas_detalhes:
            msg += "\n**Composição da Receita:**\n" + "\n".join(linhas_detalhes[:6]) + "\n\n"
        msg += (
            f"• **Preço de Venda Atual**: `{formatar_moeda(preco_venda_atual)}`\n"
            f"• **Margem Desejada**: `{margem_desejada:.0f}%`\n"
            f"• **Preço Sugerido com Margem**: `{formatar_moeda(preco_sugerido)}`\n"
            f"• **Lucro Estimado por Peça**: `{formatar_moeda(lucro_unitario)}`"
        )
        return {
            "success": True,
            "reply": msg,
            "voice_text": f"O custo de matéria-prima para {prod.get('nome')} é de {formatar_moeda(custo_materiais)} e o lucro estimado é de {formatar_moeda(lucro_unitario)}.",
            "suggestions": ["📊 Ver Financeiro", "🧾 Pedidos", "📦 Ver Estoque"]
        }

    def _executar_calcular_capacidade_producao(self, prod_nome: str, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        materiais = self._carregar_materiais()
        mat_map = {str(m.get("id")): m for m in materiais if m.get("id")}
        mat_map_name = {remover_acentos(m.get("nome", "")): m for m in materiais if m.get("nome")}

        prod = None
        p_clean = remover_acentos(prod_nome)
        if p_clean:
            for p in produtos:
                if p_clean in remover_acentos(p.get("nome", "")):
                    prod = p
                    break
        if not prod and produtos:
            prod = produtos[0]

        if not prod:
            return {
                "success": False,
                "reply": "⚠️ Nenhum produto cadastrado no catálogo para simular capacidade de produção.",
                "voice_text": "Nenhum produto cadastrado encontrado.",
                "suggestions": ["Cadastrar produto", "Ver estoque"]
            }

        receita = prod.get("receita") or []
        if isinstance(receita, str):
            try:
                receita = json.loads(receita)
            except Exception:
                receita = []

        if not receita:
            return {
                "success": True,
                "reply": f"⚠️ O produto **{prod.get('nome')}** ainda não possui uma receita de materiais vinculada. Acesse a edição do produto para adicionar a ficha técnica!",
                "voice_text": f"O produto {prod.get('nome')} não possui receita cadastrada.",
                "suggestions": [f"Editar {prod.get('nome')}", "Ver produtos"]
            }

        capacidade_maxima = None
        gargalo = None
        linhas_analise = []

        for item in receita:
            m_id = str(item.get("material_id") or "")
            m_nome = item.get("material_nome") or item.get("nome") or "Material"
            qtd_nec = float(item.get("quantidade") or item.get("qtd") or 0)
            if qtd_nec <= 0:
                continue

            mat = mat_map.get(m_id) or mat_map_name.get(remover_acentos(m_nome))
            saldo_atual = float(mat.get("quantidade") or 0) if mat else 0.0
            unidade = mat.get("unidade", "un") if mat else "un"

            possivel = int(saldo_atual // qtd_nec)
            if capacidade_maxima is None or possivel < capacidade_maxima:
                capacidade_maxima = possivel
                gargalo = m_nome

            linhas_analise.append(f"• **{m_nome}**: Estoque `{saldo_atual:g} {unidade}` (Gasta `{qtd_nec:g} {unidade}/un`) → Dá para **{possivel}** peça(s)")

        capacidade_final = capacidade_maxima if capacidade_maxima is not None else 0
        emoji = prod.get("emoji", "👜")
        nome_p = prod.get("nome", "Bolsa")

        msg = (
            f"🧮 **Simulação de Capacidade Produtiva**: {emoji} **{nome_p}**\n\n"
            f"Com o saldo atual de matérias-primas no estoque, você consegue produzir no máximo **{capacidade_final} unidade(s)**.\n\n"
        )
        if gargalo and capacidade_final >= 0:
            msg += f"⚠️ **Material Limitante (Gargalo)**: **{gargalo}**\n\n"
        if linhas_analise:
            msg += "**Detalhamento por Insumo:**\n" + "\n".join(linhas_analise) + "\n\n"
        msg += f"💡 *Dica*: Reponha o insumo **{gargalo}** para aumentar a sua capacidade de produção!"

        return {
            "success": True,
            "reply": msg,
            "voice_text": f"Você consegue produzir no máximo {capacidade_final} unidades de {nome_p}. O material limitante é {gargalo}.",
            "suggestions": ["📦 Consultar estoque", "💰 Precificação", "🧾 Criar pedido"]
        }

    def _executar_calcular_necessidade_compras(self, filtro: str, prompt_orig: str) -> dict:
        pedidos = self._carregar_pedidos()
        produtos = self._carregar_produtos()
        materiais = self._carregar_materiais()

        prod_map = {str(p.get("id")): p for p in produtos if p.get("id")}
        prod_map_name = {remover_acentos(p.get("nome", "")): p for p in produtos if p.get("nome")}
        mat_map = {str(m.get("id")): m for m in materiais if m.get("id")}
        mat_map_name = {remover_acentos(m.get("nome", "")): m for m in materiais if m.get("nome")}

        pedidos_alvo = [p for p in pedidos if (p.get("status") or "").lower() in ("pendente", "em produção", "em producao")]

        if not pedidos_alvo:
            return {
                "success": True,
                "reply": "🎉 **Tudo em dia!** Não há pedidos pendentes ou em produção no momento. Seu estoque atual atende a todas as encomendas!",
                "voice_text": "Não há pedidos pendentes no momento.",
                "suggestions": ["📦 Consultar estoque", "🧾 Novo pedido", "📊 Ver alertas"]
            }

        demanda_materiais = {}
        for ped in pedidos_alvo:
            p_id = str(ped.get("produto_id") or "")
            p_nome = ped.get("produto_nome") or ""
            qtd_ped = int(ped.get("quantidade") or 1)
            prod = prod_map.get(p_id) or prod_map_name.get(remover_acentos(p_nome))
            if not prod:
                continue

            receita = prod.get("receita") or []
            if isinstance(receita, str):
                try: receita = json.loads(receita)
                except Exception: receita = []

            for item in receita:
                m_id = str(item.get("material_id") or "")
                m_nome = item.get("material_nome") or item.get("nome") or "Material"
                qtd_unit = float(item.get("quantidade") or item.get("qtd") or 0)
                mat = mat_map.get(m_id) or mat_map_name.get(remover_acentos(m_nome))
                custo_u = float(mat.get("custo") or mat.get("preco_unitario") or 0) if mat else 0.0
                unidade = mat.get("unidade", "un") if mat else "un"

                key = m_id if m_id else remover_acentos(m_nome)
                if key not in demanda_materiais:
                    demanda_materiais[key] = {
                        "id": m_id,
                        "nome": m_nome,
                        "necessario": 0.0,
                        "unidade": unidade,
                        "custo_unit": custo_u,
                        "saldo_atual": float(mat.get("quantidade") or 0) if mat else 0.0
                    }
                demanda_materiais[key]["necessario"] += (qtd_unit * qtd_ped)

        faltas = []
        custo_total_compras = 0.0

        for key, info in demanda_materiais.items():
            saldo = info["saldo_atual"]
            nec = info["necessario"]
            if saldo < nec:
                falta_qtd = nec - saldo
                custo_est = falta_qtd * info["custo_unit"]
                custo_total_compras += custo_est
                faltas.append(f"• 🛒 **{info['nome']}**: Falta `{falta_qtd:g} {info['unidade']}` (Estoque: {saldo:g} | Necessário: {nec:g}) — Est: `{formatar_moeda(custo_est)}`")

        if not faltas:
            return {
                "success": True,
                "reply": f"✅ **Estoque Suficiente!** O estoque atual possui todos os materiais necessários para concluir os **{len(pedidos_alvo)} pedidos ativos**.",
                "voice_text": f"Seu estoque atual é suficiente para atender aos {len(pedidos_alvo)} pedidos ativos.",
                "suggestions": ["📦 Consultar estoque", "🧾 Ver pedidos", "📊 Relatório em PDF"]
            }

        msg = (
            f"📋 **Lista de Compras Inteligente ({len(pedidos_alvo)} Pedidos Ativos)**:\n\n"
            f"Para finalizar as encomendas pendentes sem interrupção na produção, você precisa comprar:\n\n"
            + "\n".join(faltas) + "\n\n"
            f"💰 **Investimento Estimado em Compras**: `{formatar_moeda(custo_total_compras)}`"
        )
        return {
            "success": True,
            "reply": msg,
            "voice_text": f"Você precisa comprar {len(faltas)} insumos para concluir os pedidos. Custo estimado de {formatar_moeda(custo_total_compras)}.",
            "suggestions": ["➕ Dar entrada", "📦 Consultar estoque", "🧾 Ver pedidos"]
        }

    def _executar_calcular_consumo_producao(self, prod_nome: str, qtd_planejada: int, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        materiais = self._carregar_materiais()
        mat_map = {str(m.get("id")): m for m in materiais if m.get("id")}
        mat_map_name = {remover_acentos(m.get("nome", "")): m for m in materiais if m.get("nome")}

        prod = None
        p_clean = remover_acentos(prod_nome)
        if p_clean:
            for p in produtos:
                if p_clean in remover_acentos(p.get("nome", "")):
                    prod = p
                    break
        if not prod and produtos:
            prod = produtos[0]

        if not prod:
            return {
                "success": False,
                "reply": "⚠️ Produto não encontrado para cálculo de consumo.",
                "voice_text": "Produto não encontrado.",
                "suggestions": ["Ver produtos", "Consultar estoque"]
            }

        receita = prod.get("receita") or []
        if isinstance(receita, str):
            try: receita = json.loads(receita)
            except Exception: receita = []

        if not receita:
            return {
                "success": True,
                "reply": f"⚠️ O produto **{prod.get('nome')}** não possui receita cadastrada.",
                "voice_text": f"O produto {prod.get('nome')} não possui receita cadastrada.",
                "suggestions": [f"Editar {prod.get('nome')}", "Ver produtos"]
            }

        linhas = []
        custo_total = 0.0
        for item in receita:
            m_id = str(item.get("material_id") or "")
            m_nome = item.get("material_nome") or item.get("nome") or "Material"
            qtd_u = float(item.get("quantidade") or item.get("qtd") or 0)
            mat = mat_map.get(m_id) or mat_map_name.get(remover_acentos(m_nome))
            unidade = mat.get("unidade", "un") if mat else "un"
            saldo = float(mat.get("quantidade") or 0) if mat else 0.0
            custo_u = float(mat.get("custo") or mat.get("preco_unitario") or 0) if mat else 0.0

            consumo = qtd_u * qtd_planejada
            custo_item = consumo * custo_u
            custo_total += custo_item
            status_estoque = "✅ Suficiente" if saldo >= consumo else f"⚠️ Falta {consumo - saldo:g} {unidade}"
            linhas.append(f"• **{m_nome}**: `{consumo:g} {unidade}` ({status_estoque}) — Custo: `{formatar_moeda(custo_item)}`")

        msg = (
            f"📐 **Cálculo de Consumo para {qtd_planejada}x {prod.get('emoji', '👜')} {prod.get('nome')}**:\n\n"
            + "\n".join(linhas) + "\n\n"
            f"💰 **Custo Total Previsto de Materiais**: `{formatar_moeda(custo_total)}`"
        )
        return {
            "success": True,
            "reply": msg,
            "voice_text": f"Para produzir {qtd_planejada} peças de {prod.get('nome')}, o custo previsto de matéria-prima é de {formatar_moeda(custo_total)}.",
            "suggestions": ["📦 Consultar estoque", "💰 Precificação", "🧾 Criar pedido"]
        }

    # ── 3. MOTOR DETERMINÍSTICO BASEADO EM REGRAS (CONTINGÊNCIA) ─────────────


    def _processar_com_regras(self, prompt_orig: str, user: dict) -> dict:
        p_clean = remover_acentos(prompt_orig)
        user_nome = user.get("nome") or user.get("username") or "Artesã(o)"
        roles = self._get_roles(user)
        roles_str = ", ".join(roles) if roles else "Colaborador"

        # ── 1. SAUDAÇÃO & MENU DE AJUDA ────────────────────────────────────────
        if any(w in p_clean for w in ["quem e voce", "o que voce faz", "ajuda", "comandos", "menu", "o que posso pedir"]) or p_clean in ["ola", "oi", "bom dia", "boa tarde", "boa noite", "ania"]:
            return self._responder_ajuda(user_nome, roles)

        # ── 2. CONSULTAR PERMISSÕES / MEU PERFIL ──────────────────────────────
        if any(w in p_clean for w in ["minhas permissoes", "meu perfil", "meu papel", "o que posso fazer", "meus acessos", "minha conta"]):
            return self._responder_minhas_permissoes(user, user_nome, roles)

        # ── 3. RELATÓRIOS & EXPORTAÇÕES (PDF, EXCEL, JSON, E-MAIL) ───────────
        if any(w in p_clean for w in ["enviar relatorio por email", "mandar relatorio por email", "disparar relatorio por email", "relatorio por email"]):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "enviar relatórios por e-mail")
            return {
                "reply": "✉️ **Envio de Relatórios por E-mail**:\n\nVocê pode disparar relatórios em **PDF** ou **Planilhas Excel** para colaboradores cadastrados ou outros e-mails diretamente pela central de envio.\n\nClique no botão abaixo para abrir a tela de envio:",
                "voice_text": "Você pode disparar relatórios por e-mail na tela de envio de relatórios.",
                "action": {"type": "navigate", "url": "/relatorios/enviar-email"},
                "suggestions": ["✉️ Abrir Envio de E-mail", "⏰ Ver Agendamentos", "📊 Ver Alertas"]
            }

        if any(w in p_clean for w in ["programar envio", "agendar relatorio", "agendamento de relatorio", "envios regulares", "agendar email"]):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "gerenciar agendamentos de e-mail")
            return {
                "reply": "⏰ **Programação de Envios Regulares**:\n\nVocê pode programar envios automáticos diários, semanais ou mensais de relatórios para os destinatários desejados.\n\nClique no botão abaixo para gerenciar os agendamentos:",
                "voice_text": "Você pode configurar envios regulares automáticos na tela de agendamentos.",
                "action": {"type": "navigate", "url": "/relatorios/agendamentos"},
                "suggestions": ["➕ Novo Agendamento", "📜 Histórico de E-mails", "📊 Ver Alertas"]
            }

        if any(w in p_clean for w in ["relatorio em pdf", "enviar relatorio", "gerar pdf", "baixar pdf", "exportar pdf", "pdf do financeiro", "relatorio financeiro pdf"]):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "gerar e baixar relatórios em PDF")
            return self._gerar_relatorio_pdf()

        if any(w in p_clean for w in ["exportar excel", "baixar excel", "gerar excel", "planilha excel", "exportar xlsx", "baixar xlsx"]):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "exportar planilhas Excel")
            return self._gerar_exportacao_excel()

        if any(w in p_clean for w in ["backup", "exportar tudo", "exportar json", "backup completo"]):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "fazer backup completo dos dados")
            return self._gerar_backup_json()

        # ── 4. AÇÕES DE PEDIDOS ───────────────────────────────────────────────
        if any(w in p_clean for w in ["mudar status", "alterar status", "concluir pedido", "cancelar pedido", "entregar pedido", "mover para producao", "colocar em producao"]):
            if not self._tem_permissao(user, "pedidos", "update"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "update", "atualizar o status de pedidos")
            return self._executar_mudar_status_pedido(p_clean, prompt_orig, user)

        if any(w in p_clean for w in ["excluir pedido", "remover pedido", "apagar pedido", "deletar pedido"]):
            if not self._tem_permissao(user, "pedidos", "delete"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "delete", "excluir pedidos")
            return self._executar_excluir_pedido(p_clean, prompt_orig)

        gatilhos_novo_pedido = [
            "criar pedido", "criar um pedido", "crie um pedido", "crie o pedido", "novo pedido", "pedido novo",
            "adicionar pedido", "adicione um pedido", "adicionar um pedido", "cadastrar pedido", "fazer pedido",
            "fazer um pedido", "faca um pedido", "registrar pedido", "registre um pedido", "recebi um pedido",
            "recebemos um pedido", "temos um pedido", "cliente pediu", "ela pediu", "ele pediu", "pediram",
            "fazer uma encomenda", "nova encomenda", "encomenda nova", "anotar pedido"
        ]
        if any(w in p_clean for w in gatilhos_novo_pedido):
            if not self._tem_permissao(user, "pedidos", "create"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "create", "criar novos pedidos de clientes")
            return self._executar_criar_pedido(p_clean, prompt_orig, user)

        # ── 5. AÇÕES DE ESTOQUE (CADASTRAR, ENTRADA, BAIXA, EXCLUIR) ──────────
        if any(w in p_clean for w in ["cadastrar material", "adicionar material", "novo material", "criar material", "adicionar novo insumo"]):
            if not self._tem_permissao(user, "adicionar", "create"):
                return self._resposta_negada(user_nome, roles_str, "adicionar", "create", "cadastrar novos materiais")
            return self._executar_cadastrar_material(p_clean, prompt_orig)

        if any(w in p_clean for w in ["dar entrada", "entrada de material", "adicionar ao estoque de", "chegou mais", "repor estoque de", "aumentar estoque"]):
            if not self._tem_permissao(user, "estoque", "update"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "update", "dar entrada de estoque")
            return self._executar_dar_entrada_material(p_clean, prompt_orig, user)

        if any(p_clean.startswith(w) for w in ["dar baixa", "baixar", "usei", "consumi", "gastei", "retirar do estoque"]) or "dar baixa" in p_clean:
            if not self._tem_permissao(user, "baixa", "create"):
                return self._resposta_negada(user_nome, roles_str, "baixa", "create", "dar baixa de insumos no estoque")
            return self._executar_dar_baixa(p_clean, prompt_orig, user)

        if any(w in p_clean for w in ["excluir material", "remover material", "apagar material", "deletar material"]):
            if not self._tem_permissao(user, "estoque", "delete"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "delete", "excluir materiais do estoque")
            return self._executar_excluir_material(p_clean, prompt_orig)

        # ── 6. AÇÕES DE PRODUTOS & BOLSAS ─────────────────────────────────────
        if any(w in p_clean for w in ["editar bolsa", "alterar bolsa", "editar produto", "alterar produto", "editar receita", "alterar receita", "mudar receita", "alterar materiais", "mudar materiais", "trocar materiais", "materiais da bolsa", "editar materiais", "alterar insumos"]):
            if not self._tem_permissao(user, "produtos", "update"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "update", "alterar receitas e materiais de bolsas")
            return self._executar_editar_produto(p_clean, prompt_orig)

        if any(w in p_clean for w in ["ajustar pecas prontas", "adicionar pecas prontas", "adicionar bolsa pronta", "remover peca pronta", "ajustar estoque pronto"]) or (("pecas prontas" in p_clean or "peca pronta" in p_clean or "estoque pronto" in p_clean) and any(w in p_clean for w in ["adicionar", "remover", "ajustar", "colocar", "tirar"])):
            if not self._tem_permissao(user, "produtos", "update"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "update", "ajustar o estoque de peças prontas")
            return self._executar_ajuste_estoque_pronto(p_clean, prompt_orig, user)

        if any(w in p_clean for w in ["cadastrar produto", "cadastrar bolsa", "nova bolsa", "novo produto", "criar bolsa", "criar produto"]):
            if not self._tem_permissao(user, "produtos", "create"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "create", "cadastrar novas bolsas e produtos")
            return self._executar_cadastrar_produto(p_clean, prompt_orig)

        if any(w in p_clean for w in ["excluir produto", "excluir bolsa", "remover produto", "remover bolsa"]):
            if not self._tem_permissao(user, "produtos", "delete"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "delete", "excluir produtos do catálogo")
            return self._executar_excluir_produto(p_clean, prompt_orig)

        # ── 7. AÇÕES DE SOBRAS & RETALHOS ─────────────────────────────────────
        if any(w in p_clean for w in ["cadastrar sobra", "cadastrar retalho", "adicionar sobra", "nova sobra", "guardar retalho"]):
            if not self._tem_permissao(user, "sobras", "create"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "create", "cadastrar sobras e retalhos")
            return self._executar_cadastrar_sobra(p_clean, prompt_orig)

        if any(w in p_clean for w in ["reaproveitar sobra", "usar sobra", "usar retalho", "descartar sobra", "descartar retalho"]):
            if not self._tem_permissao(user, "sobras", "update"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "update", "atualizar status de sobras")
            return self._executar_acao_sobra(p_clean, prompt_orig)

        if any(w in p_clean for w in ["excluir sobra", "remover sobra", "apagar sobra", "deletar sobra"]):
            if not self._tem_permissao(user, "sobras", "delete"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "delete", "excluir registros de sobras")
            return self._executar_excluir_sobra(p_clean, prompt_orig)

        # ── 8. AÇÕES FINANCEIRAS (DESPESAS) ───────────────────────────────────
        if any(w in p_clean for w in ["cadastrar despesa", "adicionar despesa", "nova despesa", "registrar despesa", "gasto de", "pagamos", "paguei"]):
            if not self._tem_permissao(user, "financeiro", "create"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "create", "cadastrar despesas no financeiro")
            return self._executar_cadastrar_despesa(p_clean, prompt_orig)

        if any(w in p_clean for w in ["excluir despesa", "remover despesa", "apagar despesa", "deletar despesa"]):
            if not self._tem_permissao(user, "financeiro", "delete"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "delete", "excluir registros de despesas")
            return self._executar_excluir_despesa(p_clean, prompt_orig)

        # ── 9. AÇÕES DE USUÁRIOS & PAPÉIS ────────────────────────────────────
        if any(w in p_clean for w in ["cadastrar usuario", "criar usuario", "novo usuario"]):
            if not self._tem_permissao(user, "usuarios", "create"):
                return self._resposta_negada(user_nome, roles_str, "usuarios", "create", "criar novos usuários")
            return self._executar_cadastrar_usuario(p_clean, prompt_orig)

        # ── 10. NAVEGAÇÃO RÁPIDA ──────────────────────────────────────────────
        nav_match = self._verificar_navegacao(p_clean)
        if nav_match:
            recurso, url_dest, nome_tela = nav_match
            if not self._tem_permissao(user, recurso, "read") and not self._tem_permissao(user, recurso, "create"):
                return self._resposta_negada(user_nome, roles_str, recurso, "read", f"navegar até a página de {nome_tela}")
            return {
                "reply": f"🧭 Abrindo a tela de **{nome_tela}** para você...",
                "voice_text": f"Abrindo a tela de {nome_tela}.",
                "action": {"type": "navigate", "url": url_dest},
                "suggestions": ["Voltar ao início", "Consultar estoque", "Ver pedidos"],
            }

        # ── 11. CONSULTAS GERAIS E INFORMATIVAS ───────────────────────────────
        if any(w in p_clean for w in ["alerta", "alertas", "estoque baixo", "acabando", "abaixo do minimo", "resumo do atelier", "diagnostico"]):
            if not self._tem_permissao(user, "relatorios", "read"):
                return self._resposta_negada(user_nome, roles_str, "relatorios", "read", "visualizar alertas e relatórios de desempenho")
            return self._consultar_alertas_e_resumo()

        if any(w in p_clean for w in ["financeiro", "saldo", "faturamento", "receita", "despesa", "lucro", "caixa", "faturamos", "ganhos"]):
            if not self._tem_permissao(user, "financeiro", "read"):
                return self._resposta_negada(user_nome, roles_str, "financeiro", "read", "consultar dados financeiros e faturamento")
            return self._consultar_financeiro(p_clean)

        if any(w in p_clean for w in ["pedido", "pedidos", "encomenda", "encomendas"]):
            if not self._tem_permissao(user, "pedidos", "read"):
                return self._resposta_negada(user_nome, roles_str, "pedidos", "read", "visualizar a lista de pedidos de clientes")
            return self._consultar_pedidos(p_clean)

        if any(w in p_clean for w in ["bolsa", "bolsas", "produto", "produtos", "receita da bolsa", "preco da bolsa", "pronta entrega"]):
            if not self._tem_permissao(user, "produtos", "read"):
                return self._resposta_negada(user_nome, roles_str, "produtos", "read", "consultar catálogo de produtos e receitas")
            return self._consultar_produtos(p_clean)

        if any(w in p_clean for w in ["sobra", "sobras", "retalho", "retalhos", "reaproveitamento"]):
            if not self._tem_permissao(user, "sobras", "read"):
                return self._resposta_negada(user_nome, roles_str, "sobras", "read", "consultar sobras e retalhos")
            return self._consultar_sobras()

        if any(w in p_clean for w in ["usuario", "usuarios", "quem tem acesso", "listar usuarios"]):
            if not self._tem_permissao(user, "usuarios", "read"):
                return self._resposta_negada(user_nome, roles_str, "usuarios", "read", "visualizar usuários do sistema")
            return self._consultar_usuarios()

        if any(w in p_clean for w in ["estoque", "material", "materiais", "quanto temos", "quantidade de", "insumo", "insumos", "gtin"]) or self._busca_material_direta(p_clean):
            if not self._tem_permissao(user, "estoque", "read"):
                return self._resposta_negada(user_nome, roles_str, "estoque", "read", "consultar materiais e quantidades em estoque")
            return self._consultar_estoque(p_clean, prompt_orig)

        # ── 12. CONSULTA AO ORQUESTRADOR MULTI-AGENTE (INTELIGÊNCIA ESPECIALISTA) ──
        if hasattr(self, "orchestrator") and self.orchestrator:
            materiais = self._carregar_materiais()
            produtos = self._carregar_produtos()
            system_ctx = {
                "user_name": user_nome,
                "roles_str": roles_str,
                "materiais": [{"id": m.get("id"), "nome": m.get("nome"), "gtin": m.get("gtin")} for m in materiais],
                "produtos": [{"id": p.get("id"), "nome": p.get("nome"), "gtin": p.get("gtin"), "preco_venda": p.get("preco_venda")} for p in produtos],
                "materiais_nomes": [m.get("nome") for m in materiais if m.get("nome")],
                "produtos_nomes": [p.get("nome") for p in produtos if p.get("nome")],
            }
            try:
                agent_res = self.orchestrator.route_and_process(prompt_orig, system_ctx, user)
                if agent_res and agent_res.handled:
                    if agent_res.action:
                        despacho = self._despachar_acao_ollama(agent_res.action, agent_res.params, prompt_orig, user, agent_res.to_dict())
                        if despacho:
                            despacho["engine"] = "multi_agent_local"
                            despacho["agent"] = agent_res.agent_name
                            return despacho
                    elif agent_res.text_response:
                        return {
                            "success": True,
                            "reply": agent_res.text_response,
                            "voice_text": agent_res.text_response.splitlines()[0] if agent_res.text_response else "Aqui está a resposta do especialista.",
                            "suggestions": ["📦 Consultar estoque", "🧾 Pedidos", "💰 Finanças", "🧵 Dicas de Costura"],
                            "engine": "multi_agent_local",
                            "agent": agent_res.agent_name
                        }
            except Exception:
                pass

        # ── FALLBACK ──────────────────────────────────────────────────────────
        return {
            "reply": f"Entendi sua mensagem, **{user_nome}**. O que exatamente você gostaria que eu faça?",
            "voice_text": "Não compreendi totalmente. Escolha uma das ações abaixo ou reformule sua pergunta.",
            "suggestions": [
                "📄 Enviar relatório em PDF",
                "📦 Consultar estoque",
                "🧾 Criar novo pedido",
                "✂️ Dar baixa de material",
                "💰 Resumo financeiro",
                "📊 Ver alertas",
                "Minhas permissões"
            ],
        }

    # ── HELPERS DE RBAC ──────────────────────────────────────────────────────

    def _tem_permissao(self, user: dict, recurso: str, acao: str) -> bool:
        roles = self._get_roles(user)
        if "Admin" in roles or "Developer" in roles:
            return True
        if not roles:
            return False
        col_map = {"create": "can_create", "read": "can_read", "update": "can_update", "delete": "can_delete"}
        col = col_map.get(acao, "can_read")
        if self._use_sqlite:
            try:
                self._init_db()
                conn = self._get_connection()
                cur = conn.cursor()
                for role in roles:
                    cur.execute(f"SELECT {col} FROM role_permissions WHERE role = ? AND resource = ?", (role, recurso))
                    row = cur.fetchone()
                    if row and row[0]:
                        conn.close()
                        return True
                conn.close()
            except Exception:
                pass
            return False
        return True

    def _resposta_negada(self, user_nome: str, roles_str: str, recurso: str, acao: str, acao_desc: str) -> dict:
        nome_amigavel = {
            "estoque": "Estoque de Materiais",
            "adicionar": "Adicionar Material",
            "baixa": "Dar Baixa em Insumos",
            "produtos": "Produtos & Receitas",
            "pedidos": "Pedidos dos Clientes",
            "sobras": "Sobras e Retalhos",
            "financeiro": "Gestão Financeira",
            "relatorios": "Alertas e Relatórios",
            "usuarios": "Gestão de Usuários",
            "roles": "Papéis & Permissões"
        }.get(recurso, recurso.capitalize())

        msg = (
            f"🔒 **Acesso Negado (RBAC)**\n\n"
            f"Desculpe, **{user_nome}**. Seu perfil de acesso (*{roles_str}*) **não possui permissão** para {acao_desc}.\n\n"
            f"📌 **Módulo Necessário**: `{nome_amigavel}` (Permissão `{recurso}:{acao}`).\n"
            f"Caso precise realizar esta operação, solicite a um **Administrador** para ajustar seus papéis em *Papéis & Permissões*."
        )
        voice = f"Acesso negado. Seu perfil não tem permissão para {acao_desc} no módulo de {nome_amigavel}."
        return {
            "success": False,
            "denied": True,
            "reply": msg,
            "voice_text": voice,
            "suggestions": ["Minhas permissões", "O que posso fazer?", "Consultar estoque", "Ver pedidos"]
        }

    # ── 1. RELATÓRIOS E EXPORTAÇÕES (PDF, EXCEL, JSON) ───────────────────────

    def _gerar_relatorio_pdf(self) -> dict:
        pedidos = self._carregar_pedidos()
        despesas = self._carregar_despesas()
        materiais = self._carregar_materiais()

        rec_total = sum(float(p.get("valor_total") or 0) for p in pedidos if p.get("status") in ("Concluído", "Entregue"))
        desp_total = sum(float(d.get("valor") or 0) for d in despesas)
        saldo = rec_total - desp_total

        msg = (
            f"📄 **Relatório Financeiro & Operacional em PDF Gerado!** ✨\n\n"
            f"O relatório completo com layout artesanal e indicadores do Ateliê Haiti está pronto para download:\n\n"
            f"• **Faturamento Realizado**: {formatar_moeda(rec_total)}\n"
            f"• **Despesas Registradas**: {formatar_moeda(desp_total)}\n"
            f"• **Saldo Líquido**: **{formatar_moeda(saldo)}**\n"
            f"• **Total de Pedidos**: {len(pedidos)} | **Materiais**: {len(materiais)}\n\n"
            f"<a href=\"/exportar/pdf\" target=\"_blank\" class=\"btn-primary\" style=\"display:inline-block; padding:10px 18px; font-size:15px !important; text-decoration:none; margin-top:6px;\">📥 Baixar Relatório em PDF</a>"
        )
        voice = f"Relatório financeiro em PDF gerado com sucesso. O faturamento é de {formatar_moeda(rec_total)} e o saldo líquido é de {formatar_moeda(saldo)}. O download foi disponibilizado."
        return {
            "success": True,
            "reply": msg,
            "voice_text": voice,
            "action": {"type": "download", "url": "/exportar/pdf", "filename": "relatorio_atelie_haiti.pdf"},
            "suggestions": ["📊 Ver alertas", "💰 Resumo financeiro", "📦 Consultar estoque"]
        }

    def _gerar_exportacao_excel(self) -> dict:
        msg = (
            f"📊 **Planilha Excel (XLSX) Gerada com Sucesso!**\n\n"
            f"A planilha contém abas completas com formatação profissional de **Estoque, Produtos, Pedidos, Despesas e Sobras**.\n\n"
            f"<a href=\"/exportar/xlsx\" target=\"_blank\" class=\"btn-primary\" style=\"display:inline-block; padding:10px 18px; font-size:15px !important; text-decoration:none; margin-top:6px;\">📥 Baixar Planilha Excel (.xlsx)</a>"
        )
        voice = "Planilha Excel com todas as abas do ateliê gerada com sucesso para download."
        return {
            "success": True,
            "reply": msg,
            "voice_text": voice,
            "action": {"type": "download", "url": "/exportar/xlsx", "filename": "export_atelie_haiti.xlsx"},
            "suggestions": ["📄 Gerar PDF", "💰 Financeiro", "Ver estoque"]
        }

    def _gerar_backup_json(self) -> dict:
        msg = (
            f"💾 **Backup Completo dos Dados Gerado!**\n\n"
            f"O arquivo JSON com a base consolidada de todos os registros do ateliê está pronto para download.\n\n"
            f"<a href=\"/exportar\" target=\"_blank\" class=\"btn-secondary\" style=\"display:inline-block; padding:10px 18px; font-size:15px !important; text-decoration:none;\">📥 Baixar Backup JSON</a>"
        )
        voice = "Arquivo de backup completo gerado para download."
        return {
            "success": True,
            "reply": msg,
            "voice_text": voice,
            "action": {"type": "download", "url": "/exportar", "filename": "export_all.json"},
            "suggestions": ["📄 Gerar PDF", "Ver estoque"]
        }

    # ── 2. AÇÕES DE PEDIDOS (MÉTODOS REGEX DE CONTINGÊNCIA) ──────────────────

    def _executar_mudar_status_pedido(self, p_clean: str, prompt_orig: str, user: dict) -> dict:
        pedidos = self._carregar_pedidos()
        if not pedidos:
            return {"reply": "Não há pedidos registrados para atualizar status.", "voice_text": "Não há pedidos registrados."}

        novo_status = None
        if "cancelar" in p_clean or "cancelado" in p_clean:
            novo_status = "Cancelado"
        elif "concluir" in p_clean or "concluido" in p_clean or "pronto" in p_clean:
            novo_status = "Concluído"
        elif "entregar" in p_clean or "entregue" in p_clean:
            novo_status = "Entregue"
        elif "producao" in p_clean or "em producao" in p_clean:
            novo_status = "Em produção"
        elif "pendente" in p_clean:
            novo_status = "Pendente"

        if not novo_status:
            return {
                "reply": "Para qual status você deseja alterar o pedido? Opções: *Pendente*, *Em produção*, *Concluído*, *Entregue* ou *Cancelado*.",
                "voice_text": "Informe o novo status desejado para o pedido.",
                "suggestions": ["Concluir pedido", "Mover para produção", "Cancelar pedido"]
            }

        pedido_alvo = None
        # 1. Busca exata por nome completo
        for p in pedidos:
            c_clean = remover_acentos(p.get("cliente", ""))
            if c_clean and c_clean in p_clean:
                pedido_alvo = p
                break

        # 2. Busca por palavras significativas do nome
        if not pedido_alvo:
            for p in pedidos:
                c_clean = remover_acentos(p.get("cliente", ""))
                palavras = [w for w in c_clean.split() if len(w) >= 4]
                if palavras and all(w in p_clean for w in palavras):
                    pedido_alvo = p
                    break

        if not pedido_alvo:
            if "ultimo" in p_clean or len(pedidos) == 1:
                pedido_alvo = pedidos[0]
            else:
                linhas = [f"• **{p['cliente']}**: {p['quantidade']}x {p['produto_nome']} (`{p['status']}`)" for p in pedidos[:4]]
                return {
                    "reply": f"De qual cliente você deseja alterar o pedido para **{novo_status}**?\n\n" + "\n".join(linhas),
                    "voice_text": "De qual cliente você deseja atualizar o pedido?",
                    "suggestions": [f"Mudar status de {p['cliente']}" for p in pedidos[:3]]
                }

        return self._executar_mudar_status_pedido_direto(pedido_alvo["cliente"], novo_status, user, prompt_orig)

    def _executar_excluir_pedido(self, p_clean: str, prompt_orig: str) -> dict:
        pedidos = self._carregar_pedidos()
        if not pedidos:
            return {"reply": "Não há pedidos para excluir.", "voice_text": "Não há pedidos para excluir."}

        pedido_alvo = None
        for p in pedidos:
            c_clean = remover_acentos(p.get("cliente", ""))
            if c_clean and c_clean in p_clean:
                pedido_alvo = p
                break

        if not pedido_alvo:
            for p in pedidos:
                c_clean = remover_acentos(p.get("cliente", ""))
                palavras = [w for w in c_clean.split() if len(w) >= 4]
                if palavras and all(w in p_clean for w in palavras):
                    pedido_alvo = p
                    break

        if not pedido_alvo:
            return {
                "reply": "Por favor informe o nome do cliente do pedido que deseja excluir. Exemplo: *\"Excluir pedido de Maria Silva\"*.",
                "voice_text": "Informe o nome do cliente do pedido que deseja excluir.",
                "suggestions": [f"Excluir pedido de {p['cliente']}" for p in pedidos[:3]]
            }

        return self._executar_excluir_pedido_direto(pedido_alvo["cliente"], prompt_orig)

    def _executar_criar_pedido(self, p_clean: str, prompt_orig: str, user: dict) -> dict:
        produtos = self._carregar_produtos()
        if not produtos:
            return {"reply": "Não há bolsas ou produtos cadastrados para registrar pedidos.", "voice_text": "Não há produtos cadastrados."}

        match_qtd = re.search(r"(\d+)\s*(?:unidades?|pecas?|bolsas?|x)?", p_clean)
        qtd = int(match_qtd.group(1)) if match_qtd else 1

        # 1. Busca produto por GTIN se houver código
        gtin_m = re.search(r"\b(\d{8,14})\b", prompt_orig)
        produto_alvo = None
        if gtin_m:
            gtin_num = gtin_m.group(1)
            produto_alvo = next((p for p in produtos if (p.get("gtin") or "").strip() == gtin_num), None)

        # 2. Busca produto por nome
        if not produto_alvo:
            for p in produtos:
                p_nome_clean = remover_acentos(p["nome"])
                if p_nome_clean in p_clean or any(palavra in p_clean for palavra in p_nome_clean.split() if len(palavra) >= 4):
                    produto_alvo = p
                    break

        # 3. Extração do cliente
        cliente = "Cliente Balcão"
        match_cliente = re.search(
            r"(?:para\s+a|para\s+o|para|de\s+uma\s+cliente\s+chamada|de\s+um\s+cliente\s+chamado|cliente\s+chamada|cliente\s+chamado|da\s+cliente|do\s+cliente|cliente)\s+([A-Za-zÀ-ÖØ-öø-ÿ\s]+?)(?:\s*,|\s+ela\b|\s+ele\b|\s+que\b|\s+pediu\b|\s+de\b|\s+da\b|\s+do\b|\s+com\b|\.|$|\n)",
            prompt_orig,
            re.IGNORECASE
        )
        if match_cliente:
            c_cand = match_cliente.group(1).strip()
            for noise in ["chamada", "chamado", "uma", "um", "cliente", "nova", "novo"]:
                c_cand = re.sub(r"^\b" + noise + r"\b\s*", "", c_cand, flags=re.IGNORECASE).strip()
            if len(c_cand) > 1 and c_cand.lower() not in ["bolsa", "pedido", "encomenda"]:
                cliente = c_cand.title()

        if not produto_alvo:
            produto_alvo = produtos[0]

        return self._executar_criar_pedido_direto(cliente, produto_alvo["nome"], qtd, user, prompt_orig)

    # ── 3. AÇÕES DE ESTOQUE (MÉTODOS REGEX DE CONTINGÊNCIA) ──────────────────

    def _executar_cadastrar_material(self, p_clean: str, prompt_orig: str) -> dict:
        nome_match = re.search(r"(?:material|insumo)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+categoria|\s+com\b|\s+qtd|\s+quantidade|\.|$)", prompt_orig, re.IGNORECASE)
        nome = nome_match.group(1).strip().title() if nome_match else ""

        if not nome or len(nome) < 2:
            return {
                "reply": "📝 Deseja abrir o formulário para cadastrar um novo material com foto e GTIN?",
                "voice_text": "Abrindo formulário de cadastro de material.",
                "action": {"type": "navigate", "url": "/adicionar"},
                "suggestions": ["➕ Abrir formulário de material", "Ver estoque"]
            }

        categoria = "Outros"
        for cat in getattr(self.app, "CATEGORIAS", []):
            if remover_acentos(cat) in p_clean:
                categoria = cat
                break

        unidade = "unidades"
        for u in getattr(self.app, "UNIDADES", []):
            if remover_acentos(u) in p_clean:
                unidade = u
                break

        qtd_match = re.search(r"(?:quantidade|qtd|com)\s+(\d+(?:[.,]\d+)?)", p_clean)
        qtd = float(qtd_match.group(1).replace(",", ".")) if qtd_match else 1.0

        custo_match = re.search(r"(?:custo|valor|preco|custando|de)\s+(?:r\$\s*)?(\d+(?:[.,]\d+)?)", p_clean)
        custo = float(custo_match.group(1).replace(",", ".")) if custo_match else 0.0

        min_match = re.search(r"(?:minimo|minima)\s+(\d+(?:[.,]\d+)?)", p_clean)
        qtd_min = float(min_match.group(1).replace(",", ".")) if min_match else 1.0

        gtin_match = re.search(r"\b(\d{8,14})\b", prompt_orig)
        gtin = gtin_match.group(1) if gtin_match else ""

        return self._executar_cadastrar_material_direto({
            "nome": nome,
            "categoria": categoria,
            "unidade": unidade,
            "quantidade": qtd,
            "custo": custo,
            "quantidade_minima": qtd_min,
            "gtin": gtin,
        }, prompt_orig)

    def _executar_dar_entrada_material(self, p_clean: str, prompt_orig: str, user: dict) -> dict:
        materiais = self._carregar_materiais()
        if not materiais:
            return {"reply": "Não há materiais no estoque.", "voice_text": "Estoque vazio."}

        match_qtd = re.search(r"(\d+(?:[.,]\d+)?)", p_clean)
        qtd = float(match_qtd.group(1).replace(",", ".")) if match_qtd else 1.0

        material_alvo = None
        for m in materiais:
            n_clean = remover_acentos(m["nome"])
            if n_clean and n_clean in p_clean:
                material_alvo = m
                break

        if not material_alvo:
            for m in materiais:
                n_clean = remover_acentos(m["nome"])
                palavras = [w for w in n_clean.split() if len(w) >= 4]
                if palavras and all(w in p_clean for w in palavras):
                    material_alvo = m
                    break

        if not material_alvo:
            return {
                "reply": "Por favor informe o nome do material e a quantidade que deseja adicionar. Exemplo: *\"Dar entrada de 5 metros de Courino\"*.",
                "voice_text": "Informe o nome do material e a quantidade para dar entrada.",
                "suggestions": [f"Dar entrada em {m['nome']}" for m in materiais[:3]]
            }

        return self._executar_entrada_direta(material_alvo["nome"], qtd, user, prompt_orig)

    def _executar_dar_baixa(self, p_clean: str, prompt_orig: str, user: dict) -> dict:
        materiais = self._carregar_materiais()
        if not materiais:
            return {"reply": "Não há materiais no estoque para dar baixa.", "voice_text": "Estoque vazio."}

        match_qtd = re.search(r"(\d+(?:[.,]\d+)?)", p_clean)
        qtd = float(match_qtd.group(1).replace(",", ".")) if match_qtd else 1.0

        material_alvo = None
        for m in materiais:
            n_clean = remover_acentos(m["nome"])
            if n_clean and n_clean in p_clean:
                material_alvo = m
                break

        if not material_alvo:
            for m in materiais:
                n_clean = remover_acentos(m["nome"])
                palavras = [w for w in n_clean.split() if len(w) >= 4]
                if palavras and any(w in p_clean for w in palavras):
                    material_alvo = m
                    break

        if not material_alvo:
            return {
                "reply": "✂️ Para dar baixa, por favor informe o **nome do material** e a **quantidade**. Exemplo: *\"Dar baixa de 2 zíperes por costura\"*.",
                "voice_text": "Por favor informe o nome do material e a quantidade que deseja dar baixa.",
                "action": {"type": "navigate", "url": "/baixa"},
                "suggestions": ["Dar baixa de couro", "Dar baixa de zíper", "Abrir tela de baixa"]
            }

        motivo = "Uso em produção (via Ania)"
        for m_teste in ["costura", "corte", "defeito", "descarte", "prototipo", "perda", "teste", "amostra"]:
            if m_teste in p_clean:
                motivo = m_teste.capitalize()
                break

        return self._executar_baixa_direta(material_alvo["nome"], qtd, motivo, user, prompt_orig)

    def _executar_excluir_material(self, p_clean: str, prompt_orig: str) -> dict:
        materiais = self._carregar_materiais()
        material_alvo = None
        for m in materiais:
            n_clean = remover_acentos(m["nome"])
            if n_clean and n_clean in p_clean:
                material_alvo = m
                break

        if not material_alvo:
            return {
                "reply": "Qual material você deseja excluir? Exemplo: *\"Excluir material Retalho de Couro\"*.",
                "voice_text": "Informe qual material deseja excluir.",
                "suggestions": [f"Excluir {m['nome']}" for m in materiais[:3]]
            }

        return self._executar_excluir_material_direto(material_alvo["nome"], prompt_orig)

    # ── 4. AÇÕES DE PRODUTOS & BOLSAS (MÉTODOS REGEX DE CONTINGÊNCIA) ─────────

    def _executar_cadastrar_produto(self, p_clean: str, prompt_orig: str) -> dict:
        nome_match = re.search(r"(?:bolsa|produto)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+com\s+preco|\s+preco|\s+valor|\.|$)", prompt_orig, re.IGNORECASE)
        nome = nome_match.group(1).strip().title() if nome_match else ""

        if not nome or len(nome) < 2:
            return {
                "reply": "👜 Deseja abrir a página para cadastrar uma nova bolsa com receita e custos?",
                "voice_text": "Abrindo cadastro de novo produto.",
                "action": {"type": "navigate", "url": "/produtos/novo"},
                "suggestions": ["➕ Abrir Novo Produto", "Ver catálogo"]
            }

        preco_match = re.search(r"(?:preco|valor|de)\s+(?:r\$\s*)?(\d+(?:[.,]\d+)?)", p_clean)
        preco = float(preco_match.group(1).replace(",", ".")) if preco_match else 0.0

        estoque_match = re.search(r"(\d+)\s*(?:pecas?\s+prontas?|prontas?)", p_clean)
        est_pronto = int(estoque_match.group(1)) if estoque_match else 0

        emoji = "👜"
        for e in ["👜", "🎒", "👝", "💼", "🧳", "👛"]:
            if e in prompt_orig:
                emoji = e
                break

        return self._executar_cadastrar_produto_direto({
            "nome": nome,
            "preco_venda": preco,
            "estoque_pronto": est_pronto,
            "emoji": emoji,
        }, prompt_orig)

    def _executar_ajuste_estoque_pronto(self, p_clean: str, prompt_orig: str, user: dict) -> dict:
        produtos = self._carregar_produtos()
        if not produtos:
            return {"reply": "Não há produtos cadastrados.", "voice_text": "Não há produtos."}

        match_qtd = re.search(r"(\d+)", p_clean)
        qtd = int(match_qtd.group(1)) if match_qtd else 1

        is_remover = any(w in p_clean for w in ["remover", "retirar", "diminuir", "subtrair"])
        acao = "remover" if is_remover else "adicionar"

        produto_alvo = None
        for p in produtos:
            p_clean_n = remover_acentos(p["nome"])
            if p_clean_n in p_clean:
                produto_alvo = p
                break

        if not produto_alvo:
            for p in produtos:
                p_clean_n = remover_acentos(p["nome"])
                palavras = [w for w in p_clean_n.split() if len(w) >= 4]
                if palavras and any(w in p_clean for w in palavras):
                    produto_alvo = p
                    break

        if not produto_alvo:
            return {
                "reply": "De qual bolsa você deseja ajustar o estoque de peças prontas? Exemplo: *\"Adicionar 2 peças prontas na Bolsa Tote\"*.",
                "voice_text": "Informe a bolsa e a quantidade para ajustar o estoque de peças prontas.",
                "suggestions": [f"Ajustar {p['nome']}" for p in produtos[:3]]
            }

        return self._executar_ajuste_estoque_pronto_direto(produto_alvo["nome"], qtd, acao, user, prompt_orig)

    def _executar_editar_produto(self, p_clean: str, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        if not produtos:
            return {"reply": "Não há bolsas ou produtos cadastrados ainda.", "voice_text": "Não há produtos cadastrados."}

        produto_alvo = None
        for p in produtos:
            p_clean_n = remover_acentos(p["nome"])
            if p_clean_n in p_clean:
                produto_alvo = p
                break

        if not produto_alvo:
            for p in produtos:
                p_clean_n = remover_acentos(p["nome"])
                palavras = [w for w in p_clean_n.split() if len(w) >= 4]
                if palavras and any(w in p_clean for w in palavras):
                    produto_alvo = p
                    break

        if not produto_alvo:
            return {
                "reply": "De qual bolsa você deseja alterar os materiais ou a receita? Exemplo: *\"Alterar receita da Bolsa Tote Clássica\"*.\n\n" +
                         "Você também pode escolher uma das opções abaixo:",
                "voice_text": "Selecione a bolsa que deseja editar a receita de materiais.",
                "suggestions": [f"Editar {p['nome']}" for p in produtos[:3]]
            }

        url_edit = f"/produtos/{produto_alvo['id']}/editar"
        msg = (
            f"🧵 **Editar Receita de {produto_alvo.get('emoji', '👜')} {produto_alvo['nome']}**\n\n"
            f"Preço de venda atual: **R$ {float(produto_alvo.get('preco_venda') or 0):.2f}**\n"
            f"Peças prontas em estoque: **{produto_alvo.get('estoque_pronto', 0)} unid.**\n\n"
            f"Abrindo a tela de edição para você alterar as matérias-primas necessárias, quantidades da receita e simular a margem de lucro."
        )
        return {
            "reply": msg,
            "voice_text": f"Abrindo tela de edição da receita de {produto_alvo['nome']}.",
            "action": {"type": "navigate", "url": url_edit},
            "suggestions": ["Ver catálogo", "➕ Novo Produto"]
        }

    def _executar_excluir_produto(self, p_clean: str, prompt_orig: str) -> dict:
        produtos = self._carregar_produtos()
        produto_alvo = None
        for p in produtos:
            p_clean_n = remover_acentos(p["nome"])
            if p_clean_n in p_clean:
                produto_alvo = p
                break

        if not produto_alvo:
            return {
                "reply": "Qual bolsa você deseja excluir? Exemplo: *\"Excluir produto Bolsa Tote\"*.",
                "voice_text": "Informe a bolsa que deseja excluir.",
                "suggestions": [f"Excluir {p['nome']}" for p in produtos[:3]]
            }

        return self._executar_excluir_produto_direto(produto_alvo["nome"], prompt_orig)

    # ── 5. AÇÕES DE SOBRAS & RETALHOS (MÉTODOS REGEX DE CONTINGÊNCIA) ─────────

    def _executar_cadastrar_sobra(self, p_clean: str, prompt_orig: str) -> dict:
        desc_match = re.search(r"(?:sobra|retalho)\s+(?:de\s+)?([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+com\s+qtd|\s+qtd|\.|$)", prompt_orig, re.IGNORECASE)
        desc = desc_match.group(1).strip().title() if desc_match else "Retalho de Material"

        qtd_match = re.search(r"(\d+(?:[.,]\d+)?)", p_clean)
        qtd = float(qtd_match.group(1).replace(",", ".")) if qtd_match else 1.0

        unidade = "metros"
        for u in getattr(self.app, "UNIDADES", []):
            if remover_acentos(u) in p_clean:
                unidade = u
                break

        return self._executar_cadastrar_sobra_direto({
            "descricao": desc,
            "quantidade": qtd,
            "unidade": unidade
        }, prompt_orig)

    def _executar_acao_sobra(self, p_clean: str, prompt_orig: str) -> dict:
        sobras = self._carregar_sobras()
        if not sobras:
            return {"reply": "Não há sobras registradas.", "voice_text": "Não há sobras registradas."}

        novo_status = "Descartado" if "descartar" in p_clean else "Reaproveitado"
        sobra_alvo = next((s for s in sobras if remover_acentos(s.get("descricao", "")) in p_clean), sobras[0] if len(sobras) == 1 else None)

        if not sobra_alvo:
            return {
                "reply": f"Qual sobra você deseja marcar como **{novo_status}**?\n\n" + "\n".join([f"• {s['descricao']} ({s['quantidade']} {s['unidade']})" for s in sobras[:3]]),
                "voice_text": "Qual sobra você deseja atualizar?",
                "suggestions": [f"{novo_status} {s['descricao']}" for s in sobras[:3]]
            }

        return self._executar_acao_sobra_direto(sobra_alvo["descricao"], novo_status, prompt_orig)

    def _executar_excluir_sobra(self, p_clean: str, prompt_orig: str) -> dict:
        sobras = self._carregar_sobras()
        sobra_alvo = next((s for s in sobras if remover_acentos(s.get("descricao", "")) in p_clean), None)
        if not sobra_alvo:
            return {"reply": "Qual sobra você deseja excluir?", "voice_text": "Qual sobra deseja excluir?"}

        return self._executar_excluir_sobra_direto(sobra_alvo["descricao"], prompt_orig)

    # ── 6. AÇÕES FINANCEIRAS (DESPESAS) ──────────────────────────────────────

    def _executar_cadastrar_despesa(self, p_clean: str, prompt_orig: str) -> dict:
        valor_match = re.search(r"(?:de|valor|r\$)\s*(\d+(?:[.,]\d+)?)", p_clean)
        valor = float(valor_match.group(1).replace(",", ".")) if valor_match else 0.0

        if valor <= 0:
            return {
                "reply": "💰 Para cadastrar uma despesa, informe o valor e a descrição. Exemplo: *\"Cadastrar despesa de 50 reais com Linhas e Zíperes\"*.",
                "voice_text": "Informe o valor e a descrição da despesa.",
                "action": {"type": "navigate", "url": "/financeiro"},
                "suggestions": ["➕ Abrir Financeiro", "Resumo financeiro"]
            }

        desc_match = re.search(r"(?:com|para|de)\s+([A-Za-zÀ-ÖØ-öø-ÿ0-9\s]+?)(?:\s+categoria|\.|$)", prompt_orig, re.IGNORECASE)
        desc = desc_match.group(1).strip().title() if desc_match else "Despesa Operacional"

        categoria = "Insumos"
        for cat in ["Insumos", "Ferramentas", "Embalagem", "Manutenção", "Frete", "Outros"]:
            if remover_acentos(cat) in p_clean:
                categoria = cat
                break

        return self._executar_cadastrar_despesa_direto({
            "descricao": desc,
            "valor": valor,
            "categoria": categoria,
        }, prompt_orig)

    def _executar_excluir_despesa(self, p_clean: str, prompt_orig: str) -> dict:
        despesas = self._carregar_despesas()
        if not despesas:
            return {"reply": "Não há despesas registradas.", "voice_text": "Não há despesas."}

        desp_alvo = next((d for d in despesas if remover_acentos(d.get("descricao", "")) in p_clean), despesas[0] if len(despesas) == 1 else None)
        if not desp_alvo:
            return {
                "reply": "Qual despesa você deseja excluir?\n\n" + "\n".join([f"• {d['descricao']} ({formatar_moeda(d['valor'])})" for d in despesas[:3]]),
                "voice_text": "Qual despesa deseja excluir?",
                "suggestions": [f"Excluir despesa de {d['descricao']}" for d in despesas[:3]]
            }

        return self._executar_excluir_despesa_direto(desp_alvo["descricao"], prompt_orig)

    # ── 7. AÇÕES DE USUÁRIOS ─────────────────────────────────────────────────

    def _executar_cadastrar_usuario(self, p_clean: str, prompt_orig: str) -> dict:
        msg = (
            f"👥 **Gestão de Usuários**\n\n"
            f"Para cadastrar um novo usuário com senha segura e atribuição de múltiplos papéis, abra a tela de cadastro:\n\n"
            f"<a href=\"/usuarios/novo\" class=\"btn-primary\" style=\"display:inline-block; padding:10px 18px; font-size:15px !important; text-decoration:none;\">➕ Novo Usuário</a>"
        )
        voice = "Abrindo tela de cadastro de novos usuários."
        return {"reply": msg, "voice_text": voice, "action": {"type": "navigate", "url": "/usuarios/novo"}, "suggestions": ["Ver usuários", "Minhas permissões"]}

    # ── 8. MÉTODOS DE AJUDA, PERMISSÕES E CONSULTAS ──────────────────────────

    def _responder_ajuda(self, user_nome: str, roles: list) -> dict:
        msg = (
            f"✨ **Olá, {user_nome}! Eu sou a Ania**, sua assistente inteligente completa do Ateliê Haiti. 🧵\n\n"
            f"Comigo você pode executar **todas as ações do sistema** por **voz 🎙️ ou texto**:\n\n"
            f"📄 **Relatórios e Documentos:**\n"
            f"• *\"Gerar relatório em PDF\"* | *\"Exportar planilha Excel\"*\n\n"
            f"🧾 **Gestão de Pedidos:**\n"
            f"• *\"Criar pedido para Maria Silva de 2 Bolsas Tote\"*\n"
            f"• *\"Concluir pedido de Carlos\"* | *\"Cancelar pedido de Ana\"*\n\n"
            f"📦 **Estoque e Insumos:**\n"
            f"• *\"Quanto couro temos em estoque?\"* | *\"Buscar GTIN 789...\"*\n"
            f"• *\"Dar baixa de 2 zíperes por costura\"* | *\"Dar entrada de 10 metros de courino\"*\n"
            f"• *\"Cadastrar material Tecido Jeans, categoria Tecido...\"*\n\n"
            f"👜 **Produtos & Peças Prontas:**\n"
            f"• *\"Cadastrar bolsa Carteira Clutch com preço 80 reais\"*\n"
            f"• *\"Adicionar 3 peças prontas na Bolsa Tote\"*\n\n"
            f"💰 **Financeiro & Despesas:**\n"
            f"• *\"Qual o faturamento e saldo?\"* | *\"Cadastrar despesa de 40 reais com linhas\"*\n\n"
            f"🔒 **Acessos & Segurança:**\n"
            f"• *\"Quais são as minhas permissões?\"*"
        )
        voice = f"Olá {user_nome}! Eu sou a Ania. Você pode me pedir para gerar relatórios em PDF, criar pedidos, dar baixa de materiais, cadastrar produtos e consultar o financeiro por voz ou texto."
        return {
            "reply": msg,
            "voice_text": voice,
            "suggestions": ["📄 Enviar relatório em PDF", "📦 Consultar estoque", "🧾 Criar novo pedido", "💰 Resumo financeiro", "📊 Ver alertas"]
        }

    def _responder_minhas_permissoes(self, user: dict, user_nome: str, roles: list) -> dict:
        roles_str = ", ".join(roles) if roles else "Nenhum papel atribuído"
        is_admin = "Admin" in roles

        tabs_permitidas = []
        for tab in getattr(self.app, "SYSTEM_TABS", []):
            t_id = tab["id"]
            if is_admin or (hasattr(self.app, "user_has_permission") and self.app.user_has_permission(t_id, "read")) or (t_id in ("adicionar", "baixa") and hasattr(self.app, "user_has_permission") and self.app.user_has_permission(t_id, "create")):
                tabs_permitidas.append(f"{tab['emoji']} **{tab['name']}**")

        msg = (
            f"👤 **Perfil e Acessos de {user_nome}**\n\n"
            f"• **Papéis Ativos**: `{roles_str}`\n"
            f"• **Nível**: {'👑 Administrador (Acesso Total a Todas as Ações)' if is_admin else '🧵 Usuário com Permissões Granulares'}\n\n"
            f"📋 **Módulos que você pode operar:**\n" +
            ("\n".join([f"• {t}" for t in tabs_permitidas]) if tabs_permitidas else "• *Nenhum módulo liberado atualmente.*")
        )
        voice = f"{user_nome}, seu perfil possui os papéis: {roles_str}. Você tem acesso a {len(tabs_permitidas)} módulos do sistema."
        return {
            "reply": msg,
            "voice_text": voice,
            "suggestions": ["📄 Gerar PDF", "📦 Consultar estoque", "🧾 Pedidos", "📊 Alertas"]
        }

    def _verificar_navegacao(self, p_clean: str):
        rotas = [
            (["ir para estoque", "abrir estoque", "me leve para o estoque", "tela de estoque", "pagina de estoque"], "estoque", "/estoque", "Estoque"),
            (["ir para adicionar", "abrir adicionar", "tela de adicionar", "pagina de adicionar"], "adicionar", "/adicionar", "Adicionar Material"),
            (["ir para baixa", "abrir baixa", "tela de baixa", "pagina de baixa"], "baixa", "/baixa", "Dar Baixa"),
            (["ir para produtos", "abrir produtos", "catalogo de bolsas", "tela de produtos", "pagina de produtos"], "produtos", "/produtos", "Produtos & Receitas"),
            (["ir para pedidos", "abrir pedidos", "tela de pedidos", "pagina de pedidos"], "pedidos", "/pedidos", "Pedidos dos Clientes"),
            (["ir para sobras", "abrir sobras", "tela de sobras", "pagina de sobras"], "sobras", "/sobras", "Sobras & Reaproveitamento"),
            (["ir para financeiro", "abrir financeiro", "tela de financeiro", "pagina de financeiro"], "financeiro", "/financeiro", "Financeiro"),
            (["ir para alertas", "abrir alertas", "ir para relatorios", "abrir relatorios", "tela de relatorios"], "relatorios", "/alertas", "Alertas e Relatórios"),
            (["ir para usuarios", "abrir usuarios", "tela de usuarios", "pagina de usuarios"], "usuarios", "/usuarios", "Gestão de Usuários"),
            (["ir para papeis", "abrir papeis", "abrir permissoes", "tela de papeis"], "roles", "/roles", "Papéis & Permissões"),
            (["ir para minha conta", "abrir minha conta", "editar meu perfil"], "estoque", "/minha-conta", "Minha Conta"),
        ]
        for triggers, recurso, url, nome in rotas:
            if any(t in p_clean for t in triggers):
                return recurso, url, nome
        return None

    def _busca_material_direta(self, p_clean: str) -> bool:
        materiais = self._carregar_materiais()
        for m in materiais:
            nm = remover_acentos(m.get("nome", ""))
            if nm and len(nm) >= 3 and nm in p_clean:
                return True
        return False

    def _consultar_estoque(self, p_clean: str, prompt_orig: str) -> dict:
        materiais = self._carregar_materiais()
        if not materiais:
            return {
                "reply": "📦 Não há materiais cadastrados no estoque ainda.",
                "voice_text": "Não há materiais cadastrados no estoque ainda.",
                "suggestions": ["➕ Cadastrar material", "Ver produtos"]
            }

        match_gtin = re.search(r"\b(\d{6,14})\b", prompt_orig)
        if match_gtin:
            gtin_num = match_gtin.group(1)
            mat_gtin = next((m for m in materiais if (m.get("gtin") or "").strip() == gtin_num), None)
            if mat_gtin:
                val_total = float(mat_gtin.get("quantidade") or 0) * float(mat_gtin.get("custo") or 0)
                msg = (
                    f"🔍 **Material Encontrado por GTIN {gtin_num}**:\n\n"
                    f"• **Item**: {mat_gtin.get('emoji', '📦')} **{mat_gtin['nome']}**\n"
                    f"• **Quantidade em Estoque**: **{mat_gtin['quantidade']} {mat_gtin['unidade']}**\n"
                    f"• **Custo Unitário**: {formatar_moeda(mat_gtin['custo'])}\n"
                    f"• **Valor Total em Estoque**: {formatar_moeda(val_total)}\n"
                    f"• **Estoque Mínimo**: {mat_gtin.get('quantidade_minima', 0)} {mat_gtin['unidade']}"
                )
                voice = f"Material encontrado: {mat_gtin['nome']}. Temos {mat_gtin['quantidade']} {mat_gtin['unidade']} em estoque."
                return {"reply": msg, "voice_text": voice, "suggestions": [f"Dar baixa de {mat_gtin['nome']}", "Ver estoque"]}

        encontrados = []
        for m in materiais:
            nome_clean = remover_acentos(m.get("nome", ""))
            cat_clean = remover_acentos(m.get("categoria", ""))
            if nome_clean in p_clean or any(word in p_clean for word in nome_clean.split() if len(word) > 2) or (cat_clean and cat_clean in p_clean):
                encontrados.append(m)

        if encontrados and len(encontrados) <= 4:
            linhas = []
            voice_parts = []
            for m in encontrados:
                qtd = float(m.get("quantidade") or 0)
                qtd_min = float(m.get("quantidade_minima") or 0)
                status_alerta = " ⚠️ *(Abaixo do mínimo!)*" if qtd <= qtd_min else " ✅ *(Normal)*"
                linhas.append(
                    f"• {m.get('emoji','📦')} **{m['nome']}**: **{m['quantidade']} {m['unidade']}** (Custo: {formatar_moeda(m['custo'])}){status_alerta}"
                )
                voice_parts.append(f"{m['nome']}: {m['quantidade']} {m['unidade']}")

            msg = "📦 **Consulta de Estoque:**\n\n" + "\n".join(linhas)
            voice = "Temos em estoque: " + ", ".join(voice_parts) + "."
            return {"reply": msg, "voice_text": voice, "suggestions": ["Dar baixa", "Ver alertas", "📄 Gerar PDF"]}

        total_itens = len(materiais)
        valor_total = sum(float(m.get("quantidade") or 0) * float(m.get("custo") or 0) for m in materiais)
        abaixo_min = [m for m in materiais if float(m.get("quantidade") or 0) <= float(m.get("quantidade_minima") or 0)]

        msg = (
            f"📦 **Visão Geral do Estoque do Ateliê**\n\n"
            f"• **Materiais Cadastrados**: {total_itens} insumos diferentes\n"
            f"• **Valor Total em Estoque**: **{formatar_moeda(valor_total)}**\n"
            f"• **Itens com Alerta (Abaixo do Mínimo)**: {len(abaixo_min)} item(ns)\n\n"
            f"Para consultar um material específico, diga: *\"Quanto couro temos?\"* ou *\"Qual o estoque de zíper?\"*."
        )
        voice = f"O estoque possui {total_itens} materiais cadastrados, somando {formatar_moeda(valor_total)}. Temos {len(abaixo_min)} itens abaixo do estoque mínimo."
        return {"reply": msg, "voice_text": voice, "suggestions": ["Quais itens estão acabando?", "Consultar couro", "Dar baixa", "📄 Gerar PDF"]}

    def _consultar_alertas_e_resumo(self) -> dict:
        materiais = self._carregar_materiais()
        pedidos = self._carregar_pedidos()

        criticos = [m for m in materiais if float(m.get("quantidade") or 0) <= float(m.get("quantidade_minima") or 0)]
        zerados = [m for m in materiais if float(m.get("quantidade") or 0) <= 0]
        pedidos_pendentes = [p for p in pedidos if p.get("status") in ("Pendente", "Em produção")]

        linhas = []
        if zerados:
            linhas.append("🚨 **Materiais Esgotados (Qtd 0):**")
            for z in zerados[:4]:
                linhas.append(f"• {z.get('emoji','❌')} **{z['nome']}**: 0 {z['unidade']}")
            linhas.append("")

        if criticos:
            linhas.append("⚠️ **Materiais Próximos do Fim (Abaixo do Mínimo):**")
            for c in criticos[:5]:
                linhas.append(f"• {c.get('emoji','⚠️')} **{c['nome']}**: **{c['quantidade']} {c['unidade']}** (mínimo: {c['quantidade_minima']} {c['unidade']})")
            linhas.append("")
        else:
            linhas.append("✅ **Estoque de Insumos Saudável**: Todos os materiais estão acima do nível mínimo!")
            linhas.append("")

        linhas.append(f"🧾 **Pedidos em Andamento**: {len(pedidos_pendentes)} pedido(s) pendentes ou em produção.")

        msg = "📊 **Diagnóstico e Alertas do Ateliê:**\n\n" + "\n".join(linhas)
        voice = f"Diagnóstico do ateliê: temos {len(criticos)} materiais em nível crítico e {len(pedidos_pendentes)} pedidos em andamento."
        return {"reply": msg, "voice_text": voice, "suggestions": ["Ver pedidos pendentes", "📄 Gerar PDF", "Consultar estoque"]}

    def _consultar_financeiro(self, p_clean: str) -> dict:
        pedidos = self._carregar_pedidos()
        despesas = self._carregar_despesas()

        receita_total = sum(float(p.get("valor_total") or 0) for p in pedidos if p.get("status") in ("Concluído", "Entregue"))
        receita_pendente = sum(float(p.get("valor_total") or 0) for p in pedidos if p.get("status") in ("Pendente", "Em produção"))
        despesa_total = sum(float(d.get("valor") or 0) for d in despesas)
        saldo_liquido = receita_total - despesa_total

        msg = (
            f"💰 **Resumo Financeiro do Ateliê**\n\n"
            f"• 📈 **Faturamento Realizado**: **{formatar_moeda(receita_total)}**\n"
            f"• ⏳ **Faturamento a Receber (Em produção)**: {formatar_moeda(receita_pendente)}\n"
            f"• 📉 **Total de Despesas**: **{formatar_moeda(despesa_total)}**\n"
            f"• 💵 **Saldo Líquido em Caixa**: **{formatar_moeda(saldo_liquido)}**\n\n"
            f"*{len(pedidos)} pedido(s) e {len(despesas)} despesa(s) registradas.*"
        )
        voice = f"O faturamento realizado é de {formatar_moeda(receita_total)}, com despesas de {formatar_moeda(despesa_total)}. O saldo líquido é de {formatar_moeda(saldo_liquido)}."
        return {"reply": msg, "voice_text": voice, "suggestions": ["📄 Enviar relatório em PDF", "Ver pedidos", "Cadastrar despesa"]}

    def _consultar_pedidos(self, p_clean: str) -> dict:
        pedidos = self._carregar_pedidos()
        if not pedidos:
            return {
                "reply": "🧾 Não há pedidos registrados no ateliê no momento.",
                "voice_text": "Não há pedidos registrados no momento.",
                "suggestions": ["➕ Criar novo pedido", "Ver produtos"]
            }

        pendentes = [p for p in pedidos if p.get("status") in ("Pendente", "Em produção")]
        concluidos = [p for p in pedidos if p.get("status") in ("Concluído", "Entregue")]

        if "pendente" in p_clean or "aberto" in p_clean or "producao" in p_clean:
            if not pendentes:
                return {
                    "reply": "🎉 Não há pedidos pendentes no momento! Todos os pedidos foram concluídos.",
                    "voice_text": "Não há pedidos pendentes.",
                    "suggestions": ["➕ Criar novo pedido", "Ver todos os pedidos"]
                }
            linhas = [f"• **{p['cliente']}**: {p['quantidade']}x {p['produto_emoji']} {p['produto_nome']} ({formatar_moeda(p['valor_total'])}) — Status: `{p['status']}`" for p in pendentes[:6]]
            msg = f"🧾 **Pedidos Pendentes e Em Produção ({len(pendentes)}):**\n\n" + "\n".join(linhas)
            voice = f"Temos {len(pendentes)} pedidos pendentes ou em produção no ateliê."
            return {"reply": msg, "voice_text": voice, "suggestions": ["➕ Criar novo pedido", "Concluir pedido", "Ver estoque"]}

        linhas = [f"• **{p['cliente']}**: {p['quantidade']}x {p['produto_emoji']} {p['produto_nome']} ({formatar_moeda(p['valor_total'])}) — `{p['status']}`" for p in pedidos[:5]]
        msg = (
            f"🧾 **Gestão de Pedidos do Ateliê**\n\n"
            f"• **Total de Pedidos**: {len(pedidos)} registros\n"
            f"• **Pendentes / Em Produção**: {len(pendentes)}\n"
            f"• **Concluídos / Entregues**: {len(concluidos)}\n\n"
            f"**Últimos Pedidos:**\n" + "\n".join(linhas)
        )
        voice = f"Temos {len(pedidos)} pedidos no total, sendo {len(pendentes)} pendentes e {len(concluidos)} concluídos."
        return {"reply": msg, "voice_text": voice, "suggestions": ["Ver pedidos pendentes", "➕ Criar novo pedido", "📄 Gerar PDF"]}

    def _consultar_produtos(self, p_clean: str) -> dict:
        produtos = self._carregar_produtos()
        if not produtos:
            return {
                "reply": "👜 Não há produtos ou bolsas cadastradas ainda.",
                "voice_text": "Não há produtos cadastrados ainda.",
                "suggestions": ["➕ Cadastrar bolsa", "Consultar estoque"]
            }

        linhas = []
        voice_items = []
        for p in produtos:
            prontas = int(p.get("estoque_pronto") or 0)
            tag_pronta = f" · 🛍️ **{prontas} pronta(s)**" if prontas > 0 else " · *(Sob encomenda)*"
            linhas.append(f"• {p.get('emoji','👜')} **{p['nome']}**: {formatar_moeda(p['preco_venda'])}{tag_pronta}")
            voice_items.append(f"{p['nome']} por {formatar_moeda(p['preco_venda'])}")

        msg = f"👜 **Catálogo de Bolsas e Produtos ({len(produtos)}):**\n\n" + "\n".join(linhas)
        voice = "Temos as seguintes bolsas cadastradas: " + ", ".join(voice_items[:4]) + "."
        return {"reply": msg, "voice_text": voice, "suggestions": ["➕ Criar pedido", "Adicionar peças prontas", "Ver estoque"]}

    def _consultar_sobras(self) -> dict:
        sobras = self._carregar_sobras()
        if not sobras:
            return {
                "reply": "♻️ Não há retalhos ou sobras registrados no momento.",
                "voice_text": "Não há sobras ou retalhos registrados.",
                "suggestions": ["Cadastrar sobra", "Consultar estoque"]
            }

        disponiveis = [s for s in sobras if s.get("status") in ("Disponível", "Disponivel", None)]
        linhas = [f"• ✂️ **{s['descricao']}**: {s['quantidade']} {s['unidade']}" for s in disponiveis[:5]]

        msg = f"♻️ **Sobras e Retalhos Disponíveis ({len(disponiveis)}):**\n\n" + "\n".join(linhas)
        voice = f"Temos {len(disponiveis)} lotes de retalhos disponíveis para reaproveitamento."
        return {"reply": msg, "voice_text": voice, "suggestions": ["Cadastrar sobra", "Consultar estoque"]}

    def _consultar_usuarios(self) -> dict:
        usuarios = self._carregar_usuarios()
        linhas = [f"• 👤 **{u.get('nome') or u['username']}** (`{u['username']}`) — Papel: `{u.get('role') or 'Colaborador'}`" for u in usuarios]
        msg = f"👥 **Usuários Cadastrados no Sistema ({len(usuarios)}):**\n\n" + "\n".join(linhas)
        voice = f"Existem {len(usuarios)} usuários cadastrados no sistema."
        return {"reply": msg, "voice_text": voice, "suggestions": ["Minhas permissões", "Cadastrar usuário"]}
