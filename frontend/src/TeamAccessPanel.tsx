import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { apiGet, apiPost, apiPut, ApiError } from './api';

type Role = 'administrator' | 'store_manager' | 'operator' | 'buyer';
type Store = { id: string; name: string; isActive: boolean };
type Membership = {
  id: string;
  email: string;
  fullName: string;
  role: Role;
  status: string;
  storeIds: string[];
  storeNames: string[];
};
type Invitation = {
  id: string;
  email: string;
  role: Role;
  roleName: string;
  status: string;
  storeNames: string[];
  expiresAt: string;
  sentCount: number;
};
type MembershipDraft = { role: Role; storeIds: string[] };
type Props = { organizationName: string };

const ROLE_OPTIONS: Array<{ value: Role; label: string }> = [
  { value: 'administrator', label: 'Administrador da rede' },
  { value: 'store_manager', label: 'Gerente de loja' },
  { value: 'operator', label: 'Operador' },
  { value: 'buyer', label: 'Comprador/aprovador' },
];

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    const fieldErrors = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    const known: Record<string, string> = {
      last_administrator_required: 'Promova outra pessoa para administradora antes de alterar este papel.',
      invitation_pending: 'Já existe um convite válido para esse e-mail.',
      membership_already_active: 'Essa pessoa já tem acesso ativo à rede.',
      store_scope_required: 'Selecione ao menos uma loja para esse papel.',
    };
    return known[error.code ?? ''] ?? `${error.message}${fieldErrors ? ` ${fieldErrors}` : ''}`;
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a operação.';
}

function roleLabel(role: Role): string {
  return ROLE_OPTIONS.find((option) => option.value === role)?.label ?? role;
}

function dateLabel(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium' }).format(new Date(value));
}

