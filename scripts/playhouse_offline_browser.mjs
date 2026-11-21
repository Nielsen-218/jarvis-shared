// Real shared service worker, fresh isolated profile, then a physically stopped server.
import fs from 'node:fs/promises';
import http from 'node:http';
import assert from 'node:assert/strict';
import lantern from '../app/games-lantern.json' with {type:'json'};
import {badges, bluffCard, challenge, objectives, restoreRivalry} from '../app/games-rivalry.mjs';
import {cardFor, restoreKnow, subjectSeat} from '../app/games-know.mjs';
import {bluffCardFor, restoreBluff, storytellerSeat} from '../app/games-bluff.mjs';
import {restoreWould, wouldCardFor} from '../app/games-would.mjs';
import {restoreTelephone,roundCount,stageKind,stageCount,starterFor} from '../app/games-telephone.mjs';
const assets=['/games.html','/games.css','/games-ui.mjs','/games-story.mjs',
 '/games-story.json','/games-party.mjs','/games-rooms.mjs',
 '/games-mystery.mjs','/games-mystery-ui.mjs','/games-lantern.json',
 '/games-rivalry.mjs','/games-rivalry-ui.mjs',
 '/games-know.mjs','/games-know-ui.mjs',
 '/games-bluff.mjs','/games-bluff-ui.mjs',
 '/games-would.mjs','/games-would-ui.mjs',
 '/games-telephone.mjs','/games-telephone-ui.mjs',
 '/imposter.html','/imposter.css','/imposter-ui.mjs',
 ...['family','couple','covers','room'].map(n=>'/games-assets/'+n+'.webp')];
