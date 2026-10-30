"""Modelos do metadata.json (schema em specs/metadata.md).

Modelos aceitam chaves extras: rotinas futuras gravam chaves próprias
que precisam sobreviver a leituras/escritas de outras rotinas.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class Arquivo(Base):
    arquivo: str
    sha256: str
    tamanho: int


class Pessoa(Base):
    nome: str = ""
    email: str = ""


class Origem(Base):
    tipo: Literal["imap", "graph", "pst", "eml"]
    conta: str | None = None
    pasta: str | None = None
    uid: int | None = None
    uidvalidity: int | None = None
    arquivo: str | None = None
    graph_id: str | None = None
    observacao: str | None = None
    filtro: list[dict] | None = None
    capturado_em: str | None = None

    def identidade(self) -> dict:
        """Campos que identificam a origem, sem data da captura nem filtro usado."""
        return self.model_dump(exclude={"capturado_em", "observacao", "filtro"}, exclude_none=True)


class ChaveDedup(Base):
    tipo: Literal["message_id", "hash_cabecalhos", "hash_conteudo"]
    valor: str


class Thread(Base):
    tipo: Literal["nova", "resposta", "encaminhamento"]
    pai_message_id: str | None = None
    metodo: Literal["cabecalho", "assunto", "ia"]


class Etapa(Base):
    status: Literal["ok", "erro", "pendente"]
    em: str
    versao: str
    erro: str | None = None


class Aviso(Base):
    etapa: str
    mensagem: str


class ItemCorpo(Base):
    ordem: int
    arquivo: str
    sha256: str
    tamanho: int
    mime: str
    content_id: str | None = None
    nome_original: str | None = None


class Anexo(Base):
    ordem: int
    tipo: Literal["arquivo", "email"]
    nome_original: str
    mime: str
    status: Literal["convertido", "original_pdf", "nao_convertido", "ignorado"]
    arquivo_original: Arquivo | None = None
    arquivo_pdf: Arquivo | None = None
    diretorio: str | None = None
    motivo: str | None = None


class ImagemRemota(Base):
    url: str
    status: Literal["embutida", "falhou", "nao_baixada"]
    sha256: str | None = None
    motivo: str | None = None


class Classificacao(Base):
    categoria: str
    subcategoria: str = ""
    confianca: float
    modelo: str
    classificado_em: str


class Pai(Base):
    id: str
    diretorio: str  # relativo ao diretório do filho


class Metadata(Base):
    schema_version: int = SCHEMA_VERSION
    id: str
    chave_dedup: ChaveDedup
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    origem: Origem
    origens_adicionais: list[Origem] = Field(default_factory=list)
    direcao: Literal["recebida", "enviada"]
    data: str | None = None
    data_utc: str | None = None
    assunto: str = ""
    remetente: Pessoa | None = None
    para: list[Pessoa] = Field(default_factory=list)
    cc: list[Pessoa] = Field(default_factory=list)
    bcc: list[Pessoa] = Field(default_factory=list)
    original: Arquivo
    pai: Pai | None = None
    pdf_principal: Arquivo | None = None
    texto_markdown: Arquivo | None = None
    corpo: list[ItemCorpo] = Field(default_factory=list)
    imagens_remotas: list[ImagemRemota] = Field(default_factory=list)
    anexos: list[Anexo] = Field(default_factory=list)
    thread: Thread
    classificacao: Classificacao | None = None
    pipeline: dict[str, Etapa] = Field(default_factory=dict)
    avisos: list[Aviso] = Field(default_factory=list)
