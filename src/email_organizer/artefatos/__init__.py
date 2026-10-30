"""Etapa 'artefatos': gera PDF principal, Markdown, corpo/ e anexos/ a partir do .eml salvo.

Idempotente: apaga e regenera tudo que é derivado; nunca toca em original/.
"""

import shutil
from pathlib import Path

from email_organizer import __version__
from email_organizer.artefatos import conversao, pdf_principal
from email_organizer.artefatos.markdown import para_markdown
from email_organizer.artefatos.mime import Parte, decompor, parse
from email_organizer.captura import ORIGINAL, agora, analisar, criar_diretorio
from email_organizer.config import Opcoes
from email_organizer.modelos import Anexo, Aviso, Etapa, ItemCorpo, Metadata, Pai
from email_organizer.repositorio import gravar_atomico, gravar_metadata, info_arquivo, ler_metadata

ETAPA = "artefatos"
PDF = "mensagem.pdf"
MD = "mensagem-texto.md"
DERIVADOS = (PDF, MD, "corpo", "anexos")
MAX_PROFUNDIDADE = 5
IGNORADOS = {
    "compactado": "compactado: não processado",
    "tnef": "container TNEF (winmail.dat): anexos internos extraídos",
    "assinatura": "assinatura S/MIME (original preservado)",
}


def _limpar(dir_msg: Path) -> None:
    for nome in DERIVADOS:
        p = dir_msg / nome
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)


def gerar_artefatos(
    dir_msg: Path,
    opcoes: Opcoes,
    contas_proprias: set[str],
    baixador: pdf_principal.Baixador = pdf_principal.baixar,
    profundidade: int = 0,
) -> Metadata:
    meta = ler_metadata(dir_msg)
    meta.avisos = [a for a in meta.avisos if a.etapa != ETAPA]
    avisos: list[str] = []
    _limpar(dir_msg)

    est = decompor(parse((dir_msg / ORIGINAL).read_bytes()))
    avisos += est.avisos

    meta.corpo = _corpo(dir_msg, est.inline, avisos)
    meta.anexos = [
        _anexo(dir_msg, meta, i, p, opcoes, contas_proprias, baixador, profundidade, avisos)
        for i, p in enumerate(_com_nomes_unicos(est.anexos), start=1)
    ]

    erro = None
    meta.pdf_principal = None
    meta.imagens_remotas = []
    if opcoes.pdf_principal:
        try:
            html = None
            if est.html is not None:
                html, meta.imagens_remotas = pdf_principal.preparar_html(
                    est.html, est.inline, opcoes.baixar_imagens_remotas, baixador
                )
            pdf_principal.gerar(
                dir_msg / PDF, _contexto(meta, html, est.texto), opcoes.timeout_conversao_s
            )
            meta.pdf_principal = info_arquivo(dir_msg / PDF, dir_msg)
        except Exception as e:
            erro = f"PDF principal: {e}"
            avisos.append(erro)

    meta.texto_markdown = None
    if opcoes.markdown:
        gravar_atomico(dir_msg / MD, para_markdown(est.html, est.texto).encode("utf-8"))
        meta.texto_markdown = info_arquivo(dir_msg / MD, dir_msg)

    meta.avisos += [Aviso(etapa=ETAPA, mensagem=a) for a in avisos]
    meta.pipeline[ETAPA] = Etapa(
        status="erro" if erro else "ok", em=agora(), versao=__version__, erro=erro
    )
    gravar_metadata(dir_msg, meta)
    return meta


def _com_nomes_unicos(partes: list[Parte]) -> list[tuple[Parte, str]]:
    usados: set[str] = set()
    return [(p, conversao.nome_unico(conversao.nome_seguro(p.nome), usados)) for p in partes]


def _corpo(dir_msg: Path, inline: list[Parte], avisos: list[str]) -> list[ItemCorpo]:
    itens = []
    for i, p in enumerate(inline, start=1):
        destino = dir_msg / "corpo" / f"imagem-{i:03d}.pdf"
        try:
            conversao.imagem_para_pdf(p.dados, destino)
        except conversao.ErroConversao as e:
            avisos.append(f"corpo {p.nome}: {e}")
            continue
        info = info_arquivo(destino, dir_msg)
        itens.append(
            ItemCorpo(
                ordem=i,
                arquivo=info.arquivo,
                sha256=info.sha256,
                tamanho=info.tamanho,
                mime=p.mime,
                content_id=p.content_id,
                nome_original=p.nome,
            )
        )
    return itens


