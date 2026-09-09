"""
test_postgres_adapter.py - Testes unitários para a camada de compatibilidade PostgreSQL
Valida a tradução de queries SQL, o comportamento do DbRow e o gerenciador de conexões.
"""

import os
import unittest
import db


class TestPostgresAdapter(unittest.TestCase):

    def test_dbrow_access_and_unpacking(self):
        """Valida se DbRow se comporta identicamente a sqlite3.Row e dicionário."""
        description = [("id",), ("nome",), ("quantidade",), ("custo",)]
        values = ("mat-test-01", "Couro Nobre", 15.5, 45.0)
        row = db.DbRow(values, description)

        # Acesso por índice numérico
        self.assertEqual(row[0], "mat-test-01")
        self.assertEqual(row[1], "Couro Nobre")
        self.assertEqual(row[2], 15.5)
        self.assertEqual(row[3], 45.0)

        # Acesso por chave (case-insensitive)
        self.assertEqual(row["id"], "mat-test-01")
        self.assertEqual(row["nome"], "Couro Nobre")
        self.assertEqual(row["NOME"], "Couro Nobre")
        self.assertEqual(row["quantidade"], 15.5)
        self.assertEqual(row.get("custo"), 45.0)
        self.assertIsNone(row.get("nao_existe"))

        # Desempacotamento de tupla (a, b, c, d = row)
        a, b, c, d = row
        self.assertEqual(a, "mat-test-01")
        self.assertEqual(b, "Couro Nobre")
        self.assertEqual(c, 15.5)
        self.assertEqual(d, 45.0)

        # Conversão direta dict(row)
        as_dict = dict(row)
        self.assertIsInstance(as_dict, dict)
        self.assertEqual(as_dict["id"], "mat-test-01")
        self.assertEqual(as_dict["nome"], "Couro Nobre")
        self.assertEqual(as_dict["quantidade"], 15.5)

        # Keys e len
        self.assertEqual(row.keys(), ["id", "nome", "quantidade", "custo"])
        self.assertEqual(len(row), 4)

    def test_sql_placeholder_replacement(self):
        """Valida que marcadores '?' viram '%s' sem alterar strings literais."""
        q1 = "SELECT * FROM materiais WHERE id = ? AND status = ?"
        self.assertEqual(db.convert_sqlite_to_pg(q1), "SELECT * FROM materiais WHERE id = %s AND status = %s")

        # Não deve substituir '?' dentro de strings literais
        q2 = "SELECT * FROM pedidos WHERE observacoes = 'Duvida? Sim' AND id = ?"
        conv2 = db.convert_sqlite_to_pg(q2)
        self.assertIn("'Duvida? Sim'", conv2)
        self.assertTrue(conv2.endswith("id = %s"))

    def test_sql_insert_or_ignore(self):
        """Valida conversão de INSERT OR IGNORE para ON CONFLICT DO NOTHING."""
        q = "INSERT OR IGNORE INTO produtos (id, nome, emoji) VALUES (?, ?, ?)"
        conv = db.convert_sqlite_to_pg(q)
        self.assertTrue(conv.startswith("INSERT INTO produtos"))
        self.assertTrue(conv.endswith("ON CONFLICT DO NOTHING"))
        self.assertIn("(%s, %s, %s)", conv)

    def test_sql_insert_or_replace(self):
        """Valida conversão de INSERT OR REPLACE (UPSERT) para PostgreSQL."""
        # Tabela com PK composta (role, resource)
        q_role = "INSERT OR REPLACE INTO role_permissions (role, resource, can_create, can_read, updated_at) VALUES (?, ?, ?, ?, ?)"
        conv_role = db.convert_sqlite_to_pg(q_role)
        self.assertTrue(conv_role.startswith("INSERT INTO role_permissions"))
        self.assertIn("ON CONFLICT (role, resource) DO UPDATE SET", conv_role)
        self.assertIn("can_create = EXCLUDED.can_create", conv_role)
        self.assertIn("can_read = EXCLUDED.can_read", conv_role)

        # Tabela com PK simples (key)
        q_meta = "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('seed_v5', '1')"
        conv_meta = db.convert_sqlite_to_pg(q_meta)
        self.assertIn("ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", conv_meta)

    def test_sql_pragma_and_begin_immediate(self):
        """Valida que comandos específicos do SQLite viram comentários no PostgreSQL."""
        self.assertTrue(db.convert_sqlite_to_pg("PRAGMA journal_mode = WAL;").startswith("-- PRAGMA"))
        self.assertTrue(db.convert_sqlite_to_pg("BEGIN IMMEDIATE").startswith("-- BEGIN IMMEDIATE"))
        self.assertEqual(
            db.convert_sqlite_to_pg("SELECT data FROM collections WHERE name=? ORDER BY rowid DESC"),
            "SELECT data FROM collections WHERE name=%s ORDER BY ctid DESC"
        )

    def test_db_connection_provider(self):
        """Valida que get_db_connection retorna conexão funcional e operacional."""
        conn = db.get_db_connection()
        self.assertIsNotNone(conn)
        cur = conn.cursor()
        cur.execute("SELECT 1 as teste")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)
        conn.close()

    def test_get_db_stats(self):
        """Valida que get_db_stats retorna dicionário consistente e preenchido."""
        stats = db.get_db_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("engine", stats)
        self.assertIn("connected", stats)
        self.assertIn("tables", stats)


if __name__ == "__main__":
    unittest.main()
