"""Execução: fontes ativas → filtro → captura → artefatos. Uma mensagem com erro
não interrompe as demais; tudo vai para o relatório.

Captura é sequencial (índice/dedup). Artefatos (CPU: WeasyPrint/LibreOffice) rodam em
processos paralelos sobre diretórios já capturados.
"""

import logging
import multiprocessing
import os
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from email_organizer.artefatos import gerar_artefatos
from email_organizer.artefatos.mime import parse
from email_organizer.captura import agora, capturar
from email_organizer.config import (
    Config,
    Fonte,
    FonteGraph,
    FonteImap,
    FontePst,
    Opcoes,
    dir_estado,
)
from email_organizer.filtro import Filtro, aceita_todos
from email_organizer.fontes import Leitor
from email_organizer.fontes.graph import LeitorGraph
from email_organizer.fontes.imap import LeitorImap
from email_organizer.fontes.pst import LeitorPst
from email_organizer.ia import ClienteOllama, classificar_lote, precisa_classificar
from email_organizer.repositorio import Repositorio, gravar_json, ler_metadata

DIR_JOBS = "_jobs"

log = logging.getLogger("email_organizer")


@dataclass
class Contagem:
    lidas: int = 0
    novas: int = 0
    duplicadas: int = 0
    filtradas: int = 0
    erros: int = 0


@dataclass
class Relatorio:
    job: str
    inicio: str
    fim: str | None = None
    cancelado: bool = False
    total: Contagem = field(default_factory=Contagem)
    por_pasta: dict[str, Contagem] = field(default_factory=dict)
    erros: list[dict] = field(default_factory=list)
    avisos: list[dict] = field(default_factory=list)
    classificacao: dict[str, int] = field(default_factory=dict)
    fase: str = "captura"  # captura | artefatos | classificacao | fim
    atual: str = ""  # "fonte / pasta" em leitura
    artefatos_total: int = 0  # mensagens enviadas para gerar artefatos
    artefatos_feitos: int = 0
    classificacao_total: int = 0

    def contagem(self, fonte: str, pasta: str) -> Contagem:
        return self.por_pasta.setdefault(f"{fonte} / {pasta}", Contagem())

    def somar(self, c: Contagem, campo: str) -> None:
        setattr(c, campo, getattr(c, campo) + 1)
        setattr(self.total, campo, getattr(self.total, campo) + 1)


Progresso = Callable[[Relatorio], None]
Abridor = Callable[[Fonte, Config, Path, Filtro | None], Leitor]


def abrir_leitor(fonte: Fonte, config: Config, raiz: Path, avulso: Filtro | None) -> Leitor:
    if isinstance(fonte, FonteImap):
        return LeitorImap(fonte, dir_estado(raiz), lambda p: config.filtros_para(fonte, p, avulso))
    if isinstance(fonte, FontePst):
        return LeitorPst(fonte)
    if isinstance(fonte, FonteGraph):
        return LeitorGraph(fonte, dir_estado(raiz), lambda p: config.filtros_para(fonte, p, avulso))
    raise TypeError(type(fonte))


# --- artefatos em paralelo -----------------------------------------------------


def _gerar_em_processo(dir_msg: str, opcoes: dict, contas: list[str]) -> list[dict]:
    meta = gerar_artefatos(Path(dir_msg), Opcoes.model_validate(opcoes), set(contas))
    return [a.model_dump() for a in meta.avisos]


def trabalhadores_padrao() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


class _Artefatos:
    def __init__(
        self, config: Config, rel: Relatorio, raiz: Path, progresso: Progresso | None = None
    ) -> None:
        self.opcoes = config.opcoes.model_dump(mode="json")
        self.contas = sorted(config.todas_contas_proprias())
        self.rel, self.raiz, self.progresso = rel, raiz, progresso
        n = config.opcoes.trabalhadores or trabalhadores_padrao()
        self.max_pendentes = 2 * n  # limita memória e mantém o progresso realista
        contexto = multiprocessing.get_context("spawn")  # seguro com threads (UI/jobs)
        self.pool = ProcessPoolExecutor(n, mp_context=contexto) if n > 1 else None
        self.pendentes: dict[Future, tuple[str, Contagem]] = {}
        self.enviados: set[Path] = set()  # duplicata no mesmo job: dois workers no mesmo dir

    def enviar(self, dir_msg: Path, c: Contagem) -> None:
        if dir_msg in self.enviados:
            return
        self.enviados.add(dir_msg)
        self.rel.artefatos_total += 1
        caminho = dir_msg.relative_to(self.raiz).as_posix()
        args = (str(dir_msg), self.opcoes, self.contas)
        if self.pool is None:
            self._concluir(caminho, c, lambda: _gerar_em_processo(*args))
            return
        self.pendentes[self.pool.submit(_gerar_em_processo, *args)] = (caminho, c)
        if len(self.pendentes) >= self.max_pendentes:
            self._coletar(FIRST_COMPLETED)

    def _coletar(self, quando: str) -> None:
        feitos, _ = wait(list(self.pendentes), return_when=quando)
        for f in feitos:
            caminho, c = self.pendentes.pop(f)
            self._concluir(caminho, c, f.result)

    def _concluir(self, caminho: str, c: Contagem, resultado: Callable[[], list[dict]]) -> None:
        try:
            avisos = resultado()
        except Exception as e:
            log.error("artefatos %s: %s", caminho, e)
            self.rel.somar(c, "erros")
            self.rel.erros.append({"mensagem": caminho, "erro": f"artefatos: {e}"})
            avisos = []
        self.rel.avisos += [
            {"mensagem": caminho, "etapa": a["etapa"], "aviso": a["mensagem"]} for a in avisos
        ]
        self.rel.artefatos_feitos += 1
        if self.progresso:
            self.progresso(self.rel)

    def fechar(self) -> None:
        while self.pendentes:  # um a um: o progresso avança durante a espera final
            self._coletar(FIRST_COMPLETED)
        if self.pool is not None:
            self.pool.shutdown()


