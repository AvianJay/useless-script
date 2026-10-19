'use strict';

// The OS/container is the security boundary. This REPL is never run directly by
// production code. Its only host capabilities are checked by the Python broker.
const repl = require('node:repl');
const readline = require('node:readline');
const { PassThrough, Writable } = require('node:stream');
const { inspect, format } = require('node:util');
const { AsyncLocalStorage } = require('node:async_hooks');

const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const execution = new AsyncLocalStorage();
const pending = new Map();
let serial = 0;
let busy = false;
let rpcCount = 0;
let rejectEvaluation = null;
const server = repl.start({
  prompt: '', terminal: false, useGlobal: false, ignoreUndefined: true,
  input: new PassThrough(), output: new Writable({ write(_chunk, _encoding, done) { done(); } }),
});
// REPL's default evaluator sends uncaught errors to its domain rather than the
// eval callback. Forward those errors so a rejected await does not hang until
// the host's timeout. The container image fixes the supported Node major.
server._domain.on('error', error => {
  if (rejectEvaluation) rejectEvaluation(error);
});

function rpc(name, args = {}) {
  const scope = execution.getStore();
  if (!scope || scope.finished) return Promise.reject(new Error('No active evaluation'));
  if (++rpcCount > 24) return Promise.reject(new Error('Bridge call limit reached for this evaluation'));
  const id = String(++serial);
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    send({ type: 'rpc', evaluation_id: scope.id, id, name, arguments: args });
  });
}

function write(...values) {
  const scope = execution.getStore();
  if (!scope || scope.finished) return;
  const line = format(...values);
  const remaining = Math.max(0, 12000 - scope.chars);
  if (remaining) scope.output.push(line.slice(0, remaining));
  scope.chars += line.length;
}

function parseCsv(input, { delimiter = ',' } = {}) {
  if (typeof input !== 'string' || input.length > 500000 || ![',', '\t', ';'].includes(delimiter)) {
    throw new Error('CSV input must be text up to 500000 characters; use comma, tab or semicolon delimiter');
  }
  const text = input.replace(/^\uFEFF/, '');
  const rows = [];
  let row = [], field = '', quoted = false, afterQuote = false;
  const finishField = () => {
    row.push(field); field = ''; afterQuote = false;
    if (row.length > 200) throw new Error('CSV column limit exceeded');
  };
  const finishRow = () => {
    finishField(); rows.push(row); row = [];
    if (rows.length > 10000) throw new Error('CSV row limit exceeded');
  };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') { quoted = false; afterQuote = true; }
      else field += ch;
    } else if (ch === delimiter) finishField();
    else if (ch === '\r' || ch === '\n') {
      finishRow();
      if (ch === '\r' && text[i + 1] === '\n') i++;
    } else if (ch === '"' && !field && !afterQuote) quoted = true;
    else if (ch === '"' || afterQuote) throw new Error('Malformed CSV quoting');
    else field += ch;
  }
  if (quoted) throw new Error('Unterminated CSV quoted field');
  if (field || row.length || afterQuote) finishRow();
  return rows;
}

function toCsv(rows, { delimiter = ',', spreadsheetSafe = true } = {}) {
  if (!Array.isArray(rows) || rows.length > 10000 || ![',', '\t', ';'].includes(delimiter)) {
    throw new Error('CSV rows or delimiter are invalid');
  }
  return rows.map(row => {
    if (!Array.isArray(row) || row.length > 200) throw new Error('CSV row must be an array of at most 200 fields');
    return row.map(value => {
      let text = value == null ? '' : String(value);
      if (spreadsheetSafe && typeof value === 'string' && /^\s*[=+\-@]/.test(text)) text = "'" + text;
      return '"' + text.replace(/"/g, '""') + '"';
    }).join(delimiter);
  }).join('\r\n');
}

Object.assign(server.context, {
  data: Object.freeze({ parseCsv, toCsv }),
  write,
  console: Object.freeze({ log: write, info: write, warn: write, error: write, debug: write }),
  tools: Object.freeze({ call: rpc }),
  browser: Object.freeze({
    read: args => rpc('browser_read', args),
    act: args => rpc('browser_act', args),
    evaluate: args => rpc('browser_evaluate', args),
    view: args => rpc('browser_view', args),
  }),
  resources: Object.freeze({
    list: () => rpc('list_resources'),
    read: (resource_id, options = {}) => rpc('read_resource', { ...options, resource_id }),
    write: (filename, content) => rpc('create_resource', { filename, content }),
    send: (resource_id, summary = '') => rpc('send_resource', { resource_id, summary }),
  }),
});

async function evaluate(message) {
  busy = true;
  rpcCount = 0;
  const scope = { id: message.id, output: [], chars: 0, finished: false };
  try {
    const value = await execution.run(scope, () => new Promise((resolve, reject) => {
      rejectEvaluation = reject;
      server.eval(message.code + '\n', server.context, 'skill-repl', (error, result) => {
        if (error) reject(error); else resolve(result);
      });
    }));
    scope.finished = true;
    send({
      type: 'result', id: message.id, ok: true, output: scope.output,
      value: value === undefined ? null : inspect(value, { depth: 4, maxArrayLength: 50, maxStringLength: 2000 }).slice(0, 12000),
      truncated: scope.chars > 12000,
    });
  } catch (error) {
    scope.finished = true;
    send({ type: 'result', id: message.id, ok: false, output: scope.output, error: String(error.message || error).slice(0, 1000) });
  } finally {
    rejectEvaluation = null;
    busy = false;
  }
}

readline.createInterface({ input: process.stdin, crlfDelay: Infinity }).on('line', line => {
  try {
    const message = JSON.parse(line);
    if (message.type === 'rpc_result') {
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      if (message.ok) request.resolve(message.data);
      else request.reject(new Error(String(message.error || 'Tool failed')));
    } else if (message.type === 'evaluate' && !busy && typeof message.code === 'string') {
      void evaluate(message);
    }
  } catch {
    // Do not echo malformed protocol input.
  }
}).on('close', () => process.exit(0));

send({ type: 'ready' });
