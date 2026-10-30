---
name: onda
description: Executa uma onda do roadmap do email-organizer (specs/ondas.md). Use quando o usuário autorizar ou pedir para iniciar/continuar uma onda de desenvolvimento.
---

# Executar onda

1. Ler `specs/ondas.md` e confirmar qual onda está autorizada. Sem autorização explícita → parar e perguntar.
2. Ler as specs relevantes da onda e `specs/decisoes.md`.
3. Se algo na spec estiver ambíguo ou conflitante, perguntar ao usuário (curto) antes de codar.
4. Escrever testes primeiro (pytest, fixtures `.eml` em `tests/fixtures/`).
5. Implementar a solução mais simples que passe nos testes e respeite as regras do `CLAUDE.md`.
6. Rodar `.venv/Scripts/pytest -q` e `.venv/Scripts/ruff check .`; corrigir até verde.
7. Atualizar specs se o comportamento mudou; nova decisão → `specs/decisoes.md`.
8. Marcar a onda como ✅ em `specs/ondas.md`.
9. Commit com mensagem `onda N: <resumo>`.
10. Reportar ao usuário em poucas linhas: o que foi feito, o que ficou pendente, próxima onda.
