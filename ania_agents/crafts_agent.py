"""
ania_agents/crafts_agent.py - Agente Consultor Técnico em Costura, Modelagem e Artesanato
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
from typing import Dict, Any, Optional, List
from ania_agents.base import BaseSpecialistAgent, AgentResponse


class CraftsAgent(BaseSpecialistAgent):
    """
    Consultor Especialista em Técnicas de Costura Criativa, Modelagem, Estruturação de Bolsas,
    Tipos de Courino, Linhas, Agulhas e Combinações de Tecidos.
    """

    def __init__(self, assistant_context=None):
        super().__init__(
            name="crafts_consultant",
            description="Consultor técnico de corte, costura, agulhas, linhas, estruturadores (EVA, TNT, Espuma) e acabamentos para ateliê.",
            assistant_context=assistant_context,
            model_preference="mistral:7b"
        )

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        # Se for comando de alteração ou operação do sistema, deixe para os agentes operacionais
        if any(w in prompt_clean for w in ["editar", "alterar", "mudar", "cadastrar", "excluir", "remover", "dar baixa", "dar entrada", "ajustar", "preco", "custo", "quanto cobrar", "quantas", "quantos", "quanto material", "capacidade", "comprar", "compras", "preciso comprar", "estoque"]):
            return 0.0

        keywords = [
            "costura", "costurar", "agulha", "linha", "calcador", "pe calcador",
            "teflon", "courino", "sintetico", "estruturador", "dublagem", "espuma",
            "eva", "tnt", "forro", "nylon 600", "oxford", "ziper", "cursor",
            "combinacao", "cores", "como fazer", "qual melhor", "dica", "sugestao",
            "modelagem", "acabamento", "vivo", "debrum"
        ]
        score = 0.0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", prompt_clean):
                score += 0.25
        if any(w in prompt_clean for w in ["como costurar", "qual agulha", "qual linha", "qual estruturador", "qual forro"]):
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
        user_name = user.get("username", "Artesã(o)").capitalize()

        # ── 1. DÚVIDAS SOBRE AGULHAS E LINHAS PARA COURINO / SINTÉTICO ──
        if any(w in prompt_clean for w in ["agulha", "linha", "ponto"]) and any(w in prompt_clean for w in ["courino", "sintetico", "couro"]):
            resp = (
                f"🧵 **Dica Técnica de Costura da Ania para você, {user_name}!**\n\n"
                f"Para costurar **courino e materiais sintéticos** com perfeição e sem danificar o material:\n"
                f"1. **Agulha Ideal**: Use agulhas de ponta seta ou lança (nº 14 para sintéticos finos 0.8mm ou nº 16/18 para peças mais grossas e camadas duplas).\n"
                f"2. **Linha**: Recomendamos **Linha de Poliamida (Nylon) nº 60** para costura estruturada e nº 40 para pesponto decorativo.\n"
                f"3. **Calcador**: Utilize sempre um **calcador de Teflon ou rolete** para que o sintético deslize suavemente sem prender ou repuxar o ponto.\n"
                f"4. **Comprimento do Ponto**: Mantenha o ponto mais largo (entre 3.5mm e 4.5mm) para não perfurar demais o sintético e evitar rasgos."
            )
            return AgentResponse(
                handled=True,
                action="consultoria_tecnica",
                text_response=resp,
                confidence=0.98,
                agent_name=self.name
            )

        # ── 2. DÚVIDAS SOBRE ESTRUTURADORES E FORROS ──
        if any(w in prompt_clean for w in ["estruturador", "estruturar", "forro", "dublar", "espuma", "eva", "tnt"]):
            resp = (
                f"✨ **Guia de Estruturação e Forro de Bolsas:**\n\n"
                f"• **Bolsas Firmes (Ex: Maletas / Clutch)**: Use **EVA 1.5mm ou 2mm** ou **Papelão Couro/Salpa** colado na peça.\n"
                f"• **Bolsas com Toque Fofo e Maleável**: Use **Espuma Torneada 3mm ou 4mm** (com acoplado/TNT).\n"
                f"• **Nécessaires e Peças Leves**: **TNT 120g** ou **Nylon 600** como forro estruturado.\n"
                f"• **Melhores Forros**: **Bagum** (fácil de limpar e impermeável), **Nylon 70 (Resinado)** ou **Cetim Dublado no TNT** para acabamento nobre."
            )
            return AgentResponse(
                handled=True,
                action="consultoria_tecnica",
                text_response=resp,
                confidence=0.98,
                agent_name=self.name
            )

        # ── 3. COMBINAÇÃO DE CORES E TENDÊNCIAS ──
        if any(w in prompt_clean for w in ["combinacao", "cores", "tendencia", "colecao", "paleta"]):
            resp = (
                f"🎨 **Sugestão de Paleta de Cores e Materiais da Ania:**\n\n"
                f"1. **Elegância Atemporal**: Courino Caramelo / Pinhão combinado com metais dourados e forro bege claro ou xadrez refinado.\n"
                f"2. **Minimalista Urbano**: Preto fosco com metais grafite/ônix e detalhes em gorgurão listrado.\n"
                f"3. **Primavera / Verão**: Tons pastel (Verde Menta, Lavanda ou Areia) combinados com metais níquel e puxadores em tassel.\n\n"
                f"💡 *Dica*: Sempre mantenha a cor do zíper combinando com a cor predominante do corpo da bolsa ou em contraste intencional de 1 tom."
            )
            return AgentResponse(
                handled=True,
                action="consultoria_tecnica",
                text_response=resp,
                confidence=0.97,
                agent_name=self.name
            )

        # ── 4. RESPOSTA CONSULTIVA GERAL SE FOR DÚVIDA DE COSTURA ──
        if any(w in prompt_clean for w in ["dica", "sugestao", "como costurar", "costura", "artesanato", "tecido", "modelagem"]):
            resp = (
                f"✂️ **Consultoria de Ateliê da Ania:**\n\n"
                f"Como sua especialista em artesanato e costura criativa, posso te orientar sobre escolha de tecidos, "
                f"regulagem de tensão da máquina, cálculo de consumo de forro, uso de vivos e acabamentos em debrum!\n"
                f"Pode me perguntar sobre qualquer técnica ou material que estiver utilizando no momento."
            )
            return AgentResponse(
                handled=True,
                action="consultoria_tecnica",
                text_response=resp,
                confidence=0.90,
                agent_name=self.name
            )

        return AgentResponse(handled=False, agent_name=self.name)
