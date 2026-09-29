/* Offer Checkpost, the page.

Every change a person makes here is one POST /api/invoke: the same invoke path the planner,
the command line and an MCP client use. The page never says who it is. The server counts a
call as the person's only when it carries both halves of the session that opening the address
printed in the terminal made: the cookie the browser sends, and the key this page sends in a
header. The key arrives once, in the address's fragment, and is kept in this tab's
sessionStorage, which only this origin can read; every call without it is the agent's. After
each call the page reads GET /api/state again and redraws from it, so the screen always shows
the server's state and never the page's own guess. The page has no other way to change
anything.

An investigation is one POST that can take seconds of live searching, so while any POST is in
flight the page polls GET /api/calls, which answers without waiting for the investigation, and
the call-log strip grows as each search runs. When idle it polls the same read more slowly: a
change in the activity log it didn't make is read and drawn, and the agent's calls among it are
said once beside the log. A redraw keeps what the person is in the middle of: the
text they typed, the caret, open disclosures and scroll positions. And when the server stops
counting the page as the person (the app restarted, or the address was opened elsewhere), the
page says so and turns off what only a person can do.

A person publishes a case as they read it. The page keeps the revision of each case as the
person last read it, and only the person's own calls move it on: a revision the agent moved
clears the chosen label and says what the agent called, and the publish click sends the
revision the person read, so the server refuses a case that changed after it.

Pasted messages and search results are outside input: everything from the server is rendered
with textContent and createElement, never parsed as HTML, and a link is a link only when it is
http or https.
*/

const POLL_MS = 500;
const WATCH_MS = 2000;
const NOT_ANSWERING = 'The app is not answering. Start it again, then reload this page.';
const SIGNED_OUT = 'This page is not signed in as the person: open the newest address printed in the terminal.';
const SESSION_HEADER = 'X-Offer-Session';

const BANDS = {
  high_risk: { cls: 'red', title: 'High risk', short: 'High risk' },
  unverified: { cls: 'amber', title: 'Unverified', short: 'Unverified' },
  // The best band never calls an offer genuine: it says what the drafts say.
  consistent_with_genuine: {
    cls: 'green',
    title: 'Nothing found contradicts the offer',
    short: 'No contradictions found',
  },
};

// The Offer Board's labels, as the server's drafts word them.
const BOARD_LABELS = {
  likely_impersonation: 'Likely impersonation',
  pay_to_apply_red_flag: 'Pay-to-apply red flag',
  unverified_ask_questions: 'Unverified: ask questions first',
  no_contradictions_found: 'No contradictions found',
};
const LABEL_CLASS = {
  likely_impersonation: 'red',
  pay_to_apply_red_flag: 'red',
  unverified_ask_questions: 'amber',
  no_contradictions_found: 'green',
};

const OUTCOMES = {
  walked_away: 'Walked away',
  proceeding: 'Proceeding',
  reported_1930: 'Reported to 1930',
};

// The rule table's short labels, so an evidence card names its rule the way the drafts do.
const RULE_LABELS = {
  fee_requested: 'fee asked',
  task_scam_pattern: 'task-scam pattern',
  sensitive_docs_early: 'documents asked for before any interview',
  chat_only_interview: 'chat-only interview',
  sender_lookalike: 'look-alike sender domain',
  domain_named_in_fraud_notice: "sender domain named in the employer's fraud notice",
  sender_free_mail: 'free-mail sender',
  fee_contradicts_employer: 'employer says it charges no fee',
  employer_fraud_notice_exists: 'employer has a recruitment-fraud notice',
  no_web_footprint: 'no web footprint',
  pay_outlier: 'pay far above comparable listings',
  no_listing_match: 'no matching listing',
  office_not_found: 'office not on Maps',
  reviews_mention_fees: 'office reviews mention fees or fraud',
  impersonation_reports: "news of fake offers in the company's name",
  contact_reported: 'recruiter contact reported as a scam',
  recruiter_site_new: 'recruiter site indexed within the past year',
  sender_official: 'sender on the official domain',
  listing_match: 'listing applies on the official domain',
  office_found: 'office found on Maps',
};

const STOPPED = {
  decisive: 'the evidence was decisive',
  done: 'every check that applied has run',
  budget: "the case's search budget ran out",
  quota: 'the quota guard: too few searches are left this month',
  search_error: 'a search failed, and nothing was made up',
  no_company: 'the message names no company to check on the web',
};

const PROVIDERS = {
  live: { text: 'Live SerpApi', cls: 'live' },
  replay: { text: 'Replay: recorded responses, not live', cls: 'replay' },
  fake: { text: 'Fake fixtures (tests)', cls: 'fake' },
};

const ACTION_TAG = { skipped: 'skip', reordered: 'reorder', stopped: 'stop', reused: 'reuse' };
const ACTION_NAME = { skipped: 'Skipped', reordered: 'Reordered', stopped: 'Stopped', reused: 'Reused' };
const STATUS_WORDS = {
  open: 'not investigated',
  investigated: 'investigated',
  published: 'on the Offer Board',
  retracted: 'taken off the board',
};

const CLAIM_FIELDS = ['company', 'role', 'city', 'pay', 'fee'];
// Every claim a person can correct: text, or whole rupees (pay a month, the fee asked). An
// emptied field removes a claim the extraction got wrong.
const EDITABLE = {
  company: { label: 'Company', max: 200 },
  role: { label: 'Role', max: 200 },
  city: { label: 'City', max: 100 },
  pay: { label: 'Pay (rupees a month)', rupees: 'monthlyInr', example: '38000' },
  fee: { label: 'Fee asked (rupees)', rupees: 'amountInr', example: '2499' },
};
const HUMAN_VERBS = ['publish_verdict', 'retract_verdict', 'record_outcome', 'run_remaining_checks'];
// The agent's tools that never move a case's revision: a read, or a draft written from the case
// as it stands.
const KEEPS_REVISION = new Set([
  'open_case',
  'get_case',
  'list_cases',
  'search_budget',
  'draft_verdict',
  'draft_recruiter_reply',
  'draft_cybercrime_report',
]);

let state = null;
let samples = [];
const ui = {
  current: null,
  composing: true,
  busy: null,
  errors: new Map(),
  edits: new Map(),
  // The <details> the person opened, by key, so a redraw leaves them open.
  open: new Set(),
  publish: new Map(),
  retracting: null,
  retractReason: '',
  budget: undefined,
  progress: null,
  focusKey: null,
  revealFocus: false,
  seenRows: new Set(),
  primed: false,
  // True once the server no longer counts this page as the person.
  signedOut: false,
  // Each case's revision as the person last read it, and for a case the agent changed since,
  // the tools it called.
  read: new Map(),
  changed: new Map(),
};

// ---- small helpers ------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [key, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (key === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
    else if (key === 'value') value = v;
    else if (key.startsWith('on') && typeof v === 'function') el.addEventListener(key.slice(2), v);
    else el.setAttribute(key, v === true ? '' : String(v));
  }
  append(el, kids);
  // Set last, so a select already has the option it names.
  if (value !== undefined) el.value = value;
  return el;
}

