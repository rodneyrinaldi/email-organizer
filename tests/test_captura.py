import hashlib
import shutil

from conftest import CONTAS, eml
from email_organizer.captura import capturar, chave_dedup, id_de, thread_por_cabecalho
from email_organizer.modelos import Origem
from email_organizer.repositorio import Repositorio, gravar_json, ler_metadata


def test_nova_mensagem_cria_estrutura(repo, origem_imap):
    bruto = eml("simples.eml")
    r = capturar(repo, bruto, origem_imap, CONTAS)

    assert r.status == "nova"
    # recebida: <conta que recebeu>/<remetente>/<mensagem>
    assert r.diretorio.parent.name == "joao@email.com"
    assert r.diretorio.parent.parent.name == "voce@gmail.com"
    assert r.diretorio.name == f"2026-10-29_14-33-10_RE-Financiamento-Imobiliario_{r.id[:6]}"
    original = r.diretorio / "original" / "mensagem.eml"
    assert original.read_bytes() == bruto

    meta = ler_metadata(r.diretorio)
    assert meta.message_id == "<abc123@banco.com.br>"
    assert meta.chave_dedup.tipo == "message_id"
    assert meta.original.sha256 == hashlib.sha256(bruto).hexdigest()
    assert meta.original.tamanho == len(bruto)
    assert meta.assunto == "RE: Financiamento Imobiliário"
    assert meta.remetente.nome == "João Silva"
    assert meta.remetente.email == "joao@email.com"
    assert [p.email for p in meta.cc] == ["outro@x.com", "fulana@y.com"]
    assert meta.data == "2026-10-29T14:33:10-03:00"
    assert meta.data_utc == "2026-10-29T17:33:10Z"
    assert meta.direcao == "recebida"
    assert meta.thread.tipo == "resposta"
    assert meta.thread.pai_message_id == "<pai999@gmail.com>"
    assert meta.references == ["<avo1@gmail.com>", "<pai999@gmail.com>"]
    assert meta.pipeline["captura"].status == "ok"
    assert meta.origem.capturado_em is not None
    assert not list(r.diretorio.parent.glob(".tmp-*"))


def test_duplicada_mesma_origem_nao_repete(repo, origem_imap):
    a = capturar(repo, eml("simples.eml"), origem_imap, CONTAS)
    b = capturar(repo, eml("simples.eml"), origem_imap, CONTAS)
    assert b.status == "duplicada" and b.diretorio == a.diretorio
    assert ler_metadata(a.diretorio).origens_adicionais == []


def test_duplicada_outra_origem_registra(repo, origem_imap):
    a = capturar(repo, eml("simples.eml"), origem_imap, CONTAS)
    pst = Origem(tipo="pst", arquivo="Advocacia.pst", pasta="Caixa de Entrada")
    capturar(repo, eml("simples.eml"), pst, CONTAS)
    capturar(repo, eml("simples.eml"), pst, CONTAS)
    adicionais = ler_metadata(a.diretorio).origens_adicionais
    assert [o.arquivo for o in adicionais] == ["Advocacia.pst"]
    # mesma mensagem em duas contas: fica na pasta da 1ª conta que a capturou
    assert a.diretorio.parent.parent.name == "voce@gmail.com"


def test_pasta_da_conta_pst(repo):
    pst = Origem(tipo="pst", arquivo="Advocacia 2019.pst", pasta="Caixa de Entrada")
    r = capturar(repo, eml("simples.eml"), pst, CONTAS)
    assert r.diretorio.relative_to(repo.raiz).parts[:2] == ("pst-advocacia-2019", "joao@email.com")
    assert len(list(repo.mensagens())) == 1


def test_dedup_sobrevive_sem_indice(repo, origem_imap):
    a = capturar(repo, eml("simples.eml"), origem_imap, CONTAS)
    gravar_json(repo.raiz / "_index.json", {"versao": 1, "mensagens": {}})  # índice desatualizado
    b = capturar(Repositorio(repo.raiz), eml("simples.eml"), origem_imap, CONTAS)
    assert b.status == "duplicada" and b.diretorio == a.diretorio


def test_sem_message_id_usa_hash(repo, origem_imap):
    r = capturar(repo, eml("sem_message_id.eml"), origem_imap, CONTAS)
    meta = ler_metadata(r.diretorio)
    assert meta.message_id is None
    assert meta.chave_dedup.tipo == "hash_cabecalhos"
    assert meta.thread.tipo == "encaminhamento" and meta.thread.metodo == "assunto"
    assert capturar(repo, eml("sem_message_id.eml"), origem_imap, CONTAS).status == "duplicada"


