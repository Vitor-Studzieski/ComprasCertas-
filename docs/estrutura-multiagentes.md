# Estrutura multiagentes do CompraCerta

**Status:** estrutura de desenvolvimento adotada. `main` contém o bootstrap do repositório; a primeira rodada de implementação começou em worktrees e branches separados. O produto ainda não está operacional.

Este documento complementa [a arquitetura do CompraCerta](arquitetura.md). A equipe de agentes aqui descrita trabalha no código do produto em frentes isoladas. Ela é diferente dos agentes de IA que poderão existir dentro do SaaS.

## Princípios de trabalho

- Cada tarefa de implementação tem um responsável, uma branch e um worktree próprios.
- `main` permanece estável. Alterações entram por pull request após revisão e integração.
- O agente mantém sua responsabilidade dentro dos caminhos de arquivo combinados; mudanças de contrato ou de propriedade compartilhada precisam ser combinadas antes.
- As regras de estoque, orçamento, compras, permissões e transações têm uma única fonte de verdade no backend. A interface e os agentes de IA não recalculam nem sobrescrevem essas regras.
- Nenhum agente pode alterar decisões de produto já aprovadas sem registrar a proposta em documentação e obter a decisão correspondente.
- Agentes não trabalham simultaneamente no mesmo arquivo de migração, contrato de API ou componente compartilhado.

## Papéis de coordenação

| Papel | Responsabilidade | Limite |
|---|---|---|
| `project_organizer` | Mantém escopo, backlog, critérios de aceite, dependências e ordem das tarefas; encaminha cada tarefa ao agente responsável. | Não decide sozinho mudanças de produto e não implementa frentes em paralelo sem um escopo explícito. |
| `project_engineer` | Mantém contratos técnicos, limites entre módulos, decisões de arquitetura e integração entre pull requests; revisa isolamento multi-tenant, transações e sincronização. | Não altera regras de negócio aprovadas nem substitui a aprovação do usuário para decisões de produto. |
| Orquestrador da tarefa | Mantém o plano da rodada, distribui tarefas compatíveis, resolve conflitos de integração e comunica andamento. | Não permite que dois agentes escrevam na mesma área ao mesmo tempo; encaminha decisões de produto ao usuário. |

`project_organizer` e `project_engineer` atuam na coordenação desta rodada. As frentes especializadas abaixo são ativadas conforme as dependências do trabalho.

## Agentes de implementação

Os caminhos são limites propostos para evitar sobreposição; serão conferidos com a estrutura real do repositório antes da primeira tarefa de código.

| Agente | Área de responsabilidade | Caminhos propostos | Dependências principais |
|---|---|---|---|
| Identidade e acesso | Cadastro, confirmação de e-mail, login, sessões, MFA, memberships, convites, papéis, acesso por loja e autorização de instalações. | `backend/apps/identity/` | Contratos de tenant, papéis e escopo aprovados; coordena os endpoints com API. |
| Domínio e compras | Catálogo, fornecedores, vendas, movimentos e saldo de estoque, políticas de reposição, cálculo determinístico, orçamento, pedidos e recebimentos. | `backend/apps/catalog/`, `backend/apps/inventory/`, `backend/apps/procurement/` | Regras registradas em `docs/arquitetura.md`; serviços de domínio não dependem da interface web. |
| API e contratos | Endpoints REST, serializers, validação de entrada/saída, erros, versionamento e especificação OpenAPI. | `backend/api/`, `docs/api/` | Consome serviços de domínio e identidade; publica contratos estáveis para frontend e desktop. Não implementa cálculos de estoque ou autorização paralelos. |
| Frontend | Aplicação React/TypeScript, navegação, formulários, tabelas, estados de erro e integração com contratos publicados pela API. | `frontend/` | Consome API/OpenAPI; não calcula valores oficiais de estoque, orçamento ou pedido. |
| Desktop, offline e sincronização | Shell Tauri, SQLite local, criptografia, outbox, idempotência, cache por organização, tray, backups e sincronização. | `desktop/`, `packages/offline-client/` | Exige contrato de sincronização, identidade e escopo de dispositivo definidos; não compartilha uma base operacional entre computadores. |
| Agentes internos de IA | Frente de desenvolvimento responsável por desenhar e implementar os agentes de IA para análise de estoque/demanda, comparação de fornecedores e cenários de compra, coordenados pelo assistente. | `backend/apps/assistant/` | Só começa depois do serviço determinístico de recomendação e dos contratos de ferramenta. Os agentes usam ferramentas permitidas do backend; não gravam diretamente no banco, não calculam preço/saldo/orçamento e não aprovam pedidos no primeiro ciclo. |
| Qualidade e segurança | Revisão de critérios de aceite, isolamento entre organizações, autorização por loja, trilhas de auditoria, concorrência e comportamento offline; prepara e executa verificações quando a etapa de implementação for autorizada. | `tests/`, `docs/qualidade/` | Atua como revisor independente; não altera código de outra frente sem combinar com seu responsável. |

