from datetime import date
from email import policy
from email.parser import BytesParser

import pytest

from conftest import eml
from email_organizer.config import Config
from email_organizer.filtro import Filtro, aceita, aceita_todos


def msg(nome: str):
    return BytesParser(policy=policy.default).parsebytes(eml(nome))


@pytest.mark.parametrize(
    ("filtro", "esperado"),
    [
        (Filtro(), True),
        (Filtro(data_de=date(2025, 7, 15), data_ate=date(2025, 7, 15)), True),
        (Filtro(data_de=date(2025, 7, 16)), False),
        (Filtro(data_ate=date(2025, 7, 14)), False),
        (Filtro(remetente=["MARIANA"]), True),
        (Filtro(remetente=["@cliente.com.br"]), True),
        (Filtro(remetente=["outro"]), False),
        (Filtro(excluir_remetente=["mariana@"]), False),
        (Filtro(destinatario=["voce"]), True),
        (Filtro(assunto=["contrato", "TRABALHISTA"]), True),
        (Filtro(assunto=["contrato"]), False),
        (Filtro(corpo=["procuracao"]), True),  # sem acento casa com acento, dentro de HTML
        (Filtro(corpo=["boleto"]), False),
        (Filtro(com_anexo=True), True),
        (Filtro(com_anexo=False), False),
    ],
)
def test_criterios(filtro, esperado):
    assert aceita(filtro, msg("com_anexo.eml")) is esperado


def test_sem_data_falha_filtro_de_data():
    assert not aceita(Filtro(data_de=date(2000, 1, 1)), msg("malformado.eml"))
    assert aceita(Filtro(), msg("malformado.eml"))


def test_aceita_todos_combina_com_e():
    m = msg("com_anexo.eml")
    assert aceita_todos([Filtro(remetente=["mariana"]), Filtro(com_anexo=True)], m)
    assert not aceita_todos([Filtro(remetente=["mariana"]), Filtro(com_anexo=False)], m)


def _config() -> Config:
    return Config.model_validate(
        {
            "filtros": {"trab": {"assunto": ["trabalhista"]}},
            "imap": [
                {
                    "nome": "g",
                    "host": "h",
                    "usuario": "u@x",
                    "filtro": {"com_anexo": True},
                    "pastas": [
                        "inbox",
                        {"nome": "Mariana", "filtro": "trab"},
                        {"nome": "outra", "filtro": {"remetente": ["a"]}},
                    ],
                }
            ],
        }
    )


def test_resolucao_mais_especifico_vence():
    c = _config()
    fonte = c.imap[0]
    assert c.filtros_para(fonte, "INBOX") == [Filtro(com_anexo=True)]  # herda da fonte
    assert c.filtros_para(fonte, "mariana") == [Filtro(assunto=["trabalhista"])]  # nomeado
    assert c.filtros_para(fonte, "outra") == [Filtro(remetente=["a"])]
    assert c.filtros_para(fonte, "nao-listada") == [Filtro(com_anexo=True)]
    avulso = Filtro(data_de=date(2025, 1, 1))
    assert c.filtros_para(fonte, "mariana", avulso) == [Filtro(assunto=["trabalhista"]), avulso]


def test_filtro_nomeado_inexistente():
    with pytest.raises(ValueError, match="não definido"):
        Config.model_validate(
            {"imap": [{"nome": "g", "host": "h", "usuario": "u", "filtro": "nada"}]}
        )
