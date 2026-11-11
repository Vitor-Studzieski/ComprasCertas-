import { useEffect, useRef, useState, type FormEvent } from 'react';
import { apiDelete, apiGet, apiPost, apiPut, ApiError, refreshCsrfToken } from './api';
import { createWebAuthnCredential, getWebAuthnAssertion } from './webauthn';
import OpeningStockWorkspace from './OpeningStockWorkspace';

type RegistrationForm = {
  organizationName: string;
  administratorName: string;
  email: string;
  password: string;
};

type Screen = 'register' | 'pending' | 'confirming' | 'accept-invitation' | 'login' | 'enrollment' | 'mfa' | 'recovery-codes' | 'organization-selection' | 'no-memberships' | 'authenticated';
type OrganizationMembership = {
  id: string;
  name: string;
  role: string;
};
type CurrentSessionResponse = {
  status: 'authenticated' | 'organization_selection_required';
  activeOrganizationId: string | null;
  organizations: OrganizationMembership[];
};
type LoginResponse = {
  status: 'authenticated' | 'second_factor_required' | 'second_factor_enrollment_required';
  challengeId?: string;
  methods?: Array<'webauthn' | 'recovery_code'>;
};
type InvitationPreview = {
  organizationName: string;
  email: string;
  role: string;
  roleName: string;
  storeNames: string[];
  expiresAt: string;
};
type InvitationAcceptResponse = {
  status: 'accepted' | 'second_factor_required' | 'second_factor_enrollment_required';
  challengeId?: string;
  organizationId?: string;
  methods?: Array<'webauthn' | 'recovery_code'>;
};

const emptyForm: RegistrationForm = {
  organizationName: '',
  administratorName: '',
  email: '',
  password: '',
};

