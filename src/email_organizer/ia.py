"""Etapa 'classificacao': Ollama remoto enriquece o metadata.json (specs/ia.md).

IA nunca decide fluxo: só grava `classificacao`, `thread` (fallback, metodo "ia") e
`pipeline.classificacao`. Ollama fora do ar → `pendente`, reprocessável depois.
"""

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from email_organizer import __version__
from email_organizer.artefatos.markdown import para_markdown
from email_organizer.artefatos.mime import decompor, parse
from email_organizer.captura import ORIGINAL, agora
from email_organizer.config import Config
from email_organizer.modelos import Classificacao, Etapa, Metadata, Thread
from email_organizer.repositorio import gravar_metadata, ler_metadata

ETAPA = "classificacao"
MAX_TEXTO = 3000
TENTATIVAS = 2  # resposta inválida → 1 retry

log = logging.getLogger("email_organizer")


class Indisponivel(Exception):
    """Ollama fora do ar ou sem resposta no prazo."""


class RespostaInvalida(Exception):
    pass


class _Resposta(BaseModel):
    categoria: str
    subcategoria: str = ""
    confianca: float
    tipo: Literal["nova", "resposta", "encaminhamento"] | None = None

    @field_validator("confianca")
    @classmethod
    def _entre_0_e_1(cls, v: float) -> float:
        if 1 < v <= 100:  # modelo respondeu em percentual
            v /= 100
        if not 0 <= v <= 1:
            raise ValueError("confianca fora de [0, 1]")
        return round(v, 4)


class ClienteOllama:
    def __init__(
        self, url: str, modelo: str, timeout_s: int, http: httpx.Client | None = None
    ) -> None:
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.http = http or httpx.Client(timeout=timeout_s)

    @classmethod
    def de_config(cls, config: Config) -> "ClienteOllama":
        o = config.ollama
        return cls(o.url, o.modelo, o.timeout_s)

    def json(self, sistema: str, usuario: str, esquema: dict) -> str:
        """Conteúdo cru da resposta (deve ser JSON conforme `esquema`)."""
        corpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "format": esquema,
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            r = self.http.post(f"{self.url}/api/chat", json=corpo)
        except httpx.TransportError as e:
            raise Indisponivel(f"Ollama {self.url}: {e}") from e
        if r.status_code in (502, 503, 504):
            raise Indisponivel(f"Ollama {self.url}: HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()["message"]["content"]

    def fechar(self) -> None:
        self.http.close()


# --- prompt -------------------------------------------------------------------

SISTEMA = (
    "Você classifica e-mails de um acervo documental jurídico pessoal (Brasil). "
    "Responda somente com JSON válido."
)


def _precisa_thread(t: Thread) -> bool:
    """Fallback só sem In-Reply-To/References e sem prefixo RE:/ENC:/FW: no assunto."""
    return t.pai_message_id is None and t.metodo != "assunto"


def _prompt(meta: Metadata, texto: str, anexos: list[str], categorias: list[str]) -> str:
    rem = meta.remetente
    linhas = [
        f"Categorias permitidas: {', '.join(categorias)}.",
        "Campos: categoria (exatamente uma das permitidas), subcategoria (assunto específico "
        "em 1 a 4 palavras, ex.: Financiamento Imobiliário), confianca (0 a 1; use valores "
        "baixos quando houver pouca informação)"
        + (
            ", tipo (nova | resposta | encaminhamento: se a mensagem responde ou encaminha "
            "outra, pelo corpo citado)."
            if _precisa_thread(meta.thread)
            else "."
        ),
        "",
        f"Direção: {meta.direcao}",
        f"Remetente: {rem.nome} <{rem.email}>" if rem else "Remetente: (desconhecido)",
        f"Assunto: {meta.assunto}",
        f"Anexos: {', '.join(anexos) or '(nenhum)'}",
        "Texto:",
        texto[:MAX_TEXTO],
    ]
    return "\n".join(linhas)


def _entrada(dir_msg: Path) -> tuple[str, list[str]]:
    est = decompor(parse((dir_msg / ORIGINAL).read_bytes()))
    return para_markdown(est.html, est.texto), [p.nome for p in est.anexos]


def _consultar(cliente: ClienteOllama, prompt: str) -> _Resposta:
    esquema = _Resposta.model_json_schema()
    esquema["required"] = list(esquema["properties"])  # sem isso o modelo omite subcategoria
    ultimo: Exception | None = None
    for _ in range(TENTATIVAS):
        conteudo = cliente.json(SISTEMA, prompt, esquema)
        try:
            return _Resposta.model_validate(json.loads(conteudo))
        except (json.JSONDecodeError, ValidationError) as e:
            ultimo = e
    raise RespostaInvalida(f"resposta inválida do modelo: {str(ultimo)[:200]}")


def _categoria(valor: str, categorias: list[str]) -> str:
    por_nome = {c.casefold(): c for c in categorias}
    return por_nome.get(valor.strip().casefold(), "Outros")


# --- etapa --------------------------------------------------------------------


def precisa_classificar(dir_msg: Path) -> bool:
    etapa = ler_metadata(dir_msg).pipeline.get(ETAPA)
    return etapa is None or etapa.status != "ok"


def _marcar(dir_msg: Path, meta: Metadata, status: str, erro: str | None = None) -> Metadata:
    meta.pipeline[ETAPA] = Etapa(status=status, em=agora(), versao=__version__, erro=erro)
    gravar_metadata(dir_msg, meta)
    return meta


def classificar(dir_msg: Path, config: Config, cliente: ClienteOllama) -> Metadata:
    meta = ler_metadata(dir_msg)
    try:
        texto, anexos = _entrada(dir_msg)
        r = _consultar(cliente, _prompt(meta, texto, anexos, config.categorias))
    except Indisponivel as e:
        return _marcar(dir_msg, meta, "pendente", str(e))
    except Exception as e:
        log.error("classificacao %s: %s", dir_msg.name, e)
        return _marcar(dir_msg, meta, "erro", str(e)[:300])

    meta.classificacao = Classificacao(
        categoria=_categoria(r.categoria, config.categorias),
        subcategoria=r.subcategoria.strip(),
        confianca=r.confianca,
        modelo=cliente.modelo,
        classificado_em=agora(),
    )
    if r.tipo and _precisa_thread(meta.thread):
        meta.thread = Thread(tipo=r.tipo, metodo="ia")
    return _marcar(dir_msg, meta, "ok")


def classificar_lote(
    dirs: Iterable[Path],
    config: Config,
    cliente: ClienteOllama,
    progresso: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int]:
    """Classifica em sequência. Ollama caiu → o resto fica `pendente` sem novas chamadas."""
    contagem = {"ok": 0, "pendente": 0, "erro": 0}
    fora: str | None = None
    for d in dirs:
        if fora:
            status = _marcar(d, ler_metadata(d), "pendente", fora).pipeline[ETAPA].status
        else:
            etapa = classificar(d, config, cliente).pipeline[ETAPA]
            status = etapa.status
            if status == "pendente":
                fora = etapa.erro
                log.warning("Ollama indisponível: %s", fora)
        contagem[status] += 1
        if progresso:
            progresso(contagem)
    return contagem
