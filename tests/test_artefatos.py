"""Testes da etapa de artefatos. Requerem WeasyPrint/LibreOffice: rodar no container dev
(`docker compose --profile dev run --rm dev`)."""

import shutil
from email.message import EmailMessage

import pytest

try:
    import weasyprint  # noqa: F401
except (ImportError, OSError):  # Windows sem GTK/Pango
    pytest.skip("WeasyPrint indisponível: rodar no container dev", allow_module_level=True)
import pymupdf  # noqa: E402

from conftest import CONTAS  # noqa: E402
from email_organizer.artefatos import gerar_artefatos  # noqa: E402
from email_organizer.artefatos.markdown import para_markdown  # noqa: E402
from email_organizer.artefatos.pdf_principal import preparar_html  # noqa: E402
from email_organizer.captura import capturar  # noqa: E402
from email_organizer.config import Opcoes  # noqa: E402
from email_organizer.modelos import Origem  # noqa: E402
from email_organizer.repositorio import Repositorio, ler_metadata, sha256_arquivo  # noqa: E402

TEM_LIBREOFFICE = shutil.which("soffice") is not None


def png(cor: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 10), False)
    pix.set_rect(pix.irect, cor)
    return pix.tobytes("png")


def pdf_simples(texto: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), texto)
    return doc.tobytes()


def mensagem_rica() -> bytes:
    interna = EmailMessage()
    interna["Message-ID"] = "<interna@x.com>"
    interna["From"] = "Banco <banco@banco.com>"
    interna["To"] = "joao@email.com"
    interna["Subject"] = "Proposta original"
    interna["Date"] = "Mon, 01 Sep 2025 08:00:00 -0300"
    interna.set_content("Texto da proposta interna.")

    m = EmailMessage()
    m["Message-ID"] = "<rica@x.com>"
    m["From"] = "João <joao@email.com>"
    m["To"] = "voce@gmail.com"
    m["Subject"] = "Documentos"
    m["Date"] = "Wed, 15 Oct 2025 10:00:00 -0300"
    m.set_content("versão texto")
    m.add_alternative(
        "<html><head><style>p{color:#333}</style></head><body>"
        "<p>Olá <b>Fulano</b>, segue:</p>"
        '<img src="cid:logo2@x"><p>meio</p><img src="cid:logo1@x">'
        '<img src="https://exemplo.com/remota.png"><img src="https://falha.com/x.png">'
        "<script>alert(1)</script></body></html>",
        subtype="html",
    )
    html = m.get_payload()[1]
    html.add_related(png((0, 255, 0)), "image", "png", cid="<logo1@x>")
    html.add_related(png((0, 0, 255)), "image", "png", cid="<logo2@x>")
    m.add_attachment(pdf_simples("contrato"), "application", "pdf", filename="contrato.pdf")
    m.add_attachment(png(), "image", "png", filename="foto.png")
    m.add_attachment(b"col1;col2\n1;2\n", "text", "csv", filename="planilha.csv")
    m.add_attachment(b"PK\x03\x04", "application", "zip", filename="docs.zip")
    m.add_attachment(b"\x00\x01", "application", "octet-stream", filename="dados.bin")
    m.add_attachment(png(), "image", "png", filename="foto.png")  # nome repetido
    m.add_attachment(b"<p>anexo html</p>", "text", "html", filename="pagina.html")
    m.add_attachment(interna)
    m.add_attachment(b"x", "application", "octet-stream", filename='a<b>:"c?.doc')
    return m.as_bytes()


def baixador_falso(url: str) -> tuple[bytes, str]:
    if "falha" in url:
        raise ValueError("404")
    return png(), "image/png"


@pytest.fixture
def gerada(repo):
    r = capturar(repo, mensagem_rica(), Origem(tipo="eml"), CONTAS)
    meta = gerar_artefatos(r.diretorio, Opcoes(), CONTAS, baixador_falso)
    return r.diretorio, meta


def test_pdf_principal(gerada):
    d, meta = gerada
    assert meta.pipeline["artefatos"].status == "ok", meta.avisos
    assert meta.pdf_principal.sha256 == sha256_arquivo(d / "mensagem.pdf")
    with pymupdf.open(d / "mensagem.pdf") as pdf:
        texto = "".join(p.get_text() for p in pdf).replace("\n", "")
        imagens = sum(len(p.get_images()) for p in pdf)
    for esperado in ("João", "Documentos", "<rica@x.com>", "Olá", "contrato.pdf", "planilha.csv"):
        assert esperado in texto
    assert "alert(1)" not in texto
    assert imagens >= 3  # 2 inline + 1 remota embutida