function append(el, kids) {
  for (const kid of [kids].flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
}

function fill(el, ...kids) {
  el.replaceChildren();
  append(el, kids);
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : word.endsWith('h') ? 'es' : 's'}`;
const cap = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);
// A reason that opens with a rule or tool name keeps it as written.
const sentence = (text) => (/^\w*_/.test(text ?? '') ? text : cap(text));
const ruleLabel = (rule) => RULE_LABELS[rule] ?? String(rule).replaceAll('_', ' ');
const isLive = (s) => !s.stale && !s.superseded;

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});
const clockFmt = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  timeZone: 'Asia/Kolkata',
});
const dayFmt = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'numeric',
  year: 'numeric',
  timeZone: 'Asia/Kolkata',
});
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parsed(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}
const clock = (iso) => (parsed(iso) ? clockFmt.format(parsed(iso)) : String(iso ?? ''));
function day(iso) {
  const d = parsed(iso);
  if (!d) return String(iso ?? '');
  const part = (type) => Number(dayFmt.formatToParts(d).find((p) => p.type === type).value);
  return `${part('day')} ${MONTHS[part('month') - 1]} ${part('year')}`;
}
const stamp = (iso) => (parsed(iso) ? `${day(iso)}, ${clock(iso).slice(0, 5)} IST` : '');

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function announce(text) {
  const box = $('announce');
  box.textContent = '';
  setTimeout(() => {
    box.textContent = text;
  }, 60);
}

function reveal(id) {
  const el = $(id);
  if (el) revealEl(el, 'start');
}

/** Scrolls `el` into view when it is under the sticky header or low on the screen. */
function revealEl(el, block) {
  const { top, bottom } = el.getBoundingClientRect();
  const bar = $('top').getBoundingClientRect();
  const covered = getComputedStyle($('top')).position === 'sticky' ? bar.bottom : 0;
  const low = block === 'start' ? top > window.innerHeight * 0.6 : bottom > window.innerHeight;
  if (top < covered || low) {
    el.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block });
  }
}

// ---- talking to the server ----------------------------------------------------------------

function httpWords(status, body) {
  const said = body && typeof body.error === 'string' && status !== 500 ? ` (${body.error})` : '';
  if (status === 400) return `The app could not read that request${said}.`;
  if (status === 403) return `The app refused a request it could not tell came from this page${said}.`;
  if (status === 413) return 'That is too long for the app to take in one request.';
  if (status === 500) return 'Something went wrong inside the app. The activity log shows what ran.';
  return `The app answered HTTP ${status}${said}.`;
}

/** The session's key, from the address's fragment the first time (taken out of the address
 * bar at once) and from this tab's sessionStorage after a reload; empty when this tab was never
 * given it, and then every call is the agent's. */
function takeSessionKey() {
  const handed = /^#session=([A-Za-z0-9_-]+)$/.exec(location.hash);
  if (handed) {
    history.replaceState(null, '', location.pathname);
    try {
      sessionStorage.setItem('session', handed[1]);
    } catch {
      // Storage turned off: the key lasts until this page is reloaded.
    }
    return handed[1];
  }
  try {
    return sessionStorage.getItem('session') ?? '';
  } catch {
    return '';
  }
}

const sessionKey = takeSessionKey();

function headers(more) {
  return { Accept: 'application/json', ...more, ...(sessionKey ? { [SESSION_HEADER]: sessionKey } : {}) };
}

async function getJSON(path) {
  const res = await fetch(path, { cache: 'no-store', headers: headers() });
  if (!res.ok) throw new Error(httpWords(res.status));
  return res.json();
}

/** `value` with each lone surrogate (half of an emoji, cut when a message was copied) replaced
 * by U+FFFD. The server refuses text that isn't valid Unicode, so a pasted message with a cut
 * emoji still opens, with one replacement character where the emoji was. */
function wellFormed(value) {
  if (typeof value === 'string') return value.toWellFormed ? value.toWellFormed() : value;
  if (Array.isArray(value)) return value.map(wellFormed);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [wellFormed(k), wellFormed(v)]));
  }
  return value;
}

// POSTs sent so far: a change to the activity log that overlaps one of them is the page's own.
let sent = 0;

async function post(tool, args) {
  sent += 1;
  let res;
  try {
    res = await fetch('/api/invoke', {
      method: 'POST',
      cache: 'no-store',
      headers: headers({ 'Content-Type': 'application/json' }),
      // No actor: the session the page and the browser hold decides who is calling.
      body: JSON.stringify(wellFormed({ tool, args })),
    });
  } catch {
    return { ok: false, outcome: 'error', error: 'The app did not answer. Is it still running?' };
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (res.ok && body && typeof body.ok === 'boolean') return body;
  return { ok: false, outcome: 'error', error: httpWords(res.status, body) };
}

let inflight = 0;
let pollTimer = null;
let polling = false;

async function pollCalls() {
  if (polling) return;
  polling = true;
  try {
    const data = await getJSON('/api/calls');
    renderStrip(data.server, data.calls || [], false);
    signedIn(data.caller === 'human');
    if (ui.progress) {
      ui.progress.now = (data.calls || []).length;
      const line = $('progress');
      if (line) line.textContent = progressText();
    }
  } catch {
    // The state read that follows every call reports a server that stopped answering.
  } finally {
    polling = false;
  }
}

function setError(slotName, text) {
  ui.errors.set(slotName, { text, fresh: true });
}

function words(out) {
  const said = String(out.error ?? '');
  if (out.outcome !== 'refused') return `Error: ${said}`;
  // A schema refusal already says "... was refused: ...".
  return /\brefused\b/.test(said) ? cap(said) : `Refused: ${said}`;
}

/** One POST through invoke, polling the call log while it runs, then a fresh read of the
 * state. `onOk` runs before the redraw, so it can pick what the redraw shows. */
async function call(tool, args, slotName, onOk) {
  ui.errors.delete(slotName);
  inflight += 1;
  if (!pollTimer) pollTimer = setInterval(pollCalls, POLL_MS);
  let out;
  try {
    out = await post(tool, args);
  } finally {
    inflight -= 1;
    if (!inflight) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
  await pollCalls();
  if (out.ok) onOk?.(out.result);
  else setError(slotName, words(out));
  await refresh();
  return out;
}

/** Runs one person's action, keeping every button disabled until it has finished. */
async function act(busy, work) {
  if (ui.busy) return;
  ui.busy = busy;
  render();
  try {
    await work();
  } finally {
    ui.busy = null;
    ui.progress = null;
    render();
  }
}

// How many activity-log entries the page has taken in; null before the first read.
let seenActivity = null;

async function refresh() {
  let fresh;
  try {
    fresh = await getJSON('/api/state');
    notice(null);
  } catch {
    notice(NOT_ANSWERING);
    return;
  }
  const log = fresh.activityLog;
  // A read that left before another came back can be behind it; the log only ever grows.
  if (seenActivity !== null && log.length < seenActivity) return;
  const added = seenActivity === null ? [] : log.slice(seenActivity);
  state = fresh;
  seenActivity = log.length;
  if (ui.current && !state.cases.some((c) => c.id === ui.current)) {
    ui.current = null;
    ui.composing = true;
  }
  absorb(added);
  render();
}

/** Takes in the activity-log entries a fresh read added, whoever's call it followed. Says which
 * calls the agent made (the planner's checks inside a call are part of that call), and follows
 * each case the person has read: a revision their own calls moved is still read, and one the
 * agent moved is not. */
function absorb(entries) {
  const agent = entries.filter((e) => e.actor === 'agent' && !e.planner);
  noteAgent(agent);
  for (const c of state.cases) {
    if (!ui.read.has(c.id) || ui.read.get(c.id) === c.revision) continue;
    const onCase = agent.filter((e) => e.result === 'ok' && e.args?.case_id === c.id);
    if (!onCase.some((e) => !KEEPS_REVISION.has(e.tool))) {
      ui.read.set(c.id, c.revision);
      continue;
    }
    const names = ui.changed.get(c.id) ?? new Set();
    for (const e of onCase) names.add(e.tool);
    ui.changed.set(c.id, names);
    // A label chosen for the case as it was is not a choice for the case as it is.
    const choice = ui.publish.get(c.id);
    if (choice) Object.assign(choice, { label: '', picked: false });
  }
}

async function refreshBudget() {
  const out = await post('search_budget', {});
  ui.budget = out.ok ? out.result : { error: out.error };
  await refresh();
}

function notice(text) {
  const el = $('notice');
  el.hidden = !text;
  el.textContent = text || '';
}

let watching = false;
let composing = false;

/** The idle poll: reads the call log and the activity count, and when the activity log grew
 * without the page, reads the state and redraws. It stands aside while the person's own call
 * runs (that call reads the state when it ends) and while they compose text with an input
 * method, which a redraw would cut short. */
async function watch() {
  if (watching || inflight || ui.busy || composing) return;
  watching = true;
  const before = sent;
  try {
    const data = await getJSON('/api/calls');
    notice(null);
    renderStrip(data.server, data.calls || [], false);
    signedIn(data.caller === 'human');
    if (data.activity !== seenActivity && sent === before) await refresh();
  } catch {
    notice(NOT_ANSWERING);
  } finally {
    watching = false;
  }
}

const agentNote = h('p', { class: 'agent-note', id: 'agent-note', hidden: true });
let agentNoteTimer = 0;

/** Says, beside the activity log, which calls the agent made: `entries` are its own. */
function noteAgent(entries) {
  const names = [...new Set(entries.map((e) => `${e.tool}${e.result === 'refused' ? ' (refused)' : ''}`))];
  if (!names.length) return;
  const shown = names.length > 4 ? [...names.slice(0, 3), `${names.length - 3} more`] : names;
  agentNote.textContent = `The agent called ${listed(shown)}.`;
  agentNote.hidden = false;
  announce(agentNote.textContent);
  clearTimeout(agentNoteTimer);
  agentNoteTimer = setTimeout(() => {
    agentNote.hidden = true;
  }, 8000);
}

/** Shows or clears the banner for a page the server no longer counts as the person, and turns
 * the human-only controls off or back on. */
function signedIn(yes) {
  if (ui.signedOut === !yes) return;
  ui.signedOut = !yes;
  const banner = $('session-lost');
  banner.hidden = yes;
  banner.textContent = yes ? '' : SIGNED_OUT;
  render();
}

// ---- actions ------------------------------------------------------------------------------

function caseById(id) {
  return state?.cases.find((c) => c.id === id) ?? null;
}

function current() {
  return ui.composing ? null : caseById(ui.current);
}

function pick(id) {
  if (ui.busy) return;
  ui.current = id;
  ui.composing = false;
  ui.retracting = null;
  ui.errors.clear();
  // Switching cases shows lines that already existed; only new ones are shown arriving.
  ui.primed = false;
  render();
  ui.primed = true;
}

function newCase() {
  if (ui.busy) return;
  ui.composing = true;
  ui.errors.clear();
  render();
  $('offer-text').focus();
}

function openCase(event) {
  event.preventDefault();
  const text = $('offer-text').value;
  if (!text.trim()) return;
  act('paste', async () => {
    await call('open_case', { text }, 'paste', (opened) => {
      ui.current = opened.id;
      ui.composing = false;
      ui.focusKey = 'confirm-claims';
      ui.revealFocus = true;
      $('offer-text').value = '';
      $('sample').value = '';
      syncPaste();
    });
  });
}

/** A claim's current value as its correction field shows it. */
function claimValue(c, field) {
  const claim = c.claims[field];
  if (!claim) return null;
  return EDITABLE[field].rupees ? claim[EDITABLE[field].rupees] : claim.value;
}

/** The corrections typed so far that differ from the claims, as update_claims takes them, or
 * `{ error }` when a rupee field isn't a whole number. */
function changedFields(c) {
  const fields = {};
  for (const [field, spec] of Object.entries(EDITABLE)) {
    const key = `${c.id}:${field}`;
    if (!ui.edits.has(key)) continue;
    const text = ui.edits.get(key).trim();
    let next = text === '' ? null : text;
    if (spec.rupees && next !== null) {
      const digits = next.replace(/[₹,\s]|^rs\.?/gi, '');
      if (!/^[1-9]\d{0,9}$/.test(digits)) {
        return { error: `${spec.label} must be a whole number of rupees, like ${spec.example}; empty it to remove the claim.` };
      }
      next = Number(digits);
    }
    if (next !== claimValue(c, field)) fields[field] = next;
  }
  return { fields };
}

function forgetEdits(c) {
  for (const field of Object.keys(EDITABLE)) ui.edits.delete(`${c.id}:${field}`);
}

function saveFixes(c) {
  const { fields, error } = changedFields(c);
  if (error || !Object.keys(fields).length) {
    setError('claims', error ?? 'Nothing to save: change a claim first.');
    render();
    return;
  }
  act('claims-save', async () => {
    await call('update_claims', { case_id: c.id, fields }, 'claims', () => forgetEdits(c));
  });
}

function confirmClaims(c) {
  const { fields, error } = changedFields(c);
  if (error) {
    setError('claims', error);
    render();
    return;
  }
  const args = { case_id: c.id, confirm: true };
  if (Object.keys(fields).length) args.fields = fields;
  act('claims-confirm', async () => {
    await call('update_claims', args, 'claims', () => {
      forgetEdits(c);
      ui.focusKey = 'investigate';
      // On a narrow screen the case card sits below the claims: bring the next step into view.
      ui.revealFocus = true;
    });
  });
}

function investigate(id) {
  act('investigate', async () => {
    ui.progress = { start: state.calls.length, now: state.calls.length };
    const out = await call('investigate', { case_id: id }, 'investigate');
    ui.progress = null;
    if (!out.ok) return;
    reveal('trace');
    await call('draft_verdict', { case_id: id }, 'investigate');
    await refreshBudget();
  });
}

function draftVerdict(id) {
  act('redraft', async () => {
    await call('draft_verdict', { case_id: id }, 'investigate');
  });
}

function runRemaining(id) {
  act('remaining', async () => {
    ui.progress = { start: state.calls.length, now: state.calls.length };
    const out = await call('run_remaining_checks', { case_id: id }, 'remaining');
    ui.progress = null;
    if (!out.ok) return;
    await call('draft_verdict', { case_id: id }, 'remaining');
    await refreshBudget();
  });
}

function writeDraft(id, tool, target) {
  act(tool, async () => {
    const out = await call(tool, { case_id: id }, 'drafts');
    if (out.ok) reveal(target);
  });
}

function publish(c) {
  const choice = ui.publish.get(c.id);
  if (!choice?.label) {
    setError('publish', 'Choose a label first: it is what students see at the top of the post.');
    render();
    return;
  }
  act('publish', async () => {
    // The revision the person read: the server refuses the click if the case changed since.
    const args = { case_id: c.id, label: choice.label, note: choice.note, revision: ui.read.get(c.id) };
    const out = await call('publish_verdict', args, 'publish', () => {
      ui.publish.delete(c.id);
      ui.focusKey = `wa-${c.id}`;
    });
    if (out.ok) reveal(`post-${c.id}`);
  });
}

function startRetract(caseId) {
  ui.retracting = caseId;
  ui.retractReason = '';
  ui.errors.delete(`retract:${caseId}`);
  render();
  $('retract-reason')?.focus();
}

function retract(caseId) {
  const reason = ui.retractReason.trim();
  if (!reason) {
    setError(`retract:${caseId}`, 'Give a reason: the activity log keeps it.');
    render();
    return;
  }
  act('retract', async () => {
    await call('retract_verdict', { case_id: caseId, reason }, `retract:${caseId}`, () => {
      ui.retracting = null;
      ui.retractReason = '';
    });
  });
}

function recordOutcome(id, outcome) {
  act('outcome', async () => {
    await call('record_outcome', { case_id: id, outcome }, 'outcome');
  });
}

async function copyText(text, button) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch {
    const scratch = h('textarea', { class: 'sr-only', readonly: true, 'aria-hidden': 'true' });
    scratch.value = text;
    document.body.append(scratch);
    scratch.select();
    try {
      copied = document.execCommand('copy');
    } catch {
      copied = false;
    }
    scratch.remove();
  }
  const label = button.textContent;
  button.textContent = copied ? 'Copied' : 'Copy failed: select the text instead';
  announce(copied ? 'Copied to the clipboard.' : 'Copying failed. Select the text and copy it.');
  setTimeout(() => {
    button.textContent = label;
  }, 1600);
}

// ---- reading a case -----------------------------------------------------------------------

function confirmed(c) {
  return CLAIM_FIELDS.every((field) => !c.claims[field] || c.claims[field].confirmed);
}

/** True when a person, not the agent, confirmed every claim the case has. */
function confirmedByPerson(c) {
  return CLAIM_FIELDS.every((field) => !c.claims[field] || c.claims[field].confirmedBy === 'human');
}

/** The latest investigation's lines, from its step 0 on, and the lines before them. */
function splitTrace(c) {
  const trace = c.trace || [];
  let start = -1;
  trace.forEach((line, i) => {
    if (line.tool === 'text_rules') start = i;
  });
  return start < 0
    ? { earlier: [], lines: trace }
    : { earlier: trace.slice(0, start), lines: trace.slice(start) };
}

function lastBand(c) {
  const trace = c.trace || [];
  return trace.length ? trace[trace.length - 1].band : null;
}

/** True when a claim was corrected after the latest investigation: a signal it found is stale
 * now, so the band its trace ended on no longer stands. */
function traceOutdated(c) {
  const byId = new Map(c.signals.map((s) => [s.id, s]));
  return splitTrace(c).lines.some((line) => (line.signalsAdded || []).some((id) => byId.get(id)?.stale));
}

/** The band the latest investigation's trace ended on, while its findings still stand. */
function tracedBand(c) {
  return traceOutdated(c) ? null : lastBand(c);
}

/** True when a draft (the verdict, the reply or the 1930 summary) no longer says what the case
 * says: it was written from an earlier revision of the case (before a correction or a check),
 * or it cites a signal that has since been set aside. */
function outdated(c, draft) {
  if (!draft) return false;
  if (draft.revision !== c.revision) return true;
  const byId = new Map(c.signals.map((s) => [s.id, s]));
  return (draft.evidenceIds || []).some((id) => !byId.has(id) || !isLive(byId.get(id)));
}

const draftOutdated = (c) => outdated(c, c.draftVerdict);

/** True when the case has a draft verdict that says what the case says now: the one a person
 * can publish. */
function draftCurrent(c) {
  const draft = c.draftVerdict;
  const traced = tracedBand(c);
  return !!draft && !draftOutdated(c) && (!traced || traced === draft.band);
}

function bandOf(c) {
  return tracedBand(c) ?? (draftOutdated(c) ? null : c.draftVerdict?.band) ?? null;
}

function stepLine(c, signalId) {
  const trace = c.trace || [];
  for (let i = trace.length - 1; i >= 0; i -= 1) {
    const line = trace[i];
    if (line.step != null && (line.signalsAdded || []).includes(signalId)) return line;
  }
  return null;
}

function verdictView(c) {
  const traced = tracedBand(c);
  const draft = c.draftVerdict;
  const headline = (d) => d.summary.split('\n')[0];
  if (draftCurrent(c)) {
    return { band: draft.band, kicker: 'Draft verdict', line: headline(draft), draft };
  }
  if (draft && !traced) {
    return {
      band: null,
      kicker: 'Claims corrected since this draft',
      line: 'A claim was corrected after this draft, so it no longer says what the case says. Draft the verdict again, or confirm the claims and investigate again.',
      redraft: true,
    };
  }
  if (!traced && traceOutdated(c)) {
    return {
      band: null,
      kicker: 'Claims corrected since the checks',
      line: 'A claim the checks relied on was corrected, so what they found is set aside. Draft the verdict to see what still stands, or confirm the claims and investigate again.',
      redraft: true,
    };
  }
  if (!traced) {
    return {
      band: 'unverified',
      kicker: 'Before any search',
      line: 'The message alone proves nothing: until a search checks a claim, the verdict stays unverified.',
    };
  }
  return {
    band: traced,
    kicker: draft ? 'Band after the latest checks' : 'Band so far',
    line: draft
      ? 'The case changed since the last draft: a claim was corrected, or a check ran. Draft the verdict again to see the evidence behind this band.'
      : 'Draft the verdict to see the evidence behind this band.',
    redraft: true,
  };
}

function stopWords(c) {
  const why = c.budget?.stoppedBecause;
  if (why === 'same_as') return `same message as ${c.sameAs ?? 'an earlier case'}: its checks were reused`;
  return STOPPED[why] ?? String(why);
}

function caseTitle(c) {
  const company = c.claims.company?.value;
  const role = c.claims.role?.value;
  if (company && role) return `${company} · ${role}`;
  return company || role || 'No company named';
}

function parseSummary(summary) {
  const lines = String(summary || '').split('\n');
  const out = { also: [], notCounted: [] };
  let section = null;
  for (const line of lines.slice(1)) {
    if (line === 'Evidence:') section = 'evidence';
    else if (line === 'Also found:') section = 'also';
    else if (line.startsWith('- ')) {
      if (section === 'also') out.also.push(line.slice(2));
    } else if (line.startsWith('Not counted: ')) out.notCounted.push(line.slice(13));
  }
  return out;
}

/** The signal an "Also found" line of the draft names: same rule label, and the same step (or
 * the message itself). */
function matchAlso(c, text, cited) {
  const m = /^(.+?) \((the message|step (\d+), [^)]*)\): /.exec(text);
  if (!m) return null;
  const step = m[3] == null ? null : Number(m[3]);
  return (
    c.signals.find(
      (s) =>
        isLive(s) &&
        !cited.has(s.id) &&
        ruleLabel(s.rule) === m[1] &&
        (step == null ? s.source === 'text' : stepLine(c, s.id)?.step === step),
    ) ?? null
  );
}

// ---- rendering: pieces --------------------------------------------------------------------

function slot(name) {
  const err = ui.errors.get(name);
  if (!err) return null;
  return h('p', { class: 'slot-msg', role: err.fresh ? 'alert' : null }, err.text);
}

function bandPill(band) {
  if (!band) return h('span', { class: 'pill none' }, 'Not checked');
  const b = BANDS[band] ?? { cls: '', short: band };
  return h('span', { class: ['pill', b.cls] }, b.short);
}

function actorPill(actor) {
  const name = typeof actor === 'string' ? actor : JSON.stringify(actor);
  const cls = actor === 'human' ? 'human' : actor === 'agent' ? 'agent' : 'other';
  return h('span', { class: ['actor', cls], title: cls === 'human' ? "a person's click" : null }, name);
}

function gradeTag(signal) {
  if (signal.direction === 'green') return h('span', { class: 'grade green' }, 'consistent');
  return h('span', { class: ['grade', 'red', signal.severity] }, `red flag · ${signal.severity}`);
}

function safeLink(url) {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return null;
  return h(
    'a',
    { href: url, target: '_blank', rel: 'noopener noreferrer nofollow', class: 'src' },
    url.replace(/^https?:\/\//i, ''),
  );
}

/** A <details> that stays open or closed across redraws, as the person left it. */
function disclosure(key, props, ...kids) {
  return h(
    'details',
    {
      ...props,
      open: ui.open.has(key),
      ontoggle: (e) => (e.target.open ? ui.open.add(key) : ui.open.delete(key)),
    },
    ...kids,
  );
}

function busyLabel(key, idle, working) {
  return ui.busy === key ? working : idle;
}

function progressText() {
  const p = ui.progress;
  const n = p ? Math.max(0, p.now - p.start) : 0;
  const said = n ? `Searching: ${plural(n, 'SerpApi call')} so far` : 'Searching…';
  // A live site: search took over a minute on 29 Sep 2026; the page should not look stuck.
  return state.server?.provider === 'live' ? `${said} (one live search can take a minute)` : said;
}

// ---- rendering: header and the call-log strip ---------------------------------------------

/** "28 Sep 2026" from "2026-09-28". */
function isoDay(text) {
  const [y, m, d] = String(text).split('-').map(Number);
  return y && m && d ? `${d} ${MONTHS[m - 1]} ${y}` : String(text);
}

function listed(items) {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** The replay banner: when the replayed responses were recorded, since they are not today's. */
function renderReplay(server) {
  const banner = $('replay');
  const recorded = Array.isArray(server.recorded) ? server.recorded.map(isoDay) : [];
  banner.hidden = server.provider !== 'replay';
  banner.textContent = recorded.length
    ? `Replaying SerpApi responses recorded on ${listed(recorded)}, not live. A search that was never recorded fails and says so; nothing is made up.`
    : 'Replay mode, and nothing is recorded yet: every search fails and says so, and nothing is made up. Start the app with a SerpApi key for live searches, or with the synthetic test data (--provider fake) to see the flow.';
  return recorded;
}

function renderHeader() {
  const server = state.server || {};
  $('bind').textContent = server.bind ?? 'not bound yet';
  const provider = PROVIDERS[server.provider] ?? { text: String(server.provider ?? '…'), cls: '' };
  const recorded = renderReplay(server);
  const badge = $('provider');
  badge.textContent =
    server.provider === 'replay'
      ? recorded.length
        ? `Replay: recorded ${recorded[recorded.length - 1]}, not live`
        : 'Replay: nothing recorded yet'
      : provider.text;
  badge.className = ['badge', 'provider', provider.cls].filter(Boolean).join(' ');
  badge.title = `SerpApi key: ${server.key === 'set' ? 'set (its value is never shown)' : 'missing'}`;

  const budget = $('budget');
  const account = ui.budget?.account;
  if (account && Number.isFinite(account.plan_searches_left)) {
    budget.hidden = false;
    budget.className = 'badge budget';
    budget.textContent = `${account.plan_searches_left.toLocaleString('en-IN')} searches left this month`;
    budget.title =
      `SerpApi Account API: ${account.this_month_usage} used of ${account.searches_per_month} ` +
      `this month, ${account.this_hour_searches} this hour`;
  } else if (ui.budget?.error) {
    budget.hidden = false;
    budget.className = 'badge budget unknown';
    budget.textContent = 'Searches left: unavailable';
    budget.title = ui.budget.error;
  } else {
    // Replay and a provider with no Account API have nothing to count.
    budget.hidden = true;
  }
}

let stripShown = 0;

function callLine(entry, index, fresh) {
  const cache = entry.cache === 'replay' ? 'recorded' : `cache ${entry.cache}`;
  return h(
    'li',
    { class: ['call', fresh && 'fresh'] },
    h('span', { class: 'call-n' }, `#${index + 1}`.padEnd(4, ' ')),
    h('span', { class: 'call-engine' }, String(entry.engine).padEnd(11, ' ')),
    h('span', { class: 'call-rest' }, ` · ${String(entry.provider).padEnd(6, ' ')} · ${cache.padEnd(10, ' ')} · `),
    h('span', { class: 'call-ms' }, `${String(entry.ms ?? '?').padStart(5, ' ')} ms`),
  );
}

