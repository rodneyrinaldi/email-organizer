"""CLI: python -m email_organizer <comando> [--output /output]"""

import argparse
import os
import sys
from pathlib import Path

from email_organizer.captura import reorganizar
from email_organizer.config import SEGREDOS, carregar_config, dir_estado
from email_organizer.fontes.graph import Autenticacao
from email_organizer.ia import ClienteOllama, classificar_lote, precisa_classificar
from email_organizer.processar import abrir_leitor, ia_habilitada, processar
from email_organizer.reiniciar import alvos, reiniciar
from email_organizer.repositorio import Repositorio


def _progresso(texto: str) -> None:
    """Linha que se reescreve no terminal; fora de terminal (docker sem -t, log) não imprime."""
    if sys.stdout.isatty():
        print(f"\r{texto}", end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):  # Windows: acentos certos no console e em pipes
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="email_organizer")
    p.add_argument("--output", type=Path, default=Path("/output"))
    p.add_argument("--estado", type=Path, help="segredos/tokens/estado (padrão: <output>/.estado)")
    sub = p.add_subparsers(dest="comando", required=True)

    pr = sub.add_parser("processar", help="processa as fontes ativas do config.json")
    pr.add_argument("--fonte", action="append", help="nome da fonte (repetível)")
    pr.add_argument("--sem-artefatos", action="store_true")
    pr.add_argument("--sem-classificacao", action="store_true")

    pa = sub.add_parser("pastas", help="lista as pastas de uma fonte")
    pa.add_argument("fonte")

    co = sub.add_parser("conectar", help="autoriza uma conta Graph (device code)")
    co.add_argument("fonte")

    cl = sub.add_parser(
        "classificar", help="classifica pendentes do repositório (sem caixa postal)"
    )
    cl.add_argument("--todas", action="store_true", help="reclassifica também as já classificadas")

    sub.add_parser("reconstruir-indice")

    re_ = sub.add_parser(
        "reiniciar", help="apaga acervo e estado incremental (mantém config, senhas e tokens)"
    )
    re_.add_argument("--confirmar", action="store_true", help="sem isso, só lista o que apagaria")

    ro = sub.add_parser(
        "reorganizar", help="move mensagens para o layout atual (ex.: enviadas por destinatário)"
    )
    ro.add_argument("--confirmar", action="store_true", help="sem isso, só lista o que moveria")

    args = p.parse_args(argv)
    raiz: Path = args.output
    if args.estado:
        os.environ["EMAIL_ORGANIZER_ESTADO"] = str(args.estado)

    if args.comando == "reiniciar":
        itens = alvos(raiz, dir_estado(raiz))
        if not args.confirmar:
            print("\n".join(str(p) for p in itens) or "nada a apagar")
            print(
                f"{len(itens)} itens. Rode com --confirmar para apagar (a caixa postal não muda)."
            )
            return 0
        print(f"{len(reiniciar(raiz, dir_estado(raiz)))} itens apagados")
        return 0

    if args.comando == "reconstruir-indice":
        print(f"{Repositorio(raiz).reconstruir_indice()} mensagens indexadas")
        return 0

    config = carregar_config(raiz / "config.json", dir_estado(raiz) / SEGREDOS)
    todas = [*config.imap, *config.graph, *config.pst]

    if args.comando == "reorganizar":
        with Repositorio(raiz) as repo:
            movidos = reorganizar(repo, config.todas_contas_proprias(), args.confirmar)
        for de, para in movidos:
            print(f"{de.relative_to(raiz)} → {para.relative_to(raiz)}")
        acao = "movidos" if args.confirmar else "a mover (rode com --confirmar)"
        print(f"{len(movidos)} diretórios {acao}")
        return 0

    if args.comando == "classificar":
        if not ia_habilitada(config):
            print("classificação desabilitada no config.json", file=sys.stderr)
            return 1
        with Repositorio(raiz) as repo:
            dirs = [d for d in repo.mensagens() if args.todas or precisa_classificar(d)]
        cliente = ClienteOllama.de_config(config)
        try:
            contagem = classificar_lote(dirs, config, cliente, lambda c: _progresso(str(c)))
        finally:
            cliente.fechar()
        print(f"\n{len(dirs)} mensagens: {contagem}")
        return 1 if contagem["erro"] or contagem["pendente"] else 0

    if args.comando == "conectar":
        fonte = next((f for f in config.graph if f.nome == args.fonte), None)
        if fonte is None:
            print(f"fonte Graph {args.fonte!r} não encontrada", file=sys.stderr)
            return 1
        usuario = Autenticacao(fonte, dir_estado(raiz)).conectar()
        print(f"conectado: {usuario}")
        return 0

    if args.comando == "pastas":
        fonte = next((f for f in todas if f.nome == args.fonte), None)
        if fonte is None:
            print(f"fonte {args.fonte!r} não encontrada", file=sys.stderr)
            return 1
        leitor = abrir_leitor(fonte, config, raiz, None)
        try:
            print("\n".join(leitor.listar_pastas()))
        finally:
            leitor.fechar()
        return 0

    fontes = None
    if args.fonte:
        fontes = [f for f in todas if f.nome in args.fonte]
    rel = processar(
        config,
        raiz,
        fontes,
        gerar=not args.sem_artefatos,
        classificar=not args.sem_classificacao,
        progresso=lambda r: _progresso(str(r.total)),
    )
    print(f"\nrelatório: {raiz / '_jobs' / rel.job / 'relatorio.md'}")
    return 1 if rel.erros else 0


if __name__ == "__main__":
    sys.exit(main())