## Limites entre backend, API e interface

- **Domínio:** valida e executa as regras de negócio em transações; mantém os dados e resultados oficiais.
- **API:** converte requisições autenticadas em chamadas aos serviços de domínio e transforma os resultados em respostas conforme OpenAPI. A autorização usa identidade, organização, papel e lojas do servidor.
- **Frontend:** apresenta os dados e permite ações autorizadas; não toma decisões definitivas de compra nem mantém regras duplicadas.
- **Assistente de IA:** interpreta perguntas, chama ferramentas autorizadas e explica resultados calculados pelo backend. As permissões são verificadas novamente no servidor em toda chamada.
- **Desktop offline:** registra operações identificadas por UUID e escopo local autorizado; a sincronização valida idempotência e conflitos no servidor. Aprovação de pedido permanece online no primeiro ciclo.

## Sequência de trabalho

1. **Preparação da base — concluída:** o repositório remoto recebeu o bootstrap em `main`; convenções de branches/worktrees e separação de frentes foram registradas.
2. **Fundação online — em andamento:** a branch `feat/backend-foundation` contém o scaffold Django/PostgreSQL e o endpoint público de saúde; `feat/api-contracts` contém um rascunho OpenAPI para cadastro, confirmação de e-mail, login e MFA; `feat/frontend-foundation` contém o scaffold React/Vite e a primeira tela de cadastro ainda sem integração com a API. As três frentes seguem em branches/worktrees separados; nenhuma representa fluxo funcional completo.
3. **Fluxo de estoque e compra:** domínio e API entregam cadastro/importação, vendas, saldo, recomendação explicável, orçamento e pedido; o frontend integra esse fluxo e os critérios de aceite.
4. **Desktop e offline:** iniciar quando contratos de autenticação de dispositivo, outbox e sincronização estiverem definidos. Tauri/SQLite, backups, resolução de conflitos e sincronização não devem ser implementados em paralelo com contratos ainda instáveis.
5. **IA de apoio:** iniciar depois que a recomendação determinística estiver disponível como serviço e ferramenta autorizada. A IA explica e prepara; o backend valida regras e a pessoa responsável aprova.
6. **Integração e liberação:** `project_engineer` revisa dependências e segurança; `project_organizer` verifica o escopo e os critérios de aceite; cada frente entra por pull request.

## Worktrees e branches

- Criar um worktree por tarefa ativa, vinculado a uma branch de trabalho; não abrir duas frentes sobre a mesma branch.
- Padrão sugerido: `feat/<area>/<tarefa>` ou `docs/<area>/<tarefa>`, por exemplo `feat/identity/signup` e `feat/api/inventory-contract`.
- Manter os worktrees sob um diretório de trabalho separado, por exemplo `../worktrees/comprascerta-<area>`, sem editar arquivos de outro worktree.
- Cada tarefa registra: objetivo, responsável, arquivos permitidos, dependências, contrato esperado, critério de aceite e o que deve ser devolvido no pull request.
- O agente publica um resumo do que mudou, decisões tomadas, riscos e verificações feitas. O orquestrador coordena a integração; `main` só recebe mudanças aprovadas.
- Neste ambiente, até três agentes especialistas podem executar em paralelo com o agente principal. Os papéis desta proposta não significam que todos estarão ativos ao mesmo tempo; cada rodada usa no máximo três worktrees de implementação, escolhidos pela ordem de dependência.

## Modelo de tarefa para delegação

```text
Tarefa:
Responsável:
Branch e worktree:
Objetivo:
Escopo incluído:
Arquivos/áreas sob responsabilidade:
Fora do escopo:
Contratos e dependências:
Critérios de aceite:
Verificações permitidas:
Resultado esperado no pull request:
```

## Situação atual

O repositório GitHub informado já está inicializado, com `main` estável e commits de bootstrap. Existem worktrees para backend, contrato da API e frontend, cada uma em branch própria. Os commits de fundação ainda precisam passar por revisão e integração via pull request. O próximo trabalho online é fechar as decisões técnicas de identidade/MFA e ligar o cadastro à API; depois vêm o fluxo de estoque e compras. Tauri/offline e agentes internos de IA permanecem em ciclos posteriores, conforme a sequência acima.