function renderStrip(server, calls, authoritative) {
  $('strip-bind').textContent = server?.bind ?? 'not bound yet';
  const list = $('calls');
  if (calls.length < stripShown) {
    // A poll that left before the latest state read can be behind it; only the state resets.
    if (!authoritative) return;
    stripShown = 0;
  }
  list.classList.toggle('idle', !calls.length);
  if (!calls.length) {
    fill(list, h('li', { class: 'call idle' }, 'No SerpApi calls yet. Each search appears here as it runs.'));
    stripShown = 0;
    $('strip-count').textContent = '';
    return;
  }
  if (stripShown === 0) list.replaceChildren();
  for (let i = stripShown; i < calls.length; i += 1) list.append(callLine(calls[i], i, ui.primed));
  stripShown = calls.length;
  fitStrip();
}

const STRIP_ROWS = 3;

/** Shows the newest calls in whole columns of three, hiding the oldest columns that don't fit
 * and saying so, so no column is ever cut in half at the strip's edge. */
function fitStrip() {
  const list = $('calls');
  if (list.classList.contains('idle')) return;
  const items = [...list.children];
  for (const item of items) item.hidden = false;
  let hidden = 0;
  while (list.scrollWidth > list.clientWidth && items.length - hidden > STRIP_ROWS) {
    for (const item of items.slice(hidden, hidden + STRIP_ROWS)) item.hidden = true;
    hidden += STRIP_ROWS;
  }
  $('strip-count').textContent =
    plural(items.length, 'call') + (hidden ? ` · the latest ${items.length - hidden} shown` : '');
  // Narrower than one column, the newest column scrolls into view instead.
  list.scrollLeft = list.scrollWidth;
  fadeStrip();
}

