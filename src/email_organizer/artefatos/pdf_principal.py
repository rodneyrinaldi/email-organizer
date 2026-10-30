"""PDF principal: cabeçalhos + corpo fiel (imagens inline na posição) + lista de anexos."""

import base64
import hashlib
import html as html_lib
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx
from jinja2 import Environment, PackageLoader

from email_organizer.artefatos.conversao import html_para_pdf
from email_organizer.artefatos.mime import Parte
from email_organizer.modelos import ImagemRemota

_IMG_SRC = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
_PERIGOSOS = re.compile(r"<(script|iframe|object|embed)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
MAX_IMAGEM_REMOTA = 10 * 1024 * 1024

_env = Environment(loader=PackageLoader("email_organizer", "templates"), autoescape=True)


def data_br(iso: str | None) -> str:
    if not iso:
        return "(sem data)"
    d = datetime.fromisoformat(iso)
    fuso = d.strftime("%z")
    return d.strftime("%d/%m/%Y %H:%M:%S") + (f" (UTC{fuso[:3]}:{fuso[3:]})" if fuso else "")


_env.filters["data_br"] = data_br

Baixador = Callable[[str], tuple[bytes, str]]
DOWNLOADS_PARALELOS = 8

_cliente: httpx.Client | None = None


def _http() -> httpx.Client:
    """Cliente compartilhado (thread-safe): reaproveita conexões entre imagens e mensagens."""
    global _cliente
    if _cliente is None:
        _cliente = httpx.Client(
            timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        )
    return _cliente


def baixar(url: str) -> tuple[bytes, str]:
    r = _http().get(url)
    r.raise_for_status()
    mime = r.headers.get("content-type", "").split(";")[0].strip()
    if not mime.startswith("image/"):
        raise ValueError(f"conteúdo não é imagem: {mime or '?'}")
    if len(r.content) > MAX_IMAGEM_REMOTA:
        raise ValueError("imagem maior que 10 MB")
    return r.content, mime


def data_uri(dados: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(dados).decode()}"


def _src(m: re.Match) -> str:
    return html_lib.unescape(m.group(3).strip())


def preparar_html(
    html: str, inline: list[Parte], baixar_remotas: bool, baixador: Baixador = baixar
) -> tuple[str, list[ImagemRemota]]:
    """Troca cid: e http(s): por data URI. Falha mantém a URL visível no lugar da imagem."""
    html = _PERIGOSOS.sub("", html)
    por_cid = {p.content_id.lower(): p for p in inline if p.content_id}
    urls = list(
        dict.fromkeys(
            s
            for s in map(_src, _IMG_SRC.finditer(html))
            if s.lower().startswith(("http:", "https:"))
        )
    )
    remotas = _baixar_todas(urls, baixar_remotas, baixador)

    def trocar(m: re.Match) -> str:
        prefixo, aspas, src = m.group(1), m.group(2), _src(m)
        if src.lower().startswith("cid:"):
            parte = por_cid.get(src[4:].lower())
            novo = data_uri(parte.dados, parte.mime) if parte else ""
            if novo:  # imagem colada: maximizada na página (CSS .eo-colada)
                prefixo = re.sub(r"^<img\b", '<img class="eo-colada"', prefixo, flags=re.I)
        elif src in remotas:
            novo = remotas[src][1]
        else:
            novo = src if src.startswith("data:") else ""
        if not novo:
            return f'{prefixo}{aspas}{aspas} alt="[imagem não disponível: {_attr(src)}]"'
        return f"{prefixo}{aspas}{novo}{aspas}"

    return _IMG_SRC.sub(trocar, html), [r[0] for r in remotas.values()]


def _baixar_todas(
    urls: list[str], baixar_remotas: bool, baixador: Baixador
) -> dict[str, tuple[ImagemRemota, str]]:
    if not baixar_remotas:
        return {u: (ImagemRemota(url=u, status="nao_baixada"), "") for u in urls}

    def uma(url: str) -> tuple[ImagemRemota, str]:
        try:
            dados, mime = baixador(url)
        except Exception as e:
            return ImagemRemota(url=url, status="falhou", motivo=str(e)[:200]), ""
        info = ImagemRemota(url=url, status="embutida", sha256=hashlib.sha256(dados).hexdigest())
        return info, data_uri(dados, mime)

    with ThreadPoolExecutor(DOWNLOADS_PARALELOS) as pool:
        return dict(zip(urls, pool.map(uma, urls), strict=True))


def _attr(texto: str) -> str:
    return texto.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")[:200]


def gerar(destino: Path, contexto: dict, timeout_s: int = 120) -> None:
    html = _env.get_template("mensagem.html.j2").render(**contexto)
    html_para_pdf(html, destino, timeout_s)
