'use strict';
// Human answers only: no AI interpretation, offline queue or automatic retries.
async function renderFarmClarifications() {
  let host = document.getElementById('farmClarifications');
  if (!host) {
    host = document.createElement('details'); host.id = 'farmClarifications'; host.className = 'card farmSection';
    document.getElementById('farmRecords').querySelector(':scope > p').after(host);
  }
  const view = await api('/api/farm/clarifications'); host.replaceChildren();
  if (!view.sources.length && !view.items.length && !Object.keys(view.templates).length) {host.hidden = true; return;}
  host.hidden = false;
  const el = (tag, text) => {const n = document.createElement(tag); if(text !== undefined)n.textContent = text; if(tag === 'button'){n.className='action';n.type='button';} return n;};
  const title = el('summary', 'Items to confirm');
  const status = el('p'); status.setAttribute('role', 'status');
  host.append(title, el('p', view.unresolved + ' unresolved items'), el('p', view.notice), status);
  const submit = async (button, command) => {
    button.disabled = true;
    try {await postJson('/api/farm/clarifications', command); await loadFarmRecords();}
    catch(e) {status.textContent = e.message; button.disabled = false;}
  };
  const create = el('details'); create.append(el('summary', 'Ask about an existing record'));
  const pickerLabel = el('label', 'Choose the record and question '), picker = el('select');
  picker.setAttribute('aria-label', 'Record to clarify');
  view.sources.forEach((s, i) => {const o = el('option', s.label); o.value = i; picker.append(o);});
  pickerLabel.append(picker); create.append(pickerLabel);
  const add = el('button', 'Add item'); add.type = 'button'; add.disabled = !view.sources.length;
  let createCommand = null;
  picker.onchange = () => {createCommand = null;};
  add.onclick = () => {
    const s = view.sources[Number(picker.value)]; if(!s)return;
    createCommand ||= {event_id: crypto.randomUUID(), operation: 'create', template: s.template, source: s.source};
    submit(add, createCommand);
  };
  create.append(add); if(!view.sources.length)create.append(el('p', 'Add a Farm record first. No missing stock or money has been assumed.'));
  host.append(create);
  const filterLabel = el('label', 'Topic '), filter = el('select'); filter.setAttribute('aria-label', 'Clarification topic');
  ['All', 'Operations', 'Finance', 'Planning'].forEach(t => {const o = el('option', t); o.value = t; filter.append(o);});
  filterLabel.append(filter); host.append(filterLabel);
  const list = el('div'); host.append(list);
  const fields = {quantity:'Quantity in individual eggs, birds or kilograms', unit:'Unit', observed_at:'Observation time (include timezone, for example +01:00)',
    value:'New planning value (money uses minor units)', start:'Planning start', end:'Planning end', amount_minor:'Sale total (currency amount, not kobo)', currency:'Currency',
    frequency:'Recurrence description', period_start:'Period start (optional unless recurring)', period_end:'Period end (optional unless recurring)'};
  const answerText = (key, value, currency) => key === 'amount_minor' ? financeMoney(value,currency) : key === 'items' ? value.map(row => row.description + ': ' + financeMoney(row.amount_minor, currency)).join('; ') : (value ?? 'Not known yet');
  for (const item of view.items) {
    const template = item.definition, card = el('details'); card.className = 'item'; card.dataset.topic = template.topic;
    const states = {OPEN:'Needs an answer', AWAITING_APPROVAL:'Owner review needed', ANSWERED:'Accepted', SUPERSEDED:'Replaced or closed'};
    card.append(el('summary', template.label + ' — ' + states[item.state] + (item.deferred ? ' · deferred' : '')));
    const src = view.sources.find(s => ['kind','event_id','field'].every(k => s.source[k] === item.source[k]));
    card.append(el('p', item.source_label));
    if(item.source_changed && !['ANSWERED','SUPERSEDED'].includes(item.state))card.append(el('p', 'The source changed. Close this question and select a fresh record before applying an answer.'));
    if(item.deferred)card.append(el('p', item.deferred_until ? 'Deferred until '+item.deferred_until : 'Deferred until you are ready; no reminders will be sent.'));
    const complete = item.state === 'SUPERSEDED' || item.applied !== undefined;
    if(!complete && !item.source_changed) {
      const form = el('form'), optionLabel = el('label', 'Answer '), option = el('select'); option.setAttribute('aria-label', 'Answer');
      const isPlan = item.source.kind === 'farm_planning_scenario_v1';
      for(const [id, label] of Object.entries(template.options)) {
        if(isPlan && !['plan','target','unknown'].includes(id))continue;
        if(!isPlan && ['plan','target'].includes(id))continue;
        const o = el('option', label); o.value = id; option.append(o);
      }
      optionLabel.append(option); form.append(optionLabel);
      const follow = el('div'), noteLabel = el('label', 'Optional notes '), notes = el('textarea'); notes.maxLength = 1000;
      notes.setAttribute('aria-label', 'Optional notes'); noteLabel.append(notes);
      const deferLabel = el('label', 'Revisit on (optional; blank means when ready) '), defer = el('input'); defer.type = 'date'; deferLabel.append(defer);
      let inputs = {}, lineInputs = [], answerCommand = null;
      const rebuild = () => {
        follow.replaceChildren(); inputs = {}; lineInputs = []; answerCommand = null;
        const choice = option.value; deferLabel.hidden = choice !== 'unknown';
        let keys = [];
        if(choice !== 'unknown') {
          if(['feed','stock','number'].includes(item.template))keys = choice === 'historical' ? ['observed_at'] : isPlan ? ['value','start','end'] : ['quantity','unit','observed_at'];
          else if(item.template === 'price')keys = ['amount_minor','currency'];
          else if(item.template === 'transport')keys = ['frequency','period_start','period_end'];
        }
        for(const key of keys) {
          const label = el('label', fields[key]); let input;
          const choices = key === 'unit' ? ['eggs','birds','kg'] : key === 'currency' ? ['NGN','USD','GBP','EUR'] : key === 'frequency' ? ['unknown','one_off','recurring'] : null;
          if(choices) {input = el('select'); for(const v of choices){const o=el('option',v.replaceAll('_',' '));o.value=v;input.append(o);}}
          else {input = el('input'); input.type = ['start','end','period_start','period_end'].includes(key) ? 'date' : 'text'; input.required = !key.startsWith('period_');}
          input.setAttribute('aria-label', fields[key]); inputs[key] = input; label.append(input); follow.append(label);
          if(key === 'unit' && src?.unit)input.value = src.unit;
          if(key === 'currency' && src?.currency)input.value = src.currency;
          if(key === 'observed_at' && choice === 'correct' && src?.observed_at)input.value = src.observed_at;
        }
        if(item.template === 'price' && choice === 'correct' && template.sale_items?.length) {
          follow.append(el('p', 'Review every sale item below. Enter amounts in the selected currency, not kobo. Their total must match the corrected sale total.'));
          const rows = item.answer?.data.items || template.sale_items;
          rows.forEach((row, index) => {
            const label = el('label', 'Item '+(index+1)+': '+row.description+' — amount');
            const input = el('input'); input.type='number'; input.min='0.01'; input.step='0.01'; input.required=true;
            input.value=(row.amount_minor/100).toFixed(2); input.setAttribute('aria-label','Item '+(index+1)+' amount');
            lineInputs.push({description:row.description,input});label.append(input);follow.append(label);
          });
        }
      };
      option.onchange = rebuild;
      if(item.answer)option.value=item.answer.option;
      rebuild();
      if(item.answer){notes.value=item.answer.notes;defer.value=item.answer.defer_until||'';for(const [key,value] of Object.entries(item.answer.data))if(inputs[key])inputs[key].value=key==='amount_minor'?(value/100).toFixed(2):value??'';}
      const save = el('button', 'Save answer for review'); save.type = 'submit'; save.className='primary';
      form.append(follow, noteLabel, deferLabel, save);
      form.addEventListener('input', () => {answerCommand = null;});
      form.onsubmit = e => {
        e.preventDefault(); const data = {};
        for(const [key,input] of Object.entries(inputs)) {
          let v = input.value;
          if(key.startsWith('period_'))v ||= null;
          if(key === 'amount_minor'){try{v=farmMinorAmount(v);}catch(e){status.textContent=e.message;return;}}
          if((key === 'value' && !['eggs_per_day','feed_kg_at_start','feed_kg_per_day'].includes(item.source.field))) {
            if(!/^\d+$/.test(v) || !Number.isSafeInteger(Number(v))){status.textContent='Enter a whole number in the numeric field.';return;} v=Number(v);
          }
          data[key]=v;
        }
        if(lineInputs.length){try{data.items=lineInputs.map(row=>({description:row.description,amount_minor:farmMinorAmount(row.input.value)}));}catch(e){status.textContent=e.message;return;}if(data.items.reduce((sum,row)=>sum+row.amount_minor,0)!==data.amount_minor){status.textContent='Item amounts must add up to the corrected sale total.';return;}}
        answerCommand ||= {event_id: crypto.randomUUID(), operation:'answer', item_id:item.id, expected_revision:item.revision,
          option:option.value, notes:notes.value, data, defer_until:option.value === 'unknown' ? defer.value || null : null};
        submit(save, answerCommand);
      };
      card.append(form);
      if(view.can_apply && item.state === 'AWAITING_APPROVAL') {
        card.append(el('p', 'Saved answer: '+template.options[item.answer.option]));
        for(const [key,value] of Object.entries(item.answer.data))card.append(el('p',(fields[key]||key)+': '+answerText(key,value,item.answer.data.currency)));
        card.append(el('p', 'Review the saved answer below before accepting. Recorded counts still require the separate stock-adjustment approval; price corrections retain the old sale in history.'));
        const apply=el('button','Accept saved answer');apply.type='button';
        const command={event_id:crypto.randomUUID(),operation:'apply',item_id:item.id,expected_revision:item.revision};
        apply.onclick=()=>submit(apply,command);card.append(apply);
      }
    }
    if(item.state !== 'SUPERSEDED') {
      const close=el('button','Close this question');close.type='button';
      const command={event_id:crypto.randomUUID(),operation:'supersede',item_id:item.id,expected_revision:item.revision};
      close.onclick=()=>submit(close,command);card.append(close,el('p','Closing preserves all answers and any recorded changes. It does not undo stock or money records.'));
    }
    const history=el('details');history.append(el('summary','Answer history'));
    for(const row of item.history) {
      const p=row.payload;if(p.operation!=='answer')continue;
      history.append(el('p', template.options[p.option]+' · '+farmDateText(row.received_at)));
      if(p.notes)history.append(el('p',p.notes));
      for(const [key,value] of Object.entries(p.data))history.append(el('p',(fields[key]||key)+': '+answerText(key,value,p.data.currency)));
    }
    card.append(history);list.append(card);
  }
  filter.onchange=()=>{for(const card of list.children)card.hidden=filter.value!=='All'&&filter.value!==card.dataset.topic;};
  if(!view.items.length)list.append(el('p','No questions have been added.'));
}
