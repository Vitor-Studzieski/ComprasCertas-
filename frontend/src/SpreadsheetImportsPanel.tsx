import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { apiPost, apiPostFormData, ApiError } from './api';

type ImportKind = 'products' | 'suppliers' | 'conditions' | 'opening_stock' | 'daily_sales' | 'out_of_stock_days';
type StoreOption = { id: string; name: string };
type PreviewIssue = { rowNumber: number | null; code: string; field?: string; message: string };
type PreviewChange = { rowNumber: number | null; key: string; action: string; before: Record<string, unknown>; after: Record<string, unknown> };
type ImportPreview = {
  batchId: string;
  kind: ImportKind;
  fileName: string;
  status: 'preview' | 'confirmed' | 'expired';
  expiresAt?: string;
  counts: { rows: number; create: number; update: number; unchanged: number };
  issueCount: number;
  issues: PreviewIssue[];
  changeCount: number;
  changes: PreviewChange[];
  canConfirm: boolean;
};
type Props = {
  role: string;
  canManageCatalog: boolean;
  stores: StoreOption[];
  selectedStoreId: string;
  onImported: () => void;
};

const kindLabels: Record<ImportKind, string> = {
  products: 'Produtos e categorias',
  suppliers: 'Fornecedores',
  conditions: 'Preços e condições de compra',
  opening_stock: 'Estoque inicial',
  daily_sales: 'Vendas diárias',
  out_of_stock_days: 'Dias sem estoque',
};

const kindHelp: Record<ImportKind, string> = {
  products: 'Colunas exatas: SKU; Nome; Unidade; Categoria. Unidade aceita unidade, kg ou litro. SKU existente atualiza nome e categoria; categoria vazia remove o vínculo, categoria nova é criada, o saldo não muda e a unidade-base permanece protegida.',
  suppliers: 'Colunas exatas: Nome; Contato; E-mail; Telefone; Observações. Fornecedor existente é atualizado pelo nome, sem reativar registros inativos.',
  conditions: 'Colunas exatas: SKU; Fornecedor; Loja; Vigente desde; Unidade de compra; Unidades por embalagem; Preço por embalagem; Prazo (dias corridos); Pedido mínimo; Múltiplo de compra. Deixe Loja vazia para aplicar à rede.',
  opening_stock: 'Colunas exatas: SKU; Quantidade. Inclua todos os SKUs ativos no sortimento na data fechada, inclusive com quantidade zero.',
  daily_sales: 'Colunas exatas: SKU; Data; Quantidade. Datas no arquivo usam DD/MM/AAAA e quantidades fracionárias usam vírgula decimal.',
  out_of_stock_days: 'Colunas exatas: SKU; Data. Registre somente dias confirmados sem estoque; datas usam DD/MM/AAAA.',
};

function formatAction(action: string): string {
  return ({ create: 'Incluir', update: 'Atualizar', unchanged: 'Sem alteração', conflict: 'Conflito' } as Record<string, string>)[action] ?? action;
}

function formatValue(value: Record<string, unknown>): string {
  const entries = Object.entries(value ?? {});
  return entries.length ? entries.map(([key, item]) => `${key}: ${String(item ?? '—')}`).join(' · ') : '—';
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.fields ? Object.values(error.fields).flat().join(' ') : '';
    return details || error.message;
  }
  return error instanceof Error ? error.message : 'Não foi possível concluir a importação.';
}

