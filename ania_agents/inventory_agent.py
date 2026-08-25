"""
ania_agents/inventory_agent.py - Agente Especialista em Estoque, Materiais e Sobras
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import re
from typing import Dict, Any, Optional, List
from ania_agents.base import BaseSpecialistAgent, AgentResponse


class InventoryAgent(BaseSpecialistAgent):
    """
    Especialista em Estoque, Matérias-Primas, Entradas, Baixas, Sobras e Alertas.
    """

    def __init__(self, assistant_context=None):
        super().__init__(
            name="inventory_specialist",
            description="Especialista em controle de materiais, estoque mínimo, entradas, baixas e aproveitamento de sobras.",
            assistant_context=assistant_context,
            model_preference="qwen2.5:7b"
        )

    def can_handle(self, prompt_clean: str, context: Dict[str, Any]) -> float:
        keywords = [
            "material", "materiais", "estoque", "entrada", "baixa", "baixar", "chegou",
            "sobra", "sobras", "retalho", "retalhos", "repor", "consumi", "gastei",
            "usei", "metros", "rolo", "unidade", "unidades", "comprar", "falta"
        ]
        score = 0.0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", prompt_clean):
                score += 0.2
        if any(w in prompt_clean for w in ["dar entrada", "dar baixa", "cadastrar material", "excluir material", "sobra", "quantas", "quantos", "capacidade", "o que preciso comprar", "lista de compras", "quanto material preciso"]):
            score += 0.7
        return min(score, 1.0)

    def process(
        self,
        prompt: str,
        prompt_clean: str,
        context: Dict[str, Any],
        user: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        materiais_nomes = context.get("materiais_nomes", [])
        materiais_lista = context.get("materiais", [])

        # ── 1. OPERAÇÕES EM LOTE DE ENTRADA OU BAIXA ──
        if (" e " in prompt_clean or "," in prompt) and any(w in prompt_clean for w in ["dar entrada", "chegou", "adicionar", "dar baixa", "usei", "consumi"]):
            is_entrada = any(w in prompt_clean for w in ["dar entrada", "chegou", "adicionar", "repor", "entrada", "comprei"])
            pedacos = re.split(r",|\be\b", prompt)
            lote_acoes = []
            for ped in pedacos:
                ped_c = self._clean_str(ped)
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
                return AgentResponse(
                    handled=True,
                    action="acoes_em_lote",
                    params={"acoes": lote_acoes},
                    confidence=0.98,
                    agent_name=self.name,
                    sub_tasks=lote_acoes
                )

        # ── 2. DAR BAIXA EM MATERIAL ──
        if any(w in prompt_clean for w in ["dar baixa", "baixa de", "baixar", "usei", "consumi", "gastei", "retirar do estoque", "saida de"]):
            mat_match = self._find_best_match(prompt_clean, materiais_nomes)
            qtd = self._extract_number(prompt_clean, default=1.0)
            motivo = "Uso em produção"
            if "motivo" in prompt_clean:
                m_mot = re.search(r"motivo[:\s]+([^\.\,\n]+)", prompt, re.IGNORECASE)
                if m_mot:
                    motivo = m_mot.group(1).strip()
            return AgentResponse(
                handled=True,
                action="dar_baixa_material",
                params={"material": mat_match or (materiais_nomes[0] if materiais_nomes else "Material"), "quantidade": qtd, "motivo": motivo},
                confidence=0.97,
                agent_name=self.name
            )

        # ── 3. DAR ENTRADA EM MATERIAL ──
        if any(w in prompt_clean for w in ["dar entrada", "entrada de", "adicionar ao estoque", "repor estoque", "repor", "chegou", "comprei"]):
            mat_match = self._find_best_match(prompt_clean, materiais_nomes)
            qtd = self._extract_number(prompt_clean, default=1.0)
            return AgentResponse(
                handled=True,
                action="dar_entrada_material",
                params={"material": mat_match or (materiais_nomes[0] if materiais_nomes else "Material"), "quantidade": qtd},
                confidence=0.97,
                agent_name=self.name
            )

        # ── 4. CADASTRAR NOVO MATERIAL ──
        if any(w in prompt_clean for w in ["cadastrar material", "adicionar material", "criar material", "novo material", "cadastre o material"]):
            nome_cand = self._extract_entity_after(prompt, ["material", "chamado", "nome"])
            cat = "Courino"
            for c in ["Courino", "Metal", "Aviamento", "Tecido", "Embalagem", "Outros"]:
                if self._clean_str(c) in prompt_clean:
                    cat = c
                    break
            qtd = self._extract_number(prompt_clean, default=0.0)
            preco = self._extract_price(prompt_clean, default=0.0)
            return AgentResponse(
                handled=True,
                action="cadastrar_material",
                params={"nome": nome_cand or "Novo Material", "categoria": cat, "quantidade": qtd, "preco_unitario": preco, "unidade": "un"},
                confidence=0.95,
                agent_name=self.name
            )

        # ── 5. EXCLUIR MATERIAL ──
        if any(w in prompt_clean for w in ["excluir material", "apagar material", "remover material", "deletar material"]):
            mat_match = self._find_best_match(prompt_clean, materiais_nomes)
            return AgentResponse(
                handled=True,
                action="excluir_material",
                params={"material": mat_match or "Material"},
                confidence=0.96,
                agent_name=self.name
            )

        # ── 6. GESTÃO DE SOBRAS ──
        if "sobra" in prompt_clean or "retalho" in prompt_clean:
            if any(w in prompt_clean for w in ["usar", "usada", "reutilizar", "aproveitar"]):
                desc = self._extract_entity_after(prompt, ["sobra", "retalho", "de"])
                return AgentResponse(
                    handled=True,
                    action="usar_sobra",
                    params={"descricao": desc or "Sobra"},
                    confidence=0.95,
                    agent_name=self.name
                )
            if any(w in prompt_clean for w in ["descartar", "jogar fora", "lixo"]):
                desc = self._extract_entity_after(prompt, ["sobra", "retalho", "de"])
                return AgentResponse(
                    handled=True,
                    action="descartar_sobra",
                    params={"descricao": desc or "Sobra"},
                    confidence=0.95,
                    agent_name=self.name
                )
            if any(w in prompt_clean for w in ["excluir", "apagar", "remover", "deletar"]):
                desc = self._extract_entity_after(prompt, ["sobra", "retalho", "de"])
                return AgentResponse(
                    handled=True,
                    action="excluir_sobra",
                    params={"descricao": desc or "Sobra"},
                    confidence=0.95,
                    agent_name=self.name
                )
            if any(w in prompt_clean for w in ["cadastrar", "criar", "adicionar", "salvar", "nova sobra"]):
                desc = self._extract_entity_after(prompt, ["sobra de", "sobra", "retalho de", "retalho"])
                qtd = self._extract_number(prompt_clean, default=1.0)
                return AgentResponse(
                    handled=True,
                    action="cadastrar_sobra",
                    params={"descricao": desc or "Sobra de Material", "quantidade": qtd, "unidade": "un"},
                    confidence=0.95,
                    agent_name=self.name
                )

        # ── 7. CÁLCULO: CAPACIDADE DE PRODUÇÃO (QUANTAS PEÇAS DÁ PRA FAZER COM O ESTOQUE) ──
        if any(w in prompt_clean for w in ["quantas", "quantos", "capacidade de producao", "quanto da pra produzir", "da pra fazer", "consigo fazer", "da para fazer"]):
            prod_match = self._find_best_match(prompt_clean, context.get("produtos_nomes", []))
            return AgentResponse(
                handled=True,
                action="calcular_capacidade_producao",
                params={"produto": prod_match or (context.get("produtos_nomes", ["Bolsa"])[0] if context.get("produtos_nomes") else "Bolsa")},
                confidence=0.98,
                agent_name=self.name
            )

        # ── 8. CÁLCULO: NECESSIDADE DE COMPRAS / LISTA DE FALTAS PARA PEDIDOS ──
        if any(w in prompt_clean for w in ["o que preciso comprar", "lista de compras", "materiais faltantes", "o que falta comprar", "comprar para os pedidos", "insumos para os pedidos", "o que falta para entregar", "o que falta para os pedidos"]):
            return AgentResponse(
                handled=True,
                action="calcular_necessidade_compras",
                params={"status": "pendentes"},
                confidence=0.98,
                agent_name=self.name
            )

        # ── 9. CÁLCULO: CONSUMO DE MATERIAIS PARA PRODUÇÃO PLANEJADA ──
        if any(w in prompt_clean for w in ["quanto material preciso para fazer", "quanto de material preciso", "quanto preciso para produzir", "material necessario para", "quanto material gasta para fazer"]):
            prod_match = self._find_best_match(prompt_clean, context.get("produtos_nomes", []))
            qtd = int(self._extract_number(prompt_clean, default=1.0))
            return AgentResponse(
                handled=True,
                action="calcular_consumo_producao",
                params={"produto": prod_match or (context.get("produtos_nomes", ["Bolsa"])[0] if context.get("produtos_nomes") else "Bolsa"), "quantidade": qtd},
                confidence=0.98,
                agent_name=self.name
            )

        # ── 10. CONSULTA DE ESTOQUE / ALERTAS ──
        if any(w in prompt_clean for w in ["consultar estoque", "ver estoque", "saldo", "quanto temos", "quanto resta", "estoque baixo", "acabando", "falta"]):
            somente_criticos = any(w in prompt_clean for w in ["baixo", "acabando", "critico", "falta", "comprar"])
            return AgentResponse(
                handled=True,
                action="consultar_estoque",
                params={"somente_criticos": somente_criticos},
                confidence=0.94,
                agent_name=self.name
            )

        return AgentResponse(handled=False, agent_name=self.name)

    def _clean_str(self, text: str) -> str:
        if not text:
            return ""
        import unicodedata
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

    def _find_best_match(self, text_clean: str, options: List[str]) -> Optional[str]:
        if not options:
            return None
        text_c = self._clean_str(text_clean)
        # Match exato
        for opt in options:
            if self._clean_str(opt) in text_c:
                return opt
        # Match por palavras
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
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                pass
        return default

    def _extract_price(self, text: str, default: float = 0.0) -> float:
        m = re.search(r"r\$\s*(\d+(?:[\.,]\d+)?)", text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                pass
        return self._extract_number(text, default)

    def _extract_entity_after(self, text: str, triggers: List[str]) -> Optional[str]:
        t_clean = self._clean_str(text)
        for trig in triggers:
            trig_c = self._clean_str(trig)
            if trig_c in t_clean:
                idx = t_clean.find(trig_c) + len(trig_c)
                sub = text[idx:].strip()
                sub = re.split(r"[,;\.\n]| com | por | para | categoria | valor | preco ", sub, flags=re.IGNORECASE)[0].strip()
                if sub:
                    return sub.title()
        return None