def test_imagens_remotas(gerada):
    _, meta = gerada
    status = {i.url: i.status for i in meta.imagens_remotas}
    assert status == {
        "https://exemplo.com/remota.png": "embutida",
        "https://falha.com/x.png": "falhou",
    }


def test_corpo_na_ordem_do_html(gerada):
    d, meta = gerada
    assert [c.content_id for c in meta.corpo] == ["logo2@x", "logo1@x"]
    assert [c.arquivo for c in meta.corpo] == ["corpo/imagem-001.pdf", "corpo/imagem-002.pdf"]
    assert all((d / c.arquivo).exists() for c in meta.corpo)


def test_anexos(gerada):
    d, meta = gerada
    por_nome = {}
    for a in meta.anexos:
        por_nome.setdefault(a.nome_original, []).append(a)

    contrato = por_nome["contrato.pdf"][0]
    assert contrato.status == "original_pdf"
    assert contrato.arquivo_original.arquivo == "anexos/originais/contrato.pdf"
    assert contrato.arquivo_pdf.arquivo == "anexos/contrato.pdf"

    fotos = por_nome["foto.png"]
    assert [f.arquivo_pdf.arquivo for f in fotos] == ["anexos/foto.pdf", "anexos/foto-2.pdf"]
    assert [f.arquivo_original.arquivo for f in fotos] == [
        "anexos/originais/foto.png",
        "anexos/originais/foto-2.png",
    ]

    assert por_nome["docs.zip"][0].status == "ignorado"
    assert por_nome["dados.bin"][0].status == "nao_convertido"
    assert por_nome["pagina.html"][0].status == "convertido"
    assert por_nome['a<b>:"c?.doc'][0].arquivo_original.arquivo == "anexos/originais/a_b___c_.doc"
    if TEM_LIBREOFFICE:
        assert por_nome["planilha.csv"][0].status == "convertido"

    for a in meta.anexos:
        if a.arquivo_original:
            assert sha256_arquivo(d / a.arquivo_original.arquivo) == a.arquivo_original.sha256
        if a.arquivo_pdf:
            assert (d / a.arquivo_pdf.arquivo).read_bytes().startswith(b"%PDF")


def test_email_anexado_recursivo(gerada):
    d, meta = gerada
    email = next(a for a in meta.anexos if a.tipo == "email")
    sub = d / email.diretorio
    assert email.diretorio.startswith("anexos/emails/")
    assert email.nome_original == "Proposta original.eml"
    filho = ler_metadata(sub)
    assert filho.message_id == "<interna@x.com>"
    assert filho.pai.id == meta.id
    assert (sub / filho.pai.diretorio / "metadata.json").resolve() == (
        d / "metadata.json"
    ).resolve()
    assert filho.pdf_principal is not None
    assert email.arquivo_pdf.arquivo == f"{email.diretorio}/mensagem.pdf"
    assert "proposta interna" in (sub / "mensagem-texto.md").read_text(encoding="utf-8")


def test_markdown_somente_texto(gerada):
    d, _ = gerada
    md = (d / "mensagem-texto.md").read_text(encoding="utf-8")
    assert "Olá **Fulano**" in md
    assert "Documentos" not in md  # sem cabeçalhos
    assert "cid:" not in md and "color:#333" not in md and "contrato" not in md


def test_idempotente(gerada, repo):
    d, meta = gerada
    original = (d / "original" / "mensagem.eml").read_bytes()
    antes = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
    meta2 = gerar_artefatos(d, Opcoes(), CONTAS, baixador_falso)
    depois = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
    assert antes == depois
    assert len(meta2.anexos) == len(meta.anexos)
    assert len(list((d / "anexos" / "emails").iterdir())) == 1
    assert (d / "original" / "mensagem.eml").read_bytes() == original
    assert meta2.pipeline["captura"].status == "ok"


def test_opcoes_desligadas(repo):
    r = capturar(repo, mensagem_rica(), Origem(tipo="eml"), CONTAS)
    meta = gerar_artefatos(
        r.diretorio, Opcoes(pdf_principal=False, markdown=False), CONTAS, baixador_falso
    )
    assert meta.pdf_principal is None and meta.texto_markdown is None
    assert not (r.diretorio / "mensagem.pdf").exists()
    assert meta.anexos  # anexos sempre


def test_texto_puro(repo, origem_imap):
    from conftest import eml

    r = capturar(repo, eml("simples.eml"), origem_imap, CONTAS)
    meta = gerar_artefatos(r.diretorio, Opcoes(), CONTAS, baixador_falso)
    assert meta.pipeline["artefatos"].status == "ok"
    assert (r.diretorio / "mensagem-texto.md").read_text(encoding="utf-8").strip() == (
        "Segue proposta de financiamento."
    )


