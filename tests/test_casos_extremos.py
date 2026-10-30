"""Casos extremos (onda 7): TNEF, S/MIME, PDF protegido, .msg, charset inválido."""

import base64
from datetime import UTC, datetime
from email.message import EmailMessage
from types import SimpleNamespace

from construtores import pdf_cifrado, tnef
from email_organizer.artefatos import conversao
from email_organizer.artefatos.mime import decompor, parse


def _com_anexo(nome: str, dados: bytes, mime: str) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "a@x.com", "b@x.com", "teste"
    m.set_content("corpo")
    principal, sub = mime.split("/")
    m.add_attachment(dados, maintype=principal, subtype=sub, filename=nome)
    return m.as_bytes()


def test_tnef_expande_anexos_internos():
    bruto = _com_anexo(
        "winmail.dat", tnef({"contrato.txt": b"ola", "foto.png": b"\x89PNG"}), "application/ms-tnef"
    )
    est = decompor(parse(bruto))
    nomes = [p.nome for p in est.anexos]
    assert nomes == ["winmail.dat", "contrato.txt", "foto.png"]
    assert est.anexos[1].dados == b"ola"
    assert any("TNEF" in a and "2 anexos" in a for a in est.avisos)
    assert conversao.tipo_conversao("winmail.dat", "application/ms-tnef") == "tnef"


def test_tnef_corrompido_vira_aviso():
    est = decompor(parse(_com_anexo("winmail.dat", b"lixo", "application/ms-tnef")))
    assert [p.nome for p in est.anexos] == ["winmail.dat"]
    assert any("TNEF" in a for a in est.avisos)


ASSINADO = (
    b"""From: a@x.com
To: b@x.com
Subject: assinado
MIME-Version: 1.0
Content-Type: multipart/signed; protocol="application/pkcs7-signature"; micalg=sha-256; boundary="B"

--B
Content-Type: text/plain; charset=utf-8

texto assinado
--B
Content-Type: application/pkcs7-signature; name="smime.p7s"
Content-Disposition: attachment; filename="smime.p7s"
Content-Transfer-Encoding: base64

"""
    + base64.b64encode(b"assinatura-der")
    + b"""
--B--
"""
)


def test_smime_assinado_extrai_conteudo_e_guarda_assinatura():
    est = decompor(parse(ASSINADO))
    assert est.texto.strip() == "texto assinado"
    assert [(p.nome, p.dados) for p in est.anexos] == [("smime.p7s", b"assinatura-der")]
    assert conversao.tipo_conversao("smime.p7s", "application/pkcs7-signature") == "assinatura"


CIFRADO = (
    b"""From: a@x.com
To: b@x.com
Subject: cifrado
MIME-Version: 1.0
Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"
Content-Disposition: attachment; filename="smime.p7m"
Content-Transfer-Encoding: base64

"""
    + base64.b64encode(b"cifrado-der")
    + b"\n"
)


def test_smime_cifrado_vira_aviso():
    est = decompor(parse(CIFRADO))
    assert [p.nome for p in est.anexos] == ["smime.p7m"]
    assert any("S/MIME cifrad" in a for a in est.avisos)
    assert conversao.tipo_conversao("smime.p7m", "application/pkcs7-mime") == "smime"


def test_charset_invalido_usa_cp1252_com_aviso():
    bruto = b"From: a@x.com\nSubject: x\nContent-Type: text/plain; charset=nao-existe\n\nol\xe9\n"
    est = decompor(parse(bruto))
    assert "é" in est.texto and any("charset" in a for a in est.avisos)


def test_pdf_protegido():
    assert conversao.pdf_protegido(pdf_cifrado())
    assert not conversao.pdf_protegido(b"%PDF-1.4 lixo")  # ilegível ≠ protegido


def _anexo_msg(nome: str, dados) -> SimpleNamespace:
    return SimpleNamespace(
        longFilename=nome, shortFilename=None, data=dados, mimetype=None, contentId=None
    )


