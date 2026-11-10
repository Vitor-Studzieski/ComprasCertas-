# CompraCerta

Plataforma SaaS para pequenas redes de mercados e lojas de conveniência administrarem estoque, fornecedores e compras. O CompraCerta explica suas recomendações de reposição e deixa a aprovação com a pessoa responsável.

## Estado do projeto

Atualizado em 10 de novembro de 2026. A base de desenvolvimento e a identidade online avançam em branches/worktrees separados. O PR #2 atualiza o OpenAPI com calendário, respostas 404 e o campo `source`; os PRs #8 (backend) e #9 (frontend) estão em draft e dependem das entregas de identidade.

Nesta fatia, o onboarding de inventário é temporariamente restrito a administradores. Um administrador lança a contagem inicial com auditoria; vendas e operações de estoque ficam bloqueadas até a loja ter `timezone` e `operationalDayStart` efetivos. Nenhum fluxo foi testado e o produto ainda não está operacional.

## Direção técnica

- Backend modular em Django/Python e API REST.
- PostgreSQL compartilhado, com isolamento por organização na aplicação e no banco.
- Frontend React com TypeScript.
- Aplicativo Windows com Tauri, armazenamento local SQLite criptografado e sincronização offline.
- Regras determinísticas de estoque e compras como fonte de verdade; recursos de IA entram depois e usam os serviços autorizados do backend.

## Documentação

- [Arquitetura, escopo e critérios de aceite](docs/arquitetura.md)
- [Papéis, worktrees e fluxo multiagentes](docs/estrutura-multiagentes.md)

## Desenvolvimento

`main` é a branch estável. Cada tarefa deve ser desenvolvida em uma branch e worktree próprios e integrada por pull request. Consulte [CONTRIBUTING.md](CONTRIBUTING.md) antes de iniciar uma frente. Os ciclos iniciais são online; Tauri/offline e agentes internos de IA virão depois que os contratos centrais estiverem estáveis.

