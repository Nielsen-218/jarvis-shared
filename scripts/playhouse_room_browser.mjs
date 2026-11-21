// Three isolated browser profiles use a loopback fixture, never real device tokens.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
async function connect(url){
 const ws=new WebSocket(url);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 let id=0;const pending=new Map();
 ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}};
 return {ws,send:(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})};
}
const version=await (await fetch('http://127.0.0.1:9337/json/version')).json();
const browser=await connect(version.webSocketDebuggerUrl),contexts=[],clients=[];
const out=new URL('../data/playhouse-evidence/',import.meta.url);await fs.mkdir(out,{recursive:true});
async function evaluate(c,expression){const r=await c.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(c,expression){for(let i=0;i<80;i++){if(await evaluate(c,expression))return;await new Promise(r=>setTimeout(r,100))}throw new Error('Timed out: '+expression)}
async function click(c,selector){await evaluate(c,'document.querySelector('+JSON.stringify(selector)+').click()')}
async function shot(c,name){const s=await c.send('Page.captureScreenshot',{format:'png'});await fs.writeFile(new URL(name+'.png',out),Buffer.from(s.data,'base64'))}
try{
 for(const [name,width,height] of [['host',1440,900],['one',390,844],['two',430,932]]){
  const {browserContextId}=await browser.send('Target.createBrowserContext');contexts.push(browserContextId);
  const {targetId}=await browser.send('Target.createTarget',{url:'about:blank',browserContextId});
  const pages=await (await fetch('http://127.0.0.1:9337/json')).json();
  const c=await connect(pages.find(p=>p.id===targetId).webSocketDebuggerUrl);clients.push(c);
  await c.send('Page.enable');
  await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<600});
  await c.send('Emulation.setTouchEmulationEnabled',{enabled:width<600});
  await c.send('Page.addScriptToEvaluateOnNewDocument',{source:"localStorage.setItem('jarvis_token',"+JSON.stringify('playhouse-test-'+name)+")"});
  await c.send('Page.navigate',{url:'http://127.0.0.1:8875/games.html'});
  await until(c,"!!document.querySelector('.preview-status')");
 }
 const [host,one,two]=clients;
 await click(host,'.room');await click(host,'#choices button');
 await until(host,"!!document.querySelector('[data-room-action=start]')");
 const code=await evaluate(host,"document.querySelector('#chapter').textContent.split(' ')[1]");
 assert.equal(await evaluate(host,"document.querySelector('#room-invite-code').textContent"),code);
 await shot(host,'room-host-lobby');
 for(const c of [one,two]){
  await click(c,'.mobile-nav button:last-child');
  await evaluate(c,"document.querySelector('#room-code').value="+JSON.stringify(code));
  await evaluate(c,"[...document.querySelectorAll('#choices button')].find(b=>b.textContent==='Antre nan salon an').click()");
  await until(c,"!!document.querySelector('[data-room-action=refresh]')");
 }
 await until(host,"document.querySelector('#chapter').textContent.includes('2 jwè')");
 await click(host,'[data-room-action=start]');
 for(let round=0;round<2;round++){
  for(const c of [one,two])await until(c,"!!document.querySelector('[data-room-action=vote0]')");
  await click(one,'[data-room-action=vote0]');
  await until(one,"document.querySelector('#choices').textContent.includes('Vòt ou anrejistre')");
  if(round===0){
   // Reload one controller; server keeps its vote and suppresses another vote.
   await one.send('Page.reload');
   await until(one,"!!document.querySelector('.preview-status')");
   await click(one,'.mobile-nav button:last-child');
   await evaluate(one,"[...document.querySelectorAll('#choices button')].find(b=>b.textContent==='Retounen nan salon mwen an').click()");
   await until(one,"document.querySelector('#choices').textContent.includes('Vòt ou anrejistre')");
   assert.equal(await evaluate(one,"!!document.querySelector('[data-room-action=vote0]')"),false);
  }
  await click(two,round===0?'[data-room-action=vote1]':'[data-room-action=vote0]');
  await until(host,"!!document.querySelector('[data-room-action=next]')");
  if(round===0){await shot(host,'room-host-vote');await shot(one,'room-phone-vote')}
  await click(host,'[data-room-action=next]');
 }
 for(const c of clients)await until(c,"document.querySelector('#chapter').textContent.includes('3/3')");
 assert.equal(await evaluate(host,"document.querySelector('#story-title').textContent"),'Sipriz pou Granmè!');
 await shot(host,'room-host-ending');await shot(two,'room-phone-ending');
 await click(host,'[data-room-action=close]');
 await until(one,"document.querySelector('#choices').textContent.includes('Salon sa a fèmen')");
 const report={passed:true,independentControllers:2,reconnectVotePreserved:true,storyCompleted:true,closedRoomInvalidated:true};
 await fs.writeFile(new URL('room-browser-results.json',out),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
}finally{
 for(const c of clients)c.ws.close();
 for(const id of contexts)await browser.send('Target.disposeBrowserContext',{browserContextId:id});
 browser.ws.close();
}
