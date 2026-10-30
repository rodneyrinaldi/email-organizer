"""Interface web local (specs/interface.md): FastAPI + Jinja2 + HTMX, sem login.

Um job por vez em thread de fundo; progresso por polling HTMX. A lógica é a mesma do CLI
(`processar`, `reprocessar`); a UI só monta os parâmetros.
"""

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader

from email_organizer import incremental, limpeza
from email_organizer.config import SEGREDOS, Config, Fonte, Pasta, carregar_config, dir_estado
from email_organizer.fontes.graph import Autenticacao
from email_organizer.ia import ClienteOllama
from email_organizer.processar import (
    DIR_JOBS,
    Abridor,
    Relatorio,
    abrir_leitor,
    ia_habilitada,
    novo_job,
    processar,
    reprocessar,
)
from email_organizer.repositorio import Repositorio, ler_json

SEP = "::"  # valor do checkbox de pasta: "<fonte>::<pasta>"

_env = Environment(loader=PackageLoader("email_organizer", "templates/web"), autoescape=True)


@dataclass
class Job:
    id: str
    tipo: str  # processar | reprocessar
    cancelar: threading.Event = field(default_factory=threading.Event)
    rel: Relatorio | None = None
    erro: str | None = None
    thread: threading.Thread | None = None
    notificado: bool = False  # toast de fim já enviado

    @property
    def ativo(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


@dataclass
class Conexao:
    fonte: str
    mensagem: str = ""
    status: str = "aguardando"  # aguardando | ok | expirado | desconectado | erro
    detalhe: str = ""
    url: str = ""
    codigo: str = ""
    expira: float = 0.0  # epoch
    forcado: bool = False  # reconexão pedida: o token antigo não conta como "conectado"

    @property
    def minutos(self) -> int:
        return max(0, int((self.expira - time.time()) // 60))


def _html(template: str, **ctx: object) -> HTMLResponse:
    return HTMLResponse(_env.get_template(template).render(**ctx))


def _avisar(texto: str, tipo: str = "aviso") -> HTMLResponse:
    """Só um toast (OOB); o alvo da requisição não muda."""
    r = _html("_toast.html.j2", texto=texto, tipo=tipo)
    r.headers["HX-Reswap"] = "none"
    return r


def _duracao(job: Job | None) -> str:
    if job is None or job.rel is None:
        return ""
    inicio = datetime.fromisoformat(job.rel.inicio)
    fim = datetime.fromisoformat(job.rel.fim) if job.rel.fim else datetime.now(inicio.tzinfo)
    s = max(0, int((fim - inicio).total_seconds()))
    return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"


def _todas(config: Config) -> list[Fonte]:
    return [*config.imap, *config.graph, *config.pst]


def _tipo(config: Config, fonte: Fonte) -> str:
    return "imap" if fonte in config.imap else "graph" if fonte in config.graph else "pst"


def criar_app(
    raiz: Path,
    abrir: Abridor = abrir_leitor,
    cliente_ia: ClienteOllama | None = None,
    autenticacao: Callable[[Fonte, Path], Autenticacao] = Autenticacao,
) -> FastAPI:
    app = FastAPI(title="email-organizer")
    # no container a raiz é /output; mostra o caminho do host (OUTPUT_DIR do .env)
    exibir = os.environ.get("EMAIL_ORGANIZER_OUTPUT_HOST") or str(raiz)
    trava = threading.Lock()
    jobs: dict[str, Job] = {}
    conexoes: dict[str, Conexao] = {}

    def config() -> Config:
        return carregar_config(raiz / "config.json", dir_estado(raiz) / SEGREDOS)

    def fonte(cfg: Config, nome: str) -> Fonte:
        f = next((f for f in _todas(cfg) if f.nome == nome), None)
        if f is None:
            raise HTTPException(404, f"fonte {nome!r} não encontrada")
        return f

    def em_andamento() -> Job | None:
        return next((j for j in jobs.values() if j.ativo), None)

    def iniciar(tipo: str, executar: Callable[[Job], Relatorio]) -> HTMLResponse:
        with trava:
            atual = em_andamento()
            if atual:
                return _avisar("já existe um job em andamento; aguarde ou cancele")
            job = Job(id=novo_job(), tipo=tipo)

            def alvo() -> None:
                try:
                    job.rel = executar(job)
                except Exception as e:
                    job.erro = str(e)

            job.thread = threading.Thread(target=alvo, daemon=True)
            jobs[job.id] = job
            job.thread.start()
        return resposta_job(job)

    def resposta_job(job: Job, aviso: str | None = None) -> HTMLResponse:
        final = not job.ativo and not job.notificado
        if final:
            job.notificado = True
        return _html(
            "_job.html.j2",
            job=job,
            aviso=aviso,
            final=final,
            duracao=_duracao(job),
            jobs=ultimos_jobs() if final else None,
        )

    def progresso(job: Job) -> Callable[[Relatorio], None]:
        return lambda r: setattr(job, "rel", r)

    def ultimos_jobs(n: int = 10) -> list[dict]:
        pasta = raiz / DIR_JOBS
        if not pasta.is_dir():
            return []
        saida = []
        for d in sorted(pasta.iterdir(), reverse=True)[:n]:
            rel = d / "relatorio.json"
            saida.append({"id": d.name, "rel": ler_json(rel) if rel.exists() else None})
        return saida

    # --- telas ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def inicio() -> HTMLResponse:
        try:
            cfg, erro = config(), None
        except Exception as e:
            cfg, erro = None, f"config.json inválido ou ausente em {raiz}: {e}"
        fontes = [{"f": f, "tipo": _tipo(cfg, f)} for f in _todas(cfg)] if cfg else []
        return _html(
            "index.html.j2",
            pagina="processar",
            raiz=exibir,
            erro=erro,
            fontes=fontes,
            opcoes=cfg.opcoes if cfg else None,
            ia=ia_habilitada(cfg) if cfg else False,
            job=(job := em_andamento()),
            duracao=_duracao(job),
            jobs=ultimos_jobs(),
        )

    @app.get("/fontes/{nome}/pastas", response_class=HTMLResponse)
    def pastas(nome: str) -> HTMLResponse:
        cfg = config()
        f = fonte(cfg, nome)
        try:
            leitor = abrir(f, cfg, raiz, None)
            try:
                lista = leitor.listar_pastas()
            finally:
                leitor.fechar()
        except Exception as e:
            return _html("_pastas.html.j2", fonte=f, erro=str(e), pastas=[], sep=SEP)
        marcadas = {p.casefold() for p in f.nomes_pastas()}
        itens = [{"nome": p, "marcada": p.casefold() in marcadas} for p in lista]
        return _html("_pastas.html.j2", fonte=f, erro=None, pastas=itens, sep=SEP)

    @app.post("/fontes/{nome}/reler", response_class=HTMLResponse)
    def reler(nome: str) -> HTMLResponse:
        if em_andamento():
            return _avisar("há um job em andamento; aguarde")
        f = fonte(config(), nome)
        if not incremental.zerar(dir_estado(raiz), f):
            return _avisar(f"{nome}: nada a zerar (já relê tudo na próxima execução)")
        return _avisar(f"{nome}: controle zerado — a próxima execução relê todas as pastas", "ok")

    # --- jobs ----------------------------------------------------------------

    @app.post("/processar", response_class=HTMLResponse)
    def processar_(
        fontes: Annotated[list[str], Form()] = [],  # noqa: B006 (padrão do FastAPI)
        pastas: Annotated[list[str], Form()] = [],  # noqa: B006
        pdf_principal: Annotated[bool, Form()] = False,
        markdown: Annotated[bool, Form()] = False,
        classificacao: Annotated[bool, Form()] = False,
    ) -> HTMLResponse:
        cfg = config()
        if not fontes:
            return _avisar("selecione ao menos uma fonte")
        escolhidas = []
        for nome in fontes:
            f = fonte(cfg, nome)
            sel = [p.split(SEP, 1)[1] for p in pastas if p.split(SEP, 1)[0] == nome]
            if sel:  # pastas marcadas na tela substituem as do config (filtros preservados)
                f = f.model_copy(update={"pastas": [f.pasta(p) or Pasta(nome=p) for p in sel]})
            escolhidas.append(f)
        cfg = cfg.model_copy(
            update={
                "opcoes": cfg.opcoes.model_copy(
                    update={
                        "pdf_principal": pdf_principal,
                        "markdown": markdown,
                        "classificacao": classificacao,
                    }
                )
            }
        )
        return iniciar(
            "processar",
            lambda job: processar(
                cfg,
                raiz,
                escolhidas,
                progresso=progresso(job),
                cancelar=job.cancelar,
                abrir=abrir,
                cliente_ia=cliente_ia,
                job=job.id,
            ),
        )

    @app.post("/reprocessar", response_class=HTMLResponse)
    def reprocessar_(
        etapas: Annotated[list[str], Form()] = [],  # noqa: B006
        todas: Annotated[bool, Form()] = False,
    ) -> HTMLResponse:
        validas = {"artefatos", "classificacao"}
        escolha = set(etapas) & validas
        if not escolha:
            return _avisar("selecione ao menos uma etapa")
        cfg = config()
        return iniciar(
            "reprocessar",
            lambda job: reprocessar(
                cfg,
                raiz,
                escolha,
                todas,
                progresso=progresso(job),
                cancelar=job.cancelar,
                cliente_ia=cliente_ia,
                job=job.id,
            ),
        )

    @app.get("/jobs/{id_job}/status", response_class=HTMLResponse)
    def status(id_job: str) -> HTMLResponse:
        job = jobs.get(id_job)
        if job is None:
            raise HTTPException(404, "job desconhecido")
        return resposta_job(job)

    @app.post("/jobs/{id_job}/cancelar", response_class=HTMLResponse)
    def cancelar(id_job: str) -> HTMLResponse:
        job = jobs.get(id_job)
        if job is None:
            raise HTTPException(404, "job desconhecido")
        job.cancelar.set()
        return resposta_job(job, aviso="cancelando após a mensagem atual…")

    @app.post("/reconstruir-indice", response_class=HTMLResponse)
    def reconstruir() -> HTMLResponse:
        if em_andamento():
            return _avisar("aguarde o job em andamento")
        try:
            n = Repositorio(raiz).reconstruir_indice()
        except Exception as e:
            return _avisar(f"falha ao reconstruir o índice: {e}", "erro")
        return _avisar(f"índice reconstruído: {n} mensagens", "ok")

    # --- Graph ---------------------------------------------------------------

    def fonte_graph(nome: str) -> Fonte:
        cfg = config()
        f = fonte(cfg, nome)
        if f not in cfg.graph:
            raise HTTPException(400, "só fontes Graph usam conectar")
        return f

    def conectado(f: Fonte) -> str | None:
        """Conta, se o token salvo vale — inclusive um salvo por outro código já autorizado."""
        try:
            return autenticacao(f, dir_estado(raiz)).verificar()
        except Exception:
            return None

    @app.post("/conectar/{nome}", response_class=HTMLResponse)
    def conectar(nome: str, forcar: bool = False) -> HTMLResponse:
        f = fonte_graph(nome)
        if not forcar and (conta := conectado(f)):
            return _html("_conectar.html.j2", c=Conexao(fonte=nome, status="ok", detalhe=conta))
        pendente = conexoes.get(nome)
        if not forcar and pendente and pendente.status == "aguardando" and pendente.minutos > 0:
            return _html("_conectar.html.j2", c=pendente)  # mesmo código: só um vale por vez
        c = Conexao(fonte=nome, forcado=forcar)
        try:
            auth = autenticacao(f, dir_estado(raiz))
            fluxo = auth.iniciar()
        except Exception as e:
            c.status, c.detalhe = "erro", str(e)
            conexoes[nome] = c
            return _html("_conectar.html.j2", c=c)
        c.mensagem = fluxo["message"]
        c.url = fluxo.get("verification_uri", "https://microsoft.com/devicelogin")
        c.codigo = fluxo.get("user_code", "")
        c.expira = float(fluxo.get("expires_at") or time.time() + int(fluxo.get("expires_in", 900)))

        def esperar() -> None:
            try:
                c.detalhe = auth.conectar(mostrar=lambda _: None, fluxo=fluxo)
                c.status = "ok"
            except Exception as e:
                if c.status != "aguardando":  # já resolvido pela verificação do token
                    return
                expirou = "70020" in str(e) or "expired" in str(e).lower()
                c.status = "expirado" if expirou else "erro"
                c.detalhe = str(e)

        conexoes[nome] = c
        threading.Thread(target=esperar, daemon=True).start()
        return _html("_conectar.html.j2", c=c)

    @app.get("/conectar/{nome}/status", response_class=HTMLResponse)
    def conectar_status(nome: str) -> HTMLResponse:
        c = conexoes.get(nome)
        if c is None:
            raise HTTPException(404, "nenhuma conexão iniciada")
        if c.status == "aguardando" and not c.forcado and (conta := conectado(fonte_graph(nome))):
            c.status, c.detalhe = "ok", conta
        return _html("_conectar.html.j2", c=c)

    @app.get("/conectar/{nome}/verificar", response_class=HTMLResponse)
    def conectar_verificar(nome: str) -> HTMLResponse:
        f = fonte_graph(nome)
        if conta := conectado(f):
            c = conexoes.get(nome)
            if c and c.status == "aguardando":
                c.status, c.detalhe = "ok", conta
            return _html("_conectar.html.j2", c=Conexao(fonte=nome, status="ok", detalhe=conta))
        c = Conexao(
            fonte=nome,
            status="desconectado",
            detalhe="conta não conectada (token ausente ou revogado)",
        )
        return _html("_conectar.html.j2", c=c)

    # --- rotinas: limpeza / lixeira -----------------------------------------

    def ocupado() -> HTMLResponse | None:
        if em_andamento():
            return _avisar("há um job em andamento; aguarde para mexer no acervo")
        return None

    @app.get("/limpeza", response_class=HTMLResponse)
    def limpeza_() -> HTMLResponse:
        return _html("limpeza.html.j2", pagina="limpeza", raiz=exibir, contas=limpeza.arvore(raiz))

    @app.get("/limpeza/mensagens", response_class=HTMLResponse)
    def limpeza_mensagens(caminho: str) -> HTMLResponse:
        try:
            msgs = limpeza.mensagens_da_pasta(raiz, caminho)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return _html("_limpeza_mensagens.html.j2", mensagens=msgs)

    @app.post("/limpeza/excluir", response_class=HTMLResponse)
    def limpeza_excluir(
        caminhos: Annotated[list[str], Form()] = [],  # noqa: B006
        recapturar: Annotated[bool, Form()] = False,
    ) -> HTMLResponse:
        if r := ocupado():
            return r
        if not caminhos:
            return _avisar("nada marcado")
        try:
            n = limpeza.mover_para_lixeira(raiz, caminhos, dir_estado(raiz) if recapturar else None)
        except (ValueError, OSError) as e:
            return _avisar(f"falha ao mover para a lixeira: {e}", "erro")
        r = _html(
            "_limpeza_arvore.html.j2",
            contas=limpeza.arvore(raiz),
            texto=f"{n} mensagem(ns) movida(s) para a lixeira"
            + ("; voltam na próxima captura" if recapturar else ""),
            tipo="ok",
        )
        return r

    @app.get("/lixeira", response_class=HTMLResponse)
    def lixeira() -> HTMLResponse:
        return _html("lixeira.html.j2", pagina="lixeira", raiz=exibir, lotes=limpeza.lotes(raiz))

    def _lotes(texto: str, tipo: str = "ok") -> HTMLResponse:
        return _html("_lixeira_lotes.html.j2", lotes=limpeza.lotes(raiz), texto=texto, tipo=tipo)

    @app.post("/lixeira/restaurar", response_class=HTMLResponse)
    def lixeira_restaurar(
        lote: Annotated[str, Form()], caminho: Annotated[str, Form()]
    ) -> HTMLResponse:
        if r := ocupado():
            return r
        try:
            limpeza.restaurar(raiz, lote, caminho)
        except (ValueError, OSError) as e:
            return _avisar(f"falha ao restaurar: {e}", "erro")
        return _lotes(f"restaurado: {caminho}")

    @app.post("/lixeira/excluir", response_class=HTMLResponse)
    def lixeira_excluir(lote: Annotated[str, Form()]) -> HTMLResponse:
        if r := ocupado():
            return r
        try:
            limpeza.excluir_lote(raiz, lote)
        except (ValueError, OSError) as e:
            return _avisar(f"falha na limpeza definitiva: {e}", "erro")
        return _lotes(f"lote {lote} apagado definitivamente")

    @app.post("/lixeira/esvaziar", response_class=HTMLResponse)
    def lixeira_esvaziar() -> HTMLResponse:
        if r := ocupado():
            return r
        try:
            n = limpeza.esvaziar(raiz)
        except OSError as e:
            return _avisar(f"falha ao esvaziar a lixeira: {e}", "erro")
        return _lotes(f"lixeira esvaziada: {n} mensagem(ns) apagada(s) definitivamente")

    return app


app = criar_app(Path(os.environ.get("EMAIL_ORGANIZER_OUTPUT", "/output")))
