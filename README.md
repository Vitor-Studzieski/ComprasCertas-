# CompraCerta

Plataforma SaaS para pequenas redes de mercados e lojas de conveniência administrarem estoque, fornecedores e compras. O CompraCerta explica suas recomendações de reposição e deixa a aprovação com a pessoa responsável.

## Estado do projeto

A base de desenvolvimento foi inicializada e as primeiras frentes começaram em branches/worktrees separados: scaffold Django, contrato OpenAPI draft e scaffold React com cadastro ainda sem conexão à API. Nenhum fluxo de produto está pronto para operação; a arquitetura e o processo multiagente estão documentados.

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

