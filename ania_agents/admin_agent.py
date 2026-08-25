"""
ania_agents/admin_agent.py - Agente Especialista em Administração, Usuários e RBAC
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
from typing import Dict, Any, Optional, List
from ania_agents.base import BaseSpecialistAgent, AgentResponse


class AdminAgent(BaseSpecialistAgent):
    """
    Especialista em Gestão de Usuários, Papéis, Permissões (RBAC) e Auditoria de Acessos.
    """

    def __init__(self, assistant_context=None):
        super().__init__(
            name="admin_specialist",
            description="Especialista em administração do sistema, consulta de usuários cadastrados e controle de permissões por abas.",
            assistant_context=assistant_context,
            model_preference="qwen2.5:7b"
        )

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        keywords = [
            "usuario", "usuarios", "permissao", "permissoes", "papel", "papeis",
            "role", "roles", "admin", "administrador", "operador", "bloqueio", "acesso"
        ]
        score = 0.0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", prompt_clean):
                score += 0.3
        if any(w in prompt_clean for w in ["listar usuarios", "quem tem acesso", "minhas permissoes", "consultar usuarios"]):
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
        # ── 1. CONSULTAR USUÁRIOS ──
        if any(w in prompt_clean for w in ["usuarios", "quem tem acesso", "listar usuarios", "ver usuarios"]):
            return AgentResponse(
                handled=True,
                action="consultar_usuarios",
                params={},
                confidence=0.96,
                agent_name=self.name
            )

        # ── 2. CONSULTAR PRÓPRIAS PERMISSÕES ──
        if any(w in prompt_clean for w in ["minhas permissoes", "meu papel", "meu acesso", "o que posso fazer"]):
            user_nome = user.get("username", "Usuário")
            roles = user.get("roles") or user.get("role") or ["Admin"]
            roles_str = ", ".join(roles) if isinstance(roles, list) else str(roles)
            resp = (
                f"🛡️ **Status de Acesso do Usuário:**\n\n"
                f"• **Usuário**: `{user_nome}`\n"
                f"• **Papéis Atribuídos**: `{roles_str}`\n\n"
                f"Você tem acesso às ferramentas e operações autorizadas para o seu perfil no sistema."
            )
            return AgentResponse(
                handled=True,
                action="consultoria_tecnica",
                text_response=resp,
                confidence=0.98,
                agent_name=self.name
            )

        return AgentResponse(handled=False, agent_name=self.name)