export default function TeamAccessPanel({ organizationName }: Props) {
  const [stores, setStores] = useState<Store[]>([]);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [drafts, setDrafts] = useState<Record<string, MembershipDraft>>({});
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<Role>('store_manager');
  const [inviteStoreIds, setInviteStoreIds] = useState<string[]>([]);
  const [confirmRevokeId, setConfirmRevokeId] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadData = useCallback(async () => {
    const [storeResponse, membershipResponse, invitationResponse] = await Promise.all([
      apiGet<{ stores: Store[] }>('/stores/'),
      apiGet<{ memberships: Membership[] }>('/organizations/current/memberships/'),
      apiGet<{ invitations: Invitation[] }>('/organizations/current/invitations/'),
    ]);
    setStores(storeResponse.stores.filter((store) => store.isActive));
    setMemberships(membershipResponse.memberships);
    setInvitations(invitationResponse.invitations);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    Promise.all([
      apiGet<{ stores: Store[] }>('/stores/'),
      apiGet<{ memberships: Membership[] }>('/organizations/current/memberships/'),
      apiGet<{ invitations: Invitation[] }>('/organizations/current/invitations/'),
    ])
      .then(([storeResponse, membershipResponse, invitationResponse]) => {
        if (cancelled) return;
        setStores(storeResponse.stores.filter((store) => store.isActive));
        setMemberships(membershipResponse.memberships);
        setInvitations(invitationResponse.invitations);
      })
      .catch((requestError: unknown) => { if (!cancelled) setError(messageFor(requestError)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  function currentDraft(membership: Membership): MembershipDraft {
    return drafts[membership.id] ?? { role: membership.role, storeIds: membership.storeIds };
  }

  function toggleStore(storeId: string, selected: string[], setSelected: (next: string[]) => void) {
    setSelected(selected.includes(storeId)
      ? selected.filter((value) => value !== storeId)
      : [...selected, storeId]);
  }

  async function submitInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyId('invite');
    setError('');
    setNotice('');
    try {
      await apiPost('/organizations/current/invitations/', {
        email: inviteEmail,
        role: inviteRole,
        storeIds: inviteRole === 'administrator' ? [] : inviteStoreIds,
      });
      setInviteEmail('');
      setInviteStoreIds([]);
      await loadData();
      setNotice(`Convite enviado para ${inviteEmail}. Ele expira em sete dias.`);
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setBusyId('');
    }
  }

  async function saveMembership(membership: Membership) {
    const draft = currentDraft(membership);
    setBusyId(membership.id);
    setError('');
    setNotice('');
    try {
      const updated = await apiPut<Membership>(
        `/organizations/current/memberships/${membership.id}/role/`,
        { role: draft.role, storeIds: draft.role === 'administrator' ? [] : draft.storeIds },
      );
      setMemberships((current) => current.map((row) => row.id === updated.id ? updated : row));
      setDrafts((current) => { const next = { ...current }; delete next[membership.id]; return next; });
      setNotice(`Acesso de ${updated.fullName || updated.email} atualizado.`);
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setBusyId('');
    }
  }

  async function revokeMembership(membership: Membership) {
    setBusyId(membership.id);
    setError('');
    setNotice('');
    try {
      const updated = await apiPost<Membership>(
        `/organizations/current/memberships/${membership.id}/revoke/`,
        {},
      );
      setMemberships((current) => current.map((row) => row.id === updated.id ? updated : row));
      setConfirmRevokeId('');
      setNotice(`Acesso de ${updated.fullName || updated.email} removido.`);
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setBusyId('');
    }
  }

  async function updateInvitation(invitation: Invitation, action: 'resend' | 'revoke') {
    setBusyId(invitation.id);
    setError('');
    setNotice('');
    try {
      await apiPost(
        `/organizations/current/invitations/${invitation.id}/${action}/`,
        {},
      );
      await loadData();
      setNotice(action === 'resend' ? `Convite reenviado para ${invitation.email}.` : `Convite de ${invitation.email} revogado.`);
    } catch (requestError) {
      setError(messageFor(requestError));
    } finally {
      setBusyId('');
    }
  }

  const pendingInvitations = invitations.filter((invitation) => invitation.status === 'pending' || invitation.status === 'expired');

  return (
    <section className="workspace-section team-access-panel" aria-labelledby="team-access-heading">
      <div className="section-heading">
        <span className="step-number" aria-hidden="true">7</span>
        <div>
          <h3 id="team-access-heading">Acesso da equipe</h3>
          <p>Convide pessoas para {organizationName}, atribua lojas e revise os papéis com segundo fator.</p>
        </div>
      </div>
      {notice && <p className="form-notice" role="status">{notice}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {loading ? <p className="sending-status" role="status">Carregando equipe…</p> : (
        <>
          <form className="workspace-form team-invite-form" onSubmit={submitInvitation} aria-busy={busyId === 'invite'}>
            <h4>Convidar uma pessoa</h4>
            <label htmlFor="teamInviteEmail">E-mail</label>
            <input autoComplete="email" disabled={busyId !== ''} id="teamInviteEmail" maxLength={254} onChange={(event) => setInviteEmail(event.target.value)} required type="email" value={inviteEmail} />
            <label htmlFor="teamInviteRole">Papel na rede</label>
            <select disabled={busyId !== ''} id="teamInviteRole" onChange={(event) => { setInviteRole(event.target.value as Role); setInviteStoreIds([]); }} value={inviteRole}>
              {ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            {inviteRole === 'administrator' ? (
              <p className="field-hint">Administradores têm acesso a todas as lojas da rede.</p>
            ) : (
              <fieldset className="team-store-picker">
                <legend>Lojas autorizadas</legend>
                {stores.map((store) => (
                  <label className="checkbox-label" key={store.id}>
                    <input checked={inviteStoreIds.includes(store.id)} disabled={busyId !== ''} onChange={() => toggleStore(store.id, inviteStoreIds, setInviteStoreIds)} type="checkbox" />
                    {store.name}
                  </label>
                ))}
                {stores.length === 0 && <p className="field-hint">Cadastre ao menos uma loja antes de convidar outro papel.</p>}
              </fieldset>
            )}
            <button className="submit-button" disabled={busyId !== '' || (inviteRole !== 'administrator' && inviteStoreIds.length === 0)} type="submit">{busyId === 'invite' ? 'Enviando convite…' : 'Enviar convite'}</button>
          </form>

          <div className="team-list-block">
            <div className="supplier-history-heading"><h4>Pessoas vinculadas</h4><span>{memberships.filter((member) => member.status === 'active').length} ativas</span></div>
            <ul className="team-member-list">
              {memberships.map((membership) => {
                const draft = currentDraft(membership);
                const active = membership.status === 'active';
                return (
                  <li className="team-member-card" key={membership.id}>
                    <div className="team-member-heading">
                      <div><strong>{membership.fullName || membership.email}</strong><span>{membership.email}</span></div>
                      <span className={`supplier-status${active ? ' supplier-status-active' : ''}`}>{active ? roleLabel(membership.role) : 'Acesso removido'}</span>
                    </div>
                    {active && (
                      <>
                        <label className="team-role-label" htmlFor={`member-role-${membership.id}`}>Papel</label>
                        <select disabled={busyId !== ''} id={`member-role-${membership.id}`} onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [membership.id]: { role: event.target.value as Role, storeIds: event.target.value === 'administrator' ? [] : draft.storeIds },
                        }))} value={draft.role}>
                          {ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                        {draft.role === 'administrator' ? (
                          <p className="field-hint">Acesso a todas as lojas.</p>
                        ) : (
                          <fieldset className="team-store-picker team-member-store-picker">
                            <legend>Lojas autorizadas</legend>
                            {stores.map((store) => (
                              <label className="checkbox-label" key={store.id}>
                                <input checked={draft.storeIds.includes(store.id)} disabled={busyId !== ''} onChange={() => setDrafts((current) => ({
                                  ...current,
                                  [membership.id]: {
                                    ...draft,
                                    storeIds: draft.storeIds.includes(store.id)
                                      ? draft.storeIds.filter((value) => value !== store.id)
                                      : [...draft.storeIds, store.id],
                                  },
                                }))} type="checkbox" />
                                {store.name}
                              </label>
                            ))}
                          </fieldset>
                        )}
                        <div className="team-member-actions">
                          <button className="secondary-button" disabled={busyId !== '' || (draft.role !== 'administrator' && draft.storeIds.length === 0)} onClick={() => void saveMembership(membership)} type="button">{busyId === membership.id ? 'Salvando…' : 'Salvar papel e escopo'}</button>
                          {confirmRevokeId === membership.id ? (
                            <><span className="field-hint">Remover acesso desta rede?</span><button className="text-button danger-text-button" disabled={busyId !== ''} onClick={() => void revokeMembership(membership)} type="button">Confirmar remoção</button><button className="text-button" disabled={busyId !== ''} onClick={() => setConfirmRevokeId('')} type="button">Cancelar</button></>
                          ) : (
                            <button className="text-button danger-text-button" disabled={busyId !== ''} onClick={() => setConfirmRevokeId(membership.id)} type="button">Remover acesso</button>
                          )}
                        </div>
                      </>
                    )}
                  </li>
                );
              })}
              {memberships.length === 0 && <li className="empty-state">Nenhuma pessoa vinculada.</li>}
            </ul>
          </div>

          <div className="team-list-block">
            <div className="supplier-history-heading"><h4>Convites</h4><span>{pendingInvitations.length} aguardando resposta</span></div>
            <ul className="entity-list team-invitation-list">
              {pendingInvitations.map((invitation) => (
                <li key={invitation.id}>
                  <div><strong>{invitation.email}</strong><span>{invitation.roleName} · {invitation.role === 'administrator' ? 'toda a rede' : invitation.storeNames.join(', ') || 'sem lojas'}</span><span>{invitation.status === 'expired' ? 'Expirou' : 'Expira'} {dateLabel(invitation.expiresAt)}</span></div>
                  <div className="team-invitation-actions">
                    <button className="text-button" disabled={busyId !== ''} onClick={() => void updateInvitation(invitation, 'resend')} type="button">Reenviar</button>
                    <button className="text-button danger-text-button" disabled={busyId !== ''} onClick={() => void updateInvitation(invitation, 'revoke')} type="button">Revogar</button>
                  </div>
                </li>
              ))}
              {pendingInvitations.length === 0 && <li className="empty-state">Nenhum convite pendente.</li>}
            </ul>
            <p className="field-hint">Convites valem por sete dias e são enviados por e-mail. Mudanças de papel e escopo ficam registradas na auditoria.</p>
          </div>
        </>
      )}
    </section>
  );
}
