"""Fonte PST via readpst (libpst): exporta para .eml num diretório temporário.

O PST é aberto somente para leitura. readpst reconstrói o MIME (PST não guarda o
original); cabeçalhos de transporte, quando existem, são preservados.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from email_organizer.config import FontePst
from email_organizer.fontes import MensagemBruta
from email_organizer.modelos import Origem

OBSERVACAO = "MIME reconstruído por readpst a partir do PST"

Exportador = Callable[[Path, Path], None]


def exportar_readpst(pst: Path, destino: Path) -> None:
    if shutil.which("readpst") is None:
        raise RuntimeError("readpst não disponível (pacote pst-utils)")
    # -e: um .eml por mensagem | -t e: só e-mails | sem -b: corpo RTF vem como rtf-body.rtf
    # -8: UTF-8 | -q: silencioso
    cmd = ["readpst", "-e", "-t", "e", "-8", "-q", "-o", str(destino), str(pst)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        detalhe = (r.stderr or r.stdout).strip()[-500:]
        raise RuntimeError(f"readpst falhou ({r.returncode}) em {pst.name}: {detalhe}")


class LeitorPst:
    def __init__(
        self, cfg: FontePst, dir_tmp: Path | None = None, exportador: Exportador = exportar_readpst
    ) -> None:
        self.cfg = cfg
        self.pst = Path(cfg.caminho)
        if not self.pst.is_file():
            raise FileNotFoundError(f"PST não encontrado: {self.pst}")
        self._tmp = Path(tempfile.mkdtemp(prefix="pst-", dir=dir_tmp))
        exportador(self.pst, self._tmp)
        self._raiz = self._raiz_exportada()

    def _raiz_exportada(self) -> Path:
        """readpst cria um diretório com o nome da pasta-raiz do PST; as pastas ficam abaixo."""
        filhos = [d for d in self._tmp.iterdir() if d.is_dir()]
        return filhos[0] if len(filhos) == 1 and not list(self._tmp.glob("*.eml")) else self._tmp

    def _pastas(self) -> dict[str, Path]:
        pastas = {}
        for d in sorted({p.parent for p in self._raiz.rglob("*.eml")}):
            nome = d.relative_to(self._raiz).as_posix()
            pastas[nome if nome != "." else ""] = d
        return pastas

    def listar_pastas(self) -> list[str]:
        return list(self._pastas())

    def mensagens(self, pastas: list[str]) -> Iterator[MensagemBruta]:
        disponiveis = self._pastas()
        por_nome = {n.casefold(): n for n in disponiveis}
        for pedida in pastas:
            nome = por_nome.get(pedida.casefold())
            if nome is None:
                continue
            arquivos = sorted(disponiveis[nome].glob("*.eml"), key=_ordem_numerica)
            for arq in arquivos:
                origem = Origem(
                    tipo="pst", arquivo=self.pst.name, pasta=nome, observacao=OBSERVACAO
                )
                yield MensagemBruta(arq.read_bytes(), origem)

    def registrar(self, origem: Origem) -> None:
        pass  # sem estado incremental: dedup resolve; reexporta a cada execução

    def fechar(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


def _ordem_numerica(p: Path) -> tuple[int, str]:
    return (int(p.stem), "") if p.stem.isdigit() else (1 << 62, p.name)
