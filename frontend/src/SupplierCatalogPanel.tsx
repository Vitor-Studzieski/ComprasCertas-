import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { apiGet, apiPatch, apiPost, ApiError } from './api';

type Supplier = {
  id: string;
  name: string;
  contactName: string;
  email: string;
  phone: string;
  notes: string;
  isActive: boolean;
  createdBy: string;
  createdAt: string;
  updatedBy: string;
  updatedAt: string;
};
type Product = {
  id: string;
  sku: string;
  name: string;
  baseUnit: 'unit' | 'kg' | 'l';
  isActive: boolean;
};
type Store = { id: string; name: string; isActive: boolean };
type SupplierCondition = {
  id: string;
  supplierId: string;
  productId: string;
  storeId: string | null;
  purchaseUnitName: string;
  baseUnitsPerPurchaseUnit: string;
  pricePerPurchaseUnit: string;
  deliveryLeadTimeDays: number;
  minimumPurchaseQuantity: string;
  purchaseMultiple: string;
  isAvailable: boolean;
  effectiveFrom: string;
  createdBy: string;
  createdAt: string;
};
type SupplierForm = {
  name: string;
  contactName: string;
  email: string;
  phone: string;
  notes: string;
};
type ConditionForm = {
  productId: string;
  storeId: string;
  purchaseUnitName: string;
  baseUnitsPerPurchaseUnit: string;
  pricePerPurchaseUnit: string;
  deliveryLeadTimeDays: string;
  minimumPurchaseQuantity: string;
  purchaseMultiple: string;
  isAvailable: boolean;
  effectiveFrom: string;
};
type Props = {
  canManageCatalog: boolean;
  isAdministrator: boolean;
};

const emptySupplierForm: SupplierForm = {
  name: '', contactName: '', email: '', phone: '', notes: '',
};

function todayLocal(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, '0');
  const day = String(today.getDate()).padStart(2, '0');
  return `${today.getFullYear()}-${month}-${day}`;
}

function emptyConditionForm(): ConditionForm {
  return {
    productId: '', storeId: '', purchaseUnitName: '', baseUnitsPerPurchaseUnit: '1',
    pricePerPurchaseUnit: '', deliveryLeadTimeDays: '0', minimumPurchaseQuantity: '0',
    purchaseMultiple: '1', isAvailable: true, effectiveFrom: todayLocal(),
  };
}

function explainError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? error.message : 'Não foi possível concluir a solicitação.';
  }

  if (error.code === 'catalog_access_required') return 'Seu perfil não tem acesso ao catálogo de fornecedores desta rede.';
  if (error.code === 'catalog_store_access_required') return 'Seu perfil não pode criar condições específicas para lojas; remova o escopo da loja e tente novamente.';
  if (error.code === 'mfa_required') return 'Confirme sua identidade com a passkey e tente novamente.';
  if (error.code === 'supplier_name_conflict') return 'Já existe um fornecedor com esse nome nesta rede.';
  if (error.code === 'supplier_condition_effective_date_conflict') return 'A nova condição precisa ter vigência posterior à última versão do mesmo escopo.';
  if (error.code === 'supplier_condition_conflict') return 'Já existe uma condição para este fornecedor, produto, escopo e data.';

  const fieldErrors = error.fields ? Object.values(error.fields).flat().join(' ') : '';
  return `${error.status === 409 ? 'Conflito com os dados atuais.' : `Erro ${error.status}.`} ${fieldErrors || error.message}`;
}

function normalizeDecimal(value: string): string {
  return value.trim().replace(',', '.');
}

function isDecimal(value: string, places: number, allowZero: boolean): boolean {
  const normalized = normalizeDecimal(value);
  const fractionalPart = places > 0 ? `(?:\\.[0-9]{1,${places}})?` : '';
  const pattern = new RegExp(`^(0|[1-9][0-9]*)${fractionalPart}$`);
  if (!pattern.test(normalized)) return false;
  return allowZero || Number(normalized) > 0;
}

function unitName(unit: Product['baseUnit']): string {
  if (unit === 'unit') return 'unidade(s)';
  return unit;
}

function displayDate(date: string): string {
  if (!date) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
}