# --- execução -----------------------------------------------------------------


@dataclass
class _Execucao:
    config: Config
    raiz: Path
    repo: Repositorio
    rel: Relatorio
    avulso: Filtro | None
    artefatos: _Artefatos | None
    progresso: Progresso | None
    cancelar: threading.Event | None
    abrir: Abridor
    para_classificar: list[Path] = field(default_factory=list)

    def cancelado(self) -> bool:
        return bool(self.cancelar and self.cancelar.is_set())

    def fonte(self, fonte: Fonte) -> None:
        log.info("fonte %s", fonte.nome)
        try:
            leitor = self.abrir(fonte, self.config, self.raiz, self.avulso)
        except Exception as e:
            log.exception("fonte %s: falha ao abrir", fonte.nome)
            self.rel.erros.append({"fonte": fonte.nome, "erro": str(e)})
            return
        try:
            for pasta in fonte.nomes_pastas() or leitor.listar_pastas():
                if self.cancelado():
                    return
                self.pasta(leitor, fonte, pasta)
        except Exception as e:
            log.exception("fonte %s: erro", fonte.nome)
            self.rel.erros.append({"fonte": fonte.nome, "erro": str(e)})
        finally:
            leitor.fechar()

    def pasta(self, leitor: Leitor, fonte: Fonte, pasta: str) -> None:
        filtros = self.config.filtros_para(fonte, pasta, self.avulso)
        filtro_json = [f.model_dump(mode="json", exclude_defaults=True) for f in filtros] or None
        contas = self.config.todas_contas_proprias()
        c = self.rel.contagem(fonte.nome, pasta)
        self.rel.atual = f"{fonte.nome} / {pasta}"
        log.info("pasta %s / %s", fonte.nome, pasta)
        try:
            for m in leitor.mensagens([pasta]):
                if self.cancelado():
                    return
                self.rel.somar(c, "lidas")
                try:
                    if filtros and not aceita_todos(filtros, parse(m.bruto)):
                        self.rel.somar(c, "filtradas")
                        continue
                    origem = m.origem.model_copy(update={"filtro": filtro_json})
                    r = capturar(self.repo, m.bruto, origem, contas)
                    leitor.registrar(origem)
                    self.rel.somar(c, "novas" if r.status == "nova" else "duplicadas")
                    if self.artefatos and _precisa_artefatos(r.diretorio):
                        self.artefatos.enviar(r.diretorio, c)
                    novo = r.diretorio not in self.para_classificar  # duplicata no mesmo job
                    if novo and precisa_classificar(r.diretorio):
                        self.para_classificar.append(r.diretorio)
                except Exception as e:
                    log.exception("mensagem %s", m.origem.identidade())
                    self.rel.somar(c, "erros")
                    self.rel.erros.append({"origem": m.origem.identidade(), "erro": str(e)})
                if self.progresso:
                    self.progresso(self.rel)
        except Exception as e:
            log.exception("pasta %s / %s", fonte.nome, pasta)
            self.rel.erros.append({"fonte": fonte.nome, "pasta": pasta, "erro": str(e)})


