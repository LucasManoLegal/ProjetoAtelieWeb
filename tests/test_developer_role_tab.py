import unittest
import sqlite3
import json
from app import (
    app, DB_PATH, init_db, carregar_papeis, encontrar_papel,
    is_user_developer, user_has_permission, user_can_access_tab,
    generate_password_hash, serializar_roles, obter_configuracoes_sso
)


class TestDeveloperRoleTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.testing = True

    def setUp(self):
        init_db()
        self.client = app.test_client()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM usuarios")
        cur.execute("DELETE FROM roles WHERE is_system=0")

        now = "2026-08-21T19:00:00"
        # Seed test developer
        cur.execute(
            """
            INSERT INTO usuarios (id, username, password_hash, role, roles, nome, email, created_at, session_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            ("dev-user-id", "dev_master", generate_password_hash("devpass123"), "Developer", serializar_roles(["Developer"]), "Dev Master", "dev@ateliehaiti.com", now)
        )
        # Seed test admin (admin comum sem developer)
        cur.execute(
            """
            INSERT INTO usuarios (id, username, password_hash, role, roles, nome, email, created_at, session_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            ("admin-user-id", "admin_comum", generate_password_hash("adminpass123"), "Admin", serializar_roles(["Admin"]), "Admin Comum", "admin@ateliehaiti.com", now)
        )
        # Seed regular user (produção)
        cur.execute(
            """
            INSERT INTO usuarios (id, username, password_hash, role, roles, nome, email, created_at, session_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            ("prod-user-id", "joao_prod", generate_password_hash("prodpass123"), "Producao", serializar_roles(["Producao"]), "João Produção", "joao@ateliehaiti.com", now)
        )
        conn.commit()
        conn.close()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["session_version"] = 0

    def test_01_developer_role_seeded_and_permissions(self):
        """Verifica que o papel Developer existe, é de sistema e tem permissões completas."""
        papeis = carregar_papeis()
        role_names = [p["name"] for p in papeis]
        self.assertIn("Developer", role_names)
        self.assertIn("Admin", role_names)

        dev_role = encontrar_papel("Developer")
        self.assertIsNotNone(dev_role)
        self.assertTrue(dev_role["permissions"]["developer"]["can_read"])
        self.assertTrue(dev_role["permissions"]["estoque"]["can_read"])
        self.assertTrue(dev_role["permissions"]["pedidos"]["can_create"])

        admin_role = encontrar_papel("Admin")
        self.assertIsNotNone(admin_role)
        # Admin não tem permissão para a aba developer
        self.assertFalse(admin_role["permissions"].get("developer", {}).get("can_read", False))

    def test_02_is_user_developer_helper(self):
        """Testa o helper is_user_developer com diferentes configurações de usuários."""
        dev_u = {"roles": ["Developer"]}
        admin_u = {"roles": ["Admin"]}
        multi_dev = {"roles": ["Admin", "Developer"]}
        prod_u = {"roles": ["Producao"]}

        self.assertTrue(is_user_developer(dev_u))
        self.assertTrue(is_user_developer(multi_dev))
        self.assertFalse(is_user_developer(admin_u))
        self.assertFalse(is_user_developer(prod_u))
        self.assertFalse(is_user_developer(None))

    def test_03_admin_cannot_access_developer_tab(self):
        """Admin comum não tem acesso à aba Developer Hub nem a suas ações restritas."""
        self._login("admin-user-id")

        # GET /developer
        r1 = self.client.get("/developer", follow_redirects=False)
        self.assertEqual(r1.status_code, 302)

        # GET /dev
        r2 = self.client.get("/dev", follow_redirects=False)
        self.assertEqual(r2.status_code, 302)

        # POST /developer/sso/salvar
        r3 = self.client.post("/developer/sso/salvar", data={"google_client_id": "hack"}, follow_redirects=False)
        self.assertEqual(r3.status_code, 302)

        # GET /developer/db/backup
        r4 = self.client.get("/developer/db/backup", follow_redirects=False)
        self.assertEqual(r4.status_code, 302)

    def test_04_developer_accesses_developer_hub_and_all_tabs(self):
        """Developer tem acesso total ao Developer Hub e a todas as outras abas do sistema."""
        self._login("dev-user-id")

        # Developer Hub
        r_dev = self.client.get("/developer", follow_redirects=True)
        self.assertEqual(r_dev.status_code, 200)
        self.assertIn("Developer Hub", r_dev.data.decode("utf-8"))

        # Abas de negócio
        for rota in ["/estoque", "/produtos", "/pedidos", "/financeiro", "/alertas", "/usuarios", "/roles"]:
            r = self.client.get(rota, follow_redirects=True)
            self.assertEqual(r.status_code, 200, f"Falha ao acessar rota: {rota}")

    def test_05_admin_cannot_assign_developer_role_on_create(self):
        """Admin não consegue criar um novo usuário atribuindo o papel Developer."""
        self._login("admin-user-id")

        r = self.client.post("/usuarios/novo", data={
            "username": "novo_tentativa_dev",
            "password": "senha",
            "password2": "senha",
            "roles": ["Developer", "Estoque"],
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)

        # Usuário criado não deve ter o papel Developer
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE username='novo_tentativa_dev'")
        row = cur.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        roles = json.loads(row["roles"]) if row["roles"] else []
        self.assertNotIn("Developer", roles)
        self.assertIn("Estoque", roles)

    def test_06_admin_cannot_edit_or_delete_developer_user(self):
        """Admin não consegue editar nem excluir uma conta de usuário que possua papel Developer."""
        self._login("admin-user-id")

        # Tentativa de editar o usuário Developer (dev-user-id)
        r_edit = self.client.post("/usuarios/dev-user-id/editar", data={
            "email": "hacked@ateliehaiti.com",
            "roles": ["Estoque"],
        }, follow_redirects=True)
        self.assertEqual(r_edit.status_code, 200)
        self.assertIn("Acesso negado", r_edit.data.decode("utf-8"))

        # Verifica que o email e papel do Developer continuam intactos
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE id='dev-user-id'")
        row = cur.fetchone()
        self.assertEqual(row["email"], "dev@ateliehaiti.com")

        # Tentativa de excluir o usuário Developer
        r_del = self.client.post("/usuarios/dev-user-id/excluir", follow_redirects=True)
        self.assertEqual(r_del.status_code, 200)
        self.assertIn("Acesso negado", r_del.data.decode("utf-8"))

        cur.execute("SELECT * FROM usuarios WHERE id='dev-user-id'")
        self.assertIsNotNone(cur.fetchone())
        conn.close()

    def test_07_admin_cannot_edit_developer_role(self):
        """Admin não consegue editar as permissões do papel Developer."""
        self._login("admin-user-id")

        r = self.client.get("/roles/Developer/editar", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Acesso negado", r.data.decode("utf-8"))

    def test_08_developer_hub_sso_and_ollama_actions(self):
        """Developer atualiza credenciais SSO e configurações do motor Ollama."""
        self._login("dev-user-id")

        # Salvar SSO
        r_sso = self.client.post("/developer/sso/salvar", data={
            "google_client_id": "novo-client-id.apps.googleusercontent.com",
            "google_client_secret": "nova-secret-super-segura",
            "ativo": "1",
            "auto_cadastro": "1",
            "papel_padrao": "Producao",
        }, follow_redirects=True)
        self.assertEqual(r_sso.status_code, 200)

        cfg = obter_configuracoes_sso()
        self.assertEqual(cfg.get("google_client_id"), "novo-client-id.apps.googleusercontent.com")
        self.assertEqual(cfg.get("google_client_secret"), "nova-secret-super-segura")

        # Salvar Ollama
        r_ollama = self.client.post("/developer/ollama/salvar", data={
            "ollama_host": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2",
            "ollama_timeout": "30",
            "ollama_emulate": "1",
        }, follow_redirects=True)
        self.assertEqual(r_ollama.status_code, 200)

    def test_09_developer_hub_database_backup_and_optimize(self):
        """Developer realiza download de backup do banco e otimização VACUUM."""
        self._login("dev-user-id")

        # Download Backup
        r_backup = self.client.get("/developer/db/backup")
        self.assertEqual(r_backup.status_code, 200)
        self.assertIn("application/x-sqlite3", r_backup.headers.get("Content-Type", ""))

        # Otimizar Banco
        r_opt = self.client.post("/developer/db/otimizar", follow_redirects=True)
        self.assertEqual(r_opt.status_code, 200)
        self.assertIn("otimizado com sucesso", r_opt.data.decode("utf-8"))

    def test_10_developer_hub_security_invalidate_sessions(self):
        """Developer invalida todas as sessões ativas com sucesso."""
        self._login("dev-user-id")

        r = self.client.post("/developer/seguranca/invalidar-sessoes", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("desconectadas com sucesso", r.data.decode("utf-8"))

    def test_11_developer_hub_database_config_and_test_connection(self):
        """Testa o painel de configuração e alternância de banco de dados do Developer Hub."""
        # 1. Não-developer (Admin comum) é barrado
        self._login("admin-user-id")
        r_admin_cfg = self.client.post("/developer/db/configurar", data={"engine": "sqlite"})
        self.assertEqual(r_admin_cfg.status_code, 302)

        r_admin_test = self.client.post("/developer/db/testar-conexao", json={"engine": "sqlite"})
        self.assertEqual(r_admin_test.status_code, 302)

        # 2. Developer tem acesso total
        self._login("dev-user-id")

        # GET /developer?tab=database exibe o formulário de configuração e os campos
        r_page = self.client.get("/developer?tab=database")
        self.assertEqual(r_page.status_code, 200)
        page_html = r_page.data.decode("utf-8")
        self.assertIn("Alternância & Configuração do Banco de Dados", page_html)
        self.assertIn("btn_testar_db", page_html)
        self.assertIn("form_db_config", page_html)

        # 3. Testar conexão SQLite local via AJAX
        r_test_sqlite = self.client.post("/developer/db/testar-conexao", json={"engine": "sqlite"})
        self.assertEqual(r_test_sqlite.status_code, 200)
        data_sqlite = r_test_sqlite.get_json()
        self.assertTrue(data_sqlite.get("success"))
        self.assertIn("SQLite local", data_sqlite.get("message"))

        # 4. Testar conexão com credencial inválida de PostgreSQL
        r_test_pg_fail = self.client.post("/developer/db/testar-conexao", json={
            "engine": "postgres",
            "host": "127.0.0.1",
            "port": "54999",
            "dbname": "fake_db",
            "user": "fake_user",
            "password": "wrong"
        })
        self.assertEqual(r_test_pg_fail.status_code, 200)
        data_pg_fail = r_test_pg_fail.get_json()
        self.assertFalse(data_pg_fail.get("success"))
        self.assertIn("Falha na conexão", data_pg_fail.get("message"))

        # 5. Salvar configuração de banco de dados
        r_save = self.client.post("/developer/db/configurar", data={
            "engine": "sqlite",
            "host": "172.16.36.14",
            "port": "5432",
            "dbname": "postgres",
            "user": "root",
            "password": "",
            "fallback": "1"
        }, follow_redirects=True)
        self.assertEqual(r_save.status_code, 200)
        save_html = r_save.data.decode("utf-8")
        self.assertIn("Configurações do banco salvas com sucesso", save_html)

        # 6. Verifica log de auditoria
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT action, details FROM audits WHERE action='configurar_banco' ORDER BY created_at DESC LIMIT 1")
        audit_row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row[0], "configurar_banco")
        self.assertIn("engine=sqlite", audit_row[1])


if __name__ == "__main__":
    unittest.main()