function displayTimestamp(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function formatBrlExact(value: string): string {
  const [whole, fraction = '00'] = value.split('.');
  const groupedWhole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `R$ ${groupedWhole},${fraction.padEnd(2, '0')}`;
}

export default function SupplierCatalogPanel({ canManageCatalog, isAdministrator }: Props) {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [stores, setStores] = useState<Store[]>([]);
  const [conditions, setConditions] = useState<SupplierCondition[]>([]);
  const [selectedSupplierId, setSelectedSupplierId] = useState('');
  const [supplierForm, setSupplierForm] = useState<SupplierForm>(emptySupplierForm);
  const [editingSupplierId, setEditingSupplierId] = useState('');
  const [supplierFormOpen, setSupplierFormOpen] = useState(false);
  const [conditionForm, setConditionForm] = useState<ConditionForm>(emptyConditionForm());
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [conditionsLoading, setConditionsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const selectedSupplier = suppliers.find((supplier) => supplier.id === selectedSupplierId);
  const selectedProduct = products.find((product) => product.id === conditionForm.productId);
  const activeProducts = useMemo(() => products.filter((product) => product.isActive), [products]);
  const activeStores = useMemo(() => stores.filter((store) => store.isActive), [stores]);
  const sortedConditions = useMemo(() => [...conditions].sort((left, right) => {
    const dateOrder = right.effectiveFrom.localeCompare(left.effectiveFrom);
    if (dateOrder !== 0) return dateOrder;
    return left.productId.localeCompare(right.productId);
  }), [conditions]);

  useEffect(() => {
    if (!canManageCatalog) {
      setCatalogLoading(false);
      return;
    }
    let cancelled = false;
    setCatalogLoading(true);
    const requests: Promise<unknown>[] = [
      apiGet<{ suppliers: Supplier[] }>('/suppliers/'),
      apiGet<{ products: Product[] }>('/products/'),
    ];
    if (isAdministrator) requests.push(apiGet<{ stores: Store[] }>('/stores/'));

    Promise.allSettled(requests).then((results) => {
      if (cancelled) return;
      const failures: string[] = [];
      const supplierResult = results[0];
      if (supplierResult.status === 'fulfilled') {
        const result = supplierResult.value as { suppliers: Supplier[] };
        setSuppliers(result.suppliers);
        setSelectedSupplierId((current) => result.suppliers.some((supplier) => supplier.id === current)
          ? current
          : result.suppliers[0]?.id ?? '');
      } else {
        failures.push(`Fornecedores: ${explainError(supplierResult.reason)}`);
      }

      const productResult = results[1];
      if (productResult.status === 'fulfilled') {
        setProducts((productResult.value as { products: Product[] }).products);
      } else {
        failures.push(`Produtos: ${explainError(productResult.reason)}`);
      }
      if (isAdministrator) {
        const storeResult = results[2];
        if (storeResult.status === 'fulfilled') {
          setStores((storeResult.value as { stores: Store[] }).stores);
        } else {
          failures.push(`Lojas: ${explainError(storeResult.reason)}`);
        }
      } else {
        setStores([]);
      }
      setError(failures.join(' '));
      setCatalogLoading(false);
    });

    return () => { cancelled = true; };
  }, [canManageCatalog, isAdministrator]);

  useEffect(() => {
    if (!canManageCatalog || !selectedSupplierId) {
      setConditions([]);
      setConditionsLoading(false);
      return;
    }

    let cancelled = false;
    setConditions([]);
    setConditionsLoading(true);
    setError('');
    apiGet<{ supplierId: string; conditions: SupplierCondition[] }>(
      `/suppliers/${selectedSupplierId}/product-conditions/`,
    )
      .then((result) => { if (!cancelled) setConditions(result.conditions); })
      .catch((requestError: unknown) => { if (!cancelled) setError(explainError(requestError)); })
      .finally(() => { if (!cancelled) setConditionsLoading(false); });

    return () => { cancelled = true; };
  }, [canManageCatalog, isAdministrator, selectedSupplierId]);

  useEffect(() => {
    if (!isAdministrator) {
      setConditionForm((current) => current.storeId ? { ...current, storeId: '' } : current);
    }
  }, [isAdministrator]);

  function startNewSupplier() {
    setEditingSupplierId('');
    setSupplierForm(emptySupplierForm);
    setSupplierFormOpen(true);
    setError('');
    setNotice('');
  }

  function editSupplier(supplier: Supplier) {
    setEditingSupplierId(supplier.id);
    setSupplierForm({
      name: supplier.name,
      contactName: supplier.contactName,
      email: supplier.email,
      phone: supplier.phone,
      notes: supplier.notes,
    });
    setSupplierFormOpen(true);
    setError('');
    setNotice('');
  }

  async function submitSupplier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    const payload = {
      name: supplierForm.name.trim(),
      contactName: supplierForm.contactName.trim(),
      email: supplierForm.email.trim(),
      phone: supplierForm.phone.trim(),
      notes: supplierForm.notes.trim(),
    };
    try {
      const saved = editingSupplierId
        ? await apiPatch<Supplier>(`/suppliers/${editingSupplierId}/`, payload)
        : await apiPost<Supplier>('/suppliers/', payload);
      setSuppliers((current) => current.some((supplier) => supplier.id === saved.id)
        ? current.map((supplier) => supplier.id === saved.id ? saved : supplier)
        : [...current, saved].sort((left, right) => left.name.localeCompare(right.name, 'pt-BR')));
      setSelectedSupplierId(saved.id);
      setSupplierFormOpen(false);
      setEditingSupplierId('');
      setSupplierForm(emptySupplierForm);
      setNotice(editingSupplierId ? 'Dados do fornecedor atualizados.' : 'Fornecedor cadastrado na rede.');
    } catch (requestError) {
      setError(explainError(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSupplierActive(supplier: Supplier) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const saved = await apiPatch<Supplier>(`/suppliers/${supplier.id}/`, { isActive: !supplier.isActive });
      setSuppliers((current) => current.map((item) => item.id === saved.id ? saved : item));
      setNotice(saved.isActive ? 'Fornecedor reativado.' : 'Fornecedor desativado. O histórico foi preservado.');
    } catch (requestError) {
      setError(explainError(requestError));
    } finally {
      setBusy(false);
    }
  }

  function latestConditionDate(productId: string, storeId: string): string | null {
    const matching = conditions.filter((condition) =>
      condition.productId === productId && (condition.storeId ?? '') === storeId,
    );
    return matching.reduce<string | null>((latest, condition) =>
      !latest || condition.effectiveFrom > latest ? condition.effectiveFrom : latest,
    null);
  }

  async function submitCondition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSupplier || !selectedProduct) return;
    setBusy(true);
    setError('');
    setNotice('');

    const decimalFields = [
      ['Conversão para unidade-base', conditionForm.baseUnitsPerPurchaseUnit, 3, false, true],
      ['Preço por unidade de compra', conditionForm.pricePerPurchaseUnit, 2, true, false],
      ['Pedido mínimo', conditionForm.minimumPurchaseQuantity, 3, true, true],
      ['Múltiplo de compra', conditionForm.purchaseMultiple, 3, false, true],
    ] as const;
    const requiresIntegers = selectedProduct.baseUnit === 'unit';
    const invalidField = decimalFields.find(([, value, places, allowZero, wholeForUnit]) =>
      !isDecimal(value, requiresIntegers && wholeForUnit ? 0 : places, allowZero),
    );
    if (invalidField) {
      const [, , places, allowZero, wholeForUnit] = invalidField;
      const precision = requiresIntegers && wholeForUnit ? 'um número inteiro' : `até ${places} casas decimais`;
      const zeroRule = allowZero ? 'zero é permitido' : 'o valor deve ser maior que zero';
      setError(`Confira “${invalidField[0]}”: informe ${precision}; ${zeroRule}.`);
      setBusy(false);
      return;
    }
    if (!conditionForm.purchaseUnitName.trim()) {
      setError('Informe o nome da unidade de compra.');
      setBusy(false);
      return;
    }
    if (!/^\d+$/.test(conditionForm.deliveryLeadTimeDays) || Number(conditionForm.deliveryLeadTimeDays) > 32767) {
      setError('Informe o prazo em dias corridos, como número inteiro entre 0 e 32767.');
      setBusy(false);
      return;
    }
    const latestDate = latestConditionDate(conditionForm.productId, isAdministrator ? conditionForm.storeId : '');
    if (latestDate && conditionForm.effectiveFrom <= latestDate) {
      setError(`A vigência precisa ser posterior à última versão deste escopo (${displayDate(latestDate)}).`);
      setBusy(false);
      return;
    }

    const payload = {
      productId: conditionForm.productId,
      ...(isAdministrator && conditionForm.storeId ? { storeId: conditionForm.storeId } : {}),
      purchaseUnitName: conditionForm.purchaseUnitName.trim(),
      baseUnitsPerPurchaseUnit: normalizeDecimal(conditionForm.baseUnitsPerPurchaseUnit),
      pricePerPurchaseUnit: normalizeDecimal(conditionForm.pricePerPurchaseUnit),
      deliveryLeadTimeDays: Number(conditionForm.deliveryLeadTimeDays),
      minimumPurchaseQuantity: normalizeDecimal(conditionForm.minimumPurchaseQuantity),
      purchaseMultiple: normalizeDecimal(conditionForm.purchaseMultiple),
      isAvailable: conditionForm.isAvailable,
      effectiveFrom: conditionForm.effectiveFrom,
    };
    try {
      const saved = await apiPost<SupplierCondition>(
        `/suppliers/${selectedSupplier.id}/product-conditions/`,
        payload,
      );
      setConditions((current) => [...current, saved]);
      setConditionForm((current) => ({
        ...emptyConditionForm(),
        storeId: current.storeId,
        productId: current.productId,
      }));
      setNotice('Nova versão da condição salva; as versões anteriores continuam no histórico.');
    } catch (requestError) {
      setError(explainError(requestError));
    } finally {
      setBusy(false);
    }
  }

  const conditionProducts = new Map(products.map((product) => [product.id, product]));
  const conditionStores = new Map(stores.map((store) => [store.id, store]));

  if (!canManageCatalog) return null;

  return (
    <section className="workspace-section supplier-catalog-panel" aria-labelledby="supplier-catalog-heading">
      <div className="section-heading">
        <span className="step-number" aria-hidden="true">7</span>
        <div>
          <h3 id="supplier-catalog-heading">Fornecedores e condições de compra</h3>
          <p>O cadastro de fornecedores é compartilhado pela rede. Registre contatos e acompanhe as condições comerciais versionadas.</p>
        </div>
      </div>

      {notice && <p className="form-notice" role="status">{notice}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {catalogLoading && <p className="sending-status" role="status">Carregando fornecedores e produtos{isAdministrator ? ', lojas' : ''}…</p>}

      {!catalogLoading && (
        <>
          <div className="supplier-toolbar">
            <p className="field-hint">{suppliers.length} fornecedor(es) na rede</p>
            <button className="secondary-button" disabled={busy} onClick={startNewSupplier} type="button">Cadastrar fornecedor</button>
          </div>

          {supplierFormOpen && (
            <form className="workspace-form supplier-form" onSubmit={submitSupplier} aria-busy={busy}>
              <h4>{editingSupplierId ? 'Editar fornecedor' : 'Novo fornecedor da rede'}</h4>
              <label htmlFor="supplierName">Nome do fornecedor</label>
              <input autoComplete="organization" disabled={busy} id="supplierName" maxLength={160} onChange={(event) => setSupplierForm({ ...supplierForm, name: event.target.value })} required value={supplierForm.name} />
              <label htmlFor="supplierContactName">Pessoa de contato (opcional)</label>
              <input autoComplete="name" disabled={busy} id="supplierContactName" maxLength={160} onChange={(event) => setSupplierForm({ ...supplierForm, contactName: event.target.value })} value={supplierForm.contactName} />
              <label htmlFor="supplierEmail">E-mail (opcional)</label>
              <input autoComplete="email" disabled={busy} id="supplierEmail" maxLength={254} onChange={(event) => setSupplierForm({ ...supplierForm, email: event.target.value })} type="email" value={supplierForm.email} />
              <label htmlFor="supplierPhone">Telefone (opcional)</label>
              <input autoComplete="tel" disabled={busy} id="supplierPhone" maxLength={32} onChange={(event) => setSupplierForm({ ...supplierForm, phone: event.target.value })} type="tel" value={supplierForm.phone} />
              <label htmlFor="supplierNotes">Observações (opcional)</label>
              <textarea disabled={busy} id="supplierNotes" maxLength={1000} onChange={(event) => setSupplierForm({ ...supplierForm, notes: event.target.value })} value={supplierForm.notes} />
              <div className="supplier-form-actions">
                <button className="submit-button" disabled={busy || !supplierForm.name.trim()} type="submit">{busy ? 'Salvando…' : editingSupplierId ? 'Salvar alterações' : 'Cadastrar fornecedor'}</button>
                <button className="text-button" disabled={busy} onClick={() => setSupplierFormOpen(false)} type="button">Cancelar</button>
              </div>
            </form>
          )}

          <div aria-label="Fornecedores cadastrados" className="supplier-list" role="list">
            {suppliers.map((supplier) => (
              <article aria-label={`Fornecedor ${supplier.name}`} className={`supplier-card${supplier.id === selectedSupplierId ? ' supplier-card-selected' : ''}`} key={supplier.id} role="listitem">
                <div className="supplier-card-main">
                  <div>
                    <h4>{supplier.name}</h4>
                    <p>{supplier.contactName || 'Sem contato definido'}{supplier.email ? ` · ${supplier.email}` : ''}{supplier.phone ? ` · ${supplier.phone}` : ''}</p>
                    {supplier.notes && <p className="supplier-notes">{supplier.notes}</p>}
                    <p className="supplier-record-meta">Cadastrado em {displayTimestamp(supplier.createdAt)} por <span title={supplier.createdBy}>{supplier.createdBy.slice(0, 8)}</span> · atualizado em {displayTimestamp(supplier.updatedAt)} por <span title={supplier.updatedBy}>{supplier.updatedBy.slice(0, 8)}</span></p>
                  </div>
                  <span className={supplier.isActive ? 'supplier-status supplier-status-active' : 'supplier-status'}>{supplier.isActive ? 'Ativo' : 'Inativo'}</span>
                </div>
                <div className="supplier-card-actions">
                  <button aria-label={`Selecionar ${supplier.name}`} aria-pressed={supplier.id === selectedSupplierId} className="text-button" disabled={busy} onClick={() => { setSelectedSupplierId(supplier.id); setError(''); setNotice(''); }} type="button">
                    {supplier.id === selectedSupplierId ? 'Selecionado' : 'Selecionar'}
                  </button>
                  <button aria-label={`Editar ${supplier.name}`} className="text-button" disabled={busy} onClick={() => editSupplier(supplier)} type="button">Editar</button>
                  <button aria-label={`${supplier.isActive ? 'Desativar' : 'Reativar'} ${supplier.name}`} className="text-button" disabled={busy} onClick={() => toggleSupplierActive(supplier)} type="button">{supplier.isActive ? 'Desativar' : 'Reativar'}</button>
                </div>
              </article>
            ))}
            {suppliers.length === 0 && <p className="calendar-block-message">Nenhum fornecedor cadastrado nesta rede.</p>}
          </div>

          <div className="supplier-conditions" aria-labelledby="supplier-conditions-heading">
              <div className="supplier-conditions-heading">
                <div>
                  <h4 id="supplier-conditions-heading">Condições de compra</h4>
                  <p>O preço é por unidade de compra; a conversão informa quantas unidades-base entram no estoque.</p>
                </div>
                {selectedSupplier && <span>{selectedSupplier.name}</span>}
              </div>

              {!selectedSupplier && <p className="calendar-block-message">Cadastre ou selecione um fornecedor para visualizar suas condições.</p>}
              {selectedSupplier && (
                <>
                  {!selectedSupplier.isActive && <p className="calendar-block-message">Este fornecedor está inativo. Reative-o para cadastrar novas condições.</p>}
                  <p className="daily-sales-note supplier-unit-note">
                    Unidade de compra, preço, pedido mínimo e múltiplo pertencem à embalagem escolhida. Exemplo: 1 caixa = 12 unidades-base. O prazo é contado em dias corridos. Uma condição específica da loja substitui a condição compartilhada para aquela loja a partir da vigência informada.
                  </p>
                  {!isAdministrator && <p className="calendar-block-message">Esta condição será compartilhada pela rede. Somente o administrador pode cadastrar uma condição específica para uma loja.</p>}

                  <form className="workspace-form supplier-condition-form" onSubmit={submitCondition} aria-busy={busy}>
                    <h5>Adicionar versão da condição</h5>
                    <label htmlFor="conditionProduct">Produto</label>
                    <select disabled={busy || activeProducts.length === 0} id="conditionProduct" onChange={(event) => setConditionForm({ ...conditionForm, productId: event.target.value })} required value={conditionForm.productId}>
                      <option value="">Selecione um produto</option>
                      {activeProducts.map((product) => <option key={product.id} value={product.id}>{product.sku} · {product.name} ({unitName(product.baseUnit)})</option>)}
                    </select>
                    {isAdministrator ? (
                      <>
                        <label htmlFor="conditionScope">Escopo da condição</label>
                        <select disabled={busy} id="conditionScope" onChange={(event) => setConditionForm({ ...conditionForm, storeId: event.target.value })} value={conditionForm.storeId}>
                          <option value="">Compartilhada pela rede</option>
                          {activeStores.map((store) => <option key={store.id} value={store.id}>{store.name} · substitui a condição compartilhada</option>)}
                        </select>
                      </>
                    ) : (
                      <p className="field-hint supplier-network-scope">Escopo: compartilhado pela rede.</p>
                    )}
                    <div className="supplier-condition-grid">
                      <div>
                        <label htmlFor="purchaseUnitName">Unidade de compra</label>
                        <input autoComplete="off" disabled={busy} id="purchaseUnitName" maxLength={40} onChange={(event) => setConditionForm({ ...conditionForm, purchaseUnitName: event.target.value })} placeholder="Ex.: caixa" required value={conditionForm.purchaseUnitName} />
                      </div>
                      <div>
                        <label htmlFor="baseUnitsPerPurchaseUnit">Unidades-base por unidade de compra</label>
                        <input disabled={busy} id="baseUnitsPerPurchaseUnit" inputMode="decimal" onChange={(event) => setConditionForm({ ...conditionForm, baseUnitsPerPurchaseUnit: event.target.value })} required value={conditionForm.baseUnitsPerPurchaseUnit} />
                        <span className="field-hint">{selectedProduct ? `1 ${conditionForm.purchaseUnitName || 'embalagem'} = ${conditionForm.baseUnitsPerPurchaseUnit || '…'} ${unitName(selectedProduct.baseUnit)}` : 'Selecione o produto para ver a unidade-base.'}</span>
                      </div>
                      <div>
                        <label htmlFor="pricePerPurchaseUnit">Preço por unidade de compra (R$)</label>
                        <input disabled={busy} id="pricePerPurchaseUnit" inputMode="decimal" onChange={(event) => setConditionForm({ ...conditionForm, pricePerPurchaseUnit: event.target.value })} required value={conditionForm.pricePerPurchaseUnit} />
                      </div>
                      <div>
                        <label htmlFor="deliveryLeadTimeDays">Prazo de entrega (dias corridos)</label>
                        <input disabled={busy} id="deliveryLeadTimeDays" inputMode="numeric" onChange={(event) => setConditionForm({ ...conditionForm, deliveryLeadTimeDays: event.target.value })} required value={conditionForm.deliveryLeadTimeDays} />
                      </div>
                      <div>
                        <label htmlFor="minimumPurchaseQuantity">Pedido mínimo na unidade de compra</label>
                        <input disabled={busy} id="minimumPurchaseQuantity" inputMode="decimal" onChange={(event) => setConditionForm({ ...conditionForm, minimumPurchaseQuantity: event.target.value })} required value={conditionForm.minimumPurchaseQuantity} />
                      </div>
                      <div>
                        <label htmlFor="purchaseMultiple">Múltiplo na unidade de compra</label>
                        <input disabled={busy} id="purchaseMultiple" inputMode="decimal" onChange={(event) => setConditionForm({ ...conditionForm, purchaseMultiple: event.target.value })} required value={conditionForm.purchaseMultiple} />
                      </div>
                    </div>
                    <p className="field-hint">Para quantidades fracionárias, use até 3 casas decimais em kg/litro. Para unidade contável, conversão, mínimo e múltiplo devem ser inteiros. Use vírgula ou ponto decimal, sem separador de milhar.</p>
                    <label htmlFor="conditionEffectiveFrom">Vigente a partir de</label>
                    <input disabled={busy} id="conditionEffectiveFrom" onChange={(event) => setConditionForm({ ...conditionForm, effectiveFrom: event.target.value })} required type="date" value={conditionForm.effectiveFrom} />
                    <label className="checkbox-label" htmlFor="conditionAvailable">
                      <input checked={conditionForm.isAvailable} disabled={busy} id="conditionAvailable" onChange={(event) => setConditionForm({ ...conditionForm, isAvailable: event.target.checked })} type="checkbox" />
                      Fornecedor disponível para este produto a partir da vigência
                    </label>
                    {!conditionForm.isAvailable && <p className="field-hint">Esta versão marca a condição como indisponível a partir da data informada.</p>}
                    <button className="submit-button" disabled={busy || conditionsLoading || !selectedSupplier.isActive || !conditionForm.productId || !conditionForm.effectiveFrom} type="submit">{busy ? 'Salvando…' : 'Adicionar versão'}</button>
                  </form>

                  <div className="supplier-history-heading">
                    <h5>Histórico de condições</h5>
                    {conditionsLoading && <span role="status">Carregando…</span>}
                  </div>
                  <div aria-label={`Histórico de condições de ${selectedSupplier.name}`} className="table-scroll supplier-condition-table-scroll" role="region" tabIndex={0}>
                    <table className="balance-table supplier-condition-table">
                      <caption>Condições compartilhadas e versões específicas por loja para {selectedSupplier.name}</caption>
                      <thead><tr><th scope="col">Produto</th><th scope="col">Escopo</th><th scope="col">Vigência</th><th scope="col">Unidade de compra</th><th scope="col">Preço</th><th scope="col">Prazo</th><th scope="col">Mínimo / múltiplo</th><th scope="col">Disponibilidade</th><th scope="col">Registrada em</th></tr></thead>
                      <tbody>
                        {sortedConditions.map((condition) => {
                          const product = conditionProducts.get(condition.productId);
                          const store = condition.storeId ? conditionStores.get(condition.storeId) : null;
                          const stockUnit = product ? unitName(product.baseUnit) : 'unidade-base';
                          return (
                            <tr key={condition.id}>
                              <td>{product ? `${product.sku} · ${product.name}` : condition.productId}</td>
                              <td>{condition.storeId ? store?.name ?? condition.storeId : 'Rede · compartilhada'}</td>
                              <td>{displayDate(condition.effectiveFrom)}</td>
                              <td>{condition.purchaseUnitName}: 1 = {condition.baseUnitsPerPurchaseUnit} {stockUnit}</td>
                              <td>{formatBrlExact(condition.pricePerPurchaseUnit)} / {condition.purchaseUnitName}</td>
                              <td>{condition.deliveryLeadTimeDays} {condition.deliveryLeadTimeDays === 1 ? 'dia' : 'dias'} corridos</td>
                              <td>{condition.minimumPurchaseQuantity} / {condition.purchaseMultiple} {condition.purchaseUnitName}</td>
                              <td>{condition.isAvailable ? 'Disponível' : 'Indisponível'}</td>
                              <td title={`Usuário ${condition.createdBy}`}>{displayTimestamp(condition.createdAt)} · {condition.createdBy.slice(0, 8)}</td>
                            </tr>
                          );
                        })}
                        {!conditionsLoading && conditions.length === 0 && <tr><td className="supplier-condition-empty" colSpan={9}>Nenhuma condição cadastrada para este fornecedor.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                  <p className="field-hint supplier-audit-note">Cada versão é imutável. Registrado por usuário e horário no servidor; última atualização do fornecedor: {displayTimestamp(selectedSupplier.updatedAt)}.</p>
                </>
              )}
          </div>
        </>
      )}
    </section>
  );
}
