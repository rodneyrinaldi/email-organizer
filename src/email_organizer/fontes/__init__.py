"""Fontes de entrada. Toda fonte entrega .eml bruto + Origem; nunca grava artefato
e nunca altera a origem."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from email_organizer.modelos import Origem


@dataclass
class MensagemBruta:
    bruto: bytes
    origem: Origem


class Leitor(Protocol):
    def listar_pastas(self) -> list[str]: ...

    def mensagens(self, pastas: list[str]) -> Iterator[MensagemBruta]: ...

    def registrar(self, origem: Origem) -> None:
        """Chamado após capturar (nova ou duplicada): permite pular na próxima leitura."""

    def fechar(self) -> None: ...