@contextmanager
def _job(raiz: Path, job: str | None, cancelar: threading.Event | None) -> Iterator[Relatorio]:
    """Diretório do job, log em arquivo e relatório gravado mesmo em caso de erro."""
    job = job or novo_job()
    dir_job = raiz / DIR_JOBS / job
    dir_job.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(dir_job / "log.txt", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    rel = Relatorio(job=job, inicio=agora())
    try:
        yield rel
    finally:
        rel.cancelado = bool(cancelar and cancelar.is_set())
        rel.fim = agora()
        rel.fase = "fim"
        gravar_json(dir_job / "relatorio.json", asdict(rel))
        (dir_job / "relatorio.md").write_text(relatorio_md(rel), encoding="utf-8")
        log.info("fim: %s", asdict(rel.total))
        log.removeHandler(handler)
        handler.close()


def novo_job() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def processar(
    config: Config,
    raiz: Path,
    fontes: list[Fonte] | None = None,
    avulso: Filtro | None = None,
    gerar: bool = True,
    progresso: Progresso | None = None,
    cancelar: threading.Event | None = None,
    abrir: Abridor = abrir_leitor,
    cliente_ia: ClienteOllama | None = None,
    classificar: bool = True,
    job: str | None = None,
) -> Relatorio:
    with _job(raiz, job, cancelar) as rel:
        artefatos = _Artefatos(config, rel, raiz, progresso) if gerar else None
        try:
            with Repositorio(raiz) as repo:
                ex = _Execucao(
                    config, raiz, repo, rel, avulso, artefatos, progresso, cancelar, abrir
                )
                for fonte in fontes if fontes is not None else config.fontes_ativas():
                    if ex.cancelado():
                        break
                    ex.fonte(fonte)
            if artefatos:
                rel.fase = "artefatos"
                artefatos.fechar()  # classificação só depois: os dois gravam o metadata.json
        finally:
            if artefatos:
                artefatos.fechar()
        if classificar and ia_habilitada(config) and ex.para_classificar:
            _classificar(config, rel, ex.para_classificar, cliente_ia, progresso, cancelar)
    return rel


def reprocessar(
    config: Config,
    raiz: Path,
    etapas: set[str],
    todas: bool = False,
    progresso: Progresso | None = None,
    cancelar: threading.Event | None = None,
    cliente_ia: ClienteOllama | None = None,
    job: str | None = None,
) -> Relatorio:
    """Refaz etapas a partir do .eml salvo (nunca acessa a caixa postal).
    Sem `todas`, só mensagens cuja etapa não está `ok`."""

    def cancelado() -> bool:
        return bool(cancelar and cancelar.is_set())

    with _job(raiz, job, cancelar) as rel:
        dirs = list(Repositorio(raiz).mensagens())
        c = rel.contagem("repositório", "reprocessar")
        if "artefatos" in etapas:
            rel.fase = "artefatos"
            artefatos = _Artefatos(config, rel, raiz, progresso)
            try:
                for d in dirs:
                    if cancelado():
                        break
                    if todas or _precisa_artefatos(d):
                        rel.somar(c, "lidas")
                        artefatos.enviar(d, c)
                        if progresso:
                            progresso(rel)
            finally:
                artefatos.fechar()
        if "classificacao" in etapas and ia_habilitada(config):
            alvo = [d for d in dirs if todas or precisa_classificar(d)]
            _classificar(config, rel, alvo, cliente_ia, progresso, cancelar)
    return rel


def ia_habilitada(config: Config) -> bool:
    return config.ollama.habilitado and config.opcoes.classificacao


def _classificar(
    config: Config,
    rel: Relatorio,
    dirs: list[Path],
    cliente: ClienteOllama | None,
    progresso: Progresso | None,
    cancelar: threading.Event | None,
) -> None:
    rel.fase = "classificacao"
    rel.classificacao_total = len(dirs)
    log.info("classificação: %d mensagens", len(dirs))
    proprio = cliente is None
    cliente = cliente or ClienteOllama.de_config(config)

    def andamento(c: dict[str, int]) -> None:
        rel.classificacao = dict(c)
        if progresso:
            progresso(rel)

    try:
        rel.classificacao = classificar_lote(
            (d for d in dirs if not (cancelar and cancelar.is_set())), config, cliente, andamento
        )
    finally:
        if proprio:
            cliente.fechar()
    if rel.classificacao.get("pendente"):
        rel.avisos.append(
            {
                "mensagem": "*",
                "etapa": "classificacao",
                "aviso": "Ollama indisponível: "
                f"{rel.classificacao['pendente']} pendentes (rode `classificar`)",
            }
        )


def _precisa_artefatos(dir_msg: Path) -> bool:
    etapa = ler_metadata(dir_msg).pipeline.get("artefatos")
    return etapa is None or etapa.status != "ok"


def relatorio_md(rel: Relatorio) -> str:
    t = rel.total
    linhas = [
        f"# Relatório {rel.job}",
        "",
        f"Início {rel.inicio} · fim {rel.fim}" + (" · **cancelado**" if rel.cancelado else ""),
        "",
        "| Fonte / pasta | Lidas | Novas | Duplicadas | Filtradas | Erros |",
        "|---|---|---|---|---|---|",
    ]
    for nome, c in rel.por_pasta.items():
        linhas.append(
            f"| {nome} | {c.lidas} | {c.novas} | {c.duplicadas} | {c.filtradas} | {c.erros} |"
        )
    linhas.append(
        f"| **Total** | {t.lidas} | {t.novas} | {t.duplicadas} | {t.filtradas} | {t.erros} |"
    )
    if rel.classificacao:
        linhas += [
            "",
            "Classificação: " + " · ".join(f"{k} {v}" for k, v in rel.classificacao.items()),
        ]
    if rel.erros:
        linhas += ["", "## Erros", *(f"- {e}" for e in rel.erros)]
    if rel.avisos:
        linhas += ["", "## Avisos", *(f"- `{a['mensagem']}` — {a['aviso']}" for a in rel.avisos)]
    return "\n".join(linhas) + "\n"
