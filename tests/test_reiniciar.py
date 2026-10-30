from pathlib import Path

from email_organizer.reiniciar import alvos, reiniciar


def _arvore(raiz: Path, estado: Path) -> None:
    for p in (
        raiz / "config.json",
        raiz / "_index.json",
        raiz / "_jobs" / "j1" / "log.txt",
        raiz / "joao@x.com" / "msg" / "metadata.json",
        estado / "segredos.json",
        estado / "tokens" / "conta.json",
        estado / "imap" / "conta" / "INBOX.json",
        estado / "graph" / "conta" / "x.json",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")


def _restante(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}


def test_reinicia_preservando_config_e_credenciais(tmp_path: Path):
    raiz, estado = tmp_path / "out", tmp_path / "estado"
    _arvore(raiz, estado)
    assert reiniciar(raiz, estado)
    assert _restante(raiz) == {"config.json"}
    assert _restante(estado) == {"segredos.json", "tokens/conta.json"}


def test_estado_dentro_do_output(tmp_path: Path):
    raiz = tmp_path / "out"
    estado = raiz / ".estado"
    _arvore(raiz, estado)
    assert estado not in alvos(raiz, estado)
    reiniciar(raiz, estado)
    assert _restante(raiz) == {"config.json", ".estado/segredos.json", ".estado/tokens/conta.json"}


def test_pastas_inexistentes(tmp_path: Path):
    assert reiniciar(tmp_path / "nada", tmp_path / "nada2") == []
