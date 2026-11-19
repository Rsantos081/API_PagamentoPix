import hmac
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request, url_for
from flask_sqlalchemy import SQLAlchemy

from pix import gerar_payload_pix, qrcode_png

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///cobrancas.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ---------------------------------------------------------------- modelo
class Cobranca(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    txid = db.Column(db.String(25), unique=True, nullable=False)
    valor = db.Column(db.Numeric(10, 2), nullable=False)
    descricao = db.Column(db.String(200), default="")
    cliente = db.Column(db.String(100), default="")
    status = db.Column(db.String(20), default="pendente", nullable=False)
    copia_e_cola = db.Column(db.Text, nullable=False)
    criado_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    pago_em = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "txid": self.txid,
            "valor": str(self.valor),
            "descricao": self.descricao,
            "cliente": self.cliente,
            "status": self.status,
            "copia_e_cola": self.copia_e_cola,
            "qrcode_url": url_for("qrcode_png_rota", cid=self.id, _external=True),
            "pagina_url": url_for("pagina_pagamento", cid=self.id, _external=True),
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
            "pago_em": self.pago_em.isoformat() if self.pago_em else None,
        }


with app.app_context():
    db.create_all()


# ------------------------------------------------------------ utilidades
def exige_chave(f):
    """Protege rotas administrativas com o header X-API-Key."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        esperada = os.getenv("API_KEY", "")
        enviada = request.headers.get("X-API-Key", "")
        if not esperada or not hmac.compare_digest(enviada, esperada):
            return jsonify(erro="não autorizado"), 401
        return f(*args, **kwargs)

    return wrapper


def parse_valor(bruto):
    try:
        valor = Decimal(str(bruto)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return valor if valor > 0 else None


def config_pix():
    chave = os.getenv("PIX_CHAVE")
    nome = os.getenv("PIX_NOME")
    cidade = os.getenv("PIX_CIDADE")
    if not (chave and nome and cidade):
        return None
    return chave, nome, cidade


# ---------------------------------------------------------- rotas da API
@app.post("/cobrancas")
@exige_chave
def criar_cobranca():
    dados = request.get_json(silent=True) or {}

    valor = parse_valor(dados.get("valor"))
    if valor is None:
        return jsonify(erro="informe um 'valor' maior que zero"), 400

    pix = config_pix()
    if pix is None:
        return jsonify(erro="configure PIX_CHAVE, PIX_NOME e PIX_CIDADE no .env"), 500

    cid = uuid.uuid4().hex
    txid = cid[:25]  # txid Pix: até 25 caracteres alfanuméricos
    payload = gerar_payload_pix(*pix, valor=valor, txid=txid)

    cobranca = Cobranca(
        id=cid,
        txid=txid,
        valor=valor,
        descricao=str(dados.get("descricao", ""))[:200],
        cliente=str(dados.get("cliente", ""))[:100],
        copia_e_cola=payload,
    )
    db.session.add(cobranca)
    db.session.commit()
    return jsonify(cobranca.to_dict()), 201


@app.get("/cobrancas")
@exige_chave
def listar_cobrancas():
    consulta = Cobranca.query.order_by(Cobranca.criado_em.desc())
    status = request.args.get("status")
    if status:
        consulta = consulta.filter_by(status=status)
    return jsonify([c.to_dict() for c in consulta.all()])


@app.get("/cobrancas/<cid>")
@exige_chave
def obter_cobranca(cid):
    cobranca = db.session.get(Cobranca, cid)
    if not cobranca:
        return jsonify(erro="cobrança não encontrada"), 404
    return jsonify(cobranca.to_dict())


def _mudar_status(cid, novo_status):
    cobranca = db.session.get(Cobranca, cid)
    if not cobranca:
        return jsonify(erro="cobrança não encontrada"), 404
    if cobranca.status != "pendente":
        return jsonify(erro=f"cobrança já está '{cobranca.status}'"), 409
    cobranca.status = novo_status
    if novo_status == "pago":
        cobranca.pago_em = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(cobranca.to_dict())


@app.patch("/cobrancas/<cid>/pagar")
@exige_chave
def marcar_pago(cid):
    return _mudar_status(cid, "pago")


@app.patch("/cobrancas/<cid>/cancelar")
@exige_chave
def cancelar(cid):
    return _mudar_status(cid, "cancelado")


# ----------------------------------------------- rotas públicas (cliente)
@app.get("/cobrancas/<cid>/qrcode.png")
def qrcode_png_rota(cid):
    cobranca = db.session.get(Cobranca, cid)
    if not cobranca:
        return jsonify(erro="cobrança não encontrada"), 404
    return Response(qrcode_png(cobranca.copia_e_cola), mimetype="image/png")


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pagamento Pix</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 420px; margin: 40px auto; padding: 0 16px; text-align: center; }
    img { width: 260px; height: 260px; }
    textarea { width: 100%; height: 90px; font-size: 12px; }
    button { padding: 10px 20px; font-size: 16px; cursor: pointer; }
  </style>
</head>
<body>
  {% if c.status == "pago" %}
    <h2>Pagamento confirmado ✅</h2>
    <p>R$ {{ c.valor }} — {{ c.descricao }}</p>
  {% elif c.status == "cancelado" %}
    <h2>Cobrança cancelada</h2>
  {% else %}
    <h2>Pagar R$ {{ c.valor }}</h2>
    <p>{{ c.descricao }}</p>
    <img src="{{ url_for('qrcode_png_rota', cid=c.id) }}" alt="QR Code Pix">
    <p>Ou use o Pix Copia e Cola:</p>
    <textarea id="codigo" readonly>{{ c.copia_e_cola }}</textarea><br><br>
    <button onclick="copiar()">Copiar código</button>
    <p id="msg"></p>
    <script>
      function copiar() {
        navigator.clipboard.writeText(document.getElementById("codigo").value);
        document.getElementById("msg").textContent = "Código copiado!";
      }
    </script>
  {% endif %}
</body>
</html>
"""


@app.get("/pagar/<cid>")
def pagina_pagamento(cid):
    cobranca = db.session.get(Cobranca, cid)
    if not cobranca:
        return "Cobrança não encontrada", 404
    return render_template_string(PAGINA, c=cobranca)


if __name__ == "__main__":
    app.run(debug=True)