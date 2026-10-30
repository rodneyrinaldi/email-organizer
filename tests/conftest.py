from pathlib import Path

import pytest

from email_organizer.modelos import Origem
from email_organizer.repositorio import Repositorio

FIXTURES = Path(__file__).parent / "fixtures"
CONTAS = {"voce@gmail.com", "contato@escritorio.adv.br"}


@pytest.fixture
def repo(tmp_path: Path) -> Repositorio:
    return Repositorio(tmp_path / "output")


@pytest.fixture
def origem_imap() -> Origem:
    return Origem(tipo="imap", conta="voce@gmail.com", pasta="mariana", uid=1)


def eml(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()
