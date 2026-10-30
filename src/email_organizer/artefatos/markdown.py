"""mensagem-texto.md: somente o texto do corpo (sem cabeçalhos, imagens, anexos)."""

import re

from markdownify import markdownify

_SEM_TEXTO = re.compile(r"<(head|style|script|title)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMENTARIOS = re.compile(r"<!--.*?-->", re.DOTALL)
_LINHAS_VAZIAS = re.compile(r"\n{3,}")


def para_markdown(html: str | None, texto: str | None) -> str:
    if html is not None:
        limpo = _COMENTARIOS.sub("", _SEM_TEXTO.sub("", html))
        md = markdownify(limpo, heading_style="ATX", strip=["img"])
    else:
        md = texto or ""
    md = "\n".join(linha.rstrip() for linha in md.replace("\r\n", "\n").split("\n"))
    md = md.replace("\xa0", " ")
    return _LINHAS_VAZIAS.sub("\n\n", md).strip() + "\n"
