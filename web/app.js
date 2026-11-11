const $ = (selector) => document.querySelector(selector);
const form = $('#search-form');
const queryInput = $('#search-input');
const results = $('#results');
const patientDialog = $('#patient-dialog');
const qualityDialog = $('#quality-dialog');
const filters = [$('#resource-type'), $('#date-from'), $('#date-to')];
let requestNumber = 0;
let currentController;
let debounce;
let detailNumber = 0;
let stats;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail : 'Please check your search and filters.';
    throw new Error(response.status === 503 ? 'The record index is not available yet. Please try again once the workspace is ready.' : detail);
  }
  return response.json();
}

function prettyDate(value) {
  if (!value) return 'Date not recorded';
  return new Intl.DateTimeFormat('en-US', {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'}).format(new Date(value.slice(0, 10) + 'T12:00:00Z'));
}

function stateMessage(title, body, isError = false) {
  const box = element('div', undefined, 'empty-state' + (isError ? ' error-state' : ''));
  box.append(element('h3', title), element('p', body));
  results.replaceChildren(box);
  $('#announcer').textContent = title + '. ' + body;
}

function renderRecords(records) {
  if (!records.length) {
    stateMessage('No records in this view', 'Try a broader date range, another record type, or reset the filters.');
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const record of records) {
    const card = $('#result-template').content.cloneNode(true);
    const badge = card.querySelector('.type-badge');
    const labels = {DocumentReference: 'DOCUMENT', DiagnosticReport: 'DIAGNOSTIC REPORT', Patient: 'PATIENT SUMMARY'};
    badge.textContent = labels[record.resource_type] || record.resource_type;
    badge.classList.toggle('diagnostic', record.resource_type === 'DiagnosticReport');
    badge.classList.toggle('patient', record.resource_type === 'Patient');
    card.querySelector('.record-date').textContent = prettyDate(record.date);
    const relevance = card.querySelector('.relevance');
    relevance.textContent = record.score === null ? '' : `${record.score.toFixed(2)} relevance`;
    relevance.title = 'Cosine similarity, from -1 to 1. Not clinical confidence.';
    card.querySelector('.record-title').textContent = record.title;
    card.querySelector('.patient-label').textContent = record.patient_name;
    card.querySelector('.patient-mrn').textContent = `MRN ${record.mrn}`;
    card.querySelector('.patient-avatar').textContent = record.patient_name.split(/\s+/).slice(0, 2).map(part => part[0]).join('');
    card.querySelector('.summary-label').textContent = record.summary_provider === 'openai' ? 'AI SUMMARY' : 'DEMO EXCERPT';
    card.querySelector('.summary-text').textContent = record.summary_snippet || 'No summary evidence available.';
    const open = card.querySelector('.record-open');
    open.setAttribute('aria-label', `Open ${record.title} for ${record.patient_name}`);
    open.addEventListener('click', () => openPatient(record.patient_id));
    fragment.append(card);
  }
  results.replaceChildren(fragment);
}

async function search() {
  clearTimeout(debounce);
  const id = ++requestNumber;
  currentController?.abort();
  currentController = new AbortController();
  const query = queryInput.value.trim();
  const [resource, from, to] = filters.map(field => field.value);
  if (from && to && from > to) {
    $('.results-section').setAttribute('aria-busy', 'false');
    $('#result-timing').textContent = '';
    stateMessage('Check the date range', 'The From date must be on or before the To date.', true);
    return;
  }
  if (query && query.length < 2) {
    $('.results-section').setAttribute('aria-busy', 'false');
    $('#result-timing').textContent = '';
    stateMessage('Keep typing', 'Enter at least two characters to search the record library.');
    return;
  }
  $('.results-section').setAttribute('aria-busy', 'true');
  $('#announcer').textContent = 'Loading records';
  results.replaceChildren(...Array.from({length: 3}, () => {
    const placeholder = element('div', undefined, 'skeleton');
    placeholder.setAttribute('aria-hidden', 'true');
    return placeholder;
  }));
  const params = new URLSearchParams();
  if (resource) params.set('resource_type', resource);
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);
  try {
    const body = await fetchJSON(`${query ? '/search' : '/records'}?${params}`, query ? {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query}), signal: currentController.signal
    } : {signal: currentController.signal});
    if (id !== requestNumber) return;
    $('#results-heading').textContent = query ? 'Search results' : 'Recently indexed';
    $('#result-description').textContent = query ? `${body.results.length} ranked matches for “${query}”` : `${body.results.length} latest records in this view`;
    $('#result-timing').textContent = query ? `${Math.round(body.elapsed_ms)} ms` : '';
    renderRecords(body.results);
    $('#announcer').textContent = `${body.results.length} ${query ? 'search matches' : 'recent records'} available`;
  } catch (error) {
    if (error.name === 'AbortError' || id !== requestNumber) return;
    $('#result-timing').textContent = '';
    $('#result-description').textContent = 'The request could not be completed';
    stateMessage('Unable to load records', error.message, true);
  } finally {
    if (id === requestNumber) $('.results-section').setAttribute('aria-busy', 'false');
  }
}