function fadeStrip() {
  const list = $('calls');
  list.classList.toggle('scrolled', list.scrollLeft > 0);
}

// ---- rendering: cases, paste, claims ------------------------------------------------------

function renderCaseBar() {
  const bar = $('casebar');
  bar.hidden = !state.cases.length;
  if (!state.cases.length) {
    bar.replaceChildren();
    return;
  }
  fill(
    bar,
    h('h2', { class: 'casebar-title', id: 'casebar-h' }, 'Cases'),
    h(
      'ol',
      { class: 'casebar-list' },
      state.cases.map((c) =>
        h(
          'li',
          {},
          h(
            'button',
            {
              type: 'button',
              class: 'case-tab',
              'aria-current': !ui.composing && c.id === ui.current ? 'true' : null,
              'data-focus': `case-${c.id}`,
              disabled: !!ui.busy,
              title: `${c.id}: ${STATUS_WORDS[c.status] ?? c.status}`,
              onclick: () => pick(c.id),
            },
            h('span', { class: 'case-id' }, c.id),
            h('span', { class: 'case-what' }, caseTitle(c)),
            bandPill(bandOf(c)),
          ),
        ),
      ),
    ),
    h(
      'button',
      {
        type: 'button',
        class: 'btn small',
        'data-focus': 'new-case',
        disabled: ui.composing || !!ui.busy,
        onclick: newCase,
      },
      'New case',
    ),
  );
  // The list is drawn anew each time, scrolled to its start; on a narrow screen the current
  // case can sit past its edge.
  const list = bar.querySelector('.casebar-list');
  const tab = bar.querySelector('.case-tab[aria-current="true"]');
  if (tab && list.scrollWidth > list.clientWidth) {
    const t = tab.getBoundingClientRect();
    const l = list.getBoundingClientRect();
    if (t.left < l.left || t.right > l.right) list.scrollLeft += t.right - l.right + 8;
  }
}

function syncPaste() {
  const text = $('offer-text').value;
  $('offer-count').textContent = `${Array.from(text).length.toLocaleString('en-IN')} / 20,000`;
  const button = $('open-case');
  button.disabled = !!ui.busy || !text.trim();
  button.textContent = busyLabel('paste', 'Open case', 'Opening…');
}

function highlighted(c) {
  const chars = Array.from(c.sourceText);
  const clamp = (n) => Math.max(0, Math.min(chars.length, Number(n) || 0));
  const spans = [];
  const claim = (key, span) => {
    if (Array.isArray(span)) spans.push({ start: clamp(span[0]), end: clamp(span[1]), key, flag: false });
  };
  for (const field of CLAIM_FIELDS) claim(field, c.claims[field]?.span);
  (c.claims.contacts || []).forEach((x, i) => claim(`contact${i}`, x.span));
  (c.claims.links || []).forEach((x, i) => claim(`link${i}`, x.span));
  for (const s of c.signals) {
    const span = s.source === 'text' && isLive(s) ? s.evidence?.span : null;
    if (Array.isArray(span)) spans.push({ start: clamp(span[0]), end: clamp(span[1]), key: s.id, flag: true });
  }
  const cuts = [...new Set([0, chars.length, ...spans.flatMap((s) => [s.start, s.end])])].sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < cuts.length - 1; i += 1) {
    const [a, b] = [cuts[i], cuts[i + 1]];
    const text = chars.slice(a, b).join('');
    const over = spans.filter((s) => s.start <= a && s.end >= b && s.start < s.end);
    if (!over.length) {
      out.push(text);
      continue;
    }
    const keys = over.filter((s) => !s.flag).map((s) => s.key);
    const flagged = over.some((s) => s.flag);
    out.push(
      h('mark', { class: [keys.length && 'claim', flagged && 'flag'], 'data-k': keys.join(' ') || null }, text),
    );
  }
  return out;
}

function light(key, scroll) {
  for (const el of document.querySelectorAll('.lit')) el.classList.remove('lit');
  if (!key) return;
  const marks = [...document.querySelectorAll('#message mark[data-k]')].filter((m) =>
    m.dataset.k.split(' ').includes(key),
  );
  for (const m of marks) m.classList.add('lit');
  for (const chip of document.querySelectorAll(`.chip[data-k="${CSS.escape(key)}"]`)) chip.classList.add('lit');
  if (scroll && marks[0]) {
    marks[0].scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
  }
}

function chip(key, kind, value, tags) {
  return h(
    'li',
    {},
    h(
      'button',
      {
        type: 'button',
        class: 'chip',
        'data-k': key,
        'data-focus': `chip-${key}`,
        title: 'Show where this came from in the message',
        onmouseenter: () => light(key),
        onmouseleave: () => light(null),
        onfocus: () => light(key),
        onblur: () => light(null),
        onclick: () => light(key, true),
      },
      h('span', { class: 'k' }, kind),
      h('span', { class: 'v' }, value),
      tags.filter(Boolean).map((t) => h('span', { class: ['t', t.cls] }, t.text)),
    ),
  );
}

