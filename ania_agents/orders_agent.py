"""
ania_agents/orders_agent.py - Agente Especialista em Pedidos, Produção e Estoque Acabado
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
from typing import Dict, Any, Optional, List
from ania_agents.base import BaseSpecialistAgent, AgentResponse


class OrdersAgent(BaseSpecialistAgent):
    """
    Especialista em Gestão de Pedidos, Encomendas de Clientes, Status do Fluxo Produtivo e Estoque Pronto.
    """

    def __init__(self, assistant_context=None):
        super().__init__(
            name="orders_specialist",
            description="Especialista em criação de pedidos, acompanhamento de prazos, alteração de status e controle de estoque de produtos acabados.",
            assistant_context=assistant_context,
            model_preference="qwen2.5:7b"
        )

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        keywords = [
            "pedido", "pedidos", "encomenda", "encomendas", "cliente", "clientes",
            "producao", "produzindo", "pronto", "entregar", "entregue", "concluido",
            "cancelado", "gtin", "codigo de barras", "estoque pronto", "acabado"
        ]
        score = 0.0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", prompt_clean):
                score += 0.2
        if any(w in prompt_clean for w in ["criar pedido", "novo pedido", "mudar status", "concluir pedido", "estoque pronto"]):
            score += 0.5
        return min(score, 1.0)

    def process(
        self,
        prompt: str,
        prompt_clean: str,
        context: Dict[str, Any],
        user: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        produtos_nomes = context.get("produtos_nomes", [])
        produtos_lista = context.get("produtos", [])

        # ── 1. CRIAR NOVO PEDIDO / ENCOMENDA ──
        gatilhos_novo_pedido = [
            "criar pedido", "criar um pedido", "crie um pedido", "crie o pedido", "novo pedido", "pedido novo",
            "adicionar pedido", "adicione um pedido", "adicionar um pedido", "cadastrar pedido", "fazer pedido",
            "fazer um pedido", "faca um pedido", "registrar pedido", "registre um pedido", "recebi um pedido",
            "recebemos um pedido", "temos um pedido", "cliente pediu", "ela pediu", "ele pediu", "pediram",
            "fazer uma encomenda", "nova encomenda", "encomenda nova", "anotar pedido"
        ]
        if any(w in prompt_clean for w in gatilhos_novo_pedido):
            qtd_m = re.search(r"(\d+)\s*(?:unidades?|pecas?|bolsas?|x)?", prompt_clean)
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
                prod_match = self._find_best_match(prompt_clean, produtos_nomes)

            # Cliente
            cliente = self._extract_client_name(prompt)

            return AgentResponse(
                handled=True,
                action="criar_pedido",
                params={
                    "cliente": cliente,
                    "produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Bolsa"),
                    "quantidade": qtd
                },
                confidence=0.98,
                agent_name=self.name
            )

        # ── 2. MUDAR STATUS DE PEDIDO ──
        if any(w in prompt_clean for w in ["mudar status", "alterar status", "passar pedido", "mude o pedido", "coloque o pedido", "marcar pedido", "atualizar status", "concluir pedido", "finalizar pedido", "entregar pedido", "cancelar pedido"]):
            # Determina o novo status
            novo_status = "Em Produção"
            if any(w in prompt_clean for w in ["concluido", "concluir", "finalizado", "pronto", "terminado", "terminou"]):
                novo_status = "Concluído"
            elif any(w in prompt_clean for w in ["entregue", "entregar", "enviado", "despachado"]):
                novo_status = "Entregue"
            elif any(w in prompt_clean for w in ["cancelado", "cancelar", "desistiu"]):
                novo_status = "Cancelado"
            elif any(w in prompt_clean for w in ["aguardando", "pendente", "fila", "espera"]):
                novo_status = "Pendente"
            elif any(w in prompt_clean for w in ["producao", "produzindo", "cortando", "costurando"]):
                novo_status = "Em Produção"

            cliente = self._extract_client_name(prompt)
            return AgentResponse(
                handled=True,
                action="mudar_status_pedido",
                params={"cliente": cliente, "novo_status": novo_status},
                confidence=0.97,
                agent_name=self.name
            )

        # ── 3. EXCLUIR PEDIDO ──
        if any(w in prompt_clean for w in ["excluir pedido", "apagar pedido", "deletar pedido", "remover pedido"]):
            cliente = self._extract_client_name(prompt)
            return AgentResponse(
                handled=True,
                action="excluir_pedido",
                params={"cliente": cliente},
                confidence=0.96,
                agent_name=self.name
            )

        # ── 4. CONTROLE DE ESTOQUE DE PRODUTOS ACABADOS (ESTOQUE PRONTO) ──
        if any(w in prompt_clean for w in ["estoque pronto", "produtos prontos", "pecas prontas", "pronta entrega"]):
            prod_match = self._find_best_match(prompt_clean, produtos_nomes)
            qtd = int(self._extract_number(prompt_clean, default=1.0))
            op = "entrada"
            if any(w in prompt_clean for w in ["saida", "saiu", "vendi", "entreguei"]):
                op = "saida"
            elif any(w in prompt_clean for w in ["definir", "zerar", "ajustar para"]):
                op = "definir"
            return AgentResponse(
                handled=True,
                action="ajustar_estoque_pronto",
                params={"produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Produto"), "quantidade": qtd, "operacao": op},
                confidence=0.96,
                agent_name=self.name
            )

        # ── 5. CADASTRAR PRODUTO NO CATÁLOGO ──
        if any(w in prompt_clean for w in ["cadastrar produto", "novo produto", "adicionar produto", "criar produto", "cadastrar bolsa", "nova bolsa", "adicionar bolsa", "criar bolsa"]):
            nome_cand = self._extract_product_name(prompt)
            preco = self._extract_price(prompt_clean, default=0.0)
            return AgentResponse(
                handled=True,
                action="cadastrar_produto",
                params={"nome": nome_cand or "Nova Bolsa", "preco_venda": preco, "categoria": "Bolsa", "emoji": "👜", "estoque_pronto": 0},
                confidence=0.98,
                agent_name=self.name
            )

        # ── EDITAR PRODUTO / RECEITA DE MATERIAIS ──
        if any(w in prompt_clean for w in ["editar bolsa", "alterar bolsa", "editar produto", "alterar produto", "editar receita", "alterar receita", "mudar receita", "alterar materiais", "mudar materiais", "trocar materiais", "materiais da bolsa", "editar materiais", "alterar materiais da"]):
            prod_match = self._find_best_match(prompt_clean, produtos_nomes)
            return AgentResponse(
                handled=True,
                action="editar_produto",
                params={"produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Bolsa")},
                confidence=0.98,
                agent_name=self.name
            )

        # ── 6. EXCLUIR PRODUTO DO CATÁLOGO ──
        if any(w in prompt_clean for w in ["excluir produto", "apagar produto", "deletar produto", "remover produto", "excluir bolsa"]):
            prod_match = self._find_best_match(prompt_clean, produtos_nomes)
            return AgentResponse(
                handled=True,
                action="excluir_produto",
                params={"produto": prod_match or "Produto"},
                confidence=0.95,
                agent_name=self.name
            )

        # ── 7. CONSULTAR PEDIDOS ──
        if any(w in prompt_clean for w in ["consultar pedido", "ver pedidos", "listar pedidos", "pedidos pendentes", "pedidos atrasados", "quais pedidos"]):
            status_filtro = None
            if "pendente" in prompt_clean: status_filtro = "Pendente"
            elif "producao" in prompt_clean: status_filtro = "Em Produção"
            elif "concluido" in prompt_clean: status_filtro = "Concluído"
            elif "entregue" in prompt_clean: status_filtro = "Entregue"
            return AgentResponse(
                handled=True,
                action="consultar_pedidos",
                params={"status": status_filtro},
                confidence=0.94,
                agent_name=self.name
            )

        return AgentResponse(handled=False, agent_name=self.name)

    def _clean_str(self, text: str) -> str:
        if not text: return ""
        import unicodedata
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

    def _find_best_match(self, text_clean: str, options: List[str]) -> Optional[str]:
        if not options: return None
        text_c = self._clean_str(text_clean)
        for opt in options:
            if self._clean_str(opt) in text_c:
                return opt
        words_input = set(text_c.split())
        best_opt = None
        best_count = 0
        for opt in options:
            opt_words = set(self._clean_str(opt).split())
            common = words_input.intersection(opt_words)
            if len(common) > best_count:
                best_count = len(common)
                best_opt = opt
        return best_opt if best_count > 0 else (options[0] if options else None)

    def _extract_number(self, text: str, default: float = 1.0) -> float:
        m = re.search(r"(\d+(?:[\.,]\d+)?)", text)
        if m:
            try: return float(m.group(1).replace(",", "."))
            except ValueError: pass
        return default

    def _extract_price(self, text: str, default: float = 0.0) -> float:
        m = re.search(r"r\$\s*(\d+(?:[\.,]\d+)?)", text, re.IGNORECASE)
        if m:
            try: return float(m.group(1).replace(",", "."))
            except ValueError: pass
        return self._extract_number(text, default)

    def _extract_client_name(self, text: str) -> str:
        c_match = re.search(
            r"(?:para\s+a|para\s+o|para|de\s+uma\s+cliente\s+chamada|de\s+um\s+cliente\s+chamado|cliente\s+chamada|cliente\s+chamado|da\s+cliente|do\s+cliente|cliente)\s+([A-Za-zÀ-ÖØ-öø-ÿ\s]+?)(?:\s*,|\s+ela\b|\s+ele\b|\s+que\b|\s+pediu\b|\s+de\b|\s+da\b|\s+do\b|\s+com\b|\.|$|\n)",
            text,
            re.IGNORECASE
        )
        if c_match:
            cand = c_match.group(1).strip()
            for noise in ["chamada", "chamado", "uma", "um", "cliente", "nova", "novo"]:
                cand = re.sub(r"^\b" + noise + r"\b\s*", "", cand, flags=re.IGNORECASE).strip()
            if len(cand) > 1 and cand.lower() not in ["bolsa", "pedido", "encomenda", "mochila", "carteira"]:
                return cand.title()
        return "Cliente Balcão"

    def _extract_product_name(self, text: str) -> Optional[str]:
        t_clean = self._clean_str(text)
        for trig in ["cadastrar bolsa", "nova bolsa", "criar bolsa", "adicionar bolsa", "cadastrar produto", "novo produto", "criar produto", "produto chamado", "bolsa chamada", "produto", "bolsa"]:
            if trig in t_clean:
                idx = t_clean.find(trig) + len(trig)
                sub = text[idx:].strip()
                sub = re.split(r"[,;\.\n]| com | por | valor | preco ", sub)[0].strip()
                if sub: return sub.title()
        return None
