---
name: spec
description: Atualiza as specs do email-organizer quando o usuário toma uma decisão, muda requisito ou quando o comportamento implementado diverge da spec.
---

# Atualizar spec

1. Identificar o arquivo em `specs/` afetado (índice em `specs/README.md`).
2. Editar de forma enxuta; specs são curtas e diretas.
3. Decisão arquitetural nova → nova entrada ADR em `specs/decisoes.md` (não editar ADRs antigos; marcar como substituído).
4. Mudança no `metadata.json` → atualizar `specs/metadata.md` e `schema_version`.
5. Mudança de escopo/ordem → `specs/ondas.md`.
6. Confirmar ao usuário em uma linha o que mudou.
