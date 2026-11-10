import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { apiGet, apiPatch, apiPost, ApiError } from './api';

type PurchaseOrderStatus = 'draft' | 'reviewed' | 'approved' | 'partially_received' | 'received' | 'cancelled' | 'stale' | string;
type PurchaseOrderItem = {
  productId: string;
  sku: string;
  productName: string;
  purchaseUnitName: string;
  baseUnitsPerPurchaseUnit: string;
  recommendedQuantity: string;
  quantity: string;
  approvedQuantity: string | null;
  receivedQuantity: string;
  unitPrice: string | null;
  estimatedLineTotal: string | null;
  minimumPurchaseQuantity: string;
  purchaseMultiple: string;
  sourceSnapshot: Record<string, unknown>;
};
type PurchaseOrder = {
  id: string;
  storeId: string;
  supplierId: string;
  supplierName: string;
  status: PurchaseOrderStatus;
  items: PurchaseOrderItem[];
  estimatedTotal: string | null;
  committedAmount: string | null;
  budgetWeekStart: string | null;
  budgetMonthStart: string | null;
  budgetStatus: string;
  stale: boolean;
  createdAt: string;
  approvedAt: string | null;
  budgetExceptionReason?: string | null;
  freightStatus?: string;
  costCompleteness?: string;
};
type Receipt = {
  id: string;
  receivedAt: string;
  supplierReference: string | null;
  lines: Array<{
    productId: string;
    physicalQuantity: string;
    acceptedQuantity: string;
    pendingExcessQuantity: string;
    actualUnitPrice: string;
    lineTotal: string;
  }>;
};
type PurchaseBudget = {
  scope: 'network' | 'store';
  storeId?: string;
  weeklyLimit: string | null;
  monthlyLimit: string | null;
  weekStartsOn: number | null;
  effectiveWeeklyLimit?: string | null;
  effectiveMonthlyLimit?: string | null;
  inheritsNetwork?: boolean;
};
type RecommendationSupplier = {
  supplierId: string;
  supplierName: string;
  selected: boolean;
  availability: string;
  purchaseUnitName: string;
  orderQuantity: string | null;
  knownMerchandiseCost?: string | null;
  costCompleteness?: string;
};
type ReplenishmentRecommendation = {
  product: { id: string; sku: string; name: string; baseUnit: 'unit' | 'kg' | 'l' };
  quantityToOrder: string | null;
  supplierComparisons: RecommendationSupplier[];
};
type RecommendationResponse = { recommendations: ReplenishmentRecommendation[] };
type Props = {
  storeId: string;
  storeName: string;
  role: string;
  isAdministrator: boolean;
  enabled: boolean;
};
type BudgetForm = { weeklyLimit: string; monthlyLimit: string; weekStartsOn: string };

const DECIMAL_PATTERN = /^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$/;
const MONEY_PATTERN = /^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$/;
const WEEKDAY_OPTIONS = [
  'Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo',
];

function displayDecimal(value: string): string {
  return value.replace('.', ',');
}

