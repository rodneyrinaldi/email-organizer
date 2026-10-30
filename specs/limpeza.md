# Rotina: Limpeza e lixeira

Primeira rotina sobre o acervo já processado (menu **Rotinas** da UI). Módulo `limpeza.py`.

## Limpeza (`/limpeza`)
- Árvore: conta → `.enviadas` → destinatário | remetente → mensagens (mensagens carregadas ao abrir a pasta).
- Cada nó tem checkbox e contagem de mensagens. Marcar uma pasta cobre tudo abaixo (filhos marcados e desabilitados).
- **Mover para a lixeira**: move cada seleção para `.lixeira/<AAAA-MM-DD_HH-MM-SS>/<caminho relativo>` (um lote por ação) e grava `_lote.json` (`criado_em`, `itens[{caminho, mensagens}]`). Seleção dentro de outra selecionada é ignorada. Pastas que ficam vazias são removidas. Índice reconstruído.
- Só aceita caminhos que são mensagens ou pastas acima delas (nada de `_jobs`, `config.json`, `.lixeira`, `..`).

## Lixeira (`/lixeira`, `<raiz>/.lixeira/`)
- Lista lotes (mais recente primeiro) com itens e contagem.
- **Restaurar** item: volta ao caminho original; falha se já existir algo lá (ex.: recapturada). Índice reconstruído.
- **Excluir definitivamente** (lote) e **Esvaziar lixeira** (tudo): apagam de verdade, com confirmação no navegador.
- `.lixeira/` fica fora do acervo: não entra no índice, na árvore nem no reprocessamento. `reiniciar` também a apaga.

## Regras
- Nunca toca na origem (caixa postal/PST); só move/apaga diretórios do output.
- Bloqueada enquanto houver job em andamento.
- Mensagem excluída sai do índice: IMAP/Graph não a recapturam (estado incremental); PST e `--todas` sim.
