// Five independent phone profiles against a loopback-only authoritative host.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import net from 'node:net';

const python='C:/Users/you/ECC/jarvis/.venv/Scripts/python.exe';
const listener=net.createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
const host=spawn(python,['tests/manual_imposter_server.py'],{cwd:process.cwd(),windowsHide:true,stdio:'ignore',
 env:{...process.env,PLAYHOUSE_IMPOSTER_TEST_PORT:String(port),PLAYHOUSE_IMPOSTER_TEST_DATA:process.cwd()+'/data/playhouse-imposter-browser/session-'+Date.now()}});
const origin='http://127.0.0.1:'+port;
async function ready(){for(let i=0;i<80;i++){try{const r=await fetch(origin+'/imposter.html');if(r.ok)return;}catch{}await new Promise(r=>setTimeout(r,100));}throw new Error('Imposter fixture did not start');}
async function connect(url){
 const ws=new WebSocket(url);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
 let seq=0;const pending=new Map(),errors=[];
 ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const p=pending.get(message.id);if(!p)return;pending.delete(message.id);clearTimeout(p.timeout);message.error?p.reject(message.error):p.resolve(message.result);}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params)};
 return {ws,errors,send:(method,params={})=>new Promise((resolve,reject)=>{const id=++seq,timeout=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout: '+method));},15000);pending.set(id,{resolve,reject,timeout});ws.send(JSON.stringify({id,method,params}));})};
}
let browser;const clients=[],contexts=[];const out=new URL('../data/playhouse-evidence/',import.meta.url);await fs.mkdir(out,{recursive:true});
async function evaluate(c,expression){const r=await c.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;}
async function until(c,expression,loops=100){for(let i=0;i<loops;i++){if(await evaluate(c,expression))return;await new Promise(r=>setTimeout(r,100));}throw new Error('Timed out: '+expression);}
async function snapshot(c){return evaluate(c,"(async()=>{const seat=JSON.parse(localStorage.getItem('jarvis_playhouse_imposter_seat_v1'));return fetch('/api/playhouse/imposter/rooms/'+seat.id,{headers:{Authorization:'Bearer '+seat.token},cache:'no-store'}).then(r=>r.json())})()");}
async function screenshot(c,name){await new Promise(r=>setTimeout(r,250));const image=await c.send('Page.captureScreenshot',{format:'png'});await fs.writeFile(new URL(name+'.png',out),Buffer.from(image.data,'base64'));}
async function press(c,selector){await evaluate(c,`document.querySelector(${JSON.stringify(selector)}).click()`);}
try{
 await ready();
 const version=await (await fetch('http://127.0.0.1:9337/json/version')).json();browser=await connect(version.webSocketDebuggerUrl);
 for(const [index,width,height] of [[0,430,932],[1,360,800],[2,390,844],[3,412,915],[4,428,926]]){
  const {browserContextId}=await browser.send('Target.createBrowserContext');contexts.push(browserContextId);
  const {targetId}=await browser.send('Target.createTarget',{url:'about:blank',browserContextId});
  const pages=await (await fetch('http://127.0.0.1:9337/json')).json();
  const c=await connect(pages.find(p=>p.id===targetId).webSocketDebuggerUrl);clients.push(c);
  await c.send('Runtime.enable');await c.send('Page.enable');
  await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:true});
  await c.send('Emulation.setTouchEmulationEnabled',{enabled:true});
  await c.send('Page.navigate',{url:origin+'/imposter.html'});
  await until(c,"!!document.querySelector('#content .field input')");
  assert(await evaluate(c,'document.documentElement.scrollWidth<=innerWidth'));
 }
 const [leader,...others]=clients;
 await evaluate(leader,"document.querySelector('.field input').value='Host';document.querySelector('#content button.primary').click()");
 await until(leader,"!!document.querySelector('.room-code')");
 const code=await evaluate(leader,"document.querySelector('.room-code').textContent");assert.equal(code.length,6);
 for(let i=0;i<others.length;i++){
  const c=others[i];await evaluate(c,`document.querySelectorAll('.field input')[0].value=${JSON.stringify('Guest '+(i+1))};document.querySelectorAll('.field input')[1].value=${JSON.stringify(code)};document.querySelector('#content button.secondary').click()`);
  await until(c,"!!document.querySelector('.room-code')");
 }
 await until(leader,"document.querySelectorAll('.person').length===5");
 await evaluate(leader,"const s=document.querySelectorAll('.form-grid select');s[0].value='30';s[1].value='10';s[2].value='25';s[3].value='8';[...document.querySelectorAll('#content button')].find(b=>b.textContent.includes('Sove règ')).click()");
 await until(leader,"document.querySelector('#content button.primary')?.textContent.includes('Kòmanse')");
 await screenshot(leader,'imposter-host-lobby');
 await press(leader,'#content button.primary');
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('Wòl sekrè')");
 const roles=await Promise.all(clients.map(async c=>(await snapshot(c)).role));
 const imposter=roles.indexOf('imposter');assert.equal(roles.filter(r=>r==='imposter').length,1);
 await press(clients[2],'#language');
 assert.equal(await evaluate(clients[2],"document.documentElement.lang"),'en');
 assert((await evaluate(clients[2],"document.querySelector('#announcement').textContent")).includes('View your role privately'));
 assert((await evaluate(clients[2],"document.title")).includes('Trust Nobody'));
 await press(clients[2],'#language');
 for(const c of clients)assert.equal(await evaluate(c,"!!document.querySelector('.role-card')"),false,'Role hidden until tap');
 await press(clients[imposter],'#content button.secondary');
 assert.equal(await evaluate(clients[imposter],"document.querySelector('.role-card strong').textContent"),'Impostè');
 for(let i=0;i<clients.length;i++)if(i!==imposter)assert.equal(await evaluate(clients[i],"!!document.querySelector('.role-card')"),false);
 await screenshot(clients[imposter],'imposter-private-role');
 for(const c of clients)await press(c,'#content button.primary');
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('Lannwit') && !!document.querySelector('#content .field select')");
 await evaluate(clients[2],"Date.now=()=>0");
 await new Promise(resolve=>setTimeout(resolve,1000));
 assert(Math.abs(Number.parseInt(await evaluate(clients[2],"document.querySelector('#countdown').textContent"),10)-(await snapshot(clients[2])).seconds_left)<=2,'Countdown uses host time, not phone wall clock');
 const innocent=roles.findIndex(r=>r!=='imposter'),victim=innocent;
 for(const c of clients){const view=await snapshot(c);
  assert.equal(view.phase,'night');assert(view.players.every(p=>p.alignment===null));
  assert.equal(await evaluate(c,"document.querySelectorAll('#content .field select option').length"),5);
  assert.equal(await evaluate(c,"!!document.querySelector('.role-card')"),false);
 }
 // Reload a guest while its role is hidden. The seat token resumes the same night.
 await clients[1].send('Page.reload');await until(clients[1],"!!document.querySelector('#content .field select')");
 assert.equal((await snapshot(clients[1])).phase,'night');
 for(let i=0;i<clients.length;i++){
  const target=i===imposter?victim:imposter;
  await evaluate(clients[i],`const pick=document.querySelector('#content .field select');pick.value=${JSON.stringify(String(target))};pick.dispatchEvent(new Event('change'));document.querySelector('#content button.primary').click()`);
  await until(clients[i],"document.querySelector('#content .event-card.safe')?.textContent.includes('sele') || document.querySelector('#phase-label').textContent.includes('konfimasyon')");
 }
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('konfimasyon')");
 for(const c of clients){const view=await snapshot(c);assert.equal(view.phase,'confirm');assert.equal(view.confirmation_target,c===clients[imposter]?victim:imposter);}
 for(const c of clients)await press(c,'#content button.primary');
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('Maten')");
 const morning=await snapshot(leader);assert.equal(morning.event.victim,victim);
 assert.equal(morning.players.filter(p=>!p.alive).length,1);
 await screenshot(leader,'imposter-morning-phone');
 const dead=clients[victim];await press(dead,'#content .choice-grid button');
 assert.equal((await snapshot(dead)).prediction,'innocents');
 assert.equal((await snapshot(clients[imposter])).prediction,null);
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('diskisyon')",150);
 assert.equal(await evaluate(leader,"!!document.querySelector('.role-card strong')"),true,'Optional alibi challenge visible');
 await screenshot(clients[2],'imposter-discussion-phone');
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('vòt sekrè')",520);
 for(let i=0;i<clients.length;i++){
  if(i===victim){assert.equal((await snapshot(clients[i])).alive,false);continue;}
  const target=i===imposter?roles.findIndex((r,j)=>r!=='imposter'&&j!==victim):imposter;
  await evaluate(clients[i],`[...document.querySelectorAll('#content .choice-grid button')].find(b=>b.textContent===${JSON.stringify(''+(target===0?'Host':'Guest '+target))}).click()`);
 }
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('fen')");
 const ending=await snapshot(leader);assert.equal(ending.event.winner,'innocents');
 await press(clients[2],'#language');
 assert((await evaluate(clients[2],"document.querySelector('#headline').textContent")).includes('win!'));
 await press(clients[2],'#language');
 assert(ending.players.every(p=>p.role&&Number.isInteger(p.score)));
 assert(ending.event.awards.length>=1);
 await screenshot(leader,'imposter-ending-phone');
 await press(leader,'#content button.primary');
 for(const c of clients)await until(c,"document.querySelector('#phase-label').textContent.includes('Wòl sekrè')");
 const newRoles=await Promise.all(clients.map(async c=>(await snapshot(c)).role));
 assert.notEqual(newRoles.indexOf('imposter'),imposter,'Rematch rotates imposter');
 assert((await snapshot(leader)).players.every(p=>p.alive));
 assert(clients.every(c=>c.errors.filter(e=>!String(e.exceptionDetails?.text).includes('message channel closed')).length===0));
 const report={passed:true,phones:5,code,roleHiddenUntilTap:true,privateNight:true,oneNightElimination:true,
  deadPredictionPrivate:true,privateVote:true,rematchRotated:true,lanWithoutInternetServices:true};
 await fs.writeFile(new URL('imposter-browser-results.json',out),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
}finally{
 for(const c of clients)c.ws.close();
 if(browser){for(const id of contexts)await browser.send('Target.disposeBrowserContext',{browserContextId:id});browser.ws.close();}
 host.kill();
}
