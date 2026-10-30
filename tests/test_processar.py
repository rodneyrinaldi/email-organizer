import json
from pathlib import Path

from conftest import eml
from email_organizer.config import Config, Ollama, Opcoes
from email_organizer.fontes import MensagemBruta
from email_organizer.modelos import Origem
from email_organizer.processar import processar
from email_organizer.repositorio import Repositorio, ler_metadata


class LeitorFalso:
    def __init__(self, pastas: dict[str, list[str]]) -> None:
        self.pastas = pastas
        self.registrados: list[Origem] = []
        self.fechado = False

    def listar_pastas(self):
        return list(self.pastas)

    def mensagens(self, pastas):
        for p in pastas:
            for nome in self.pastas[p]:
                if nome == "ERRO":
                    yield MensagemBruta(b"", Origem(tipo="pst", arquivo="x.pst", pasta=p))
                    continue
                yield MensagemBruta(eml(nome), Origem(tipo="pst", arquivo="x.pst", pasta=p))

    def registrar(self, origem):
        self.registrados.append(origem)

    def fechar(self):
        self.fechado = True


def _config(pastas: list) -> Config:
    return Config.model_validate(
        {
            "contas_proprias": ["voce@gmail.com"],
            "ollama": {"habilitado": False},
            "pst": [{"ativo": True, "nome": "pst", "caminho": "/x.pst", "pastas": pastas}],
        }
    )


def test_processar_filtra_captura_e_reporta(tmp_path: Path):
    leitor = LeitorFalso({"Inbox": ["simples.eml", "com_anexo.eml", "simples.eml", "enviada.eml"]})
    config = _config([{"nome": "Inbox", "filtro": {"excluir_remetente": ["mariana"]}}])
    rel = processar(config, tmp_path, gerar=False, abrir=lambda *_: leitor)

    t = rel.total
    assert (t.lidas, t.novas, t.duplicadas, t.filtradas, t.erros) == (4, 2, 1, 1, 0)
    assert leitor.fechado and len(leitor.registrados) == 3

    repo = Repositorio(tmp_path)
    dirs = list(repo.mensagens())
    assert len(dirs) == 2
    meta = ler_metadata(dirs[0])
    assert meta.origem.filtro == [{"excluir_remetente": ["mariana"]}]

    dir_job = tmp_path / "_jobs" / rel.job
    assert (
        json.loads((dir_job / "relatorio.json").read_text(encoding="utf-8"))["total"]["novas"] == 2
    )
    assert "| **Total** | 4 | 2 | 1 | 1 | 0 |" in (dir_job / "relatorio.md").read_text(
        encoding="utf-8"
    )
    assert (dir_job / "log.txt").exists()


def test_processar_sem_pastas_configuradas_lista_todas(tmp_path: Path):
    leitor = LeitorFalso({"A": ["simples.eml"], "B": ["enviada.eml"]})
    rel = processar(_config([]), tmp_path, gerar=False, abrir=lambda *_: leitor)
    assert rel.total.novas == 2 and set(rel.por_pasta) == {"pst / A", "pst / B"}


def test_fonte_que_falha_ao_abrir_nao_derruba(tmp_path: Path):
    def abrir(*_):
        raise FileNotFoundError("PST travado")

    rel = processar(_config([]), tmp_path, gerar=False, abrir=abrir)
    assert rel.erros and "PST travado" in rel.erros[0]["erro"]


class ClienteFalso:
    modelo = "falso"

    def __init__(self) -> None:
        self.chamadas = 0

    def json(self, sistema, usuario, esquema):
        self.chamadas += 1
        return json.dumps({"categoria": "Contratos", "confianca": 0.8, "tipo": "nova"})


def test_processar_classifica_novas_e_nao_repete(tmp_path: Path):
    config = _config([]).model_copy(
        update={"ollama": Ollama(), "categorias": ["Contratos", "Outros"]}
    )
    leitor = LeitorFalso({"Inbox": ["simples.eml", "enviada.eml", "simples.eml"]})
    cliente = ClienteFalso()

    rel = processar(config, tmp_path, gerar=False, abrir=lambda *_: leitor, cliente_ia=cliente)

    assert rel.classificacao == {"ok": 2, "pendente": 0, "erro": 0}
    for d in Repositorio(tmp_path).mensagens():
        assert ler_metadata(d).classificacao.categoria == "Contratos"
    rel = processar(config, tmp_path, gerar=False, abrir=lambda *_: leitor, cliente_ia=cliente)
    assert cliente.chamadas == 2 and rel.classificacao == {}


def test_artefatos_uma_vez_por_diretorio_no_job(tmp_path: Path, monkeypatch):
    """Duplicata no mesmo job não reenvia o diretório: dois workers no mesmo dir corrompem."""
    from email_organizer import processar as mod

    chamadas: list[str] = []
    monkeypatch.setattr(mod, "_gerar_em_processo", lambda d, *_: chamadas.append(d) or [])
    config = _config([]).model_copy(update={"opcoes": Opcoes(trabalhadores=1)})
    leitor = LeitorFalso({"Inbox": ["simples.eml", "simples.eml", "enviada.eml"]})

    processar(config, tmp_path, abrir=lambda *_: leitor)

    assert len(chamadas) == 2 and len(set(chamadas)) == 2
