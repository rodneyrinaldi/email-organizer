"""Conversões para PDF: imagens (PyMuPDF), office (LibreOffice headless), HTML (WeasyPrint)."""

import html as html_lib
import mimetypes
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

import pymupdf

EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".jfif"}
EXT_OFFICE = {
    ".doc", ".docx", ".odt", ".rtf", ".csv",
    ".xls", ".xlsx", ".xlsm", ".ods",
    ".ppt", ".pptx", ".pps", ".ppsx", ".odp",
}  # fmt: skip
EXT_HTML = {".html", ".htm"}
EXT_COMPACTADO = {".zip", ".rar", ".7z", ".gz", ".tar", ".tgz", ".bz2", ".xz"}
MIME_COMPACTADO = {"application/zip", "application/x-zip-compressed", "application/x-rar",
                   "application/x-7z-compressed", "application/gzip"}  # fmt: skip
MIME_ASSINATURA = {"application/pkcs7-signature", "application/x-pkcs7-signature"}
MIME_SMIME = {"application/pkcs7-mime", "application/x-pkcs7-mime"}

_PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
               *(f"lpt{i}" for i in range(10))}  # fmt: skip


class ErroConversao(Exception):
    pass


def nome_seguro(nome: str, maximo: int = 80) -> str:
    """Nome de arquivo válido em Windows e Linux, preservando a extensão."""
    nome = unicodedata.normalize("NFC", nome)
    nome = _PROIBIDOS.sub("_", nome).strip(" .") or "arquivo"
    p = Path(nome)
    base, ext = p.stem or "arquivo", p.suffix[:10]
    if base.lower() in _RESERVADOS:
        base = f"_{base}"
    return base[: maximo - len(ext)].rstrip(" .") + ext


def nome_unico(nome: str, usados: set[str]) -> str:
    p = Path(nome)
    candidato, i = nome, 2
    while candidato.lower() in usados:
        candidato = f"{p.stem}-{i}{p.suffix}"
        i += 1
    usados.add(candidato.lower())
    return candidato


A4_PT = (595, 842)
MARGEM_PT = 28  # ~10 mm


def imagem_para_pdf(dados: bytes, destino: Path) -> None:
    """Uma página A4 por quadro da imagem, ampliada/reduzida para ocupar a área útil
    (proporção mantida; paisagem se a imagem for mais larga que alta). Pixels originais
    preservados: só a escala de exibição muda."""
    try:
        with pymupdf.open(stream=dados) as img:
            fonte = pymupdf.open("pdf", img.convert_to_pdf())
    except Exception as e:
        raise ErroConversao(f"imagem inválida: {e}") from e
    with fonte, pymupdf.open() as saida:
        for pagina in fonte:
            largura, altura = A4_PT if pagina.rect.height >= pagina.rect.width else A4_PT[::-1]
            nova = saida.new_page(width=largura, height=altura)
            util = nova.rect + (MARGEM_PT, MARGEM_PT, -MARGEM_PT, -MARGEM_PT)
            nova.show_pdf_page(util, fonte, pagina.number)  # mantém proporção, centralizada
        destino.parent.mkdir(parents=True, exist_ok=True)
        saida.save(destino, garbage=3, deflate=True)


def office_para_pdf(origem: Path, destino: Path, timeout_s: int) -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise ErroConversao("LibreOffice não disponível")
    with tempfile.TemporaryDirectory(prefix="lo-") as tmp:
        perfil = Path(tmp, "perfil").as_uri()  # perfil isolado: evita lock entre execuções
        saida = Path(tmp, "saida")
        cmd = [soffice, f"-env:UserInstallation={perfil}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", str(saida), str(origem)]  # fmt: skip
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired as e:
            raise ErroConversao(f"timeout LibreOffice ({timeout_s}s)") from e
        gerados = list(saida.glob("*.pdf"))
        if not gerados:
            raise ErroConversao("LibreOffice não gerou PDF")
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(gerados[0], destino)