def test_enviada(repo, origem_imap):
    r = capturar(repo, eml("enviada.eml"), origem_imap, CONTAS)
    meta = ler_metadata(r.diretorio)
    assert meta.direcao == "enviada"
    # enviada: <conta>/.enviadas/<primeiro destinatário>/<mensagem>
    rel = r.diretorio.relative_to(repo.raiz).parts[:-1]
    assert rel == ("voce@gmail.com", ".enviadas", "cliente@cliente.com")
    assert meta.thread.tipo == "nova"
    assert list(repo.mensagens()) == [r.diretorio]
    assert repo.reconstruir_indice() == 1
    assert capturar(repo, eml("enviada.eml"), origem_imap, CONTAS).status == "duplicada"


def test_enviada_sem_to_usa_cc_e_depois_sem_destinatario(repo, origem_imap):
    base = eml("enviada.eml").replace(b"To: cliente@cliente.com", b"Cc: Fulano <F@x.com>")
    assert capturar(repo, base, origem_imap, CONTAS).diretorio.parent.name == "f@x.com"
    linhas = eml("enviada.eml").replace(b"<env1@", b"<env2@").splitlines(keepends=True)
    sem = b"".join(linha for linha in linhas if not linha.startswith(b"To:"))
    assert capturar(repo, sem, origem_imap, CONTAS).diretorio.parent.name == "_sem-destinatario"


def test_malformado_nao_quebra(repo, origem_imap):
    bruto = eml("malformado.eml")
    r = capturar(repo, bruto, origem_imap, CONTAS)
    meta = ler_metadata(r.diretorio)
    assert meta.data is None
    assert r.diretorio.name.startswith("sem-data_")
    assert any("data" in a.mensagem for a in meta.avisos)
    assert (r.diretorio / "original" / "mensagem.eml").read_bytes() == bruto


def test_chave_hash_conteudo_quando_sem_cabecalhos():
    c = chave_dedup(None, "", None, "", b"x")
    assert c.tipo == "hash_conteudo"
    assert id_de(c) == id_de(chave_dedup(None, "", None, "", b"x"))
    assert id_de(c) != id_de(chave_dedup(None, "", None, "", b"y"))


def test_thread_por_cabecalho():
    assert thread_por_cabecalho(None, [], "Assunto").tipo == "nova"
    assert thread_por_cabecalho(None, [], "Res: oi").tipo == "resposta"
    t = thread_por_cabecalho(None, ["<a@x>", "<b@x>"], "oi")
    assert (t.tipo, t.pai_message_id, t.metodo) == ("resposta", "<b@x>", "cabecalho")


def test_indice_persistido_e_reconstruido(repo, origem_imap):
    r = capturar(repo, eml("simples.eml"), origem_imap, CONTAS)
    repo.salvar_indice()
    assert Repositorio(repo.raiz).caminho(r.id) == r.diretorio
    (repo.raiz / "_index.json").unlink()
    novo = Repositorio(repo.raiz)
    assert len(novo) == 1 and novo.caminho(r.id) == r.diretorio


def test_reorganizar_move_enviada_antiga(repo, origem_imap):
    from email_organizer.captura import reorganizar

    r = capturar(repo, eml("enviada.eml"), origem_imap, CONTAS)
    antigo = repo.raiz / "voce@gmail.com" / r.diretorio.name
    r.diretorio.rename(antigo)  # layout antigo: <remetente>/<mensagem>, sem pasta da conta
    shutil.rmtree(repo.raiz / "voce@gmail.com" / ".enviadas")
    recebida = capturar(repo, eml("simples.eml"), origem_imap, CONTAS).diretorio
    sem_conta = repo.raiz / "joao@email.com" / recebida.name
    sem_conta.parent.mkdir()
    recebida.rename(sem_conta)  # layout antigo
    recebida.parent.rmdir()

    movidos = reorganizar(repo, CONTAS)

    assert sorted(movidos) == sorted([(antigo, r.diretorio), (sem_conta, recebida)])
    assert sorted(repo.mensagens()) == sorted([r.diretorio, recebida])
    assert not (repo.raiz / "joao@email.com").exists()
    assert repo.caminho(r.id) == r.diretorio
    assert reorganizar(repo, CONTAS) == []
