import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { apiGet, apiPatch, apiPost, ApiError } from './api';

type PurchaseItem = {
  productId: string;
  sku: string;
  productName: string;
  purchaseUnitName: string;
  approvedQuantity: string | null;
  receivedQuantity: string;
};
type Delivery = {
  id: string;
  productId: string;
  sku: string;
  productName: string;
  purchaseUnitName: string;
  etaDate: string;
  expectedQuantity: string;
  receivedQuantity: string;
  remainingQuantity: string;
  status: 'confirmed' | 'received' | 'cancelled';
  events: Array<{ type: string; oldEtaDate: string | null; newEtaDate: string | null; reason: string; at: string; actorName: string }>;
};
type RiskAlert = {
  id: string;
  productId: string;
  sku: string;
  productName: string;
  oldEtaDate: string;
  newEtaDate: string;
  previousStockoutDate: string | null;
  projectedStockoutDate: string | null;
  status: 'open' | 'acknowledged' | 'resolved' | 'justified';
  actionNote: string;
  resolutionReason: string;
};
type Tracking = { sentToSupplierAt: string | null; deliveries: Delivery[]; alerts: RiskAlert[] };
type Props = {
  storeId: string;
  orderId: string;
  orderStatus: string;
  items: PurchaseItem[];
  role: string;
  isAdministrator: boolean;
  online: boolean;
  onRefresh: () => void;
};

const DECIMAL_PATTERN = /^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$/;

function dateLabel(value: string | null): string {
  if (!value) return 'sem ruptura projetada';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium', timeZone: 'UTC' })
    .format(new Date(value.slice(0, 10) + 'T00:00:00Z'));
}

function dateTimeLabel(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function deliveryEventLabel(entry: Delivery['events'][number]): string {
  if (entry.type === 'eta_changed') return dateLabel(entry.oldEtaDate) + ' → ' + dateLabel(entry.newEtaDate);
  const labels: Record<string, string> = {
    eta_confirmed: 'Prazo confirmado',
    receipt_allocated: 'Recebimento alocado à parcela',
    remaining_delivery_cancelled: 'Saldo da parcela cancelado',
    risk_alert_acknowledged: 'Alerta reconhecido',
    risk_alert_resolved: 'Alerta encerrado como resolvido',
    risk_alert_justified: 'Alerta encerrado com justificativa',
  };
  return labels[entry.type] ?? entry.type.replaceAll('_', ' ');
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    confirmed: 'Confirmada',
    received: 'Recebida',
    cancelled: 'Cancelada',
    open: 'Aberto',
    acknowledged: 'Reconhecido',
    resolved: 'Resolvido',
    justified: 'Justificado',
  };
  return labels[value] ?? value;
}

