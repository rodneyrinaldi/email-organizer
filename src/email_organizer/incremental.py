"""Controle incremental de IMAP/Graph (`<estado>/imap|graph/<conta>/<pasta>.json`).

Lista o que já foi capturado em cada pasta, para a próxima execução baixar só o novo.
Aqui ficam as operações de fora dos leitores: liberar mensagens excluídas do acervo (para
serem recapturadas) e zerar o controle de uma fonte (reler tudo; a dedup evita duplicatas).
"""

import shutil
from pathlib import Path

from email_organizer.config import Fonte, FonteGraph, FonteImap
from email_organizer.repositorio import gravar_json, ler_json, slug


def _arquivo(estado: Path, tipo: str, conta: str, pasta: str) -> Path:
    return estado / tipo / slug(conta, 80) / f"{slug(pasta, 80)}.json"


def liberar(estado: Path, origens: list[dict]) -> int:
    """Tira as origens (IMAP uid / Graph id) do controle. Retorna quantas foram liberadas."""
    por_arquivo: dict[Path, set] = {}
    for o in origens:
        if o.get("tipo") == "imap" and o.get("uid") is not None:
            chave = o["uid"]
        elif o.get("tipo") == "graph" and o.get("graph_id"):
            chave = o["graph_id"]
        else:
            continue  # PST/eml: sem controle incremental
        arq = _arquivo(estado, o["tipo"], o.get("conta") or "", o.get("pasta") or "")
        por_arquivo.setdefault(arq, set()).add((chave, o.get("uidvalidity")))
    total = 0
    for arq, chaves in por_arquivo.items():
        if not arq.exists():
            continue
        dados = ler_json(arq)
        validade = dados.get("uidvalidity")
        remover = {c for c, uv in chaves if uv is None or uv == validade}
        antes = len(dados.get("capturados", []))
        dados["capturados"] = [c for c in dados.get("capturados", []) if c not in remover]
        total += antes - len(dados["capturados"])
        gravar_json(arq, dados)
    return total


def zerar(estado: Path, fonte: Fonte) -> bool:
    """Apaga o controle da fonte: a próxima execução relê todas as pastas. True se havia algo."""
    if isinstance(fonte, FonteImap):
        tipo = "imap"
    elif isinstance(fonte, FonteGraph):
        tipo = "graph"
    else:
        return False  # PST não tem controle: sempre relê
    pasta = estado / tipo / slug(fonte.usuario, 80)
    if not pasta.exists():
        return False
    shutil.rmtree(pasta)
    return True
