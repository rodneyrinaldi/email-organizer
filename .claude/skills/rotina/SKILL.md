---
name: rotina
description: Cria uma nova rotina de enriquecimento sobre o repositório documental (ex. OCR, entidades, resumo, dossiê, cronologia). Use quando o usuário pedir uma nova funcionalidade que processe mensagens já capturadas.
---

# Nova rotina de enriquecimento

Toda rotina segue: ler `metadata.json` → enriquecer `metadata.json` → gerar novos artefatos. Nunca reacessa a origem.

1. Definir a chave própria da rotina no JSON (ex.: `entidades`, `resumo`) e a etapa em `pipeline.<nome>`.
2. Documentar a chave em `specs/metadata.md` e incrementar `schema_version` se alterar estrutura existente.
3. Implementar em `src/email_organizer/rotinas/<nome>.py` com a assinatura:
   `def executar(dir_mensagem: Path, config: Config) -> ResultadoEtapa`
4. Usar o repositório (`repositorio/`) para ler/gravar o JSON de forma atômica; só tocar nas próprias chaves.
5. Ser idempotente: rodar duas vezes não duplica nada.
6. Artefatos novos ficam dentro do diretório da mensagem (ou em `/output/_<rotina>/` se forem agregados, ex. dossiê).
7. Registrar falhas em `avisos` sem abortar.
8. Testes com diretório de mensagem fixture; expor na UI como etapa de reprocessamento.
