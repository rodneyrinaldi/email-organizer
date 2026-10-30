from datetime import datetime
from pathlib import Path

from email_organizer.modelos import Metadata
from email_organizer.repositorio import (
    gravar_json,
    ler_json,
    nome_diretorio,
    nome_remetente,
    slug,
)


def test_slug():
    assert slug("Financiamento Imobiliário — Bradesco!") == "Financiamento-Imobiliario-Bradesco"
    assert slug("") == "sem-assunto"
    assert slug("ação " * 30) == ("acao-" * 8).rstrip("-")
    assert len(slug("x" * 100)) == 40


def test_nome_remetente():
    assert nome_remetente("Joao@Email.com") == "joao@email.com"
    assert nome_remetente("a b/c@x.com") == "a_b_c@x.com"
    assert nome_remetente(None) == "_sem-remetente"
    assert nome_remetente("  ") == "_sem-remetente"


def test_nome_diretorio():
    d = datetime(2026, 10, 29, 14, 33, 10)
    assert nome_diretorio(d, "Oi", "8e4c2b9a1f03") == "2026-10-29_14-33-10_Oi_8e4c2b"
    assert nome_diretorio(None, "Oi", "8e4c2b9a1f03") == "sem-data_Oi_8e4c2b"


def test_gravar_json_atomico(tmp_path: Path):
    p = tmp_path / "a" / "x.json"
    gravar_json(p, {"á": 1})
    gravar_json(p, {"á": 2})
    assert ler_json(p) == {"á": 2}
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]


def test_metadata_preserva_chaves_de_outras_rotinas():
    base = {
        "id": "x",
        "chave_dedup": {"tipo": "message_id", "valor": "<a@b>"},
        "origem": {"tipo": "eml"},
        "direcao": "recebida",
        "original": {"arquivo": "original/mensagem.eml", "sha256": "0", "tamanho": 1},
        "thread": {"tipo": "nova", "metodo": "cabecalho"},
        "resumo": {"texto": "rotina futura"},
    }
    dump = Metadata.model_validate(base).model_dump(mode="json", exclude_none=True)
    assert dump["resumo"] == {"texto": "rotina futura"}