export default function SpreadsheetImportsPanel({ role, canManageCatalog, stores, selectedStoreId, onImported }: Props) {
  const allowedKinds = useMemo(() => {
    const options: ImportKind[] = [];
    if (canManageCatalog) options.push('products', 'suppliers', 'conditions');
    if (['administrator', 'store_manager', 'operator'].includes(role)) options.push('opening_stock', 'daily_sales', 'out_of_stock_days');
    return options;
  }, [canManageCatalog, role]);
  const [kind, setKind] = useState<ImportKind>(allowedKinds[0] ?? 'products');
  const [file, setFile] = useState<File | null>(null);
  const [closedOperationalDate, setClosedOperationalDate] = useState('');
  const [operationalDateFrom, setOperationalDateFrom] = useState('');
  const [operationalDateTo, setOperationalDateTo] = useState('');
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const needsStore = ['opening_stock', 'daily_sales', 'out_of_stock_days'].includes(kind);

  useEffect(() => {
    if (!allowedKinds.includes(kind) && allowedKinds.length) setKind(allowedKinds[0]);
  }, [allowedKinds, kind]);

  useEffect(() => {
    setPreview(null);
    setFile(null);
    if (fileInput.current) fileInput.current.value = '';
    setError('');
    setNotice('');
  }, [kind, selectedStoreId, closedOperationalDate, operationalDateFrom, operationalDateTo]);

  if (!allowedKinds.length) return null;

  async function downloadTemplate(format: 'csv' | 'xlsx') {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`/api/v1/imports/templates/${kind}/?format=${format}`, { credentials: 'include' });
      if (!response.ok) throw new Error('Não foi possível baixar o modelo de importação.');
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = `comprascerta-modelo-${kind}.${format}`;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch (requestError) {
      setError(apiErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function submitPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError('Selecione um arquivo CSV ou .xlsx.');
      return;
    }
    if (needsStore && !selectedStoreId) {
      setError('Selecione uma loja antes de importar.');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    setPreview(null);
    try {
      const body = new FormData();
      body.set('kind', kind);
      body.set('file', file);
      if (needsStore) body.set('storeId', selectedStoreId);
      if (kind === 'opening_stock') body.set('closedOperationalDate', closedOperationalDate);
      if (kind === 'daily_sales') {
        body.set('operationalDateFrom', operationalDateFrom);
        body.set('operationalDateTo', operationalDateTo);
      }
      const result = await apiPostFormData<ImportPreview>('/imports/previews/', body);
      setPreview(result);
      setNotice(result.canConfirm
        ? 'Prévia pronta. Confira as alterações e confirme para gravar tudo atomicamente.'
        : 'A validação encontrou erros. Nenhuma linha será gravada até corrigir o arquivo.');
    } catch (requestError) {
      setError(apiErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function confirmImport() {
    if (!preview || !preview.canConfirm || preview.status !== 'preview') return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await apiPost<ImportPreview>(`/imports/previews/${preview.batchId}/confirm/`, {});
      setPreview(result);
      setNotice(`Importação concluída: ${result.counts.create} inclusão(ões), ${result.counts.update} atualização(ões) e ${result.counts.unchanged} sem alteração.`);
      setFile(null);
      if (fileInput.current) fileInput.current.value = '';
      onImported();
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.preview && typeof requestError.preview === 'object') {
        setPreview(requestError.preview as ImportPreview);
      } else if (requestError instanceof ApiError && requestError.code === 'import_preview_expired') {
        setPreview({ ...preview, status: 'expired', canConfirm: false });
      }
      setError(apiErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace-section spreadsheet-import-panel" aria-labelledby="spreadsheet-import-heading">
      <div className="section-heading">
        <span className="step-number" aria-hidden="true">⇧</span>
        <div><h3 id="spreadsheet-import-heading">Importação de planilhas</h3><p>Use o modelo exato do CompraCerta. O arquivo inteiro é validado antes da gravação.</p></div>
      </div>
      <div className="spreadsheet-template-actions" aria-label="Baixar modelo da importação selecionada">
        <span className="field-hint">Modelo de {kindLabels[kind]}:</span>
        <button className="text-button" disabled={busy} onClick={() => void downloadTemplate('csv')} type="button">Baixar CSV</button>
        <button className="text-button" disabled={busy} onClick={() => void downloadTemplate('xlsx')} type="button">Baixar Excel</button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="form-notice" role="status">{notice}</p>}
      <form className="workspace-form spreadsheet-import-form" onSubmit={submitPreview} aria-busy={busy}>
        <label htmlFor="spreadsheetImportKind">Tipo de dados</label>
        <select disabled={busy} id="spreadsheetImportKind" onChange={(event) => setKind(event.target.value as ImportKind)} value={kind}>
          {allowedKinds.map((option) => <option key={option} value={option}>{kindLabels[option]}</option>)}
        </select>
        {needsStore && <p className="field-hint">Loja da importação: {stores.find((store) => store.id === selectedStoreId)?.name ?? 'selecione uma loja acima'}.</p>}
        {kind === 'opening_stock' && <>
          <label htmlFor="importClosedDate">Data operacional fechada pela contagem</label>
          <input disabled={busy} id="importClosedDate" onChange={(event) => setClosedOperationalDate(event.target.value)} required type="date" value={closedOperationalDate} />
        </>}
        {kind === 'daily_sales' && <>
          <label htmlFor="importSalesFrom">Período completo: de</label>
          <input disabled={busy} id="importSalesFrom" onChange={(event) => setOperationalDateFrom(event.target.value)} required type="date" value={operationalDateFrom} />
          <label htmlFor="importSalesTo">até</label>
          <input disabled={busy} id="importSalesTo" onChange={(event) => setOperationalDateTo(event.target.value)} required type="date" value={operationalDateTo} />
          <p className="field-hint">Dentro desse período completo, SKU/dia sem linha significa zero somente enquanto o produto esteve ativo no sortimento.</p>
        </>}
        <label htmlFor="spreadsheetImportFile">Arquivo CSV ou Excel (.xlsx)</label>
        <input accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" disabled={busy} id="spreadsheetImportFile" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); }} ref={fileInput} required type="file" />
          <p className="field-hint">{kindHelp[kind]}</p>
          <p className="field-hint">CSV: UTF-8, separador ponto e vírgula, vírgula decimal e data DD/MM/AAAA. Limite de 10 MB e 10.000 linhas.</p>
        <button className="submit-button" disabled={busy || (needsStore && !selectedStoreId)} type="submit">{busy ? 'Validando…' : 'Gerar prévia'}</button>
      </form>
      {preview && <div className="spreadsheet-import-preview" aria-live="polite">
        <div className="spreadsheet-preview-heading">
          <div><h4>Prévia: {preview.fileName}</h4><p>{preview.counts.rows} linhas · {preview.status === 'confirmed' ? 'confirmada' : preview.status === 'expired' ? 'expirada' : 'aguardando confirmação'}</p></div>
          {preview.status === 'preview' && <button className="submit-button" disabled={busy || !preview.canConfirm} onClick={() => void confirmImport()} type="button">{busy ? 'Gravando…' : 'Confirmar importação'}</button>}
        </div>
        <dl className="spreadsheet-import-counts">
          <div><dt>Incluir</dt><dd>{preview.counts.create}</dd></div>
          <div><dt>Atualizar</dt><dd>{preview.counts.update}</dd></div>
          <div><dt>Sem alteração</dt><dd>{preview.counts.unchanged}</dd></div>
          <div><dt>Erros</dt><dd>{preview.issueCount}</dd></div>
        </dl>
        {preview.issueCount > 0 && <div className="table-scroll spreadsheet-issues-scroll" role="region" aria-label="Erros encontrados na planilha" tabIndex={0}>
          <table className="balance-table"><thead><tr><th>Linha</th><th>Campo</th><th>Problema</th></tr></thead><tbody>
            {preview.issues.map((issue, index) => <tr key={`${issue.rowNumber ?? 'file'}-${issue.code}-${index}`}><td>{issue.rowNumber ?? 'Arquivo'}</td><td>{issue.field ?? '—'}</td><td>{issue.message}</td></tr>)}
            {preview.issueCount > preview.issues.length && <tr><td colSpan={3}>Exibindo {preview.issues.length} de {preview.issueCount} erros.</td></tr>}
          </tbody></table>
        </div>}
        {preview.changeCount > 0 && <div className="table-scroll spreadsheet-changes-scroll" role="region" aria-label="Alterações previstas pela importação" tabIndex={0}>
          <table className="balance-table"><thead><tr><th>Linha</th><th>Registro</th><th>Ação</th><th>Dados atuais</th><th>Dados do arquivo</th></tr></thead><tbody>
            {preview.changes.map((change, index) => <tr key={`${change.rowNumber ?? 'row'}-${change.key}-${index}`}><td>{change.rowNumber ?? '—'}</td><td>{change.key}</td><td>{formatAction(change.action)}</td><td>{formatValue(change.before)}</td><td>{formatValue(change.after)}</td></tr>)}
            {preview.changeCount > preview.changes.length && <tr><td colSpan={5}>Exibindo {preview.changes.length} de {preview.changeCount} alterações.</td></tr>}
          </tbody></table>
        </div>}
        <p className="field-hint">A prévia expira em 30 minutos. Na confirmação, o sistema valida novamente o arquivo e os dados afetados; qualquer conflito impede toda a gravação.</p>
      </div>}
    </section>
  );
}
