import time

import pytest

from email_organizer.artefatos.conversao import ErroConversao, limite_tempo


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGALRM"), reason="só Unix")
def test_limite_tempo_interrompe():
    with pytest.raises(ErroConversao, match="timeout"), limite_tempo(1, "teste"):
        time.sleep(3)


def test_limite_tempo_nao_interfere():
    with limite_tempo(5, "teste"):
        pass


def _png(largura: int, altura: int) -> bytes:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, largura, altura), False)
    pix.clear_with(200)
    return pix.tobytes("png")


@pytest.mark.parametrize(
    "largura,altura,pagina", [(40, 20, (842, 595)), (20, 40, (595, 842)), (3000, 4000, (595, 842))]
)
def test_imagem_maximizada_em_a4(tmp_path, largura, altura, pagina):
    import pymupdf

    from email_organizer.artefatos.conversao import MARGEM_PT, imagem_para_pdf

    destino = tmp_path / "x.pdf"
    imagem_para_pdf(_png(largura, altura), destino)
    with pymupdf.open(destino) as doc:
        assert len(doc) == 1
        p = doc[0]
        assert (round(p.rect.width), round(p.rect.height)) == pagina
        caixa = pymupdf.Rect(p.get_image_info()[0]["bbox"])
        util = p.rect + (MARGEM_PT, MARGEM_PT, -MARGEM_PT, -MARGEM_PT)
        # ocupa toda a largura ou toda a altura útil, mantendo a proporção
        assert max(caixa.width / util.width, caixa.height / util.height) > 0.99
        assert abs(caixa.width / caixa.height - largura / altura) < 0.01


def test_imagem_invalida(tmp_path):
    from email_organizer.artefatos.conversao import imagem_para_pdf

    with pytest.raises(ErroConversao):
        imagem_para_pdf(b"nao e imagem", tmp_path / "x.pdf")