def test_msg_para_eml():
    interna = SimpleNamespace(
        sender="Joao <j@x.com>", to="eu@x.com", cc=None, subject="interna", messageId="<i@x>",
        inReplyTo=None, date=datetime(2024, 5, 1, 10, 0, tzinfo=UTC),
        body="texto interno", htmlBody=None, attachments=[],
    )  # fmt: skip
    msg = SimpleNamespace(
        sender="Maria <m@x.com>", to="eu@x.com; outro@x.com", cc="c@x.com", subject="Proposta",
        messageId="<p@x>", inReplyTo="<pai@x>",
        date=datetime(2024, 5, 2, 9, 30, tzinfo=UTC),
        body="texto", htmlBody=b"<p>html</p>",
        attachments=[_anexo_msg("proposta.pdf", b"%PDF"), _anexo_msg("interna.msg", interna)],
    )  # fmt: skip
    eml = parse(conversao._eml_de_msg(msg))
    assert eml["Subject"] == "Proposta" and eml["Message-ID"] == "<p@x>"
    assert eml["In-Reply-To"] == "<pai@x>" and "outro@x.com" in eml["To"]
    est = decompor(eml)
    assert "html" in est.html and est.texto.strip() == "texto"
    assert [p.mime for p in est.anexos] == ["application/pdf", "message/rfc822"]
    assert b"Subject: interna" in est.anexos[1].dados


def test_txt_nao_usa_libreoffice():
    assert conversao.tipo_conversao("Message Headers.txt", "text/plain") == "texto"
    assert conversao.tipo_conversao("dados.csv", "text/plain") == "office"


RTF_HTML = (
    rb"{\rtf1\ansi\ansicpg1252\fromhtml1 \deff0{\fonttbl{\f0\fswiss Arial;}}"
    rb"{\*\htmltag64 <html>}{\*\htmltag64 <body>}Ol\'e1 <img src=cid:img1@x>"
    rb"{\*\htmltag72 </body>}{\*\htmltag72 </html>}}"
)


def _readpst(corpo: str | None, rtf: bytes) -> bytes:
    """Formato do readpst sem -b: corpo (se houver) + rtf-body.rtf + imagem."""
    m = EmailMessage()
    m["From"], m["Subject"] = "a@x.com", "rtf"
    if corpo is not None:
        m.set_content(corpo)
    else:
        m.make_mixed()
    m.add_attachment(rtf, maintype="application", subtype="rtf", filename="rtf-body.rtf")
    m.add_attachment(b"\x89PNG", maintype="image", subtype="png", filename="image001.png",
                     cid="<img1@x>")  # fmt: skip
    return m.as_bytes()


def test_corpo_so_em_rtf_vira_corpo():
    est = decompor(parse(_readpst(None, RTF_HTML)))
    assert "Olá" in est.html
    assert [p.nome for p in est.anexos] == []  # rtf-body some; imagem referenciada vira inline
    assert [p.nome for p in est.inline] == ["image001.png"]
    assert any("RTF" in a for a in est.avisos)


def test_rtf_duplicado_do_corpo_e_descartado():
    est = decompor(parse(_readpst("texto normal", RTF_HTML)))
    assert est.texto.strip() == "texto normal"
    assert "rtf-body.rtf" not in [p.nome for p in est.anexos]


def test_rtf_puro_sem_corpo_fica_como_anexo():
    est = decompor(parse(_readpst(None, rb"{\rtf1\ansi puro}")))
    assert "rtf-body.rtf" in [p.nome for p in est.anexos]


def test_multipart_vazio_nao_vira_anexo():
    bruto = (
        b"From: a@x.com\nSubject: x\nMIME-Version: 1.0\nContent-Type: multipart/mixed; "
        b'boundary="B"\n\n--B--\n'
    )
    assert decompor(parse(bruto)).anexos == []
