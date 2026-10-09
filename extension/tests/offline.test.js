const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const Offline = require('../offline.js');

const response = { account_id: 'a', changed: true, cursor: 'one',
  authorized_article_ids:['article'], items: [{id:'article',content:'forbidden',title:'Example',is_read:false,bookmark_types:[]}] };
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
  const session = { apiUrl:'https://example.test/api', accessToken:'token-a', account:'a',connectionId:'connection-a' };
  const local = { readingCache: Offline.queue(Offline.apply(Offline.empty('a'),response),'article','read',true) };
  const calls = [];
  const chrome = {storage:{session:{get:async()=>({...session}),clear:async()=>{Object.keys(session).forEach(k=>delete session[k]);}},
    local:{get:async()=>structuredClone(local),set:async value=>Object.assign(local,structuredClone(value)),remove:async k=>delete local[k]}}};
  const context = vm.createContext({chrome,DriftreadOffline:Offline,JSON,URLSearchParams,fetch:async(url,opts)=>{
    calls.push({url,opts}); const result = responses.shift();
    if (result instanceof Error) throw result;
    return { status:result.status ?? 200, ok:(result.status ?? 200)<400, json:async()=>result.body };
  }});
  vm.runInContext(fs.readFileSync(require.resolve('../offline-client.js'),'utf8'),context);
  return {context,session,local,calls};
}
test('reconnection flushes pending idempotent actions before authoritative cache sync',async()=>{
  const h=harness([{body:response},{status:204},{body:{...response,items:[]}}]);
  await vm.runInContext('syncReading()',h.context);
  assert.equal(h.calls[1].opts.method,'POST'); assert.match(h.calls[1].url,/\/sync\/operations$/);
  assert.deepEqual(JSON.parse(h.calls[1].opts.body),{article_id:'article',kind:'read',enabled:true});
  assert.equal(h.calls[1].opts.headers.Authorization,'Bearer token-a');
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

test('in-flight account switch never writes cached operations with the new token',async()=>{
  const h=harness([{status:204}]);
  h.context.fetch=async(url,opts)=>{
    h.calls.push({url,opts}); h.session.accessToken='token-b'; h.session.account='b'; h.session.connectionId='connection-b';
    return {status:204,ok:true};
  };
  await assert.rejects(vm.runInContext('syncReading()',h.context));
  assert.equal(h.calls.length,1); assert.equal(h.calls[0].opts.headers.Authorization,'Bearer token-a');
  assert.equal(h.local.readingCache.account,'a'); // sync never overwrites the newly selected account
});

test('rights revocation removes cached articles and cancels pending work before replay',async()=>{
  const h=harness([{body:{...response,items:[],authorized_article_ids:[]}}]);
  await vm.runInContext('syncReading()',h.context);
  assert.equal(h.calls.length,1); assert.equal(h.local.readingCache.items.length,0);
  assert.equal(h.local.readingCache.pending.length,0);
});

test('revocation between preflight and replay discards 404 and still refreshes cache',async()=>{
  const h=harness([{body:response},{status:404},{body:{...response,items:[],authorized_article_ids:[]}}]);
  await vm.runInContext('syncReading()',h.context);
  assert.equal(h.calls.length,3); assert.equal(h.local.readingCache.pending.length,0);
  assert.equal(h.local.readingCache.items.length,0);
});

test('pending work outside the recent window survives authorized preflight',async()=>{
  const h=harness([{body:{...response,items:[]}},{status:204},{body:{...response,items:[]}}]);
  await vm.runInContext('syncReading()',h.context);
  assert.match(h.calls[0].url,/pending_ids=article/);
  assert.equal(h.calls[1].opts.method,'POST'); assert.equal(h.local.readingCache.pending.length,0);
});

test('read and bookmark undo replay through the atomic endpoint using the original account',async()=>{
  for (const kind of ['read','favorite','read_later']) {
    const h=harness([{body:response},{status:204},{body:response}]);
    h.local.readingCache.pending=[{articleId:'article',kind,enabled:false}];
    await vm.runInContext('syncReading()',h.context);
    assert.match(h.calls[1].url,/\/me\/sync\/operations$/);
    assert.equal(h.calls[1].opts.method,'POST');
    assert.equal(h.calls[1].opts.headers.Authorization,'Bearer token-a');
    assert.deepEqual(JSON.parse(h.calls[1].opts.body),{article_id:'article',kind,enabled:false});
    assert.equal(h.local.readingCache.pending.length,0);
  }
});

test('busy atomic replay retains pending intent for the next idempotent sync',async()=>{
  const h=harness([{body:response},{status:503}]);
  await assert.rejects(vm.runInContext('syncReading()',h.context));
  assert.equal(h.local.readingCache.pending.length,1);
  // Supply a successful replay between the two sync pulls.
  let call=0; h.context.fetch=async()=>++call===2 ? {status:204,ok:true} : {status:200,ok:true,json:async()=>response};
  await vm.runInContext('syncReading()',h.context);
  assert.equal(h.local.readingCache.pending.length,0);
});

test('stale authentication failure cannot erase a newly connected account',async()=>{
  const h=harness([]);
  h.context.fetch=async()=>{
    Object.assign(h.session,{account:'b',accessToken:'token-b',connectionId:'connection-b'});
    h.local.readingCache=Offline.empty('b');
    return {status:401,ok:false};
  };
  await assert.rejects(vm.runInContext('syncReading()',h.context));
  assert.equal(h.session.accessToken,'token-b'); assert.equal(h.local.readingCache.account,'b');
});

function optionsHarness() {
  const h=harness([]), handlers={}, requests=[], permission=[];
  const elements=Object.fromEntries(['apiUrl','accessToken','saved','disconnect','save'].map(id=>[id,{
    value:'',textContent:'',addEventListener:(_,fn)=>handlers[id]=fn,
  }]));
  h.context.document={getElementById:id=>elements[id]};
  h.context.URL=URL; let id=0; h.context.crypto={randomUUID:()=>`connection-${++id}`};
  h.context.chrome.storage.session.get=async(keys,cb)=>{const cfg={...h.session}; if(cb)cb(cfg); return cfg;};
  h.context.chrome.storage.session.set=async cfg=>Object.assign(h.session,cfg);
  h.context.chrome.storage.sync={remove:async()=>{}};
  h.context.chrome.permissions={request:()=>new Promise(resolve=>permission.push(resolve))};
  h.context.fetch=(url,opts)=>new Promise(resolve=>requests.push({url,opts,resolve:body=>resolve({status:200,ok:true,json:async()=>body})}));
  vm.runInContext(fs.readFileSync(require.resolve('../options.js'),'utf8'),h.context);
  async function start(account) {
    elements.apiUrl.value='https://example.test/api'; elements.accessToken.value=`token-${account}`;
    const task=handlers.save(); permission.shift()(true);
    for(let i=0;i<20;i++) await Promise.resolve();
    return {task,request:requests.at(-1)};
  }
  return {...h,elements,handlers,requests,permission,start};
}

test('options old response cannot overwrite a newer account connection',async()=>{
  const h=optionsHarness(); const a=await h.start('a'); const b=await h.start('b');
  b.request.resolve({...response,account_id:'b'}); await b.task;
  a.request.resolve(response); await a.task;
  assert.equal(h.session.account,'b'); assert.equal(h.session.accessToken,'token-b');
  assert.equal(h.local.readingCache.account,'b'); assert.equal(h.elements.saved.textContent,'帳號已連線');
});

test('options disconnect invalidates an in-flight account response',async()=>{
  const h=optionsHarness(); const a=await h.start('a');
  await h.handlers.disconnect(); a.request.resolve(response); await a.task;
  assert.equal(h.session.account,undefined); assert.equal(h.local.readingCache,undefined);
  assert.match(h.elements.saved.textContent,/已中斷/);
});

test('options ignores an older delayed permissions dialog',async()=>{
  const h=optionsHarness(); h.elements.apiUrl.value='https://example.test/api';
  h.elements.accessToken.value='token-a'; const old=h.handlers.save(); const resolveOld=h.permission.shift();
  const newer=await h.start('b'); newer.request.resolve({...response,account_id:'b'}); await newer.task;
  resolveOld(true); await old;
  assert.equal(h.requests.length,1); assert.equal(h.session.account,'b');
});


test('queue bounds pending article IDs while allowing all actions on the existing article',()=>{
  const state=Offline.empty('a');
  state.items=Array.from({length:101},(_,id)=>({id:String(id)}));
  let queued=state;
  for(let id=0;id<100;id++) queued=Offline.queue(queued,String(id),'read',true);
  assert.throws(()=>Offline.queue(queued,'100','read',true),/100/);
  assert.equal(Offline.queue(queued,'0','favorite',true).pending.length,101);
});
