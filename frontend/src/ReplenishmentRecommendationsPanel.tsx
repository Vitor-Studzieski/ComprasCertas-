import { useEffect, useState, type FormEvent } from 'react';
import { apiGet, apiPatch, apiPost, ApiError } from './api';

type ProductUnit = 'unit' | 'kg' | 'l';
type SupplierComparison = {
  supplierId: string;
  supplierName: string;
  availability: string;
  unitCost: string | null;
  unitCostSource?: string | null;
  knownMerchandiseCost?: string | null;
  deliveryLeadTimeDays: number;
  purchaseUnitName: string;
  freightStatus: 'known' | 'unknown' | 'not_applicable' | string;
  costCompleteness: 'complete' | 'partial' | 'unknown' | string;
  minimumPurchaseQuantity: string;
  purchaseMultiple: string;
  orderQuantity: string | null;
  stockAtArrival: string | null;
  predictedShortageBeforeArrival: string;
  quantityNeededBase: string | null;
  orderQuantityBase: string | null;
  excessOverTarget: string | null;
  selected: boolean;
  partialCost: boolean;
  ruptureAvoidanceRank: number | null;
  costRank: number | null;
  reasonCodes: string[];
};
type Recommendation = {
  product: { id: string; sku: string; name: string; baseUnit: ProductUnit; categoryId: string | null; categoryName?: string | null };
  currentStock: string;
  averageDailyDemand: string | null;
  validDemandDays: number;
  lowEvidence: boolean;
  targetStock: string | null;
  suggestedQuantity: string | null;
  quantityToOrder: string | null;
  excessOverTarget: string | null;
  coverageDays: string | null;
  evidence: {
    demandWindowStart: string;
    demandWindowEnd: string;
    explicitSalesDays: number;
    excludedOutOfStockDays: number;
    completeSalesDays: number;
  };
  effectiveParameters: {
    minimumStock: string | null;
    safetyDays: number | null;
    reviewIntervalDays: number | null;
    source: string;
    minimumStockSource?: string | null;
    safetyDaysSource?: string | null;
    reviewIntervalSource?: string | null;
  };
  supplierComparisons: SupplierComparison[];
  reasonCodes: string[];
  warnings: string[];
};
type RecommendationResponse = {
  storeId: string;
  asOfDate: string;
  reviewIntervalDays: number | null;
  ruleVersion: string;
  limitations: string[];
  recommendations: Recommendation[];
};
type Props = {
  storeId: string;
  storeName: string;
  enabled: boolean;
  products: Array<{ id: string; sku: string; name: string; baseUnit: ProductUnit }>;
  assortments: Array<{ productId: string; sku: string; productName: string; baseUnit: ProductUnit }>;
  categories: ProductCategory[];
  onCategoryCreated: (category: ProductCategory) => void;
};

type ReplenishmentSettings = {
  minimumStock: string | null;
  safetyDays: number | null;
  reviewIntervalDays: number | null;
};
type SettingField = keyof ReplenishmentSettings;
type SettingScope = 'organization' | 'store' | 'category' | 'product' | 'store-product';
type ProductCategory = { id: string; name: string; createdAt: string };
type SalesCoveragePeriod = { id: string; operationalDateFrom: string; operationalDateTo: string; createdBy: string; createdAt: string };
type OutOfStockDay = { id: string; productId: string; operationalDate: string; createdBy: string; createdAt: string };

const NETWORK_SETTINGS_FIELDS: SettingField[] = ['minimumStock', 'safetyDays', 'reviewIntervalDays'];
const STORE_SETTINGS_FIELDS: SettingField[] = ['reviewIntervalDays'];
const PRODUCT_SETTINGS_FIELDS: SettingField[] = ['minimumStock', 'safetyDays'];
const DECIMAL_STOCK_PATTERN = /^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$/;

