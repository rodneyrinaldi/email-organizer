"""Rotina 'resumo' (specs/resumo.md): resumo por mensagem + considerações finais, em PDF.

Janela de contexto (modelo pequeno, ex.: qwen2.5:7b):
- cada mensagem é resumida isoladamente; o resultado fica em cache no `metadata.json`
  (chave `resumo`) e é reaproveitado nas próximas vezes;
- o corpo vai sem o histórico citado (respostas repetem a conversa inteira);
- corpo longo: resumido em partes (map) e as notas das partes consolidadas (reduce);
- considerações: feitas só sobre os resumos curtos; se não couberem numa chamada,
  consolidação hierárquica em blocos até caber.
IA só enriquece: grava `resumo` e `pipeline.resumo`; nada é movido ou apagado.
"""

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader
from pydantic import BaseModel, ValidationError, field_validator

from email_organizer import __version__
from email_organizer.captura import agora
from email_organizer.ia import ClienteOllama, Indisponivel, RespostaInvalida, _entrada
from email_organizer.limpeza import _validar, _validos
from email_organizer.repositorio import METADATA, Repositorio, gravar_json, ler_json

ETAPA = "resumo"
VERSAO = 1  # muda o prompt → muda aqui: invalida o cache dos resumos
CONTEXTO = 8192  # num_ctx (tokens) pedido ao Ollama
MAX_CORPO = 7000  # chars do corpo numa chamada (~2,5 mil tokens em português)
PARTE = 5000  # tamanho de cada parte quando o corpo passa de MAX_CORPO
MAX_PARTES = 8  # corpo gigante: o resto é cortado
MAX_BLOCO = 12000  # chars de resumos por chamada na consolidação
TENTATIVAS = 2

TEMPERATURAS = ("cordial", "neutra", "tensa", "hostil")
SITUACOES = ("resolvida", "em andamento", "pendente", "em conflito", "informativa")

SISTEMA = (
    "Você analisa e-mails de um acervo documental jurídico pessoal (Brasil). Seja fiel ao "
    "texto: não invente fatos, nomes, valores ou datas; se algo não estiver claro, diga. "
    "Escreva em português e responda somente com JSON válido."
)


# --- respostas -------------------------------------------------------------------


def _normalizar(valor: str, validos: tuple[str, ...], padrao: str) -> str:
    v = (valor or "").strip().casefold()
    return next((x for x in validos if x == v or v.startswith(x)), padrao)


class ResumoMensagem(BaseModel):
    resumo: str
    temperatura: str
    assuntos: list[str]
    pedidos_prazos: list[str]

    @field_validator("temperatura")
    @classmethod
    def _temp(cls, v: str) -> str:
        return _normalizar(v, TEMPERATURAS, "neutra")


class Notas(BaseModel):
    notas: str


class Consideracoes(BaseModel):
    situacao: str
    panorama: str
    conteudo: str
    temperatura: str
    evolucao_tom: str
    pontos_atencao: list[str]
    proximos_passos: list[str]

    @field_validator("situacao")
    @classmethod
    def _sit(cls, v: str) -> str:
        return _normalizar(v, SITUACOES, "informativa")

    @field_validator("temperatura")
    @classmethod
    def _temp(cls, v: str) -> str:
        return _normalizar(v, TEMPERATURAS, "neutra")


def _perguntar[M: BaseModel](cliente: ClienteOllama, prompt: str, modelo: type[M]) -> M:
    esquema = modelo.model_json_schema()
    esquema["required"] = list(esquema["properties"])
    ultimo: Exception | None = None
    for _ in range(TENTATIVAS):
        conteudo = cliente.json(SISTEMA, prompt, esquema, contexto=CONTEXTO)
        try:
            return modelo.model_validate(json.loads(conteudo))
        except (json.JSONDecodeError, ValidationError) as e:
            ultimo = e
    raise RespostaInvalida(f"resposta inválida do modelo: {str(ultimo)[:200]}")


# --- texto da mensagem ----------------------------------------------------------

