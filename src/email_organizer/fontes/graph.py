"""Fonte Microsoft Graph (Hotmail/Outlook.com e Microsoft 365) — somente leitura.

Auth: MSAL device code flow, escopo Mail.Read (delegado). Token em cache em
.estado/tokens/. Bruto: GET /me/messages/{id}/$value (MIME original do servidor).
Estado incremental como no IMAP: ids já capturados por pasta.
"""

import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import httpx

from email_organizer.config import FonteGraph
from email_organizer.filtro import Filtro
from email_organizer.fontes import MensagemBruta
from email_organizer.modelos import Origem
from email_organizer.repositorio import gravar_atomico, gravar_json, ler_json, slug

GRAPH = "https://graph.microsoft.com/v1.0"
ESCOPOS = ["Mail.Read"]
TENTATIVAS = 5

ObterToken = Callable[[], str]


# --- auth ---------------------------------------------------------------------


class Autenticacao:
    def __init__(self, cfg: FonteGraph, dir_estado: Path) -> None:
        import msal  # import tardio: só quem usa Graph precisa

        if not cfg.client_id:
            raise ValueError(f"fonte {cfg.nome!r}: client_id não configurado")
        self.cfg = cfg
        self.arquivo = dir_estado / "tokens" / f"{slug(cfg.usuario, 80)}.json"
        self.cache = msal.SerializableTokenCache()
        if self.arquivo.exists():
            self.cache.deserialize(self.arquivo.read_text(encoding="utf-8"))
        self.app = msal.PublicClientApplication(
            cfg.client_id,
            authority=f"https://login.microsoftonline.com/{cfg.tenant}",
            token_cache=self.cache,
        )

    def _salvar(self) -> None:
        if self.cache.has_state_changed:
            gravar_atomico(self.arquivo, self.cache.serialize().encode("utf-8"))

    def token(self) -> str:
        contas = self.app.get_accounts(username=self.cfg.usuario) or self.app.get_accounts()
        r = self.app.acquire_token_silent(ESCOPOS, account=contas[0]) if contas else None
        if not r or "access_token" not in r:
            raise PermissionError(
                f"conta {self.cfg.usuario} não conectada: rode "
                f'`python -m email_organizer conectar "{self.cfg.nome}"`'
            )
        self._salvar()
        return r["access_token"]

    def verificar(self) -> str:
        """Conta conectada se o token em cache vale (renova sem interação); senão erro."""
        self.token()
        contas = self.app.get_accounts(username=self.cfg.usuario) or self.app.get_accounts()
        return contas[0].get("username") or self.cfg.usuario

    def iniciar(self) -> dict:
        """Device code flow, parte 1: fluxo com `message` (URL + código) e `verification_uri`."""
        fluxo = self.app.initiate_device_flow(scopes=ESCOPOS)
        if "user_code" not in fluxo:
            raise RuntimeError(f"falha ao iniciar device flow: {fluxo.get('error_description')}")
        return fluxo

    def conectar(self, mostrar: Callable[[str], None] = print, fluxo: dict | None = None) -> str:
        """Device code flow: mostra URL + código e espera (bloqueia) o usuário autorizar."""
        fluxo = fluxo or self.iniciar()
        mostrar(fluxo["message"])
        r = self.app.acquire_token_by_device_flow(fluxo)
        if "access_token" not in r:
            raise PermissionError(f"autorização falhou: {r.get('error_description')}")
        self._salvar()
        return r.get("id_token_claims", {}).get("preferred_username", self.cfg.usuario)


# --- leitor -------------------------------------------------------------------


def filtro_servidor(filtros: list[Filtro]) -> str | None:
    """Só datas (sentDateTime), com 1 dia de folga; decisão final é o filtro local."""
    partes = []
    for f in filtros:
        if f.data_de:
            partes.append(
                f"sentDateTime ge {(f.data_de - timedelta(days=1)).isoformat()}T00:00:00Z"
            )
        if f.data_ate:
            partes.append(
                f"sentDateTime lt {(f.data_ate + timedelta(days=2)).isoformat()}T00:00:00Z"
            )
    return " and ".join(partes) or None


