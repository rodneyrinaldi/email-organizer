import json
import threading
import time

import pytest

from conftest import CONTAS, eml
from email_organizer import resumo
from email_organizer.captura import capturar
from email_organizer.ia import Indisponivel
from email_organizer.modelos import Origem
from email_organizer.repositorio import ler_json


class OllamaFalso:
    modelo = "falso"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.contextos: list[int | None] = []

    def json(self, sistema, usuario, esquema, contexto=None):
        self.prompts.append(usuario)
        self.contextos.append(contexto)
        campos = set(esquema["properties"])
        if campos == {"notas"}:
            return json.dumps({"notas": f"notas {len(self.prompts)}"})
        if "pedidos_prazos" in campos:
            return json.dumps(
                {
                    "resumo": "Pede o contrato assinado.",
                    "temperatura": "Tensa",
                    "assuntos": ["contrato"],
                    "pedidos_prazos": ["enviar até sexta"],
                }
            )
        return json.dumps(
            {
                "situacao": "em andamento",
                "panorama": "Negociação de contrato.",
                "conteudo": "Troca de minutas.",
                "temperatura": "neutra",
                "evolucao_tom": "Estável.",
                "pontos_atencao": ["prazo"],
                "proximos_passos": [],
            }
        )


@pytest.fixture
def acervo(repo, origem_imap):
    a = capturar(repo, eml("simples.eml"), origem_imap, CONTAS).diretorio
    b = capturar(repo, eml("enviada.eml"), origem_imap, CONTAS).diretorio
    return repo.raiz, a, b


@pytest.fixture
def sem_pdf(monkeypatch):
    """WeasyPrint não roda no Windows: grava o HTML no lugar do PDF."""
    import email_organizer.artefatos.conversao as conv

    monkeypatch.setattr(conv, "html_para_pdf", lambda html, destino: destino.write_text(html))


def test_sem_citacao():
    texto = "Segue a minuta.\n\nEm 10/01/2026, Fulano escreveu:\n> texto antigo\nmais antigo"
    assert resumo.sem_citacao(texto) == "Segue a minuta."
    assert resumo.sem_citacao("ok\n> citado\nfim") == "ok\nfim"
    enc = "Veja.\n---------- Forwarded message ---------\nDe: X\nConteúdo encaminhado"
    assert "Conteúdo encaminhado" in resumo.sem_citacao(enc)  # pouco texto próprio: mantém
    longo = "a" * 300 + "\nDe: X <x@y>\nEnviado: hoje\nhistórico"
    assert "histórico" not in resumo.sem_citacao(longo)


def test_partes_e_blocos():
    texto = "\n\n".join(["p" * 900] * 10)
    ps = resumo.partes(texto, 2000)
    assert all(len(p) <= 2000 for p in ps) and "".join(ps).count("p") == 9000
    bs = resumo.blocos(["x" * 400] * 10, 1000)
    assert [len(b) for b in bs] == [2, 2, 2, 2, 2]


def test_resumo_por_mensagem_fica_em_cache(acervo):
    _, a, _ = acervo
    ia = OllamaFalso()
    r = resumo.resumir_mensagem(a, ia)
    assert r["temperatura"] == "tensa" and r["modelo"] == "falso"
    dados = ler_json(a / "metadata.json")
    assert dados["resumo"]["resumo"] == "Pede o contrato assinado."
    assert dados["pipeline"]["resumo"]["status"] == "ok"
    assert dados["pipeline"]["captura"]["status"] == "ok"  # chaves alheias intactas
    assert ia.contextos == [resumo.CONTEXTO]
    assert resumo.resumir_mensagem(a, ia)["reaproveitado"] and len(ia.prompts) == 1
    resumo.resumir_mensagem(a, ia, refazer=True)
    assert len(ia.prompts) == 2


def test_corpo_longo_em_partes():
    ia = OllamaFalso()
    texto = "\n\n".join(["parágrafo " * 100] * 20)  # ~20 mil chars
    resumo.resumir_texto(ia, {"assunto": "x", "direcao": "recebida"}, texto, [])
    assert len(ia.prompts) > 2 and "notas das partes" in ia.prompts[-1]
    assert all(len(p) < resumo.MAX_CORPO + 1500 for p in ia.prompts)