def test_preparar_html_sem_download():
    html, remotas = preparar_html('<img src="http://a/b.png">', [], False, baixador_falso)
    assert 'src=""' in html and remotas[0].status == "nao_baixada"


def test_data_br():
    from email_organizer.artefatos.pdf_principal import data_br

    assert data_br("2025-10-15T10:00:00-03:00") == "15/10/2025 10:00:00 (UTC-03:00)"
    assert data_br(None) == "(sem data)"


def test_markdown_texto():
    assert para_markdown(None, "linha\r\n\r\n\r\n\r\nfim") == "linha\n\nfim\n"


@pytest.mark.parametrize("trabalhadores", [1, 2])
def test_processar_gera_artefatos_em_paralelo(tmp_path, trabalhadores):
    from email_organizer.config import Config
    from email_organizer.processar import processar
    from test_processar import LeitorFalso

    config = Config.model_validate(
        {
            "pst": [{"ativo": True, "nome": "p", "caminho": "/x.pst"}],
            "opcoes": {"trabalhadores": trabalhadores, "baixar_imagens_remotas": False},
        }
    )
    leitor = LeitorFalso({"Inbox": ["simples.eml", "com_anexo.eml", "enviada.eml"]})
    rel = processar(config, tmp_path, abrir=lambda *_: leitor)
    assert rel.total.novas == 3 and rel.total.erros == 0, rel.erros
    for d in Repositorio(tmp_path).mensagens():
        meta = ler_metadata(d)
        assert meta.pipeline["artefatos"].status == "ok"
        assert (d / "mensagem.pdf").exists()


def test_imagem_colada_recebe_classe_de_maximizacao():
    from email_organizer.artefatos.mime import Parte

    parte = Parte("a.png", "image/png", b"\x89PNG", "img1@x")
    html, _ = preparar_html(
        '<p><img width="30" style="width:30px" src="cid:img1@x">'
        '<img src="data:image/gif;base64,R0">',
        [parte],
        False,
        baixador_falso,
    )
    coladas = html.count('class="eo-colada"')
    assert coladas == 1 and html.index('class="eo-colada"') < html.index("data:image/png")


def _extremos() -> bytes:
    from construtores import pdf_cifrado, tnef

    m = EmailMessage()
    m["Message-ID"] = "<extremos@x.com>"
    m["From"], m["To"], m["Subject"] = "a@x.com", "b@x.com", "extremos"
    m["Date"] = "Mon, 01 Sep 2025 08:00:00 -0300"
    m.set_content("corpo")
    m.add_attachment(
        tnef({"dentro.pdf": pdf_simples("interno")}), maintype="application",
        subtype="ms-tnef", filename="winmail.dat",
    )  # fmt: skip
    m.add_attachment(pdf_cifrado(), maintype="application", subtype="pdf", filename="sig.pdf")
    m.add_attachment(b"\xd0\xcf\x11\xe0", maintype="application", subtype="vnd.ms-outlook",
                     filename="antigo.msg")  # fmt: skip
    return m.as_bytes()


def test_casos_extremos_nos_artefatos(repo, monkeypatch):
    from email_organizer.artefatos import conversao

    interna = EmailMessage()
    interna["From"], interna["Subject"] = "c@x.com", "do outlook"
    interna.set_content("texto do msg")
    monkeypatch.setattr(conversao, "msg_para_eml", lambda dados: interna.as_bytes())
    with repo:
        r = capturar(repo, _extremos(), Origem(tipo="eml", arquivo="x.eml"), CONTAS)
    meta = gerar_artefatos(r.diretorio, Opcoes(), CONTAS, baixador_falso)

    por_nome = {a.nome_original: a for a in meta.anexos}
    assert por_nome["winmail.dat"].status == "ignorado"
    assert por_nome["dentro.pdf"].status == "original_pdf"
    assert por_nome["sig.pdf"].status == "original_pdf"
    assert por_nome["sig.pdf"].motivo == "PDF protegido por senha"
    msg = por_nome["antigo.msg"]
    assert (msg.tipo, msg.status, msg.motivo) == ("email", "convertido", "convertido de .msg")
    assert msg.arquivo_original.arquivo == "anexos/originais/antigo.msg"
    filho = ler_metadata(r.diretorio / msg.diretorio)
    assert filho.assunto == "do outlook"
    avisos = " ".join(a.mensagem for a in meta.avisos)
    assert "TNEF" in avisos and "protegido" in avisos
