'use strict';
async function mountPlanning() {
  if (document.getElementById('farmPlanning')) return;
  let data = await api('/api/farm/planning'), offset = 0, pending = null, previewSignature = null, evidence = null;
  const root = document.createElement('details'); root.id = 'farmPlanning';
  document.getElementById('farmFinanceCard').append(root);
  function add(tag, text, parent = root) {
    const e = document.createElement(tag); if (text !== null) e.textContent = text; parent.append(e); return e;
  }
  add('summary', 'Budgets and forecasts');
  add('p', 'Explore a plan without changing farm records or spending money. Leave anything unknown blank. These are your assumptions, not predictions verified by AI.');
  const status = add('p', ''); status.id = 'farmPlanStatus'; status.setAttribute('role', 'status');
  const saved = add('div', null), form = add('form', null); form.id = 'farmPlanForm';
  const controls = {};
  function field(parent, label, key, type = 'text', required = false) {
    const wrap = add('label', label, parent), input = add('input', null, wrap);
    input.id = 'farmPlan_' + key; input.type = type; input.required = required; controls[key] = input;
    return input;
  }
  field(form, 'Plan name', 'name', 'text', true).maxLength = 120;
  field(form, 'Start date (Lagos)', 'start', 'date', true);
  field(form, 'End date, inclusive (Lagos)', 'end', 'date', true);
  const currencyLabel = add('label', 'Currency', form), currency = add('select', null, currencyLabel);
  currency.id = 'farmPlan_currency'; controls.currency = currency;
  for (const value of ['NGN', 'USD', 'GBP', 'EUR']) add('option', value, currency).value = value;
  function group(label, fields) {
    const section = add('details', null, form); add('summary', label, section);
    for (const [key, text, step] of fields) {
      const input = field(section, text, key, 'number'); input.min = '0'; input.step = step;
    }
  }
  group('1. Egg production', [['eggs_per_day', 'Expected eggs collected each day', '.001']]);
  group('2. Feed duration', [['feed_kg_at_start', 'Feed at the start (kg)', '.001'], ['feed_kg_per_day', 'Expected feed used each day (kg)', '.001']]);
  group('3. Sales value', [['eggs_to_sell', 'Individual eggs expected to sell during this period', '1'], ['egg_price_minor', 'Expected price per individual egg, in the selected currency', '.01']]);
  group('4. Customer receipts', [['customer_receipts_minor', 'Customer payments expected during this period', '.01']]);
  group('5. Supplier payments', [['supplier_payments_minor', 'Payments to suppliers expected during this period', '.01']]);
  group('6. Operating expenses and optional budget', [['operating_expenses_minor', 'Expected operating costs for this period', '.01'], ['operating_budget_minor', 'Optional operating-cost budget for this period', '.01']]);
  const basisLabel = add('label', 'Where did these assumptions come from? Note dates, records, estimates and anything excluded.', form);
  const basis = add('textarea', null, basisLabel); basis.id = 'farmPlan_basis'; basis.required = true; basis.maxLength = 1000; controls.basis = basis;
  const preview = add('button', 'Preview plan', form); preview.type = 'submit';
  const save = add('button', 'Save this plan as a new version', form); save.type = 'button'; save.hidden = !data.can_save; save.disabled = true;
  const output = add('div', null); output.id = 'farmPlanOutput';
  const quantities = ['eggs_per_day', 'feed_kg_at_start', 'feed_kg_per_day'];
  const amounts = ['egg_price_minor', 'customer_receipts_minor', 'supplier_payments_minor', 'operating_expenses_minor', 'operating_budget_minor'];
  function values() {
    const assumptions = {};
    for (const key of quantities) assumptions[key] = controls[key].value || null;
    for (const key of amounts) assumptions[key] = controls[key].value === '' ? null : farmMinorAmount(controls[key].value);
    assumptions.eggs_to_sell = controls.eggs_to_sell.value === '' ? null : Number(controls.eggs_to_sell.value);
    return {name: controls.name.value, start: controls.start.value, end: controls.end.value, currency: currency.value, basis: basis.value, assumptions};
  }
  function money(value, code) {
    if (value === null) return 'Unavailable';
    const n = BigInt(value), magnitude = n < 0n ? -n : n;
    return code + ' ' + (n < 0n ? '-' : '') + (magnitude / 100n) + '.' + (magnitude % 100n).toString().padStart(2, '0');
  }
  function show(result, title) {
    output.replaceChildren(); add('h4', title, output);
    const lines = [
      ['Egg production', result.egg_production.estimated_eggs === null ? 'Unavailable' : result.egg_production.estimated_eggs + ' eggs (rounded down)'],
      ['Feed use', result.feed.estimated_use_kg === null ? 'Unavailable' : result.feed.estimated_use_kg + ' kg'],
      ['Feed duration', result.feed.days_available === null ? 'Unavailable' : result.feed.days_available + ' days, rounded'],
      ['Feed remaining at period end', result.feed.end_balance_kg === null ? 'Unavailable' : result.feed.end_balance_kg + ' kg'],
      ['Sales value', money(result.sales_value_minor, result.currency)],
      ['Expected customer receipts', money(result.customer_receipts_minor, result.currency)],
      ['Expected supplier payments', money(result.supplier_payments_minor, result.currency)],
      ['Expected operating expenses', money(result.operating_expenses_minor, result.currency)],
      ['Operating-cost budget', result.budget.status === 'NOT_SET' ? 'Not set' : money(result.budget.operating_limit_minor, result.currency)],
      ['Budget less planned operating costs', money(result.budget.remaining_minor, result.currency)]
    ];
    for (const [label, value] of lines) add('p', label + ': ' + value, output);
    if (result.budget.status === 'OVER_PLAN') add('p', 'Planned operating costs exceed this budget. This is not a spending approval.', output);
    const notes = add('ul', null, output); for (const text of result.limitations) add('li', text, notes);
  }
  form.addEventListener('input', () => { save.disabled = !pending; previewSignature = null; });
  form.onsubmit = async event => {
    event.preventDefault(); preview.disabled = true; save.disabled = true;
    try {
      if (pending) throw Error('The previous save outcome is unknown. Retry saving the unchanged plan first.');
      const p = values(), result = await farmPost('/api/farm/planning/preview', p);
      previewSignature = JSON.stringify(p); evidence = result.evidence_revision;
      show(result, 'Scenario preview — ' + result.days + ' days');
      save.disabled = !data.can_save; status.textContent = 'Preview only. No farm records or payments changed.';
    } catch (error) { output.replaceChildren(); status.textContent = error.message; }
    finally { preview.disabled = false; }
  };
  function renderSaved() {
    saved.replaceChildren(); add('h4', 'Saved plans', saved); add('p', data.notice, saved);
    for (const row of data.records) {
      const p = row.payload, card = add('details', null, saved);
      add('summary', p.name + ' — ' + p.start + ' to ' + p.end, card);
      add('p', row.review_needed ? 'New farm records have arrived. Review these assumptions; the saved result has not changed.' : 'Saved estimate. Unchanged records do not establish that assumptions are correct.', card);
      add('p', 'Saved: ' + farmDateText(row.received_at) + '. Version: ' + p.event_id, card);
      add('p', 'Basis: ' + p.basis, card);
      const view = add('button', 'View saved result', card); view.type = 'button';
      view.onclick = () => { show(row.result, 'Saved estimate — ' + p.name); status.textContent = 'Viewing the original saved result.'; };
      const reuse = add('button', 'Use as a new plan', card); reuse.type = 'button';
      reuse.onclick = () => {
        if (pending) { status.textContent = 'Resolve the previous save before replacing the draft.'; return; }
        for (const key of ['name', 'start', 'end', 'currency', 'basis']) controls[key].value = p[key];
        for (const key of [...quantities, 'eggs_to_sell']) controls[key].value = p.assumptions[key] ?? '';
        for (const key of amounts) controls[key].value = p.assumptions[key] === null ? '' : money(String(p.assumptions[key]), '').trim();
        save.disabled = true; previewSignature = null;
        status.textContent = 'Assumptions copied. Review dates and values, then preview. The saved plan remains unchanged.';
      };
    }
    const refresh = add('button', 'Refresh saved plans', saved); refresh.type = 'button'; refresh.onclick = () => reload(0);
    if (offset) { const previous = add('button', 'Newer plans', saved); previous.type = 'button'; previous.onclick = () => reload(Math.max(0, offset - 20)); }
    if (data.next_offset !== null) { const next = add('button', 'Older plans', saved); next.type = 'button'; next.onclick = () => reload(data.next_offset); }
  }
  async function reload(nextOffset) {
    try { const next = await api('/api/farm/planning?offset=' + nextOffset); data = next; offset = nextOffset; save.hidden = !data.can_save; renderSaved(); }
    catch (error) { status.textContent = error.message; }
  }
  save.onclick = async () => {
    save.disabled = true;
    try {
      const p = values(), signature = JSON.stringify(p);
      if (pending && pending.signature !== signature) throw Error('Retry the unchanged plan: its previous save outcome is unknown.');
      if (!pending) {
        if (previewSignature !== signature) throw Error('Preview these exact assumptions before saving.');
        pending = {signature, payload: {...p, event_id: crypto.randomUUID(), expected_revision: data.revision, expected_evidence_revision: evidence}};
      }
      await farmPost('/api/farm/planning', pending.payload);
      pending = null; previewSignature = null; await reload(0);
      status.textContent = 'Plan saved as a new version. Farm records and payments are unchanged.';
    } catch (error) {
      if (error.status >= 400 && error.status < 500) { pending = null; previewSignature = null; }
      status.textContent = error.message;
    } finally { save.disabled = !pending; }
  };
  renderSaved();
}
