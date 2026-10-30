"""Decomposição MIME: corpo (html/texto), partes inline e anexos."""

import mimetypes
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

RTF_READPST = "rtf-body.rtf"  # readpst (sem -b) anexa o corpo RTF com este nome
_CID_REF = re.compile(r"""\bsrc\s*=\s*["']?cid:([^"'\s>]+)""", re.IGNORECASE)


@dataclass
class Parte:
    nome: str
    mime: str
    dados: bytes
    content_id: str | None = None


@dataclass
class Estrutura:
    html: str | None
    texto: str | None
    inline: list[Parte] = field(default_factory=list)  # ordem de aparição no corpo
    anexos: list[Parte] = field(default_factory=list)  # e-mails anexados: mime message/rfc822
    avisos: list[str] = field(default_factory=list)


def parse(bruto: bytes) -> EmailMessage:
    return BytesParser(policy=policy.default).parsebytes(bruto)


def _folhas(parte: EmailMessage) -> Iterator[EmailMessage]:
    """Partes não-multipart; não desce em message/rfc822 (e-mail anexado é uma folha)."""
    if parte.get_content_type() == "message/rfc822":
        yield parte
    elif parte.is_multipart():
        for sub in parte.iter_parts():
            yield from _folhas(sub)
    elif parte.get_content_maintype() == "multipart":
        return  # multipart sem partes (readpst gera para mensagem sem corpo): não é anexo
    else:
        yield parte


def texto_da_parte(parte: EmailMessage, avisos: list[str]) -> str:
    try:
        return parte.get_content()
    except Exception as e:  # charset desconhecido/inválido
        dados = parte.get_payload(decode=True) or b""
        avisos.append(f"charset inválido em {parte.get_content_type()} ({e}); usado cp1252")
        return dados.decode("cp1252", "replace")


def dados_da_parte(parte: EmailMessage) -> bytes:
    if parte.get_content_type() == "message/rfc822":
        interna = parte.get_payload()
        interna = interna[0] if isinstance(interna, list) else interna
        return interna.as_bytes(policy=policy.SMTP)
    return parte.get_payload(decode=True) or b""


def _cid(parte: EmailMessage) -> str | None:
    valor = parte.get("Content-ID")
    return str(valor).strip().strip("<>") if valor else None


def _nome(parte: EmailMessage, padrao: str) -> str:
    try:
        nome = parte.get_filename()
    except Exception:
        nome = None
    if nome:
        return nome
    if parte.get_content_type() == "message/rfc822":
        interna = parte.get_payload()
        interna = interna[0] if isinstance(interna, list) else interna
        try:
            assunto = str(interna.get("Subject") or "").strip()
        except Exception:
            assunto = ""
        return f"{assunto[:80] or padrao}.eml"
    ext = mimetypes.guess_extension(parte.get_content_type()) or ".bin"
    return f"{padrao}{ext}"


