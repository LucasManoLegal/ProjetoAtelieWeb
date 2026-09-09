"""
test_ania_ollama.py - Testes Automatizados para a Integração Híbrida Ania + Ollama
Valida o cliente OllamaEngine, Tool Calling estruturado, execução de ações no banco SQLite,
controle de permissões RBAC e contingência automática caso o servidor Ollama esteja indisponível.
"""

import os
import json
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
from app import (
    app, init_db, DB_PATH, generate_password_hash,
    carregar_materiais, carregar_pedidos, carregar_produtos,
    carregar_sobras, carregar_despesas, USE_SQLITE
)
from ania_ollama import OllamaEngine
from ania_assistant import AniaAssistant


class TestAniaOllamaIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.testing = True
        os.environ["TESTING"] = "1"

    def setUp(self):
        init_db()
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        cur = conn.cursor()

        try:
            # Cria usuário Admin (acesso total)
            cur.execute(
                "INSERT OR REPLACE INTO usuarios (id, username, password_hash, role, roles, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("admin-ollama-test", "admin_ollama", generate_password_hash("admin123"), "Admin", '["Admin"]')
            )

            # Cria papel customizado 'EstoqueApenas' com permissão apenas em estoque e baixa
            cur.execute("INSERT OR IGNORE INTO roles (id, name, description, is_system, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                        ("role-est-ollama", "EstoqueApenas", "Acesso apenas ao estoque", 0))
            
            cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                        ("EstoqueApenas", "estoque", 0, 1, 1, 0))
            cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                        ("EstoqueApenas", "baixa", 1, 1, 0, 0))
            cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                        ("EstoqueApenas", "financeiro", 0, 0, 0, 0))
            cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                        ("EstoqueApenas", "relatorios", 0, 0, 0, 0))
            cur.execute("INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, can_update, can_delete) VALUES (?, ?, ?, ?, ?, ?)",
                        ("EstoqueApenas", "pedidos", 0, 0, 0, 0))

            # Cria usuário com papel 'EstoqueApenas'
            cur.execute(
                "INSERT OR REPLACE INTO usuarios (id, username, password_hash, role, roles, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("user-est-ollama", "op_estoque_ollama", generate_password_hash("op123"), "EstoqueApenas", '["EstoqueApenas"]')
            )

            # Garante a existência de um material para testes de baixa
            cur.execute(
                "INSERT OR REPLACE INTO materiais (id, nome, categoria, emoji, quantidade, unidade, quantidade_minima, custo, gtin, foto, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                ("mat-teste-ollama", "Courino Caramelo", "Courino", "🟫", 50.0, "metros", 5.0, 35.0, "7891234567890", "")
            )

            # Garante a existência de uma bolsa para testes de pedidos
            now_iso = datetime.now().isoformat()
            cur.execute(
                "INSERT OR REPLACE INTO produtos (id, nome, emoji, preco_venda, receita, gtin, estoque_pronto, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("prod-teste-ollama", "Bolsa Tote Artesanal", "👜", 180.0, "[]", "7890001112223", 5, now_iso, now_iso)
            )

            conn.commit()
        finally:
            conn.close()

    def test_ollama_engine_offline_status(self):
        """Verifica se o OllamaEngine reporta offline quando o servidor não está rodando e emulação está desativada."""
        engine = OllamaEngine(host="http://127.0.0.1:59999", timeout=0.5, emulate_if_offline=False)
        self.assertFalse(engine.is_online(force_refresh=True))
        status = engine.get_status()
        self.assertFalse(status["online"])
        self.assertEqual(status["mode"], "contingency_rules")

    def test_contingencia_automatica_quando_ollama_desabilitado(self):
        """Testa se a Ania responde normalmente via regras de contingência quando o Ollama está desabilitado."""
        engine_off = OllamaEngine(enabled=False)
        assistant = AniaAssistant(app, ollama_engine=engine_off)
        user = {"id": "admin-ollama-test", "username": "admin_ollama", "roles": '["Admin"]'}
        res = assistant.processar_mensagem("Quanto couro temos em estoque?", user)
        self.assertIn("Estoque", res["reply"])
        self.assertEqual(res.get("engine"), "regras_locais")

    def test_ia_local_emulada_ativa_em_localhost(self):
        """Testa o motor de IA Local integrado respondendo como IA com badge qwen2.5:3b."""
        engine_local = OllamaEngine(emulate_if_offline=True)
        assistant = AniaAssistant(app, ollama_engine=engine_local)
        user = {"id": "admin-ollama-test", "username": "admin_ollama", "roles": '["Admin"]'}
        res = assistant.processar_mensagem("Olá Ania, tudo bem?", user)
        self.assertTrue(res.get("success", True))
        self.assertEqual(res.get("engine"), "ollama")
        self.assertIn("qwen2.5:3b", res.get("model", ""))

    def test_ollama_tool_calling_dar_baixa(self):
        """Testa a execução de baixa de estoque acionada por Tool Calling da IA."""
        engine_mock = MagicMock(spec=OllamaEngine)
        engine_mock.enabled = True
        engine_mock.is_online.return_value = True
        engine_mock.model = "qwen2.5:3b"
        engine_mock.process_prompt.return_value = {
            "action": "dar_baixa_material",
            "params": {
                "material": "Courino Caramelo",
                "quantidade": 4.0,
                "motivo": "Produção de bolsas"
            },
            "_model": "qwen2.5:3b",
            "_elapsed_ms": 120.5
        }

        with patch("app.get_ania_engine") as mock_get_engine:
            import sys
            assistant = AniaAssistant(sys.modules["app"], ollama_engine=engine_mock)
            mock_get_engine.return_value = assistant

            with app.test_client() as c:
                c.post("/login", data={"username": "admin_ollama", "password": "admin123"}, follow_redirects=True)

                resp = c.post("/api/ania/chat", json={"message": "Baixa quatro metros de courino caramelo para produção"})
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data.get("success"))
                self.assertIn("Baixa Realizada", data["reply"])
                self.assertEqual(data.get("engine"), "ollama")
                self.assertEqual(data.get("model"), "qwen2.5:3b")

                # Verifica se o estoque foi realmente atualizado no banco
                materiais = carregar_materiais()
                mat = next((m for m in materiais if m["id"] == "mat-teste-ollama"), None)
                self.assertIsNotNone(mat)
                self.assertEqual(float(mat["quantidade"]), 46.0)

    def test_ollama_tool_calling_rbac_bloqueado(self):
        """Verifica se o RBAC bloqueia a ação mesmo que a IA tenha acionado a ferramenta."""
        engine_mock = MagicMock(spec=OllamaEngine)
        engine_mock.enabled = True
        engine_mock.is_online.return_value = True
        engine_mock.model = "qwen2.5:3b"
        engine_mock.process_prompt.return_value = {
            "action": "gerar_relatorio_pdf",
            "params": {},
            "_model": "qwen2.5:3b"
        }

        with patch("app.get_ania_engine") as mock_get_engine:
            import sys
            assistant = AniaAssistant(sys.modules["app"], ollama_engine=engine_mock)
            mock_get_engine.return_value = assistant

            with app.test_client() as c:
                c.post("/login", data={"username": "op_estoque_ollama", "password": "op123"}, follow_redirects=True)

                resp = c.post("/api/ania/chat", json={"message": "Gere o relatório financeiro em PDF"})
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data.get("denied"))
                self.assertIn("Acesso Negado", data["reply"])

    def test_ollama_conversacao_direta(self):
        """Testa resposta conversacional do LLM sem disparo de ferramentas."""
        engine_mock = MagicMock(spec=OllamaEngine)
        engine_mock.enabled = True
        engine_mock.is_online.return_value = True
        engine_mock.model = "qwen2.5:3b"
        engine_mock.process_prompt.return_value = {
            "action": "conversar_direto",
            "reply": "Para costurar courino, recomendo usar agulha calibre 16 ou 18 e linha de poliamida nº 60.",
            "voice_text": "Para costurar courino, use agulha calibre 16 e linha de poliamida.",
            "suggestions": ["📦 Consultar estoque", "🧾 Criar novo pedido"],
            "_model": "qwen2.5:3b"
        }

        with patch("app.get_ania_engine") as mock_get_engine:
            import sys
            assistant = AniaAssistant(sys.modules["app"], ollama_engine=engine_mock)
            mock_get_engine.return_value = assistant

            with app.test_client() as c:
                c.post("/login", data={"username": "admin_ollama", "password": "admin123"}, follow_redirects=True)

                resp = c.post("/api/ania/chat", json={"message": "Qual agulha devo usar para costurar courino?"})
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data.get("success"))
                self.assertIn("agulha calibre 16", data["reply"])
                self.assertEqual(data.get("engine"), "ollama")

    def test_auto_selecao_melhor_modelo_disponivel(self):
        """Testa se o OllamaEngine seleciona o modelo de maior capacidade automaticamente."""
        engine = OllamaEngine(model=None)
        # Se tiver 7b e 3b, prioriza 7b
        self.assertEqual(engine._select_best_model(["qwen2.5:3b", "qwen2.5:7b"]), "qwen2.5:7b")
        # Se tiver 14b, prioriza 14b
        self.assertEqual(engine._select_best_model(["qwen2.5:3b", "qwen2.5:14b", "llama3.1:8b"]), "qwen2.5:14b")
        # Se tiver 8b, prioriza 8b
        self.assertEqual(engine._select_best_model(["qwen2.5:3b", "llama3.1:8b"]), "llama3.1:8b")

    def test_acoes_em_lote_via_ania(self):
        """Testa execução de múltiplas ações em lote em uma única mensagem."""
        with app.test_client() as c:
            c.post("/login", data={"username": "admin_ollama", "password": "admin123"}, follow_redirects=True)
            prompt = "Dar entrada de 5 metros de Courino Caramelo e 10 metros de Courino Caramelo"
            resp = c.post("/api/ania/chat", json={"message": prompt})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertIn("Operações em Lote", data["reply"])

    def test_memoria_multi_turn_context(self):
        """Testa histórico multi-turn na sessão recuperando contexto da pergunta anterior."""
        with app.test_client() as c:
            c.post("/login", data={"username": "admin_ollama", "password": "admin123"}, follow_redirects=True)
            # Pergunta 1
            resp1 = c.post("/api/ania/chat", json={"message": "Quanto custa a Bolsa Tote Artesanal?"})
            self.assertEqual(resp1.status_code, 200)
            
            # Pergunta 2 (faz referência com pronome 'dela')
            resp2 = c.post("/api/ania/chat", json={"message": "Crie um pedido dela para o Carlos"})
            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.get_json()
            self.assertTrue(data2.get("success"))
            self.assertIn("Carlos", data2["reply"])

    def test_sqlite_wal_mode_active(self):
        """Verifica se o SQLite está operando com modo WAL e concorrência ativa."""
        init_db()
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        modo = cur.fetchone()[0].lower()
        conn.close()
        self.assertEqual(modo, "wal")

    def test_modo_contingencia_estrito_via_api(self):
        """Testa se o endpoint respeita o modo 'contingencia' selecionado pelo usuário."""
        with app.test_client() as c:
            c.post("/login", data={"username": "admin_ollama", "password": "admin123"}, follow_redirects=True)
            resp = c.post("/api/ania/chat", json={"message": "Consultar estoque", "mode": "contingencia"})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("engine"), "regras_locais")

    def test_modo_ia_offline_erro_explicito(self):
        """Testa se no modo IA, quando o Ollama está desativado/offline, retorna erro sem mudar para contingência."""
        import sys
        engine_mock = MagicMock(spec=OllamaEngine)
        engine_mock.enabled = False
        engine_mock.is_online.return_value = False
        
        assistant = AniaAssistant(sys.modules["app"], ollama_engine=engine_mock)
        user = {"username": "admin", "roles": ["Admin"]}
        res = assistant.processar_mensagem("Consultar estoque", user, mode="ia")
        
        self.assertFalse(res.get("success"))
        self.assertIn("IA Offline", res.get("reply"))
        self.assertEqual(res.get("engine"), "ollama_offline")


if __name__ == "__main__":
    unittest.main()
