"""Fonte IMAP — somente leitura (EXAMINE + BODY.PEEK). Nunca STORE/COPY/MOVE/EXPUNGE.

Estado incremental: UIDs já capturados por pasta/UIDVALIDITY. Mensagens rejeitadas
por filtro não entram no estado, então mudar o filtro reavalia tudo.
"""

import base64
import contextlib
import imaplib
import re
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

from email_organizer.config import FonteImap
from email_organizer.filtro import Filtro
from email_organizer.fontes import MensagemBruta
from email_organizer.modelos import Origem
from email_organizer.repositorio import gravar_json, ler_json, slug

_LIST = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<nome>.+)')
_UTF7 = re.compile(r"&([A-Za-z0-9+,]*)-")
_MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

Conexao = Callable[[FonteImap], imaplib.IMAP4]


# --- UTF-7 modificado (RFC 3501 §5.1.3) ----------------------------------------


def decodificar_utf7(nome: str) -> str:
    def dec(m: re.Match) -> str:
        if not m.group(1):
            return "&"
        b64 = m.group(1).replace(",", "/")
        return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")

    return _UTF7.sub(dec, nome)


def codificar_utf7(nome: str) -> str:
    saida, pendente = [], []

    def descarregar() -> None:
        if pendente:
            b64 = base64.b64encode("".join(pendente).encode("utf-16-be")).decode()
            saida.append("&" + b64.rstrip("=").replace("/", ",") + "-")
            pendente.clear()

    for c in nome:
        if 0x20 <= ord(c) <= 0x7E:
            descarregar()
            saida.append("&-" if c == "&" else c)
        else:
            pendente.append(c)
    descarregar()
    return "".join(saida)


def _aspas(nome: str) -> str:
    return '"' + nome.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _data_imap(d: date) -> str:
    return f"{d.day}-{_MESES[d.month - 1]}-{d.year}"


def criterios_servidor(filtros: list[Filtro]) -> list[str]:
    """Pré-filtro no servidor: só datas (SENTSINCE/SENTBEFORE usam o cabeçalho Date),
    com 1 dia de folga por fuso. Texto não é enviado: o servidor não ignora acentos e
    daria falso negativo. A decisão final é sempre o filtro local."""
    criterios: list[str] = []
    for f in filtros:
        if f.data_de:
            criterios += ["SENTSINCE", _data_imap(f.data_de - timedelta(days=1))]
        if f.data_ate:
            criterios += ["SENTBEFORE", _data_imap(f.data_ate + timedelta(days=2))]
    return criterios or ["ALL"]


def conectar(cfg: FonteImap) -> imaplib.IMAP4:
    conn = imaplib.IMAP4_SSL(cfg.host, cfg.porta) if cfg.ssl else imaplib.IMAP4(cfg.host, cfg.porta)
    conn.login(cfg.usuario, cfg.senha)
    return conn


class LeitorImap:
    def __init__(
        self,
        cfg: FonteImap,
        dir_estado: Path,
        filtros_da_pasta: Callable[[str], list[Filtro]] = lambda _: [],
        conexao: Conexao = conectar,
    ) -> None:
        self.cfg = cfg
        self.filtros_da_pasta = filtros_da_pasta
        self.dir_estado = dir_estado / "imap" / slug(cfg.usuario, 80)
        self._conn = conexao(cfg)
        self._estados: dict[str, dict] = {}

    # estado ---------------------------------------------------------------

    def _arquivo_estado(self, pasta: str) -> Path:
        return self.dir_estado / f"{slug(pasta, 80)}.json"

    def _estado(self, pasta: str, uidvalidity: int) -> dict:
        arq = self._arquivo_estado(pasta)
        estado = ler_json(arq) if arq.exists() else {}
        if estado.get("uidvalidity") != uidvalidity:
            estado = {"pasta": pasta, "uidvalidity": uidvalidity, "capturados": []}
        estado["_set"] = set(estado["capturados"])
        self._estados[pasta] = estado
        return estado

    def registrar(self, origem: Origem) -> None:
        estado = self._estados.get(origem.pasta or "")
        if estado is not None and origem.uidvalidity == estado["uidvalidity"]:
            estado["_set"].add(origem.uid)

    def salvar_estado(self) -> None:
        for pasta, estado in self._estados.items():
            dados = {k: v for k, v in estado.items() if k != "_set"}
            dados["capturados"] = sorted(estado["_set"])
            gravar_json(self._arquivo_estado(pasta), dados)

    # leitura --------------------------------------------------------------

    def listar_pastas(self) -> list[str]:
        tipo, dados = self._conn.list()
        if tipo != "OK":
            raise RuntimeError(f"LIST falhou: {dados}")
        pastas = []
        for item in dados:
            if isinstance(item, tuple):  # nome como literal {n}
                linha, nome_bruto = item[0], item[1]
                m = _LIST.match(linha)
            else:
                m = _LIST.match(item or b"")
                nome_bruto = m.group("nome") if m else b""
            if not m or b"\\noselect" in m.group("flags").lower():
                continue
            nome = nome_bruto.decode("ascii", "replace").strip()
            if nome.startswith('"') and nome.endswith('"'):
                nome = nome[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            pastas.append(decodificar_utf7(nome))
        return pastas

    def mensagens(self, pastas: list[str]) -> Iterator[MensagemBruta]:
        for pasta in pastas:
            yield from self._mensagens_pasta(pasta)

    def _mensagens_pasta(self, pasta: str) -> Iterator[MensagemBruta]:
        tipo, dados = self._conn.select(_aspas(codificar_utf7(pasta)), readonly=True)
        if tipo != "OK":
            raise RuntimeError(f"pasta {pasta!r} inacessível: {dados}")
        _, valores = self._conn.response("UIDVALIDITY")
        uidvalidity = int(valores[0]) if valores and valores[0] else 0
        estado = self._estado(pasta, uidvalidity)

        criterios = criterios_servidor(self.filtros_da_pasta(pasta))
        tipo, dados = self._conn.uid("SEARCH", None, *criterios)
        if tipo != "OK":
            raise RuntimeError(f"SEARCH falhou em {pasta!r}: {dados}")
        uids = [int(u) for u in (dados[0] or b"").split()]
        for uid in uids:
            if uid in estado["_set"]:
                continue
            bruto = self._baixar(uid)
            if bruto is None:
                continue
            origem = Origem(
                tipo="imap",
                conta=self.cfg.usuario,
                pasta=pasta,
                uid=uid,
                uidvalidity=uidvalidity,
            )
            yield MensagemBruta(bruto, origem)
        self.salvar_estado()

    def _baixar(self, uid: int) -> bytes | None:
        tipo, dados = self._conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if tipo != "OK":
            raise RuntimeError(f"FETCH {uid} falhou: {dados}")
        for item in dados:
            if isinstance(item, tuple) and len(item) > 1:
                return item[1]
        return None  # mensagem removida entre SEARCH e FETCH

    def fechar(self) -> None:
        self.salvar_estado()
        with contextlib.suppress(Exception):
            self._conn.logout()
