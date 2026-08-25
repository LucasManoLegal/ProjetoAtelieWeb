"""
ania_agents/finance_agent.py - Agente Especialista em Finanças, Precificação e Custos
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
from typing import Dict, Any, Optional, List
from ania_agents.base import BaseSpecialistAgent, AgentResponse


class FinanceAgent(BaseSpecialistAgent):
    """
    Especialista em Gestão Financeira, Despesas, Precificação com Markup, Lucro e Simulações de Custo.
    """

    def __init__(self, assistant_context=None):
        super().__init__(
            name="finance_specialist",
            description="Especialista em cálculos de precificação, fluxo de caixa, despesas, faturamento e margem de lucro por peça.",
            assistant_context=assistant_context,
            model_preference="qwen2.5:7b"
        )

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        keywords = [
            "financeiro", "financas", "despesa", "despesas", "gasto", "gastos",
            "faturamento", "receita", "lucro", "margem", "markup", "custo",
            "preco", "precificacao", "quanto cobrar", "lucro liquido", "caixa"
        ]
        score = 0.0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", prompt_clean):
                score += 0.25
        if any(w in prompt_clean for w in ["cadastrar despesa", "quanto cobrar", "calcular lucro", "margem de lucro", "faturamento"]):
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

        # ── 1. CADASTRAR DESPESA ──
        if any(w in prompt_clean for w in ["cadastrar despesa", "nova despesa", "adicionar despesa", "gastei com", "pagamento de", "paguei"]):
            desc = self._extract_expense_desc(prompt)
            valor = self._extract_price(prompt_clean, default=0.0)
            cat = "Geral"
            for c in ["Materiais", "Equipamentos", "Manutenção", "Luz", "Internet", "Frete", "Marketing", "Outros"]:
                if self._clean_str(c) in prompt_clean:
                    cat = c
                    break
            return AgentResponse(
                handled=True,
                action="cadastrar_despesa",
                params={"descricao": desc or "Despesa do Ateliê", "valor": valor, "categoria": cat},
                confidence=0.97,
                agent_name=self.name
            )

        # ── 2. EXCLUIR DESPESA ──
        if any(w in prompt_clean for w in ["excluir despesa", "apagar despesa", "deletar despesa", "remover despesa"]):
            desc = self._extract_expense_desc(prompt)
            return AgentResponse(
                handled=True,
                action="excluir_despesa",
                params={"descricao": desc or "Despesa"},
                confidence=0.96,
                agent_name=self.name
            )

        # ── 3. CÁLCULO DE PRECIFICAÇÃO / QUANTO COBRAR ──
        if any(w in prompt_clean for w in ["quanto cobrar", "quanto devo cobrar", "quanto posso cobrar", "calcular preco", "precificar", "precificacao", "qual preco", "margem de lucro", "margem"]):
            prod_match = self._find_best_match(prompt_clean, produtos_nomes)
            margem = 50.0  # default 50%
            m_pct = re.search(r"(\d+)\s*%", prompt_clean)
            if m_pct:
                margem = float(m_pct.group(1))

            return AgentResponse(
                handled=True,
                action="calcular_precificacao",
                params={"produto": prod_match or (produtos_nomes[0] if produtos_nomes else "Bolsa"), "margem_desejada": margem},
                confidence=0.95,
                agent_name=self.name
            )

        # ── 4. CONSULTA FINANCEIRA / BALANÇO ──
        if any(w in prompt_clean for w in ["financeiro", "faturamento", "lucro", "balanco", "fluxo de caixa", "quanto ganhamos", "quanto entrou", "quanto faturou"]):
            periodo = "mes"
            if "hoje" in prompt_clean: periodo = "hoje"
            elif "semana" in prompt_clean: periodo = "semana"
            elif "ano" in prompt_clean: periodo = "ano"
            return AgentResponse(
                handled=True,
                action="consultar_financeiro",
                params={"periodo": periodo},
                confidence=0.95,
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

    def _extract_price(self, text: str, default: float = 0.0) -> float:
        m = re.search(r"r\$\s*(\d+(?:[\.,]\d+)?)", text, re.IGNORECASE)
        if m:
            try: return float(m.group(1).replace(",", "."))
            except ValueError: pass
        m2 = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:reais|reais\b)", text, re.IGNORECASE)
        if m2:
            try: return float(m2.group(1).replace(",", "."))
            except ValueError: pass
        m3 = re.search(r"(\d+(?:[\.,]\d+)?)", text)
        if m3:
            try: return float(m3.group(1).replace(",", "."))
            except ValueError: pass
        return default

    def _extract_expense_desc(self, text: str) -> Optional[str]:
        t_clean = self._clean_str(text)
        for trig in ["despesa de", "despesa", "gasto com", "gasto de", "paguei", "pagamento de"]:
            if trig in t_clean:
                idx = t_clean.find(trig) + len(trig)
                sub = text[idx:].strip()
                sub = re.split(r"[,;\.\n]| no valor | de r\$ | r\$ | valor ", sub)[0].strip()
                if sub: return sub.capitalize()
        return None
