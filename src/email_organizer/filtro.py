"""Filtros de seleção de mensagens (specs/config.md#filtros).

Avaliação local sobre a mensagem parseada; fontes podem pré-filtrar no servidor,
mas a decisão final é sempre esta, para PST e IMAP darem o mesmo resultado.
"""

import re
import unicodedata
from datetime import date
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from pydantic import BaseModel, Field


class Filtro(BaseModel):
    data_de: date | None = None
    data_ate: date | None = None
    remetente: list[str] = Field(default_factory=list)
    destinatario: list[str] = Field(default_factory=list)
    assunto: list[str] = Field(default_factory=list)
    corpo: list[str] = Field(default_factory=list)
    com_anexo: bool | None = None
    excluir_remetente: list[str] = Field(default_factory=list)

    def vazio(self) -> bool:
        return self == Filtro()


def normalizar(texto: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return ascii_.casefold()


def _contem(alvo: str, termos: list[str]) -> bool:
    n = normalizar(alvo)
    return any(normalizar(t) in n for t in termos)


def _cab(msg: EmailMessage, *nomes: str) -> str:
    partes = []
    for k, v in msg.raw_items():
        if k.lower() in nomes:
            try:
                partes.append(str(msg.policy.header_factory(k, v)))
            except Exception:
                partes.append(str(v))
    return " ".join(partes)


def _corpo(msg: EmailMessage) -> str:
    textos = []
    for parte in msg.walk():
        if parte.is_attachment() or parte.get_content_maintype() != "text":
            continue
        try:
            conteudo = parte.get_content()
        except Exception:
            conteudo = (parte.get_payload(decode=True) or b"").decode("latin-1", "replace")
        if parte.get_content_subtype() == "html":
            conteudo = re.sub(r"<[^>]+>", " ", conteudo)
        textos.append(conteudo)
    return "\n".join(textos)


def _tem_anexo(msg: EmailMessage) -> bool:
    return any(p.is_attachment() for p in msg.walk())


def _data(msg: EmailMessage) -> date | None:
    try:
        return parsedate_to_datetime(_cab(msg, "date")).date()
    except (TypeError, ValueError):
        return None


def aceita(filtro: Filtro, msg: EmailMessage) -> bool:
    if filtro.data_de or filtro.data_ate:
        d = _data(msg)
        if d is None:
            return False
        if filtro.data_de and d < filtro.data_de:
            return False
        if filtro.data_ate and d > filtro.data_ate:
            return False
    remetente = _cab(msg, "from")
    if filtro.remetente and not _contem(remetente, filtro.remetente):
        return False
    if filtro.excluir_remetente and _contem(remetente, filtro.excluir_remetente):
        return False
    if filtro.destinatario and not _contem(_cab(msg, "to", "cc", "bcc"), filtro.destinatario):
        return False
    if filtro.assunto and not _contem(_cab(msg, "subject"), filtro.assunto):
        return False
    if filtro.com_anexo is not None and _tem_anexo(msg) != filtro.com_anexo:
        return False
    return not filtro.corpo or _contem(_corpo(msg), filtro.corpo)


def aceita_todos(filtros: list[Filtro], msg: EmailMessage) -> bool:
    return all(aceita(f, msg) for f in filtros)
