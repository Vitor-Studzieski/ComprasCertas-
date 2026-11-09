import { useEffect, useRef, useState, type FormEvent } from 'react';
import { apiDelete, apiGet, apiPost, ApiError, refreshCsrfToken } from './api';
import { createWebAuthnCredential, getWebAuthnAssertion } from './webauthn';

type RegistrationForm = {
  organizationName: string;
  administratorName: string;
  email: string;
  password: string;
};

type Screen = 'register' | 'pending' | 'confirming' | 'login' | 'enrollment' | 'mfa' | 'recovery-codes' | 'authenticated';
type LoginResponse = {
  status: 'authenticated' | 'second_factor_required' | 'second_factor_enrollment_required';
  challengeId?: string;
  methods?: Array<'webauthn' | 'recovery_code'>;
};

const emptyForm: RegistrationForm = {
  organizationName: '',
  administratorName: '',
  email: '',
  password: '',
};

function initialScreen(): Screen {
  return window.location.pathname.replace(/\/$/, '') === '/confirm-email' ? 'confirming' : 'register';
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const fieldErrors = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    return fieldErrors || error.message;
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a solicitação.';
}

export default function App() {
  const [screen, setScreen] = useState<Screen>(initialScreen);
  const [registration, setRegistration] = useState(emptyForm);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [challengeId, setChallengeId] = useState('');
  const [methods, setMethods] = useState<LoginResponse['methods']>([]);
  const [recoveryCode, setRecoveryCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [showRecoveryInput, setShowRecoveryInput] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const confirmationStarted = useRef(false);
  const hasInteracted = useRef(false);
  const [loginSessionConflict, setLoginSessionConflict] = useState(false);

  useEffect(() => {
    if (window.location.pathname.replace(/\/$/, '') === '/confirm-email') return;
    let cancelled = false;
    apiGet<{ status: string }>('/sessions/current/')
      .then(() => {
        if (!cancelled && !hasInteracted.current) setScreen('authenticated');
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (screen !== 'confirming' || confirmationStarted.current) return;
    confirmationStarted.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get('token');
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);

    if (!token) {
      setError('O link não contém um código de confirmação. Solicite um novo e-mail.');
      setScreen('pending');
      return;
    }

    let cancelled = false;
    apiPost<{ status: string }>('/email-confirmations/', { token })
      .then(() => {
        if (cancelled) return;
        setNotice('E-mail confirmado. Entre com seu e-mail e senha para configurar o segundo fator.');
        setScreen('login');
      })
      .catch((requestError: unknown) => {
        if (cancelled) return;
        setError(errorMessage(requestError));
        setScreen('pending');
      });

    return () => { cancelled = true; };
  }, [screen]);

  async function submitRegistration(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    hasInteracted.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiPost('/registrations/', {
        organizationName: registration.organizationName,
        administratorName: registration.administratorName,
        email: registration.email,
        password: registration.password,
      });
      setEmail(registration.email);
      setRegistration((current) => ({ ...current, password: '' }));
      setScreen('pending');
      setNotice('Se o endereço puder iniciar o cadastro, enviaremos um link de confirmação.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function resendConfirmation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    hasInteracted.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiPost('/email-confirmations/resend/', { email });
      setNotice('Se houver um cadastro pendente para esse endereço, enviaremos outro link.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    hasInteracted.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    setLoginSessionConflict(false);
    try {
      const result = await apiPost<LoginResponse>('/sessions/', { email, password });
      setPassword('');
      if (result.status === 'authenticated') {
        await refreshCsrfToken();
        setScreen('authenticated');
      } else {
        setChallengeId(result.challengeId ?? '');
        setMethods(result.methods ?? []);
        setShowRecoveryInput(false);
        setScreen(result.status === 'second_factor_enrollment_required' ? 'enrollment' : 'mfa');
      }
    } catch (requestError) {
      setError(errorMessage(requestError));
      if (requestError instanceof ApiError && requestError.status === 409) {
        setLoginSessionConflict(true);
      }
    } finally {
      setBusy(false);
    }
  }

  async function enrollPasskey() {
    setBusy(true);
    setError('');
    try {
      const options = await apiPost<Record<string, unknown>>(
        '/sessions/mfa/enrollment/options/',
        { challengeId },
      );
      const credential = await createWebAuthnCredential(options);
      const result = await apiPost<{ recoveryCodes: string[] }>(
        '/sessions/mfa/enrollment/verify/',
        { challengeId, credential },
      );
      await refreshCsrfToken();
      setRecoveryCodes(result.recoveryCodes);
      setScreen('recovery-codes');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function authenticateWithPasskey() {
    setBusy(true);
    setError('');
    try {
      const options = await apiPost<Record<string, unknown>>(
        '/sessions/mfa/authentication/options/',
        { challengeId },
      );
      const credential = await getWebAuthnAssertion(options);
      await apiPost('/sessions/mfa/authentication/verify/', { challengeId, credential });
      await refreshCsrfToken();
      setScreen('authenticated');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function authenticateWithRecoveryCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await apiPost('/sessions/mfa/recovery-code/', { challengeId, recoveryCode });
      await refreshCsrfToken();
      setRecoveryCode('');
      setScreen('authenticated');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    setError('');
    try {
      await apiDelete('/sessions/current/');
      await refreshCsrfToken();
      setLoginSessionConflict(false);
      setScreen('login');
      setNotice('Você saiu da sua conta.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  function goTo(next: Screen) {
    hasInteracted.current = true;
    setError('');
    setNotice('');
    setScreen(next);
  }

  const panelTitle = {
    register: 'Crie sua conta',
    pending: 'Confirme seu e-mail',
    confirming: 'Confirmando seu e-mail',
    login: 'Entre na sua conta',
    enrollment: 'Proteja sua conta',
    mfa: 'Confirme sua identidade',
    'recovery-codes': 'Guarde seus códigos',
    authenticated: 'Acesso liberado',
  }[screen];

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

      <section className="form-panel" aria-labelledby="panel-title">
        <div className="form-heading">
          <p className="eyebrow">CompraCerta</p>
          <h2 id="panel-title">{panelTitle}</h2>
          {screen === 'register' && <p>Configure sua rede e convide sua equipe depois.</p>}
        </div>

        {notice && <p className="form-notice" role="status">{notice}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}

        {screen === 'register' && (
          <>
            <div className="trial-note" role="note">
              <span className="trial-icon" aria-hidden="true">30</span>
              <span><strong>30 dias grátis</strong><br />O teste começa após confirmar seu e-mail. Não é necessário informar pagamento.</span>
            </div>
            <form className="registration-form" onSubmit={submitRegistration} aria-busy={busy}>
              <label htmlFor="organizationName">Nome da rede</label>
              <input autoComplete="organization" disabled={busy} id="organizationName" minLength={2} maxLength={160} onChange={(event) => setRegistration({ ...registration, organizationName: event.target.value })} required value={registration.organizationName} />
              <label htmlFor="administratorName">Nome do administrador</label>
              <input autoComplete="name" disabled={busy} id="administratorName" minLength={2} maxLength={160} onChange={(event) => setRegistration({ ...registration, administratorName: event.target.value })} required value={registration.administratorName} />
              <label htmlFor="registerEmail">E-mail</label>
              <input autoComplete="email" disabled={busy} id="registerEmail" maxLength={254} onChange={(event) => setRegistration({ ...registration, email: event.target.value })} required type="email" value={registration.email} />
              <label htmlFor="registerPassword">Senha</label>
              <input autoComplete="new-password" disabled={busy} id="registerPassword" maxLength={256} onChange={(event) => setRegistration({ ...registration, password: event.target.value })} required type="password" value={registration.password} />
              <p className="field-hint">Você confirma seu e-mail antes de acessar a plataforma.</p>
              <button className="submit-button" disabled={busy} type="submit">{busy ? 'Enviando...' : 'Criar conta de teste'}</button>
            </form>
            <p className="login-prompt">Já tem uma conta? <button className="text-button" onClick={() => goTo('login')} type="button">Entrar</button></p>
          </>
        )}

        {screen === 'pending' && (
          <div className="confirmation-state">
            <div className="confirmation-symbol" aria-hidden="true">✉</div>
            <p className="confirmation-copy">Enviamos instruções para confirmar o endereço. O teste de 30 dias começa após a confirmação.</p>
            <form className="registration-form" onSubmit={resendConfirmation}>
              <label htmlFor="resendEmail">E-mail</label>
              <input autoComplete="email" disabled={busy} id="resendEmail" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
              <button className="submit-button" disabled={busy} type="submit">{busy ? 'Enviando...' : 'Reenviar confirmação'}</button>
            </form>
            <button className="text-button standalone-link" onClick={() => goTo('login')} type="button">Voltar ao login</button>
          </div>
        )}

        {screen === 'confirming' && <p className="sending-status" role="status">Validando o link de confirmação…</p>}

        {screen === 'login' && (
          <form className="registration-form" onSubmit={submitLogin} aria-busy={busy}>
            <label htmlFor="loginEmail">E-mail</label>
            <input autoComplete="username" disabled={busy} id="loginEmail" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
            <label htmlFor="loginPassword">Senha</label>
            <input autoComplete="current-password" disabled={busy} id="loginPassword" onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
            <button className="submit-button" disabled={busy} type="submit">{busy ? 'Entrando...' : 'Entrar'}</button>
            {loginSessionConflict && <button className="secondary-button" disabled={busy} onClick={signOut} type="button">Encerrar sessão atual</button>}
            <p className="login-prompt">Ainda não tem conta? <button className="text-button" onClick={() => goTo('register')} type="button">Criar conta</button></p>
          </form>
        )}

        {screen === 'enrollment' && (
          <div className="confirmation-state">
            <p className="confirmation-copy">Administradores e pessoas que aprovam operações precisam configurar uma passkey antes de acessar a plataforma.</p>
            <p className="field-hint">Use Windows Hello, uma passkey do dispositivo ou uma chave de segurança compatível.</p>
            <button className="submit-button" disabled={busy} onClick={enrollPasskey} type="button">{busy ? 'Configurando...' : 'Configurar passkey'}</button>
          </div>
        )}

        {screen === 'mfa' && (
          <div className="confirmation-state">
            <p className="confirmation-copy">Confirme sua identidade para concluir a entrada.</p>
            {methods?.includes('webauthn') && <button className="submit-button" disabled={busy} onClick={authenticateWithPasskey} type="button">{busy ? 'Aguardando...' : 'Usar passkey'}</button>}
            {methods?.includes('recovery_code') && !showRecoveryInput && <button className="secondary-button" disabled={busy} onClick={() => { setError(''); setShowRecoveryInput(true); }} type="button">Usar código de recuperação</button>}
            {methods?.includes('recovery_code') && showRecoveryInput && (
              <form className="registration-form recovery-form" onSubmit={authenticateWithRecoveryCode}>
                <label htmlFor="recoveryCode">Código de recuperação</label>
                <input autoComplete="one-time-code" disabled={busy} id="recoveryCode" onChange={(event) => setRecoveryCode(event.target.value)} required value={recoveryCode} />
                <button className="submit-button" disabled={busy} type="submit">{busy ? 'Verificando...' : 'Verificar código'}</button>
              </form>
            )}
            <button className="text-button standalone-link" disabled={busy} onClick={() => goTo('login')} type="button">Cancelar e voltar ao login</button>
          </div>
        )}

        {screen === 'recovery-codes' && (
          <div className="confirmation-state">
            <p className="confirmation-copy">Salve estes códigos agora. Cada código só pode ser usado uma vez e não será exibido novamente.</p>
            <ul className="recovery-codes" aria-label="Códigos de recuperação">
              {recoveryCodes.map((code) => <li key={code}><code>{code}</code></li>)}
            </ul>
            <button className="submit-button" onClick={() => { setRecoveryCodes([]); setScreen('authenticated'); }} type="button">Guardei os códigos, continuar</button>
          </div>
        )}

        {screen === 'authenticated' && (
          <div className="confirmation-state">
            <p className="confirmation-copy">Sua sessão está ativa. O próximo passo é configurar a primeira loja e os produtos da rede.</p>
            <button className="secondary-button" disabled={busy} onClick={signOut} type="button">{busy ? 'Saindo...' : 'Sair da conta'}</button>
          </div>
        )}

        <p className="legal-copy">Os termos de uso e a política de privacidade serão apresentados antes da conclusão do cadastro.</p>
      </section>
    </main>
  );
}