export default function PurchaseOrderDeliveryTrackingPanel({
  storeId,
  orderId,
  orderStatus,
  items,
  role,
  isAdministrator,
  online,
  onRefresh,
}: Props) {
  const canManage = isAdministrator || role === 'store_manager' || role === 'buyer';
  const canSendOrConfirm = canManage && ['approved', 'partially_received'].includes(orderStatus);
  const [tracking, setTracking] = useState<Tracking>({ sentToSupplierAt: null, deliveries: [], alerts: [] });
  const [quantity, setQuantity] = useState('');
  const [productId, setProductId] = useState('');
  const [etaDate, setEtaDate] = useState('');
  const [etaDrafts, setEtaDrafts] = useState<Record<string, string>>({});
  const [etaReasons, setEtaReasons] = useState<Record<string, string>>({});
  const [alertActions, setAlertActions] = useState<Record<string, string>>({});
  const [alertReasons, setAlertReasons] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const path = '/stores/' + storeId + '/purchase-orders/' + orderId + '/expected-deliveries/';
  const remainingByProduct = useMemo(() => {
    const remaining = new Map<string, number>();
    for (const item of items) {
      const expected = tracking.deliveries
        .filter((delivery) => delivery.productId === item.productId && delivery.status === 'confirmed')
        .reduce((total, delivery) => total + Number(delivery.remainingQuantity), 0);
      const approved = item.approvedQuantity === null ? 0 : Number(item.approvedQuantity);
      remaining.set(item.productId, Math.max(0, approved - Number(item.receivedQuantity) - expected));
    }
    return remaining;
  }, [items, tracking.deliveries]);

  async function loadTracking() {
    const data = await apiGet<Tracking>(path);
    setTracking(data);
  }

  useEffect(() => {
    let cancelled = false;
    apiGet<Tracking>(path)
      .then((data) => { if (!cancelled) setTracking(data); })
      .catch((requestError: unknown) => {
        if (!cancelled) setError(requestError instanceof ApiError ? requestError.message : 'Não foi possível carregar as entregas esperadas.');
      });
    return () => { cancelled = true; };
  }, [path]);

  async function runMutation(action: () => Promise<unknown>, success: string): Promise<boolean> {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
      await loadTracking();
      onRefresh();
      setNotice(success);
      return true;
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : 'Não foi possível atualizar o acompanhamento da entrega.');
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function markSent() {
    await runMutation(
      () => apiPost('/stores/' + storeId + '/purchase-orders/' + orderId + '/send/', {}),
      'Envio ao fornecedor registrado. Agora você pode registrar os prazos confirmados.',
    );
  }

  async function createDelivery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!productId || !DECIMAL_PATTERN.test(quantity) || !etaDate) {
      setError('Escolha o produto e informe quantidade e data válidas.');
      return;
    }
    const saved = await runMutation(
      () => apiPost(path, { productId, quantity, etaDate }),
      'Entrega confirmada registrada e incluída na previsão de estoque.',
    );
    if (saved) {
      setProductId('');
      setQuantity('');
      setEtaDate('');
    }
  }

  async function updateEta(event: FormEvent<HTMLFormElement>, deliveryId: string) {
    event.preventDefault();
    const nextEta = etaDrafts[deliveryId] ?? '';
    const reason = (etaReasons[deliveryId] ?? '').trim();
    if (!nextEta || !reason) {
      setError('Informe a nova data e a justificativa da mudança.');
      return;
    }
    await runMutation(
      () => apiPatch(path + deliveryId + '/', { etaDate: nextEta, reason }),
      'Prazo atualizado. Recomendações e alertas foram reavaliados.',
    );
  }

  async function acknowledge(alert: RiskAlert) {
    const actionNote = (alertActions[alert.id] ?? '').trim();
    if (!actionNote) {
      setError('Descreva a ação tomada antes de reconhecer o alerta.');
      return;
    }
    await runMutation(
      () => apiPost('/stores/' + storeId + '/purchase-orders/' + orderId + '/delivery-risk-alerts/' + alert.id + '/acknowledge/', { actionNote }),
      'Ação do alerta registrada. Ele continua aberto até resolução ou justificativa.',
    );
  }

  async function resolve(alert: RiskAlert, resolution: 'resolved' | 'justified') {
    const reason = (alertReasons[alert.id] ?? '').trim();
    if (!reason) {
      setError('Informe a justificativa para encerrar o alerta.');
      return;
    }
    await runMutation(
      () => apiPost('/stores/' + storeId + '/purchase-orders/' + orderId + '/delivery-risk-alerts/' + alert.id + '/resolve/', { resolution, reason }),
      resolution === 'resolved' ? 'Alerta encerrado como resolvido.' : 'Alerta encerrado com justificativa.',
    );
  }

  const activeAlerts = tracking.alerts.filter((alert) => ['open', 'acknowledged'].includes(alert.status));

  return (
    <section className="delivery-tracking" aria-labelledby="delivery-tracking-heading" aria-busy={busy}>
      <div className="delivery-tracking-heading">
        <div>
          <h5 id="delivery-tracking-heading">Entregas confirmadas pelo fornecedor</h5>
          <p>Somente pedidos enviados e parcelas com prazo confirmado entram no estoque previsto.</p>
        </div>
        {tracking.sentToSupplierAt && (
          <span className="delivery-sent-badge">Enviado em {dateLabel(tracking.sentToSupplierAt.slice(0, 10))}</span>
        )}
      </div>
      {error && <p className="calendar-block-message" role="alert">{error}</p>}
      {notice && <p className="purchase-receipt-result" role="status">{notice}</p>}
      {!tracking.sentToSupplierAt && canSendOrConfirm && (
        <div className="delivery-send-step">
          <p>Registre quando o pedido aprovado foi enviado manualmente. Isso libera o registro de prazos confirmados.</p>
          <button className="secondary-button" disabled={busy || !online} onClick={() => void markSent()} type="button">
            Registrar envio ao fornecedor
          </button>
        </div>
      )}
      {!tracking.sentToSupplierAt && !canSendOrConfirm && (
        <p className="field-hint">O pedido só pode receber parcelas previstas depois de aprovado e registrado como enviado.</p>
      )}
      {tracking.deliveries.length > 0 ? (
        <div className="delivery-list">
          {tracking.deliveries.map((delivery) => (
            <article className="delivery-card" key={delivery.id}>
              <div className="delivery-card-heading">
                <div>
                  <strong>{delivery.sku} · {delivery.productName}</strong>
                  <span>{delivery.status === 'confirmed' ? delivery.remainingQuantity + ' ' + delivery.purchaseUnitName + ' previstos' : delivery.status === 'received' ? delivery.receivedQuantity + ' ' + delivery.purchaseUnitName + ' recebidos' : delivery.expectedQuantity + ' ' + delivery.purchaseUnitName + ' cancelados'}</span>
                </div>
                <span className={'delivery-status delivery-status-' + delivery.status}>{statusLabel(delivery.status)}</span>
              </div>
              <p>Previsão atual: <strong>{dateLabel(delivery.etaDate)}</strong> · quantidade original {delivery.expectedQuantity} {delivery.purchaseUnitName}</p>
              {delivery.status === 'confirmed' && canManage && (
                <form className="delivery-eta-form" onSubmit={(event) => void updateEta(event, delivery.id)}>
                  <label htmlFor={'delivery-eta-' + delivery.id}>Nova previsão</label>
                  <input id={'delivery-eta-' + delivery.id} disabled={busy || !online} type="date" value={etaDrafts[delivery.id] ?? delivery.etaDate} onChange={(event) => setEtaDrafts((current) => ({ ...current, [delivery.id]: event.target.value }))} />
                  <label htmlFor={'delivery-reason-' + delivery.id}>Motivo da alteração</label>
                  <input id={'delivery-reason-' + delivery.id} disabled={busy || !online} maxLength={1000} value={etaReasons[delivery.id] ?? ''} onChange={(event) => setEtaReasons((current) => ({ ...current, [delivery.id]: event.target.value }))} placeholder="Ex.: fornecedor informou atraso" />
                  <button className="text-button" disabled={busy || !online} type="submit">Salvar prazo</button>
                </form>
              )}
              {delivery.events.length > 0 && (
                <details className="delivery-history">
                  <summary>Histórico de alterações ({delivery.events.length})</summary>
                  <ul>
                    {delivery.events.map((entry, index) => (
                      <li key={delivery.id + '-' + index}>
                        {deliveryEventLabel(entry)}
                        {' · '}{entry.actorName} · {dateTimeLabel(entry.at)}
                        {entry.reason ? ' · ' + entry.reason : ''}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </article>
          ))}
        </div>
      ) : tracking.sentToSupplierAt ? (
        <p className="empty-state">Nenhuma parcela de entrega confirmada foi registrada para este pedido.</p>
      ) : null}
      {tracking.sentToSupplierAt && canSendOrConfirm && (
        <form className="delivery-create-form" onSubmit={(event) => void createDelivery(event)}>
          <h6>Adicionar parcela com ETA confirmado</h6>
          <label htmlFor={'delivery-product-' + orderId}>Produto</label>
          <select id={'delivery-product-' + orderId} disabled={busy || !online} value={productId} onChange={(event) => setProductId(event.target.value)}>
            <option value="">Selecione um saldo aprovado</option>
            {items.filter((item) => (remainingByProduct.get(item.productId) ?? 0) > 0).map((item) => (
              <option key={item.productId} value={item.productId}>
                {item.sku} · {item.productName} · faltam confirmar {remainingByProduct.get(item.productId)} {item.purchaseUnitName}
              </option>
            ))}
          </select>
          <div className="delivery-create-fields">
            <div>
              <label htmlFor={'delivery-quantity-' + orderId}>Quantidade de compra</label>
              <input id={'delivery-quantity-' + orderId} disabled={busy || !online} inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} placeholder="Ex.: 12" />
            </div>
            <div>
              <label htmlFor={'delivery-date-' + orderId}>Data confirmada</label>
              <input id={'delivery-date-' + orderId} disabled={busy || !online} type="date" value={etaDate} onChange={(event) => setEtaDate(event.target.value)} />
            </div>
          </div>
          <button className="secondary-button" disabled={busy || !online || !productId || !quantity || !etaDate} type="submit">Registrar entrega confirmada</button>
        </form>
      )}
      {activeAlerts.length > 0 && (
        <div className="delivery-alerts">
          <h6>Alertas de risco de ruptura</h6>
          {activeAlerts.map((alert) => (
            <article className={'delivery-alert delivery-alert-' + alert.status} key={alert.id}>
              <strong>{alert.sku} · {alert.productName}: o atraso antecipou a ruptura prevista</strong>
              <p>ETA: {dateLabel(alert.oldEtaDate)} → {dateLabel(alert.newEtaDate)} · ruptura: {dateLabel(alert.previousStockoutDate)} → {dateLabel(alert.projectedStockoutDate)}</p>
              {alert.status === 'open' && canManage && (
                <div className="delivery-alert-action">
                  <label htmlFor={'delivery-alert-action-' + alert.id}>Ação tomada</label>
                  <input id={'delivery-alert-action-' + alert.id} disabled={busy || !online} value={alertActions[alert.id] ?? ''} onChange={(event) => setAlertActions((current) => ({ ...current, [alert.id]: event.target.value }))} placeholder="Ex.: contatar fornecedor alternativo" />
                  <button className="text-button" disabled={busy || !online} onClick={() => void acknowledge(alert)} type="button">Registrar ação e reconhecer</button>
                </div>
              )}
              {alert.status === 'acknowledged' && canManage && (
                <div className="delivery-alert-action">
                  <label htmlFor={'delivery-alert-reason-' + alert.id}>Justificativa de encerramento</label>
                  <input id={'delivery-alert-reason-' + alert.id} disabled={busy || !online} value={alertReasons[alert.id] ?? ''} onChange={(event) => setAlertReasons((current) => ({ ...current, [alert.id]: event.target.value }))} placeholder="Descreva a solução ou a justificativa" />
                  <div className="delivery-alert-buttons">
                    <button className="text-button" disabled={busy || !online} onClick={() => void resolve(alert, 'resolved')} type="button">Encerrar como resolvido</button>
                    <button className="text-button" disabled={busy || !online} onClick={() => void resolve(alert, 'justified')} type="button">Encerrar com justificativa</button>
                  </div>
                </div>
              )}
              {!canManage && <p className="field-hint">Um gerente ou comprador autorizado precisa registrar uma ação para este alerta.</p>}
            </article>
          ))}
        </div>
      )}
      {tracking.alerts.some((alert) => ['resolved', 'justified'].includes(alert.status)) && (
        <details className="delivery-history">
          <summary>Alertas encerrados</summary>
          <ul>
            {tracking.alerts.filter((alert) => ['resolved', 'justified'].includes(alert.status)).map((alert) => (
              <li key={alert.id}>
                {alert.sku} · {statusLabel(alert.status)} · ETA {dateLabel(alert.oldEtaDate)} → {dateLabel(alert.newEtaDate)}
                {alert.resolutionReason ? ' · ' + alert.resolutionReason : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!online && <p className="field-hint">As alterações de entrega e os alertas exigem conexão neste ciclo online.</p>}
    </section>
  );
}
