# Contribuindo com CompraCerta

## Fluxo de trabalho

1. Consulte `docs/arquitetura.md` e `docs/estrutura-multiagentes.md` antes de iniciar uma tarefa.
2. Use uma branch e um worktree por tarefa. Não faça implementação diretamente em `main`.
3. Declare arquivos e contratos sob responsabilidade da tarefa. Combine mudanças que afetem API, modelos compartilhados ou migrações antes de editar.
4. Abra um pull request para `main` com objetivo, escopo, critérios de aceite, verificações realizadas e riscos conhecidos.
5. Não altere uma decisão de produto confirmada sem atualizar a documentação e obter a decisão necessária.

## Limites técnicos

- O backend é a fonte de verdade para estoque, orçamento, compras, permissões e transações.
- A camada REST expõe os serviços de domínio; o frontend não duplica suas regras.
- Toda operação de cliente é limitada à organização, papel e lojas autorizadas.
- Recursos de IA usam ferramentas do backend, não escrevem diretamente no banco e não aprovam pedidos no primeiro ciclo.
- Operações offline devem ser idempotentes, preservar conflitos e sincronizar sem descartar a outbox.

## Pull requests

- Mantenha cada pull request focado em uma frente.
- Descreva mudanças de contrato e impacto em worktrees dependentes.
- Informe verificações executadas e itens que ainda não puderam ser verificados.
- Não inclua segredos, `.env` real, bancos locais, caches ou artefatos de build.

