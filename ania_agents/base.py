"""
ania_agents/base.py - Classe Base e Estruturas de Dados para Agentes Especialistas
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class AgentResponse:
    """Estrutura padrão de retorno para qualquer agente especialista."""
    handled: bool = False
    action: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    text_response: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    agent_name: str = "base"
    sub_tasks: List[Dict[str, Any]] = field(default_factory=list)
    needs_confirmation: bool = False
    confirmation_message: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "handled": self.handled,
            "action": self.action,
            "params": self.params,
            "text_response": self.text_response,
            "data": self.data,
            "confidence": self.confidence,
            "agent_name": self.agent_name,
            "needs_confirmation": self.needs_confirmation,
        }
        if self.confirmation_message:
            res["confirmation_message"] = self.confirmation_message
        if self.sub_tasks:
            res["sub_tasks"] = self.sub_tasks
        if self.extra:
            res.update(self.extra)
        return res


class BaseSpecialistAgent:
    """
    Classe base para agentes especialistas da Ania.
    Cada agente é responsável por um domínio específico do Ateliê.
    """

    def __init__(self, name: str, description: str, assistant_context=None, model_preference: str = "qwen2.5:3b"):
        self.name = name
        self.description = description
        self.assistant = assistant_context
        self.model_preference = model_preference

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        """
        Retorna a pontuação de afinidade (0.0 a 1.0) para saber se este agente
        é o mais qualificado para lidar com o comando/pergunta.
        """
        raise NotImplementedError

    def process(self, prompt: str, prompt_clean: str, context: Dict[str, Any], user: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> AgentResponse:
        """
        Processa a solicitação do usuário e retorna um AgentResponse.
        """
        raise NotImplementedError