function displayDate(value: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${value.slice(0, 10)}T00:00:00Z`));
}

function displayDateTime(value: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function statusLabel(status: PurchaseOrderStatus): string {
  const labels: Record<string, string> = {
    draft: 'Rascunho',
    reviewed: 'Revisado',
    approved: 'Aprovado',
    partially_received: 'Recebido parcialmente',
    received: 'Recebido',
    cancelled: 'Cancelado',
    stale: 'Desatualizado',
  };
  return labels[status] ?? status.replaceAll('_', ' ');
}

function budgetStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    not_approved: 'Aguardando aprovação',
    within_limits: 'Dentro dos limites semanal e mensal',
    exception_approved: 'Aprovado com exceção justificada',
  };
  return labels[status] ?? status.replaceAll('_', ' ');
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    const known: Record<string, string> = {
      purchase_order_stale: 'O pedido está desatualizado. Recarregue a recomendação e revise os itens antes de continuar.',
      purchase_budget_exception_required: 'Informe uma justificativa para aprovar acima do orçamento.',
      purchase_budget_required: 'Configure os limites semanal e mensal antes de aprovar o pedido.',
      purchase_budget_exceeded: 'O pedido ultrapassa um limite semanal ou mensal. Informe a justificativa de exceção e reenvie para aprovação.',
      purchase_order_conflict: 'O pedido mudou desde a última leitura. Recarregue e revise os dados atuais.',
    };
    return known[error.code ?? ''] ?? `Erro ${error.status}${error.code ? ` (${error.code})` : ''}: ${fields || error.message}`;
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a operação.';
}

function newBudgetForm(budget: PurchaseBudget | null): BudgetForm {
  return {
    weeklyLimit: budget?.weeklyLimit ?? '',
    monthlyLimit: budget?.monthlyLimit ?? '',
    weekStartsOn: String(budget?.weekStartsOn ?? 0),
  };
}

function upsertOrder(current: PurchaseOrder[], updated: PurchaseOrder): PurchaseOrder[] {
  const orders = new Map(current.map((order) => [order.id, order]));
  orders.set(updated.id, updated);
  return [...orders.values()].sort((left, right) => right.createdAt.localeCompare(left.createdAt));
}

function onlineNow(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine;
}

export default function PurchaseOrdersPanel({ storeId, storeName, role, isAdministrator, enabled }: Props) {
  const canManageOrders = isAdministrator || role === 'store_manager';
  const canCreateDraft = canManageOrders || role === 'buyer';
  const canReviewOrders = canManageOrders || role === 'buyer';
  const canApproveOrders = isAdministrator || role === 'store_manager';
  const canReadOrders = isAdministrator || role === 'store_manager' || role === 'buyer';
  const [online, setOnline] = useState(onlineNow);
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [recommendations, setRecommendations] = useState<ReplenishmentRecommendation[]>([]);
  const [networkBudget, setNetworkBudget] = useState<PurchaseBudget | null>(null);
  const [storeBudget, setStoreBudget] = useState<PurchaseBudget | null>(null);
  const [networkBudgetForm, setNetworkBudgetForm] = useState<BudgetForm>(newBudgetForm(null));
  const [storeBudgetForm, setStoreBudgetForm] = useState<BudgetForm>(newBudgetForm(null));
  const [selectedSupplierId, setSelectedSupplierId] = useState('');
  const [selectedProductIds, setSelectedProductIds] = useState<string[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<PurchaseOrder | null>(null);
  const [editSupplierId, setEditSupplierId] = useState('');
  const [editQuantities, setEditQuantities] = useState<Record<string, string>>({});
  const [changeReason, setChangeReason] = useState('');
  const [budgetExceptionReason, setBudgetExceptionReason] = useState('');
  const [supplierReference, setSupplierReference] = useState('');
  const [receiptQuantities, setReceiptQuantities] = useState<Record<string, string>>({});
  const [receiptPrices, setReceiptPrices] = useState<Record<string, string>>({});
  const [cancelReason, setCancelReason] = useState('');
  const [latestReceipt, setLatestReceipt] = useState<Receipt | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloadSequence, setReloadSequence] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const supplierOptions = useMemo(() => {
    const options = new Map<string, string>();
    for (const recommendation of recommendations) {
      for (const supplier of recommendation.supplierComparisons) {
        if (supplier.availability === 'available' && supplier.orderQuantity !== null) {
          options.set(supplier.supplierId, supplier.supplierName);
        }
      }
    }
    return [...options.entries()].map(([id, name]) => ({ id, name })).sort((left, right) => left.name.localeCompare(right.name, 'pt-BR'));
  }, [recommendations]);

  const createableRecommendations = useMemo(() => recommendations.filter((item) =>
    item.supplierComparisons.some((supplier) => supplier.supplierId === selectedSupplierId && supplier.availability === 'available' && supplier.orderQuantity !== null),
  ), [recommendations, selectedSupplierId]);

  useEffect(() => {
    const updateOnline = () => setOnline(navigator.onLine);
    window.addEventListener('online', updateOnline);
    window.addEventListener('offline', updateOnline);
    return () => {
      window.removeEventListener('online', updateOnline);
      window.removeEventListener('offline', updateOnline);
    };
  }, []);

  useEffect(() => {
    if (!enabled || !canReadOrders || !storeId) {
      setOrders([]);
      setRecommendations([]);
      setNetworkBudget(null);
      setStoreBudget(null);
      setSelectedOrder(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    Promise.all([
      apiGet<{ purchaseOrders: PurchaseOrder[] }>(`/stores/${storeId}/purchase-orders/`),
      apiGet<RecommendationResponse>(`/stores/${storeId}/replenishment-recommendations/`),
      apiGet<PurchaseBudget>('/purchase-budgets/'),
      apiGet<PurchaseBudget>(`/stores/${storeId}/purchase-budget/`),
    ])
      .then(([orderList, recommendationData, network, store]) => {
        if (cancelled) return;
        setOrders(orderList.purchaseOrders);
        setRecommendations(recommendationData.recommendations);
        setNetworkBudget(network);
        setStoreBudget(store);
        setNetworkBudgetForm(newBudgetForm(network));
        setStoreBudgetForm(newBudgetForm(store));
        setSelectedOrder((current) => current ? orderList.purchaseOrders.find((order) => order.id === current.id) ?? null : null);
        setSelectedSupplierId((current) => current && recommendationData.recommendations.some((item) => item.supplierComparisons.some((supplier) => supplier.supplierId === current))
          ? current
          : recommendationData.recommendations.flatMap((item) => item.supplierComparisons).find((supplier) => supplier.selected && supplier.orderQuantity !== null)?.supplierId ?? '');
      })
      .catch((requestError: unknown) => { if (!cancelled) setError(errorMessage(requestError)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [canReadOrders, enabled, reloadSequence, storeId]);

  useEffect(() => {
    if (!selectedOrder) {
      setEditSupplierId('');
      setEditQuantities({});
      setChangeReason('');
      setBudgetExceptionReason('');
      setReceiptQuantities({});
      setReceiptPrices({});
      setCancelReason('');
      return;
    }
    setEditSupplierId(selectedOrder.supplierId);
    setEditQuantities(Object.fromEntries(selectedOrder.items.map((item) => [item.productId, item.quantity])));
    setChangeReason('');
    setBudgetExceptionReason('');
    setReceiptQuantities({});
    setReceiptPrices({});
    setCancelReason('');
  }, [selectedOrder?.id, selectedOrder?.status]);

  async function refreshOrders() {
    setReloadSequence((current) => current + 1);
  }

  async function openOrder(orderId: string) {
    setBusy(true);
    setError('');
    setNotice('');
    setLatestReceipt(null);
    try {
      const order = await apiGet<PurchaseOrder>(`/stores/${storeId}/purchase-orders/${orderId}/`);
      setSelectedOrder(order);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  function updateOrders(updated: PurchaseOrder) {
    setOrders((current) => upsertOrder(current, updated));
    setSelectedOrder(updated);
  }

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!online || !canCreateDraft || !selectedSupplierId || selectedProductIds.length === 0) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const order = await apiPost<PurchaseOrder>(`/stores/${storeId}/purchase-orders/`, {
        supplierId: selectedSupplierId,
        items: selectedProductIds.map((productId) => ({ productId })),
      });
      updateOrders(order);
      setSelectedProductIds([]);
      setNotice('Rascunho criado com as quantidades calculadas nas recomendações atuais.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedOrder || !online || !canManageOrders) return;
    for (const item of selectedOrder.items) {
      const quantity = editQuantities[item.productId] ?? '';
      if (!DECIMAL_PATTERN.test(quantity)) {
        setError(`Informe uma quantidade válida para ${item.productName}, com até três casas decimais.`);
        return;
      }
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const order = await apiPatch<PurchaseOrder>(`/stores/${storeId}/purchase-orders/${selectedOrder.id}/`, {
        supplierId: editSupplierId,
        items: selectedOrder.items.map((item) => ({ productId: item.productId, quantity: editQuantities[item.productId] })),
        ...(changeReason.trim() ? { changeReason: changeReason.trim() } : {}),
      });
      updateOrders(order);
      setNotice('Rascunho salvo. O servidor revalidou as condições, as quantidades e o orçamento.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function reviewDraft() {
    if (!selectedOrder || !online || !canReviewOrders) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const order = await apiPost<PurchaseOrder>(`/stores/${storeId}/purchase-orders/${selectedOrder.id}/review/`, {});
      updateOrders(order);
      setNotice('Pedido revisado. A aprovação ainda precisa ser feita por uma pessoa autorizada.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function approveOrder() {
    if (!selectedOrder || !online) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const order = await apiPost<PurchaseOrder>(`/stores/${storeId}/purchase-orders/${selectedOrder.id}/approve/`, {
        ...(budgetExceptionReason.trim() ? { budgetExceptionReason: budgetExceptionReason.trim() } : {}),
      });
      updateOrders(order);
      setNotice('Pedido aprovado e orçamento revalidado pelo servidor.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function recordReceipt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedOrder || !online) return;
    const items: Array<{ productId: string; physicalQuantity: string; actualUnitPrice: string }> = [];
    for (const item of selectedOrder.items) {
      const physicalQuantity = (receiptQuantities[item.productId] ?? '').trim();
      const actualUnitPrice = (receiptPrices[item.productId] ?? '').trim();
      if (!physicalQuantity) continue;
      if (!DECIMAL_PATTERN.test(physicalQuantity) || !MONEY_PATTERN.test(actualUnitPrice)) {
        setError(`Confira quantidade e preço por unidade de compra para ${item.productName}.`);
        return;
      }
      items.push({ productId: item.productId, physicalQuantity, actualUnitPrice });
    }
    if (items.length === 0) {
      setError('Informe ao menos um produto recebido.');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const receipt = await apiPost<Receipt>(`/stores/${storeId}/purchase-orders/${selectedOrder.id}/receipts/`, {
        ...(supplierReference.trim() ? { supplierReference: supplierReference.trim() } : {}),
        items,
        ...(budgetExceptionReason.trim() ? { budgetExceptionReason: budgetExceptionReason.trim() } : {}),
      });
      setLatestReceipt(receipt);
      setSupplierReference('');
      setReceiptQuantities({});
      const refreshed = await apiGet<PurchaseOrder>(`/stores/${storeId}/purchase-orders/${selectedOrder.id}/`);
      updateOrders(refreshed);
      setNotice('Recebimento registrado. O estoque foi atualizado com as quantidades aceitas.');
      setReloadSequence((current) => current + 1);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function cancelRemaining(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedOrder || !online || !cancelReason.trim()) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const order = await apiPost<PurchaseOrder>(`/stores/${storeId}/purchase-orders/${selectedOrder.id}/cancel-remaining/`, { reason: cancelReason.trim() });
      updateOrders(order);
      setNotice('Quantidade ainda não recebida cancelada e compromisso correspondente liberado.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function saveBudget(event: FormEvent<HTMLFormElement>, scope: 'network' | 'store') {
    event.preventDefault();
    if (!online || !isAdministrator) return;
    const form = scope === 'network' ? networkBudgetForm : storeBudgetForm;
    const path = scope === 'network' ? '/purchase-budgets/' : `/stores/${storeId}/purchase-budget/`;
    if (scope === 'network' && (!form.weeklyLimit.trim() || !form.monthlyLimit.trim())) {
      setError('A rede precisa definir os limites semanal e mensal antes de salvar.');
      return;
    }
    for (const [label, value] of [['semanal', form.weeklyLimit], ['mensal', form.monthlyLimit]] as const) {
      if (value.trim() && !MONEY_PATTERN.test(value.trim())) {
        setError(`Informe um limite ${label} válido, sem arredondamento e com até duas casas decimais.`);
        return;
      }
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const budget = await apiPatch<PurchaseBudget>(path, {
        weeklyLimit: form.weeklyLimit.trim() || null,
        monthlyLimit: form.monthlyLimit.trim() || null,
        ...(scope === 'network' ? { weekStartsOn: Number(form.weekStartsOn) } : {}),
      });
      if (scope === 'network') {
        setNetworkBudget(budget);
        setNetworkBudgetForm(newBudgetForm(budget));
      } else {
        setStoreBudget(budget);
        setStoreBudgetForm(newBudgetForm(budget));
      }
      setNotice(scope === 'network' ? 'Orçamento padrão da rede salvo.' : 'Ajuste de orçamento desta loja salvo.');
      setReloadSequence((current) => current + 1);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  function toggleProduct(productId: string) {
    setSelectedProductIds((current) => current.includes(productId)
      ? current.filter((id) => id !== productId)
      : [...current, productId]);
  }

  const effectiveWeeklyLimit = storeBudget?.effectiveWeeklyLimit ?? storeBudget?.weeklyLimit ?? networkBudget?.effectiveWeeklyLimit ?? networkBudget?.weeklyLimit ?? null;
  const effectiveMonthlyLimit = storeBudget?.effectiveMonthlyLimit ?? storeBudget?.monthlyLimit ?? networkBudget?.effectiveMonthlyLimit ?? networkBudget?.monthlyLimit ?? null;
  const weekStartsOn = networkBudget?.weekStartsOn ?? storeBudget?.weekStartsOn;

  return (
    <section className="workspace-section purchase-orders-panel" aria-labelledby="purchase-orders-heading" aria-busy={loading}>
      <div className="section-heading purchase-orders-heading">
        <span className="step-number" aria-hidden="true">8</span>
        <div>
          <h3 id="purchase-orders-heading">Pedidos e orçamento</h3>
          <p>Rascunhos, revisão, aprovação online e recebimentos de {storeName}.</p>
        </div>
        <button className="secondary-button purchase-orders-refresh" disabled={loading || !enabled || !storeId} onClick={() => void refreshOrders()} type="button">
          {loading ? 'Atualizando…' : 'Atualizar pedidos'}
        </button>
      </div>

      {!enabled && <p className="empty-state">O painel de compras ficará disponível quando houver contexto de loja autorizado.</p>}
      {enabled && !storeId && <p className="empty-state">Selecione uma loja para consultar orçamento e pedidos.</p>}
      {!online && enabled && <p className="calendar-block-message" role="status">Você está sem conexão. Criar, revisar, aprovar e receber pedidos exigem conexão ativa; os botões ficam bloqueados até reconectar.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-notice" role="status">{notice}</p>}

      {enabled && storeId && !loading && (
        <>
          <div className="purchase-budget-summary">
            <div>
              <span>Limite semanal efetivo</span>
              <strong>{effectiveWeeklyLimit === null ? 'Não configurado' : `R$ ${displayDecimal(effectiveWeeklyLimit)}`}</strong>
            </div>
            <div>
              <span>Limite mensal efetivo</span>
              <strong>{effectiveMonthlyLimit === null ? 'Não configurado' : `R$ ${displayDecimal(effectiveMonthlyLimit)}`}</strong>
            </div>
            <div>
              <span>Semana começa</span>
              <strong>{weekStartsOn === null || weekStartsOn === undefined ? 'Não configurado' : WEEKDAY_OPTIONS[weekStartsOn] ?? 'Não configurado'}</strong>
            </div>
            <div>
              <span>Origem dos limites</span>
              <strong>{storeBudget?.inheritsNetwork ? 'Padrão da rede' : 'Ajuste desta loja'}</strong>
            </div>
          </div>
          {(effectiveWeeklyLimit === null || effectiveMonthlyLimit === null) && (
            <p className="calendar-block-message" role="status">Falta configurar um limite semanal e/ou mensal. O servidor validará as exceções antes da aprovação.</p>
          )}

          {isAdministrator && (
            <details className="purchase-budget-settings">
              <summary>Configurar limites de orçamento</summary>
              <p className="field-hint">Só o administrador da rede pode mudar os limites. O padrão vale para a rede; limites vazios na loja herdam os valores da rede.</p>
              <div className="purchase-budget-editors">
                <form className="purchase-budget-form" onSubmit={(event) => void saveBudget(event, 'network')} aria-busy={busy}>
                  <h4>Padrão da rede</h4>
                  <label htmlFor="networkWeeklyBudget">Limite semanal (R$)</label>
                  <input autoComplete="off" disabled={busy || !online} id="networkWeeklyBudget" inputMode="decimal" onChange={(event) => setNetworkBudgetForm((current) => ({ ...current, weeklyLimit: event.target.value }))} placeholder="Sem limite configurado" type="text" value={networkBudgetForm.weeklyLimit} />
                  <label htmlFor="networkMonthlyBudget">Limite mensal (R$)</label>
                  <input autoComplete="off" disabled={busy || !online} id="networkMonthlyBudget" inputMode="decimal" onChange={(event) => setNetworkBudgetForm((current) => ({ ...current, monthlyLimit: event.target.value }))} placeholder="Sem limite configurado" type="text" value={networkBudgetForm.monthlyLimit} />
                  <label htmlFor="networkWeekStartsOn">Início da semana</label>
                  <select disabled={busy || !online} id="networkWeekStartsOn" onChange={(event) => setNetworkBudgetForm((current) => ({ ...current, weekStartsOn: event.target.value }))} value={networkBudgetForm.weekStartsOn}>
                    {WEEKDAY_OPTIONS.map((day, index) => <option key={day} value={index}>{day}</option>)}
                  </select>
                  <p className="field-hint">Informe valores em reais usando ponto decimal e até duas casas. A rede precisa manter os dois limites configurados.</p>
                  <button className="secondary-button" disabled={busy || !online} type="submit">Salvar padrão da rede</button>
                </form>
                <form className="purchase-budget-form" onSubmit={(event) => void saveBudget(event, 'store')} aria-busy={busy}>
                  <h4>Ajustes · {storeName}</h4>
                  <label htmlFor="storeWeeklyBudget">Limite semanal (R$)</label>
                  <input autoComplete="off" disabled={busy || !online} id="storeWeeklyBudget" inputMode="decimal" onChange={(event) => setStoreBudgetForm((current) => ({ ...current, weeklyLimit: event.target.value }))} placeholder="Herdar da rede" type="text" value={storeBudgetForm.weeklyLimit} />
                  <label htmlFor="storeMonthlyBudget">Limite mensal (R$)</label>
                  <input autoComplete="off" disabled={busy || !online} id="storeMonthlyBudget" inputMode="decimal" onChange={(event) => setStoreBudgetForm((current) => ({ ...current, monthlyLimit: event.target.value }))} placeholder="Herdar da rede" type="text" value={storeBudgetForm.monthlyLimit} />
                  <p className="field-hint">Apague um valor para herdar o padrão da rede. O início da semana é definido na rede.</p>
                  <button className="secondary-button" disabled={busy || !online} type="submit">Salvar ajuste da loja</button>
                </form>
              </div>
            </details>
          )}

          <details className="purchase-order-create" open={orders.length === 0}>
            <summary>Criar rascunho a partir da recomendação</summary>
            <p className="field-hint">Escolha um fornecedor e os produtos disponíveis para ele. O servidor cria o rascunho com as quantidades calculadas e guarda o snapshot da recomendação.</p>
            {recommendations.length === 0 && <p className="empty-state">Não há recomendações de compra para criar um rascunho. Confira o painel de recomendações acima.</p>}
            {recommendations.length > 0 && (
              <form className="purchase-order-create-form" onSubmit={(event) => void createDraft(event)} aria-busy={busy}>
                <label htmlFor="purchaseDraftSupplier">Fornecedor</label>
                <select disabled={busy || !online || supplierOptions.length === 0} id="purchaseDraftSupplier" onChange={(event) => { setSelectedSupplierId(event.target.value); setSelectedProductIds([]); }} value={selectedSupplierId}>
                  <option value="">Selecione um fornecedor</option>
                  {supplierOptions.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
                </select>
                <div className="purchase-order-product-options">
                  {createableRecommendations.map((recommendation) => {
                    const condition = recommendation.supplierComparisons.find((supplier) => supplier.supplierId === selectedSupplierId);
                    return (
                      <label className="purchase-order-product-option" htmlFor={`draft-product-${recommendation.product.id}`} key={recommendation.product.id}>
                        <input checked={selectedProductIds.includes(recommendation.product.id)} disabled={busy || !online} id={`draft-product-${recommendation.product.id}`} onChange={() => toggleProduct(recommendation.product.id)} type="checkbox" />
                        <span><strong>{recommendation.product.sku} · {recommendation.product.name}</strong><small>Quantidade sugerida: {condition?.orderQuantity === null ? 'não disponível' : `${displayDecimal(condition?.orderQuantity ?? '')} ${condition?.purchaseUnitName ?? ''}`}</small>{condition?.knownMerchandiseCost != null && <small>Mercadoria: R$ {displayDecimal(condition.knownMerchandiseCost)} · frete não incluído/parcial</small>}</span>
                      </label>
                    );
                  })}
                  {selectedSupplierId && createableRecommendations.length === 0 && <p className="empty-state">Nenhum item tem quantidade de compra válida para este fornecedor.</p>}
                </div>
                <button className="submit-button" disabled={busy || !online || !canCreateDraft || !selectedSupplierId || selectedProductIds.length === 0} type="submit">{busy ? 'Criando…' : 'Criar rascunho'}</button>
                {!canCreateDraft && <p className="field-hint">Seu perfil pode consultar os pedidos; um comprador ou gerente autorizado cria o rascunho.</p>}
              </form>
            )}
          </details>

          <div className="purchase-orders-list-heading">
            <div><h4>Pedidos da loja</h4><p>{orders.length} pedido(s) · cada pedido permanece vinculado a esta loja.</p></div>
            <span>Atualizado ao consultar</span>
          </div>
          <ul className="purchase-order-list" aria-label={`Pedidos de ${storeName}`}>
            {orders.map((order) => (
              <li className={selectedOrder?.id === order.id ? 'purchase-order-list-item is-selected' : 'purchase-order-list-item'} key={order.id}>
                <div className="purchase-order-list-main">
                  <div>
                    <strong>{order.supplierName}</strong>
                    <span>{statusLabel(order.status)} · criado {displayDateTime(order.createdAt)}{order.stale ? ' · requer recálculo' : ''}</span>
                  </div>
                  <div className="purchase-order-list-values">
                    <span>Estimado: {order.estimatedTotal === null ? '—' : `R$ ${displayDecimal(order.estimatedTotal)}`}</span>
                    <span>Comprometido: {order.committedAmount === null ? 'R$ 0,00' : `R$ ${displayDecimal(order.committedAmount)}`}</span>
                  </div>
                  <button className="text-button" disabled={busy} onClick={() => void openOrder(order.id)} type="button">{selectedOrder?.id === order.id ? 'Recarregar detalhes' : 'Abrir pedido'}</button>
                </div>
              </li>
            ))}
            {orders.length === 0 && !loading && <li className="empty-state">Ainda não há pedidos nesta loja.</li>}
          </ul>

          {selectedOrder && (
            <section className="purchase-order-detail" aria-labelledby="purchase-order-detail-heading" aria-busy={busy}>
              <div className="purchase-order-detail-heading">
                <div>
                  <p className="eyebrow">Detalhes do pedido</p>
                  <h4 id="purchase-order-detail-heading">{selectedOrder.supplierName}</h4>
                  <p>{statusLabel(selectedOrder.status)} · criado {displayDateTime(selectedOrder.createdAt)}{selectedOrder.approvedAt ? ` · aprovado ${displayDateTime(selectedOrder.approvedAt)}` : ''}</p>
                </div>
                <button className="text-button" disabled={busy || !online} onClick={() => void openOrder(selectedOrder.id)} type="button">Recarregar</button>
              </div>
              {selectedOrder.stale && <p className="calendar-block-message" role="status">Este rascunho está desatualizado. Confira as recomendações atuais e revalide antes de solicitar aprovação.</p>}
              <p className="field-hint">O valor comprometido só muda com aprovação e é revalidado pelo servidor.</p>
              {(selectedOrder.freightStatus === 'unknown' || selectedOrder.costCompleteness === 'partial') && <p className="calendar-block-message" role="status">Frete não informado; os valores exibidos são parciais e representam mercadoria.</p>}
              <div className="purchase-budget-summary purchase-order-totals">
                <div><span>Estimativa de mercadoria</span><strong>{selectedOrder.estimatedTotal === null ? '—' : `R$ ${displayDecimal(selectedOrder.estimatedTotal)}`}</strong></div>
                <div><span>Comprometido</span><strong>{selectedOrder.committedAmount === null ? 'R$ 0,00' : `R$ ${displayDecimal(selectedOrder.committedAmount)}`}</strong></div>
                <div><span>Validação do orçamento</span><strong>{budgetStatusLabel(selectedOrder.budgetStatus)}</strong></div>
                <div><span>Períodos de aprovação</span><strong>Semana {displayDate(selectedOrder.budgetWeekStart)} · mês {displayDate(selectedOrder.budgetMonthStart)}</strong></div>
              </div>
              {selectedOrder.budgetExceptionReason && <p className="purchase-receipt-result" role="status"><strong>Justificativa de exceção:</strong> {selectedOrder.budgetExceptionReason}</p>}
              <div className="table-scroll purchase-order-lines-scroll" role="region" aria-label="Itens do pedido" tabIndex={0}>
                <table className="balance-table purchase-order-lines-table">
                  <thead><tr><th scope="col">Produto</th><th scope="col">Mínimo / embalagem</th><th scope="col">Qtd. sugerida</th><th scope="col">Qtd. no pedido</th><th scope="col">Aprovada</th><th scope="col">Recebida</th><th scope="col">Preço/unidade</th><th scope="col">Estimativa da linha</th></tr></thead>
                  <tbody>
                    {selectedOrder.items.map((item) => {
                      const baseUnit = recommendations.find((recommendation) => recommendation.product.id === item.productId)?.product.baseUnit;
                      return (
                        <tr key={item.productId}>
                          <th scope="row">{item.sku} · {item.productName}<span className="purchase-order-unit-note">1 {item.purchaseUnitName} = {displayDecimal(item.baseUnitsPerPurchaseUnit)} {baseUnit === 'unit' ? 'unidades' : baseUnit ?? 'unidade-base'}</span></th>
                          <td>{displayDecimal(item.minimumPurchaseQuantity)} / {displayDecimal(item.purchaseMultiple)} {item.purchaseUnitName}</td>
                          <td>{displayDecimal(item.recommendedQuantity)} {item.purchaseUnitName}</td>
                          <td>{displayDecimal(item.quantity)} {item.purchaseUnitName}</td>
                          <td>{item.approvedQuantity === null ? '—' : `${displayDecimal(item.approvedQuantity)} ${item.purchaseUnitName}`}</td>
                          <td>{displayDecimal(item.receivedQuantity)} {item.purchaseUnitName}</td>
                          <td>{item.unitPrice === null ? '—' : `R$ ${displayDecimal(item.unitPrice)}`}</td>
                          <td>{item.estimatedLineTotal === null ? '—' : `R$ ${displayDecimal(item.estimatedLineTotal)}`}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {latestReceipt && (
                <div className="purchase-receipt-result" role="status">
                  <h5>Recebimento registrado · {displayDateTime(latestReceipt.receivedAt)}</h5>
                  {latestReceipt.supplierReference && <p>Referência do fornecedor: {latestReceipt.supplierReference}</p>}
                  <div className="table-scroll" role="region" aria-label="Resultado do recebimento" tabIndex={0}>
                    <table className="balance-table">
                      <thead><tr><th scope="col">Produto</th><th scope="col">Recebido fisicamente</th><th scope="col">Aceito no estoque</th><th scope="col">Excedente pendente</th><th scope="col">Preço real/unidade</th></tr></thead>
                      <tbody>{latestReceipt.lines.map((line) => {
                        const item = selectedOrder.items.find((candidate) => candidate.productId === line.productId);
                        return <tr key={line.productId}><th scope="row">{item?.productName ?? line.productId}</th><td>{displayDecimal(line.physicalQuantity)}</td><td>{displayDecimal(line.acceptedQuantity)}</td><td>{displayDecimal(line.pendingExcessQuantity)}</td><td>R$ {displayDecimal(line.actualUnitPrice)}</td></tr>;
                      })}</tbody>
                    </table>
                  </div>
                  <p className="field-hint">Apenas a quantidade aceita atualiza o estoque. O excedente fica pendente e fora do saldo até uma decisão em uma etapa futura.</p>
                </div>
              )}

              {selectedOrder.status === 'draft' && (
                <form className="workspace-form purchase-order-action-form" onSubmit={(event) => void saveDraft(event)} aria-busy={busy}>
                  <h5>Editar rascunho</h5>
                  <label htmlFor="editDraftSupplier">Fornecedor</label>
                  <select disabled={busy || !online || !canManageOrders} id="editDraftSupplier" onChange={(event) => setEditSupplierId(event.target.value)} value={editSupplierId}>
                    {!supplierOptions.some((supplier) => supplier.id === selectedOrder.supplierId) && <option value={selectedOrder.supplierId}>{selectedOrder.supplierName} · fornecedor atual</option>}
                    {supplierOptions.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
                  </select>
                  {selectedOrder.items.map((item) => (
                    <div className="purchase-order-edit-line" key={item.productId}>
                      <label htmlFor={`edit-quantity-${item.productId}`}>{item.sku} · {item.productName} ({item.purchaseUnitName})</label>
                      <input autoComplete="off" disabled={busy || !online || !canManageOrders} id={`edit-quantity-${item.productId}`} inputMode="decimal" onChange={(event) => setEditQuantities((current) => ({ ...current, [item.productId]: event.target.value }))} placeholder="Quantidade de compra" type="text" value={editQuantities[item.productId] ?? ''} />
                    </div>
                  ))}
                  <label htmlFor="draftChangeReason">Justificativa da mudança (se necessária)</label>
                  <textarea disabled={busy || !online || !canManageOrders} id="draftChangeReason" onChange={(event) => setChangeReason(event.target.value)} placeholder="Informe quando mudar para uma condição de maior risco ou reduzir a quantidade sugerida." value={changeReason} />
                  <p className="field-hint">Quantidades são na unidade de compra. O servidor exige justificativa quando a mudança aumenta o risco ou reduz a sugestão, e valida novamente os parâmetros.</p>
                  <button className="secondary-button" disabled={busy || !online || !canManageOrders} type="submit">Salvar alterações do rascunho</button>
                </form>
              )}

              <div className="purchase-order-actions">
                {selectedOrder.status === 'draft' && (
                  <div className="purchase-order-action-block">
                    <h5>Revisão</h5>
                    <p className="field-hint">A revisão e a aprovação são online. Revisar registra o estado atual do pedido para a etapa de aprovação.</p>
                    <button className="secondary-button" disabled={busy || !online || !canReviewOrders || selectedOrder.stale} onClick={() => void reviewDraft()} type="button">Solicitar revisão do pedido</button>
                  </div>
                )}
                {selectedOrder.status === 'reviewed' && (
                  <div className="purchase-order-action-block">
                    <h5>Aprovar pedido</h5>
                    {!canApproveOrders && <p className="field-hint">Somente o gerente responsável pela loja pode aprovar este pedido.</p>}
                    <p className="field-hint">A aprovação é online; os limites e a justificativa de exceção são conferidos pelo servidor.</p>
                    <label htmlFor="approvalBudgetException">Justificativa de exceção de orçamento (quando necessária)</label>
                    <textarea disabled={busy || !online || !canApproveOrders} id="approvalBudgetException" onChange={(event) => setBudgetExceptionReason(event.target.value)} placeholder="Explique a exceção, se os limites forem excedidos." value={budgetExceptionReason} />
                    <button className="submit-button" disabled={busy || !online || !canApproveOrders || selectedOrder.stale} onClick={() => void approveOrder()} type="button">Aprovar pedido online</button>
                  </div>
                )}
                {(selectedOrder.status === 'approved' || selectedOrder.status === 'partially_received') && canManageOrders && (
                  <>
                    <form className="workspace-form purchase-order-action-form" onSubmit={(event) => void recordReceipt(event)} aria-busy={busy}>
                      <h5>Registrar recebimento parcial ou total</h5>
                      <p className="field-hint">Informe somente os itens entregues nesta remessa. Quantidade e preço estão na unidade de compra; o preço real substitui a estimativa das unidades aceitas.</p>
                      <label htmlFor="supplierReference">Referência da nota ou entrega (opcional)</label>
                      <input autoComplete="off" disabled={busy || !online} id="supplierReference" onChange={(event) => setSupplierReference(event.target.value)} value={supplierReference} />
                      {selectedOrder.items.map((item) => (
                        <div className="purchase-receipt-line" key={item.productId}>
                          <strong>{item.sku} · {item.productName}</strong>
                          <span>Aprovada: {item.approvedQuantity === null ? '—' : `${displayDecimal(item.approvedQuantity)} ${item.purchaseUnitName}`} · recebida: {displayDecimal(item.receivedQuantity)} {item.purchaseUnitName}</span>
                          <label htmlFor={`receipt-quantity-${item.productId}`}>Quantidade física recebida ({item.purchaseUnitName})</label>
                          <input autoComplete="off" disabled={busy || !online} id={`receipt-quantity-${item.productId}`} inputMode="decimal" onChange={(event) => setReceiptQuantities((current) => ({ ...current, [item.productId]: event.target.value }))} placeholder="Deixe vazio se não chegou" type="text" value={receiptQuantities[item.productId] ?? ''} />
                          <label htmlFor={`receipt-price-${item.productId}`}>Preço real por unidade de compra (R$)</label>
                          <input autoComplete="off" disabled={busy || !online} id={`receipt-price-${item.productId}`} inputMode="decimal" onChange={(event) => setReceiptPrices((current) => ({ ...current, [item.productId]: event.target.value }))} placeholder={item.unitPrice ?? 'Preço da nota'} type="text" value={receiptPrices[item.productId] ?? ''} />
                        </div>
                      ))}
                      <label htmlFor="receiptBudgetException">Justificativa de exceção no recebimento (quando necessária)</label>
                      <textarea disabled={busy || !online} id="receiptBudgetException" onChange={(event) => setBudgetExceptionReason(event.target.value)} placeholder="Use se o custo real exigir uma exceção aos limites." value={budgetExceptionReason} />
                      <button className="secondary-button" disabled={busy || !online} type="submit">Registrar recebimento</button>
                    </form>
                    {canApproveOrders && (
                      <form className="workspace-form purchase-order-action-form" onSubmit={(event) => void cancelRemaining(event)} aria-busy={busy}>
                        <h5>Cancelar quantidade ainda não recebida</h5>
                        <label htmlFor="cancelRemainingReason">Motivo do cancelamento</label>
                        <textarea disabled={busy || !online} id="cancelRemainingReason" onChange={(event) => setCancelReason(event.target.value)} required value={cancelReason} />
                        <p className="field-hint">Informe o motivo; o servidor libera o compromisso correspondente e preserva os recebimentos já registrados.</p>
                        <button className="secondary-button" disabled={busy || !online || !cancelReason.trim()} type="submit">Cancelar saldo pendente</button>
                      </form>
                    )}
                  </>
                )}
              </div>
            </section>
          )}
        </>
      )}
    </section>
  );
}