function claimChips(c) {
  const cl = c.claims;
  const out = [];
  const marks = (x) => [
    x.corrected ? { text: 'corrected', cls: 'corrected' } : null,
    x.confirmed && x.confirmedBy === 'human' ? { text: '✓ confirmed', cls: 'ok' } : null,
    x.confirmed && x.confirmedBy !== 'human' ? { text: 'confirmed by the agent', cls: 'note' } : null,
  ];
  for (const field of ['company', 'role', 'city']) {
    if (cl[field]) out.push(chip(field, field, cl[field].value, marks(cl[field])));
  }
  if (cl.pay) {
    const digits = cl.pay.raw.replace(/\D/g, '');
    const normal =
      cl.pay.monthlyInr != null && !digits.includes(String(Math.round(cl.pay.monthlyInr)))
        ? { text: `${inr.format(cl.pay.monthlyInr)} a month`, cls: 'note' }
        : null;
    out.push(chip('pay', 'pay', cl.pay.raw, [normal, ...marks(cl.pay)]));
  }
  if (cl.fee) {
    const amount = cl.fee.amountInr == null ? { text: 'no amount named', cls: 'note' } : null;
    out.push(chip('fee', 'fee asked', cl.fee.raw, [amount, ...marks(cl.fee)]));
  }
  (cl.contacts || []).forEach((x, i) =>
    out.push(chip(`contact${i}`, x.kind, x.value, [x.synthetic ? { text: 'placeholder, never searched', cls: 'note' } : null])),
  );
  (cl.links || []).forEach((x, i) =>
    out.push(chip(`link${i}`, 'link', x.value, [x.synthetic ? { text: 'placeholder', cls: 'note' } : null])),
  );
  return out.length ? out : [h('li', { class: 'none' }, 'No claims were found in the message.')];
}

function corrections(c) {
  const inputs = Object.entries(EDITABLE).map(([field, spec]) => {
    const key = `${c.id}:${field}`;
    const id = `fix-${field}`;
    // A fee asked without an amount is still a claim: the field is empty, and says so.
    const asked = field === 'fee' && c.claims.fee && c.claims.fee.amountInr == null;
    return h(
      'div',
      { class: ['field', spec.rupees && 'rupees'] },
      h('label', { for: id }, spec.label),
      h('input', {
        id,
        type: 'text',
        inputmode: spec.rupees ? 'numeric' : null,
        maxlength: spec.max ?? 16,
        autocomplete: 'off',
        spellcheck: 'false',
        placeholder: asked ? 'Asked, no amount named' : 'Not in the message',
        'data-focus': id,
        value: ui.edits.get(key) ?? String(claimValue(c, field) ?? ''),
        oninput: (e) => ui.edits.set(key, e.target.value),
      }),
    );
  });
  return disclosure(
    `fix:${c.id}`,
    { class: 'fix' },
    h('summary', {}, 'Correct a claim: company, role, city, pay or fee'),
    h(
      'div',
      { class: 'fix-body' },
      h('div', { class: 'fix-fields' }, inputs),
      h(
        'p',
        { class: 'fine' },
        'Empty a field to remove a claim the extraction got wrong, such as a fee the message says it never asks. Signals that relied on a corrected claim are set aside as stale, and the claim needs confirming again.',
      ),
      h(
        'button',
        { type: 'button', class: 'btn', 'data-focus': 'save-fixes', disabled: !!ui.busy, onclick: () => saveFixes(c) },
        busyLabel('claims-save', 'Save corrections', 'Saving…'),
      ),
    ),
  );
}

/** A case whose verdict is on the Offer Board stays as it was published until it is retracted. */
const onBoard = (c) => !!c.publishedVerdict;
const ON_BOARD_HINT = 'Its verdict is on the Offer Board, so the case stays as published. Retract it from the board to correct a claim or investigate again.';

function cardClaims(c) {
  const ok = confirmed(c);
  const byPerson = ok && confirmedByPerson(c);
  return h(
    'section',
    { class: 'card claims', 'aria-labelledby': 'claims-h' },
    h(
      'div',
      { class: 'card-head' },
      h('h2', { id: 'claims-h' }, 'Claims in the message'),
      h(
        'span',
        { class: ['state', byPerson && 'ok'] },
        byPerson ? 'Confirmed' : ok ? 'Confirmed by the agent' : 'Not confirmed yet',
      ),
    ),
    h(
      'p',
      { class: 'sub' },
      byPerson
        ? 'A person checked these against the message. Correcting one makes it unconfirmed again.'
        : ok
          ? 'The agent confirmed the claims marked so, not a person. Check them against the highlighted text before you rely on them, then confirm them yourself.'
          : 'Check each claim against the highlighted text, correct any that are wrong, then confirm. Nothing is searched until the claims are confirmed.',
    ),
    h('ul', { class: 'chips' }, claimChips(c)),
    onBoard(c) ? h('p', { class: 'fine frozen' }, ON_BOARD_HINT) : corrections(c),
    byPerson || onBoard(c)
      ? null
      : h(
          'div',
          { class: 'actions' },
          h(
            'button',
            {
              type: 'button',
              class: 'btn primary',
              'data-focus': 'confirm-claims',
              disabled: !!ui.busy,
              onclick: () => confirmClaims(c),
            },
            busyLabel('claims-confirm', 'Confirm claims', 'Confirming…'),
          ),
        ),
    slot('claims'),
  );
}

function cardMessage(c) {
  return h(
    'section',
    { class: 'card message-card', 'aria-labelledby': 'msg-h' },
    h('h2', { id: 'msg-h' }, 'The message'),
    h(
      'p',
      { class: 'legend' },
      h('mark', { class: 'claim' }, 'Highlighted'),
      ': where a claim came from. ',
      h('mark', { class: 'flag' }, 'Underlined'),
      ': what a text rule flagged.',
    ),
    h(
      'pre',
      { class: 'message', id: 'message', tabindex: '0', 'aria-label': 'The message as pasted', 'data-scroll': `message:${c.id}` },
      highlighted(c),
    ),
  );
}

function cardFlags(c) {
  const flags = c.signals.filter((s) => s.source === 'text' && !s.superseded);
  return h(
    'section',
    { class: 'card flags-card', 'aria-labelledby': 'flags-h' },
    h('h2', { id: 'flags-h' }, 'Red flags in the message itself'),
    h(
      'p',
      { class: 'proves-nothing' },
      h('strong', {}, 'The message alone proves nothing. '),
      'On their own these leave the verdict unverified: only a search result can move it.',
    ),
    flags.length
      ? h(
          'ul',
          { class: 'flags' },
          flags.map((s) =>
            h(
              'li',
              { class: ['flag-item', s.stale && 'stale'] },
              h(
                'div',
                { class: 'flag-head' },
                h('span', { class: 'flag-name' }, cap(ruleLabel(s.rule))),
                gradeTag(s),
                s.stale ? h('span', { class: 'tag' }, 'stale') : null,
              ),
              h('code', { class: 'rule-id' }, s.rule),
              s.evidence?.quote ? h('blockquote', {}, s.evidence.quote) : null,
            ),
          ),
        )
      : h('p', { class: 'fine' }, 'No text rule fired.'),
  );
}

function renderSide() {
  const c = current();
  $('paste').hidden = !ui.composing;
  syncPaste();
  fill($('paste-slot'), slot('paste'));
  fill($('claims'), c ? [cardClaims(c), cardMessage(c), cardFlags(c)] : null);
}

// ---- rendering: the case ------------------------------------------------------------------

const verdictBox = h('div', { id: 'verdict', class: 'banner', 'aria-live': 'polite', 'aria-atomic': 'true' });
let verdictKey = '';

function updateVerdict(c) {
  const v = verdictView(c);
  const band = v.band ? BANDS[v.band] ?? { cls: '', title: v.band } : { cls: '', title: 'Draft out of date' };
  const key = JSON.stringify([c.id, v.band, v.kicker, v.line, v.draft?.draftedAt]);
  if (key === verdictKey) return v;
  verdictKey = key;
  verdictBox.className = `banner ${band.cls}`;
  fill(
    verdictBox,
    h('p', { class: 'banner-kicker' }, v.kicker),
    h('p', { class: 'band-title' }, band.title),
    h('p', { class: 'banner-line' }, sentence(v.line)),
    v.band === 'consistent_with_genuine'
      ? h('p', { class: 'banner-caveat' }, 'That is not proof it is real: no check found anything against it.')
      : null,
    v.draft
      ? h('p', { class: 'banner-meta' }, `Drafted ${stamp(v.draft.draftedAt)}. A draft: only a person publishes.`)
      : null,
  );
  return v;
}

function cardInvestigation(c) {
  const v = updateVerdict(c);
  const ok = confirmed(c);
  const ran = (c.trace || []).length > 0;
  const hint = onBoard(c)
    ? ON_BOARD_HINT
    : !ok
    ? 'Confirm the claims first: nothing is searched until they are confirmed.'
    : ran
      ? 'Investigating again starts afresh, with a fresh budget; earlier findings are set aside.'
      : `Searches only what earlier results make worthwhile: at most ${plural(c.budget?.maxSearches ?? 6, 'search')} for this case.`;
  return h(
    'section',
    { class: 'card investigation', 'aria-labelledby': 'case-h' },
    h(
      'div',
      { class: 'case-head' },
      h('h2', { id: 'case-h' }, caseTitle(c)),
      h(
        'p',
        { class: 'case-meta' },
        h('span', { class: 'case-id' }, c.id),
        ` · opened ${stamp(c.createdAt)} · ${STATUS_WORDS[c.status] ?? c.status}`,
        c.sameAs ? ` · same message as ${c.sameAs}` : null,
      ),
    ),
    verdictBox,
    h(
      'div',
      { class: 'investigate-row' },
      h(
        'button',
        {
          type: 'button',
          class: ['btn', 'big', !ran && 'primary'],
          'data-focus': 'investigate',
          disabled: !ok || onBoard(c) || !!ui.busy,
          'aria-describedby': 'investigate-hint',
          onclick: () => investigate(c.id),
        },
        busyLabel('investigate', ran ? 'Investigate again' : 'Investigate', 'Investigating…'),
      ),
      v.redraft
        ? h(
            'button',
            { type: 'button', class: 'btn', 'data-focus': 'redraft', disabled: !!ui.busy, onclick: () => draftVerdict(c.id) },
            busyLabel('redraft', 'Draft the verdict', 'Drafting…'),
          )
        : null,
      h('p', { class: 'hint', id: 'investigate-hint' }, hint),
    ),
    ui.busy === 'investigate' ? h('p', { class: 'progress', id: 'progress' }, progressText()) : null,
    slot('investigate'),
  );
}

function budgetLine(c) {
  const b = c.budget;
  if (!b || (b.stoppedBecause == null && !b.spent)) return null;
  const parts = [`${plural(b.spent, 'search')} spent of ${b.maxSearches}`];
  if (b.saved) parts.push(`${b.saved} not spent`);
  if (b.stoppedBecause) parts.push(`stopped: ${stopWords(c)}`);
  return h('p', { class: 'budget-line' }, parts.join(' · '));
}

