const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const Offline = require('../offline.js');

const response = { account_id: 'a', changed: true, cursor: 'one',
  items: [{id:'article',content:'forbidden',title:'Example',is_read:false,bookmark_types:[]}] };
test('offline cache excludes fulltext, replaces deletions, and rejects another account', () => {
  const cached = Offline.apply(Offline.empty('a'), response, 10);
  assert.equal(cached.items[0].content, undefined);
  assert.equal(Offline.usable(cached,'a',100),true);
  assert.equal(Offline.usable(cached,'b',100),false);
  assert.equal(Offline.usable(undefined,undefined),false);
  assert.equal(Offline.usable(cached,'a',86400011),false);
  assert.equal(Offline.apply(cached,{...response,items:[]}).items.length,0);
  assert.throws(() => Offline.apply(cached,{...response,account_id:'b'}));
});
test('repeated offline actions coalesce to the desired idempotent state', () => {
  let state = Offline.apply(Offline.empty('a'),response);
  state = Offline.queue(state,'article','read',true);
  state = Offline.queue(state,'article','favorite',true);
  state = Offline.queue(state,'article','read',false);
  assert.equal(state.pending.length,2);
  assert.equal(state.pending.find(p=>p.kind==='read').enabled,false);
  assert.throws(()=>Offline.queue(state,'unknown','read',true));
});
function harness(responses) {
  const session = { apiUrl:'https://example.test/api', accessToken:'token-a', account:'a' };
  const local = { readingCache: Offline.queue(Offline.apply(Offline.empty('a'),response),'article','read',true) };
  const calls = [];
  const chrome = {storage:{session:{get:async()=>({...session}),clear:async()=>{Object.keys(session).forEach(k=>delete session[k]);}},
    local:{get:async()=>structuredClone(local),set:async value=>Object.assign(local,structuredClone(value)),remove:async k=>delete local[k]}}};
  const context = vm.createContext({chrome,DriftreadOffline:Offline,JSON,fetch:async(url,opts)=>{
    calls.push({url,opts}); const result = responses.shift();
    if (result instanceof Error) throw result;
    return { status:result.status ?? 200, ok:(result.status ?? 200)<400, json:async()=>result.body };
  }});
  vm.runInContext(fs.readFileSync(require.resolve('../offline-client.js'),'utf8'),context);
  return {context,session,local,calls};
}
test('reconnection flushes pending idempotent actions before authoritative cache sync',async()=>{
  const h=harness([{status:204},{body:{...response,items:[]}}]);
  await vm.runInContext('syncReading()',h.context);
  assert.equal(h.calls[0].opts.method,'POST'); assert.match(h.calls[0].url,/\/read$/);
  assert.equal(h.calls[0].opts.headers.Authorization,'Bearer token-a');
  assert.equal(h.local.readingCache.pending.length,0); assert.equal(h.local.readingCache.items.length,0);
});
test('offline network failure retains pending work and auth failure purges it',async()=>{
  const offline=harness([new Error('offline')]);
  await assert.rejects(vm.runInContext('syncReading()',offline.context));
  assert.equal(offline.local.readingCache.pending.length,1);
  const denied=harness([{status:401}]);
  await assert.rejects(vm.runInContext('syncReading()',denied.context));
  assert.equal(denied.local.readingCache,undefined); assert.equal(denied.session.accessToken,undefined);
});
