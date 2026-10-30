import pytest

from conftest import CONTAS, eml
from email_organizer import limpeza
from email_organizer.captura import capturar


@pytest.fixture
def acervo(repo, origem_imap):
    recebida = capturar(repo, eml("simples.eml"), origem_imap, CONTAS).diretorio
    enviada = capturar(repo, eml("enviada.eml"), origem_imap, CONTAS).diretorio
    return repo.raiz, recebida, enviada


def test_arvore(acervo):
    raiz, recebida, enviada = acervo
    (conta,) = limpeza.arvore(raiz)
    assert (conta.nome, conta.tipo, conta.mensagens) == ("voce@gmail.com", "conta", 2)
    filhos = {n.nome: n for n in conta.filhos.values()}
    assert filhos[".enviadas"].tipo == "enviadas"
    assert filhos[".enviadas"].filhos["cliente@cliente.com"].tipo == "destinatario"
    assert filhos["joao@email.com"].tipo == "remetente"
    msgs = limpeza.mensagens_da_pasta(raiz, "voce@gmail.com/joao@email.com")
    assert [m["caminho"] for m in msgs] == [recebida.relative_to(raiz).as_posix()]


def test_mover_restaurar_e_esvaziar(acervo):
    raiz, recebida, enviada = acervo
    rel_env = enviada.relative_to(raiz).as_posix()
    # pasta .enviadas inteira + mensagem dentro dela (ignorada: já coberta pela pasta)
    n = limpeza.mover_para_lixeira(raiz, ["voce@gmail.com/.enviadas", rel_env])
    assert n == 1
    assert not enviada.exists() and not (raiz / "voce@gmail.com/.enviadas").exists()
    (lote,) = limpeza.lotes(raiz)
    assert lote["itens"] == [{"caminho": "voce@gmail.com/.enviadas", "mensagens": 1}]
    assert (raiz / ".lixeira" / lote["nome"] / rel_env / "metadata.json").exists()
    assert len(list(limpeza.Repositorio(raiz).mensagens())) == 1  # lixeira fora do acervo

    limpeza.restaurar(raiz, lote["nome"], "voce@gmail.com/.enviadas")
    assert enviada.exists() and limpeza.lotes(raiz) == []

    limpeza.mover_para_lixeira(raiz, [recebida.relative_to(raiz).as_posix()])
    assert not (raiz / "voce@gmail.com/joao@email.com").exists()  # pasta vazia sai
    assert limpeza.esvaziar(raiz) == 1
    assert not (raiz / ".lixeira").exists()
    assert recebida.parent.parent.exists()  # a conta continua (tem .enviadas)


def test_caminho_invalido(acervo):
    raiz, *_ = acervo
    for ruim in ["", "_jobs", "config.json", "../fora", ".lixeira", "voce@gmail.com/x"]:
        with pytest.raises(ValueError):
            limpeza.mover_para_lixeira(raiz, [ruim])
    with pytest.raises(ValueError):
        limpeza.excluir_lote(raiz, "../output")


def test_restaurar_nao_sobrescreve(acervo, origem_imap):
    raiz, recebida, _ = acervo
    rel = recebida.relative_to(raiz).as_posix()
    limpeza.mover_para_lixeira(raiz, [rel])
    novo = limpeza.Repositorio(raiz)  # índice já sem a mensagem → recaptura no mesmo lugar
    assert capturar(novo, eml("simples.eml"), origem_imap, CONTAS).status == "nova"
    (lote,) = limpeza.lotes(raiz)
    with pytest.raises(FileExistsError):
        limpeza.restaurar(raiz, lote["nome"], rel)


def test_mover_libera_controle_incremental(acervo, tmp_path):
    from email_organizer import incremental
    from email_organizer.repositorio import gravar_json, ler_json

    raiz, recebida, enviada = acervo  # origem: imap voce@gmail.com, pasta "mariana", uid 1
    estado = tmp_path / "estado"
    arq = estado / "imap" / "voce-gmail-com" / "mariana.json"
    gravar_json(arq, {"pasta": "mariana", "uidvalidity": None, "capturados": [1, 2, 3]})
    graph = estado / "graph" / "voce-gmail-com" / "x.json"
    gravar_json(graph, {"pasta": "x", "capturados": ["a"]})

    limpeza.mover_para_lixeira(raiz, [recebida.relative_to(raiz).as_posix()], estado)
    assert ler_json(arq)["capturados"] == [2, 3]
    assert ler_json(graph)["capturados"] == ["a"]  # outra fonte intocada
    # sem estado (opção desmarcada): controle não muda
    limpeza.mover_para_lixeira(raiz, [enviada.relative_to(raiz).as_posix()])
    assert ler_json(arq)["capturados"] == [2, 3]

    assert (
        incremental.liberar(
            estado, [{"tipo": "graph", "conta": "voce@gmail.com", "pasta": "x", "graph_id": "a"}]
        )
        == 1
    )
    assert incremental.liberar(estado, [{"tipo": "pst", "arquivo": "x.pst"}]) == 0


def test_zerar_controle_da_fonte(tmp_path):
    from email_organizer import incremental
    from email_organizer.config import FonteGraph, FonteImap, FontePst
    from email_organizer.repositorio import gravar_json

    estado = tmp_path / "estado"
    gravar_json(estado / "graph" / "eu-hotmail-com" / "inbox.json", {"capturados": ["a"]})
    hotmail = FonteGraph(nome="Hotmail", usuario="eu@hotmail.com", client_id="x")
    assert incremental.zerar(estado, hotmail) is True
    assert not (estado / "graph" / "eu-hotmail-com").exists()
    assert incremental.zerar(estado, hotmail) is False
    assert incremental.zerar(estado, FonteImap(nome="g", host="h", usuario="a@b.c")) is False
    assert incremental.zerar(estado, FontePst(nome="p", caminho="/x.pst")) is False