function traceRow(c, line, sig, index, animate) {
  const numbered = line.step != null;
  const failed = line.action === 'failed';
  const searched = !!line.engine && line.cache != null;
  const tag = numbered ? String(line.step) : ACTION_TAG[line.action] ?? line.action;
  const name = numbered ? `Step ${line.step}${failed ? ', failed' : ''}` : ACTION_NAME[line.action] ?? line.action;
  const meta = [
    h('code', { class: 'tool' }, line.tool),
    line.engine ? h('code', { class: 'engine' }, line.engine) : null,
    searched ? h('span', {}, line.provider) : null,
    searched ? h('span', {}, line.cache === 'replay' ? 'recorded' : `cache ${line.cache}`) : null,
    searched && line.ms != null ? h('span', { class: 'num' }, `${line.ms} ms`) : null,
    numbered && line.step === 0 ? h('span', {}, 'no search') : null,
    numbered && line.step > 0 && !failed
      ? h('span', {}, `${plural(line.searchesSpent ?? 0, 'search')} spent${line.reusedFrom ? ` by ${line.reusedFrom}` : ''}`)
      : null,
    line.searchesSaved && !numbered ? h('span', {}, `${line.searchesSaved} not spent`) : null,
    h('span', {}, actorPill(line.actor)),
    line.reusedFrom ? h('span', {}, `reused from ${line.reusedFrom}`) : null,
  ];
  const signals = (line.signalsAdded || []).map((id) => sig.get(id)).filter(Boolean);
  const showBand = ['ran', 'failed', 'stopped', 'reused'].includes(line.action) && line.band;
  const query = searched && typeof line.params?.q === 'string' ? line.params.q : null;
  const key = `${c.id}:${index}:${line.tool}:${line.action}`;
  const fresh = animate && !ui.seenRows.has(key);
  ui.seenRows.add(key);
  return h(
    'li',
    { class: ['trace-row', `act-${line.action}`, fresh && 'enter', fresh && `d${Math.min(index, 12)}`] },
    h('div', { class: 'gutter' }, h('span', { class: 'sr-only' }, `${name}: `), h('span', { class: 'tag', 'aria-hidden': 'true' }, tag)),
    h(
      'div',
      { class: 'row-body' },
      h('p', { class: 'because' }, sentence(line.because)),
      h('p', { class: 'meta' }, meta),
      query ? h('p', { class: 'query' }, h('span', { class: 'q' }, 'q'), ' ', query) : null,
      line.note ? h('p', { class: ['note', failed && 'failed'] }, h('strong', {}, failed ? 'Failed: ' : 'Found: '), line.note) : null,
      signals.length
        ? h(
            'ul',
            { class: 'fired', 'aria-label': 'Signals fired' },
            signals.map((s) =>
              h('li', { class: ['sig', s.direction, s.severity] }, cap(ruleLabel(s.rule)), s.stale ? ' (stale)' : null),
            ),
          )
        : null,
    ),
    showBand ? h('div', { class: 'after' }, h('span', { class: 'after-k' }, 'band after'), bandPill(line.band)) : null,
  );
}

function cardTrace(c) {
  const { earlier, lines } = splitTrace(c);
  if (!lines.length) return null;
  const sig = new Map(c.signals.map((s) => [s.id, s]));
  return h(
    'section',
    { class: 'card trace-card', id: 'trace', 'aria-labelledby': 'trace-h' },
    h(
      'div',
      { class: 'card-head' },
      h('h2', { id: 'trace-h' }, 'How the planner decided'),
      h(
        'div',
        { class: 'trace-sum' },
        budgetLine(c),
        // The band the trace ended on stands only while its findings do; the rows keep theirs.
        traceOutdated(c)
          ? h('span', { class: 'state', title: 'A claim these checks relied on was corrected' }, 'Out of date')
          : bandPill(lastBand(c)),
      ),
    ),
    h(
      'p',
      { class: 'sub' },
      "One line per check: why it ran, was skipped or moved up, what it found, and the band after it. The planner's own checks are the agent's; checks a person asked for are the human's.",
    ),
    h('ol', { class: 'trace' }, lines.map((line, i) => traceRow(c, line, sig, i, ui.primed))),
    earlier.length
      ? disclosure(
          `earlier:${c.id}`,
          { class: 'earlier' },
          h('summary', {}, `${plural(earlier.length, 'line')} from an earlier investigation, set aside`),
          h('ol', { class: 'trace' }, earlier.map((line, i) => traceRow(c, line, sig, i, false))),
        )
      : null,
  );
}

function cardRemaining(c) {
  const b = c.budget;
  if (!b || b.stoppedBecause !== 'decisive' || !b.saved) return null;
  return h(
    'section',
    { class: 'card remaining', 'aria-labelledby': 'remaining-h' },
    h('h2', { id: 'remaining-h' }, 'Checks the decisive stop skipped'),
    h(
      'p',
      { class: 'sub' },
      `The planner stopped once the evidence was decisive, with ${plural(b.saved, 'search')} not spent. The agent never spends searches that cannot change the band. A person can, to see what the rest would find.`,
    ),
    h(
      'div',
      { class: 'actions' },
      h(
        'button',
        {
          type: 'button',
          class: 'btn',
          'data-focus': 'remaining',
          disabled: onBoard(c) || !!ui.busy || ui.signedOut,
          onclick: () => runRemaining(c.id),
        },
        busyLabel('remaining', 'Run remaining checks', 'Running checks…'),
      ),
      ui.busy === 'remaining' ? h('p', { class: 'progress', id: 'progress' }, progressText()) : null,
    ),
    onBoard(c) ? h('p', { class: 'fine' }, ON_BOARD_HINT) : null,
    slot('remaining'),
  );
}

function evidenceCard(c, s, compact) {
  const e = s.evidence || {};
  const fromText = s.source === 'text';
  const line = fromText ? null : stepLine(c, s.id);
  const quote = fromText ? e.quote : e.snippet;
  const source = fromText
    ? null
    : [
        safeLink(e.link) ?? h('span', {}, `${e.engine} search “${e.query}”`),
        e.date ? ` · dated ${e.date}` : null,
        e.retrievedAt ? ` · retrieved ${day(e.retrievedAt)}` : null,
      ];
  return h(
    'li',
    { class: ['ev', s.direction, compact && 'compact'] },
    h(
      'div',
      { class: 'ev-head' },
      h('span', { class: 'ev-label' }, cap(ruleLabel(s.rule))),
      gradeTag(s),
      s.stale ? h('span', { class: 'tag', title: 'A claim it relied on was corrected' }, 'stale') : null,
    ),
    h(
      'p',
      { class: 'ev-where' },
      fromText ? 'From the message itself' : line ? [`Step ${line.step} · `, h('code', { class: 'tool' }, line.tool)] : 'A search',
      !fromText && e.engine ? [' on ', h('code', { class: 'engine' }, e.engine)] : null,
      s.reusedFrom ? ` · reused from ${s.reusedFrom}` : null,
    ),
    !fromText && s.detail ? h('p', { class: 'ev-finding' }, cap(s.detail)) : null,
    !fromText && e.title && !compact ? h('p', { class: 'ev-title' }, e.title) : null,
    quote && !(compact && !fromText) ? h('blockquote', { class: 'ev-quote' }, quote) : null,
    source ? h('p', { class: 'ev-source' }, source) : null,
  );
}

function cardEvidence(c) {
  const d = c.draftVerdict;
  if (!d) return null;
  const byId = new Map(c.signals.map((s) => [s.id, s]));
  const citedIds = new Set(d.evidenceIds || []);
  const cited = [...citedIds].map((id) => byId.get(id)).filter(Boolean);
  const parts = parseSummary(d.summary);
  const also = parts.also.map((text) => ({ text, signal: matchAlso(c, text, citedIds) }));
  const outdated = !draftCurrent(c);
  return h(
    'section',
    { class: 'card evidence', id: 'evidence', 'aria-labelledby': 'evidence-h' },
    h(
      'div',
      { class: 'card-head' },
      h('h2', { id: 'evidence-h' }, 'Evidence behind the draft verdict'),
      outdated ? h('span', { class: 'state' }, 'Out of date') : bandPill(d.band),
    ),
    h(
      'p',
      { class: 'sub' },
      cited.length
        ? 'What the decision table counted. Every finding quotes the result it came from.'
        : 'The decision table cited no signal.',
    ),
    cited.length ? h('ol', { class: 'cards' }, cited.map((s) => evidenceCard(c, s, false))) : null,
    also.length
      ? [
          h('h3', {}, 'Also found'),
          h(
            'ol',
            { class: 'cards compact' },
            also.map((a) => (a.signal ? evidenceCard(c, a.signal, true) : h('li', { class: 'ev plain' }, a.text))),
          ),
        ]
      : null,
    parts.notCounted.length
      ? [h('h3', {}, 'Not counted'), h('ul', { class: 'not-counted' }, parts.notCounted.map((t) => h('li', {}, t)))]
      : null,
    disclosure(
      `full:${c.id}`,
      { class: 'full' },
      h('summary', {}, 'The draft verdict as text'),
      h(
        'div',
        { class: 'draft-head' },
        h('p', { class: 'fine' }, `Drafted ${stamp(d.draftedAt)}`),
        h(
          'button',
          { type: 'button', class: 'btn small', disabled: outdated, onclick: (e) => copyText(d.summary, e.currentTarget) },
          outdated ? 'Draft it again to copy' : 'Copy',
        ),
      ),
      h('pre', { class: 'draft-text', 'data-scroll': `verdict:${c.id}` }, d.summary),
    ),
  );
}

/** One copyable draft. An out-of-date one says so and can't be copied until it is drafted
 * again: it may quote evidence a correction set aside, or miss what a later check found. */
function draftBlock(c, id, title, meta, text, stale) {
  return h(
    'article',
    { class: ['draft', stale && 'stale'], id: `draft-${id}`, 'aria-labelledby': `draft-${id}-h` },
    h(
      'div',
      { class: 'draft-head' },
      h('h3', { id: `draft-${id}-h` }, title, stale ? h('span', { class: 'state' }, 'Out of date') : null),
      h(
        'button',
        {
          type: 'button',
          class: 'btn small',
          'data-focus': `copy-${id}`,
          disabled: stale,
          onclick: (e) => copyText(text, e.currentTarget),
        },
        stale ? 'Draft it again to copy' : 'Copy',
      ),
    ),
    stale
      ? h(
          'p',
          { class: 'stale-note' },
          'Out of date: a claim was corrected or a check ran after this was drafted, so it may cite evidence that no longer stands. Draft it again.',
        )
      : null,
    h('p', { class: 'fine' }, meta),
    h('pre', { class: 'draft-text', tabindex: '0', 'data-scroll': `${id}:${c.id}` }, text),
    h('p', { class: 'draft-note' }, 'Draft only: nothing is sent or filed.'),
  );
}

