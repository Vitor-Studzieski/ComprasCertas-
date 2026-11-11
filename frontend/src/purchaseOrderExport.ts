export type PurchaseOrderExportItem = {
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
};

export type PurchaseOrderExport = {
  id: string;
  supplierName: string;
  status: string;
  items: PurchaseOrderExportItem[];
  estimatedTotal: string | null;
  committedAmount: string | null;
  createdAt: string;
  approvedAt: string | null;
  budgetWeekStart: string | null;
  budgetMonthStart: string | null;
  budgetExceptionReason?: string | null;
  budgetStatus?: string;
  sourceAsOfDate?: string | null;
  freightStatus?: string;
  costCompleteness?: string;
};

type Cell = { text: string } | { number: string } | null;

function text(value: string | null | undefined): Cell {
  return value == null ? null : { text: value };
}

function number(value: string | null | undefined): Cell {
  if (value == null || value === '') return null;
  if (!/^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)) return { text: value };
  return { number: value };
}

function xmlEscape(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

function columnName(index: number): string {
  let value = index + 1;
  let result = '';
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function cellXml(cell: Cell, reference: string): string {
  if (cell === null) return `<c r="${reference}"/>`;
  if ('number' in cell) return `<c r="${reference}" t="n"><v>${cell.number}</v></c>`;
  return `<c r="${reference}" t="inlineStr"><is><t xml:space="preserve">${xmlEscape(cell.text)}</t></is></c>`;
}

function workbookXml(rows: Cell[][]): string {
  const sheetRows = rows.map((row, rowIndex) => {
    const cells = row.map((cell, columnIndex) => cellXml(cell, `${columnName(columnIndex)}${rowIndex + 1}`)).join('');
    return `<row r="${rowIndex + 1}">${cells}</row>`;
  }).join('');
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${sheetRows}</sheetData></worksheet>`;
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function littleEndian(size: number, write: (view: DataView) => void): Uint8Array {
  const bytes = new Uint8Array(size);
  write(new DataView(bytes.buffer));
  return bytes;
}

function zipStoredFiles(files: Array<{ name: string; content: string }>): Uint8Array {
  const encoder = new TextEncoder();
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let localOffset = 0;

  for (const file of files) {
    const filename = encoder.encode(file.name);
    const data = encoder.encode(file.content);
    const checksum = crc32(data);
    const localHeader = littleEndian(30, (view) => {
      view.setUint32(0, 0x04034b50, true);
      view.setUint16(4, 20, true);
      view.setUint16(6, 0x0800, true);
      view.setUint16(8, 0, true);
      view.setUint16(10, 0, true);
      view.setUint16(12, 0x0021, true);
      view.setUint32(14, checksum, true);
      view.setUint32(18, data.length, true);
      view.setUint32(22, data.length, true);
      view.setUint16(26, filename.length, true);
      view.setUint16(28, 0, true);
    });
    localParts.push(localHeader, filename, data);

    const centralHeader = littleEndian(46, (view) => {
      view.setUint32(0, 0x02014b50, true);
      view.setUint16(4, 20, true);
      view.setUint16(6, 20, true);
      view.setUint16(8, 0x0800, true);
      view.setUint16(10, 0, true);
      view.setUint16(12, 0, true);
      view.setUint16(14, 0x0021, true);
      view.setUint32(16, checksum, true);
      view.setUint32(20, data.length, true);
      view.setUint32(24, data.length, true);
      view.setUint16(28, filename.length, true);
      view.setUint16(30, 0, true);
      view.setUint16(32, 0, true);
      view.setUint16(34, 0, true);
      view.setUint16(36, 0, true);
      view.setUint32(38, 0, true);
      view.setUint32(42, localOffset, true);
    });
    centralParts.push(centralHeader, filename);
    localOffset += localHeader.length + filename.length + data.length;
  }

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const endRecord = littleEndian(22, (view) => {
    view.setUint32(0, 0x06054b50, true);
    view.setUint16(4, 0, true);
    view.setUint16(6, 0, true);
    view.setUint16(8, files.length, true);
    view.setUint16(10, files.length, true);
    view.setUint32(12, centralSize, true);
    view.setUint32(16, localOffset, true);
    view.setUint16(20, 0, true);
  });
  const allParts = [...localParts, ...centralParts, endRecord];
  const result = new Uint8Array(allParts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of allParts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function safeFilename(value: string): string {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() || 'pedido';
}

function download(bytes: Uint8Array, contentType: string, filename: string): void {
  const ownedBytes = new Uint8Array(bytes.byteLength);
  ownedBytes.set(bytes);
  const url = URL.createObjectURL(new Blob([ownedBytes.buffer as ArrayBuffer], { type: contentType }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    approved: 'Aprovado',
    partially_received: 'Recebido parcialmente',
    received: 'Recebido',
  };
  return labels[value] ?? value.replaceAll('_', ' ');
}

function dateLabel(value: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeZone: 'UTC' }).format(new Date(`${value.slice(0, 10)}T00:00:00Z`));
}

export function exportPurchaseOrderXlsx(order: PurchaseOrderExport, storeName: string): void {
  const rows: Cell[][] = [
    [text('CompraCerta · pedido de compra')],
    [text('Loja'), text(storeName)],
    [text('Fornecedor'), text(order.supplierName)],
    [text('Status'), text(statusLabel(order.status))],
    [text('Criado em'), text(new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(order.createdAt)))],
    [text('Aprovado em'), text(order.approvedAt ? new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(order.approvedAt)) : '—')],
    [text('Dados da recomendação até'), text(dateLabel(order.sourceAsOfDate ?? null))],
    [text('Validação do orçamento'), text(order.budgetStatus?.replaceAll('_', ' ') ?? '—')],
    [text('Estimativa de mercadoria (R$)'), number(order.estimatedTotal)],
    [text('Valor comprometido (R$)'), number(order.committedAmount)],
    [text('Semana do orçamento'), text(dateLabel(order.budgetWeekStart))],
    [text('Mês do orçamento'), text(dateLabel(order.budgetMonthStart))],
    [text('Frete'), text(order.freightStatus === 'unknown' || order.costCompleteness === 'partial' ? 'Não informado' : 'Informado')],
    [text('Completude do custo'), text(order.costCompleteness === 'partial' ? 'Parcial; não inclui frete desconhecido.' : 'Conforme custos conhecidos no pedido.')],
    [text('Justificativa da exceção'), text(order.budgetExceptionReason)],
    [],
    ['SKU', 'Produto', 'Unidade de compra', 'Unidades base/embalagem', 'Quantidade sugerida', 'Quantidade pedida', 'Quantidade aprovada', 'Quantidade recebida', 'Preço por unidade (R$)', 'Estimativa da linha (R$)'].map(text),
    ...order.items.map((item) => [
      text(item.sku), text(item.productName), text(item.purchaseUnitName), number(item.baseUnitsPerPurchaseUnit),
      number(item.recommendedQuantity), number(item.quantity), number(item.approvedQuantity), number(item.receivedQuantity),
      number(item.unitPrice), number(item.estimatedLineTotal),
    ]),
  ];
  const files = [
    { name: '[Content_Types].xml', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>' },
    { name: '_rels/.rels', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>' },
    { name: 'xl/workbook.xml', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Pedido" sheetId="1" r:id="rId1"/></sheets></workbook>' },
    { name: 'xl/_rels/workbook.xml.rels', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>' },
    { name: 'xl/worksheets/sheet1.xml', content: workbookXml(rows) },
  ];
  const file = zipStoredFiles(files);
  download(file, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', `${safeFilename(storeName)}-pedido-${order.id.slice(0, 8)}.xlsx`);
}

export function printPurchaseOrder(title: string): void {
  const previousTitle = document.title;
  document.title = safeFilename(title);
  const restoreTitle = () => { document.title = previousTitle; };
  window.addEventListener('afterprint', restoreTitle, { once: true });
  window.print();
  window.setTimeout(restoreTitle, 30_000);
}
