"""
ania_agents/reports_agent.py - Agente Especialista em Relatórios, Exportações e Backups
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
from typing import Dict, Any, Optional, List
from ania_agents.base import BaseSpecialistAgent, AgentResponse


class ReportsAgent(BaseSpecialistAgent):
    """
    Especialista na Geração de Relatórios Visuais em PDF, Planilhas Excel (.xlsx) e Backups JSON.
    """

    def __init__(self, assistant_context=None):
        super().__init__(
            name="reports_specialist",
            description="Especialista em emissão de relatórios formais em PDF com layout profissional, exportação de planilhas e backup de dados.",
            assistant_context=assistant_context,
            model_preference="qwen2.5:7b"
        )

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        keywords = [
            "relatorio", "relatorios", "pdf", "excel", "planilha", "exportar",
            "download", "imprimir", "backup", "salvar dados", "copia de seguranca"
        ]
        score = 0.0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", prompt_clean):
                score += 0.3
        if any(w in prompt_clean for w in ["gerar relatorio", "baixar pdf", "exportar excel", "gerar backup"]):
            score += 0.6
        return min(score, 1.0)

    def process(
        self,
        prompt: str,
        prompt_clean: str,
        context: Dict[str, Any],
        user: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        # ── 1. GERAR RELATÓRIO PDF ──
        if "pdf" in prompt_clean or any(w in prompt_clean for w in ["imprimir", "gerar relatorio", "relatorio completo", "relatorio geral"]):
            return AgentResponse(
                handled=True,
                action="gerar_relatorio_pdf",
                params={"tipo": "completo"},
                confidence=0.98,
                agent_name=self.name
            )

        # ── 2. EXPORTAR PLANILHA EXCEL ──
        if "excel" in prompt_clean or "planilha" in prompt_clean or "xlsx" in prompt_clean or "tabela" in prompt_clean:
            return AgentResponse(
                handled=True,
                action="exportar_excel",
                params={},
                confidence=0.98,
                agent_name=self.name
            )

        # ── 3. GERAR BACKUP DE DADOS ──
        if "backup" in prompt_clean or "copia de seguranca" in prompt_clean:
            return AgentResponse(
                handled=True,
                action="gerar_backup",
                params={},
                confidence=0.98,
                agent_name=self.name
            )

        return AgentResponse(handled=False, agent_name=self.name)