const server=http.createServer(async(req,res)=>{
 const path=new URL(req.url,'http://localhost').pathname;
 if(path==='/api/playhouse/probe'){
  res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'}).end('{"fixture":true}');return;
 }
 if(!assets.includes(path)&&path!=='/sw.js'){res.writeHead(404).end();return}
 try{
  const bytes=await fs.readFile(new URL('../app'+path,import.meta.url));
  const type=path.endsWith('.webp')?'image/webp':path.endsWith('.css')?'text/css':path.endsWith('.json')?'application/json':/\.m?js$/.test(path)?'text/javascript':'text/html';
  res.writeHead(200,{'Content-Type':type,'Cache-Control':'no-store'}).end(bytes);
 }catch{res.writeHead(404).end()}
});
async function connect(url){
 const ws=new WebSocket(url);await new Promise((r,j)=>{
  const timeout=setTimeout(()=>{ws.close();j(new Error('Browser connection timed out'));},5000);
  ws.onopen=()=>{clearTimeout(timeout);r()};
  ws.onerror=error=>{clearTimeout(timeout);j(error)};
 });
 let id=0;const pending=new Map(),errors=[];
 ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(!p)return;pending.delete(m.id);clearTimeout(p.timeout);m.error?p.reject(m.error):p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params)};
 return {ws,errors,send:(method,params={})=>new Promise((resolve,reject)=>{
  const request=++id;
  const timeout=setTimeout(()=>{pending.delete(request);reject(new Error('Timed out: '+method));},10000);
  pending.set(request,{resolve,reject,timeout});
  ws.send(JSON.stringify({id:request,method,params}));
 })};
}
const version=await (await fetch('http://127.0.0.1:9337/json/version')).json();
const browser=await connect(version.webSocketDebuggerUrl);
let context,c;
const out=new URL('../data/playhouse-evidence/',import.meta.url);await fs.mkdir(out,{recursive:true});
async function evaluate(expression){const r=await c.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value}
async function until(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100))}throw new Error('Timed out: '+expression)}
const choice = index => evaluate(`document.querySelectorAll('#choices button')[${index}].click()`);
const startAdventure = () => evaluate("document.querySelector('#start-story').click()");
const savedMatch = async () => restoreRivalry(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_rivalry_v1'))"));
const savedKnow = async () => restoreKnow(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_know_v1'))"));
const savedBluff = async () => restoreBluff(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_bluff_v1'))"));
const savedWould = async () => restoreWould(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_would_v1'))"));
const savedTelephone=async()=>restoreTelephone(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1'))"));
async function telephoneMatch(players,length,language){
 assert.equal(await evaluate('document.documentElement.lang'),language);
 await evaluate("document.querySelectorAll('.game')[4].click()");
 await evaluate(`document.querySelectorAll('#choices select')[0].value=${JSON.stringify(length)};document.querySelectorAll('#choices select')[1].value=${JSON.stringify(String(players))};document.querySelectorAll('#choices select')[1].dispatchEvent(new Event('change'));[...document.querySelectorAll('.telephone-names input')].forEach((input,i)=>input.value='Player '+(i+1));document.querySelector('#choices button').click()`);
 let game=await savedTelephone();assert.equal(game.phase,'handoff');
 assert.equal(game.variant,players===2?'duo':'chain');
 const rounds=roundCount(game);
 for(let round=0;round<rounds;round++){
  const secret=players===2?starterFor(game):null;
  for(let stage=0;stage<stageCount(players,game.variant);stage++){
   assert.equal(game.phase,'handoff');
   const nextName=game.names[(round+stage)%players];
   assert.equal(await evaluate("document.querySelector('#story-title').textContent"),nextName);
   assert(!(await evaluate("document.querySelector('#choices').textContent")).includes(players===2?secret.en:'Original dragon'));
   await choice(0);
   game=await savedTelephone();assert.equal(game.phase,'entry');
   if(stageKind(stage,game.variant)==='drawing'){
    if(players===2)assert((await evaluate("document.querySelector('#choices').textContent")).includes(secret[language]));
    const box=await evaluate("document.querySelector('.telephone-canvas').getBoundingClientRect().toJSON()");
    for(const [type,x,y,buttons] of [['mousePressed',.25,.25,1],['mouseMoved',.5,.5,1],['mouseReleased',.75,.65,0]])
      await c.send('Input.dispatchMouseEvent',{type,x:box.x+box.width*x,y:box.y+box.height*y,button:'left',buttons,clickCount:1});
    assert.equal(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.length"),1);
    if((players===8&&round===0&&stage===1)||(players===2&&round===0&&stage===0)){
      await evaluate('window.beforeTelephoneReload=true');await c.send('Page.reload');
      await until("!window.beforeTelephoneReload && !!document.querySelector('.preview-status')");
      await evaluate("document.querySelectorAll('.game')[4].click();[...document.querySelectorAll('#choices button')].find(button=>/Kontinye|Resume/.test(button.textContent)).click()");
      assert(!(await evaluate("document.querySelector('#choices').textContent")).includes(players===2?secret[language]:'Original dragon'));
      await choice(0);
      assert.equal(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.length"),1);
    }
   }else{
    if(players===2)assert(!(await evaluate("document.querySelector('#choices').textContent")).includes(secret[language]));
    await evaluate(`document.querySelector('.telephone-text').value=${JSON.stringify(stage===0?'Original dragon on a skateboard':'Guess '+round+'-'+stage)}`);
   }
   await evaluate("[...document.querySelectorAll('#choices button')].at(-1).click()");
   game=await savedTelephone();
  }
  assert.equal(game.phase,'reveal');
  assert.equal(await evaluate("document.querySelectorAll('.telephone-reveal-card').length"),stageCount(players,game.variant)+(players===2?1:0));
  await evaluate("[...document.querySelectorAll('#choices button')].at(-1).click()");
  game=await savedTelephone();
 }
 assert.equal(game.phase,'end');assert.equal(game.chains.length,rounds);
 await evaluate("document.querySelector('#close-story').click()");
 return {players,length,language,chains:game.chains.length,contributions:game.chains.reduce((n,c)=>n+c.entries.length,0)};
}
async function rivalryMatch(replay, lang, scenario) {
 const winnerSeat=replay%2;
 assert.equal(await evaluate('document.documentElement.lang'),lang);
 await startAdventure();
 await choice(0);
 await evaluate(`document.querySelector('#choices select').value=${JSON.stringify(String(scenario))};[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['Nia','Jo'][i])`);
 await choice(0);
 assert.equal((await savedMatch()).scenario,scenario);
 for (let player=0;player<2;player++) {
  const state = await savedMatch();
  const privateObjective = objectives[state.players[state.seat].role][lang];
  assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(privateObjective));
  await choice(0);
  assert((await evaluate("document.querySelector('#story-copy').textContent")).includes(privateObjective));
  assert.equal(await evaluate("document.querySelector('#story-title').textContent"),
   (state.players[state.seat].role===0?'🐐 ':'🦜 ')+state.names[state.seat]);
  if (player===0) {
   await evaluate("document.querySelector('#close-story').click()");
   assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(privateObjective));
   await startAdventure();await choice(0);await choice(1);
   assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(privateObjective));
   await choice(0);
   assert((await evaluate("document.querySelector('#story-copy').textContent")).includes(privateObjective));
  }
  await choice(0);
  await choice(1);
  await choice(0);
 }
 let beforeFinal;
 for (let round=0;round<12;round++) {
  let state = await savedMatch();
  assert.equal(state.round,round);
  if (round===11) beforeFinal = state.players.map(player=>player.score);
  while (state.phase==='play') {
   const step = challenge(state);
   const {seat} = state;
   await choice(0); // Every turn starts covered.
   if (step.kind==='route') {
    await choice(round===8 ? (seat===winnerSeat ? 3 : 1) : round===4 && seat===winnerSeat ? 2 : 0);
   } else if (step.kind==='bluff') {
    if(state.claim===null) assert((await evaluate("document.querySelector('#story-copy').textContent")).includes(bluffCard(state).clue[lang]));
    await choice(state.claim===null ? (bluffCard(state).actual+1)%3 : seat!==winnerSeat ? 0 : 1);
    if (round===9 && replay===0 && state.claim===null) {
     const committed=await savedMatch();
     await evaluate('window.beforeOfflineReload=true');
     await c.send('Page.reload');
     await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
     await startAdventure();await choice(0);await choice(1);
     assert.equal(await evaluate("document.querySelectorAll('#choices button').length"),1);
     assert.deepEqual(await savedMatch(),committed);
     assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(badges[bluffCard(state).actual][lang]));
    }
   } else {
    const missed = (round===3 && seat===winnerSeat) || (round===6 && seat===winnerSeat) || (round===11 && seat!==winnerSeat);
    if (step.kind==='order') {
     for (const digit of step.answer) await choice(Number(digit)-1);
     await choice(4);
    } else {
     if (step.kind==='memory') {
      await choice(0);
      await choice(0);
     }
     if (step.kind==='wager') {
      await choice(state.players[seat].coins>=2 ? 1 : 0);
      if (round===6 && state.turns===0 && replay===0) {
       const committed = await savedMatch();
       await evaluate('window.beforeOfflineReload=true');
       await c.send('Page.reload');
       await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
       await startAdventure();
       await choice(0);
       await choice(1);
       assert.equal(await evaluate("document.querySelectorAll('#choices button').length"),1);
       assert.deepEqual(await savedMatch(),committed);
       await choice(0);
       assert((await evaluate("document.querySelector('#story-copy').textContent")).includes((lang==='ht'?'Paryaj ou: ':'Your stake: ')+'2'));
      }
     }
     const answer=JSON.stringify(missed?'0':step.answer);
     await evaluate(`(() => {const input=document.querySelector('.rivalry-input'); input.value=${answer};input.dispatchEvent(new Event('input'));})()`);
     await choice(0);
    }
   }
   state = await savedMatch();
  }
  assert.equal(state.phase,'result');
  assert.equal(state.log.length,2*(round+1));
  assert((await evaluate("document.querySelector('#story-copy').textContent")).includes('Nia'));
  await choice(0);
 }
 const state = await savedMatch();
 assert.equal(state.end,winnerSeat);
 assert(beforeFinal[winnerSeat]<beforeFinal[1-winnerSeat]);
 assert.equal(await evaluate("document.querySelector('#story-title').textContent"),state.names[state.end]+(lang==='ht'?' genyen!':' wins!'));
 const shot = await c.send('Page.captureScreenshot',{format:'png'});
 await fs.writeFile(new URL('rivalry-ending-'+replay+'.png',out),Buffer.from(shot.data,'base64'));
 await evaluate("document.querySelector('#close-story').click()");
 return {winner:state.names[state.end],language:lang,scenario,beforeFinal,score:state.players.map(player=>player.score),challenges:12,turns:state.log.length};
}
async function knowMatch(replay,lang) {
 const winnerSeat=replay%2;
 assert.equal(await evaluate('document.documentElement.lang'),lang);
 await evaluate("document.querySelectorAll('.game')[1].click()");
 await evaluate("[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['Nia','Jo'][i])");
 await choice(0);
 for(let round=0;round<12;round++) {
  let state=await savedKnow();
  assert.equal(state.round,round);
  assert.equal(state.phase,'cover-subject');
  const card=cardFor(state),truth=round%3;
  assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.choices[truth][lang]));
  await choice(0);
  await choice(truth);
  state=await savedKnow();
  assert.equal(state.phase,'forecast');
  if(round===0 && replay===0) {
   await evaluate('window.beforeOfflineReload=true');await c.send('Page.reload');
   await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
   await evaluate("document.querySelectorAll('.game')[1].click()");await choice(1);
   assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.choices[truth][lang]));
   assert.deepEqual(await savedKnow(),state);
   await choice(0);
  }
  const guesser=1-subjectSeat(state);
  await choice(guesser===winnerSeat?0:1);
  assert.equal((await savedKnow()).phase,'cover-guesser');
  await choice(0);
  if(guesser===winnerSeat && round===1) {
   await choice(3);
   const hinted=await savedKnow();
   assert.equal(hinted.hint,true);
   await evaluate('window.beforeOfflineReload=true');await c.send('Page.reload');
   await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
   await evaluate("document.querySelectorAll('.game')[1].click()");await choice(1);
   assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.choices[truth][lang]));
   assert.deepEqual(await savedKnow(),hinted);
   await choice(0);
   assert.equal(await evaluate(`document.querySelectorAll('#choices button')[${(truth+1)%3}].disabled`),true);
  }
  if(guesser===winnerSeat && round===3) await choice(3);
  await choice(guesser===winnerSeat?truth:(truth+2)%3);
  state=await savedKnow();
  assert.equal(state.phase,'result');
  assert.equal(state.history.length,round+1);
  assert((await evaluate("document.querySelector('#story-copy').textContent")).includes(state.names[guesser]));
  await choice(0);
 }
 const state=await savedKnow();
 assert.equal(state.phase,'end');
 assert(state.players[winnerSeat].score>state.players[1-winnerSeat].score);
 const shot=await c.send('Page.captureScreenshot',{format:'png'});
 await fs.writeFile(new URL('know-ending-'+replay+'.png',out),Buffer.from(shot.data,'base64'));
 await evaluate("document.querySelector('#close-story').click()");
 if(replay===1) {
  await evaluate("document.querySelectorAll('.game')[1].click()");
  await choice(2);
  assert.equal(await evaluate("localStorage.getItem('jarvis_playhouse_know_v1')"),null);
  await evaluate("document.querySelector('#close-story').click()");
 }
 return {winner:state.names[winnerSeat],language:lang,score:state.players.map(player=>player.score),rounds:state.history.length};
}
async function bluffMatch(replay,lang) {
 const winnerSeat=replay%2;
 assert.equal(await evaluate('document.documentElement.lang'),lang);
 await evaluate("document.querySelectorAll('.game')[2].click()");
 await evaluate("[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['Nia','Jo'][i])");
 await choice(0);
 for(let round=0;round<12;round++) {
  let state=await savedBluff();
  assert.equal(state.round,round);
  assert.equal(state.phase,'cover-storyteller');
  const card=bluffCardFor(state),truth=round%2===0;
  assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.yes[lang]));
  await choice(0);
  await choice(truth?0:1);
  state=await savedBluff();
  assert.equal(state.phase,'claim');
  const storyteller=storytellerSeat(state),detective=1-storyteller;
  if(storyteller===winnerSeat && state.players[storyteller].wild) await choice(2);
  const claim=storyteller===winnerSeat?!truth:truth;
  await choice(claim?0:1);
  state=await savedBluff();
  assert.equal(state.phase,'cover-detective');
  if(round===0) {
   await evaluate('window.beforeOfflineReload=true');await c.send('Page.reload');
   await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
   assert.equal(await evaluate('document.documentElement.lang'),lang);
   await evaluate("document.querySelectorAll('.game')[2].click()");await choice(1);
   assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.yes[lang]));
   assert.deepEqual(await savedBluff(),state);
  }
  await choice(0);
  const believe=detective===winnerSeat?claim===truth:claim!==truth;
  await choice(believe?0:1);
  state=await savedBluff();
  assert.equal(state.phase,'result');
  assert.equal(state.history.length,round+1);
  assert.equal(state.history.at(-1).winner,winnerSeat);
  await choice(0);
 }
 const state=await savedBluff();
 assert.equal(state.phase,'end');
 assert(state.players[winnerSeat].score>state.players[1-winnerSeat].score);
 const shot=await c.send('Page.captureScreenshot',{format:'png'});
 await fs.writeFile(new URL('bluff-ending-'+replay+'.png',out),Buffer.from(shot.data,'base64'));
 await evaluate("document.querySelector('#close-story').click()");
 if(replay===1) {
  await evaluate("document.querySelectorAll('.game')[2].click()");await choice(2);
  assert.equal(await evaluate("localStorage.getItem('jarvis_playhouse_bluff_v1')"),null);
  await evaluate("document.querySelector('#close-story').click()");
 }
 return {winner:state.names[winnerSeat],language:lang,score:state.players.map(player=>player.score),rounds:state.history.length};
}
async function wouldMatch(replay,lang){
 const winnerSeat=replay%2;
 assert.equal(await evaluate('document.documentElement.lang'),lang);
 await evaluate("document.querySelectorAll('.game')[3].click();document.querySelector('#choices button').click()");
 await evaluate("[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['Nia','Jo'][i])");
 await choice(0);
 for(let round=0;round<12;round++){
  let state=await savedWould();
  assert.equal(state.round,round);
  const card=wouldCardFor(state);
  for(let turn=0;turn<2;turn++){
   const seat=state.seat,own=(seat+round)%2,truth=(1-seat+round)%2;
   assert.equal(state.phase,'cover');
   assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.choices[own][lang]));
   await choice(0);
   await choice(own);
   await choice(seat===winnerSeat?truth:1-truth);
   state=await savedWould();
   if(state.phase==='risk'){
    await choice(seat===winnerSeat&&state.players[seat].double?1:0);
    state=await savedWould();
   }
   if(round===0&&turn===0){
    await evaluate('window.beforeOfflineReload=true');await c.send('Page.reload');
    await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
    assert.equal(await evaluate('document.documentElement.lang'),lang);
    await evaluate("document.querySelectorAll('.game')[3].click();document.querySelector('#choices button').click()");
    await choice(1);
    assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes(card.choices[own][lang]));
    assert.deepEqual(await savedWould(),state);
   }
  }
  assert.equal(state.phase,'result');
  assert.equal(state.history.length,round+1);
  await choice(0);
 }
 const state=await savedWould();
 assert.equal(state.phase,'end');
 assert(state.players[winnerSeat].score>state.players[1-winnerSeat].score);
 assert((await evaluate("document.querySelector('#story-title').textContent")).includes(state.names[winnerSeat]));
 assert((await evaluate("document.querySelector('#story-copy').textContent")).includes(state.names[winnerSeat]));
 await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
 await evaluate('new Promise(resolve=>setTimeout(resolve,250))');
 const shot=await c.send('Page.captureScreenshot',{format:'png'});
 await fs.writeFile(new URL('would-ending-'+replay+'.png',out),Buffer.from(shot.data,'base64'));
 await evaluate("document.querySelector('#close-story').click()");
 if(replay===1){
  await evaluate("document.querySelectorAll('.game')[3].click();document.querySelector('#choices button').click()");
  await choice(2);
  assert.equal(await evaluate("localStorage.getItem('jarvis_playhouse_would_v1')"),null);
  await evaluate("document.querySelector('#close-story').click()");
 }
 return {winner:state.names[winnerSeat],language:lang,score:state.players.map(player=>player.score),rounds:state.history.length};
}
async function importPack(raw) {
 const data = JSON.stringify(raw);
 await evaluate(`(() => {
   const input = document.querySelector('#choices input[type=file]');
   const transfer = new DataTransfer();
   transfer.items.add(new File([${data}], 'pack.json', {type:'application/json'}));
   input.files = transfer.files;
   input.dispatchEvent(new Event('change'));
 })()`);
}
try{
 await new Promise(r=>server.listen(8876,'127.0.0.1',r));
 context=(await browser.send('Target.createBrowserContext')).browserContextId;
 const {targetId}=await browser.send('Target.createTarget',{url:'about:blank',browserContextId:context});
 const pages=await (await fetch('http://127.0.0.1:9337/json')).json();
 c=await connect(pages.find(p=>p.id===targetId).webSocketDebuggerUrl);
 await c.send('Page.enable');await c.send('Runtime.enable');await c.send('Network.enable');
 await c.send('Network.setCacheDisabled',{cacheDisabled:true});
 await c.send('Page.navigate',{url:'http://127.0.0.1:8876/games.html'});
 await until('!!navigator.serviceWorker.controller');
 await until('Promise.all('+JSON.stringify(assets)+'.map(p=>caches.match(p))).then(a=>a.every(Boolean))');
 assert.deepEqual(await evaluate("fetch('/api/playhouse/probe').then(r=>r.json())"),{fixture:true});
 assert.equal(await evaluate("caches.match('/api/playhouse/probe').then(Boolean)"),false);
 // Closing the origin also blocks network fetches made by the service worker itself.
 server.closeAllConnections();await new Promise(r=>server.close(r));
 await c.send('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
 for(const [width,height] of [[1440,900],[390,844]]){
  await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<600});
  await c.send('Emulation.setTouchEmulationEnabled',{enabled:width<600});
  await evaluate('window.beforeOfflineReload=true');
  await c.send('Page.reload');
  await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
  assert(await evaluate("performance.getEntriesByType('navigation')[0].workerStart>0"),'Reload must be served through the worker');
  assert.equal(await evaluate('navigator.onLine'),false);
  await evaluate("Promise.all(['family','couple','covers','room'].map(n=>new Promise((r,j)=>{const i=new Image();i.onload=r;i.onerror=j;i.src='/games-assets/'+n+'.webp'}))).then(()=>true)");
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  for(let i=0;i<2;i++)assert.equal(await evaluate("document.querySelector('#language').click();document.documentElement.lang"),i===0?'en':'ht');
  const screenshot=await c.send('Page.captureScreenshot',{format:'png'});
  await fs.writeFile(new URL('offline-'+width+'.png',out),Buffer.from(screenshot.data,'base64'));
  const desktop = width === 1440;
  await evaluate("document.querySelector('#start-story').click();document.querySelectorAll('#choices button')[1].click()");
  if (desktop) {
   const invalidFiles = ['{broken', JSON.stringify({version:1}), ' '.repeat(96 * 1024 + 1),
    JSON.stringify({...lantern,title:{ht:'Mistè',en:'<img src=x onerror=window.packRan=true>'}})];
   for (const raw of invalidFiles) {
    await evaluate('window.previousImportInput=document.querySelector("#choices input[type=file]")');
    await importPack(raw);
    await until("document.querySelector('#choices input[type=file]')!==window.previousImportInput");
    assert((await evaluate("document.querySelector('#choices [role=status]')?.textContent")).includes('pa yon pak'));
    assert.equal(await evaluate("!!window.packRan || !!document.querySelector('#story img')"),false);
   }
   await importPack(JSON.stringify({...lantern, id:'lantern-import', title:{ht:'Mistè enpòte',en:'Imported mystery'}}));
   await until("document.querySelector('#story-title').textContent === 'Mistè enpòte'");
  } else {
   await evaluate("document.querySelector('#close-story').click();document.querySelector('#language').click();document.querySelector('#start-story').click();document.querySelectorAll('#choices button')[1].click()");
   assert.equal(await evaluate("document.querySelector('#story-title').textContent"),'Imported mystery');
  }
  await choice(desktop ? 0 : 1);
  await choice(desktop ? 0 : 6);
  const players = desktop ? 2 : 8;
  const lang = desktop ? 'ht' : 'en';
  const clues = lantern.cases[desktop ? 0 : 1].clues.map(clue => clue[lang]);
  const visibleClues = async () => {
   const copy = await evaluate("document.querySelector('#story-copy').textContent");
   return clues.flatMap((clue,index) => copy.includes(clue) ? [index] : []);
  };
  const seen = new Set();
  for(let i=0;i<players;i++){
   assert.equal(await evaluate("document.querySelector('#choices button').textContent"),desktop?'Mwen pare':'I’m ready');
   assert.deepEqual(await visibleClues(), []);
   await choice(0);
   const revealed = await visibleClues();
   assert.equal(revealed.length, 8 / players);
   for (const clue of revealed) {
    assert(!seen.has(clue), 'A clue was shown to two different players');
    seen.add(clue);
   }
   if (i === 0) {
    await evaluate("document.querySelector('#close-story').click()");
    assert.deepEqual(await visibleClues(), []);
    await evaluate("document.querySelector('#start-story').click();document.querySelectorAll('#choices button')[1].click()");
    await choice(0);
    assert.deepEqual(await visibleClues(), []);
    await choice(0);
    assert.deepEqual(await visibleClues(), revealed);
   }
   assert((await evaluate("document.querySelector('#story-copy').textContent")).includes(desktop?'Pataje':'Share what'));
   await choice(0);
  }
  assert.equal(seen.size, 8);
  assert.deepEqual(await visibleClues(), []);
  const path = desktop ? [1,0,1,0,0,0] : [0,1,0,0,0,0];
  for(let round=0;round<6;round++){
   await choice(0);
   for(let i=0;i<players;i++){
    await choice(0);
    await choice(path[round]);
    if(desktop && round===0 && i===0){
     await evaluate('window.beforeOfflineReload=true');
     await c.send('Page.reload');
     await until("!window.beforeOfflineReload && !!document.querySelector('.preview-status')");
     await evaluate("document.querySelector('#start-story').click();document.querySelectorAll('#choices button')[1].click()");
     await choice(0);
     assert.equal(await evaluate("document.querySelector('#story-title').textContent"),'Jwè 2');
     assert.equal(await evaluate("document.querySelectorAll('#choices button').length"),1);
    }
   }
   await choice(0);
  }
  assert.equal(await evaluate("document.querySelector('#story-title').textContent"),
   desktop?'Lantèn nan klere ankò':'A festival in the rain');
  const ending = await c.send('Page.captureScreenshot',{format:'png'});
  await fs.writeFile(new URL('mystery-ending-'+width+'.png',out),Buffer.from(ending.data,'base64'));
  if(!desktop) await evaluate("document.querySelector('#close-story').click();document.querySelector('#language').click();document.querySelector('#start-story').click();document.querySelectorAll('#choices button')[1].click()");
  await evaluate("document.querySelector('#close-story').click();document.querySelectorAll('.game')[3].click();document.querySelectorAll('#choices button')[1].click();document.querySelector('#choices button').click()");
  for(let i=0;i<5;i++)await evaluate("document.querySelector('#choices button').click();document.querySelector('#choices button').click();document.querySelectorAll('#choices button')[1].click();document.querySelector('#choices button').click()");
  assert.equal(await evaluate("document.querySelector('#chapter').textContent"),'Pati a fini');
  await evaluate("document.querySelector('#close-story').click()");
  assert.equal(await evaluate("fetch('/api/playhouse/probe').then(()=>false,()=>true)"),true);
  assert.equal(await evaluate("caches.match('/api/playhouse/probe').then(Boolean)"),false);
 assert.equal(await evaluate("fetch('/imposter.html').then(r=>r.ok&&r.text()).then(t=>t.includes('Trust Nobody'))"),true);
 }
 const firstRivalry = await rivalryMatch(0,'ht',0);
 await evaluate("document.querySelector('#language').click()");
 const secondRivalry = await rivalryMatch(1,'en',1);
 await evaluate("document.querySelector('#language').click()");
 const thirdRivalry = await rivalryMatch(2,'ht',2);
 const rivalry = [firstRivalry,secondRivalry,thirdRivalry];
 const firstKnow=await knowMatch(0,'ht');
 await evaluate("document.querySelector('#language').click()");
 const secondKnow=await knowMatch(1,'en');
 assert.deepEqual(await evaluate("({lang:document.documentElement.lang,open:document.querySelector('#story').classList.contains('open'),inert:document.querySelector('.shell').inert})"),{lang:'en',open:false,inert:false});
 await evaluate("document.querySelector('#language').click()");
 assert.equal(await evaluate('document.documentElement.lang'),'ht');
 const know=[firstKnow,secondKnow];
 const firstBluff=await bluffMatch(0,'ht');
 await evaluate("document.querySelector('#language').click()");
 const secondBluff=await bluffMatch(1,'en');
 await evaluate("document.querySelector('#language').click()");
 const bluff=[firstBluff,secondBluff];
 const firstWould=await wouldMatch(0,'ht');
 await evaluate("document.querySelector('#language').click()");
 const secondWould=await wouldMatch(1,'en');
 await evaluate("document.querySelector('#language').click()");
 const would=[firstWould,secondWould];
 const firstTelephone=await telephoneMatch(2,'quick','ht');
 await evaluate("document.querySelector('#language').click()");
 const secondTelephone=await telephoneMatch(8,'standard','en');
 await evaluate("document.querySelector('#language').click()");
 const telephone=[firstTelephone,secondTelephone];
 // Permission and offline failures must offer recovery, never a fake room.
 await evaluate("document.querySelector('.mobile-nav button:last-child').click();document.querySelector('#choices button').click()");
 await until("document.querySelector('#choices [role=status]')?.textContent.includes('konekte aparèy')");
 await evaluate("localStorage.setItem('jarvis_token','playhouse-offline-fixture');document.querySelector('#choices button').click()");
 await until("document.querySelector('#choices [role=status]')?.textContent.includes('Verifye koneksyon')");
 assert.equal(await evaluate("!!document.querySelector('#room-invite-code')"),false);
 const failure=await c.send('Page.captureScreenshot',{format:'png'});
 await fs.writeFile(new URL('offline-room-error.png',out),Buffer.from(failure.data,'base64'));
 await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
 assert(await evaluate("document.activeElement===document.querySelector('.mobile-nav button:last-child')"));
 assert.equal(c.errors.length,0,JSON.stringify(c.errors));
 const report={passed:true,serverStopped:true,httpCacheDisabled:true,viewports:2,assetsCached:assets.length,localGamesCompleted:13,imposterShellCached:true,apiNeverCached:true,roomFailuresRecoverable:true,errors:c.errors.length,rivalry,know,bluff,would,telephone,
  mystery:{players:[2,8],languages:['ht','en'],routes:['west','east'],endings:['lantern','small festival'],distinctPrivateClues:8,hiddenOnClose:true,midBallotResume:true,rejectedFiles:4}};
 await fs.writeFile(new URL('offline-browser-results.json',out),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
}finally{
 server.closeAllConnections();server.close();c?.ws.close();
 if(context)await browser.send('Target.disposeBrowserContext',{browserContextId:context});
 browser.ws.close();
}
