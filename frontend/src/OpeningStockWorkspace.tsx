import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { apiGet, apiPost, ApiError } from './api';
import DailySalesPanel from './DailySalesPanel';
import PurchaseOrdersPanel from './PurchaseOrdersPanel';
import ReplenishmentRecommendationsPanel from './ReplenishmentRecommendationsPanel';
import SupplierCatalogPanel from './SupplierCatalogPanel';
import TeamAccessPanel from './TeamAccessPanel';

type CalendarVersion = {
  id: string;
  timeZone: string;
  operationalDayStart: string;
  effectiveFrom: string;
  createdAt: string;
};
type Store = {
  id: string;
  name: string;
  isActive: boolean;
  calendarOverrides: Array<{
    id?: string;
    timeZone: string | null;
    operationalDayStart: string | null;
    effectiveFrom: string;
  }>;
  effectiveCalendar: { timeZone: string; operationalDayStart: string } | null;
};
type StoreCalendarVersion = {
  id: string;
  timeZone: string | null;
  operationalDayStart: string | null;
  effectiveFrom: string;
};
type Product = {
  id: string;
  sku: string;
  name: string;
  baseUnit: 'unit' | 'kg' | 'l';
  categoryId: string | null;
  categoryName: string | null;
  isActive: boolean;
};
type ProductCategory = { id: string; name: string; createdAt: string };
type Assortment = {
  id: string;
  productId: string;
  sku: string;
  productName: string;
  baseUnit: Product['baseUnit'];
  effectiveFrom: string;
  effectiveTo: string | null;
};
type StockBalance = {
  productId: string;
  sku: string;
  productName: string;
  baseUnit: Product['baseUnit'];
  quantity: string;
};
type WorkspaceData = {
  calendar: { versions: CalendarVersion[] };
  storeList: { stores: Store[] };
  productList: { products: Product[] };
  categoryList: { categories: ProductCategory[] };
};
type Props = {
  organizationName: string;
  role: string;
  canManageCatalog: boolean;
  isAdministrator: boolean;
  onSwitchOrganization: () => void;
  canSwitchOrganization: boolean;
  onSignOut: () => void;
  accountBusy: boolean;
};

const initialCalendarForm = { timeZone: '', operationalDayStart: '', effectiveFrom: '' };
const initialProductForm = { sku: '', name: '', baseUnit: 'unit' as Product['baseUnit'], categoryId: '' };
const initialStoreCalendarForm = { timeZone: '', operationalDayStart: '', effectiveFrom: '' };

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const statusText: Record<number, string> = {
      403: 'Ação não autorizada (403).',
      404: 'Rede, loja ou produto indisponível (404).',
      409: 'Conflito com os dados atuais (409). Atualize as informações e tente novamente.',
    };
    const fieldErrors = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    return `${statusText[error.status] ?? `Erro ${error.status}.`} ${fieldErrors || error.message}`;
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a solicitação.';
}

