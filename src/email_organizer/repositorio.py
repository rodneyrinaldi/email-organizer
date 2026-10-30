"""Acesso ao repositório documental: JSON atômico, hashes, nomes, índice."""

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from email_organizer.modelos import Arquivo, Metadata, Origem

METADATA = "metadata.json"
INDICE = "_index.json"
SEM_REMETENTE = "_sem-remetente"
SEM_DESTINATARIO = "_sem-destinatario"
SEM_CONTA = "_sem-conta"
ENVIADAS = ".enviadas"  # ponto: aparece primeiro na listagem
MAX_REMETENTE = 60
MAX_SLUG = 40


# --- arquivos -----------------------------------------------------------------


def gravar_atomico(caminho: Path, dados: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=".tmp-", suffix=caminho.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dados)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, caminho)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def gravar_json(caminho: Path, dados: dict) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    gravar_atomico(caminho, texto.encode("utf-8"))


def ler_json(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def info_arquivo(caminho: Path, base: Path) -> Arquivo:
    return Arquivo(
        arquivo=caminho.relative_to(base).as_posix(),
        sha256=sha256_arquivo(caminho),
        tamanho=caminho.stat().st_size,
    )


def ler_metadata(dir_mensagem: Path) -> Metadata:
    return Metadata.model_validate(ler_json(dir_mensagem / METADATA))


def gravar_metadata(dir_mensagem: Path, meta: Metadata) -> None:
    gravar_json(dir_mensagem / METADATA, meta.model_dump(mode="json", exclude_none=True))


# --- nomes --------------------------------------------------------------------


def slug(texto: str, maximo: int = MAX_SLUG) -> str:
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", ascii_).strip("-")
    return s[:maximo].rstrip("-") or "sem-assunto"


def nome_remetente(email: str | None) -> str:
    if not email or not email.strip():
        return SEM_REMETENTE
    s = re.sub(r"[^a-z0-9@._+-]", "_", email.strip().lower())
    return s[:MAX_REMETENTE].rstrip(". ") or SEM_REMETENTE


def nome_conta(origem: Origem) -> str:
    """Pasta da conta que recebeu: e-mail (IMAP/Graph) ou `pst-<arquivo>` (PST)."""
    if origem.conta:
        return nome_remetente(origem.conta)
    if origem.arquivo:
        return f"pst-{slug(Path(origem.arquivo).stem).lower()}"
    return SEM_CONTA


def nome_diretorio(data: datetime | None, assunto: str, id_: str, tamanho_id: int = 6) -> str:
    prefixo = data.strftime("%Y-%m-%d_%H-%M-%S") if data else "sem-data"
    return f"{prefixo}_{slug(assunto)}_{id_[:tamanho_id]}"


# --- índice -------------------------------------------------------------------


class Repositorio:
    """Raiz do output. O índice (id → caminho relativo) é só um acelerador:
    pode ser reconstruído varrendo os metadata.json."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz
        self.raiz.mkdir(parents=True, exist_ok=True)
        self._indice: dict[str, str] = {}
        self._pendentes = 0
        arquivo = raiz / INDICE
        if arquivo.exists():
            self._indice = ler_json(arquivo).get("mensagens", {})
        else:
            self.reconstruir_indice()

    def caminho(self, id_: str) -> Path | None:
        rel = self._indice.get(id_)
        if rel is None:
            return None
        p = self.raiz / rel
        return p if (p / METADATA).exists() else None

    def registrar(self, id_: str, dir_mensagem: Path, salvar_a_cada: int = 50) -> None:
        self._indice[id_] = dir_mensagem.relative_to(self.raiz).as_posix()
        self._pendentes += 1
        if self._pendentes >= salvar_a_cada:
            self.salvar_indice()

    def salvar_indice(self) -> None:
        gravar_json(self.raiz / INDICE, {"versao": 1, "mensagens": self._indice})
        self._pendentes = 0

    def mensagens(self) -> Iterator[Path]:
        """Diretórios de mensagens (os que têm metadata.json), em qualquer nível do layout:
        <conta>/<remetente>/<mensagem>, <conta>/.enviadas/<destinatário>/<mensagem> e layouts
        antigos sem a pasta da conta."""

        def andar(pasta: Path, nivel: int) -> Iterator[Path]:
            for d in sorted(pasta.iterdir()):
                if not d.is_dir() or (d.name.startswith(".") and d.name != ENVIADAS):
                    continue
                if (
                    nivel == 0
                    and d.name.startswith("_")
                    and d.name not in (SEM_CONTA, SEM_REMETENTE)
                ):
                    continue  # _jobs etc.
                if (d / METADATA).exists():
                    yield d
                elif nivel < 3:
                    yield from andar(d, nivel + 1)

        yield from andar(self.raiz, 0)

    def reconstruir_indice(self) -> int:
        self._indice = {}
        for d in self.mensagens():
            self._indice[ler_json(d / METADATA)["id"]] = d.relative_to(self.raiz).as_posix()
        self.salvar_indice()
        return len(self._indice)

    def __len__(self) -> int:
        return len(self._indice)

    def __enter__(self) -> "Repositorio":
        return self

    def __exit__(self, *_: object) -> None:
        self.salvar_indice()