function initialScreen(): Screen {
  const path = window.location.pathname.replace(/\/$/, '');
  if (path === '/confirm-email') return 'confirming';
  if (path === '/accept-invitation') return 'accept-invitation';
  return 'register';
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const fieldErrors = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    return `Erro ${error.status}${error.code ? ` (${error.code})` : ''}: ${fieldErrors || error.message}`;
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
  const [organizations, setOrganizations] = useState<OrganizationMembership[]>([]);
  const [activeOrganizationId, setActiveOrganizationId] = useState<string | null>(null);
  const [pendingOrganizationId, setPendingOrganizationId] = useState('');
  const [recoveryCode, setRecoveryCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [showRecoveryInput, setShowRecoveryInput] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const panelHeading = useRef<HTMLHeadingElement>(null);
  const previousScreen = useRef(screen);
  const confirmationStarted = useRef(false);
  const hasInteracted = useRef(false);
  const [loginSessionConflict, setLoginSessionConflict] = useState(false);
  const [invitationToken, setInvitationToken] = useState('');
  const [invitationPreview, setInvitationPreview] = useState<InvitationPreview | null>(null);
  const [invitationFullName, setInvitationFullName] = useState('');
  const [invitationPassword, setInvitationPassword] = useState('');
  const [hasInvitationSession, setHasInvitationSession] = useState(false);
  const [acceptInvitationAfterLogin, setAcceptInvitationAfterLogin] = useState(false);
  const [pendingInvitationOrganizationId, setPendingInvitationOrganizationId] = useState('');

  function applySessionContext(context: CurrentSessionResponse) {
    const activeMembership = context.organizations.find(
      (organization) => organization.id === context.activeOrganizationId,
    );
    setOrganizations(context.organizations);
    setActiveOrganizationId(activeMembership?.id ?? null);
    setPendingOrganizationId(
      activeMembership?.id ?? (context.organizations.length === 1 ? context.organizations[0].id : ''),
    );

    if (context.organizations.length === 0) {
      setScreen('no-memberships');
      return;
    }
    if (context.status === 'organization_selection_required' || !activeMembership) {
      setScreen('organization-selection');
      return;
    }
    setScreen('authenticated');
  }

  async function loadSessionContext() {
    const context = await apiGet<CurrentSessionResponse>('/sessions/current/');
    applySessionContext(context);
    return context;
  }

  async function finishAuthenticatedSession(organizationId = '') {
    await refreshCsrfToken();
    let context = await apiGet<CurrentSessionResponse>('/sessions/current/');
    if (
      organizationId
      && context.organizations.some((organization) => organization.id === organizationId)
      && context.activeOrganizationId !== organizationId
    ) {
      context = await apiPut<CurrentSessionResponse>(
        '/sessions/current/organization/',
        { organizationId },
      );
    }
    setHasInvitationSession(true);
    setAcceptInvitationAfterLogin(false);
    setPendingInvitationOrganizationId('');
    setInvitationPassword('');
    window.history.replaceState(null, '', `${window.location.origin}/`);
    applySessionContext(context);
  }

  async function acceptInvitationToken(account?: { fullName?: string; password?: string }) {
    const result = await apiPost<InvitationAcceptResponse>('/invitations/accept/', {
      token: invitationToken,
      ...(account?.fullName ? { fullName: account.fullName } : {}),
      ...(account?.password ? { password: account.password } : {}),
    });
    if (result.status === 'second_factor_required' || result.status === 'second_factor_enrollment_required') {
      setPendingInvitationOrganizationId(result.organizationId ?? '');
      setChallengeId(result.challengeId ?? '');
      setMethods(result.methods ?? []);
      setShowRecoveryInput(false);
      setAcceptInvitationAfterLogin(false);
      setInvitationPassword('');
      setScreen(result.status === 'second_factor_enrollment_required' ? 'enrollment' : 'mfa');
      return;
    }
    setNotice('Convite aceito. Carregando sua rede…');
    await finishAuthenticatedSession(result.organizationId ?? '');
  }

  async function submitInvitationAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await acceptInvitationToken({ fullName: invitationFullName, password: invitationPassword });
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitExistingAccountInvitation() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await acceptInvitationToken();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (previousScreen.current !== screen) panelHeading.current?.focus();
    previousScreen.current = screen;
  }, [screen]);

  useEffect(() => {
    if (window.location.pathname.replace(/\/$/, '') === '/confirm-email') return;
    let cancelled = false;
    const isInvitationRoute = window.location.pathname.replace(/\/$/, '') === '/accept-invitation';
    apiGet<CurrentSessionResponse>('/sessions/current/')
      .then((context) => {
        if (cancelled || hasInteracted.current) return;
        if (isInvitationRoute) {
          setOrganizations(context.organizations);
          setActiveOrganizationId(context.activeOrganizationId);
          setHasInvitationSession(true);
          return;
        }
        applySessionContext(context);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (screen !== 'accept-invitation' || invitationToken) return;
    const token = new URLSearchParams(window.location.hash.slice(1)).get('token');
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
    if (!token) {
      setError('O link não contém um token de convite. Peça ao administrador para reenviá-lo.');
      return;
    }
    setInvitationToken(token);
    let cancelled = false;
    apiPost<InvitationPreview>('/invitations/preview/', { token })
      .then((preview) => { if (!cancelled) setInvitationPreview(preview); })
      .catch((requestError: unknown) => { if (!cancelled) setError(errorMessage(requestError)); });
    return () => { cancelled = true; };
  }, [invitationToken, screen]);

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
        if (acceptInvitationAfterLogin && invitationToken) {
          await refreshCsrfToken();
          await acceptInvitationToken();
        } else {
          await refreshCsrfToken();
          await loadSessionContext();
        }
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
      if (acceptInvitationAfterLogin && invitationToken) {
        await refreshCsrfToken();
        await acceptInvitationToken();
      } else {
        await finishAuthenticatedSession(pendingInvitationOrganizationId);
      }
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
      setRecoveryCode('');
      if (acceptInvitationAfterLogin && invitationToken) {
        await refreshCsrfToken();
        await acceptInvitationToken();
      } else {
        await finishAuthenticatedSession(pendingInvitationOrganizationId);
      }
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
      setOrganizations([]);
      setActiveOrganizationId(null);
      setPendingOrganizationId('');
      setHasInvitationSession(false);
      setScreen('login');
      setNotice('Você saiu da sua conta.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function selectOrganization(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const context = await apiPut<CurrentSessionResponse>(
        '/sessions/current/organization/',
        { organizationId: pendingOrganizationId },
      );
      applySessionContext(context);
      if (context.activeOrganizationId) {
        const selected = context.organizations.find((organization) => organization.id === context.activeOrganizationId);
        if (selected) setNotice(`Rede ativa: ${selected.name}.`);
      }
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function continueAfterRecoveryCodes() {
    setBusy(true);
    setError('');
    try {
      if (acceptInvitationAfterLogin && invitationToken) {
        await acceptInvitationToken();
      } else {
        await finishAuthenticatedSession(pendingInvitationOrganizationId);
      }
      setRecoveryCodes([]);
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
    'accept-invitation': 'Aceite seu convite',
    login: 'Entre na sua conta',
    enrollment: 'Proteja sua conta',
    mfa: 'Confirme sua identidade',
    'recovery-codes': 'Guarde seus códigos',
    'organization-selection': 'Selecione sua rede',
    'no-memberships': 'Nenhuma rede vinculada',
    authenticated: 'Configuração da operação',
  }[screen];

  return (
    <main className={`page-shell${screen === 'authenticated' ? ' workspace-shell' : ''}`}>
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
          <h2 id="panel-title" ref={panelHeading} tabIndex={-1}>{panelTitle}</h2>
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

        {screen === 'accept-invitation' && (
          <div className="confirmation-state invitation-acceptance-state">
            {!invitationPreview ? (
              <p className="sending-status" role="status">Validando o convite…</p>
            ) : (
              <>
                <div className="confirmation-symbol" aria-hidden="true">✓</div>
                <p className="confirmation-copy">Você foi convidado(a) para acessar a rede <strong>{invitationPreview.organizationName}</strong>.</p>
                <dl className="invitation-summary">
                  <div><dt>Papel</dt><dd>{invitationPreview.roleName}</dd></div>
                  <div><dt>Lojas</dt><dd>{invitationPreview.role === 'administrator' ? 'Toda a rede' : invitationPreview.storeNames.join(', ')}</dd></div>
                  <div><dt>Convite válido até</dt><dd>{new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium' }).format(new Date(invitationPreview.expiresAt))}</dd></div>
                </dl>
                {hasInvitationSession ? (
                  <>
                    <button className="submit-button" disabled={busy} onClick={() => void submitExistingAccountInvitation()} type="button">{busy ? 'Aceitando…' : 'Aceitar com a conta atual'}</button>
                    <button className="secondary-button" disabled={busy} onClick={() => { setAcceptInvitationAfterLogin(true); setEmail(invitationPreview.email); void signOut(); }} type="button">Entrar com outra conta</button>
                  </>
                ) : (
                  <>
                    <form className="registration-form" onSubmit={submitInvitationAccount} aria-busy={busy}>
                      <label htmlFor="invitationFullName">Seu nome</label>
                      <input autoComplete="name" disabled={busy} id="invitationFullName" maxLength={160} minLength={2} onChange={(event) => setInvitationFullName(event.target.value)} required value={invitationFullName} />
                      <label htmlFor="invitationPassword">Crie uma senha</label>
                      <input autoComplete="new-password" disabled={busy} id="invitationPassword" maxLength={256} onChange={(event) => setInvitationPassword(event.target.value)} required type="password" value={invitationPassword} />
                      <p className="field-hint">O convite confirma este e-mail. Gerentes, compradores e administradores configuram uma passkey antes de acessar a rede.</p>
                      <button className="submit-button" disabled={busy} type="submit">{busy ? 'Criando acesso…' : 'Criar conta e aceitar'}</button>
                    </form>
                    <p className="login-prompt">Já tem uma conta? <button className="text-button" onClick={() => { setAcceptInvitationAfterLogin(true); setEmail(invitationPreview.email); goTo('login'); }} type="button">Entrar e aceitar o convite</button></p>
                  </>
                )}
              </>
            )}
          </div>
        )}

        {screen === 'login' && (
          <form className="registration-form" onSubmit={submitLogin} aria-busy={busy}>
            <label htmlFor="loginEmail">E-mail</label>
            <input autoComplete="username" disabled={busy} id="loginEmail" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
            <label htmlFor="loginPassword">Senha</label>
            <input autoComplete="current-password" disabled={busy} id="loginPassword" onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
            <button className="submit-button" disabled={busy} type="submit">{busy ? 'Entrando...' : 'Entrar'}</button>
            {loginSessionConflict && <button className="secondary-button" disabled={busy} onClick={signOut} type="button">Encerrar sessão atual</button>}
            {acceptInvitationAfterLogin && <button className="text-button standalone-link" disabled={busy} onClick={() => { setAcceptInvitationAfterLogin(false); setScreen('accept-invitation'); }} type="button">Voltar ao convite</button>}
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
            <button className="submit-button" disabled={busy} onClick={continueAfterRecoveryCodes} type="button">{busy ? 'Carregando redes...' : 'Guardei os códigos, continuar'}</button>
          </div>
        )}

        {screen === 'organization-selection' && (
          <div className="confirmation-state">
            <p className="confirmation-copy">Escolha a rede que deseja acessar. A seleção define o contexto da sessão; o servidor continua validando suas permissões em cada operação.</p>
            <form className="registration-form" onSubmit={selectOrganization} aria-busy={busy}>
              <label htmlFor="activeOrganization">Rede</label>
              <select
                className="organization-select"
                disabled={busy}
                id="activeOrganization"
                onChange={(event) => setPendingOrganizationId(event.target.value)}
                required
                value={pendingOrganizationId}
              >
                {organizations.length !== 1 && <option disabled value="">Selecione uma rede</option>}
                {organizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>
                    {organization.name} · {roleLabel(organization.role)}
                  </option>
                ))}
              </select>
              <button className="submit-button" disabled={busy || !pendingOrganizationId} type="submit">{busy ? 'Selecionando...' : 'Acessar rede'}</button>
            </form>
            {activeOrganizationId && <button className="text-button standalone-link" disabled={busy} onClick={() => setScreen('authenticated')} type="button">Voltar à rede ativa</button>}
            <button className="secondary-button" disabled={busy} onClick={signOut} type="button">Sair da conta</button>
          </div>
        )}

        {screen === 'no-memberships' && (
          <div className="confirmation-state">
            <p className="confirmation-copy">Sua conta está conectada, mas não tem vínculo ativo com nenhuma rede. Você não tem acesso a lojas nem a dados operacionais. Peça ao administrador da rede um convite.</p>
            <button className="secondary-button" disabled={busy} onClick={signOut} type="button">{busy ? 'Saindo...' : 'Sair da conta'}</button>
          </div>
        )}

        {screen === 'authenticated' && activeOrganizationId && (
          <OpeningStockWorkspace
            key={activeOrganizationId}
            accountBusy={busy}
            canSwitchOrganization={organizations.length > 1}
            canManageCatalog={['administrator', 'buyer'].includes(organizations.find((organization) => organization.id === activeOrganizationId)?.role ?? '')}
            isAdministrator={organizations.find((organization) => organization.id === activeOrganizationId)?.role === 'administrator'}
            onSignOut={signOut}
            onSwitchOrganization={() => setScreen('organization-selection')}
            organizationName={organizations.find((organization) => organization.id === activeOrganizationId)?.name ?? 'Rede ativa'}
            role={organizations.find((organization) => organization.id === activeOrganizationId)?.role ?? ''}
          />
        )}

        {screen !== 'authenticated' && <p className="legal-copy">Os termos de uso e a política de privacidade serão apresentados antes da conclusão do cadastro.</p>}
      </section>
    </main>
  );
}

function roleLabel(role: string): string {
  const labels: Record<string, string> = {
    administrator: 'Administrador da rede',
    store_manager: 'Gerente de loja',
    operator: 'Operador',
    buyer: 'Comprador/aprovador',
  };
  return labels[role] ?? role;
}
