"""Captura: .eml bruto → diretório da mensagem com original/ e metadata.json.

Não gera artefatos derivados (onda 2). Garante dedup e escrita atômica do diretório.
"""

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Literal

from email_organizer import __version__
from email_organizer.modelos import Aviso, ChaveDedup, Etapa, Metadata, Origem, Pessoa, Thread
from email_organizer.repositorio import (
    ENVIADAS,
    METADATA,
    SEM_DESTINATARIO,
    Repositorio,
    gravar_atomico,
    gravar_metadata,
    info_arquivo,
    ler_metadata,
    nome_conta,
    nome_diretorio,
    nome_remetente,
)

ORIGINAL = "original/mensagem.eml"
_PREFIXO_RESPOSTA = re.compile(r"^\s*(re|res|resp|aw|sv)\s*:", re.IGNORECASE)
_PREFIXO_ENCAMINHAMENTO = re.compile(r"^\s*(fw|fwd|enc|tr|wg)\s*:", re.IGNORECASE)
_MSG_ID = re.compile(r"<[^<>\s]+>")


@dataclass
class ResultadoCaptura:
    status: Literal["nova", "duplicada"]
    id: str
    diretorio: Path


def agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --- cabeçalhos ---------------------------------------------------------------


def _cabecalho(msg: EmailMessage, nome: str, avisos: list[Aviso]) -> str:
    try:
        valor = msg.get(nome)
        return str(valor).strip() if valor is not None else ""
    except Exception as e:  # cabeçalho malformado não pode abortar a captura
        avisos.append(Aviso(etapa="captura", mensagem=f"cabeçalho {nome} inválido: {e}"))
        return _cabecalho_bruto(msg, nome)


def _cabecalho_bruto(msg: EmailMessage, nome: str) -> str:
    for k, v in msg.raw_items():
        if k.lower() == nome.lower():
            return str(v).strip()
    return ""


def _enderecos(msg: EmailMessage, nome: str, avisos: list[Aviso]) -> list[Pessoa]:
    try:
        cab = msg.get(nome)
        if cab is None:
            return []
        return [Pessoa(nome=a.display_name, email=a.addr_spec.lower()) for a in cab.addresses]
    except Exception:
        brutos = [str(v) for k, v in msg.raw_items() if k.lower() == nome.lower()]
        return [Pessoa(nome=n, email=e.lower()) for n, e in getaddresses(brutos) if n or e]


def normalizar_message_id(valor: str) -> str | None:
    m = _MSG_ID.search(valor or "")
    return m.group(0) if m else None


def _data(msg: EmailMessage, avisos: list[Aviso]) -> datetime | None:
    bruto = _cabecalho(msg, "Date", avisos)
    if not bruto:
        return None
    try:
        return parsedate_to_datetime(bruto)
    except (TypeError, ValueError):
        avisos.append(Aviso(etapa="captura", mensagem=f"data inválida: {bruto!r}"))
        return None


def _utc(data: datetime) -> datetime:
    return (data if data.tzinfo else data.replace(tzinfo=UTC)).astimezone(UTC)


def chave_dedup(
    message_id: str | None, remetente: str, data: datetime | None, assunto: str, bruto: bytes
) -> ChaveDedup:
    if message_id:
        return ChaveDedup(tipo="message_id", valor=message_id)
    if remetente or data or assunto:
        data_txt = _utc(data).isoformat() if data else ""
        texto = f"{remetente}|{data_txt}|{assunto}".encode()
        return ChaveDedup(tipo="hash_cabecalhos", valor=hashlib.sha256(texto).hexdigest())
    return ChaveDedup(tipo="hash_conteudo", valor=hashlib.sha256(bruto).hexdigest())


def id_de(chave: ChaveDedup) -> str:
    return hashlib.sha256(f"{chave.tipo}:{chave.valor}".encode()).hexdigest()[:12]


def thread_por_cabecalho(in_reply_to: str | None, references: list[str], assunto: str) -> Thread:
    pai = in_reply_to or (references[-1] if references else None)
    if pai:
        tipo = "encaminhamento" if _PREFIXO_ENCAMINHAMENTO.match(assunto) else "resposta"
        return Thread(tipo=tipo, pai_message_id=pai, metodo="cabecalho")
    if _PREFIXO_ENCAMINHAMENTO.match(assunto):
        return Thread(tipo="encaminhamento", metodo="assunto")
    if _PREFIXO_RESPOSTA.match(assunto):
        return Thread(tipo="resposta", metodo="assunto")
    return Thread(tipo="nova", metodo="cabecalho")


# --- captura ------------------------------------------------------------------


def _registrar_origem(dir_mensagem: Path, origem: Origem) -> None:
    meta = ler_metadata(dir_mensagem)
    conhecidas = [meta.origem.identidade(), *(o.identidade() for o in meta.origens_adicionais)]
    if origem.identidade() not in conhecidas:
        meta.origens_adicionais.append(origem)
        gravar_metadata(dir_mensagem, meta)


@dataclass
class Analise:
    """Cabeçalhos extraídos do .eml bruto, suficientes para dedup e metadata."""

    msg: EmailMessage
    message_id: str | None
    assunto: str
    remetente: Pessoa | None
    data: datetime | None
    chave: ChaveDedup
    id: str
    avisos: list[Aviso]


