import { useState, type FormEvent } from 'react';

type RegistrationForm = {
  organizationName: string;
  administratorName: string;
  email: string;
  password: string;
};

const emptyForm: RegistrationForm = {
  organizationName: '',
  administratorName: '',
  email: '',
  password: '',
};

export default function App() {
  const [form, setForm] = useState(emptyForm);
  const [notice, setNotice] = useState('');

  function updateField(field: keyof RegistrationForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setNotice('');
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice('O formulário está pronto. O cadastro será enviado quando a integração com a API estiver disponível.');
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

      <section className="form-panel" aria-labelledby="form-title">
        <div className="form-heading">
          <p className="eyebrow">Comece por aqui</p>
          <h2 id="form-title">Crie sua conta</h2>
          <p>Configure sua rede e convide sua equipe depois.</p>
        </div>

        <div className="trial-note" role="note">
          <span className="trial-icon" aria-hidden="true">30</span>
          <span><strong>30 dias grátis</strong><br />O período começa após confirmar seu e-mail. Sem cartão para iniciar.</span>
        </div>

        <form className="registration-form" onSubmit={handleSubmit}>
          <label htmlFor="organizationName">Nome da rede</label>
          <input
            autoComplete="organization"
            id="organizationName"
            name="organizationName"
            onChange={(event) => updateField('organizationName', event.target.value)}
            placeholder="Ex.: Mercados Horizonte"
            required
            value={form.organizationName}
          />

          <label htmlFor="administratorName">Seu nome</label>
          <input
            autoComplete="name"
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
            id="password"
            name="password"
            onChange={(event) => updateField('password', event.target.value)}
            placeholder="Crie uma senha"
            required
            type="password"
            value={form.password}
          />
          <p className="field-hint">Você confirma seu e-mail antes de acessar a plataforma.</p>

          <button className="submit-button" type="submit">Criar conta de teste</button>
          {notice && <p className="form-notice" role="status">{notice}</p>}
        </form>

        <p className="login-prompt">Já tem uma conta? <a href="/login">Entrar</a></p>
        <p className="legal-copy">Ao continuar, você concorda com os termos de uso e a política de privacidade.</p>
      </section>
    </main>
  );
}