_CORTE = re.compile(
    r"^\s*(?:"
    r"Em .{0,200}escreveu:\s*$|On .{0,200}wrote:\s*$"
    r"|-{2,}\s*(?:Original Message|Mensagem original|Forwarded message|Mensagem encaminhada)"
    r"|_{10,}\s*$"
    r"|\**(?:De|From)\**:\**\s.+"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_ENCAMINHADA = re.compile(
    r"^\s*(?:enc|fw|fwd)\s*:|-{2,}\s*(?:Forwarded message|Mensagem encaminhada)",
    re.IGNORECASE | re.MULTILINE,
)
_PROPRIO_MINIMO = 200  # encaminhamento com texto próprio menor que isso mantém o encaminhado


def sem_citacao(texto: str, assunto: str = "") -> str:
    """Tira o histórico citado (`>` e o que vem depois de "Em ... escreveu:", "De:" etc.).
    Encaminhamento com pouco texto próprio mantém o conteúdo encaminhado: é ele o assunto."""
    linhas = [li for li in texto.splitlines() if not li.lstrip().startswith(">")]
    limpo = "\n".join(linhas)
    m = _CORTE.search(limpo)
    if not m:
        return limpo.strip()
    proprio, citado = limpo[: m.start()].strip(), limpo[m.start() :].strip()
    encaminhada = _ENCAMINHADA.search(assunto) or _ENCAMINHADA.search(citado[:200])
    if len(proprio) >= _PROPRIO_MINIMO or not encaminhada:
        return proprio
    return f"{proprio}\n\n[conteúdo citado/encaminhado]\n{citado}".strip()


def partes(texto: str, tamanho: int = PARTE) -> list[str]:
    """Quebra em partes de até `tamanho` chars, preferindo fim de parágrafo."""
    saida, resto = [], texto
    while len(resto) > tamanho:
        corte = resto.rfind("\n\n", 0, tamanho)
        if corte < tamanho // 2:
            corte = resto.rfind("\n", 0, tamanho)
        if corte < tamanho // 2:
            corte = tamanho
        saida.append(resto[:corte].strip())
        resto = resto[corte:]
    if resto.strip():
        saida.append(resto.strip())
    return saida


def _pessoa(p: dict | None) -> str:
    if not p:
        return "(desconhecido)"
    return f"{p.get('nome')} <{p.get('email')}>" if p.get("nome") else p.get("email") or "?"


def _cabecalho(meta: dict, anexos: list[str]) -> list[str]:
    return [
        f"Data: {meta.get('data') or '(sem data)'}",
        f"Direção: {meta.get('direcao')}",
        f"De: {_pessoa(meta.get('remetente'))}",
        f"Para: {', '.join(_pessoa(p) for p in meta.get('para', [])) or '(ninguém)'}",
        f"Assunto: {meta.get('assunto') or '(sem assunto)'}",
        f"Anexos: {', '.join(anexos) or '(nenhum)'}",
    ]


_CAMPOS_MSG = (
    "Campos: resumo (2 a 5 frases objetivas: quem pede ou informa o quê, decisões, valores, "
    "datas), temperatura (tom da mensagem: cordial | neutra | tensa | hostil), assuntos (até 3 "
    "temas curtos), pedidos_prazos (pedidos, compromissos e prazos explícitos; lista vazia se "
    "não houver)."
)


def resumir_texto(cliente: ClienteOllama, meta: dict, texto: str, anexos: list[str]) -> dict:
    corpo = sem_citacao(texto, meta.get("assunto") or "") or "(corpo vazio)"
    cab = _cabecalho(meta, anexos)
    if len(corpo) > MAX_CORPO:  # map-reduce dentro da mensagem
        pedacos = partes(corpo)
        cortado = len(pedacos) > MAX_PARTES
        notas = []
        for i, p in enumerate(pedacos[:MAX_PARTES], 1):
            prompt = "\n".join(
                [
                    f"Trecho {i} de {len(pedacos)} de um e-mail longo. Campo: notas (até 6 frases "
                    "com os fatos relevantes: pedidos, decisões, valores, datas, nomes).",
                    *cab,
                    "Trecho:",
                    p,
                ]
            )
            notas.append(f"[parte {i}] {_perguntar(cliente, prompt, Notas).notas.strip()}")
        corpo = "\n".join(notas) + ("\n[texto restante omitido]" if cortado else "")
        cab.append("Texto abaixo: notas das partes do e-mail, em ordem.")
    prompt = "\n".join([_CAMPOS_MSG, "", *cab, "Texto:", corpo])
    return _perguntar(cliente, prompt, ResumoMensagem).model_dump()


# --- etapa por mensagem (cache no metadata.json) --------------------------------


def resumir_mensagem(dir_msg: Path, cliente: ClienteOllama, refazer: bool = False) -> dict:
    """Resumo da mensagem; reaproveita o do metadata.json se for da mesma versão e modelo."""
    arq = dir_msg / METADATA
    dados = ler_json(arq)
    atual = dados.get(ETAPA)
    if (
        not refazer
        and atual
        and atual.get("versao") == VERSAO
        and atual.get("modelo") == cliente.modelo
    ):
        return {**atual, "reaproveitado": True}
    texto, anexos = _entrada(dir_msg)
    r = resumir_texto(cliente, dados, texto, anexos)
    dados = ler_json(arq)  # relê: só mexe nas próprias chaves
    dados[ETAPA] = {**r, "modelo": cliente.modelo, "versao": VERSAO, "gerado_em": agora()}
    dados.setdefault("pipeline", {})[ETAPA] = {"status": "ok", "em": agora(), "versao": __version__}
    gravar_json(arq, dados)
    return dados[ETAPA]


# --- considerações (reduce hierárquico) -----------------------------------------


def _linha(item: dict) -> str:
    r = item.get("resumo") or {}
    pedidos = "; ".join(r.get("pedidos_prazos") or [])
    return (
        f"[{item['n']}] {item['data'] or 's/d'} | {item['direcao']} | de {item['de']} | "
        f"assunto: {item['assunto']} | tom: {r.get('temperatura', '?')}\n"
        f"{r.get('resumo') or '(sem resumo)'}" + (f"\nPedidos/prazos: {pedidos}" if pedidos else "")
    )


def blocos(linhas: list[str], maximo: int | None = None) -> list[list[str]]:
    maximo = maximo or MAX_BLOCO
    saida: list[list[str]] = [[]]
    tamanho = 0
    for li in linhas:
        li = li[:maximo]
        if saida[-1] and tamanho + len(li) > maximo:
            saida.append([])
            tamanho = 0
        saida[-1].append(li)
        tamanho += len(li) + 1
    return saida


def consolidar(
    cliente: ClienteOllama, linhas: list[str], aviso: Callable[[str], None] | None = None
) -> dict:
    nivel = 0
    while sum(len(li) + 1 for li in linhas) > MAX_BLOCO:  # não cabe: sintetiza por blocos
        nivel += 1
        grupos = blocos(linhas)
        novas = []
        for i, g in enumerate(grupos, 1):
            if aviso:
                aviso(f"consolidando bloco {i} de {len(grupos)} (nível {nivel})")
            prompt = "\n".join(
                [
                    f"Bloco {i} de {len(grupos)} de resumos de e-mails em ordem cronológica. "
                    "Campo: notas (até 10 frases: fatos, pedidos, prazos, valores, decisões e "
                    "como o tom evoluiu; cite os números [n] das mensagens).",
                    "",
                    *g,
                ]
            )
            novas.append(f"[bloco {i}] {_perguntar(cliente, prompt, Notas).notas.strip()}")
        if len(novas) >= len(linhas):  # não reduziu: evita laço infinito
            linhas = [li[: MAX_BLOCO // max(1, len(novas))] for li in novas]
            break
        linhas = novas
    if aviso:
        aviso("redigindo as considerações finais")
    prompt = "\n".join(
        [
            "Abaixo, resumos de e-mails em ordem cronológica (ou notas de blocos deles). "
            "Analise o conjunto. Campos: situacao (resolvida | em andamento | pendente | em "
            "conflito | informativa), panorama (1 parágrafo: situação geral), conteudo (1 a 2 "
            "parágrafos: o que foi tratado), temperatura (tom predominante: cordial | neutra | "
            "tensa | hostil), evolucao_tom (como o tom mudou ao longo do tempo, citando [n]), "
            "pontos_atencao (riscos, divergências, prazos, valores em aberto), proximos_passos "
            "(o que parece pendente de quem; lista vazia se nada).",
            "",
            *linhas,
        ]
    )
    return _perguntar(cliente, prompt, Consideracoes).model_dump()


# --- seleção e execução ----------------------------------------------------------


def selecionar(raiz: Path, caminhos: list[str]) -> list[Path]:
    """Mensagens dentro dos caminhos marcados (pastas ou mensagens), sem repetir."""
    validos = _validos(raiz)
    alvos = [_validar(raiz, c, validos).relative_to(raiz).as_posix() for c in caminhos]
    saida = []
    for d in Repositorio(raiz).mensagens():
        rel = d.relative_to(raiz).as_posix()
        if any(rel == a or rel.startswith(a + "/") for a in alvos):
            saida.append(d)
    return saida


@dataclass
class Progresso:
    total: int = 0
    feitos: int = 0
    reaproveitados: int = 0
    fase: str = "mensagens"  # mensagens | consideracoes | pdf | fim
    atual: str = ""
    falhas: list[dict] = field(default_factory=list)


class Cancelado(Exception):
    pass


def gerar(
    raiz: Path,
    caminhos: list[str],
    cliente: ClienteOllama,
    destino: Path,
    refazer: bool = False,
    progresso: Progresso | None = None,
    cancelar: threading.Event | None = None,
) -> Path:
    """Resume as mensagens selecionadas e grava o PDF em `destino`."""
    prog = progresso or Progresso()
    dirs = selecionar(raiz, caminhos)
    if not dirs:
        raise ValueError("nenhuma mensagem na seleção")
    itens = []
    for d in dirs:
        m = ler_json(d / METADATA)
        itens.append(
            {
                "dir": d,
                "caminho": d.relative_to(raiz).as_posix(),
                "ordem": m.get("data_utc") or m.get("data") or "",
                "data": m.get("data"),
                "direcao": m.get("direcao"),
                "de": _pessoa(m.get("remetente")),
                "para": ", ".join(_pessoa(p) for p in m.get("para", [])),
                "assunto": m.get("assunto") or "(sem assunto)",
            }
        )
    itens.sort(key=lambda i: i["ordem"])
    prog.total = len(itens)
    for n, item in enumerate(itens, 1):
        if cancelar and cancelar.is_set():
            raise Cancelado("cancelado")
        item["n"] = n
        prog.atual = item["assunto"]
        try:
            item["resumo"] = resumir_mensagem(item["dir"], cliente, refazer)
            prog.reaproveitados += bool(item["resumo"].get("reaproveitado"))
        except Indisponivel:
            raise
        except Exception as e:  # uma mensagem ruim não derruba o documento
            item["resumo"] = None
            prog.falhas.append({"caminho": item["caminho"], "erro": str(e)[:300]})
        prog.feitos = n

    if cancelar and cancelar.is_set():
        raise Cancelado("cancelado")
    prog.fase = "consideracoes"
    validos = [i for i in itens if i["resumo"]]
    consideracoes = (
        consolidar(cliente, [_linha(i) for i in validos], lambda t: setattr(prog, "atual", t))
        if validos
        else None
    )

    prog.fase, prog.atual = "pdf", "montando o PDF"
    html = renderizar(caminhos, itens, consideracoes, cliente.modelo, prog.falhas)
    from email_organizer.artefatos.conversao import html_para_pdf

    destino.parent.mkdir(parents=True, exist_ok=True)
    html_para_pdf(html, destino)
    prog.fase, prog.atual = "fim", ""
    return destino


# --- PDF ---------------------------------------------------------------------------

_env = Environment(loader=PackageLoader("email_organizer", "templates"), autoescape=True)


def _data_br(iso: str | None) -> str:
    if not iso:
        return "s/d"
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")


_env.filters["data_br"] = _data_br


def renderizar(
    caminhos: list[str],
    itens: list[dict],
    consideracoes: dict | None,
    modelo: str,
    falhas: list[dict],
) -> str:
    datas = [i["data"] for i in itens if i["data"]]
    return _env.get_template("resumo.html.j2").render(
        caminhos=caminhos,
        itens=itens,
        c=consideracoes,
        modelo=modelo,
        falhas=falhas,
        gerado=datetime.now().strftime("%d/%m/%Y %H:%M"),
        periodo=(_data_br(min(datas))[:10], _data_br(max(datas))[:10]) if datas else None,
    )
