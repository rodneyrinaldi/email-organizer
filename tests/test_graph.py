from datetime import date
from pathlib import Path

import httpx

from conftest import eml
from email_organizer.config import FonteGraph
from email_organizer.filtro import Filtro
from email_organizer.fontes.graph import GRAPH, LeitorGraph, filtro_servidor


def _servidor(chamadas: list[httpx.Request], falhar_uma_vez: bool = False):
    estado = {"falhou": False}
    pastas = {
        "/v1.0/me/mailFolders": [
            {"id": "inbox", "displayName": "Caixa de Entrada", "childFolderCount": 1},
            {"id": "sent", "displayName": "Itens Enviados", "childFolderCount": 0},
        ],
        "/v1.0/me/mailFolders/inbox/childFolders": [
            {"id": "ang", "displayName": "Mariana", "childFolderCount": 0},
        ],
    }
    brutos = {"m1": eml("simples.eml"), "m2": eml("enviada.eml"), "m3": eml("com_anexo.eml")}

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        assert req.method == "GET"
        assert req.headers["Authorization"] == "Bearer tok"
        if falhar_uma_vez and not estado["falhou"]:
            estado["falhou"] = True
            return httpx.Response(429, headers={"Retry-After": "0"})
        caminho = req.url.path
        if caminho in pastas:
            return httpx.Response(200, json={"value": pastas[caminho]})
        if caminho == "/v1.0/me/mailFolders/ang/messages":
            if "skiptoken" in str(req.url):
                return httpx.Response(200, json={"value": [{"id": "m3"}]})
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "m1"}, {"id": "m2"}],
                    "@odata.nextLink": f"{GRAPH}/me/mailFolders/ang/messages?skiptoken=x",
                },
            )
        if caminho.endswith("/$value"):
            return httpx.Response(200, content=brutos[caminho.split("/")[-2]])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _leitor(tmp_path: Path, chamadas: list, **kw) -> LeitorGraph:
    cfg = FonteGraph(nome="hot", usuario="eu@hotmail.com", client_id="x")
    return LeitorGraph(cfg, tmp_path, obter_token=lambda: "tok", cliente=_servidor(chamadas, **kw))


def test_pastas_aninhadas(tmp_path: Path):
    leitor = _leitor(tmp_path, [])
    assert leitor.listar_pastas() == [
        "Caixa de Entrada",
        "Caixa de Entrada/Mariana",
        "Itens Enviados",
    ]


def test_mensagens_paginadas_e_incremental(tmp_path: Path):
    chamadas: list[httpx.Request] = []
    leitor = _leitor(tmp_path, chamadas)
    msgs = list(leitor.mensagens(["caixa de entrada/mariana"]))
    assert [m.origem.graph_id for m in msgs] == ["m1", "m2", "m3"]
    assert msgs[0].bruto == eml("simples.eml")
    assert (msgs[0].origem.tipo, msgs[0].origem.pasta) == ("graph", "Caixa de Entrada/Mariana")
    leitor.registrar(msgs[0].origem)
    leitor.fechar()

    leitor2 = _leitor(tmp_path, [])
    assert [m.origem.graph_id for m in leitor2.mensagens(["Caixa de Entrada/Mariana"])] == [
        "m2",
        "m3",
    ]


def test_retry_em_429(tmp_path: Path):
    leitor = _leitor(tmp_path, [], falhar_uma_vez=True)
    assert "Itens Enviados" in leitor.listar_pastas()


def test_filtro_servidor():
    assert filtro_servidor([Filtro(assunto=["x"])]) is None
    assert filtro_servidor([Filtro(data_de=date(2025, 3, 1))]) == (
        "sentDateTime ge 2025-02-28T00:00:00Z"
    )
