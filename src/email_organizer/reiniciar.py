"""Volta ao estado inicial: apaga o acervo gerado e o estado incremental.

Preserva `config.json`, `segredos.json` e `tokens/` (credenciais). Nunca acessa a caixa postal.
"""

import shutil
from pathlib import Path

PRESERVAR_OUTPUT = {"config.json"}
PRESERVAR_ESTADO = {"segredos.json", "tokens"}


def _filhos(base: Path, preservar: set[str], tambem: Path | None = None) -> list[Path]:
    if not base.is_dir():
        return []
    fora = {base / n for n in preservar} | ({tambem} if tambem else set())
    return sorted(p for p in base.iterdir() if p not in fora)


def alvos(raiz: Path, estado: Path) -> list[Path]:
    """O que `reiniciar` apagaria. O estado pode estar dentro do output (padrão `.estado`)."""
    return _filhos(raiz, PRESERVAR_OUTPUT, tambem=estado) + _filhos(estado, PRESERVAR_ESTADO)


def reiniciar(raiz: Path, estado: Path) -> list[Path]:
    apagados = alvos(raiz, estado)
    for p in apagados:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    return apagados
