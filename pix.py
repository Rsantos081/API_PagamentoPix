import io
import unicodedata
from decimal import Decimal

import qrcode


def _limpar(texto: str, limite: int) -> str:
    """Remove acentos, deixa em maiúsculas e corta no limite do padrão Pix."""
    ascii_txt = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return ascii_txt.upper().strip()[:limite]


def _crc16(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _campo(id_: str, valor: str) -> str:
    return f"{id_}{len(valor):02d}{valor}"


def gerar_payload_pix(
    chave: str,
    nome: str,
    cidade: str,
    valor: Decimal | None = None,
    txid: str = "***",
) -> str:
    """Gera o texto do Pix Copia e Cola (BR Code estático).

    valor=None gera um código de valor livre (o pagador digita o valor).
    """
    conta = _campo("00", "br.gov.bcb.pix") + _campo("01", chave)
    payload = (
        _campo("00", "01")
        + _campo("26", conta)
        + _campo("52", "0000")
        + _campo("53", "986")
        + (_campo("54", f"{valor:.2f}") if valor else "")
        + _campo("58", "BR")
        + _campo("59", _limpar(nome, 25))
        + _campo("60", _limpar(cidade, 15))
        + _campo("62", _campo("05", txid))
        + "6304"
    )
    return payload + _crc16(payload)


def qrcode_png(payload: str) -> bytes:
    """Devolve a imagem PNG do QR Code para o payload."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()