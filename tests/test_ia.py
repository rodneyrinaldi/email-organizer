import json
from pathlib import Path

import httpx
import pytest

from conftest import CONTAS, eml
from email_organizer.captura import capturar
from email_organizer.config import Config
from email_organizer.ia import ClienteOllama, classificar, classificar_lote, precisa_classificar
from email_organizer.repositorio import Repositorio, gravar_metadata, ler_metadata

CONFIG = Config.model_validate({"categorias": ["Banco", "Trabalhista", "Outros"]})


def _cliente(respostas: list, chamadas: list | None = None) -> ClienteOllama:
    """respostas: dict (JSON devolvido pelo modelo), str (conteúdo cru) ou Exception."""
    fila = list(respostas)

    def handler(req: httpx.Request) -> httpx.Response:
        if chamadas is not None:
            chamadas.append(json.loads(req.content))
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        conteudo = r if isinstance(r, str) else json.dumps(r)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": conteudo}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ClienteOllama("http://ollama:11434", "qwen2.5:7b", 5, http)


def _msg(repo: Repositorio, origem, nome: str) -> Path:
    with repo:
        return capturar(repo, eml(nome), origem, CONTAS).diretorio


def test_classifica_e_grava_no_json(repo, origem_imap):
    d = _msg(repo, origem_imap, "com_anexo.eml")
    meta = ler_metadata(d)
    meta.minha_rotina = {"x": 1}  # chave de outra rotina deve sobreviver
    gravar_metadata(d, meta)
    chamadas: list = []
    cliente = _cliente(
        [
            {
                "categoria": "trabalhista",
                "subcategoria": "Reclamação",
                "confianca": 0.9,
                "tipo": "nova",
            }
        ],
        chamadas,
    )

    meta = classificar(d, CONFIG, cliente)

    assert meta.classificacao.categoria == "Trabalhista"
    assert meta.classificacao.subcategoria == "Reclamação"
    assert meta.classificacao.confianca == 0.9
    assert meta.classificacao.modelo == "qwen2.5:7b"
    assert meta.pipeline["classificacao"].status == "ok"
    salvo = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert (
        salvo["minha_rotina"] == {"x": 1} and salvo["classificacao"]["categoria"] == "Trabalhista"
    )
    pedido = chamadas[0]
    assert pedido["model"] == "qwen2.5:7b" and pedido["stream"] is False
    assert isinstance(pedido["format"], dict)
    assert "Documentos do processo trabalhista" in pedido["messages"][-1]["content"]


def test_categoria_fora_da_lista_vira_outros_e_confianca_percentual(repo, origem_imap):
    d = _msg(repo, origem_imap, "com_anexo.eml")
    cliente = _cliente([{"categoria": "Astrologia", "subcategoria": "", "confianca": 85}])
    meta = classificar(d, CONFIG, cliente)
    assert meta.classificacao.categoria == "Outros"
    assert meta.classificacao.confianca == 0.85


def test_json_invalido_tenta_de_novo(repo, origem_imap):
    d = _msg(repo, origem_imap, "com_anexo.eml")
    cliente = _cliente(["isto não é json", {"categoria": "Banco", "confianca": 0.5}])
    assert classificar(d, CONFIG, cliente).classificacao.categoria == "Banco"


def test_json_invalido_duas_vezes_vira_erro(repo, origem_imap):
    d = _msg(repo, origem_imap, "com_anexo.eml")
    meta = classificar(d, CONFIG, _cliente(["{", '{"confianca": "alta"}']))
    assert meta.pipeline["classificacao"].status == "erro"
    assert meta.classificacao is None


def test_ollama_indisponivel_fica_pendente(repo, origem_imap):
    d = _msg(repo, origem_imap, "com_anexo.eml")
    meta = classificar(d, CONFIG, _cliente([httpx.ConnectError("recusada")]))
    assert meta.pipeline["classificacao"].status == "pendente"
    assert meta.classificacao is None
    assert precisa_classificar(d)


def test_thread_fallback_ia_so_sem_cabecalho_nem_prefixo(repo, origem_imap):
    sem_pista = _msg(repo, origem_imap, "com_anexo.eml")
    com_cabecalho = _msg(repo, origem_imap, "simples.eml")
    resp = {"categoria": "Banco", "confianca": 0.7, "tipo": "resposta"}

    meta = classificar(sem_pista, CONFIG, _cliente([resp]))
    assert (meta.thread.tipo, meta.thread.metodo) == ("resposta", "ia")

    antes = ler_metadata(com_cabecalho).thread
    assert classificar(com_cabecalho, CONFIG, _cliente([resp])).thread == antes


def test_lote_para_de_chamar_quando_ollama_cai(repo, origem_imap):
    dirs = [_msg(repo, origem_imap, n) for n in ("com_anexo.eml", "enviada.eml", "simples.eml")]
    chamadas: list = []
    cliente = _cliente([httpx.ConnectError("recusada")], chamadas)

    contagem = classificar_lote(dirs, CONFIG, cliente)

    assert len(chamadas) == 1
    assert contagem == {"ok": 0, "pendente": 3, "erro": 0}
    assert all(ler_metadata(d).pipeline["classificacao"].status == "pendente" for d in dirs)


@pytest.mark.parametrize("status,precisa", [("ok", False), ("erro", True), ("pendente", True)])
def test_precisa_classificar(repo, origem_imap, status, precisa):
    d = _msg(repo, origem_imap, "com_anexo.eml")
    assert precisa_classificar(d)
    meta = ler_metadata(d)
    meta.pipeline["classificacao"] = meta.pipeline["captura"].model_copy(update={"status": status})
    gravar_metadata(d, meta)
    assert precisa_classificar(d) is precisa
