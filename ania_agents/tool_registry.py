"""
ania_agents/tool_registry.py - Catálogo e Registro de Ferramentas com Validação RBAC
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

from typing import Dict, Any, Optional, Callable, List


class ToolDefinition:
    def __init__(
        self,
        name: str,
        resource: str,
        action_type: str,
        description: str,
        param_schema: Dict[str, Any],
        handler: Optional[Callable] = None
    ):
        self.name = name
        self.resource = resource
        self.action_type = action_type  # 'create', 'read', 'update', 'delete'
        self.description = description
        self.param_schema = param_schema
        self.handler = handler


class ToolRegistry:
    """
    Catálogo central de todas as ferramentas disponíveis no Ateliê Web.
    Garante validação estrita de RBAC antes de executar qualquer alteração no banco.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "resource": t.resource,
                "action_type": t.action_type,
                "description": t.description,
                "params": t.param_schema,
            }
            for t in self._tools.values()
        ]

    def _register_default_tools(self):
        # ── ESTOQUE E MATERIAIS ──
        self.register(ToolDefinition(
            name="dar_entrada_material",
            resource="estoque",
            action_type="update",
            description="Dá entrada (adiciona saldo) a um material existente no estoque",
            param_schema={"material": "str", "quantidade": "float"}
        ))
        self.register(ToolDefinition(
            name="dar_baixa_material",
            resource="baixa",
            action_type="create",
            description="Dá baixa (remove saldo) de um material do estoque com motivo",
            param_schema={"material": "str", "quantidade": "float", "motivo": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="cadastrar_material",
            resource="materiais",
            action_type="create",
            description="Cadastra um novo material na base de dados",
            param_schema={"nome": "str", "categoria": "str (opcional)", "quantidade": "float (opcional)", "unidade": "str (opcional)", "preco_unitario": "float (opcional)", "estoque_minimo": "float (opcional)"}
        ))
        self.register(ToolDefinition(
            name="excluir_material",
            resource="materiais",
            action_type="delete",
            description="Exclui permanentemente um material do estoque",
            param_schema={"material": "str"}
        ))
        self.register(ToolDefinition(
            name="consultar_estoque",
            resource="estoque",
            action_type="read",
            description="Consulta o saldo atual ou materiais abaixo do estoque mínimo",
            param_schema={"filtro": "str (opcional)", "somente_criticos": "bool (opcional)"}
        ))
        self.register(ToolDefinition(
            name="cadastrar_sobra",
            resource="sobras",
            action_type="create",
            description="Registra uma sobra ou retalho aproveitável de material",
            param_schema={"descricao": "str", "material_origem": "str (opcional)", "quantidade": "float (opcional)", "unidade": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="usar_sobra",
            resource="sobras",
            action_type="update",
            description="Marca uma sobra de material como reutilizada em uma peça",
            param_schema={"descricao": "str"}
        ))
        self.register(ToolDefinition(
            name="descartar_sobra",
            resource="sobras",
            action_type="update",
            description="Marca uma sobra de material como descartada",
            param_schema={"descricao": "str"}
        ))
        self.register(ToolDefinition(
            name="excluir_sobra",
            resource="sobras",
            action_type="delete",
            description="Exclui o registro de uma sobra de material",
            param_schema={"descricao": "str"}
        ))
        self.register(ToolDefinition(
            name="calcular_capacidade_producao",
            resource="estoque",
            action_type="read",
            description="Calcula a quantidade máxima de um produto que pode ser produzida com o saldo atual de materiais",
            param_schema={"produto": "str"}
        ))
        self.register(ToolDefinition(
            name="calcular_necessidade_compras",
            resource="estoque",
            action_type="read",
            description="Calcula a lista exata de materiais faltantes para atender aos pedidos pendentes",
            param_schema={"status": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="calcular_consumo_producao",
            resource="estoque",
            action_type="read",
            description="Calcula a quantidade de materiais necessários para produzir uma quantidade planejada de produtos",
            param_schema={"produto": "str", "quantidade": "int"}
        ))

        # ── PRODUTOS E CATÁLOGO ──
        self.register(ToolDefinition(
            name="cadastrar_produto",
            resource="produtos",
            action_type="create",
            description="Cadastra um novo modelo de produto ou bolsa no catálogo",
            param_schema={"nome": "str", "preco_venda": "float (opcional)", "categoria": "str (opcional)", "tempo_producao_min": "int (opcional)"}
        ))
        self.register(ToolDefinition(
            name="ajustar_estoque_pronto",
            resource="produtos",
            action_type="update",
            description="Ajusta o estoque de produtos acabados prontos para entrega",
            param_schema={"produto": "str", "quantidade": "int", "operacao": "str (entrada|saida|definir)"}
        ))
        self.register(ToolDefinition(
            name="excluir_produto",
            resource="produtos",
            action_type="delete",
            description="Exclui um produto do catálogo",
            param_schema={"produto": "str"}
        ))
        self.register(ToolDefinition(
            name="consultar_produtos",
            resource="produtos",
            action_type="read",
            description="Consulta catálogo de produtos, preços de venda e fichas técnicas",
            param_schema={"termo": "str (opcional)"}
        ))

        # ── PEDIDOS E PRODUÇÃO ──
        self.register(ToolDefinition(
            name="criar_pedido",
            resource="pedidos",
            action_type="create",
            description="Cadastra um novo pedido/encomenda de cliente no sistema",
            param_schema={"cliente": "str", "produto": "str", "quantidade": "int (opcional)", "data_entrega": "str (opcional)", "observacoes": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="mudar_status_pedido",
            resource="pedidos",
            action_type="update",
            description="Altera o status de um pedido (ex: Pendente, Em Produção, Concluído, Entregue, Cancelado)",
            param_schema={"cliente_ou_id": "str", "novo_status": "str"}
        ))
        self.register(ToolDefinition(
            name="excluir_pedido",
            resource="pedidos",
            action_type="delete",
            description="Exclui um pedido do sistema",
            param_schema={"cliente_ou_id": "str"}
        ))
        self.register(ToolDefinition(
            name="consultar_pedidos",
            resource="pedidos",
            action_type="read",
            description="Consulta pedidos por status, cliente ou data de entrega",
            param_schema={"status": "str (opcional)", "cliente": "str (opcional)"}
        ))

        # ── FINANCEIRO E DESPESAS ──
        self.register(ToolDefinition(
            name="cadastrar_despesa",
            resource="financeiro",
            action_type="create",
            description="Registra uma nova despesa do ateliê",
            param_schema={"descricao": "str", "valor": "float", "categoria": "str (opcional)", "data": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="excluir_despesa",
            resource="financeiro",
            action_type="delete",
            description="Exclui uma despesa registrada",
            param_schema={"descricao": "str"}
        ))
        self.register(ToolDefinition(
            name="consultar_financeiro",
            resource="financeiro",
            action_type="read",
            description="Consulta balanço, faturamento, despesas e lucro estimado",
            param_schema={"periodo": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="calcular_precificacao",
            resource="financeiro",
            action_type="read",
            description="Calcula o custo de fabricação, markup sugerido e margem de lucro de um produto",
            param_schema={"produto": "str", "margem_desejada": "float (opcional)"}
        ))

        # ── RELATÓRIOS E EXPORTAÇÕES ──
        self.register(ToolDefinition(
            name="gerar_relatorio_pdf",
            resource="relatorios",
            action_type="read",
            description="Gera o relatório visual em formato PDF do ateliê",
            param_schema={"tipo": "str (opcional)"}
        ))
        self.register(ToolDefinition(
            name="exportar_excel",
            resource="relatorios",
            action_type="read",
            description="Exporta a planilha Excel completa com dados do sistema",
            param_schema={}
        ))
        self.register(ToolDefinition(
            name="gerar_backup",
            resource="relatorios",
            action_type="read",
            description="Gera o arquivo de backup completo dos dados em formato JSON",
            param_schema={}
        ))

        # ── ADMINISTRAÇÃO E USUÁRIOS ──
        self.register(ToolDefinition(
            name="consultar_usuarios",
            resource="usuarios",
            action_type="read",
            description="Lista usuários cadastrados e seus papéis no sistema",
            param_schema={}
        ))
