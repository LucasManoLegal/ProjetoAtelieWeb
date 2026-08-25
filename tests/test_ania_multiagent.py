"""
tests/test_ania_multiagent.py - Testes Automatizados da Arquitetura Multi-Agente
Ateliê Haiti - Sistema Multi-Agente 100% Gratuito
"""

import os
import unittest
import sqlite3

from app import (
    app, init_db, DB_PATH, generate_password_hash,
    carregar_materiais, carregar_pedidos, carregar_produtos,
    carregar_sobras, carregar_despesas
)
import ania_assistant
from ania_agents import (
    MultiAgentOrchestrator, InventoryAgent, OrdersAgent,
    FinanceAgent, ReportsAgent, CraftsAgent, AdminAgent
)


class TestAniaMultiAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.testing = True
        os.environ["TESTING"] = "1"
        os.environ["OLLAMA_EMULATE"] = "1"

    def setUp(self):
        init_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Usuário Admin
        cur.execute(
            "INSERT OR REPLACE INTO usuarios (id, username, password_hash, role, roles, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("admin-ma-test", "admin_multiagent", generate_password_hash("admin123"), "Admin", '["Admin"]')
        )

        # Papel Operador (Apenas Estoque e Baixa)
        cur.execute("INSERT OR IGNORE INTO roles (id, name, description, is_system, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                    ("role-op-only", "OperadorBasico", "Acesso apenas ao estoque", 0))
        cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                    ("OperadorBasico", "estoque", 0, 1, 1, 0))
        cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                    ("OperadorBasico", "baixa", 1, 1, 0, 0))
        cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                    ("OperadorBasico", "financeiro", 0, 0, 0, 0))
        cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                    ("OperadorBasico", "relatorios", 0, 0, 0, 0))

        cur.execute(
            "INSERT OR REPLACE INTO usuarios (id, username, password_hash, role, roles, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("user-op-only", "operador_simples", generate_password_hash("op123"), "OperadorBasico", '["OperadorBasico"]')
        )

        # Garante materiais e produtos para testes
        cur.execute("DELETE FROM materiais WHERE nome = 'Courino Preto Teste'")
        cur.execute("INSERT INTO materiais (id, nome, categoria, quantidade, unidade, custo, quantidade_minima) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("mat-test-1", "Courino Preto Teste", "Courino", 50.0, "metros", 30.0, 10.0))

        cur.execute("DELETE FROM produtos WHERE nome = 'Bolsa Carteira Teste'")
        cur.execute("INSERT INTO produtos (id, nome, emoji, preco_venda, receita, estoque_pronto) VALUES (?, ?, ?, ?, ?, ?)",
                    ("prod-test-1", "Bolsa Carteira Teste", "👛", 120.0, '[{"material_id": "mat-test-1", "material_nome": "Courino Preto Teste", "quantidade": 0.5}]', 5))

        conn.commit()
        conn.close()

        self.engine = ania_assistant.AniaAssistant(app)
        self.admin_user = {"id": "admin-ma-test", "username": "admin_multiagent", "role": "Admin", "roles": ["Admin"]}
        self.op_user = {"id": "user-op-only", "username": "operador_simples", "role": "OperadorBasico", "roles": ["OperadorBasico"]}

    def test_orchestrator_initialization(self):
        """Verifica se o orquestrador e todos os 6 especialistas estão carregados."""
        self.assertIsNotNone(self.engine.orchestrator)
        orchestrator = self.engine.orchestrator
        self.assertEqual(len(orchestrator.specialists), 6)
        agent_names = [s.name for s in orchestrator.specialists]
        self.assertIn("inventory_specialist", agent_names)
        self.assertIn("orders_specialist", agent_names)
        self.assertIn("finance_specialist", agent_names)
        self.assertIn("reports_specialist", agent_names)
        self.assertIn("crafts_consultant", agent_names)
        self.assertIn("admin_specialist", agent_names)

    def test_inventory_agent_stock_entry_and_exit(self):
        """Testa ações de entrada e baixa despachadas pelo especialista de estoque."""
        # Entrada
        res_ent = self.engine.processar_mensagem("Dar entrada de 15 metros de Courino Preto Teste", self.admin_user)
        self.assertTrue(res_ent.get("success"), f"Falha na entrada: {res_ent}")

        # Baixa
        res_bx = self.engine.processar_mensagem("Dar baixa de 5 metros de Courino Preto Teste para produção", self.admin_user)
        self.assertTrue(res_bx.get("success"), f"Falha na baixa: {res_bx}")

    def test_orders_agent_creation_and_status(self):
        """Testa criação e alteração de status de pedidos pelo especialista de pedidos."""
        res_ped = self.engine.processar_mensagem("Criar um pedido de 2 Bolsa Carteira Teste para a cliente Renata Silveira", self.admin_user)
        self.assertTrue(res_ped.get("success"), f"Falha ao criar pedido: {res_ped}")

        res_st = self.engine.processar_mensagem("Mudar status do pedido da Renata Silveira para Concluído", self.admin_user)
        self.assertTrue(res_st.get("success"), f"Falha ao mudar status: {res_st}")

    def test_finance_agent_pricing_calculation(self):
        """Testa cálculo de precificação e análise de custos pelo especialista financeiro."""
        res_preco = self.engine.processar_mensagem("Quanto devo cobrar na Bolsa Carteira Teste para ter 60% de margem?", self.admin_user)
        self.assertTrue(res_preco.get("success"), f"Falha na precificação: {res_preco}")
        self.assertIn("Precificação", res_preco.get("reply", ""))
        self.assertIn("Courino Preto Teste", res_preco.get("reply", ""))

    def test_finance_agent_expense_registration(self):
        """Testa cadastro de despesa pelo especialista financeiro."""
        res_desp = self.engine.processar_mensagem("Cadastrar despesa de Frete no valor de R$ 38,50", self.admin_user)
        self.assertTrue(res_desp.get("success"), f"Falha na despesa: {res_desp}")

    def test_reports_agent_pdf_and_excel(self):
        """Testa geração de relatório PDF e planilha Excel pelo especialista de relatórios."""
        res_pdf = self.engine.processar_mensagem("Gerar relatório completo em PDF do ateliê", self.admin_user)
        self.assertTrue(res_pdf.get("success"), f"Falha no PDF: {res_pdf}")
        self.assertIn("action", res_pdf)
        self.assertEqual(res_pdf["action"]["type"], "download")

        res_xls = self.engine.processar_mensagem("Exportar planilha Excel completa", self.admin_user)
        self.assertTrue(res_xls.get("success"), f"Falha no Excel: {res_xls}")
        self.assertIn("action", res_xls)
        self.assertEqual(res_xls["action"]["type"], "download")

    def test_crafts_agent_consulting(self):
        """Testa consultoria técnica e criativa de costura e artesanato."""
        res_dica = self.engine.processar_mensagem("Qual a melhor agulha e linha para costurar courino grosso?", self.admin_user)
        self.assertTrue(res_dica.get("success"), f"Falha na consultoria: {res_dica}")
        self.assertIn("Agulha", res_dica.get("reply", ""))
        self.assertIn("Poliamida", res_dica.get("reply", ""))

    def test_admin_agent_user_listing(self):
        """Testa listagem de usuários e consulta de permissões."""
        res_users = self.engine.processar_mensagem("Listar usuários do sistema", self.admin_user)
        self.assertTrue(res_users.get("success"), f"Falha ao listar usuários: {res_users}")
        self.assertIn("admin_multiagent", res_users.get("reply", ""))

    def test_inventory_agent_capacity_calculation(self):
        """Testa cálculo de quantas peças dá para produzir com o saldo atual de materiais."""
        res_cap = self.engine.processar_mensagem("Quantas Bolsa Carteira Teste consigo fazer com o estoque atual?", self.admin_user)
        self.assertTrue(res_cap.get("success"), f"Falha no cálculo de capacidade: {res_cap}")
        self.assertIn("Capacidade Produtiva", res_cap.get("reply", ""))
        self.assertIn("Courino Preto Teste", res_cap.get("reply", ""))

    def test_inventory_agent_shopping_list(self):
        """Testa cálculo e emissão de lista de compras para pedidos pendentes."""
        res_comp = self.engine.processar_mensagem("O que preciso comprar para os pedidos pendentes?", self.admin_user)
        self.assertTrue(res_comp.get("success"), f"Falha na lista de compras: {res_comp}")

    def test_inventory_agent_consumption_calculation(self):
        """Testa simulação de consumo de materiais para quantidade planejada de produtos."""
        res_cons = self.engine.processar_mensagem("Quanto material preciso para fazer 10 Bolsa Carteira Teste?", self.admin_user)
        self.assertTrue(res_cons.get("success"), f"Falha no cálculo de consumo: {res_cons}")
        self.assertIn("Cálculo de Consumo para 10x", res_cons.get("reply", ""))

    def test_rbac_restriction_on_specialists(self):
        """Garante que operador sem permissão é bloqueado em finanças e relatórios."""
        # Bloqueio em financeiro
        res_fin_denied = self.engine.processar_mensagem("Cadastrar despesa de R$ 100 de luz", self.op_user)
        self.assertFalse(res_fin_denied.get("success"))
        self.assertTrue(res_fin_denied.get("denied"))

        # Bloqueio em relatórios
        res_rep_denied = self.engine.processar_mensagem("Gerar relatório em PDF", self.op_user)
        self.assertFalse(res_rep_denied.get("success"))
        self.assertTrue(res_rep_denied.get("denied"))


if __name__ == "__main__":
    unittest.main()