function sourceLink(resource, label = 'View source ↗') {
  const link = element('a', label);
  link.href = resource.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.setAttribute('aria-label', `${label.replace('↗', '').trim()}: ${resource.title} (opens in a new tab)`);
  return link;
}

async function openPatient(patientId) {
  const id = ++detailNumber;
  const content = $('#patient-content');
  const loading = element('h2', 'Loading patient…');
  loading.id = 'patient-name';
  content.replaceChildren(loading);
  if (!patientDialog.open) patientDialog.showModal();
  try {
    const data = await fetchJSON(`/patients/${encodeURIComponent(patientId)}`);
    if (id !== detailNumber || !patientDialog.open) return;
    const {patient, summary, resources} = data;
    const name = element('h2', patient.name?.[0]?.text || 'Unknown patient');
    name.id = 'patient-name';
    const demographics = element('p', `MRN ${patient.identifier?.[0]?.value || 'Unknown'} · ${patient.gender || 'Unknown'} · DOB ${patient.birthDate || 'Not recorded'}`, 'patient-demographics');
    const heading = element('div', undefined, 'summary-heading');
    heading.append(element('h3', summary.provider === 'openai' ? 'AI clinical summary' : 'Demo record excerpts'), element('span', `${summary.word_count} words`));
    content.replaceChildren(name, demographics, heading);
    const sections = [
      ['Chief concern', summary.chief_concern ? [summary.chief_concern] : []],
      ['Key diagnoses', summary.key_diagnoses],
      ['Recent media', summary.recent_media],
      ['Flagged anomalies', summary.flagged_anomalies],
    ];
    for (const [title, items] of sections) {
      const section = element('section', undefined, 'summary-section');
      section.append(element('h4', title));
      if (!items.length) section.append(element('p', 'Not documented in the selected evidence.', 'evidence-item'));
      for (const item of items) {
        const line = element('p', item.text, 'evidence-item');
        const source = resources.find(resource => resource.resource_type === 'DocumentReference' && resource.id === item.source_id);
        if (source) line.append(sourceLink(source));
        section.append(line);
      }
      content.append(section);
    }
    const disclaimer = element('div', undefined, 'patient-disclaimer');
    disclaimer.append(element('strong', summary.confidence), document.createTextNode(summary.disclaimer));
    content.append(disclaimer, element('h3', `Linked records · ${resources.length}`));
    const list = element('div', undefined, 'resource-links');
    for (const resource of resources) {
      const link = sourceLink(resource, resource.title);
      link.append(element('small', resource.resource_type + ' ↗'));
      list.append(link);
    }
    const bundle = element('a', 'View complete FHIR bundle ↗', 'bundle-link');
    bundle.href = `/patients/${encodeURIComponent(patientId)}/bundle`;
    bundle.target = '_blank';
    bundle.rel = 'noopener noreferrer';
    content.append(list, bundle);
  } catch (error) {
    if (id !== detailNumber || !patientDialog.open) return;
    const title = element('h2', 'Unable to load patient');
    title.id = 'patient-name';
    content.replaceChildren(title, element('p', error.message));
  }
}

form.addEventListener('submit', event => {event.preventDefault(); search();});
queryInput.addEventListener('input', () => {clearTimeout(debounce); debounce = setTimeout(search, 320);});
filters.forEach(field => field.addEventListener('change', search));
document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => {
  queryInput.value = button.dataset.query;
  search();
}));
$('#clear-button').addEventListener('click', () => {form.reset(); search(); queryInput.focus();});
$('#close-patient').addEventListener('click', () => patientDialog.close());
patientDialog.addEventListener('close', () => {detailNumber += 1;});
$('#close-quality').addEventListener('click', () => qualityDialog.close());
for (const dialog of [patientDialog, qualityDialog]) dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
});
$('#quality-button').addEventListener('click', () => {
  const content = $('#quality-content');
  content.replaceChildren();
  if (stats) {
    const list = element('dl');
    for (const [label, value] of [['Input records', stats.input_records], ['Accepted documents', stats.records], ['Quarantined records', stats.rejected_records], ['Duplicates removed', stats.duplicates], ['FHIR schema / reference errors', stats.validation_errors]]) {
      list.append(element('dt', label), element('dd', String(value)));
    }
    content.append(list, element('p', 'Conflicting identities and invalid records are held out of search. Original documents remain the source of truth. All included records are synthetic.'));
  } else content.append(element('p', 'Workspace statistics are unavailable. Please reload and try again.'));
  qualityDialog.showModal();
});

async function initialize() {
  try {
    stats = await fetchJSON('/stats');
    $('#record-count').textContent = stats.records;
    $('#patient-count').textContent = stats.patients;
    $('#resource-count').textContent = stats.fhir_resources;
    $('#index-status').textContent = stats.ready ? 'Search index ready' : 'Index not ready';
    if (stats.summary_providers.includes('demo')) $('#summary-notice p').textContent = 'Demo excerpts are selected without an LLM. Verify all information against the original documents.';
    else $('#summary-notice p').textContent = 'AI-generated summaries are not clinical decisions. Verify the original documents.';
  } catch {
    $('#index-status').textContent = 'Workspace unavailable';
  }
  await search();
}
initialize();