class LeitorGraph:
    def __init__(
        self,
        cfg: FonteGraph,
        dir_estado: Path,
        filtros_da_pasta: Callable[[str], list[Filtro]] = lambda _: [],
        obter_token: ObterToken | None = None,
        cliente: httpx.Client | None = None,
    ) -> None:
        self.cfg = cfg
        self.filtros_da_pasta = filtros_da_pasta
        self.dir_estado = dir_estado / "graph" / slug(cfg.usuario, 80)
        self.obter_token = obter_token or Autenticacao(cfg, dir_estado).token
        self.http = cliente or httpx.Client(timeout=60)
        self._pastas: dict[str, str] | None = None  # caminho → id
        self._estados: dict[str, dict] = {}

    # http -----------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        for tentativa in range(TENTATIVAS):
            r = self.http.get(
                url, params=params, headers={"Authorization": f"Bearer {self.obter_token()}"}
            )
            if r.status_code in (429, 503, 504) and tentativa < TENTATIVAS - 1:
                time.sleep(float(r.headers.get("Retry-After", 2**tentativa)))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("inalcançável")

    def _paginas(self, url: str, params: dict | None = None) -> Iterator[dict]:
        proxima: str | None = url
        while proxima:
            dados = self._get(proxima, params).json()
            params = None  # nextLink já carrega os parâmetros
            yield from dados.get("value", [])
            proxima = dados.get("@odata.nextLink")

    # pastas ---------------------------------------------------------------

    def _mapa_pastas(self) -> dict[str, str]:
        if self._pastas is None:
            self._pastas = {}
            self._descer(f"{GRAPH}/me/mailFolders", "")
        return self._pastas

    def _descer(self, url: str, prefixo: str) -> None:
        params = {"$top": "100", "$select": "id,displayName,childFolderCount"}
        for p in self._paginas(url, params):
            caminho = f"{prefixo}{p['displayName']}"
            self._pastas[caminho] = p["id"]
            if p.get("childFolderCount"):
                self._descer(f"{GRAPH}/me/mailFolders/{p['id']}/childFolders", f"{caminho}/")

    def listar_pastas(self) -> list[str]:
        return list(self._mapa_pastas())

    # estado ---------------------------------------------------------------

    def _arquivo_estado(self, pasta: str) -> Path:
        return self.dir_estado / f"{slug(pasta, 80)}.json"

    def _estado(self, pasta: str) -> dict:
        arq = self._arquivo_estado(pasta)
        estado = ler_json(arq) if arq.exists() else {"pasta": pasta, "capturados": []}
        estado["_set"] = set(estado["capturados"])
        self._estados[pasta] = estado
        return estado

    def registrar(self, origem: Origem) -> None:
        estado = self._estados.get(origem.pasta or "")
        if estado is not None and origem.graph_id:
            estado["_set"].add(origem.graph_id)

    def salvar_estado(self) -> None:
        for pasta, estado in self._estados.items():
            dados = {k: v for k, v in estado.items() if k != "_set"}
            dados["capturados"] = sorted(estado["_set"])
            gravar_json(self._arquivo_estado(pasta), dados)

    # mensagens ------------------------------------------------------------

    def mensagens(self, pastas: list[str]) -> Iterator[MensagemBruta]:
        mapa = {k.casefold(): (k, v) for k, v in self._mapa_pastas().items()}
        for pedida in pastas:
            if pedida.casefold() not in mapa:
                raise RuntimeError(f"pasta {pedida!r} inexistente na conta {self.cfg.usuario}")
            nome, id_pasta = mapa[pedida.casefold()]
            yield from self._mensagens_pasta(nome, id_pasta)

    def _mensagens_pasta(self, pasta: str, id_pasta: str) -> Iterator[MensagemBruta]:
        estado = self._estado(pasta)
        params = {"$top": "100", "$select": "id", "$orderby": "sentDateTime"}
        filtro = filtro_servidor(self.filtros_da_pasta(pasta))
        if filtro:
            params["$filter"] = filtro
        url = f"{GRAPH}/me/mailFolders/{id_pasta}/messages"
        for item in self._paginas(url, params):
            if item["id"] in estado["_set"]:
                continue
            bruto = self._get(f"{GRAPH}/me/messages/{item['id']}/$value").content
            origem = Origem(tipo="graph", conta=self.cfg.usuario, pasta=pasta, graph_id=item["id"])
            yield MensagemBruta(bruto, origem)
        self.salvar_estado()

    def fechar(self) -> None:
        self.salvar_estado()
        self.http.close()