def analisar(bruto: bytes) -> Analise:
    avisos: list[Aviso] = []
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(bruto, headersonly=True)
    message_id = normalizar_message_id(_cabecalho(msg, "Message-ID", avisos))
    assunto = _cabecalho(msg, "Subject", avisos)
    remetentes = _enderecos(msg, "From", avisos)
    remetente = remetentes[0] if remetentes else None
    data = _data(msg, avisos)
    chave = chave_dedup(message_id, remetente.email if remetente else "", data, assunto, bruto)
    return Analise(msg, message_id, assunto, remetente, data, chave, id_de(chave), avisos)


def criar_diretorio(
    destino: Path,
    bruto: bytes,
    a: Analise,
    origem: Origem,
    contas_proprias: set[str],
    extras: dict | None = None,
) -> Metadata:
    """Monta o diretório em .tmp-<id> e renomeia: o diretório final nunca fica incompleto."""
    msg, avisos = a.msg, list(a.avisos)
    in_reply_to = normalizar_message_id(_cabecalho(msg, "In-Reply-To", avisos))
    references = _MSG_ID.findall(_cabecalho(msg, "References", avisos))
    remetente = a.remetente
    direcao = "enviada" if remetente and remetente.email in contas_proprias else "recebida"

    tmp = destino.parent / f".tmp-{a.id}"
    if tmp.exists():
        shutil.rmtree(tmp)
    gravar_atomico(tmp / ORIGINAL, bruto)
    meta = Metadata(
        id=a.id,
        chave_dedup=a.chave,
        message_id=a.message_id,
        in_reply_to=in_reply_to,
        references=references,
        origem=origem,
        direcao=direcao,
        data=a.data.isoformat() if a.data else None,
        data_utc=_utc(a.data).isoformat().replace("+00:00", "Z") if a.data else None,
        assunto=a.assunto,
        remetente=remetente,
        para=_enderecos(msg, "To", avisos),
        cc=_enderecos(msg, "Cc", avisos),
        bcc=_enderecos(msg, "Bcc", avisos),
        original=info_arquivo(tmp / ORIGINAL, tmp),
        thread=thread_por_cabecalho(in_reply_to, references, a.assunto),
        pipeline={"captura": Etapa(status="ok", em=agora(), versao=__version__)},
        avisos=avisos,
        **(extras or {}),
    )
    gravar_metadata(tmp, meta)
    tmp.rename(destino)
    return meta


def _pasta(raiz: Path, a: Analise, origem: Origem, contas_proprias: set[str]) -> Path:
    """<conta>/<remetente>/ ; enviada (remetente é conta própria): <conta>/.enviadas/<primeiro
    destinatário>/ (To, senão Cc, senão Bcc)."""
    conta = raiz / nome_conta(origem)
    email = a.remetente.email if a.remetente else None
    if not email or email not in contas_proprias:
        return conta / nome_remetente(email)
    for cabecalho in ("To", "Cc", "Bcc"):
        destinatarios = [p for p in _enderecos(a.msg, cabecalho, []) if p.email]
        if destinatarios:
            return conta / ENVIADAS / nome_remetente(destinatarios[0].email)
    return conta / ENVIADAS / SEM_DESTINATARIO


def capturar(
    repo: Repositorio, bruto: bytes, origem: Origem, contas_proprias: set[str]
) -> ResultadoCaptura:
    a = analisar(bruto)
    if origem.capturado_em is None:
        origem = origem.model_copy(update={"capturado_em": agora()})

    existente = repo.caminho(a.id)
    pasta_remetente = _pasta(repo.raiz, a, origem, contas_proprias)
    destino = pasta_remetente / nome_diretorio(a.data, a.assunto, a.id)
    if existente is None and (destino / METADATA).exists():
        if ler_metadata(destino).id == a.id:  # capturado antes, índice desatualizado
            existente = destino
        else:  # colisão de id6 com outra mensagem
            destino = pasta_remetente / nome_diretorio(a.data, a.assunto, a.id, tamanho_id=12)
    if existente is not None:
        _registrar_origem(existente, origem)
        repo.registrar(a.id, existente)
        return ResultadoCaptura("duplicada", a.id, existente)

    criar_diretorio(destino, bruto, a, origem, contas_proprias)
    repo.registrar(a.id, destino)
    return ResultadoCaptura("nova", a.id, destino)


def reorganizar(
    repo: Repositorio, contas_proprias: set[str], aplicar: bool = True
) -> list[tuple[Path, Path]]:
    """Move diretórios que não estão onde a regra atual de pastas os colocaria (ex.: enviadas
    antigas em <remetente>/ em vez de <remetente>/<destinatário>/). Conteúdo intocado;
    só o diretório muda de lugar. Reconstrói o índice."""
    movidos = []
    for d in list(repo.mensagens()):
        a = analisar((d / ORIGINAL).read_bytes())
        origem = ler_metadata(d).origem
        alvo = _pasta(repo.raiz, a, origem, contas_proprias) / d.name
        if alvo == d:
            continue
        if alvo.exists():
            raise FileExistsError(f"destino já existe: {alvo}")
        if not aplicar:
            movidos.append((d, alvo))
            continue
        alvo.parent.mkdir(parents=True, exist_ok=True)
        d.rename(alvo)
        movidos.append((d, alvo))
        for vazia in (d.parent, d.parent.parent):  # limpa pastas que ficaram vazias
            if vazia != repo.raiz and vazia.is_dir() and not any(vazia.iterdir()):
                vazia.rmdir()
    if aplicar:
        repo.reconstruir_indice()
    return movidos
