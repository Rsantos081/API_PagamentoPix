import os
import tempfile
import unittest


os.environ["API_KEY"] = "test-api-key"
os.environ["PIX_CHAVE"] = "teste@pix.com"
os.environ["PIX_NOME"] = "Empresa Teste"
os.environ["PIX_CIDADE"] = "Sao Paulo"

temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{temp_db.name}"

from app import Cobranca, app, db


class RotasCobrancaTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        self.headers = {"X-API-Key": "test-api-key"}
        with app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def criar(self, payload=None):
        return self.client.post(
            "/cobrancas",
            json=payload if payload is not None else {"valor": "10.50", "descricao": "Teste", "cliente": "Ana"},
            headers=self.headers,
        )

    def test_criar_listar_obter_cobranca(self):
        criada = self.criar()
        self.assertEqual(criada.status_code, 201)
        dados = criada.get_json()
        cid = dados["id"]
        self.assertEqual(dados["valor"], "10.50")
        self.assertEqual(dados["status"], "pendente")
        self.assertIn(cid, dados["qrcode_url"])
        self.assertIn(cid, dados["pagina_url"])

        listada = self.client.get("/cobrancas", headers=self.headers)
        self.assertEqual(listada.status_code, 200)
        self.assertEqual(len(listada.get_json()), 1)

        obtida = self.client.get(f"/cobrancas/{cid}", headers=self.headers)
        self.assertEqual(obtida.status_code, 200)
        self.assertEqual(obtida.get_json()["id"], cid)

    def test_protecao_api_key_e_validacao_valor(self):
        sem_chave = self.client.post("/cobrancas", json={"valor": "10"})
        chave_errada = self.client.post(
            "/cobrancas",
            json={"valor": "10"},
            headers={"X-API-Key": "errada"},
        )
        valor_invalido = self.criar(payload={"valor": "0"})

        self.assertEqual(sem_chave.status_code, 401)
        self.assertEqual(chave_errada.status_code, 401)
        self.assertEqual(valor_invalido.status_code, 400)
        self.assertIn("valor", valor_invalido.get_json()["erro"])

    def test_rota_nao_encontrada(self):
        resposta = self.client.get("/cobrancas/inexistente", headers=self.headers)
        self.assertEqual(resposta.status_code, 404)
        self.assertIn("não encontrada", resposta.get_json()["erro"])

    def test_pagar_cancelar_e_bloquear_nova_mudanca(self):
        cid = self.criar().get_json()["id"]

        pago = self.client.patch(f"/cobrancas/{cid}/pagar", headers=self.headers)
        self.assertEqual(pago.status_code, 200)
        self.assertEqual(pago.get_json()["status"], "pago")
        self.assertIsNotNone(pago.get_json()["pago_em"])

        pago_novamente = self.client.patch(f"/cobrancas/{cid}/pagar", headers=self.headers)
        self.assertEqual(pago_novamente.status_code, 409)

        cid_cancelar = self.criar({"valor": "20"}).get_json()["id"]
        cancelado = self.client.patch(f"/cobrancas/{cid_cancelar}/cancelar", headers=self.headers)
        self.assertEqual(cancelado.status_code, 200)
        self.assertEqual(cancelado.get_json()["status"], "cancelado")

    def test_filtros_qrcode_e_paginas(self):
        cid = self.criar({"valor": "12.34", "descricao": "Assinatura"}).get_json()["id"]

        filtro = self.client.get("/cobrancas?status=pendente", headers=self.headers)
        self.assertEqual(filtro.status_code, 200)
        self.assertEqual(len(filtro.get_json()), 1)

        imagem = self.client.get(f"/cobrancas/{cid}/qrcode.png")
        self.assertEqual(imagem.status_code, 200)
        self.assertEqual(imagem.mimetype, "image/png")
        self.assertTrue(imagem.data.startswith(b"\x89PNG"))

        pagina = self.client.get(f"/pagar/{cid}")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Pagar R$ 12.34", pagina.get_data(as_text=True))

    def test_qrcode_e_pagina_inexistentes(self):
        imagem = self.client.get("/cobrancas/inexistente/qrcode.png")
        pagina = self.client.get("/pagar/inexistente")
        self.assertEqual(imagem.status_code, 404)
        self.assertEqual(pagina.status_code, 404)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        try:
            os.unlink(temp_db.name)
        except OSError:
            pass
