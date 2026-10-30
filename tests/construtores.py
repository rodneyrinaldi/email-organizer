"""Gera em memória formatos binários difíceis de versionar como fixture (TNEF, PDF cifrado)."""

import struct

TNEF_ASSINATURA = 0x223E9F78
NIVEL_MENSAGEM, NIVEL_ANEXO = 1, 2
ATT_TNEF_VERSION = 0x00089006
ATT_ATTACH_REND_DATA = 0x00069002
ATT_ATTACH_TITLE = 0x00018010
ATT_ATTACH_DATA = 0x0006800F


def _atributo(nivel: int, id_: int, dados: bytes) -> bytes:
    soma = sum(dados) & 0xFFFF
    return struct.pack("<BII", nivel, id_, len(dados)) + dados + struct.pack("<H", soma)


def tnef(anexos: dict[str, bytes]) -> bytes:
    """winmail.dat mínimo com os anexos dados (nome → conteúdo)."""
    corpo = struct.pack("<IH", TNEF_ASSINATURA, 0x0001)
    corpo += _atributo(NIVEL_MENSAGEM, ATT_TNEF_VERSION, struct.pack("<I", 0x00010000))
    for nome, dados in anexos.items():
        corpo += _atributo(NIVEL_ANEXO, ATT_ATTACH_REND_DATA, b"\x01\x00" + b"\x00" * 12)
        corpo += _atributo(NIVEL_ANEXO, ATT_ATTACH_TITLE, nome.encode("latin-1") + b"\x00")
        corpo += _atributo(NIVEL_ANEXO, ATT_ATTACH_DATA, dados)
    return corpo


def pdf_cifrado() -> bytes:
    import pymupdf

    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "sigiloso")
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