@contextmanager
def limite_tempo(segundos: int, descricao: str) -> Iterator[None]:
    """Interrompe operações que travam (layout patológico no WeasyPrint). Só atua na
    thread principal de processos Unix — caso dos workers de artefatos."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def estourou(*_: object) -> None:
        raise ErroConversao(f"{descricao}: timeout ({segundos}s)")

    anterior = signal.signal(signal.SIGALRM, estourou)
    signal.alarm(segundos)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, anterior)


def html_para_pdf(html: str, destino: Path, timeout_s: int = 120) -> None:
    """Render sem acesso externo: só data URI (imagens remotas já chegam embutidas)."""
    from weasyprint import HTML  # import tardio: depende de libs nativas (Pango)
    from weasyprint.urls import URLFetcher

    destino.parent.mkdir(parents=True, exist_ok=True)
    with limite_tempo(timeout_s, "render WeasyPrint"):
        HTML(string=html, url_fetcher=URLFetcher(allowed_protocols={"data"})).write_pdf(destino)


def tipo_conversao(nome: str, mime: str) -> str:
    ext = Path(nome).suffix.lower()
    if mime == "message/rfc822" or ext == ".eml":
        return "email"
    if ext in EXT_COMPACTADO or mime in MIME_COMPACTADO:
        return "compactado"
    if mime in ("application/ms-tnef", "application/vnd.ms-tnef") or nome.lower() == "winmail.dat":
        return "tnef"
    if ext == ".p7s" or mime in MIME_ASSINATURA:
        return "assinatura"
    if ext == ".p7m" or mime in MIME_SMIME:
        return "smime"
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in EXT_IMAGEM or mime.startswith("image/"):
        return "imagem"
    if ext in EXT_HTML or mime == "text/html":
        return "html"
    if ext == ".txt" or (mime == "text/plain" and ext not in EXT_OFFICE):
        return "texto"
    if ext in EXT_OFFICE:
        return "office"
    if ext == ".msg" or mime == "application/vnd.ms-outlook":
        return "msg"
    return "desconhecido"


def texto_para_pdf(dados: bytes, destino: Path, timeout_s: int = 120) -> None:
    """Texto puro (utf-8, senão cp1252) em fonte monoespaçada, quebras preservadas."""
    try:
        texto = dados.decode("utf-8")
    except UnicodeDecodeError:
        texto = dados.decode("cp1252", "replace")
    html = (
        '<html><head><meta charset="utf-8"><style>@page { size: A4; margin: 15mm }'
        "pre { white-space: pre-wrap; overflow-wrap: break-word; font: 9pt 'DejaVu Sans Mono',"
        " monospace }</style></head><body><pre>" + html_lib.escape(texto) + "</pre></body></html>"
    )
    html_para_pdf(html, destino, timeout_s)


def pdf_protegido(dados: bytes) -> bool:
    """PDF que exige senha para abrir. PDF ilegível não conta como protegido."""
    try:
        with pymupdf.open(stream=dados, filetype="pdf") as doc:
            return bool(doc.needs_pass)
    except Exception:
        return False


# --- .msg (Outlook) -----------------------------------------------------------


def msg_para_eml(dados: bytes) -> bytes:
    """Anexo .msg → .eml equivalente (cabeçalhos, corpo, anexos; .msg embutido recursivo)."""
    import extract_msg

    try:
        msg = extract_msg.openMsg(dados)
    except Exception as e:
        raise ErroConversao(f".msg ilegível: {e}") from e
    try:
        return _eml_de_msg(msg)
    finally:
        msg.close()


def _eml_de_msg(msg: object) -> bytes:
    return _mensagem_de_msg(msg).as_bytes()


def _mensagem_de_msg(msg: object) -> EmailMessage:
    from email.utils import format_datetime

    eml = EmailMessage()
    for cabecalho, attr in (
        ("From", "sender"),
        ("To", "to"),
        ("Cc", "cc"),
        ("Subject", "subject"),
        ("Message-ID", "messageId"),
        ("In-Reply-To", "inReplyTo"),
    ):
        valor = getattr(msg, attr, None)
        if valor:
            eml[cabecalho] = " ".join(str(valor).split()).replace(";", ",")
    data = getattr(msg, "date", None)
    if data is not None:
        eml["Date"] = format_datetime(data)
    eml["X-Email-Organizer"] = "convertido de .msg (Outlook)"

    texto = getattr(msg, "body", None) or ""
    eml.set_content(texto)
    html = getattr(msg, "htmlBody", None)
    if html:
        eml.add_alternative(html.decode("utf-8", "replace") if isinstance(html, bytes) else html,
                            subtype="html")  # fmt: skip

    for i, a in enumerate(getattr(msg, "attachments", []) or [], start=1):
        nome = a.longFilename or a.shortFilename or f"anexo-{i:03d}"
        if hasattr(a.data, "attachments"):  # .msg embutido
            eml.add_attachment(
                _mensagem_de_msg(a.data),
                disposition="attachment",
                filename=f"{Path(nome).stem}.eml",
            )
            continue
        if not isinstance(a.data, bytes):
            continue
        tipo = a.mimetype or mimetypes.guess_type(nome)[0] or "application/octet-stream"
        principal, sub = tipo.split("/", 1)
        eml.add_attachment(a.data, maintype=principal, subtype=sub, filename=nome,
                           cid=f"<{a.contentId}>" if a.contentId else None)  # fmt: skip
    return eml
