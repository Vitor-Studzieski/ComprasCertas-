import { useState, type FormEvent } from 'react';

type RegistrationForm = {
  organizationName: string;
  administratorName: string;
  email: string;
  password: string;
};

type RegistrationStatus = 'editing' | 'submitting' | 'error' | 'pending-confirmation';

type ScreenState = {
  status: RegistrationStatus;
  preview: boolean;
};

const emptyForm: RegistrationForm = {
  organizationName: '',
  administratorName: '',
  email: '',
  password: '',
};

function getInitialScreen(): ScreenState {
  // URL previews let the UI states be reviewed without submitting or simulating a response.
  const preview = new URLSearchParams(window.location.search).get('preview');

  switch (preview) {
    case 'sending':
      return { status: 'submitting', preview: true };
    case 'error':
      return { status: 'error', preview: true };
    case 'confirmation':
      return { status: 'pending-confirmation', preview: true };
    default:
      return { status: 'editing', preview: false };
  }
}

function PreviewNotice({ status }: { status: RegistrationStatus }) {
  const message = status === 'submitting'
    ? 'Prévia visual do envio. Nenhuma chamada à API está sendo feita.'
    : status === 'pending-confirmation'
      ? 'Prévia visual da confirmação pendente. Nenhum e-mail foi enviado.'
      : 'Prévia visual do erro. Nenhum dado foi enviado.';

  return <p className="preview-note" role="note">{message}</p>;
}

function PendingConfirmation({ preview }: { preview: boolean }) {
  return (
    <div className="confirmation-state">
      <div className="confirmation-symbol" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <path d="M4 6.75h16v10.5H4z" />
          <path d="m4.5 7.25 7.5 6 7.5-6" />
        </svg>
      </div>
      <p className="eyebrow">Cadastro CompraCerta</p>
      <h2 id="confirmation-title">Confirme seu e-mail</h2>
      <p className="confirmation-copy">
        Depois que o cadastro for conectado e enviado, a pessoa administradora receberá um link
        para confirmar o endereço de e-mail.
      </p>
      <div className="trial-note" role="note">
        <span className="trial-icon" aria-hidden="true">30</span>
        <span>
          <strong>30 dias grátis</strong><br />
          O teste começa após a confirmação do e-mail. Não é necessário informar dados de pagamento.
        </span>
      </div>
      {preview && <PreviewNotice status="pending-confirmation" />}
      <a className="return-link" href="/">Voltar ao cadastro</a>
    </div>
  );
}

export default function App() {
  const [form, setForm] = useState(emptyForm);
  const [screen, setScreen] = useState<ScreenState>(getInitialScreen);
  const isSubmitting = screen.status === 'submitting';

  function updateField(field: keyof RegistrationForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setScreen({ status: 'editing', preview: false });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setScreen({ status: 'error', preview: false });
  }

  return (
    <main className="page-shell">
      <section className="intro-panel" aria-labelledby="intro-title">
        <a className="brand" href="/" aria-label="CompraCerta, início">
          <span className="brand-mark" aria-hidden="true">C</span>
          <span>Compra<span className="brand-accent">Certa</span></span>
        </a>

        <div className="intro-copy">
          <p className="eyebrow">Estoque com mais previsibilidade</p>
          <h1 id="intro-title">Boas compras começam com boas informações.</h1>
          <p className="intro-description">
            Organize produtos, fornecedores e vendas. Receba sugestões de reposição
            com os motivos explicados para sua equipe decidir com segurança.
          </p>
          <ul className="feature-list">
            <li><span aria-hidden="true">↗</span>Antecipe riscos de falta de produtos</li>
            <li><span aria-hidden="true">◎</span>Compare prazo e custo dos fornecedores</li>
            <li><span aria-hidden="true">✓</span>Revise cada recomendação antes de comprar</li>
          </ul>
        </div>
        <p className="intro-footnote">Feito para mercados de bairro e lojas de conveniência.</p>
      </section>

      <section className="form-panel" aria-labelledby={screen.status === 'pending-confirmation' ? 'confirmation-title' : 'form-title'}>
        {screen.status === 'pending-confirmation' ? (
          <PendingConfirmation preview={screen.preview} />
        ) : (
          <>
            <div className="form-heading">
              <p className="eyebrow">Comece por aqui</p>
              <h2 id="form-title">Crie sua conta</h2>
              <p>Configure sua rede e convide sua equipe depois.</p>
            </div>

            {screen.preview && <PreviewNotice status={screen.status} />}

            <div className="trial-note" role="note">
              <span className="trial-icon" aria-hidden="true">30</span>
              <span>
                <strong>30 dias grátis</strong><br />
                O teste começa após confirmar seu e-mail. Não é necessário informar dados de pagamento.
              </span>
            </div>

            <form className="registration-form" onSubmit={handleSubmit} aria-busy={isSubmitting}>
              <label htmlFor="organizationName">Nome da rede</label>
              <input
                autoComplete="organization"
                disabled={isSubmitting}
                id="organizationName"
                name="organizationName"
                onChange={(event) => updateField('organizationName', event.target.value)}
                placeholder="Ex.: Mercados Horizonte"
                required
                value={form.organizationName}
              />

              <label htmlFor="administratorName">Nome do administrador</label>
              <input
                autoComplete="name"
                disabled={isSubmitting}
                id="administratorName"
                name="administratorName"
                onChange={(event) => updateField('administratorName', event.target.value)}
                placeholder="Nome da pessoa administradora"
                required
                value={form.administratorName}
              />

              <label htmlFor="email">E-mail</label>
              <input
                autoComplete="email"
                disabled={isSubmitting}
                id="email"
                name="email"
                onChange={(event) => updateField('email', event.target.value)}
                placeholder="voce@sualoja.com.br"
                required
                type="email"
                value={form.email}
              />

              <label htmlFor="password">Senha</label>
              <input
                autoComplete="new-password"
                disabled={isSubmitting}
                id="password"
                name="password"
                onChange={(event) => updateField('password', event.target.value)}
                placeholder="Crie uma senha"
                required
                type="password"
                value={form.password}
              />
              <p className="field-hint">Você confirma seu e-mail antes de acessar a plataforma.</p>

              <button className="submit-button" disabled={isSubmitting} type="submit">
                {isSubmitting && <span className="spinner" aria-hidden="true" />}
                {isSubmitting ? 'Enviando...' : 'Criar conta de teste'}
              </button>
              {isSubmitting && (
                <p className="sending-status" role="status">
                  Prévia visual: o envio real aguarda a integração com a API.
                </p>
              )}
              {screen.status === 'error' && (
                <p className="form-error" role="alert">
                  O cadastro ainda não está conectado à API. Nenhum dado foi enviado.
                </p>
              )}
            </form>

            <p className="login-prompt">
              Já tem uma conta? <span>A entrada estará disponível em breve.</span>
            </p>
            <p className="legal-copy">
              Os termos de uso e a política de privacidade serão apresentados antes da conclusão do cadastro.
            </p>
          </>
        )}
      </section>
    </main>
  );
}
