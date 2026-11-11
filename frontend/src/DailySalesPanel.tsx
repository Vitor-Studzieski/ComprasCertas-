import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { apiGet, apiPost, ApiError } from './api';

type BaseUnit = 'unit' | 'kg' | 'l';
type StoreAssortment = {
  productId: string;
  sku: string;
  productName: string;
  baseUnit: BaseUnit;
  effectiveFrom: string;
};
type DailySale = {
  id: string;
  productId: string;
  sku: string;
  productName: string;
  baseUnit: BaseUnit;
  operationalDate: string;
  quantity: string;
  cutoffDate: string;
  stockTreatment: 'demand_only' | 'outbound_movement';
  demandEligible: boolean;
  createdBy: string;
  createdAt: string;
  correctionCount: number;
};
type DailySaleCorrection = {
  id: string;
  previousQuantity: string;
  newQuantity: string;
  reason: string;
  correctedBy: string;
  correctedAt: string;
  compensatingMovementId: string | null;
};
type Props = {
  storeId: string;
  storeName: string;
  assortments: StoreAssortment[];
  storeDetailsLoading: boolean;
  canEdit: boolean;
  onBusyChange: (busy: boolean) => void;
};

function createUuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  if (typeof crypto === 'undefined') throw new Error('Este navegador não oferece geração segura de UUID.');
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function quantityPattern(unit: BaseUnit): RegExp {
  return unit === 'unit' ? /^(0|[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$/;
}

function quantityHint(unit: BaseUnit): string {
  return unit === 'unit' ? 'Informe um número inteiro não negativo.' : 'Informe um número não negativo, com até três casas decimais e ponto decimal.';
}

function quantityUnit(unit: BaseUnit): string {
  return unit === 'unit' ? 'unidades' : unit;
}

function dateLabel(value: string): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`));
}

function dateTimeLabel(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    switch (error.code) {
      case 'operational_calendar_required':
        return `Calendário operacional necessário (${error.status}). Configure o fuso horário e o início do dia para a data da venda. ${fields || error.message}`;
      case 'opening_stock_required':
        return `Estoque inicial necessário (${error.status}). Registre a contagem inicial da loja antes de lançar vendas. ${fields || error.message}`;
      case 'daily_sale_conflict':
        return `Já existe uma venda diária para esse produto e data (${error.status}). Atualizamos a lista; confira o registro antes de tentar novamente. ${fields || error.message}`;
      case 'idempotency_conflict':
        return `A chave desta operação já foi usada com outros dados (${error.status}). Edite o lançamento para gerar uma nova chave e tente novamente. ${fields || error.message}`;
      case 'daily_sale_quantity_conflict':
        return `O total mudou desde a última leitura (${error.status}). Atualizamos o registro e o histórico; revise a quantidade atual antes de corrigir novamente. ${fields || error.message}`;
      default:
        return `Erro ${error.status}${error.code ? ` (${error.code})` : ''}: ${fields || error.message}`;
    }
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a solicitação.';
}

export default function DailySalesPanel({ storeId, storeName, assortments, storeDetailsLoading, canEdit, onBusyChange }: Props) {
  const salesRequestSequence = useRef(0);
  const currentStoreId = useRef(storeId);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [loadedRange, setLoadedRange] = useState<{ from: string; to: string } | null>(null);
  const [sales, setSales] = useState<DailySale[]>([]);
  const [salesLoaded, setSalesLoaded] = useState(false);
  const [selectedSaleId, setSelectedSaleId] = useState('');
  const [corrections, setCorrections] = useState<DailySaleCorrection[]>([]);
  const [correctionsLoaded, setCorrectionsLoaded] = useState(false);
  const [saleOperationalDate, setSaleOperationalDate] = useState('');
  const [saleProductId, setSaleProductId] = useState('');
  const [saleQuantity, setSaleQuantity] = useState('');
  const [saleOperationId, setSaleOperationId] = useState(createUuid);
  const [correctionNewQuantity, setCorrectionNewQuantity] = useState('');
  const [correctionReason, setCorrectionReason] = useState('');
  const [correctionOperationId, setCorrectionOperationId] = useState(createUuid);
  const [loadingSales, setLoadingSales] = useState(false);
  const [loadingCorrections, setLoadingCorrections] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const selectedSale = sales.find((sale) => sale.id === selectedSaleId);
  const eligibleProducts = useMemo(() => {
    const products = new Map<string, StoreAssortment>();
    for (const assortment of assortments) {
      if (!saleOperationalDate || assortment.effectiveFrom <= saleOperationalDate) {
        products.set(assortment.productId, assortment);
      }
    }
    return [...products.values()].sort((left, right) => left.productName.localeCompare(right.productName, 'pt-BR'));
  }, [assortments, saleOperationalDate]);
  const selectedProduct = eligibleProducts.find((product) => product.productId === saleProductId);

  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);

  useEffect(() => {
    currentStoreId.current = storeId;
    salesRequestSequence.current += 1;
    setSales([]);
    setSalesLoaded(false);
    setLoadedRange(null);
    setLoadingSales(false);
    setSelectedSaleId('');
    setCorrections([]);
    setCorrectionsLoaded(false);
    setLoadingCorrections(false);
    setDateFrom('');
    setDateTo('');
    setSaleOperationalDate('');
    setSaleProductId('');
    setSaleQuantity('');
    setSaleOperationId(createUuid());
    setCorrectionNewQuantity('');
    setCorrectionReason('');
    setCorrectionOperationId(createUuid());
    setError('');
    setNotice('');
    return () => { salesRequestSequence.current += 1; };
  }, [storeId]);

  useEffect(() => {
    if (!selectedSale) {
      setCorrections([]);
      setCorrectionsLoaded(false);
      return;
    }

    let cancelled = false;
    setLoadingCorrections(true);
    setCorrectionsLoaded(false);
    setCorrectionNewQuantity(selectedSale.quantity);
    setCorrectionReason('');
    setCorrectionOperationId(createUuid());
    apiGet<{ dailySaleId: string; corrections: DailySaleCorrection[] }>(
      `/stores/${storeId}/daily-sales/${selectedSale.id}/corrections/`,
    )
      .then((result) => {
        if (cancelled) return;
        setCorrections(result.corrections);
        setCorrectionsLoaded(true);
      })
      .catch((requestError: unknown) => {
        if (!cancelled) setError(errorMessage(requestError));
      })
      .finally(() => {
        if (!cancelled) setLoadingCorrections(false);
      });

    return () => { cancelled = true; };
  }, [selectedSale?.id, selectedSale?.quantity, storeId]);

  async function loadSales(from: string, to: string, preserveError = false) {
    if (!storeId || !from || !to) return;
    if (from > to) {
      setError('A data inicial deve ser anterior ou igual à data final.');
      return;
    }

    const query = new URLSearchParams({ operationalDateFrom: from, operationalDateTo: to });
    const requestSequence = ++salesRequestSequence.current;
    const requestStoreId = storeId;
    const isCurrentRequest = () => requestSequence === salesRequestSequence.current && requestStoreId === currentStoreId.current;
    setLoadingSales(true);
    setSales([]);
    setSalesLoaded(false);
    setLoadedRange(null);
    if (!preserveError) setError('');
    try {
      const result = await apiGet<{ operationalDateFrom: string; operationalDateTo: string; sales: DailySale[] }>(
        `/stores/${storeId}/daily-sales/?${query.toString()}`,
      );
      if (!isCurrentRequest()) return;
      setSales(result.sales);
      setSalesLoaded(true);
      setLoadedRange({ from: result.operationalDateFrom, to: result.operationalDateTo });
      setSelectedSaleId((current) => result.sales.some((sale) => sale.id === current) ? current : '');
    } catch (requestError) {
      if (isCurrentRequest()) setError(errorMessage(requestError));
    } finally {
      if (isCurrentRequest()) setLoadingSales(false);
    }
  }

  async function refreshSalesAfterWrite(operationalDate: string, preserveError = false) {
    const keepCurrentRange = Boolean(dateFrom && dateTo && dateFrom <= operationalDate && operationalDate <= dateTo);
    const from = keepCurrentRange ? dateFrom : operationalDate;
    const to = keepCurrentRange ? dateTo : operationalDate;
    if (!keepCurrentRange) {
      setDateFrom(from);
      setDateTo(to);
    }
    await loadSales(from, to, preserveError);
  }

  async function submitRange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice('');
    await loadSales(dateFrom, dateTo);
  }

  async function submitSale(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProduct) {
      setError('Selecione um produto que já pertença ao sortimento da loja na data informada.');
      return;
    }
    if (!quantityPattern(selectedProduct.baseUnit).test(saleQuantity)) {
      setError(quantityHint(selectedProduct.baseUnit));
      return;
    }

    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiPost<DailySale>(`/stores/${storeId}/daily-sales/`, {
        productId: selectedProduct.productId,
        operationalDate: saleOperationalDate,
        quantity: saleQuantity,
        operationId: saleOperationId,
      });
      setSaleQuantity('');
      setSaleOperationId(createUuid());
      setNotice('Total diário de vendas registrado. Os efeitos de estoque e demanda foram calculados pelo servidor.');
      await refreshSalesAfterWrite(saleOperationalDate);
    } catch (requestError) {
      const message = errorMessage(requestError);
      setError(message);
      if (requestError instanceof ApiError && ['daily_sale_conflict', 'idempotency_conflict'].includes(requestError.code ?? '')) {
        await refreshSalesAfterWrite(saleOperationalDate, true);
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function submitCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSale) return;
    if (!quantityPattern(selectedSale.baseUnit).test(correctionNewQuantity)) {
      setError(quantityHint(selectedSale.baseUnit));
      return;
    }
    if (!correctionReason.trim()) {
      setError('Informe o motivo da correção para manter a auditoria.');
      return;
    }

    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiPost<DailySaleCorrection>(
        `/stores/${storeId}/daily-sales/${selectedSale.id}/corrections/`,
        {
          expectedCurrentQuantity: selectedSale.quantity,
          newQuantity: correctionNewQuantity,
          reason: correctionReason.trim(),
          operationId: correctionOperationId,
        },
      );
      setNotice('Correção registrada e auditada. O servidor atualizou o total e qualquer movimento compensatório necessário.');
      await refreshSalesAfterWrite(selectedSale.operationalDate);
      const history = await apiGet<{ dailySaleId: string; corrections: DailySaleCorrection[] }>(
        `/stores/${storeId}/daily-sales/${selectedSale.id}/corrections/`,
      );
      setCorrections(history.corrections);
      setCorrectionsLoaded(true);
      setCorrectionOperationId(createUuid());
    } catch (requestError) {
      const message = errorMessage(requestError);
      setError(message);
      if (requestError instanceof ApiError && requestError.code === 'daily_sale_quantity_conflict') {
        await refreshSalesAfterWrite(selectedSale.operationalDate, true);
        try {
          const history = await apiGet<{ dailySaleId: string; corrections: DailySaleCorrection[] }>(
            `/stores/${storeId}/daily-sales/${selectedSale.id}/corrections/`,
          );
          setCorrections(history.corrections);
          setCorrectionsLoaded(true);
        } catch (historyError) {
          setError(errorMessage(historyError));
        }
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  function updateSaleDate(value: string) {
    setSaleOperationalDate(value);
    setSaleProductId('');
    setSaleOperationId(createUuid());
  }

  function updateSaleProduct(value: string) {
    setSaleProductId(value);
    setSaleOperationId(createUuid());
  }

  function updateSaleQuantity(value: string) {
    setSaleQuantity(value);
    setSaleOperationId(createUuid());
  }

  function updateCorrectionQuantity(value: string) {
    setCorrectionNewQuantity(value);
    setCorrectionOperationId(createUuid());
  }

  function updateCorrectionReason(value: string) {
    setCorrectionReason(value);
    setCorrectionOperationId(createUuid());
  }

  return (
    <section className="workspace-section daily-sales-panel" aria-labelledby="daily-sales-heading">
      <div className="section-heading">
        <span className="step-number" aria-hidden="true">7</span>
        <div>
          <h3 id="daily-sales-heading">Vendas diárias</h3>
          <p>Registre o total por produto e consulte ou corrija os dados da loja {storeName}.</p>
        </div>
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-notice" role="status">{notice}</p>}
      <p className="daily-sales-note">As datas são dias operacionais locais da loja; o intervalo consultado é inclusivo. O servidor valida calendário e contagem inicial e decide se a venda movimenta estoque ou entra apenas no histórico de demanda.</p>

      <form className="workspace-form daily-sales-range" onSubmit={submitRange} aria-busy={loadingSales}>
        <label htmlFor="dailySalesFrom">Data inicial</label>
        <input disabled={busy || loadingSales || storeDetailsLoading || !storeId} id="dailySalesFrom" onChange={(event) => setDateFrom(event.target.value)} required type="date" value={dateFrom} />
        <label htmlFor="dailySalesTo">Data final</label>
        <input disabled={busy || loadingSales || storeDetailsLoading || !storeId} id="dailySalesTo" onChange={(event) => setDateTo(event.target.value)} required type="date" value={dateTo} />
        <button className="secondary-button" disabled={busy || loadingSales || storeDetailsLoading || !storeId} type="submit">{loadingSales ? 'Consultando…' : 'Consultar período'}</button>
      </form>

      {storeDetailsLoading && <p className="sending-status" role="status">Carregando sortimento da loja…</p>}
      {!storeDetailsLoading && !storeId && <p className="empty-state">Selecione uma loja para consultar ou registrar vendas.</p>}
      {loadedRange && <p className="field-hint daily-sales-loaded-range" role="status">Período exibido: {dateLabel(loadedRange.from)} a {dateLabel(loadedRange.to)}.</p>}

      <div className="table-scroll daily-sales-table-scroll" role="region" aria-label="Totais de vendas diárias" tabIndex={0}>
        <table className="balance-table daily-sales-table">
          <thead><tr><th scope="col">Data operacional</th><th scope="col">SKU</th><th scope="col">Produto</th><th scope="col">Quantidade</th><th scope="col">Tratamento</th><th scope="col">Auditoria</th><th scope="col">Ação</th></tr></thead>
          <tbody>
            {sales.map((sale) => (
              <tr className={selectedSaleId === sale.id ? 'selected-sale-row' : ''} key={sale.id}>
                <td>{dateLabel(sale.operationalDate)}</td>
                <td>{sale.sku}</td>
                <td>{sale.productName}</td>
                <td>{sale.quantity} {quantityUnit(sale.baseUnit)}</td>
                <td>{sale.stockTreatment === 'demand_only' ? 'Histórico de demanda' : 'Movimento de saída'} · {sale.demandEligible ? 'elegível para demanda' : 'fora da demanda'} · corte {dateLabel(sale.cutoffDate)}</td>
                <td>{dateTimeLabel(sale.createdAt)} · usuário {sale.createdBy} · {sale.correctionCount} correção(ões)</td>
                <td><button className="text-button" aria-pressed={selectedSaleId === sale.id} onClick={() => setSelectedSaleId(sale.id)} type="button">{canEdit ? 'Ver/corrigir' : 'Ver histórico'}</button></td>
              </tr>
            ))}
            {sales.length === 0 && salesLoaded && <tr><td colSpan={7}>Nenhuma venda registrada neste período.</td></tr>}
            {sales.length === 0 && !salesLoaded && <tr><td colSpan={7}>Escolha um período e consulte as vendas.</td></tr>}
            {loadingSales && <tr><td colSpan={7}>Carregando vendas…</td></tr>}
          </tbody>
        </table>
      </div>

      {canEdit ? <form className="workspace-form daily-sale-form" onSubmit={submitSale} aria-busy={busy}>
        <h4>Registrar total diário</h4>
        <label htmlFor="saleOperationalDate">Data operacional da venda</label>
        <input disabled={busy || storeDetailsLoading || !storeId} id="saleOperationalDate" onChange={(event) => updateSaleDate(event.target.value)} required type="date" value={saleOperationalDate} />
        <label htmlFor="saleProduct">Produto do sortimento da loja</label>
        <select disabled={busy || storeDetailsLoading || eligibleProducts.length === 0} id="saleProduct" onChange={(event) => updateSaleProduct(event.target.value)} required value={saleProductId}>
          <option value="">Selecione um produto</option>
          {eligibleProducts.map((product) => <option key={product.productId} value={product.productId}>{product.sku} · {product.productName}</option>)}
        </select>
        <label htmlFor="saleQuantity">Quantidade vendida</label>
        <input disabled={busy || !selectedProduct} id="saleQuantity" inputMode={selectedProduct?.baseUnit === 'unit' ? 'numeric' : 'decimal'} min="0" onChange={(event) => updateSaleQuantity(event.target.value)} required step={selectedProduct?.baseUnit === 'unit' ? '1' : '0.001'} type="number" value={saleQuantity} />
        {selectedProduct && <p className="field-hint">Unidade: {quantityUnit(selectedProduct.baseUnit)}. {quantityHint(selectedProduct.baseUnit)}</p>}
        {!storeDetailsLoading && saleOperationalDate && eligibleProducts.length === 0 && <p className="field-hint">Nenhum produto vinculado à loja desde essa data está disponível para lançamento.</p>}
        <button className="submit-button" disabled={busy || storeDetailsLoading || !storeId || !selectedProduct} type="submit">{busy ? 'Salvando…' : 'Registrar venda diária'}</button>
      </form> : <p className="field-hint">Seu papel permite consultar as vendas desta loja, mas não registrar ou corrigir totais.</p>}

      {selectedSale && (
        <section className="daily-sale-detail" aria-labelledby="correction-heading">
          <div className="daily-sale-detail-heading">
            <div><h4 id="correction-heading">Correções: {selectedSale.productName} · {dateLabel(selectedSale.operationalDate)}</h4><p>Total atual: {selectedSale.quantity} {quantityUnit(selectedSale.baseUnit)}</p></div>
            {loadingCorrections && <span role="status">Carregando histórico…</span>}
          </div>
          <div className="table-scroll daily-correction-table-scroll" role="region" aria-label="Histórico de correções da venda" tabIndex={0}>
            <table className="balance-table">
              <thead><tr><th scope="col">Corrigido em</th><th scope="col">Anterior</th><th scope="col">Novo total</th><th scope="col">Motivo</th><th scope="col">Responsável</th><th scope="col">Movimento compensatório</th></tr></thead>
              <tbody>
                {corrections.map((correction) => (
                  <tr key={correction.id}>
                    <td>{dateTimeLabel(correction.correctedAt)}</td>
                    <td>{correction.previousQuantity}</td>
                    <td>{correction.newQuantity}</td>
                    <td>{correction.reason}</td>
                    <td>{correction.correctedBy}</td>
                    <td>{correction.compensatingMovementId ?? 'Não necessário'}</td>
                  </tr>
                ))}
                {correctionsLoaded && corrections.length === 0 && <tr><td colSpan={6}>Esta venda ainda não teve correções.</td></tr>}
              </tbody>
            </table>
          </div>
          {canEdit && <form className="workspace-form daily-correction-form" onSubmit={submitCorrection} aria-busy={busy}>
            <h4>Corrigir total diário</h4>
            <label htmlFor="correctionNewQuantity">Novo total</label>
            <input disabled={busy || loadingCorrections} id="correctionNewQuantity" inputMode={selectedSale.baseUnit === 'unit' ? 'numeric' : 'decimal'} min="0" onChange={(event) => updateCorrectionQuantity(event.target.value)} required step={selectedSale.baseUnit === 'unit' ? '1' : '0.001'} type="number" value={correctionNewQuantity} />
            <p className="field-hint">A quantidade atual usada para conferir conflito é {selectedSale.quantity} {quantityUnit(selectedSale.baseUnit)}.</p>
            <label htmlFor="correctionReason">Motivo da correção</label>
            <textarea disabled={busy || loadingCorrections} id="correctionReason" onChange={(event) => updateCorrectionReason(event.target.value)} required rows={3} value={correctionReason} />
            <button className="submit-button" disabled={busy || loadingCorrections || !correctionReason.trim()} type="submit">{busy ? 'Salvando correção…' : 'Salvar correção auditada'}</button>
          </form>}
        </section>
      )}
    </section>
  );
}
