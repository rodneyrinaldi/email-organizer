import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from email_organizer.repositorio import Repositorio, ler_metadata
from email_organizer.web import criar_app
from test_processar import ClienteFalso, LeitorFalso

CONFIG = {
    "contas_proprias": ["voce@gmail.com"],
    "categorias": ["Contratos", "Outros"],
    "pst": [
        {"ativo": True, "nome": "pst", "caminho": "/x.pst", "pastas": ["Inbox"]},
        {"ativo": False, "nome": "velho", "caminho": "/y.pst"},
    ],
    "graph": [{"nome": "Hotmail", "usuario": "eu@hotmail.com", "client_id": "x"}],
}


@pytest.fixture
def raiz(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("EMAIL_ORGANIZER_ESTADO", raising=False)
    (tmp_path / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    return tmp_path


def _cliente(raiz: Path, leitor=None, ia=None, auth=None) -> TestClient:
    leitor = leitor or LeitorFalso({"Inbox": ["simples.eml", "enviada.eml"], "Outra": []})
    kw = {"abrir": lambda *_: leitor, "cliente_ia": ia or ClienteFalso()}
    if auth:
        kw["autenticacao"] = auth
    return TestClient(criar_app(raiz, **kw))


def _esperar(c: TestClient, html: str) -> str:
    id_job = html.split("/jobs/")[1].split("/")[0]
    for _ in range(1500):  # até ~30 s: no container os PDFs são gerados de verdade
        r = c.get(f"/jobs/{id_job}/status").text
        if "every 2s" not in r:
            return r
        time.sleep(0.02)
    raise AssertionError("job não terminou")


def test_inicio_lista_fontes(raiz):
    r = _cliente(raiz).get("/")
    assert r.status_code == 200
    assert "pst" in r.text and "velho" in r.text and "(inativa)" in r.text
    assert "/conectar/Hotmail" in r.text


def test_config_ausente_mostra_erro(tmp_path):
    r = TestClient(criar_app(tmp_path)).get("/")
    assert r.status_code == 200 and "config.json inválido ou ausente" in r.text


def test_pastas_sob_demanda(raiz):
    r = _cliente(raiz).get("/fontes/pst/pastas")
    assert 'value="pst::Inbox" checked' in r.text and 'value="pst::Outra" >' in r.text


def test_processar_job_ate_o_fim(raiz):
    c = _cliente(raiz)
    r = c.post("/processar", data={"fontes": ["pst"], "classificacao": "true"})
    fim = _esperar(c, r.text)
    assert "concluído" in fim and "Classificação: ok 2" in fim
    dirs = list(Repositorio(raiz).mensagens())
    assert len(dirs) == 2 and ler_metadata(dirs[0]).classificacao.categoria == "Contratos"
    assert "Últimos jobs" in c.get("/").text


def test_pastas_da_tela_substituem_config(raiz):
    leitor = LeitorFalso({"Inbox": ["simples.eml"], "Outra": ["enviada.eml"]})
    c = _cliente(raiz, leitor)
    r = c.post("/processar", data={"fontes": ["pst"], "pastas": ["pst::Outra"]})
    fim = _esperar(c, r.text)
    assert "pst / Outra" in fim and "pst / Inbox" not in fim


def test_um_job_por_vez_e_cancelar(raiz):
    liberar = threading.Event()

    class Lento(LeitorFalso):
        def mensagens(self, pastas):
            for m in super().mensagens(pastas):
                liberar.wait(5)
                yield m

    c = _cliente(raiz, Lento({"Inbox": ["simples.eml", "enviada.eml", "com_anexo.eml"]}))
    r1 = c.post("/processar", data={"fontes": ["pst"]})
    r2 = c.post("/processar", data={"fontes": ["pst"]})
    assert "já existe um job em andamento" in r2.text
    id_job = r1.text.split("/jobs/")[1].split("/")[0]
    assert "cancelando" in c.post(f"/jobs/{id_job}/cancelar").text
    liberar.set()
    fim = _esperar(c, r1.text)
    assert "cancelado" in fim


def test_reprocessar_classificacao(raiz):
    c = _cliente(raiz)
    _esperar(c, c.post("/processar", data={"fontes": ["pst"]}).text)  # sem IA
    ia = ClienteFalso()
    c2 = _cliente(raiz, ia=ia)
    fim = _esperar(c2, c2.post("/reprocessar", data={"etapas": ["classificacao"]}).text)
    assert ia.chamadas == 2 and "Classificação: ok 2" in fim
    assert "selecione ao menos uma etapa" in c2.post("/reprocessar", data={}).text


def test_reconstruir_indice(raiz):
    c = _cliente(raiz)
    _esperar(c, c.post("/processar", data={"fontes": ["pst"]}).text)
    assert "2 mensagens" in c.post("/reconstruir-indice").text


def test_conectar_graph(raiz):
    liberar = threading.Event()

    class AuthFalsa:
        def __init__(self, *_):
            pass

        def iniciar(self):
            return {
                "message": "...",
                "user_code": "ABC123",
                "verification_uri": "https://microsoft.com/link",
                "expires_in": 900,
            }

        def conectar(self, mostrar, fluxo):
            liberar.wait(5)
            return "eu@hotmail.com"

    c = _cliente(raiz, auth=AuthFalsa)
    assert "ABC123" in c.post("/conectar/Hotmail").text
    liberar.set()
    for _ in range(100):
        r = c.get("/conectar/Hotmail/status").text
        if "conectado" in r:
            break
        time.sleep(0.02)
    assert "conectado: eu@hotmail.com" in r
    assert c.post("/conectar/pst").status_code == 400


def test_conectar_codigo_expirado(raiz):
    class AuthFalsa:
        def __init__(self, *_):
            pass

        def verificar(self):
            raise PermissionError("não conectada")

        def iniciar(self):
            return {"message": "...", "user_code": "XYZ", "expires_in": 900}

        def conectar(self, mostrar, fluxo):
            raise PermissionError("autorização falhou: AADSTS70020: This device code has expired.")

    c = _cliente(raiz, auth=AuthFalsa)
    c.post("/conectar/Hotmail")
    for _ in range(100):
        r = c.get("/conectar/Hotmail/status").text
        if "expirou" in r:
            break
        time.sleep(0.02)
    assert "gerar novo código" in r


def test_toast_de_fim_uma_vez_e_atualiza_ultimos(raiz):
    c = _cliente(raiz)
    r = c.post("/processar", data={"fontes": ["pst"]})
    id_job = r.text.split("/jobs/")[1].split("/")[0]
    textos = [r.text]
    for _ in range(1500):  # até ~30 s: no container os PDFs são gerados de verdade
        textos.append(c.get(f"/jobs/{id_job}/status").text)
        if "every 2s" not in textos[-1]:
            break
        time.sleep(0.02)
    finais = [t for t in textos if "Job concluído:" in t]
    assert len(finais) == 1 and 'id="ultimos" hx-swap-oob' in finais[0]
    assert "Job concluído:" not in c.get(f"/jobs/{id_job}/status").text  # já notificado


def test_avisos_nao_trocam_o_painel(raiz):
    c = _cliente(raiz)
    r = c.post("/processar", data={})
    assert r.headers["HX-Reswap"] == "none" and "selecione ao menos uma fonte" in r.text
    r = c.post("/reconstruir-indice")
    assert r.headers["HX-Reswap"] == "none" and "bg-emerald-600" in r.text


def test_limpeza_e_lixeira(raiz):
    from conftest import CONTAS, eml
    from email_organizer.captura import capturar
    from email_organizer.modelos import Origem

    origem = Origem(tipo="imap", conta="voce@gmail.com", pasta="INBOX", uid=1)
    msg = capturar(Repositorio(raiz), eml("simples.eml"), origem, CONTAS).diretorio
    capturar(Repositorio(raiz), eml("enviada.eml"), origem, CONTAS)
    c = _cliente(raiz)

    assert "Limpeza" in c.get("/").text  # menu
    r = c.get("/limpeza").text
    assert "voce@gmail.com" in r and ".enviadas" in r and "joao@email.com" in r
    r = c.get("/limpeza/mensagens", params={"caminho": "voce@gmail.com/joao@email.com"})
    rel = msg.relative_to(raiz).as_posix()
    assert rel in r.text
    assert c.get("/limpeza/mensagens", params={"caminho": "_jobs"}).status_code == 404

    assert "nada marcado" in c.post("/limpeza/excluir").text
    r = c.post("/limpeza/excluir", data={"caminhos": [rel]}).text
    assert "1 mensagem(ns) movida(s)" in r and "joao@email.com" not in r
    assert "caminho inválido" in c.post("/limpeza/excluir", data={"caminhos": ["../x"]}).text

    r = c.get("/lixeira").text
    assert rel in r and "Esvaziar lixeira" in r
    lote = next((raiz / ".lixeira").iterdir()).name
    r = c.post("/lixeira/restaurar", data={"lote": lote, "caminho": rel}).text
    assert "restaurado" in r and "Lixeira vazia" in r and msg.exists()

    c.post("/limpeza/excluir", data={"caminhos": ["voce@gmail.com"]})
    r = c.post("/lixeira/esvaziar").text
    assert "2 mensagem(ns) apagada(s)" in r
    assert not (raiz / ".lixeira").exists() and not (raiz / "voce@gmail.com").exists()


def test_conectar_detecta_token_salvo_por_outro_codigo(raiz):
    """Autorizou um código antigo: a thread do código atual nunca termina, mas o token vale."""
    estado = {"token": False, "iniciados": 0}

    class AuthFalsa:
        def __init__(self, *_):
            pass

        def verificar(self):
            if not estado["token"]:
                raise PermissionError("não conectada")
            return "eu@hotmail.com"

        def iniciar(self):
            estado["iniciados"] += 1
            return {"message": "...", "user_code": f"COD{estado['iniciados']}", "expires_in": 900}

        def conectar(self, mostrar, fluxo):
            threading.Event().wait(5)  # nunca autorizado
            raise PermissionError("expired")

    c = _cliente(raiz, auth=AuthFalsa)
    assert "COD1" in c.post("/conectar/Hotmail").text
    assert "COD1" in c.post("/conectar/Hotmail").text  # mesmo código, sem gerar outro
    assert estado["iniciados"] == 1
    assert "aguardando" in c.get("/conectar/Hotmail/status").text
    assert "não conectada" in c.get("/conectar/Hotmail/verificar").text

    estado["token"] = True  # token gravado por outro fluxo
    r = c.get("/conectar/Hotmail/status").text
    assert "conectado: eu@hotmail.com" in r and "every 3s" not in r
    assert "conectado: eu@hotmail.com" in c.get("/conectar/Hotmail/verificar").text
    # já conectado: conectar não gera código; reconectar (forcar) gera
    assert "conectado: eu@hotmail.com" in c.post("/conectar/Hotmail").text
    r = c.post("/conectar/Hotmail?forcar=true").text
    assert "COD2" in r and "aguardando" in c.get("/conectar/Hotmail/status").text


def test_reler_tudo(raiz):
    from email_organizer.repositorio import gravar_json

    c = _cliente(raiz)
    assert "nada a zerar" in c.post("/fontes/Hotmail/reler").text
    gravar_json(raiz / ".estado" / "graph" / "eu-hotmail-com" / "inbox.json", {"capturados": []})
    r = c.post("/fontes/Hotmail/reler")
    assert r.headers["HX-Reswap"] == "none" and "controle zerado" in r.text
    assert not (raiz / ".estado" / "graph" / "eu-hotmail-com").exists()
    assert "reler tudo" in c.get("/").text and "voltam na próxima captura" in c.get("/limpeza").text