function cardDrafts(c) {
  return h(
    'section',
    { class: 'card drafts', id: 'drafts', 'aria-labelledby': 'drafts-h' },
    h('h2', { id: 'drafts-h' }, 'Drafts to copy'),
    h('p', { class: 'sub' }, 'Draft only: nothing is sent or filed. Copy what helps, and use it yourself.'),
    h(
      'div',
      { class: 'actions' },
      h(
        'button',
        {
          type: 'button',
          class: 'btn',
          'data-focus': 'draft-reply',
          disabled: !!ui.busy,
          onclick: () => writeDraft(c.id, 'draft_recruiter_reply', 'draft-reply'),
        },
        busyLabel('draft_recruiter_reply', 'Draft recruiter reply', 'Drafting…'),
      ),
      h(
        'button',
        {
          type: 'button',
          class: 'btn',
          'data-focus': 'draft-report',
          disabled: !!ui.busy,
          onclick: () => writeDraft(c.id, 'draft_cybercrime_report', 'draft-report'),
        },
        busyLabel('draft_cybercrime_report', 'Draft 1930 / cybercrime.gov.in summary', 'Drafting…'),
      ),
    ),
    slot('drafts'),
    c.draftReply
      ? draftBlock(
          c,
          'reply',
          'Reply to the recruiter',
          `One verification question per red flag · drafted ${stamp(c.draftReply.draftedAt)}. The person sends it, if they choose.`,
          c.draftReply.text,
          outdated(c, c.draftReply),
        )
      : null,
    c.draftReport
      ? draftBlock(
          c,
          'report',
          'Summary for helpline 1930 / cybercrime.gov.in',
          `Drafted ${stamp(c.draftReport.draftedAt)}. The person checks it, completes it and reports it themselves.`,
          c.draftReport.text,
          outdated(c, c.draftReport),
        )
      : null,
  );
}

/** Why `label` would say more than a verdict drafted as `band` found, as the server words it,
 * or null when it fits: "No contradictions found" only for the best band, a red flag never for
 * it, and "ask questions first" for any band. */
function labelMisfit(label, band) {
  if (!band) return null;
  if (label === 'no_contradictions_found' && band !== 'consistent_with_genuine') return 'not what the checks found';
  if (LABEL_CLASS[label] === 'red' && band === 'consistent_with_genuine') return 'says more than the checks found';
  return null;
}

// The label a band points to on its own. A high-risk verdict leaves the red flag to the person.
const BAND_LABEL = { consistent_with_genuine: 'no_contradictions_found', unverified: 'unverified_ask_questions' };

/** Why the case can't be published now, in words, or null when it can. */
function publishBlocked(c) {
  if (!c.signals.some((s) => s.source !== 'text' && isLive(s))) {
    if (!(c.trace || []).length) return 'A verdict goes on the board only with evidence from a search: investigate first.';
    const why = c.budget?.stoppedBecause;
    if (why === 'no_company') {
      return 'This message names no company, so there is nothing a search can check, and a verdict on the text alone does not go on the board. Copy the recruiter reply or the 1930 summary instead.';
    }
    if (why === 'search_error') return 'A search failed and nothing was made up, so there is no search evidence to publish. Investigate again to retry.';
    return 'A claim was corrected, so what the searches found is set aside. Confirm the claims and investigate again before publishing.';
  }
  if (!c.draftVerdict) return 'Draft the verdict first: a post carries the drafted band and the evidence behind it.';
  if (!draftCurrent(c)) return 'The case changed since the draft verdict. Draft it again and read it before publishing.';
  return null;
}

function cardPublish(c) {
  const posted = c.publishedVerdict;
  const band = draftCurrent(c) ? c.draftVerdict.band : null;
  const draft = ui.publish.get(c.id) ?? { label: '', note: '', picked: false };
  const changed = ui.changed.get(c.id);
  // The label the band points to, until the person picks one; never one that doesn't fit, and
  // none on its own once the agent changed the case after the person read it.
  if (!draft.picked || labelMisfit(draft.label, band)) {
    draft.label = changed ? '' : BAND_LABEL[band] ?? '';
    draft.picked = false;
  }
  ui.publish.set(c.id, draft);
  const blocked = publishBlocked(c);
  const syncButton = () => {
    const button = $('publish-btn');
    if (button) button.disabled = !!ui.busy || ui.signedOut || !!blocked || !draft.label;
  };
  let body;
  if (posted) {
    body = h(
      'p',
      { class: 'posted' },
      'On the Offer Board as ',
      h('strong', { class: ['label-text', LABEL_CLASS[posted.label]] }, BOARD_LABELS[posted.label] ?? posted.label),
      ` since ${stamp(posted.publishedAt)}. To publish again, retract it from the board first.`,
    );
  } else {
    body = h(
      'form',
      {
        class: 'publish-form',
        novalidate: true,
        onsubmit: (e) => {
          e.preventDefault();
          publish(c);
        },
      },
      h(
        'div',
        { class: 'field' },
        h('label', { for: 'pub-label' }, 'Label'),
        h(
          'select',
          {
            id: 'pub-label',
            'data-focus': 'pub-label',
            value: draft.label,
            onchange: (e) => {
              draft.label = e.target.value;
              draft.picked = true;
              // Choosing the label is reading the case as it is shown now.
              ui.read.set(c.id, c.revision);
              if (ui.changed.delete(c.id)) $('publish-changed')?.remove();
              syncButton();
            },
          },
          h('option', { value: '' }, 'Choose a label…'),
          Object.entries(BOARD_LABELS).map(([value, text]) => {
            const misfit = labelMisfit(value, band);
            return h('option', { value, disabled: !!misfit }, misfit ? `${text}: ${misfit}` : text);
          }),
        ),
        band
          ? h(
              'p',
              { class: 'fine', id: 'pub-label-hint' },
              `${band === 'consistent_with_genuine' ? 'The draft verdict says nothing found contradicts the offer' : `The draft verdict is ${BANDS[band].title.toLowerCase()}`}. A label that says more than the checks found is not offered.`,
            )
          : null,
      ),
      h(
        'div',
        { class: 'field' },
        h('label', { for: 'pub-note' }, 'Note for students (optional)'),
        h('textarea', {
          id: 'pub-note',
          rows: 3,
          maxlength: 1000,
          'data-focus': 'pub-note',
          value: draft.note,
          oninput: (e) => {
            draft.note = e.target.value;
          },
        }),
      ),
      changed
        ? h(
            'p',
            { class: 'blocked', id: 'publish-changed' },
            `The agent changed this case after you read it: it called ${listed([...changed])}. Read it again, then choose the label.`,
          )
        : null,
      blocked ? h('p', { class: 'blocked', id: 'publish-blocked' }, blocked) : null,
      h(
        'div',
        { class: 'actions' },
        h(
          'button',
          {
            type: 'submit',
            class: 'btn primary',
            id: 'publish-btn',
            'data-focus': 'publish',
            'aria-describedby': blocked ? 'publish-blocked' : null,
            disabled: !!ui.busy || ui.signedOut || !!blocked || !draft.label,
          },
          busyLabel('publish', 'Publish to Offer Board', 'Publishing…'),
        ),
      ),
      slot('publish'),
    );
  }
  return h(
    'section',
    { class: 'card publish', 'aria-labelledby': 'publish-h' },
    h('h2', { id: 'publish-h' }, 'Publish a verdict'),
    h(
      'p',
      { class: 'sub' },
      'Only a person publishes. The post describes this message, not the company named in it, and you can retract it.',
    ),
    body,
  );
}

function cardOutcome(c) {
  const o = c.outcome;
  return h(
    'section',
    { class: 'card outcome', 'aria-labelledby': 'outcome-h' },
    h('h2', { id: 'outcome-h' }, 'Outcome'),
    h('p', { class: 'sub' }, 'What the person who got this offer decided. Recorded by a person; the app does nothing else with it.'),
    h(
      'div',
      { class: 'outcomes', role: 'group', 'aria-labelledby': 'outcome-h' },
      Object.entries(OUTCOMES).map(([value, text]) =>
        h(
          'button',
          {
            type: 'button',
            class: 'btn',
            'aria-pressed': o?.value === value ? 'true' : 'false',
            'data-focus': `outcome-${value}`,
            disabled: !!ui.busy || ui.signedOut,
            onclick: () => recordOutcome(c.id, value),
          },
          text,
        ),
      ),
    ),
    o ? h('p', { class: 'fine' }, `Recorded: ${(OUTCOMES[o.value] ?? o.value).toLowerCase()}, ${stamp(o.recordedAt)}.`) : null,
    slot('outcome'),
  );
}

function introCard() {
  const step = (lead, rest) => h('li', {}, h('span', {}, h('strong', {}, lead), ` ${rest}`));
  return h(
    'section',
    { class: 'card intro', 'aria-labelledby': 'intro-h' },
    h('h2', { id: 'intro-h' }, 'Check an offer before anyone pays'),
    h(
      'p',
      { class: 'lede' },
      'A scammer can forge the message, but not the search results about it. Offer Checkpost reads the claims in a forwarded job offer and checks each one against search results.',
    ),
    h(
      'ol',
      { class: 'steps' },
      step('Paste', 'the message a student forwarded. Each claim appears as a chip, highlighted where it came from.'),
      step('Confirm', 'or correct the claims. Nothing is searched until they are confirmed, and the page marks any the agent confirmed instead of you.'),
      step('Investigate.', 'Each search runs only when earlier results make it worthwhile, and the trace says why.'),
      step(
        'Decide.',
        'Read the draft verdict and its evidence. Copy the recruiter reply or the 1930 summary, or publish to the Offer Board: only a person publishes.',
      ),
    ),
    h(
      'p',
      { class: 'fine' },
      'The app never sends a message, files a report or contacts a recruiter. The most it says for an offer is that nothing found contradicts it.',
    ),
  );
}

function renderMain() {
  const c = current();
  if (!c) {
    fill($('case'), introCard());
    return;
  }
  // Shown for the first time: from here on, the person has read the case at this revision.
  if (!ui.read.has(c.id)) ui.read.set(c.id, c.revision);
  fill(
    $('case'),
    cardInvestigation(c),
    cardTrace(c),
    cardRemaining(c),
    cardEvidence(c),
    cardDrafts(c),
    cardPublish(c),
    cardOutcome(c),
  );
}

