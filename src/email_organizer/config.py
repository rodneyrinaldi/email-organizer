"""Leitura do config.json (raiz do output) + segredos.json (pasta de estado)."""

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from email_organizer.filtro import Filtro

ESTADO_PADRAO = ".estado"
SEGREDOS = "segredos.json"

# filtro inline ou nome de um filtro em Config.filtros
RefFiltro = Filtro | str | None


class Pasta(BaseModel):
    nome: str
    filtro: RefFiltro = None


class Fonte(BaseModel):
    ativo: bool = False
    nome: str
    pastas: list[Pasta] = Field(default_factory=list)
    filtro: RefFiltro = None

    @field_validator("pastas", mode="before")
    @classmethod
    def _pastas_simples(cls, v: list) -> list:
        return [{"nome": p} if isinstance(p, str) else p for p in v]

    def pasta(self, nome: str) -> Pasta | None:
        alvo = nome.casefold()
        return next((p for p in self.pastas if p.nome.casefold() == alvo), None)

    def nomes_pastas(self) -> list[str]:
        return [p.nome for p in self.pastas]


class FonteImap(Fonte):
    host: str
    porta: int = 993
    ssl: bool = True
    usuario: str
    senha: str = ""


class FonteGraph(Fonte):
    usuario: str
    client_id: str = ""
    tenant: str = "consumers"


class FontePst(Fonte):
    caminho: str


class Ollama(BaseModel):
    habilitado: bool = True
    url: str = "http://localhost:11434"
    modelo: str = "qwen2.5:7b"
    timeout_s: int = 120


class Opcoes(BaseModel):
    pdf_principal: bool = True
    markdown: bool = True
    classificacao: bool = True
    baixar_imagens_remotas: bool = True
    timeout_conversao_s: int = 120
    trabalhadores: int = 0  # processos para artefatos; 0 = metade dos núcleos


class Config(BaseModel):
    contas_proprias: list[str] = Field(default_factory=list)
    filtros: dict[str, Filtro] = Field(default_factory=dict)
    imap: list[FonteImap] = Field(default_factory=list)
    graph: list[FonteGraph] = Field(default_factory=list)
    pst: list[FontePst] = Field(default_factory=list)
    ollama: Ollama = Field(default_factory=Ollama)
    opcoes: Opcoes = Field(default_factory=Opcoes)
    categorias: list[str] = Field(default_factory=lambda: ["Outros"])

    @model_validator(mode="after")
    def _refs_validas(self) -> "Config":
        for fonte in (*self.imap, *self.graph, *self.pst):
            for ref in (fonte.filtro, *(p.filtro for p in fonte.pastas)):
                if isinstance(ref, str) and ref not in self.filtros:
                    raise ValueError(f"fonte {fonte.nome!r}: filtro {ref!r} não definido")
        return self

    def todas_contas_proprias(self) -> set[str]:
        """E-mails do usuário: contas_proprias + usuários IMAP/Graph (ativos ou não)."""
        contas = {*self.contas_proprias}
        contas.update(f.usuario for f in self.imap)
        contas.update(f.usuario for f in self.graph)
        return {c.strip().lower() for c in contas if c.strip()}

    def fontes_ativas(self) -> list[FonteImap | FonteGraph | FontePst]:
        return [f for f in (*self.imap, *self.graph, *self.pst) if f.ativo]

    def _resolver(self, ref: RefFiltro) -> Filtro | None:
        return self.filtros[ref] if isinstance(ref, str) else ref

    def filtros_para(self, fonte: Fonte, pasta: str, avulso: Filtro | None = None) -> list[Filtro]:
        """Filtro da pasta (ou, na falta, da fonte) + filtro avulso da UI, combinados com E."""
        p = fonte.pasta(pasta)
        base = self._resolver(p.filtro if p and p.filtro is not None else fonte.filtro)
        return [f for f in (base, avulso) if f is not None and not f.vazio()]


def dir_estado(raiz: Path) -> Path:
    """Segredos, tokens e estado incremental. Fica fora da pasta sincronizada definindo
    EMAIL_ORGANIZER_ESTADO; na falta, `<raiz>/.estado`."""
    return Path(os.environ.get("EMAIL_ORGANIZER_ESTADO") or raiz / ESTADO_PADRAO)


def carregar_config(caminho: Path, segredos: Path | None = None) -> Config:
    """`segredos`: JSON {nome da fonte: senha}; preenche a senha das fontes IMAP."""
    config = Config.model_validate_json(caminho.read_text(encoding="utf-8"))
    if segredos is not None and segredos.exists():
        senhas = json.loads(segredos.read_text(encoding="utf-8"))
        for fonte in config.imap:
            if fonte.nome in senhas:
                fonte.senha = senhas[fonte.nome]
    return config