function quantityPattern(unit: Product['baseUnit']): RegExp {
  return unit === 'unit' ? /^(0|[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$/;
}

function unitLabel(unit: Product['baseUnit']): string {
  return unit === 'unit' ? 'unidades' : unit;
}

function displayRole(role: string): string {
  const labels: Record<string, string> = {
    administrator: 'Administrador da rede',
    store_manager: 'Gerente de loja',
    operator: 'Operador',
    buyer: 'Comprador/aprovador',
  };
  return labels[role] ?? role;
}

export default function OpeningStockWorkspace({
  organizationName,
  role,
  canManageCatalog,
  isAdministrator,
  onSwitchOrganization,
  canSwitchOrganization,
  onSignOut,
  accountBusy,
}: Props) {
  const [calendarVersions, setCalendarVersions] = useState<CalendarVersion[]>([]);
  const [stores, setStores] = useState<Store[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<ProductCategory[]>([]);
  const [assortments, setAssortments] = useState<Assortment[]>([]);
  const [storeCalendarVersions, setStoreCalendarVersions] = useState<StoreCalendarVersion[]>([]);
  const [storeDetailsLoading, setStoreDetailsLoading] = useState(false);
  const [balances, setBalances] = useState<StockBalance[]>([]);
  const [balanceDate, setBalanceDate] = useState('');
  const [balanceLoaded, setBalanceLoaded] = useState(false);
  const [selectedStoreId, setSelectedStoreId] = useState('');
  const [closedOperationalDate, setClosedOperationalDate] = useState('');
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [calendarForm, setCalendarForm] = useState(initialCalendarForm);
  const [storeName, setStoreName] = useState('');
  const [storeOverridesEnabled, setStoreOverridesEnabled] = useState(false);
  const [storeOverrideTimeZone, setStoreOverrideTimeZone] = useState('');
  const [storeOverrideDayStart, setStoreOverrideDayStart] = useState('');
  const [storeOverrideEffectiveFrom, setStoreOverrideEffectiveFrom] = useState('');
  const [selectedStoreCalendarForm, setSelectedStoreCalendarForm] = useState(initialStoreCalendarForm);
  const [productForm, setProductForm] = useState(initialProductForm);
  const [assortmentProductId, setAssortmentProductId] = useState('');
  const [assortmentFrom, setAssortmentFrom] = useState('');
  const [assortmentTo, setAssortmentTo] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dailySalesBusy, setDailySalesBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reloadSequence, setReloadSequence] = useState(0);
  const activeStore = stores.find((store) => store.id === selectedStoreId);
  const hasStoreOverrideValue = Boolean(storeOverrideTimeZone.trim() || storeOverrideDayStart);
  const storeCreationHasNetworkDefault = calendarVersions.some((version) => version.effectiveFrom <= storeOverrideEffectiveFrom);
  const storeOverrideComplete = !storeOverridesEnabled || (storeCreationHasNetworkDefault
    ? hasStoreOverrideValue
    : Boolean(storeOverrideTimeZone.trim() && storeOverrideDayStart));
  const selectedStoreOverrideHasNetworkDefault = calendarVersions.some((version) => version.effectiveFrom <= selectedStoreCalendarForm.effectiveFrom);
  const selectedStoreOverrideHasValue = Boolean(selectedStoreCalendarForm.timeZone.trim() || selectedStoreCalendarForm.operationalDayStart);
  const selectedStoreOverrideComplete = selectedStoreOverrideHasNetworkDefault
    ? selectedStoreOverrideHasValue
    : Boolean(selectedStoreCalendarForm.timeZone.trim() && selectedStoreCalendarForm.operationalDayStart);
  const canEditStoreCalendar = isAdministrator || role === 'store_manager';
  const managerCalendarOverrideComplete = Boolean(selectedStoreCalendarForm.effectiveFrom)
    && (selectedStoreOverrideHasValue || Boolean(activeStore?.effectiveCalendar));
  const canSaveSelectedStoreCalendar = isAdministrator
    ? selectedStoreOverrideComplete
    : managerCalendarOverrideComplete;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');

    Promise.all([
      apiGet<WorkspaceData['storeList']>('/stores/'),
      apiGet<WorkspaceData['productList']>('/products/'),
      apiGet<WorkspaceData['categoryList']>('/product-categories/'),
      isAdministrator
        ? apiGet<WorkspaceData['calendar']>('/organizations/current/operational-calendar/')
        : Promise.resolve<WorkspaceData['calendar']>({ versions: [] }),
    ])
      .then(([storeList, productList, categoryList, calendar]) => {
        if (cancelled) return;
        setCalendarVersions(calendar.versions);
        setStores(storeList.stores);
        setProducts(productList.products);
        setCategories(categoryList.categories);
        setSelectedStoreId((current) => storeList.stores.some((store) => store.id === current)
          ? current
          : storeList.stores[0]?.id ?? '');
      })
      .catch((requestError: unknown) => {
        if (!cancelled) setError(describeError(requestError));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [isAdministrator, reloadSequence]);

  useEffect(() => {
    if (!selectedStoreId) {
      setAssortments([]);
      setStoreCalendarVersions([]);
      setBalances([]);
      setBalanceDate('');
      setBalanceLoaded(false);
      setStoreDetailsLoading(false);
      return;
    }

    let cancelled = false;
    setAssortments([]);
    setStoreCalendarVersions([]);
    setSelectedStoreCalendarForm(initialStoreCalendarForm);
    setBalances([]);
    setError('');
    setBalanceLoaded(false);
    setStoreDetailsLoading(true);
    let pendingStoreDetails = 2;
    const markStoreDetailLoaded = () => {
      pendingStoreDetails -= 1;
      if (!cancelled && pendingStoreDetails === 0) setStoreDetailsLoading(false);
    };

    apiGet<{ assortments: Assortment[] }>(`/stores/${selectedStoreId}/product-assortments/`)
      .then((result) => { if (!cancelled) setAssortments(result.assortments); })
      .catch((requestError: unknown) => { if (!cancelled) setError(describeError(requestError)); })
      .finally(markStoreDetailLoaded);

    apiGet<{ versions: StoreCalendarVersion[] }>(`/stores/${selectedStoreId}/operational-calendar/versions/`)
      .then((result) => { if (!cancelled) setStoreCalendarVersions(result.versions); })
      .catch((requestError: unknown) => { if (!cancelled) setError(describeError(requestError)); })
      .finally(markStoreDetailLoaded);

    if (activeStore?.effectiveCalendar) {
      apiGet<{ balances: StockBalance[]; asOfOperationalDate: string }>(`/stores/${selectedStoreId}/stock-balances/`)
        .then((result) => {
          if (cancelled) return;
          setBalances(result.balances);
          setBalanceDate(result.asOfOperationalDate);
          setBalanceLoaded(true);
        })
        .catch((requestError: unknown) => { if (!cancelled) setError(describeError(requestError)); });
    }

    return () => { cancelled = true; };
  }, [activeStore, selectedStoreId, reloadSequence, stores]);

  const activeAssortments = useMemo(() => {
    if (!closedOperationalDate) return [];
    const activeByProduct = new Map<string, Assortment>();
    for (const assortment of assortments) {
      if (assortment.effectiveFrom <= closedOperationalDate &&
          (assortment.effectiveTo === null || closedOperationalDate < assortment.effectiveTo)) {
        activeByProduct.set(assortment.productId, assortment);
      }
    }
    return [...activeByProduct.values()].sort((left, right) =>
      left.productName.localeCompare(right.productName, 'pt-BR'),
    );
  }, [assortments, closedOperationalDate]);

  const activeProducts = products.filter((product) => product.isActive);
  const calendarForCount = useMemo(() => {
    if (!closedOperationalDate || !activeStore) return null;
    const networkVersion = [...calendarVersions]
      .filter((version) => version.effectiveFrom <= closedOperationalDate)
      .sort((left, right) => left.effectiveFrom.localeCompare(right.effectiveFrom))
      .slice(-1)[0];
    const storeOverride = [...storeCalendarVersions]
      .filter((version) => version.effectiveFrom <= closedOperationalDate)
      .sort((left, right) => left.effectiveFrom.localeCompare(right.effectiveFrom))
      .slice(-1)[0];
    const timeZone = storeOverride?.timeZone ?? networkVersion?.timeZone;
    const operationalDayStart = storeOverride?.operationalDayStart ?? networkVersion?.operationalDayStart;
    return timeZone && operationalDayStart ? { timeZone, operationalDayStart } : null;
  }, [activeStore, calendarVersions, closedOperationalDate, storeCalendarVersions]);

  function reloadWorkspace() {
    setReloadSequence((current) => current + 1);
  }

  async function submitCalendar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const created = await apiPost<CalendarVersion>('/organizations/current/operational-calendar/versions/', {
        timeZone: calendarForm.timeZone.trim(),
        operationalDayStart: calendarForm.operationalDayStart,
        effectiveFrom: calendarForm.effectiveFrom,
      });
      setCalendarVersions((current) => [...current, created].sort((a, b) => a.effectiveFrom.localeCompare(b.effectiveFrom)));
      setCalendarForm(initialCalendarForm);
      setNotice('Calendário operacional da rede salvo.');
      await reloadWorkspace();
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitStore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const calendarOverrides = storeOverridesEnabled
        ? {
            effectiveFrom: storeOverrideEffectiveFrom,
            ...(storeOverrideTimeZone.trim() ? { timeZone: storeOverrideTimeZone.trim() } : {}),
            ...(storeOverrideDayStart ? { operationalDayStart: storeOverrideDayStart } : {}),
          }
        : undefined;
      const created = await apiPost<Store>('/stores/', {
        name: storeName.trim(),
        ...(calendarOverrides ? { calendarOverrides } : {}),
      });
      setStoreName('');
      setStoreOverridesEnabled(false);
      setStoreOverrideTimeZone('');
      setStoreOverrideDayStart('');
      setStoreOverrideEffectiveFrom('');
      setSelectedStoreCalendarForm(initialStoreCalendarForm);
      setStores((current) => [...current, created]);
      setSelectedStoreId(created.id);
      setClosedOperationalDate('');
      setQuantities({});
      setNotice(`Loja ${created.name} criada.`);
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitSelectedStoreCalendar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedStoreId || !selectedStoreOverrideComplete) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const created = await apiPost<StoreCalendarVersion>(
        `/stores/${selectedStoreId}/operational-calendar/versions/`,
        {
          effectiveFrom: selectedStoreCalendarForm.effectiveFrom,
          ...(selectedStoreCalendarForm.timeZone.trim() ? { timeZone: selectedStoreCalendarForm.timeZone.trim() } : {}),
          ...(selectedStoreCalendarForm.operationalDayStart ? { operationalDayStart: selectedStoreCalendarForm.operationalDayStart } : {}),
        },
      );
      setStoreCalendarVersions((current) => [...current, created].sort((left, right) => left.effectiveFrom.localeCompare(right.effectiveFrom)));
      setSelectedStoreCalendarForm(initialStoreCalendarForm);
      setNotice(`Exceção de calendário salva para ${activeStore?.name ?? 'a loja'}.`);
      reloadWorkspace();
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const created = await apiPost<Product>('/products/', {
        sku: productForm.sku.trim(),
        name: productForm.name.trim(),
        baseUnit: productForm.baseUnit,
        ...(productForm.categoryId ? { categoryId: productForm.categoryId } : {}),
      });
      setProducts((current) => [...current, created]);
      setProductForm(initialProductForm);
      setNotice(`Produto ${created.name} cadastrado.`);
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitAssortment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedStoreId) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const created = await apiPost<Assortment>(`/stores/${selectedStoreId}/product-assortments/`, {
        productId: assortmentProductId,
        effectiveFrom: assortmentFrom,
        ...(assortmentTo ? { effectiveTo: assortmentTo } : {}),
      });
      setAssortments((current) => [...current, created]);
      setAssortmentProductId('');
      setAssortmentFrom('');
      setAssortmentTo('');
      setNotice(`${created.productName} incluído no sortimento de ${activeStore?.name ?? 'loja'}.`);
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitOpeningCount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedStoreId || !closedOperationalDate || activeAssortments.length === 0) return;
    if (!calendarForCount || !activeStore?.effectiveCalendar) {
      setError('A contagem exige calendário efetivo tanto na data da contagem quanto na data operacional atual. Configure fuso horário e início do dia para ambas antes de registrar.');
      return;
    }

    const missing = activeAssortments.find((assortment) => quantities[assortment.productId] === undefined || quantities[assortment.productId] === '');
    if (missing) {
      setError(`Informe a quantidade de ${missing.productName}. Digite 0 quando não houver saldo.`);
      return;
    }
    const invalid = activeAssortments.find((assortment) => !quantityPattern(assortment.baseUnit).test(quantities[assortment.productId]));
    if (invalid) {
      setError(invalid.baseUnit === 'unit'
        ? `A quantidade de ${invalid.productName} deve ser um número inteiro não negativo.`
        : `A quantidade de ${invalid.productName} deve ser não negativa e ter no máximo três casas decimais, usando ponto decimal.`);
      return;
    }

    setBusy(true);
    setError('');
    setNotice('');
    try {
      const created = await apiPost<{ cutoffDate: string; createdAt: string; createdBy: string; lines: unknown[] }>(
        `/stores/${selectedStoreId}/opening-stock-counts/`,
        {
          closedOperationalDate,
          lines: activeAssortments.map((assortment) => ({
            productId: assortment.productId,
            quantity: quantities[assortment.productId],
          })),
        },
      );
      const recordedAt = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(created.createdAt));
      setNotice(`Contagem inicial registrada e auditada em ${recordedAt}. O primeiro dia operacional completo será ${created.cutoffDate}.`);
      setQuantities({});
      await loadBalances(selectedStoreId);
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function loadBalances(storeId: string) {
    if (!stores.find((store) => store.id === storeId)?.effectiveCalendar) {
      setBalances([]);
      setBalanceDate('');
      setBalanceLoaded(false);
      return;
    }
    setBusy(true);
    try {
      const result = await apiGet<{ balances: StockBalance[]; asOfOperationalDate: string }>(`/stores/${storeId}/stock-balances/`);
      setBalances(result.balances);
      setBalanceDate(result.asOfOperationalDate);
      setBalanceLoaded(true);
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  function displayDate(date: string): string {
    if (!date) return '—';
    return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
  }

  return (
    <div className="operations-workspace">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Rede ativa</p>
          <h3>{organizationName}</h3>
          <p className="field-hint">Perfil: {displayRole(role)}</p>
        </div>
        <div className="workspace-account-actions">
          {canSwitchOrganization && <button className="secondary-button" disabled={accountBusy || busy || dailySalesBusy} onClick={onSwitchOrganization} type="button">Trocar de rede</button>}
          <button className="text-button" disabled={accountBusy || busy || dailySalesBusy} onClick={onSignOut} type="button">Sair</button>
        </div>
      </header>

      {notice && <p className="form-notice" role="status">{notice}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {loading && <p className="sending-status" role="status">Carregando lojas e produtos autorizados…</p>}

      {isAdministrator && !loading && (
        <>
          <section className="workspace-section" aria-labelledby="calendar-heading">
            <div className="section-heading">
              <span className="step-number" aria-hidden="true">1</span>
              <div><h3 id="calendar-heading">Calendário operacional da rede</h3><p>Define o fuso e o horário que fecham cada dia operacional.</p></div>
            </div>
            {calendarVersions.length === 0 ? (
              <form className="workspace-form" onSubmit={submitCalendar} aria-busy={busy}>
                <label htmlFor="networkTimeZone">Fuso horário IANA da rede</label>
                <input autoComplete="off" disabled={busy} id="networkTimeZone" maxLength={64} onChange={(event) => setCalendarForm({ ...calendarForm, timeZone: event.target.value })} placeholder="Ex.: America/Sao_Paulo" required value={calendarForm.timeZone} />
                <label htmlFor="networkDayStart">Início do dia operacional (HH:mm)</label>
                <input disabled={busy} id="networkDayStart" onChange={(event) => setCalendarForm({ ...calendarForm, operationalDayStart: event.target.value })} required type="time" value={calendarForm.operationalDayStart} />
                <label htmlFor="networkCalendarDate">Vigente a partir da data operacional</label>
                <input disabled={busy} id="networkCalendarDate" onChange={(event) => setCalendarForm({ ...calendarForm, effectiveFrom: event.target.value })} required type="date" value={calendarForm.effectiveFrom} />
                <button className="submit-button" disabled={busy} type="submit">{busy ? 'Salvando…' : 'Salvar calendário da rede'}</button>
              </form>
            ) : (
              <>
                <ul className="calendar-version-list" aria-label="Versões do calendário da rede">
                  {[...calendarVersions].sort((a, b) => b.effectiveFrom.localeCompare(a.effectiveFrom)).map((version) => (
                    <li key={version.id}><strong>{displayDate(version.effectiveFrom)}</strong><span>{version.timeZone} · início às {version.operationalDayStart}</span></li>
                  ))}
                </ul>
                <p className="field-hint">As versões são preservadas e valem a partir da data informada. Uma nova versão pode preencher datas sem calendário vigente.</p>
                <form className="workspace-form" onSubmit={submitCalendar} aria-busy={busy}>
                  <h4>Adicionar versão do calendário</h4>
                  <label htmlFor="networkTimeZone">Fuso horário IANA da rede</label>
                  <input autoComplete="off" disabled={busy} id="networkTimeZone" maxLength={64} onChange={(event) => setCalendarForm({ ...calendarForm, timeZone: event.target.value })} placeholder="Ex.: America/Sao_Paulo" required value={calendarForm.timeZone} />
                  <label htmlFor="networkDayStart">Início do dia operacional (HH:mm)</label>
                  <input disabled={busy} id="networkDayStart" onChange={(event) => setCalendarForm({ ...calendarForm, operationalDayStart: event.target.value })} required type="time" value={calendarForm.operationalDayStart} />
                  <label htmlFor="networkCalendarDate">Vigente a partir da data operacional</label>
                  <input disabled={busy} id="networkCalendarDate" onChange={(event) => setCalendarForm({ ...calendarForm, effectiveFrom: event.target.value })} required type="date" value={calendarForm.effectiveFrom} />
                  <button className="submit-button" disabled={busy} type="submit">{busy ? 'Salvando…' : 'Adicionar versão'}</button>
                </form>
              </>
            )}
          </section>

          <section className="workspace-section" aria-labelledby="stores-heading">
            <div className="section-heading">
              <span className="step-number" aria-hidden="true">2</span>
              <div><h3 id="stores-heading">Lojas</h3><p>Cadastre as lojas e, se necessário, defina exceções de calendário.</p></div>
            </div>
            <ul className="entity-list" aria-label="Lojas cadastradas">
              {stores.map((store) => (
                <li key={store.id}>
                  <strong>{store.name}</strong>
                  <span>{store.effectiveCalendar ? `${store.effectiveCalendar.timeZone} · início às ${store.effectiveCalendar.operationalDayStart}` : 'Calendário efetivo ainda não resolvido'} · {store.calendarOverrides.length ? `${store.calendarOverrides.length} exceção(ões)` : 'herda padrão da rede'}</span>
                </li>
              ))}
              {stores.length === 0 && <li className="empty-state">Nenhuma loja cadastrada.</li>}
            </ul>
            <div className="workspace-form store-selector-form">
              <label htmlFor="selectedStore">Loja para calendário, sortimento, contagem e saldo</label>
              <select disabled={busy || dailySalesBusy || stores.length === 0} id="selectedStore" onChange={(event) => { setSelectedStoreId(event.target.value); setClosedOperationalDate(''); setQuantities({}); setSelectedStoreCalendarForm(initialStoreCalendarForm); }} value={selectedStoreId}>
                {stores.length === 0 && <option value="">Cadastre uma loja</option>}
                {stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}
              </select>
            </div>
            {selectedStoreId && (
              <div className="store-calendar-settings">
                <h4>Calendário de {activeStore?.name ?? 'loja selecionada'}</h4>
                {!activeStore?.effectiveCalendar && <p className="calendar-block-message" role="status">Esta loja está sem calendário efetivo. Configure uma exceção abaixo ou um padrão da rede; contagem e consulta de saldo permanecem bloqueadas até a vigência adequada.</p>}
                <ul className="calendar-version-list" aria-label={`Exceções de calendário de ${activeStore?.name ?? 'loja selecionada'}`}>
                  {storeCalendarVersions.map((version) => (
                    <li key={version.id}>
                      <strong>{displayDate(version.effectiveFrom)}</strong>
                      <span>{version.timeZone ?? 'Fuso herdado da rede'} · {version.operationalDayStart ? `início às ${version.operationalDayStart}` : 'início herdado da rede'}</span>
                    </li>
                  ))}
                  {!storeDetailsLoading && storeCalendarVersions.length === 0 && <li className="empty-state">Nenhuma exceção cadastrada; a loja herda o padrão da rede.</li>}
                  {storeDetailsLoading && <li className="empty-state">Carregando calendário da loja…</li>}
                </ul>
                <form className="workspace-form" onSubmit={submitSelectedStoreCalendar} aria-busy={busy}>
                  <label htmlFor="selectedStoreOverrideDate">Nova exceção vigente a partir de</label>
                  <input disabled={busy} id="selectedStoreOverrideDate" onChange={(event) => setSelectedStoreCalendarForm({ ...selectedStoreCalendarForm, effectiveFrom: event.target.value })} required type="date" value={selectedStoreCalendarForm.effectiveFrom} />
                  <label htmlFor="selectedStoreOverrideTimeZone">Fuso horário próprio (vazio herda da rede)</label>
                  <input autoComplete="off" disabled={busy} id="selectedStoreOverrideTimeZone" maxLength={64} onChange={(event) => setSelectedStoreCalendarForm({ ...selectedStoreCalendarForm, timeZone: event.target.value })} placeholder="Ex.: America/Sao_Paulo" value={selectedStoreCalendarForm.timeZone} />
                  <label htmlFor="selectedStoreOverrideDayStart">Início do dia próprio (vazio herda da rede)</label>
                  <input disabled={busy} id="selectedStoreOverrideDayStart" onChange={(event) => setSelectedStoreCalendarForm({ ...selectedStoreCalendarForm, operationalDayStart: event.target.value })} type="time" value={selectedStoreCalendarForm.operationalDayStart} />
                  <p className="field-hint">{selectedStoreOverrideHasNetworkDefault
                    ? 'Há padrão da rede vigente nesta data. Informe pelo menos um campo; os demais herdam o padrão.'
                    : 'Não há padrão da rede vigente nesta data. Informe fuso e início do dia para habilitar operações na loja.'}</p>
                  <button className="submit-button" disabled={busy || !selectedStoreId || !selectedStoreCalendarForm.effectiveFrom || !canSaveSelectedStoreCalendar} type="submit">{busy ? 'Salvando…' : 'Adicionar exceção de calendário'}</button>
                </form>
              </div>
            )}
            <form className="workspace-form" onSubmit={submitStore} aria-busy={busy}>
              <label htmlFor="storeName">Nome da loja</label>
              <input disabled={busy} id="storeName" maxLength={160} onChange={(event) => setStoreName(event.target.value)} required value={storeName} />
              <label className="checkbox-label" htmlFor="storeOverridesEnabled">
                <input checked={storeOverridesEnabled} disabled={busy} id="storeOverridesEnabled" onChange={(event) => setStoreOverridesEnabled(event.target.checked)} type="checkbox" />
                Definir exceções de calendário para esta loja
              </label>
              {storeOverridesEnabled && (
                <div className="override-fields">
                  <label htmlFor="storeOverrideDate">Exceções vigentes a partir de</label>
                  <input disabled={busy} id="storeOverrideDate" onChange={(event) => setStoreOverrideEffectiveFrom(event.target.value)} required type="date" value={storeOverrideEffectiveFrom} />
                  <label htmlFor="storeOverrideTimeZone">Fuso próprio (deixe vazio para herdar)</label>
                  <input disabled={busy} id="storeOverrideTimeZone" maxLength={64} onChange={(event) => setStoreOverrideTimeZone(event.target.value)} placeholder="Ex.: America/Manaus" value={storeOverrideTimeZone} />
                  <label htmlFor="storeOverrideDayStart">Início próprio (deixe vazio para herdar)</label>
                  <input disabled={busy} id="storeOverrideDayStart" onChange={(event) => setStoreOverrideDayStart(event.target.value)} type="time" value={storeOverrideDayStart} />
                  <p className="field-hint">{!storeCreationHasNetworkDefault
                    ? 'Sem padrão de rede, informe o fuso e o início do dia para habilitar operações nesta loja.'
                    : 'Informe pelo menos um valor de exceção. Os campos vazios herdam o padrão da rede vigente nessa data.'}</p>
                </div>
              )}
              <button className="submit-button" disabled={busy || !storeName.trim() || !storeOverrideComplete} type="submit">{busy ? 'Salvando…' : 'Criar loja'}</button>
              {calendarVersions.length === 0 && <p className="field-hint">Você pode criar a loja sem calendário. Vendas e operações de estoque ficam bloqueadas até haver fuso horário e início do dia operacional vigentes.</p>}
            </form>
          </section>

          <section className="workspace-section" aria-labelledby="products-heading">
            <div className="section-heading">
              <span className="step-number" aria-hidden="true">3</span>
              <div><h3 id="products-heading">Produtos</h3><p>O SKU precisa ser único dentro da rede.</p></div>
            </div>
            <ul className="entity-list product-list" aria-label="Produtos cadastrados">
              {products.map((product) => (
                <li key={product.id}><strong>{product.name}</strong><span>SKU {product.sku} · {unitLabel(product.baseUnit)}{product.baseUnit !== 'unit' ? ' (até 3 casas decimais)' : ''}{product.categoryName ? ` · ${product.categoryName}` : ' · sem categoria'}</span></li>
              ))}
              {products.length === 0 && <li className="empty-state">Nenhum produto cadastrado.</li>}
            </ul>
            <form className="workspace-form product-form" onSubmit={submitProduct} aria-busy={busy}>
              <label htmlFor="productSku">SKU interno</label>
              <input disabled={busy} id="productSku" maxLength={64} onChange={(event) => setProductForm({ ...productForm, sku: event.target.value })} required value={productForm.sku} />
              <label htmlFor="productName">Nome do produto</label>
              <input disabled={busy} id="productName" maxLength={200} onChange={(event) => setProductForm({ ...productForm, name: event.target.value })} required value={productForm.name} />
              <label htmlFor="productBaseUnit">Unidade-base</label>
              <select disabled={busy} id="productBaseUnit" onChange={(event) => setProductForm({ ...productForm, baseUnit: event.target.value as Product['baseUnit'] })} value={productForm.baseUnit}>
                <option value="unit">Unidade contável</option>
                <option value="kg">Quilograma (kg)</option>
                <option value="l">Litro (l)</option>
              </select>
              <label htmlFor="productCategory">Categoria (opcional)</label>
              <select disabled={busy} id="productCategory" onChange={(event) => setProductForm({ ...productForm, categoryId: event.target.value })} value={productForm.categoryId}>
                <option value="">Sem categoria</option>
                {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
              </select>
              <button className="submit-button" disabled={busy} type="submit">{busy ? 'Salvando…' : 'Cadastrar produto'}</button>
            </form>
          </section>

          <section className="workspace-section" aria-labelledby="assortment-heading">
            <div className="section-heading">
              <span className="step-number" aria-hidden="true">4</span>
              <div><h3 id="assortment-heading">Sortimento por loja</h3><p>Informe em quais datas cada produto pertence ao sortimento da loja.</p></div>
            </div>
            {selectedStoreId && (
              <>
                <ul className="entity-list" aria-label={`Produtos do sortimento de ${activeStore?.name ?? 'loja selecionada'}`}>
                  {assortments.map((item) => <li key={item.id}><strong>{item.productName}</strong><span>Vigência: {displayDate(item.effectiveFrom)} até {item.effectiveTo ? displayDate(item.effectiveTo) : 'sem data final'}</span></li>)}
                  {storeDetailsLoading && <li className="empty-state">Carregando vínculos da loja…</li>}
                  {!storeDetailsLoading && assortments.length === 0 && <li className="empty-state">Nenhum produto vinculado a esta loja.</li>}
                </ul>
                <form className="workspace-form assortment-form" onSubmit={submitAssortment} aria-busy={busy}>
                  <label htmlFor="assortmentProduct">Produto</label>
                  <select disabled={busy || activeProducts.length === 0} id="assortmentProduct" onChange={(event) => setAssortmentProductId(event.target.value)} required value={assortmentProductId}>
                    <option value="">Selecione um produto</option>
                    {activeProducts.map((product) => <option key={product.id} value={product.id}>{product.sku} · {product.name}</option>)}
                  </select>
                  <label htmlFor="assortmentFrom">Ativo a partir de (inclusiva)</label>
                  <input disabled={busy} id="assortmentFrom" onChange={(event) => setAssortmentFrom(event.target.value)} required type="date" value={assortmentFrom} />
                  <label htmlFor="assortmentTo">Encerrar em (opcional, data exclusiva)</label>
                  <input disabled={busy} id="assortmentTo" onChange={(event) => setAssortmentTo(event.target.value)} type="date" value={assortmentTo} />
                  <button className="submit-button" disabled={busy || activeProducts.length === 0} type="submit">{busy ? 'Salvando…' : 'Adicionar ao sortimento'}</button>
                </form>
              </>
            )}
          </section>

          <section className="workspace-section" aria-labelledby="opening-count-heading">
            <div className="section-heading">
              <span className="step-number" aria-hidden="true">5</span>
              <div><h3 id="opening-count-heading">Contagem inicial de estoque</h3><p>Registre o saldo físico fechado da loja. Todos os produtos ativos no sortimento precisam de uma quantidade, inclusive zero. O servidor audita o administrador e cada item informado.</p></div>
            </div>
            <form className="workspace-form" onSubmit={submitOpeningCount} aria-busy={busy}>
              <label htmlFor="closedOperationalDate">Data operacional fechada da contagem</label>
              <input disabled={busy || !selectedStoreId} id="closedOperationalDate" onChange={(event) => { setClosedOperationalDate(event.target.value); setQuantities({}); }} required type="date" value={closedOperationalDate} />
              {storeDetailsLoading && <p className="sending-status" role="status">Carregando o sortimento e o calendário da loja…</p>}
              {closedOperationalDate && !calendarForCount && <p className="calendar-block-message" role="status">A contagem fica bloqueada até existir fuso horário e início do dia operacional vigentes para esta loja na data fechada escolhida.</p>}
              {closedOperationalDate && calendarForCount && <p className="field-hint">Calendário da data fechada: {calendarForCount.timeZone} · início às {calendarForCount.operationalDayStart}.</p>}
              {closedOperationalDate && calendarForCount && !activeStore?.effectiveCalendar && <p className="calendar-block-message" role="status">A contagem também exige um calendário efetivo para a data operacional atual da loja. Configure fuso e início vigentes hoje, além do calendário da data fechada.</p>}
              {closedOperationalDate && activeAssortments.length > 0 && (
                <div className="count-lines">
                  {activeAssortments.map((item) => (
                    <div className="count-line" key={item.productId}>
                      <label htmlFor={`count-${item.productId}`}><strong>{item.productName}</strong><span>SKU {item.sku} · {unitLabel(item.baseUnit)}</span></label>
                      <input
                        autoComplete="off"
                        disabled={busy}
                        id={`count-${item.productId}`}
                        inputMode={item.baseUnit === 'unit' ? 'numeric' : 'decimal'}
                        min="0"
                        onChange={(event) => setQuantities((current) => ({ ...current, [item.productId]: event.target.value }))}
                        placeholder="Informe a contagem"
                        required
                        step={item.baseUnit === 'unit' ? '1' : '0.001'}
                        type="number"
                        value={quantities[item.productId] ?? ''}
                      />
                    </div>
                  ))}
                  <p className="field-hint">Kg e litro aceitam até três casas decimais. Use ponto para casas decimais; não haverá arredondamento.</p>
                </div>
              )}
              {closedOperationalDate && !storeDetailsLoading && activeAssortments.length === 0 && <p className="empty-state">Não há produtos no sortimento desta loja nessa data. Adicione os vínculos antes de registrar a contagem.</p>}
              <button className="submit-button" disabled={busy || storeDetailsLoading || !selectedStoreId || !closedOperationalDate || !calendarForCount || !activeStore?.effectiveCalendar || activeAssortments.length === 0} type="submit">{busy ? 'Registrando…' : 'Registrar contagem completa'}</button>
            </form>
          </section>

          <section className="workspace-section" aria-labelledby="balances-heading">
            <div className="section-heading">
              <span className="step-number" aria-hidden="true">6</span>
              <div><h3 id="balances-heading">Saldo atual do estoque</h3><p>Calculado a partir das movimentações registradas no ledger.</p></div>
            </div>
            <div className="balance-toolbar">
              <p className="field-hint">{balanceDate ? `Data operacional: ${displayDate(balanceDate)}` : 'Selecione uma loja com calendário operacional configurado.'}</p>
              <button className="secondary-button" disabled={busy || !selectedStoreId || !activeStore?.effectiveCalendar} onClick={() => void loadBalances(selectedStoreId)} type="button">Atualizar saldo</button>
            </div>
            {!activeStore?.effectiveCalendar && <p className="calendar-block-message" role="status">A consulta do saldo está bloqueada. Configure fuso horário e início do dia operacional vigentes para esta loja antes de iniciar operações de estoque.</p>}
            {activeStore?.effectiveCalendar ? (
              <div className="table-scroll" role="region" aria-label="Saldo atual por produto" tabIndex={0}>
                <table className="balance-table">
                  <thead><tr><th scope="col">SKU</th><th scope="col">Produto</th><th scope="col">Unidade</th><th scope="col">Saldo</th></tr></thead>
                  <tbody>
                    {balances.map((balance) => <tr key={balance.productId}><td>{balance.sku}</td><td>{balance.productName}</td><td>{unitLabel(balance.baseUnit)}</td><td>{balance.quantity}</td></tr>)}
                    {balances.length === 0 && balanceLoaded && <tr><td colSpan={4}>Nenhum movimento de estoque retornado para esta loja.</td></tr>}
                    {balances.length === 0 && !balanceLoaded && <tr><td colSpan={4}>Saldo não carregado. Confira o aviso da solicitação.</td></tr>}
                  </tbody>
                </table>
              </div>
            ) : <p className="empty-state">Saldo indisponível até configurar um calendário operacional efetivo para a loja.</p>}
          </section>

        </>
      )}

      {!isAdministrator && !loading && (
        <section className="workspace-section" aria-labelledby="assigned-stores-heading">
          <div className="section-heading">
            <span className="step-number" aria-hidden="true">1</span>
            <div><h3 id="assigned-stores-heading">Lojas atribuídas</h3><p>A lista contém apenas as lojas autorizadas para seu papel nesta rede.</p></div>
          </div>
          {stores.length > 0 ? (
            <div className="workspace-form store-selector-form">
              <label htmlFor="assignedStore">Loja ativa</label>
              <select disabled={busy || dailySalesBusy} id="assignedStore" onChange={(event) => { setSelectedStoreId(event.target.value); setClosedOperationalDate(''); setQuantities({}); }} value={selectedStoreId}>
                {stores.map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}
              </select>
            </div>
          ) : <p className="empty-state">Ainda não há lojas atribuídas a esta conta. Peça ao administrador da rede para revisar seu escopo.</p>}
          {activeStore && !activeStore.effectiveCalendar && <p className="calendar-block-message" role="status">Esta loja não tem calendário operacional vigente; vendas e consultas de saldo ficam bloqueadas até a configuração ser concluída.</p>}
        </section>
      )}

      {!isAdministrator && !loading && selectedStoreId && (
        <section className="workspace-section" aria-labelledby="assigned-calendar-heading">
          <div className="section-heading">
            <span className="step-number" aria-hidden="true">2</span>
            <div><h3 id="assigned-calendar-heading">Calendário da loja</h3><p>{activeStore?.name ?? 'Loja selecionada'} · versões por data, com histórico preservado.</p></div>
          </div>
          {!activeStore?.effectiveCalendar && <p className="calendar-block-message" role="status">Esta loja ainda não tem calendário vigente. O gerente precisa definir o fuso e o fechamento do dia antes das operações de venda e estoque.</p>}
          <ul className="calendar-version-list" aria-label={`Versões do calendário de ${activeStore?.name ?? 'loja selecionada'}`}>
            {storeCalendarVersions.map((version) => (
              <li key={version.id}>
                <strong>{displayDate(version.effectiveFrom)}</strong>
                <span>{version.timeZone ?? 'Fuso herdado da rede'} · {version.operationalDayStart ? `início às ${version.operationalDayStart}` : 'início herdado da rede'}</span>
              </li>
            ))}
            {!storeDetailsLoading && storeCalendarVersions.length === 0 && <li className="empty-state">Nenhuma exceção cadastrada; os valores são herdados da rede.</li>}
            {storeDetailsLoading && <li className="empty-state">Carregando calendário da loja…</li>}
          </ul>
          {canEditStoreCalendar ? (
            <form className="workspace-form" onSubmit={submitSelectedStoreCalendar} aria-busy={busy}>
              <h4>Definir exceção para esta loja</h4>
              <label htmlFor="assignedStoreOverrideDate">Vigente a partir de</label>
              <input disabled={busy} id="assignedStoreOverrideDate" onChange={(event) => setSelectedStoreCalendarForm({ ...selectedStoreCalendarForm, effectiveFrom: event.target.value })} required type="date" value={selectedStoreCalendarForm.effectiveFrom} />
              <label htmlFor="assignedStoreOverrideTimeZone">Fuso horário próprio</label>
              <input autoComplete="off" disabled={busy} id="assignedStoreOverrideTimeZone" maxLength={64} onChange={(event) => setSelectedStoreCalendarForm({ ...selectedStoreCalendarForm, timeZone: event.target.value })} placeholder="Ex.: America/Sao_Paulo" value={selectedStoreCalendarForm.timeZone} />
              <label htmlFor="assignedStoreOverrideDayStart">Início do dia operacional próprio</label>
              <input disabled={busy} id="assignedStoreOverrideDayStart" onChange={(event) => setSelectedStoreCalendarForm({ ...selectedStoreCalendarForm, operationalDayStart: event.target.value })} type="time" value={selectedStoreCalendarForm.operationalDayStart} />
              <p className="field-hint">O gerente pode preencher um ou os dois valores quando a loja já tem calendário vigente. Sem calendário efetivo, informe fuso e horário de fechamento. Os campos não informados herdam o valor da rede quando houver.</p>
              <button className="submit-button" disabled={busy || storeDetailsLoading || !selectedStoreCalendarForm.effectiveFrom || !canSaveSelectedStoreCalendar} type="submit">{busy ? 'Salvando…' : 'Salvar calendário da loja'}</button>
            </form>
          ) : (
            <p className="field-hint">Apenas o gerente desta loja pode alterar o calendário local.</p>
          )}
        </section>
      )}

      {!isAdministrator && !loading && selectedStoreId && (
        <section className="workspace-section" aria-labelledby="assigned-balance-heading">
          <div className="section-heading">
            <span className="step-number" aria-hidden="true">2</span>
            <div><h3 id="assigned-balance-heading">Saldo atual do estoque</h3><p>{balanceDate ? `Data operacional: ${displayDate(balanceDate)}.` : 'Saldo calculado pelas movimentações registradas.'}</p></div>
          </div>
          <div className="balance-toolbar">
            <p className="field-hint">{activeStore?.name ?? 'Loja selecionada'}</p>
            <button className="secondary-button" disabled={busy || !activeStore?.effectiveCalendar} onClick={() => void loadBalances(selectedStoreId)} type="button">Atualizar saldo</button>
          </div>
          <div className="table-scroll" role="region" aria-label="Saldo atual por produto" tabIndex={0}>
            <table className="balance-table">
              <thead><tr><th scope="col">SKU</th><th scope="col">Produto</th><th scope="col">Unidade</th><th scope="col">Saldo</th></tr></thead>
              <tbody>
                {balances.map((balance) => <tr key={balance.productId}><td>{balance.sku}</td><td>{balance.productName}</td><td>{unitLabel(balance.baseUnit)}</td><td>{balance.quantity}</td></tr>)}
                {balances.length === 0 && balanceLoaded && <tr><td colSpan={4}>Nenhum movimento de estoque retornado para esta loja.</td></tr>}
                {balances.length === 0 && !balanceLoaded && <tr><td colSpan={4}>Saldo não carregado. Confira o aviso da solicitação.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!loading && selectedStoreId && (
        <>
          <DailySalesPanel
            assortments={assortments}
            canEdit={isAdministrator || role === 'store_manager' || role === 'operator'}
            onBusyChange={setDailySalesBusy}
            storeDetailsLoading={storeDetailsLoading}
            storeId={selectedStoreId}
            storeName={activeStore?.name ?? 'loja selecionada'}
          />
          <ReplenishmentRecommendationsPanel
            assortments={assortments}
            categories={categories}
            enabled={isAdministrator || role === 'store_manager' || role === 'buyer'}
            canManageEvidence={isAdministrator || role === 'store_manager'}
            canManageSettings={isAdministrator}
            onCategoryCreated={(category) => setCategories((current) => current.some((item) => item.id === category.id) ? current : [...current, category].sort((left, right) => left.name.localeCompare(right.name, 'pt-BR')))}
            products={products}
            storeId={selectedStoreId}
            storeName={activeStore?.name ?? 'loja selecionada'}
          />
          <PurchaseOrdersPanel
            enabled={isAdministrator || role === 'store_manager' || role === 'buyer' || role === 'operator'}
            isAdministrator={isAdministrator}
            role={role}
            storeId={selectedStoreId}
            storeName={activeStore?.name ?? 'loja selecionada'}
          />
          {isAdministrator && <TeamAccessPanel organizationName={organizationName} />}
        </>
      )}
      <SupplierCatalogPanel canManageCatalog={canManageCatalog} isAdministrator={isAdministrator} role={role} />
    </div>
  );
}