def decompor(msg: EmailMessage) -> Estrutura:
    avisos: list[str] = []
    corpo_html = msg.get_body(preferencelist=("html",))
    corpo_txt = msg.get_body(preferencelist=("plain",))
    html = texto_da_parte(corpo_html, avisos) if corpo_html is not None else None
    texto = texto_da_parte(corpo_txt, avisos) if corpo_txt is not None else None
    if html is None and texto is None and not msg.is_multipart():
        texto = texto_da_parte(msg, avisos) if msg.get_content_maintype() == "text" else ""

    corpos = {id(corpo_html), id(corpo_txt)}
    rtf = next((p for p in _folhas(msg) if _nome(p, "").lower() == RTF_READPST), None)
    if rtf is not None:
        if (html or "").strip() or (texto or "").strip():
            corpos.add(id(rtf))  # cópia do corpo em RTF gerada pelo readpst: não é anexo
        else:
            html_rtf, texto_rtf = _corpo_rtf(dados_da_parte(rtf), avisos)
            if html_rtf is not None or texto_rtf is not None:
                html, texto = html_rtf, texto_rtf
                corpos.add(id(rtf))

    referenciados = [c.lower() for c in _CID_REF.findall(html or "")]
    inline: dict[str, Parte] = {}
    inline_sem_ref: list[Parte] = []
    anexos: list[Parte] = []

    for n, parte in enumerate(_folhas(msg), start=1):
        if id(parte) in corpos:
            continue
        mime = parte.get_content_type()
        disposicao = parte.get_content_disposition()
        cid = _cid(parte)
        if (
            parte.get_content_maintype() == "text"
            and disposicao is None
            and not parte.get_filename()
        ):
            continue  # alternativa de corpo não escolhida
        p = Parte(_nome(parte, f"parte-{n:03d}"), mime, dados_da_parte(parte), cid)
        referenciada = bool(cid and cid.lower() in referenciados)
        if referenciada and parte.get_content_maintype() == "image":
            inline[cid.lower()] = p  # exibida no corpo, mesmo marcada como attachment (Outlook)
        elif parte.get_content_maintype() == "image" and disposicao != "attachment":
            inline_sem_ref.append(p)
        elif referenciada and disposicao != "attachment":
            inline[cid.lower()] = p
        else:
            anexos.append(p)

    if msg.get_content_type() == "application/pkcs7-mime":
        tipo = msg.get_param("smime-type") or "desconhecido"
        avisos.append(f"S/MIME cifrado/opaco ({tipo}): conteúdo não extraído, original preservado")

    expandidos: list[Parte] = []
    for p in anexos:
        expandidos.append(p)  # o container TNEF também fica (original preservado)
        if eh_tnef(p.nome, p.mime):
            internos, corpo = _tnef(p, avisos)
            expandidos += internos
            if html is None and texto is None and corpo:
                html, texto = corpo

    ordenados = [inline[c] for c in dict.fromkeys(referenciados) if c in inline]
    return Estrutura(html, texto, ordenados + inline_sem_ref, expandidos, avisos)


def _corpo_rtf(dados: bytes, avisos: list[str]) -> tuple[str | None, str | None]:
    """Corpo só em RTF (Outlook): extrai o HTML/texto encapsulado. RTF puro → (None, None)
    e o arquivo segue como anexo (LibreOffice converte)."""
    import logging

    from RTFDE.deencapsulate import DeEncapsulator

    logging.getLogger("RTFDE").setLevel(logging.ERROR)
    try:
        d = DeEncapsulator(dados)
        d.deencapsulate()
    except Exception:
        avisos.append("corpo só em RTF não encapsulado: mantido como anexo rtf-body.rtf")
        return None, None
    conteudo = d.html if d.content_type == "html" else d.text
    if isinstance(conteudo, bytes):
        try:
            conteudo = conteudo.decode("utf-8")
        except UnicodeDecodeError:
            conteudo = conteudo.decode("cp1252", "replace")
    avisos.append(f"corpo extraído do RTF do Outlook ({d.content_type})")
    return (conteudo, None) if d.content_type == "html" else (None, conteudo)


def eh_tnef(nome: str, mime: str) -> bool:
    return mime in ("application/ms-tnef", "application/vnd.ms-tnef") or (
        nome.lower() == "winmail.dat"
    )


def _tnef(parte: Parte, avisos: list[str]) -> tuple[list[Parte], tuple[str | None, str | None]]:
    """winmail.dat (Outlook RTF): anexos internos viram anexos normais; corpo como fallback."""
    import tnefparse

    try:
        t = tnefparse.TNEF(parte.dados)
    except Exception as e:
        avisos.append(f"TNEF {parte.nome} ilegível: {e}")
        return [], (None, None)
    internos = []
    for i, a in enumerate(t.attachments, start=1):
        nome = a.long_filename() or a.name or f"tnef-{i:03d}.bin"
        mime = mimetypes.guess_type(nome)[0] or "application/octet-stream"
        internos.append(Parte(nome, mime, a.data or b""))
    avisos.append(f"TNEF {parte.nome}: {len(internos)} anexos extraídos")
    html = t.htmlbody.decode("utf-8", "replace") if isinstance(t.htmlbody, bytes) else t.htmlbody
    texto = t.body.decode("cp1252", "replace") if isinstance(t.body, bytes) else t.body
    return internos, (html or None, texto or None)
