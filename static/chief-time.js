'use strict';
function chiefLocalInput(value = new Date()) {
  const d = new Date(value), pad = n => String(n).padStart(2, '0');
  if (!Number.isFinite(d.getTime())) throw new Error('Enter a valid time.');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function chiefLocalISO(value) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error('Enter a complete local date and time.');
  const d = new Date(value);
  if (!Number.isFinite(d.getTime()) || chiefLocalInput(d) !== value) {
    throw new Error('That local time does not exist. Check the date or daylight-saving clock change.');
  }
  // A backward clock change can give the same wall time two different instants.
  // Require the person entering it to choose; never guess which occurrence.
  const candidates = new Map([[d.getTime(), d]]);
  for (const direction of [-1, 1]) {
    const neighbour = new Date(d.getTime()+direction*86400000);
    const alternative = new Date(d.getTime()+(neighbour.getTimezoneOffset()-d.getTimezoneOffset())*60000);
    if (chiefLocalInput(alternative) === value) candidates.set(alternative.getTime(), alternative);
  }
  const choices = [...candidates.values()].sort((a,b)=>a-b);
  if (choices.length > 1) {
    const selected = window.prompt('The clock change makes this time occur twice. Choose the occurrence:\n'+choices.map((at,i)=>`${i+1}: ${at.toISOString()} (UTC)`).join('\n'));
    if (!/^[12]$/.test(selected || '') || !choices[Number(selected)-1]) throw new Error('Choose which occurrence of this local time you mean before saving.');
    return choices[Number(selected)-1].toISOString();
  }
  return d.toISOString();
}
function chiefReportingDay(zone, value = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(value));
  const fields = Object.fromEntries(parts.map(p=>[p.type,p.value]));
  return `${fields.year}-${fields.month}-${fields.day}`;
}