// ---- rendering: the board and the activity log --------------------------------------------

function retractForm(caseId) {
  return h(
    'form',
    {
      class: 'retract',
      novalidate: true,
      onsubmit: (e) => {
        e.preventDefault();
        retract(caseId);
      },
    },
    h('label', { for: 'retract-reason' }, 'Why take it off the board?'),
    h('input', {
      id: 'retract-reason',
      type: 'text',
      maxlength: 200,
      autocomplete: 'off',
      'data-focus': 'retract-reason',
      value: ui.retractReason,
      oninput: (e) => {
        ui.retractReason = e.target.value;
      },
    }),
    h(
      'div',
      { class: 'actions' },
      h(
        'button',
        { type: 'submit', class: 'btn danger', disabled: !!ui.busy || ui.signedOut },
        busyLabel('retract', 'Retract verdict', 'Retracting…'),
      ),
      h(
        'button',
        {
          type: 'button',
          class: 'btn quiet',
          onclick: () => {
            ui.retracting = null;
            render();
          },
        },
        'Cancel',
      ),
    ),
  );
}

/** One board post, drawn from the post alone: what was published stays as it was published. */
function postItem(post) {
  return h(
    'li',
    { class: 'post', id: `post-${post.caseId}` },
    h(
      'div',
      { class: 'post-head' },
      h('span', { class: ['post-label', LABEL_CLASS[post.label]] }, BOARD_LABELS[post.label] ?? post.label),
      h('span', { class: 'case-id' }, post.caseId),
    ),
    h('p', { class: 'post-offer' }, post.offer ?? 'The message names no company, role or city.'),
    post.note ? h('p', { class: 'post-note' }, post.note) : null,
    h('p', { class: 'fine' }, `Published ${stamp(post.publishedAt)} by ${post.by === 'human' ? 'a person' : post.by}`),
    disclosure(
      `wa:${post.caseId}`,
      { class: 'wa' },
      h('summary', {}, 'The WhatsApp text'),
      h('pre', { class: 'draft-text', 'data-scroll': `wa:${post.caseId}` }, post.whatsapp),
    ),
    h(
      'div',
      { class: 'actions' },
      h(
        'button',
        { type: 'button', class: 'btn', 'data-focus': `wa-${post.caseId}`, onclick: (e) => copyText(post.whatsapp, e.currentTarget) },
        'Copy for WhatsApp',
      ),
      ui.retracting === post.caseId
        ? null
        : h(
            'button',
            {
              type: 'button',
              class: 'btn quiet',
              'data-focus': `retract-${post.caseId}`,
              disabled: !!ui.busy || ui.signedOut,
              onclick: () => startRetract(post.caseId),
            },
            'Retract…',
          ),
    ),
    ui.retracting === post.caseId ? retractForm(post.caseId) : null,
    slot(`retract:${post.caseId}`),
  );
}

function renderBoard() {
  const posts = state.board || [];
  fill(
    $('board'),
    h(
      'div',
      { class: 'card-head' },
      h('h2', { id: 'board-h' }, 'Offer Board'),
      h('a', { class: 'btn small', href: '/api/board.html', download: 'offer-board.html' }, 'Download board (HTML)'),
    ),
    h(
      'p',
      { class: 'sub' },
      'Your published log of checked offers. It reaches students as WhatsApp text you copy, or as the downloaded page.',
    ),
    posts.length
      ? h('ol', { class: 'posts' }, posts.map(postItem))
      : h('p', { class: 'empty' }, 'Nothing published yet. Only a person publishes a verdict.'),
  );
}

function argsText(args) {
  if (args == null) return '';
  if (typeof args !== 'object' || Array.isArray(args)) return JSON.stringify(args);
  const text = Object.entries(args)
    .map(([k, v]) => `${k} ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' · ');
  return text.length > 140 ? `${text.slice(0, 139)}…` : text;
}

function logRow(entry) {
  const args = argsText(entry.args);
  return h(
    'li',
    { class: ['log-row', `r-${entry.result}`, entry.actor === 'agent' && 'by-agent'] },
    h('span', { class: 'log-time num' }, clock(entry.ts)),
    actorPill(entry.actor),
    h('span', { class: 'log-call' }, h('code', {}, String(entry.tool)), args ? h('span', { class: 'log-args' }, args) : null),
    h('span', { class: ['log-result', entry.result] }, String(entry.result)),
    entry.reason ? h('span', { class: 'log-reason' }, entry.reason) : null,
  );
}

function renderActivity() {
  const log = state.activityLog || [];
  const agents = log.filter((e) => e.actor === 'agent').length;
  // At its end, the log follows the newest call; scrolled back, it stays where the person left it.
  const old = $('log');
  const follow = !old || old.scrollTop + old.clientHeight >= old.scrollHeight - 4;
  const top = old?.scrollTop ?? 0;
  const list = log.length
    ? h('ol', { class: 'log', id: 'log', tabindex: '0', 'aria-label': 'Activity log, oldest first' }, log.map(logRow))
    : h('p', { class: 'empty' }, 'Nothing has run yet.');
  fill(
    $('activity'),
    h(
      'div',
      { class: 'card-head' },
      h('h2', { id: 'activity-h' }, 'Activity log'),
      log.length ? h('span', { class: 'fine' }, `${plural(log.length, 'call')} · ${agents} by the agent`) : null,
    ),
    h(
      'p',
      { class: 'sub' },
      "Every call through the one invoke path, with who made it: a person's clicks are the human's; the planner's own checks, and every call without the person's session, are the agent's. Refusals are kept too.",
    ),
    agentNote,
    list,
  );
  if (log.length) list.scrollTop = follow ? list.scrollHeight : top;
}

// ---- the whole page -----------------------------------------------------------------------

let rendering = false;

function render() {
  if (!state) return;
  rendering = true;
  try {
    draw();
  } finally {
    rendering = false;
  }
}

/** What a redraw would otherwise lose: the caret in the focused field and how far each
 * scrolling box was scrolled. Typed text is kept in `ui` as it is typed, and open <details> in
 * `ui.open`. */
function snapshot() {
  const active = document.activeElement;
  const field = active?.dataset?.focus && typeof active.setSelectionRange === 'function';
  return {
    caret: field
      ? {
          key: active.dataset.focus,
          start: active.selectionStart,
          end: active.selectionEnd,
          direction: active.selectionDirection,
          top: active.scrollTop,
        }
      : null,
    scrolls: new Map([...document.querySelectorAll('[data-scroll]')].map((el) => [el.dataset.scroll, el.scrollTop])),
  };
}

function draw() {
  const kept = snapshot();
  renderHeader();
  renderStrip(state.server, state.calls || [], true);
  renderCaseBar();
  renderSide();
  renderMain();
  renderBoard();
  renderActivity();
  const active = document.activeElement;
  const lost = !active || active === document.body || !active.isConnected || active.closest('[hidden]');
  if (ui.focusKey && lost) {
    const el = document.querySelector(`[data-focus="${CSS.escape(ui.focusKey)}"]`);
    if (el && !el.disabled) {
      el.focus({ preventScroll: true });
      if (kept.caret?.key === ui.focusKey && typeof el.setSelectionRange === 'function') {
        el.setSelectionRange(kept.caret.start, kept.caret.end, kept.caret.direction);
        el.scrollTop = kept.caret.top;
      }
      // Focus the page moved on purpose (Confirm, then Investigate) is brought into view too.
      if (ui.revealFocus) {
        ui.revealFocus = false;
        revealEl(el, 'center');
      }
    }
  }
  for (const el of document.querySelectorAll('[data-scroll]')) {
    if (kept.scrolls.has(el.dataset.scroll)) el.scrollTop = kept.scrolls.get(el.dataset.scroll);
  }
  for (const err of ui.errors.values()) err.fresh = false;
}

async function loadSamples() {
  try {
    const data = await getJSON('/api/samples');
    samples = Array.isArray(data.samples) ? data.samples : [];
  } catch {
    samples = [];
  }
  const select = $('sample');
  append(
    select,
    samples.map((s) => h('option', { value: s.id }, `Sample ${String(s.id).toUpperCase()}: ${s.label}`)),
  );
  $('sample-wrap').hidden = !samples.length;
}

async function loadTools() {
  try {
    const data = await getJSON('/api/tools');
    const list = Array.isArray(data) ? data : data.tools || [];
    const names = new Set(list.map((t) => t.name));
    const hidden = HUMAN_VERBS.every((v) => !names.has(v));
    $('tools-line').textContent =
      `The agent's tool list has ${plural(list.length, 'tool')}.` +
      (hidden
        ? ' Publishing, retracting, recording an outcome and running checks past a decisive result are not among them: only a person does those, here.'
        : '');
  } catch {
    $('tools-line').textContent = '';
  }
}

function bindStatic() {
  $('paste-form').addEventListener('submit', openCase);
  $('offer-text').addEventListener('input', syncPaste);
  $('offer-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) $('paste-form').requestSubmit();
  });
  $('calls').addEventListener('scroll', fadeStrip, { passive: true });
  let fitting = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(fitting);
    fitting = requestAnimationFrame(fitStrip);
  });
  $('sample').addEventListener('change', (e) => {
    const sample = samples.find((s) => s.id === e.target.value);
    if (sample) $('offer-text').value = sample.text;
    syncPaste();
  });
  document.addEventListener('focusin', (e) => {
    ui.focusKey = e.target?.dataset?.focus ?? null;
  });
  document.addEventListener('compositionstart', () => {
    composing = true;
  });
  document.addEventListener('compositionend', () => {
    composing = false;
  });
  document.addEventListener('focusout', (e) => {
    // Focus left for nowhere on purpose. A redraw removing the focused element fires this
    // too, and keeps the key so the new element takes the focus back.
    if (!rendering && !e.relatedTarget) ui.focusKey = null;
  });
}

async function init() {
  bindStatic();
  syncPaste();
  await refresh();
  if (!state) return;
  if (state.cases.length) {
    ui.current = state.cases[state.cases.length - 1].id;
    ui.composing = false;
  }
  render();
  // From here on, a trace line or a call that appears is new, and is shown arriving.
  ui.primed = true;
  await Promise.all([loadSamples(), loadTools(), refreshBudget()]);
  setInterval(watch, WATCH_MS);
}

init();
