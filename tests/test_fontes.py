from datetime import date
from pathlib import Path

import pytest

from conftest import eml
from email_organizer.config import FonteImap, FontePst
from email_organizer.filtro import Filtro
from email_organizer.fontes.imap import (
    LeitorImap,
    codificar_utf7,
    criterios_servidor,
    decodificar_utf7,
)
from email_organizer.fontes.pst import LeitorPst

# --- IMAP --------------------------------------------------------------------


class ImapFalso:
    """Servidor IMAP mínimo em memória; registra comandos para checar que é só leitura."""

    def __init__(self, pastas: dict[str, dict[int, bytes]], uidvalidity: int = 7) -> None:
        self.pastas = pastas
        self.uidvalidity = uidvalidity
        self.comandos: list[tuple] = []
        self.atual: str | None = None

    def list(self):
        linhas = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Noselect \\HasChildren) "/" "[Gmail]"']
        linhas += [f'(\\HasNoChildren) "/" "{codificar_utf7(p)}"'.encode() for p in self.pastas]
        return "OK", linhas

    def select(self, nome, readonly=False):
        self.comandos.append(("select", nome, readonly))
        self.atual = decodificar_utf7(nome.strip('"'))
        return ("OK", [b"1"]) if self.atual in self.pastas else ("NO", [b"nao existe"])

    def response(self, codigo):
        return codigo, [str(self.uidvalidity).encode()]

    def uid(self, comando, *args):
        self.comandos.append(("uid", comando, *args))
        msgs = self.pastas[self.atual]
        if comando == "SEARCH":
            return "OK", [" ".join(map(str, sorted(msgs))).encode()]
        if comando == "FETCH":
            u = int(args[0])
            return "OK", [(f"1 (UID {u} BODY[] {{9}}".encode(), msgs[u]), b")"]
        raise AssertionError(f"comando não permitido: {comando}")

    def logout(self):
        self.comandos.append(("logout",))


def _cfg_imap() -> FonteImap:
    return FonteImap(nome="g", host="h", usuario="eu@gmail.com", pastas=["mariana"])


def test_utf7_ida_e_volta():
    for nome in ("Itens Enviados", "Lixo & Spam", "Ações/Procurações", "日本"):
        assert decodificar_utf7(codificar_utf7(nome)) == nome
    assert codificar_utf7("Ações") == "A&AOcA9Q-es"


def test_criterios_servidor_so_datas():
    assert criterios_servidor([Filtro(remetente=["joão"])]) == ["ALL"]
    assert criterios_servidor([Filtro(data_de=date(2025, 3, 1), data_ate=date(2025, 3, 31))]) == [
        "SENTSINCE", "28-Feb-2025", "SENTBEFORE", "2-Apr-2025",
    ]  # fmt: skip


def test_imap_somente_leitura_e_incremental(tmp_path: Path):
    servidor = ImapFalso({"mariana": {1: eml("simples.eml"), 2: eml("enviada.eml")}})
    leitor = LeitorImap(_cfg_imap(), tmp_path, conexao=lambda _: servidor)
    assert "mariana" in leitor.listar_pastas() and "[Gmail]" not in leitor.listar_pastas()

    msgs = list(leitor.mensagens(["mariana"]))
    assert [m.origem.uid for m in msgs] == [1, 2]
    assert msgs[0].bruto == eml("simples.eml")
    assert msgs[0].origem.uidvalidity == 7 and msgs[0].origem.pasta == "mariana"
    leitor.registrar(msgs[0].origem)  # só a 1 foi capturada (2 filtrada, por ex.)
    leitor.fechar()

    selects = [c for c in servidor.comandos if c[0] == "select"]
    assert all(readonly for _, _, readonly in selects)
    fetches = [c for c in servidor.comandos if c[:2] == ("uid", "FETCH")]
    assert all(c[3] == "(BODY.PEEK[])" for c in fetches)

    servidor.comandos.clear()
    leitor2 = LeitorImap(_cfg_imap(), tmp_path, conexao=lambda _: servidor)
    assert [m.origem.uid for m in leitor2.mensagens(["mariana"])] == [2]


def test_imap_uidvalidity_mudou_relê_tudo(tmp_path: Path):
    servidor = ImapFalso({"mariana": {1: eml("simples.eml")}})
    leitor = LeitorImap(_cfg_imap(), tmp_path, conexao=lambda _: servidor)
    for m in leitor.mensagens(["mariana"]):
        leitor.registrar(m.origem)
    leitor.fechar()
    servidor.uidvalidity = 8
    leitor2 = LeitorImap(_cfg_imap(), tmp_path, conexao=lambda _: servidor)
    assert len(list(leitor2.mensagens(["mariana"]))) == 1


def test_imap_pasta_inexistente(tmp_path: Path):
    leitor = LeitorImap(_cfg_imap(), tmp_path, conexao=lambda _: ImapFalso({}))
    with pytest.raises(RuntimeError, match="inacessível"):
        list(leitor.mensagens(["nada"]))


# --- PST ---------------------------------------------------------------------


def _exportador_falso(pst: Path, destino: Path) -> None:
    raiz = destino / "Arquivo de Dados do Outlook"
    for pasta, nomes in {
        "Caixa de Entrada": ["simples.eml", "enviada.eml"],
        "Caixa de Entrada/Mariana": ["com_anexo.eml"],
        "Itens Enviados": ["enviada.eml"],
    }.items():
        d = raiz / pasta
        d.mkdir(parents=True)
        for i, n in enumerate(nomes, start=1):
            (d / f"{i}.eml").write_bytes(eml(n))


def test_pst(tmp_path: Path):
    pst = tmp_path / "Advocacia.pst"
    pst.write_bytes(b"!BDN")
    leitor = LeitorPst(FontePst(nome="a", caminho=str(pst)), tmp_path, _exportador_falso)
    assert leitor.listar_pastas() == [
        "Caixa de Entrada",
        "Caixa de Entrada/Mariana",
        "Itens Enviados",
    ]
    msgs = list(leitor.mensagens(["caixa de entrada/mariana", "inexistente"]))
    assert len(msgs) == 1
    o = msgs[0].origem
    assert (o.tipo, o.arquivo, o.pasta) == ("pst", "Advocacia.pst", "Caixa de Entrada/Mariana")
    assert "readpst" in o.observacao
    tmp = leitor._tmp
    leitor.fechar()
    assert not tmp.exists()
    assert pst.read_bytes() == b"!BDN"


def test_pst_inexistente(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        LeitorPst(FontePst(nome="a", caminho=str(tmp_path / "x.pst")), tmp_path, _exportador_falso)
