import json
from pathlib import Path

from email_organizer.config import carregar_config, dir_estado

EXEMPLO = Path(__file__).parents[1] / "config.example.json"


def test_config_exemplo():
    c = carregar_config(EXEMPLO)
    ativas = c.fontes_ativas()
    assert [(f.usuario, f.nomes_pastas()) for f in ativas] == [
        ("voce@gmail.com", ["mariana"])
    ]
    assert all(not p.ativo for p in c.pst)
    assert "contato@escritorio.adv.br" in c.todas_contas_proprias()
    assert "Outros" in c.categorias


def test_segredos_preenchem_senha_imap(tmp_path):
    segredos = tmp_path / "segredos.json"
    segredos.write_text(json.dumps({"Gmail pessoal": "s3nha"}), encoding="utf-8")
    c = carregar_config(EXEMPLO, segredos)
    assert next(f for f in c.imap if f.nome == "Gmail pessoal").senha == "s3nha"
    assert carregar_config(EXEMPLO, tmp_path / "nao-existe.json").imap[0].senha == ""


def test_dir_estado_por_variavel(tmp_path, monkeypatch):
    monkeypatch.delenv("EMAIL_ORGANIZER_ESTADO", raising=False)
    assert dir_estado(tmp_path) == tmp_path / ".estado"
    monkeypatch.setenv("EMAIL_ORGANIZER_ESTADO", "/estado")
    assert dir_estado(tmp_path) == Path("/estado")