def _anexo(
    dir_msg: Path,
    meta: Metadata,
    ordem: int,
    parte_nome: tuple[Parte, str],
    opcoes: Opcoes,
    contas_proprias: set[str],
    baixador: pdf_principal.Baixador,
    profundidade: int,
    avisos: list[str],
) -> Anexo:
    p, nome = parte_nome
    tipo = conversao.tipo_conversao(nome, p.mime)
    base = {"ordem": ordem, "nome_original": p.nome, "mime": p.mime}

    if tipo == "email":
        return _email_anexado(
            dir_msg, meta, p, opcoes, contas_proprias, baixador, profundidade, avisos, base
        )

    original = dir_msg / "anexos" / "originais" / nome
    gravar_atomico(original, p.dados)
    base["arquivo_original"] = info_arquivo(original, dir_msg)
    if tipo in IGNORADOS:
        return Anexo(tipo="arquivo", status="ignorado", motivo=IGNORADOS[tipo], **base)
    if tipo == "smime":
        avisos.append(f"anexo {p.nome}: S/MIME cifrado, sem conversão")
        return Anexo(tipo="arquivo", status="nao_convertido", motivo="S/MIME cifrado", **base)
    if tipo == "desconhecido":
        return Anexo(
            tipo="arquivo", status="nao_convertido", motivo="formato sem conversor", **base
        )
    if tipo == "msg":
        return _msg_anexado(
            dir_msg, meta, p, opcoes, contas_proprias, baixador, profundidade, avisos, base
        )

    destino = _destino_pdf(dir_msg, nome)
    try:
        if tipo == "pdf":
            shutil.copyfile(original, destino)
        elif tipo == "imagem":
            conversao.imagem_para_pdf(p.dados, destino)
        elif tipo == "texto":
            conversao.texto_para_pdf(p.dados, destino, opcoes.timeout_conversao_s)
        elif tipo == "html":
            conversao.html_para_pdf(
                p.dados.decode("utf-8", "replace"), destino, opcoes.timeout_conversao_s
            )
        else:
            conversao.office_para_pdf(original, destino, opcoes.timeout_conversao_s)
    except Exception as e:
        avisos.append(f"anexo {p.nome}: {e}")
        return Anexo(tipo="arquivo", status="nao_convertido", motivo=str(e)[:300], **base)
    status = "original_pdf" if tipo == "pdf" else "convertido"
    if tipo == "pdf" and conversao.pdf_protegido(p.dados):
        avisos.append(f"anexo {p.nome}: PDF protegido por senha, copiado sem alteração")
        base["motivo"] = "PDF protegido por senha"
    return Anexo(tipo="arquivo", status=status, arquivo_pdf=info_arquivo(destino, dir_msg), **base)


def _msg_anexado(
    dir_msg: Path,
    meta: Metadata,
    p: Parte,
    opcoes: Opcoes,
    contas_proprias: set[str],
    baixador: pdf_principal.Baixador,
    profundidade: int,
    avisos: list[str],
    base: dict,
) -> Anexo:
    """.msg do Outlook → .eml equivalente processado como e-mail anexado; o original .msg
    (já salvo em anexos/originais/) continua sendo o `arquivo_original`."""
    try:
        eml = conversao.msg_para_eml(p.dados)
    except conversao.ErroConversao as e:
        avisos.append(f"anexo {p.nome}: {e}")
        return Anexo(tipo="arquivo", status="nao_convertido", motivo=str(e)[:300], **base)
    original = base.pop("arquivo_original")
    a = _email_anexado(
        dir_msg, meta, Parte(p.nome, "message/rfc822", eml, p.content_id), opcoes,
        contas_proprias, baixador, profundidade, avisos, base,
    )  # fmt: skip
    return a.model_copy(update={"arquivo_original": original, "motivo": "convertido de .msg"})


def _destino_pdf(dir_msg: Path, nome: str) -> Path:
    pasta = dir_msg / "anexos"
    pasta.mkdir(parents=True, exist_ok=True)
    usados = {f.name.lower() for f in pasta.glob("*.pdf")}
    return pasta / conversao.nome_unico(f"{Path(nome).stem}.pdf", usados)


def _email_anexado(
    dir_msg: Path,
    meta: Metadata,
    p: Parte,
    opcoes: Opcoes,
    contas_proprias: set[str],
    baixador: pdf_principal.Baixador,
    profundidade: int,
    avisos: list[str],
    base: dict,
) -> Anexo:
    a = analisar(p.dados)
    pasta = dir_msg / "anexos" / "emails"
    pasta.mkdir(parents=True, exist_ok=True)
    nome = a.id[:6]
    if (pasta / nome).exists():
        nome = conversao.nome_unico(nome, {d.name.lower() for d in pasta.iterdir()})
    sub = pasta / nome
    origem = meta.origem.model_copy(update={"observacao": f"e-mail anexado à mensagem {meta.id}"})
    criar_diretorio(
        sub, p.dados, a, origem, contas_proprias, {"pai": Pai(id=meta.id, diretorio="../../..")}
    )
    rel = sub.relative_to(dir_msg).as_posix()
    if profundidade >= MAX_PROFUNDIDADE:
        avisos.append(f"e-mail anexado {p.nome}: profundidade máxima, artefatos não gerados")
        filho = ler_metadata(sub)
    else:
        filho = gerar_artefatos(sub, opcoes, contas_proprias, baixador, profundidade + 1)
    pdf = None
    if filho.pdf_principal:
        pdf = info_arquivo(sub / filho.pdf_principal.arquivo, dir_msg)
    return Anexo(
        tipo="email",
        status="convertido" if pdf else "nao_convertido",
        diretorio=rel,
        arquivo_original=info_arquivo(sub / ORIGINAL, dir_msg),
        arquivo_pdf=pdf,
        **base,
    )


def _contexto(meta: Metadata, html: str | None, texto: str | None) -> dict:
    anexos = [
        {
            "ordem": a.ordem,
            "nome_original": a.nome_original,
            "tamanho": _tamanho(a.arquivo_original.tamanho) if a.arquivo_original else "",
            "sha256": a.arquivo_original.sha256 if a.arquivo_original else "",
        }
        for a in meta.anexos
    ]
    return {
        "meta": meta,
        "corpo_html": html,
        "corpo_texto": texto or "",
        "anexos": anexos,
        "gerado_em": agora(),
        "versao": __version__,
    }


def _tamanho(n: int) -> str:
    for unidade in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unidade}"
        n /= 1024
    return f"{n:.1f} GB"
