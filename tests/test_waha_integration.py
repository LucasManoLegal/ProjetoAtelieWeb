"""
tests/test_waha_integration.py - Testes de Unidade e Integração da API WAHA (WhatsApp)
Valida persistência de configurações, cliente HTTP, processamento de webhooks,
criação automática de pedidos, rotas da aba developer e verificação em tempo real.
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock
from app import app, init_db, DB_PATH, carregar_produtos
import waha_service


class TestWahaIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

    def _login_as_developer(self):
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE role='Developer' LIMIT 1")
        row = cur.fetchone()
        if row:
            dev_id = row[0]
        else:
            dev_id = "dev-test-id"
            from app import agora, serializar_roles, generate_password_hash
            now = agora().isoformat()
            cur.execute(
                "INSERT OR REPLACE INTO usuarios (id, username, password_hash, role, roles, nome, email, session_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (dev_id, "developer", generate_password_hash("developer"), "Developer", serializar_roles(["Developer"]), "Desenvolvedor", "dev@atelie.com", now)
            )
            conn.commit()
        conn.close()

        with self.client.session_transaction() as sess:
            sess["user_id"] = dev_id
            sess["session_version"] = 0

    # ── 1. CONFIGURAÇÕES WAHA (CRUD & AMBIENTE) ──────────────────────────────
    def test_01_configuracoes_waha_crud(self):
        """Valida que configurações da API WAHA são lidas e persistidas com sucesso."""
        nova_cfg = {
            "api_url": "http://127.0.0.1:3000",
            "session_name": "atelie_sessao_teste",
            "api_key": "minha_chave_secreta_teste",
            "webhook_secret": "token_secreto_webhook",
            "ativo": 1,
            "auto_reply": 1,
            "notificar_admin": 0,
            "status_padrao": "Pendente",
        }
        ok = waha_service.salvar_configuracoes_waha(nova_cfg)
        self.assertTrue(ok)

        lida = waha_service.obter_configuracoes_waha()
        self.assertEqual(lida["api_url"], "http://127.0.0.1:3000")
        self.assertEqual(lida["session_name"], "atelie_sessao_teste")
        self.assertEqual(lida["api_key"], "minha_chave_secreta_teste")
        self.assertEqual(lida["webhook_secret"], "token_secreto_webhook")
        self.assertEqual(lida["ativo"], 1)
        self.assertEqual(lida["auto_reply"], 1)

    # ── 2. TESTE DE CONECTIVIDADE COM MOCK DA API WAHA ───────────────────────
    @patch("requests.get")
    def test_02_testar_conexao_waha_online_working(self, mock_get):
        """Valida teste de conexão quando sessão está ativa (WORKING)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps([
            {
                "name": "default",
                "status": "WORKING",
                "me": {"id": "5511999998888@c.us", "pushName": "Ateliê Oficial"}
            }
        ])
        mock_resp.json.return_value = json.loads(mock_resp.text)
        mock_get.return_value = mock_resp

        res = waha_service.testar_conexao_waha("http://localhost:3000", session_name="default")
        self.assertTrue(res["ok"])
        self.assertTrue(res["waha_online"])
        self.assertEqual(res["session_status"], "WORKING")
        self.assertIn("operacional", res["message"].lower())

    @patch("requests.get")
    def test_03_testar_conexao_waha_scan_qr(self, mock_get):
        """Valida teste de conexão quando sessão está aguardando leitura de QR Code."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps([
            {"name": "default", "status": "SCAN_QR_CODE"}
        ])
        mock_resp.json.return_value = json.loads(mock_resp.text)
        mock_get.return_value = mock_resp

        res = waha_service.testar_conexao_waha("http://localhost:3000", session_name="default")
        self.assertTrue(res["ok"])
        self.assertEqual(res["session_status"], "SCAN_QR_CODE")
        self.assertIn("qr code", res["message"].lower())

    # ── 3. INTENÇÃO DE PEDIDO & MATCHING DE PRODUTOS ─────────────────────────
    def test_04_identificar_intencao_e_produto(self):
        """Testa o extrator de intenções para pedidos, catálogo, status e saudações."""
        produtos_mock = [
            {"id": "prod-1", "nome": "Bolsa Carteira", "preco_venda": 95.0, "emoji": "👛"},
            {"id": "prod-2", "nome": "Bolsa Tote Grande", "preco_venda": 180.0, "emoji": "👜"},
        ]

        # Pedido direto com quantidade
        r1 = waha_service.identificar_intencao_e_produto("Olá, quero 2 bolsa carteira por favor", produtos_mock)
        self.assertTrue(r1["is_order"])
        self.assertEqual(r1["produto"]["nome"], "Bolsa Carteira")
        self.assertEqual(r1["quantidade"], 2)

        # Pedido com verbo encomendar
        r2 = waha_service.identificar_intencao_e_produto("Gostaria de encomendar 1 bolsa tote grande", produtos_mock)
        self.assertTrue(r2["is_order"])
        self.assertEqual(r2["produto"]["nome"], "Bolsa Tote Grande")
        self.assertEqual(r2["quantidade"], 1)

        # Consulta de catálogo
        r3 = waha_service.identificar_intencao_e_produto("Boa tarde, você pode me mandar o catálogo com os preços?", produtos_mock)
        self.assertTrue(r3["is_catalog"])
        self.assertFalse(r3["is_order"])

        # Consulta de status
        r4 = waha_service.identificar_intencao_e_produto("Como está a situação do meu pedido?", produtos_mock)
        self.assertTrue(r4["is_status"])

    # ── 4. CRIAÇÃO AUTOMÁTICA DE PEDIDO ──────────────────────────────────────
    def test_05_criar_pedido_whatsapp(self):
        """Valida que um pedido criado via WhatsApp é persistido no banco com atributos corretos."""
        prod = {"id": "prod-teste-waha", "nome": "Bolsa Baú Chic", "preco_venda": 150.0, "emoji": "🧳"}
        pedido = waha_service.criar_pedido_whatsapp(
            cliente_nome="Mariana Souza",
            cliente_telefone="5511977776666",
            produto=prod,
            quantidade=2,
            status_padrao="Pendente",
            observacao_extra="Entrega rápida",
        )

        self.assertIsNotNone(pedido.get("id"))
        self.assertEqual(pedido["cliente"], "Mariana Souza")
        self.assertEqual(pedido["produto_nome"], "Bolsa Baú Chic")
        self.assertEqual(pedido["quantidade"], 2)
        self.assertEqual(pedido["preco_unitario"], 150.0)
        self.assertEqual(pedido["valor_total"], 300.0)
        self.assertEqual(pedido["status"], "Pendente")
        self.assertEqual(pedido["origem"], "whatsapp")
        self.assertEqual(pedido["telefone_cliente"], "5511977776666")
        self.assertIn("97777-6666", pedido["observacoes"])

    # ── 5. PROCESSAMENTO DO WEBHOOK COM RESPOSTA AUTOMÁTICA ──────────────────
    @patch("waha_service.enviar_mensagem_whatsapp")
    def test_06_processar_webhook_pedido_completo(self, mock_enviar_msg):
        """Simula webhook do WAHA recebendo mensagem de pedido e gerando resposta."""
        mock_enviar_msg.return_value = {"ok": True}

        # Garante configuração ativa e auto_reply
        waha_service.salvar_configuracoes_waha({
            "ativo": 1,
            "auto_reply": 1,
            "status_padrao": "Pendente",
        })

        produtos = carregar_produtos()
        prod_nome = produtos[0]["nome"] if produtos else "Bolsa Carteira"

        payload = {
            "event": "message",
            "session": "default",
            "payload": {
                "id": "waha_msg_123",
                "from": "5511988881234@c.us",
                "fromMe": False,
                "body": f"Olá! Quero 2 {prod_nome} para entrega",
                "notifyName": "Letícia Ramos",
            }
        }

        res = waha_service.processar_webhook_waha(payload)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "pedido_criado")
        self.assertIn("pedido", res)
        self.assertEqual(res["pedido"]["cliente"], "Letícia Ramos")
        self.assertEqual(res["pedido"]["quantidade"], 2)

        # Confirma que a resposta automática de confirmação foi disparada
        mock_enviar_msg.assert_called()

    def test_07_processar_webhook_from_me_ignorado(self):
        """Garante que mensagens enviadas pelo próprio ateliê (fromMe=true) sejam ignoradas."""
        payload = {
            "event": "message",
            "payload": {
                "from": "5511988881234@c.us",
                "fromMe": True,
                "body": "Mensagem enviada por mim",
            }
        }
        res = waha_service.processar_webhook_waha(payload)
        self.assertTrue(res.get("ignored"))
        self.assertIn("próprio ateliê", res.get("reason", ""))

    # ── 6. ENDPOINTS DEVELOPER & AUDITORIA NO APP FLASK ──────────────────────
    def test_08_developer_dashboard_acesso_waha(self):
        """Garante que o desenvolvedor consegue acessar o tab waha no Developer Hub."""
        self._login_as_developer()

        resp = self.client.get("/developer?tab=waha")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("WhatsApp (WAHA API)", html)
        self.assertIn("tabpanel_waha", html)
        self.assertIn("Simulador de Pedido WhatsApp", html)

    def test_09_developer_waha_salvar_e_testar_ajax(self):
        """Valida rota POST de salvar e testar conexão via AJAX."""
        self._login_as_developer()

        # Salvar
        r_save = self.client.post("/developer/waha/salvar", data={
            "api_url": "http://localhost:3000",
            "session_name": "teste_dev_hub",
            "api_key": "key_123",
            "webhook_secret": "",
            "ativo": "1",
            "auto_reply": "1",
            "status_padrao": "Pendente",
        }, follow_redirects=True)
        self.assertEqual(r_save.status_code, 200)

        # Testar AJAX com mock
        with patch("waha_service.testar_conexao_waha") as mock_test:
            mock_test.return_value = {
                "ok": True,
                "waha_online": True,
                "session_status": "WORKING",
                "message": "Operacional",
                "sessions": []
            }
            r_ajax = self.client.post("/developer/waha/testar", json={"api_url": "http://localhost:3000"})
            self.assertEqual(r_ajax.status_code, 200)
            data = r_ajax.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["session_status"], "WORKING")

    def test_10_developer_simular_pedido_ajax(self):
        """Valida que o simulador de pedidos do Developer Hub cria pedido e atualiza contador."""
        self._login_as_developer()

        produtos = carregar_produtos()
        prod_id = produtos[0]["id"] if produtos else "prod_1"

        r_sim = self.client.post("/developer/waha/simular-pedido", json={
            "cliente": "Cliente Teste Simulado",
            "telefone": "11988887777",
            "produto_id": prod_id,
            "quantidade": 1,
            "mensagem": "Simulação via teste automatizado",
        })
        self.assertEqual(r_sim.status_code, 200)
        data = r_sim.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("pedido", data)
        self.assertEqual(data["pedido"]["cliente"], "Cliente Teste Simulado")

    # ── 7. ROTA DE LIVE CHECK DE PEDIDOS ─────────────────────────────────────
    def test_11_api_pedidos_ultimos_live_check(self):
        """Valida que o endpoint /api/pedidos/ultimos retorna informações precisas para o polling."""
        resp = self.client.get("/api/pedidos/ultimos")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("count", data)
        self.assertIn("latest_id", data)
        self.assertIn("latest_cliente", data)
        self.assertIn("latest_origem", data)


if __name__ == "__main__":
    unittest.main()
