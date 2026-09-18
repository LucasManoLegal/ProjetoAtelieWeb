import os
import sys
import unittest
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Configuration for testing environment
os.environ["USE_SQLITE"] = "1"
os.environ["FLASK_ENV"] = "testing"

import app


class TestGoogleCalendar(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        app.app.config["WTF_CSRF_ENABLED"] = False
        app.app.secret_key = "test-secret-calendar-key"
        self.client = app.app.test_client()

        app.init_db()
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM usuarios")
        cur.execute("DELETE FROM configuracoes_sso")
        cur.execute("DELETE FROM pedidos")
        cur.execute("DELETE FROM produtos")
        cur.execute("DELETE FROM audits")

        now = app.agora().isoformat()
        cur.execute(
            """
            INSERT INTO usuarios 
            (id, username, password_hash, role, roles, nome, email, google_id, google_refresh_token, google_access_token, google_token_expiry, created_at, session_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                "admin-cal-uid",
                "artesa_admin",
                app.generate_password_hash("admin123"),
                "Developer",
                app.serializar_roles(["Developer", "Admin"]),
                "Artesã Maria",
                "maria@ateliehaiti.com",
                "google-sub-12345",
                "dummy-google-refresh-token",
                "dummy-google-access-token",
                (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                now
            )
        )

        cur.execute(
            """
            INSERT INTO produtos
            (id, nome, emoji, preco_venda, receita, estoque_pronto, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 5, ?, ?)
            """,
            ("prod-bolsa-01", "Bolsa Tote Vintage", "👜", 180.0, "[]", now, now)
        )

        cur.execute(
            """
            INSERT INTO configuracoes_sso
            (id, google_client_id, google_client_secret, ativo, auto_cadastro, papel_padrao, updated_at)
            VALUES ('sso-1', 'client-id-test.apps.googleusercontent.com', 'secret-key-test', 1, 1, 'Producao', ?)
            """,
            (now,)
        )

        conn.commit()
        conn.close()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "admin-cal-uid"
            sess["session_version"] = 0

    def test_01_pedidos_table_has_delivery_and_calendar_columns(self):
        """Verifica se a tabela pedidos possui as novas colunas data_entrega e google_event_id."""
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(pedidos)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()

        self.assertIn("data_entrega", cols)
        self.assertIn("google_event_id", cols)
        self.assertIn("google_calendar_synced_at", cols)

    def test_02_criar_pedido_com_data_entrega(self):
        """Testa criar pedido com data de entrega na rota /pedidos/novo."""
        self._login()
        with patch("app.criar_ou_atualizar_evento_google_calendar") as mock_cal:
            mock_cal.return_value = {"success": True, "action": "created", "event_id": "mock_event_123"}

            resp = self.client.post("/pedidos/novo", data={
                "cliente": "Clara Mendes",
                "produto_id": "prod-bolsa-01",
                "quantidade": "2",
                "preco_unitario": "180.00",
                "status": "Em produção",
                "data_entrega": "2026-10-15",
                "observacoes": "Bolsa com alça alongada"
            }, follow_redirects=True)

            self.assertEqual(resp.status_code, 200)

        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM pedidos WHERE cliente='Clara Mendes'")
        p = cur.fetchone()
        conn.close()

        self.assertIsNotNone(p)
        self.assertEqual(p["data_entrega"], "2026-10-15")
        self.assertEqual(p["cliente"], "Clara Mendes")

    def test_03_rota_calendario_entregas_renderiza(self):
        """Testa renderização da página de Agenda de Entregas (/calendario)."""
        self._login()

        # Insere pedido de teste com entrega
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.cursor()
        now = app.agora().isoformat()
        cur.execute(
            """
            INSERT INTO pedidos
            (id, cliente, produto_id, produto_nome, produto_emoji, quantidade, preco_unitario, valor_total, status, data_pedido, data_pedido_iso, data_entrega, google_event_id, google_calendar_synced_at, observacoes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ped-cal-01", "Juliana Silva", "prod-bolsa-01", "Bolsa Tote Vintage", "👜", 1, 180.0, 180.0, "Em produção", "18/09/2026", now, "2026-09-25", "ev_juliana_123", now, "Entrega expressa", now, now)
        )
        conn.commit()
        conn.close()

        with patch("app.verificar_conexao_google_calendar") as mock_conn:
            mock_conn.return_value = {"conectado": True, "email": "maria@ateliehaiti.com", "summary": "Agenda Ateliê"}

            resp = self.client.get("/calendario")
            self.assertEqual(resp.status_code, 200)
            html = resp.data.decode("utf-8")
            self.assertIn("Agenda de Entregas", html)
            self.assertIn("Juliana Silva", html)
            self.assertIn("Bolsa Tote Vintage", html)
            self.assertIn("Google Calendar Conectado", html)

    def test_04_atualizacao_rapida_data_entrega(self):
        """Testa rota /pedidos/<id>/data-entrega para definir ou atualizar data."""
        self._login()
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.cursor()
        now = app.agora().isoformat()
        cur.execute(
            """
            INSERT INTO pedidos
            (id, cliente, produto_id, produto_nome, produto_emoji, quantidade, preco_unitario, valor_total, status, data_pedido, data_pedido_iso, data_entrega, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ped-cal-02", "Fernanda Lima", "prod-bolsa-01", "Bolsa Tote Vintage", "👜", 1, 180.0, 180.0, "Pendente", "18/09/2026", now, "", now, now)
        )
        conn.commit()
        conn.close()

        with patch("app.criar_ou_atualizar_evento_google_calendar") as mock_cal:
            mock_cal.return_value = {"success": True, "action": "created", "event_id": "ev_fernanda_777"}

            resp = self.client.post("/pedidos/ped-cal-02/data-entrega", data={
                "data_entrega": "2026-10-20"
            }, follow_redirects=True)

            self.assertEqual(resp.status_code, 200)
            mock_cal.assert_called_once()

        conn = sqlite3.connect(app.DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT data_entrega FROM pedidos WHERE id='ped-cal-02'")
        row = cur.fetchone()
        conn.close()

        self.assertEqual(row["data_entrega"], "2026-10-20")

    def test_05_mock_criar_ou_atualizar_evento_google_calendar_post(self):
        """Testa função criar_ou_atualizar_evento_google_calendar com chamada POST à API do Google."""
        pedido = {
            "id": "ped-test-api-01",
            "cliente": "Beatriz Ramos",
            "produto_nome": "Bolsa Transversal",
            "produto_emoji": "👜",
            "quantidade": 1,
            "valor_total": 150.0,
            "status": "Em produção",
            "data_entrega": "2026-11-10",
            "google_event_id": ""
        }

        # Mock de requests.post bem sucedido
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "gcal_event_beatriz_999",
            "htmlLink": "https://calendar.google.com/event?eid=xyz"
        }

        with patch("requests.post", return_value=mock_response):
            res = app.criar_ou_atualizar_evento_google_calendar(pedido, user_id="admin-cal-uid")

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "created")
        self.assertEqual(res.get("event_id"), "gcal_event_beatriz_999")

    def test_06_mock_criar_ou_atualizar_evento_google_calendar_patch(self):
        """Testa atualização (PATCH) de evento existente no Google Calendar."""
        pedido = {
            "id": "ped-test-api-02",
            "cliente": "Carlos Eduardo",
            "produto_nome": "Mochila Couro",
            "produto_emoji": "🎒",
            "quantidade": 1,
            "valor_total": 290.0,
            "status": "Concluído",
            "data_entrega": "2026-11-12",
            "google_event_id": "gcal_event_carlos_555"
        }

        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 200
        mock_patch_resp.json.return_value = {
            "id": "gcal_event_carlos_555",
            "htmlLink": "https://calendar.google.com/event?eid=carlos"
        }

        with patch("requests.patch", return_value=mock_patch_resp):
            res = app.criar_ou_atualizar_evento_google_calendar(pedido, user_id="admin-cal-uid")

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("action"), "updated")
        self.assertEqual(res.get("event_id"), "gcal_event_carlos_555")

    def test_07_mock_excluir_evento_google_calendar(self):
        """Testa exclusão de evento no Google Calendar (DELETE)."""
        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 204

        with patch("requests.delete", return_value=mock_del_resp):
            res = app.excluir_evento_google_calendar("gcal_event_carlos_555", user_id="admin-cal-uid")

        self.assertTrue(res.get("success"))

    def test_08_sincronizar_todos_pedidos_lote(self):
        """Testa sincronização em lote de todos os pedidos ativos com entrega."""
        self._login()
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.cursor()
        now = app.agora().isoformat()
        cur.execute(
            """
            INSERT INTO pedidos
            (id, cliente, produto_id, produto_nome, produto_emoji, quantidade, preco_unitario, valor_total, status, data_pedido, data_pedido_iso, data_entrega, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ped-batch-01", "Marina Souza", "prod-bolsa-01", "Bolsa Tote Vintage", "👜", 1, 180.0, 180.0, "Em produção", "18/09/2026", now, "2026-10-05", now, now)
        )
        cur.execute(
            """
            INSERT INTO pedidos
            (id, cliente, produto_id, produto_nome, produto_emoji, quantidade, preco_unitario, valor_total, status, data_pedido, data_pedido_iso, data_entrega, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ped-batch-02", "Renata Gomes", "prod-bolsa-01", "Bolsa Tote Vintage", "👜", 2, 180.0, 360.0, "Pendente", "18/09/2026", now, "2026-10-08", now, now)
        )
        conn.commit()
        conn.close()

        with patch("app.criar_ou_atualizar_evento_google_calendar") as mock_sync:
            mock_sync.return_value = {"success": True, "action": "created", "event_id": "ev_batch"}

            resp = self.client.post("/pedidos/sync-calendar-todos", follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertGreaterEqual(mock_sync.call_count, 2)

    def test_09_developer_hub_teste_calendar_api(self):
        """Testa a rota de teste diagnóstico da API do Google Calendar no Developer Hub."""
        self._login()

        mock_post = MagicMock()
        mock_post.status_code = 201
        mock_post.json.return_value = {
            "id": "gcal_diag_test_event",
            "htmlLink": "https://calendar.google.com/event?eid=diag"
        }

        with patch("app.verificar_conexao_google_calendar") as mock_conn, \
             patch("app.criar_ou_atualizar_evento_google_calendar") as mock_create:
            mock_conn.return_value = {"conectado": True, "email": "maria@ateliehaiti.com", "summary": "Agenda Principal"}
            mock_create.return_value = {"success": True, "action": "created", "event_id": "gcal_diag_test_event"}

            resp = self.client.post("/developer/sso/testar-calendar", follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            html = resp.data.decode("utf-8")
            self.assertIn("Conexão com o Google Calendar validada com sucesso", html)

    def test_10_ania_consulta_entregas(self):
        """Testa a assistente Ania respondendo sobre próximas entregas agendadas."""
        conn = sqlite3.connect(app.DB_PATH)
        cur = conn.cursor()
        now = app.agora().isoformat()
        cur.execute(
            """
            INSERT INTO pedidos
            (id, cliente, produto_id, produto_nome, produto_emoji, quantidade, preco_unitario, valor_total, status, data_pedido, data_pedido_iso, data_entrega, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ped-ania-cal", "Helena Castro", "prod-bolsa-01", "Bolsa Tote Vintage", "👜", 1, 180.0, 180.0, "Em produção", "18/09/2026", now, "2026-09-30", now, now)
        )
        conn.commit()
        conn.close()

        from ania_assistant import AniaAssistant
        ania = AniaAssistant(app)
        user = {"id": "admin-cal-uid", "username": "artesa_admin", "role": "Admin", "roles": ["Admin"]}

        res = ania.processar_mensagem("quais são as entregas agendadas no ateliê?", user, mode="contingencia")
        self.assertIn("Próximas Entregas Agendadas", res.get("reply", ""))
        self.assertIn("Helena Castro", res.get("reply", ""))


if __name__ == "__main__":
    unittest.main()