function displayDate(value: string): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`));
}

function unitLabel(unit: ProductUnit): string {
  return unit === 'unit' ? 'unidades' : unit;
}

function displayExactDecimal(value: string): string {
  return value.replace('.', ',');
}

function freightLabel(status: SupplierComparison['freightStatus']): string {
  if (status === 'known') return 'Frete informado';
  if (status === 'not_applicable') return 'Frete não aplicável';
  return 'Frete desconhecido';
}

function unitCostSourceLabel(source: string | null | undefined): string {
  if (source === 'last_confirmed_receipt') return 'último recebimento confirmado nesta loja';
  if (source === 'supplier_condition') return 'condição comercial do fornecedor';
  return 'fonte do preço não informada';
}

function supplierAvailabilityLabel(supplier: SupplierComparison): string {
  return supplier.availability === 'available'
    ? `${supplier.deliveryLeadTimeDays} dias`
    : supplier.availability.replaceAll('_', ' ');
}

function reasonLabel(code: string): string {
  const labels: Record<string, string> = {
    arrives_before_predicted_stockout: 'A entrega chega antes da ruptura prevista',
    stockout_predicted_before_arrival: 'A ruptura está prevista antes da entrega',
    selected_shortest_lead_time_when_all_at_risk: 'Menor prazo entre fornecedores quando todos chegam após a ruptura',
    selected_lowest_known_unit_cost_after_risk: 'Menor preço conhecido entre opções com o mesmo risco de ruptura',
    selected_lowest_known_merchandise_cost_after_risk: 'Menor subtotal conhecido de mercadoria entre opções com o mesmo risco de ruptura',
    stockout_before_arrival: 'Ruptura prevista antes da chegada',
    partial_cost: 'Frete ausente; custo total parcial',
    low_evidence: 'Baixa evidência de demanda',
    missing_minimum_stock: 'Estoque mínimo não configurado',
    freight_unknown: 'Frete desconhecido; custo total parcial',
  };
  return labels[code] ?? code.replaceAll('_', ' ');
}

function warningLabel(code: string): string {
  const labels: Record<string, string> = {
    low_evidence: 'Há poucos dias válidos de venda; a recomendação tem baixa evidência.',
    missing_minimum_stock: 'Configure um estoque mínimo para definir um alvo sem histórico válido.',
    review_interval_missing: 'O intervalo de revisão ainda não foi configurado.',
    safety_days_missing: 'Os dias de segurança ainda não foram configurados.',
    supplier_condition_missing: 'Cadastre uma condição de fornecedor para comparar prazo e embalagem.',
    stockout_before_arrival: 'Há risco de falta antes da chegada do fornecedor.',
    freight_unknown_cost_partial: 'O frete não foi informado; o custo total fica parcial.',
  };
  return labels[code] ?? reasonLabel(code);
}

function limitationLabel(code: string): string {
  const labels: Record<string, string> = {
    expected_deliveries_not_modeled: 'Entregas futuras confirmadas ainda não são consideradas no saldo projetado.',
    budget_not_modeled: 'Os limites semanal e mensal de orçamento ainda não são considerados.',
    freight_not_recorded: 'O frete não está registrado; não é possível afirmar o custo total completo.',
  };
  return labels[code] ?? code.replaceAll('_', ' ');
}

function parameterSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    'store_product': 'ajuste produto/loja',
    product: 'ajuste do produto',
    category: 'padrão da categoria',
    organization: 'padrão da rede',
    network: 'padrão da rede',
    mixed: 'parâmetros herdados e ajustados',
    unconfigured: 'sem parâmetros configurados',
  };
  return labels[source] ?? source;
}

function settingsFromResponse(value: unknown): Partial<ReplenishmentSettings> {
  if (!value || typeof value !== 'object') return {};
  const envelope = value as { settings?: Partial<ReplenishmentSettings> } & Partial<ReplenishmentSettings>;
  return envelope.settings ?? envelope;
}

function settingLabel(field: SettingField): string {
  if (field === 'minimumStock') return 'Estoque mínimo (unidade-base)';
  if (field === 'safetyDays') return 'Dias de segurança';
  return 'Intervalo de revisão (dias)';
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    return `Erro ${error.status}${error.code ? ` (${error.code})` : ''}: ${fields || error.message}`;
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a solicitação.';
}

function ReplenishmentSettingsEditor({
  title,
  path,
  scope,
  fields,
  enabled,
  onSaved,
}: {
  title: string;
  path: string;
  scope: SettingScope;
  fields: SettingField[];
  enabled: boolean;
  onSaved: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [loadedPath, setLoadedPath] = useState('');
  const [values, setValues] = useState<Record<SettingField, string>>({ minimumStock: '', safetyDays: '', reviewIntervalDays: '' });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (!expanded || loadedPath === path || !enabled) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    apiGet<unknown>(path)
      .then((response) => {
        if (cancelled) return;
        const data = settingsFromResponse(response);
        setValues({
          minimumStock: data.minimumStock ?? '',
          safetyDays: data.safetyDays === null || data.safetyDays === undefined ? '' : String(data.safetyDays),
          reviewIntervalDays: data.reviewIntervalDays === null || data.reviewIntervalDays === undefined ? '' : String(data.reviewIntervalDays),
        });
        setLoadedPath(path);
      })
      .catch((requestError: unknown) => { if (!cancelled) setError(describeError(requestError)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled, expanded, loadedPath, path]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: Partial<ReplenishmentSettings> = {};
    for (const field of fields) {
      const value = values[field].trim();
      if (field === 'minimumStock') {
        if (value && !DECIMAL_STOCK_PATTERN.test(value)) {
          setError('Use uma quantidade não negativa, com até três casas decimais e ponto decimal.');
          return;
        }
        body.minimumStock = value || null;
      } else if (field === 'safetyDays' || field === 'reviewIntervalDays') {
        if (value && !/^(0|[1-9][0-9]*)$/.test(value)) {
          setError('Informe um número inteiro de dias.');
          return;
        }
        body[field] = value ? Number(value) : null;
      }
    }

    setBusy(true);
    setError('');
    setNotice('');
    try {
      const response = await apiPatch<unknown>(path, body);
      const data = settingsFromResponse(response);
      setValues({
        minimumStock: data.minimumStock === null ? '' : data.minimumStock ?? values.minimumStock,
        safetyDays: data.safetyDays === null ? '' : data.safetyDays === undefined ? values.safetyDays : String(data.safetyDays),
        reviewIntervalDays: data.reviewIntervalDays === null ? '' : data.reviewIntervalDays === undefined ? values.reviewIntervalDays : String(data.reviewIntervalDays),
      });
      setLoadedPath(path);
      setNotice('Parâmetros salvos. A recomendação será atualizada com os valores vigentes.');
      onSaved();
    } catch (requestError) {
      setError(describeError(requestError));
    } finally {
      setBusy(false);
    }
  }

  const hint = scope === 'store'
    ? 'Este valor sobrescreve o intervalo de revisão padrão da rede para esta loja.'
    : scope === 'store-product'
      ? 'Estes valores têm precedência sobre os ajustes do produto, da categoria e da rede.'
      : scope === 'product'
        ? 'Estes valores sobrescrevem os padrões da categoria e da rede; um ajuste produto/loja tem precedência.'
        : scope === 'category'
          ? 'Estes valores se aplicam quando não há ajuste mais específico no produto ou na loja.'
          : 'Padrões usados quando não há valores configurados em níveis mais específicos.';

  return (
    <div className="replenishment-settings-editor">
      <button className="replenishment-settings-toggle" aria-expanded={expanded} onClick={() => setExpanded((current) => !current)} type="button">
        <span>{title}</span><span aria-hidden="true">{expanded ? '−' : '+'}</span>
      </button>
      {expanded && (
        <div className="replenishment-settings-content">
          {loading && <p className="sending-status" role="status">Carregando parâmetros…</p>}
          {error && <p className="form-error" role="alert">{error}</p>}
          {notice && <p className="form-notice" role="status">{notice}</p>}
      {!loading && loadedPath === path && (
            <form className="replenishment-settings-form" onSubmit={(event) => void save(event)} aria-busy={busy}>
              <p className="field-hint">{hint}</p>
              {fields.map((field) => (
                <div className="replenishment-setting-field" key={field}>
                  <label htmlFor={`${scope}-${path}-${field}`}>{settingLabel(field)}</label>
                  <input
                    autoComplete="off"
                    disabled={busy}
                    id={`${scope}-${path}-${field}`}
                    inputMode={field === 'minimumStock' ? 'decimal' : 'numeric'}
                    min={field === 'reviewIntervalDays' ? '1' : '0'}
                    onChange={(event) => setValues((current) => ({ ...current, [field]: event.target.value }))}
                    placeholder="Sem ajuste neste nível"
                    step={field === 'minimumStock' ? 'any' : '1'}
                    type={field === 'minimumStock' ? 'text' : 'number'}
                    value={values[field]}
                  />
                </div>
              ))}
              <p className="field-hint">Apague o valor para remover o ajuste e herdar o nível anterior.</p>
              <button className="secondary-button" disabled={busy} type="submit">{busy ? 'Salvando…' : 'Salvar parâmetros'}</button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    return `Erro ${error.status}${error.code ? ` (${error.code})` : ''}: ${fields || error.message}`;
  }
  return error instanceof Error ? error.message : 'Não foi possível carregar as recomendações.';
}

export default function ReplenishmentRecommendationsPanel({
  storeId,
  storeName,
  enabled,
  products,
  assortments,
  categories,
  onCategoryCreated,
}: Props) {
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [reloadSequence, setReloadSequence] = useState(0);
  const [coveragePeriods, setCoveragePeriods] = useState<SalesCoveragePeriod[]>([]);
  const [outOfStockDays, setOutOfStockDays] = useState<OutOfStockDay[]>([]);
  const [salesCoverageFrom, setSalesCoverageFrom] = useState('');
  const [salesCoverageTo, setSalesCoverageTo] = useState('');
  const [outOfStockProductId, setOutOfStockProductId] = useState('');
  const [outOfStockDate, setOutOfStockDate] = useState('');
  const [categoryName, setCategoryName] = useState('');
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupError, setSetupError] = useState('');
  const [setupNotice, setSetupNotice] = useState('');

  useEffect(() => {
    if (!storeId || !enabled) {
      setResult(null);
      setLoading(false);
      setError('');
      return;
    }

    let cancelled = false;
    setResult(null);
    setLoading(true);
    setError('');
    apiGet<RecommendationResponse>(`/stores/${storeId}/replenishment-recommendations/`)
      .then((data) => { if (!cancelled) setResult(data); })
      .catch((requestError: unknown) => { if (!cancelled) setError(errorMessage(requestError)); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [enabled, reloadSequence, storeId]);

  useEffect(() => {
    if (!storeId || !enabled) {
      setCoveragePeriods([]);
      setOutOfStockDays([]);
      return;
    }
    let cancelled = false;
    setCoveragePeriods([]);
    setOutOfStockDays([]);
    setSetupError('');
    setSetupNotice('');
    setOutOfStockProductId('');
    setOutOfStockDate('');
    Promise.all([
      apiGet<{ coveragePeriods: SalesCoveragePeriod[] }>(`/stores/${storeId}/sales-coverage/`),
      apiGet<{ outOfStockDays: OutOfStockDay[] }>(`/stores/${storeId}/out-of-stock-days/`),
    ])
      .then(([coverage, outOfStock]) => {
        if (cancelled) return;
        setCoveragePeriods(coverage.coveragePeriods);
        setOutOfStockDays(outOfStock.outOfStockDays);
      })
      .catch((requestError: unknown) => { if (!cancelled) setSetupError(describeError(requestError)); });
    return () => { cancelled = true; };
  }, [enabled, reloadSequence, storeId]);

  async function declareCoverage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (salesCoverageFrom > salesCoverageTo) {
      setSetupError('A data inicial deve ser anterior ou igual à data final.');
      return;
    }
    setSetupBusy(true);
    setSetupError('');
    setSetupNotice('');
    try {
      const response = await apiPost<SalesCoveragePeriod>(
        `/stores/${storeId}/sales-coverage/`,
        { operationalDateFrom: salesCoverageFrom, operationalDateTo: salesCoverageTo },
      );
      setCoveragePeriods((current) => {
        const merged = new Map(current.map((period) => [period.id, period]));
        merged.set(response.id, response);
        return [...merged.values()].sort((left, right) => right.operationalDateTo.localeCompare(left.operationalDateTo));
      });
      setSalesCoverageFrom('');
      setSalesCoverageTo('');
      setSetupNotice('Período completo registrado. Dias sem linha de venda dentro do período poderão contar como zero para produtos ativos.');
      setReloadSequence((current) => current + 1);
    } catch (requestError) {
      setSetupError(describeError(requestError));
    } finally {
      setSetupBusy(false);
    }
  }

  async function recordOutOfStockDay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSetupBusy(true);
    setSetupError('');
    setSetupNotice('');
    try {
      const response = await apiPost<OutOfStockDay>(
        `/stores/${storeId}/out-of-stock-days/`,
        { productId: outOfStockProductId, operationalDate: outOfStockDate },
      );
      setOutOfStockDays((current) => {
        const merged = new Map(current.map((day) => [day.id, day]));
        merged.set(response.id, response);
        return [...merged.values()].sort((left, right) => right.operationalDate.localeCompare(left.operationalDate));
      });
      setOutOfStockDate('');
      setSetupNotice('Dia sem estoque registrado. Ele será excluído da média de demanda.');
      setReloadSequence((current) => current + 1);
    } catch (requestError) {
      setSetupError(describeError(requestError));
    } finally {
      setSetupBusy(false);
    }
  }

  async function createCategory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSetupBusy(true);
    setSetupError('');
    setSetupNotice('');
    try {
      const response = await apiPost<ProductCategory | { category: ProductCategory }>('/product-categories/', { name: categoryName.trim() });
      const created = 'category' in response ? response.category : response;
      onCategoryCreated(created);
      setCategoryName('');
      setSetupNotice(`Categoria ${created.name} criada. Ela já pode ser vinculada a novos produtos.`);
    } catch (requestError) {
      setSetupError(describeError(requestError));
    } finally {
      setSetupBusy(false);
    }
  }

  return (
    <section className="workspace-section replenishment-panel" aria-labelledby="replenishment-heading" aria-busy={loading}>
      <div className="section-heading replenishment-heading">
        <span className="step-number" aria-hidden="true">7</span>
        <div>
          <h3 id="replenishment-heading">Recomendação de compra</h3>
          <p>Reposição explicada para {storeName}; o cálculo e as quantidades vêm do servidor.</p>
        </div>
        <button
          className="secondary-button replenishment-refresh"
          disabled={loading || !enabled || !storeId}
          onClick={() => setReloadSequence((current) => current + 1)}
          type="button"
        >
          {loading ? 'Atualizando…' : 'Atualizar recomendação'}
        </button>
      </div>

      {!enabled && <p className="empty-state">A consulta de recomendações ficará disponível quando a API habilitar esse perfil de acesso.</p>}
      {loading && <p className="sending-status" role="status">Calculando recomendações com os dados atuais da loja…</p>}
      {error && <p className="form-error" role="alert">{error}</p>}

      {enabled && !storeId && <p className="empty-state">Cadastre uma loja e configure o calendário operacional para consultar recomendações.</p>}
      {result && !loading && (
        <>
          <div className="replenishment-meta">
            <span>Loja: <strong>{storeName}</strong></span>
            <span>Data de referência: <strong>{displayDate(result.asOfDate)}</strong></span>
            <span>Revisão: <strong>{result.reviewIntervalDays ?? 'não configurada'}{result.reviewIntervalDays === null ? '' : ' dias'}</strong></span>
            <span>Regra: <strong>{result.ruleVersion}</strong></span>
          </div>

          {result.limitations.length > 0 && (
            <div className="replenishment-notices" role="status" aria-label="Limitações dos dados usados">
              <strong>Limitações dos dados</strong>
              <ul>{result.limitations.map((limitation, index) => <li key={`${index}-${limitation}`}>{limitationLabel(limitation)}</li>)}</ul>
            </div>
          )}

          {result.recommendations.length === 0 ? (
            <p className="empty-state">Não há produtos elegíveis para recomendação nesta loja. Confira o sortimento ativo e os parâmetros de reposição.</p>
          ) : (
            <div className="recommendation-list">
              {result.recommendations.map((item) => (
                  <article className="recommendation-card" key={item.product.id} aria-labelledby={`recommendation-${item.product.id}`}>
                    <div className="recommendation-card-heading">
                      <div>
                        <p className="eyebrow">SKU {item.product.sku}</p>
                        <h4 id={`recommendation-${item.product.id}`}>{item.product.name}</h4>
                      </div>
                      <span className={`evidence-badge${item.lowEvidence ? ' is-low' : ''}`}>
                        {item.lowEvidence ? 'Baixa evidência' : 'Evidência disponível'}
                      </span>
                    </div>

                    <dl className="recommendation-metrics">
                      <div><dt>Estoque atual</dt><dd>{displayExactDecimal(item.currentStock)} {unitLabel(item.product.baseUnit)}</dd></div>
                      <div><dt>Demanda média diária</dt><dd>{item.averageDailyDemand === null ? 'Não estimada' : `${displayExactDecimal(item.averageDailyDemand)} ${unitLabel(item.product.baseUnit)}`}</dd></div>
                      <div><dt>Dias válidos</dt><dd>{item.validDemandDays}</dd></div>
                      <div><dt>Cobertura atual</dt><dd>{item.coverageDays === null ? 'Sem cálculo' : `${displayExactDecimal(item.coverageDays)} dias`}</dd></div>
                      <div><dt>Estoque-alvo</dt><dd>{item.targetStock === null ? 'Não definido' : `${displayExactDecimal(item.targetStock)} ${unitLabel(item.product.baseUnit)}`}</dd></div>
                      <div><dt>Quantidade a pedir</dt><dd className="recommendation-quantity">{item.quantityToOrder === null ? 'Sem quantidade sugerida' : `${displayExactDecimal(item.quantityToOrder)} ${unitLabel(item.product.baseUnit)}`}</dd></div>
                    </dl>

                    <div className="recommendation-explanation">
                      <p><strong>Janela de demanda:</strong> {displayDate(item.evidence.demandWindowStart)} a {displayDate(item.evidence.demandWindowEnd)}.</p>
                      <p><strong>Dados usados:</strong> {item.evidence.explicitSalesDays} dias com venda explícita, {item.evidence.completeSalesDays} dias cobertos e {item.evidence.excludedOutOfStockDays} dias sem estoque excluídos.</p>
                      <p><strong>Parâmetros efetivos:</strong> mínimo {item.effectiveParameters.minimumStock === null ? 'não configurado' : `${displayExactDecimal(item.effectiveParameters.minimumStock)} ${unitLabel(item.product.baseUnit)}`}, segurança {item.effectiveParameters.safetyDays ?? 'não configurada'} dias, revisão {item.effectiveParameters.reviewIntervalDays ?? 'não configurada'} dias ({parameterSourceLabel(item.effectiveParameters.source)}).</p>
                      <p className="replenishment-parameter-sources"><strong>Origem dos parâmetros:</strong> mínimo {item.effectiveParameters.minimumStockSource ? parameterSourceLabel(item.effectiveParameters.minimumStockSource) : 'não informada'}; segurança {item.effectiveParameters.safetyDaysSource ? parameterSourceLabel(item.effectiveParameters.safetyDaysSource) : 'não informada'}; revisão {item.effectiveParameters.reviewIntervalSource ? parameterSourceLabel(item.effectiveParameters.reviewIntervalSource) : 'não informada'}.</p>
                      {item.suggestedQuantity !== null && item.quantityToOrder !== null && item.suggestedQuantity !== item.quantityToOrder && (
                        <p><strong>Necessidade antes do ajuste:</strong> {displayExactDecimal(item.suggestedQuantity)} {unitLabel(item.product.baseUnit)}; quantidade final {displayExactDecimal(item.quantityToOrder)} {unitLabel(item.product.baseUnit)} para respeitar o mínimo e o múltiplo de embalagem.</p>
                      )}
                      {item.excessOverTarget !== null && <p><strong>Excedente sobre o alvo após o ajuste à embalagem:</strong> {displayExactDecimal(item.excessOverTarget)} {unitLabel(item.product.baseUnit)}.</p>}
                      {item.reasonCodes.map((reason) => <p key={reason}>{reasonLabel(reason)}</p>)}
                      {item.warnings.map((warning) => <p className="calendar-block-message" key={warning} role="status">{warningLabel(warning)}</p>)}
                    </div>

                    <div className="supplier-comparisons">
                      <h5>Comparação de fornecedores</h5>
                      {item.supplierComparisons.length === 0 ? (
                        <p className="empty-state">Nenhuma condição de fornecedor elegível está cadastrada para este produto.</p>
                      ) : (
                        <div className="table-scroll" role="region" aria-label={`Fornecedores comparados para ${item.product.name}`} tabIndex={0}>
                          <table className="balance-table supplier-comparison-table">
                            <thead>
                              <tr><th scope="col">Fornecedor</th><th scope="col">Prazo</th><th scope="col">Saldo na chegada</th><th scope="col">Falta antes da chegada</th><th scope="col">Preço / subtotal de mercadoria</th><th scope="col">Frete / custo total</th><th scope="col">Mínimo / embalagem</th><th scope="col">Quantidade</th><th scope="col">Critério</th></tr>
                            </thead>
                            <tbody>
                              {item.supplierComparisons.map((supplier) => (
                                <tr className={supplier.selected ? 'supplier-comparison-selected' : undefined} key={supplier.supplierId}>
                                  <th scope="row">{supplier.supplierName}</th>
                                  <td>{supplierAvailabilityLabel(supplier)}</td>
                                  <td>{supplier.stockAtArrival === null ? 'Sem projeção' : `${displayExactDecimal(supplier.stockAtArrival)} ${unitLabel(item.product.baseUnit)}`}</td>
                                  <td>{displayExactDecimal(supplier.predictedShortageBeforeArrival)} {unitLabel(item.product.baseUnit)}</td>
                                  <td>
                                    {supplier.unitCost === null ? 'Preço unitário desconhecido' : `R$ ${displayExactDecimal(supplier.unitCost)} / ${supplier.purchaseUnitName}`}
                                    {supplier.unitCost !== null && <span className="supplier-reasons">Fonte: {unitCostSourceLabel(supplier.unitCostSource)}</span>}
                                    {supplier.knownMerchandiseCost !== null && supplier.knownMerchandiseCost !== undefined && <span className="supplier-reasons">Subtotal: R$ {displayExactDecimal(supplier.knownMerchandiseCost)} · frete não incluído/parcial</span>}
                                  </td>
                                  <td>{freightLabel(supplier.freightStatus)}{supplier.costCompleteness === 'partial' || supplier.partialCost ? <span className="partial-cost-label"> · total parcial</span> : supplier.costCompleteness === 'unknown' ? <span className="partial-cost-label"> · total desconhecido</span> : null}</td>
                                  <td>{displayExactDecimal(supplier.minimumPurchaseQuantity)} / {displayExactDecimal(supplier.purchaseMultiple)} {supplier.purchaseUnitName}</td>
                                  <td>
                                    {supplier.orderQuantity === null ? 'Não recomendada' : `${displayExactDecimal(supplier.orderQuantity)} ${supplier.purchaseUnitName}`}
                                    {supplier.orderQuantityBase !== null && <span className="supplier-reasons">({displayExactDecimal(supplier.orderQuantityBase)} {unitLabel(item.product.baseUnit)} em estoque)</span>}
                                    {supplier.selected && <span className="supplier-selected-label"> · sugerido</span>}
                                  </td>
                                  <td>
                                    {supplier.ruptureAvoidanceRank !== null && `Prazo ${supplier.ruptureAvoidanceRank}`}
                                    {supplier.costRank !== null && ` · custo ${supplier.costRank}`}
                                    {supplier.ruptureAvoidanceRank === null && supplier.costRank === null && 'Sem classificação'}
                                    {supplier.reasonCodes.length > 0 && <span className="supplier-reasons">{supplier.reasonCodes.map(reasonLabel).join('; ')}</span>}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </article>
              ))}
            </div>
          )}
          <p className="field-hint replenishment-scope-note">Esta etapa calcula a necessidade e compara as condições cadastradas. Orçamentos, pedidos esperados e aprovações ainda não fazem parte desta tela.</p>
        </>
      )}

      {enabled && (
        <div className="replenishment-setup-grid">
          <details className="replenishment-setup-section">
            <summary>Configurar parâmetros de reposição</summary>
            <p className="field-hint">O sistema usa o valor mais específico: produto/loja, produto, categoria e rede. O intervalo de revisão usa o ajuste da loja ou, sem ele, o padrão da rede. Os valores vazios herdam o nível anterior.</p>
            <ReplenishmentSettingsEditor
              enabled={enabled}
              fields={NETWORK_SETTINGS_FIELDS}
              onSaved={() => setReloadSequence((current) => current + 1)}
              path="/replenishment-settings/"
              scope="organization"
              title="Padrões da rede"
            />
            {storeId && (
              <ReplenishmentSettingsEditor
                enabled={enabled}
                fields={STORE_SETTINGS_FIELDS}
                onSaved={() => setReloadSequence((current) => current + 1)}
                path={`/stores/${storeId}/replenishment-settings/`}
                scope="store"
                title={`Intervalo de revisão · ${storeName}`}
              />
            )}
            <div className="replenishment-scope-list">
              <h4>Padrões por categoria</h4>
              {categories.map((category) => (
                <ReplenishmentSettingsEditor
                  enabled={enabled}
                  fields={PRODUCT_SETTINGS_FIELDS}
                  key={category.id}
                  onSaved={() => setReloadSequence((current) => current + 1)}
                  path={`/product-categories/${category.id}/replenishment-settings/`}
                  scope="category"
                  title={category.name}
                />
              ))}
              {categories.length === 0 && <p className="empty-state">Cadastre uma categoria para configurar padrões compartilhados.</p>}
              <form className="replenishment-inline-form" onSubmit={(event) => void createCategory(event)} aria-busy={setupBusy}>
                <label htmlFor="replenishmentCategoryName">Nova categoria</label>
                <div className="replenishment-inline-controls">
                  <input autoComplete="off" disabled={setupBusy} id="replenishmentCategoryName" maxLength={120} onChange={(event) => setCategoryName(event.target.value)} required value={categoryName} />
                  <button className="secondary-button" disabled={setupBusy || !categoryName.trim()} type="submit">{setupBusy ? 'Salvando…' : 'Criar categoria'}</button>
                </div>
              </form>
            </div>
            <div className="replenishment-scope-list">
              <h4>Padrões por produto</h4>
              {products.map((product) => (
                <ReplenishmentSettingsEditor
                  enabled={enabled}
                  fields={PRODUCT_SETTINGS_FIELDS}
                  key={product.id}
                  onSaved={() => setReloadSequence((current) => current + 1)}
                  path={`/products/${product.id}/replenishment-settings/`}
                  scope="product"
                  title={`${product.sku} · ${product.name}`}
                />
              ))}
              {products.length === 0 && <p className="empty-state">Cadastre produtos para configurar parâmetros específicos.</p>}
            </div>
            <div className="replenishment-scope-list">
              <h4>Ajustes específicos da loja</h4>
              {assortments.map((product) => (
                <ReplenishmentSettingsEditor
                  enabled={enabled}
                  fields={PRODUCT_SETTINGS_FIELDS}
                  key={product.productId}
                  onSaved={() => setReloadSequence((current) => current + 1)}
                  path={`/stores/${storeId}/products/${product.productId}/replenishment-settings/`}
                  scope="store-product"
                  title={`${product.sku} · ${product.productName}`}
                />
              ))}
              {assortments.length === 0 && <p className="empty-state">Vincule produtos ao sortimento desta loja para criar ajustes específicos.</p>}
            </div>
          </details>

          <details className="replenishment-setup-section">
            <summary>Completar os dados de demanda</summary>
            <p className="field-hint">Registre intervalos de vendas comprovadamente completos. Dentro deles, a ausência de uma linha significa zero para produtos ativos no sortimento. Marque os dias sem estoque separadamente para que sejam excluídos da média.</p>
            {setupError && <p className="form-error" role="alert">{setupError}</p>}
            {setupNotice && <p className="form-notice" role="status">{setupNotice}</p>}
            <div className="replenishment-data-grid">
              <div className="replenishment-scope-list">
                <h4>Períodos completos de vendas · {storeName}</h4>
                <ul className="calendar-version-list" aria-label="Períodos de vendas completos">
                  {coveragePeriods.map((period) => (
                    <li key={period.id}><strong>{displayDate(period.operationalDateFrom)} – {displayDate(period.operationalDateTo)}</strong><span>Declarado completo · {displayDate(period.createdAt.slice(0, 10))}</span></li>
                  ))}
                  {coveragePeriods.length === 0 && <li className="empty-state">Nenhum período completo declarado.</li>}
                </ul>
                <form className="replenishment-settings-form" onSubmit={(event) => void declareCoverage(event)} aria-busy={setupBusy}>
                  <label htmlFor="salesCoverageFrom">De (data operacional)</label>
                  <input disabled={setupBusy} id="salesCoverageFrom" onChange={(event) => setSalesCoverageFrom(event.target.value)} required type="date" value={salesCoverageFrom} />
                  <label htmlFor="salesCoverageTo">Até (data operacional)</label>
                  <input disabled={setupBusy} id="salesCoverageTo" onChange={(event) => setSalesCoverageTo(event.target.value)} required type="date" value={salesCoverageTo} />
                  <button className="secondary-button" disabled={setupBusy || !storeId} type="submit">Declarar período completo</button>
                </form>
              </div>
              <div className="replenishment-scope-list">
                <h4>Dias sem estoque · {storeName}</h4>
                <ul className="calendar-version-list" aria-label="Dias sem estoque registrados">
                  {outOfStockDays.map((record) => {
                    const product = products.find((candidate) => candidate.id === record.productId);
                    return <li key={record.id}><strong>{product ? `${product.sku} · ${product.name}` : 'Produto'}</strong><span>{displayDate(record.operationalDate)}</span></li>;
                  })}
                  {outOfStockDays.length === 0 && <li className="empty-state">Nenhum dia sem estoque registrado.</li>}
                </ul>
                <form className="replenishment-settings-form" onSubmit={(event) => void recordOutOfStockDay(event)} aria-busy={setupBusy}>
                  <label htmlFor="outOfStockProduct">Produto</label>
                  <select disabled={setupBusy || assortments.length === 0} id="outOfStockProduct" onChange={(event) => setOutOfStockProductId(event.target.value)} required value={outOfStockProductId}>
                    <option value="">Selecione um produto do sortimento</option>
                    {assortments.map((product) => <option key={product.productId} value={product.productId}>{product.sku} · {product.productName}</option>)}
                  </select>
                  <label htmlFor="outOfStockDate">Data operacional sem estoque</label>
                  <input disabled={setupBusy} id="outOfStockDate" onChange={(event) => setOutOfStockDate(event.target.value)} required type="date" value={outOfStockDate} />
                  <button className="secondary-button" disabled={setupBusy || !storeId || !outOfStockProductId} type="submit">Registrar dia sem estoque</button>
                </form>
              </div>
            </div>
          </details>
        </div>
      )}
    </section>
  );
}