def test_consolidacao_hierarquica(monkeypatch):
    monkeypatch.setattr(resumo, "MAX_BLOCO", 1000)
    ia = OllamaFalso()
    c = resumo.consolidar(ia, [f"[{i}] " + "r" * 300 for i in range(20)])
    assert c["situacao"] == "em andamento"
    assert any("Bloco 1 de" in p for p in ia.prompts)
    assert all(len(p) < 2500 for p in ia.prompts)


def test_gerar_documento(acervo, sem_pdf, tmp_path):
    raiz, a, b = acervo
    destino = tmp_path / "r.pdf"
    prog = resumo.Progresso()
    resumo.gerar(raiz, ["voce@gmail.com"], OllamaFalso(), destino, progresso=prog)
    html = destino.read_text()
    assert prog.total == prog.feitos == 2 and prog.fase == "fim"
    assert "Pede o contrato assinado." in html and "Considerações" in html
    assert "Negociação de contrato." in html and a.relative_to(raiz).as_posix() in html
    with pytest.raises(ValueError):
        resumo.gerar(raiz, ["../fora"], OllamaFalso(), destino)


def test_gerar_pst_e_falha_isolada(repo, sem_pdf, tmp_path):
    pst = Origem(tipo="pst", arquivo="Arq.pst", pasta="Clientes/Fulano")
    capturar(repo, eml("simples.eml"), pst, CONTAS)
    capturar(repo, eml("enviada.eml"), pst, CONTAS)

    class Instavel(OllamaFalso):
        def json(self, sistema, usuario, esquema, contexto=None):
            if "pedidos_prazos" in esquema["properties"] and not self.prompts:
                self.prompts.append(usuario)
                return "não é json"  # 1ª mensagem: 2 respostas inválidas
            return super().json(sistema, usuario, esquema, contexto)

    ia = Instavel()
    ia.prompts.clear()
    prog = resumo.Progresso()
    destino = tmp_path / "r.pdf"
    resumo.gerar(repo.raiz, ["Arq/Clientes"], ia, destino, progresso=prog)
    assert prog.total == 2


def test_ollama_fora_aborta(acervo, sem_pdf, tmp_path):
    raiz, *_ = acervo

    class Fora(OllamaFalso):
        def json(self, *a, **k):
            raise Indisponivel("fora")

    with pytest.raises(Indisponivel):
        resumo.gerar(raiz, ["voce@gmail.com"], Fora(), tmp_path / "r.pdf")


def test_cancelar(acervo, sem_pdf, tmp_path):
    raiz, *_ = acervo
    ev = threading.Event()
    ev.set()
    with pytest.raises(resumo.Cancelado):
        resumo.gerar(raiz, ["voce@gmail.com"], OllamaFalso(), tmp_path / "r.pdf", cancelar=ev)


def test_web_resumo(acervo, sem_pdf, monkeypatch):
    from fastapi.testclient import TestClient

    from email_organizer.web import criar_app

    raiz, *_ = acervo
    (raiz / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("EMAIL_ORGANIZER_ESTADO", str(raiz.parent / "estado"))
    c = TestClient(criar_app(raiz, cliente_ia=OllamaFalso()))
    assert "Resumo" in c.get("/").text
    pagina = c.get("/resumo").text
    assert "Gerar resumo" in pagina and "voce@gmail.com" in pagina
    assert "nada marcado" in c.post("/resumo").text
    r = c.post("/resumo", data={"caminhos": ["voce@gmail.com"]}).text
    id_ = r.split("/resumo/")[1].split("/")[0]
    for _ in range(300):
        r = c.get(f"/resumo/{id_}/status").text
        if "every 2s" not in r:
            break
        time.sleep(0.02)
    assert "Resumo pronto" in r and f"/resumo/{id_}/pdf" in r
    pdf = c.get(f"/resumo/{id_}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    baixar = c.get(f"/resumo/{id_}/pdf", params={"baixar": 1}).headers["content-disposition"]
    assert baixar.startswith("attachment")


def test_pdf_real(acervo, tmp_path):
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint sem Pango: rodar no container dev")
    import pymupdf

    raiz, *_ = acervo
    destino = tmp_path / "r.pdf"
    resumo.gerar(raiz, ["voce@gmail.com"], OllamaFalso(), destino)
    texto = "".join(p.get_text() for p in pymupdf.open(destino))
    assert "Resumo por mensagem" in texto and "Considerações" in texto
