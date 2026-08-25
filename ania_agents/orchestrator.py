"""
ania_agents/orchestrator.py - Orquestrador Central Multi-Agente (Maestro)
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
import unicodedata
from typing import Dict, Any, Optional, List

from ania_agents.base import BaseSpecialistAgent, AgentResponse
from ania_agents.inventory_agent import InventoryAgent
from ania_agents.orders_agent import OrdersAgent
from ania_agents.finance_agent import FinanceAgent
from ania_agents.reports_agent import ReportsAgent
from ania_agents.crafts_agent import CraftsAgent
from ania_agents.admin_agent import AdminAgent
from ania_agents.tool_registry import ToolRegistry


def remover_acentos(texto: str) -> str:
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()


class MultiAgentOrchestrator:
    """
    Maestro / Orquestrador Inteligente Multi-Agente.
    Coordena os agentes especialistas, roteia intenções e executa ferramentas no sistema.
    """

    def __init__(self, assistant_context=None, ollama_engine=None):
        self.assistant = assistant_context
        self.ollama = ollama_engine
        self.registry = ToolRegistry()

        # Instanciação dos agentes especialistas
        self.specialists: List[BaseSpecialistAgent] = [
            InventoryAgent(assistant_context=assistant_context),
            OrdersAgent(assistant_context=assistant_context),
            FinanceAgent(assistant_context=assistant_context),
            ReportsAgent(assistant_context=assistant_context),
            CraftsAgent(assistant_context=assistant_context),
            AdminAgent(assistant_context=assistant_context),
        ]

    def route_and_process(
        self,
        prompt: str,
        context: Dict[str, Any],
        user: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        """
        Analisa o prompt do usuário, pontua os especialistas e despacha para o melhor agente.
        """
        p_clean = remover_acentos(prompt)

        # ── 1. VERIFICAÇÃO DE PERGUNTAS / COMANDOS COMPOSTOS (DECOMPOSIÇÃO) ──
        # Exemplo: "Vendi 2 bolsas e preciso cadastrar o pedido e dar baixa de 4 courino"
        if " e " in p_clean and any(w in p_clean for w in ["pedido", "encomenda"]) and any(w in p_clean for w in ["dar baixa", "baixa", "consumi", "gastei"]):
            # Divide e processa com Orders e Inventory
            orders_agent = next((s for s in self.specialists if isinstance(s, OrdersAgent)), None)
            inv_agent = next((s for s in self.specialists if isinstance(s, InventoryAgent)), None)

            if orders_agent and inv_agent:
                resp_ord = orders_agent.process(prompt, p_clean, context, user, history)
                resp_inv = inv_agent.process(prompt, p_clean, context, user, history)

                sub_actions = []
                if resp_ord.action:
                    sub_actions.append({"action": resp_ord.action, "params": resp_ord.params})
                if resp_inv.action:
                    if resp_inv.action == "acoes_em_lote":
                        sub_actions.extend(resp_inv.params.get("acoes", []))
                    else:
                        sub_actions.append({"action": resp_inv.action, "params": resp_inv.params})

                if len(sub_actions) >= 2:
                    return AgentResponse(
                        handled=True,
                        action="acoes_em_lote",
                        params={"acoes": sub_actions},
                        confidence=0.98,
                        agent_name="orchestrator_multi_agent",
                        sub_tasks=sub_actions
                    )

        # ── 2. SELEÇÃO DO ESPECIALISTA COM MAIOR AFINIDADE ──
        scored_specialists = []
        for specialist in self.specialists:
            score = specialist.can_handle(p_clean, context)
            scored_specialists.append((score, specialist))

        scored_specialists.sort(key=lambda x: x[0], reverse=True)
        best_score, best_specialist = scored_specialists[0]

        # Se houver especialista com score positivo, processa por ele
        if best_score > 0.0:
            resp = best_specialist.process(prompt, p_clean, context, user, history)
            if resp.handled:
                return resp

        # ── 3. TENTATIVA COM DEMAIS ESPECIALISTAS COMO FALLBACK ──
        for score, specialist in scored_specialists:
            if specialist == best_specialist:
                continue
            resp = specialist.process(prompt, p_clean, context, user, history)
            if resp.handled:
                return resp

        # ── 4. RESPOSTA PADRÃO SE NENHUM ESPECIALISTA RECONHECER AÇÃO ESPECÍFICA ──
        user_name = user.get("username", "Artesã(o)").capitalize()
        default_text = (
            f"Olá, {user_name}! Sou a **Ania**, sua assistente inteligente especializada no Ateliê Haiti. ✨\n\n"
            f"Posso ajudar com:\n"
            f"• 📦 **Estoque**: Entradas, baixas, sobras e alertas de materiais.\n"
            f"• 📋 **Pedidos**: Cadastro de encomendas, fluxo produtivo e leitor GTIN.\n"
            f"• 💰 **Financeiro**: Precificação, despesas, faturamento e margem de lucro.\n"
            f"• 📊 **Relatórios**: PDFs personalizados, planilhas Excel e backups.\n"
            f"• 🧵 **Consultoria Técnica**: Dicas de costura, agulhas, linhas e courinos."
        )
        return AgentResponse(
            handled=True,
            action=None,
            text_response=default_text,
            confidence=0.85,
            agent_name="orchestrator_general"
        )
