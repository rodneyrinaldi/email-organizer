"""Rotina de limpeza (specs/limpeza.md): árvore do acervo e lixeira em `<raiz>/.lixeira/`.

Excluir = mover para `.lixeira/<lote>/<caminho relativo>`; restaurar = mover de volta.
Só a limpeza definitiva apaga de verdade. Após mover, o índice é reconstruído.
"""

import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from email_organizer.repositorio import (
    ENVIADAS,
    METADATA,
    Repositorio,
    gravar_json,
    ler_json,
)

LIXEIRA = ".lixeira"
LOTE = "_lote.json"


@dataclass
class No:
    nome: str
    caminho: str  # relativo à raiz, posix
    tipo: str  # conta | enviadas | remetente | destinatario
    mensagens: int = 0
    filhos: dict[str, "No"] = field(default_factory=dict)
    tem_mensagens: bool = False  # mensagens diretamente nesta pasta


def _tipo(partes: tuple[str, ...]) -> str:
    if len(partes) == 1:
        return "conta"
    if partes[1] == ENVIADAS:
        return "enviadas" if len(partes) == 2 else "destinatario"
    return "remetente"


def arvore(raiz: Path) -> list[No]:
    """Pastas (conta → remetente | .enviadas → destinatário) com contagem de mensagens."""
    topo: dict[str, No] = {}
    for d in Repositorio(raiz).mensagens():
        partes = d.relative_to(raiz).parts[:-1]
        nivel = topo
        for i in range(len(partes)):
            sub = partes[: i + 1]
            no = nivel.get(sub[-1])
            if no is None:
                no = nivel[sub[-1]] = No(sub[-1], "/".join(sub), _tipo(sub))
            no.mensagens += 1
            if i == len(partes) - 1:
                no.tem_mensagens = True
            nivel = no.filhos
    return list(topo.values())


def mensagens_da_pasta(raiz: Path, caminho: str) -> list[dict]:
    pasta = _validar(raiz, caminho, _validos(raiz))
    saida = []
    for d in sorted(pasta.iterdir()):
        if d.is_dir() and (d / METADATA).exists():
            meta = ler_json(d / METADATA)
            saida.append(
                {
                    "caminho": d.relative_to(raiz).as_posix(),
                    "assunto": meta.get("assunto") or "(sem assunto)",
                    "data": (meta.get("data") or "")[:16].replace("T", " "),
                }
            )
    return saida


def _validos(raiz: Path) -> dict[str, int]:
    """Caminhos que podem ser excluídos (cada mensagem e cada pasta acima dela) → nº de
    mensagens dentro."""
    validos: dict[str, int] = {}
    for d in Repositorio(raiz).mensagens():
        partes = d.relative_to(raiz).parts
        for i in range(len(partes)):
            c = "/".join(partes[: i + 1])
            validos[c] = validos.get(c, 0) + 1
    return validos


def _validar(raiz: Path, caminho: str, validos: dict[str, int]) -> Path:
    if caminho not in validos:
        raise ValueError(f"caminho inválido: {caminho}")
    return raiz / caminho


def _limpar_vazias(pasta: Path, raiz: Path) -> None:
    while pasta != raiz and pasta.is_dir() and not any(pasta.iterdir()):
        pasta.rmdir()
        pasta = pasta.parent


def _novo_lote(lixeira: Path) -> Path:
    nome = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    lote, n = lixeira / nome, 2
    while lote.exists():
        lote, n = lixeira / f"{nome}-{n}", n + 1
    return lote


def mover_para_lixeira(raiz: Path, caminhos: list[str]) -> int:
    """Move as seleções para um novo lote. Seleção dentro de outra selecionada é ignorada.
    Retorna o número de mensagens movidas."""
    validos = _validos(raiz)
    escolhidos = sorted({c.strip("/") for c in caminhos})
    for c in escolhidos:
        _validar(raiz, c, validos)
    escolhidos = [
        c
        for c in escolhidos
        if not any(PurePosixPath(c).is_relative_to(o) and c != o for o in escolhidos)
    ]
    if not escolhidos:
        return 0
    lote = _novo_lote(raiz / LIXEIRA)
    itens, total = [], 0
    for c in escolhidos:
        origem, destino = raiz / c, lote / c
        n = validos[c]
        destino.parent.mkdir(parents=True, exist_ok=True)
        origem.rename(destino)
        _limpar_vazias(origem.parent, raiz)
        itens.append({"caminho": c, "mensagens": n})
        total += n
    gravar_json(
        lote / LOTE, {"criado_em": datetime.now().isoformat(timespec="seconds"), "itens": itens}
    )
    Repositorio(raiz).reconstruir_indice()
    return total


# --- lixeira ------------------------------------------------------------------


def lotes(raiz: Path) -> list[dict]:
    lixeira = raiz / LIXEIRA
    if not lixeira.is_dir():
        return []
    saida = []
    for d in sorted(lixeira.iterdir(), reverse=True):
        if d.is_dir() and (d / LOTE).exists():
            dados = ler_json(d / LOTE)
            itens = dados.get("itens", [])
            saida.append(
                {
                    "nome": d.name,
                    "criado_em": dados.get("criado_em", ""),
                    "itens": itens,
                    "mensagens": sum(i.get("mensagens", 0) for i in itens),
                }
            )
    return saida


def _lote(raiz: Path, nome: str) -> Path:
    d = raiz / LIXEIRA / nome
    if "/" in nome or "\\" in nome or nome.startswith(".") or not (d / LOTE).exists():
        raise ValueError(f"lote inválido: {nome}")
    return d


def restaurar(raiz: Path, nome: str, caminho: str) -> None:
    lote = _lote(raiz, nome)
    dados = ler_json(lote / LOTE)
    itens = dados.get("itens", [])
    if caminho not in {i["caminho"] for i in itens}:
        raise ValueError(f"item não está no lote: {caminho}")
    origem, destino = lote / caminho, raiz / caminho
    if destino.exists():
        raise FileExistsError(f"já existe no acervo: {caminho}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    origem.rename(destino)
    _limpar_vazias(origem.parent, lote)
    dados["itens"] = [i for i in itens if i["caminho"] != caminho]
    if dados["itens"]:
        gravar_json(lote / LOTE, dados)
    else:
        _apagar(lote)
    Repositorio(raiz).reconstruir_indice()


def _forcar_escrita(funcao, caminho, _exc) -> None:  # noqa: ANN001
    """Windows/sincronizadores: arquivo somente leitura impede rmtree."""
    os.chmod(caminho, stat.S_IWRITE)
    funcao(caminho)


def _apagar(pasta: Path) -> None:
    shutil.rmtree(pasta, onexc=_forcar_escrita)


def excluir_lote(raiz: Path, nome: str) -> None:
    """Limpeza definitiva de um lote. Irreversível."""
    _apagar(_lote(raiz, nome))


def esvaziar(raiz: Path) -> int:
    """Limpeza definitiva de toda a lixeira. Irreversível. Retorna mensagens apagadas."""
    n = sum(lote["mensagens"] for lote in lotes(raiz))
    lixeira = raiz / LIXEIRA
    if lixeira.is_dir():
        _apagar(lixeira)
    return n
