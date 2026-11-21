import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import http from 'node:http';
const server=http.createServer(async(req,res)=>{
 try {
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(!/^\/games(?:\.html|\.css|-ui\.mjs|-story\.(?:mjs|json)|-party\.mjs|-rooms\.mjs|-(?:mystery|rivalry|know|bluff|would|telephone)(?:-ui)?\.mjs|-lantern\.json|-assets\/(?:family|couple|covers|room)\.webp)$/.test(pathname)){res.writeHead(404).end();return}
  const bytes=await fs.readFile(new URL('../app'+pathname,import.meta.url));
  const mime=pathname.endsWith('.webp')?'image/webp':pathname.endsWith('.css')?'text/css':pathname.endsWith('.json')?'application/json':pathname.endsWith('.mjs')?'text/javascript':'text/html';
  res.writeHead(200,{'Content-Type':mime+'; charset=utf-8'}).end(bytes);
 }catch{res.writeHead(404).end()}
});
await new Promise(r=>server.listen(8874,'127.0.0.1',r));
const out = new URL('../data/playhouse-evidence/', import.meta.url);
await fs.mkdir(out,{recursive:true});
const tab=await (await fetch('http://127.0.0.1:9337/json/new?about:blank',{method:'PUT'})).json();
assert(tab?.webSocketDebuggerUrl,'Dedicated test tab missing');
const ws=new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
let sequence=0;const pending=new Map(),errors=[],results=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(!p)return;pending.delete(m.id);clearTimeout(p.timeout);m.error?p.reject(m.error):p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params)};
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Timed out: '+method));},10000);pending.set(id,{resolve,reject,timeout});ws.send(JSON.stringify({id,method,params}))});
const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value};
try {
 await send('Runtime.enable');await send('Page.enable');
 for(const [width,height] of [[1440,900],[1920,1080],[360,800],[390,844],[430,932]]){
  await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<600});
  await send('Emulation.setTouchEmulationEnabled',{enabled:width<600});
  await evaluate('window.beforePlayhouseNavigation=true');
  await send('Page.navigate',{url:'http://127.0.0.1:8874/games.html'});
  for(let i=0;i<60;i++){if(await evaluate("!window.beforePlayhouseNavigation && !!document.querySelector('.preview-status')"))break;await new Promise(r=>setTimeout(r,100))}
  assert.equal(await evaluate('!!window.beforePlayhouseNavigation'),false,'Wait for fresh navigation');
  assert(await evaluate("!!document.querySelector('.preview-status')"),JSON.stringify({page:await evaluate("({url:location.href,text:document.body.innerText.slice(0,400)})"),errors}));
  await evaluate("Promise.all(['family','couple','covers','room'].map(n=>new Promise((r,j)=>{const i=new Image();i.onload=r;i.onerror=j;i.src='/games-assets/'+n+'.webp'}))).then(()=>true)");
  const layout=await evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth,hero:document.querySelector('.hero').getBoundingClientRect().toJSON(),couple:document.querySelector('.love-card').getBoundingClientRect().toJSON()})");
  assert(layout.scroll<=width,'Horizontal overflow');
  assert(width<600?layout.couple.top>=layout.hero.bottom:Math.abs(layout.couple.top-layout.hero.top)<2,'Hero arrangement');
  const shot=await send('Page.captureScreenshot',{format:'png'});
  await fs.writeFile(new URL(width+'x'+height+'.png',out),Buffer.from(shot.data,'base64'));
  for(let n=0;n<4;n++)assert.equal(await evaluate("document.querySelector('#language').click();document.documentElement.lang"),n%2===0?'en':'ht');
  assert.equal(await evaluate("document.querySelector('#start-story').click();document.querySelectorAll('#choices button')[1].click();document.querySelector('#story-title').textContent"),'Mistè Lantèn ki Disparèt la');
  assert(await evaluate("document.querySelector('.shell').inert"));
  await evaluate("document.querySelector('#close-story').focus()");
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:8});
  assert(await evaluate("document.activeElement===document.querySelector('#choices button:last-child')"),'Shift-Tab stays in dialog');
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
  assert.equal(await evaluate('document.activeElement.id'),'close-story');
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  assert.equal(await evaluate("document.activeElement.id"),'start-story');
  assert.equal(await evaluate("document.querySelectorAll('.game')[5].click();document.querySelector('#story-title').textContent"),'Ap vini');
  await evaluate("document.querySelector('#close-story').click()");
  await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.game')).transitionDuration"),'0s');
  await send('Emulation.setEmulatedMedia',{features:[]});
  assert(await evaluate("[...document.querySelectorAll('button')].filter(b=>b.getClientRects().length).every(b=>b.getBoundingClientRect().height>=44)"),'Visible touch targets at least 44px high');
  const bluff=await evaluate(`(()=>{
    const started=performance.now();
    document.querySelectorAll('.game')[2].click();
    [...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['Nia','Jo'][i]);
    for(let i=0;i<6;i++)document.querySelector('#choices button').click();
    return {body:document.querySelector('#story-copy').textContent,ms:performance.now()-started};
  })()`);
  assert(bluff.body.includes('+2 pwen'),'Bluff result has earned score');
  await evaluate("document.querySelector('#choices button').click()");
  assert((await evaluate("document.querySelector('#chapter').textContent")).includes('2 / 12'));
  await evaluate("document.querySelector('#close-story').click();document.querySelectorAll('.game')[3].click();document.querySelectorAll('#choices button')[1].click();document.querySelector('#choices button').click()");
  for(let round=0;round<5;round++){
    await evaluate("document.querySelector('#choices button').click()");
    assert.equal(await evaluate("document.querySelector('#chapter').textContent"),'Pase aparèy la');
    await evaluate("document.querySelector('#choices button').click();document.querySelectorAll('#choices button')[1].click()");
    assert((await evaluate("document.querySelector('#story-copy').textContent")).includes('Egalite!'));
    await evaluate("document.querySelector('#choices button').click()");
  }
  assert.equal(await evaluate("document.querySelector('#chapter').textContent"),'Pati a fini');
  if(width===390){
    const resultShot=await send('Page.captureScreenshot',{format:'png'});
    await fs.writeFile(new URL('vote-results-mobile.png',out),Buffer.from(resultShot.data,'base64'));
  }
  await evaluate("document.querySelector('#close-story').click()");
  await evaluate("localStorage.removeItem('jarvis_playhouse_rivalry_v1');document.querySelector('#start-story').click();document.querySelector('#choices button').click();[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['WWWWWWWWWWWWWWWWWWWW','Jo'][i]);document.querySelector('#choices button').click()");
  assert.equal(await evaluate("document.querySelector('#choices button').textContent"),'Mwen pare');
  await evaluate("document.querySelector('#choices button').click()");
  const rivalryLayout=await evaluate("(() => {const card=document.querySelector('.story-card');const text=document.querySelector('#story-copy').getBoundingClientRect();const view=card.getBoundingClientRect();return {scroll:document.documentElement.scrollWidth,cardScroll:card.scrollTop,cardWidth:card.clientWidth,cardContentWidth:card.scrollWidth,textTop:text.top,viewTop:view.top,viewBottom:view.bottom,buttons:[...document.querySelectorAll('#choices button')].map(b=>b.getBoundingClientRect().height)}})()");
  assert(rivalryLayout.scroll<=width,'Rivalry horizontal overflow');
  assert(rivalryLayout.cardContentWidth<=rivalryLayout.cardWidth,'Long player name must fit the dialog');
  assert(rivalryLayout.buttons.every(height=>height>=44),'Rivalry touch targets');
  if(width===390){
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const rivalryShot=await send('Page.captureScreenshot',{format:'png'});
    await fs.writeFile(new URL('rivalry-brief-mobile.png',out),Buffer.from(rivalryShot.data,'base64'));
  }
  await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#choices button')).transitionDuration"),'0s');
  await send('Emulation.setEmulatedMedia',{features:[]});
  await evaluate("document.querySelector('#close-story').click()");
  await evaluate("localStorage.removeItem('jarvis_playhouse_know_v1');document.querySelectorAll('.game')[1].click();[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['WWWWWWWWWWWWWWWWWWWW','Jo'][i]);document.querySelector('#choices button').click();document.querySelector('#choices button').click()");
  if(!(await evaluate("document.querySelector('#story-title').textContent.includes('WWWW')")))
    await evaluate("for(let i=0;i<6;i++)document.querySelector('#choices button').click()");
  assert(await evaluate("document.querySelector('#story-title').textContent.includes('WWWW')"));
  const knowLayout=await evaluate("(() => {const card=document.querySelector('.story-card');return {scroll:document.documentElement.scrollWidth,cardWidth:card.clientWidth,cardContentWidth:card.scrollWidth,buttons:[...document.querySelectorAll('#choices button')].map(b=>b.getBoundingClientRect().height)}})()");
  assert(knowLayout.scroll<=width && knowLayout.cardContentWidth<=knowLayout.cardWidth,'Know Me long name must fit');
  assert(knowLayout.buttons.every(height=>height>=44),'Know Me touch targets');
  if(width===390){
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const knowShot=await send('Page.captureScreenshot',{format:'png'});
    await fs.writeFile(new URL('know-question-mobile.png',out),Buffer.from(knowShot.data,'base64'));
  }
  await evaluate("document.querySelector('#close-story').click()");
  await evaluate("document.querySelectorAll('.game')[3].click();document.querySelector('#choices button').click();[...document.querySelectorAll('#choices input.rivalry-input')].forEach((input,i)=>input.value=['WWWWWWWWWWWWWWWWWWWW','Jo'][i]);document.querySelector('#choices button').click();document.querySelector('#choices button').click()");
  const wouldLayout=await evaluate("(() => {const card=document.querySelector('.story-card');return {scroll:document.documentElement.scrollWidth,cardWidth:card.clientWidth,cardContentWidth:card.scrollWidth,buttons:[...document.querySelectorAll('#choices button')].map(b=>b.getBoundingClientRect().height)}})()");
  assert(wouldLayout.scroll<=width && wouldLayout.cardContentWidth<=wouldLayout.cardWidth,'Would You Rather dialog width');
  assert(wouldLayout.buttons.every(height=>height>=44),'Would You Rather touch targets');
  if(width===390){
    await evaluate('new Promise(resolve=>setTimeout(resolve,250))');
    const wouldShot=await send('Page.captureScreenshot',{format:'png'});
    await fs.writeFile(new URL('would-choice-mobile.png',out),Buffer.from(wouldShot.data,'base64'));
  }
  await evaluate("document.querySelector('#close-story').click()");
  await evaluate("document.querySelectorAll('.game')[4].click();document.querySelectorAll('#choices select')[1].value='3';document.querySelectorAll('#choices select')[1].dispatchEvent(new Event('change'));[...document.querySelectorAll('.telephone-names input')].forEach((input,i)=>input.value=['Ana','Ben','Cleo'][i]);[...document.querySelectorAll('#choices button')].find(b=>b.textContent.includes('Kòmanse jwèt')).click()");
  assert.equal(await evaluate("document.querySelector('#story-title').textContent"),'Ana');
  assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes('kabrit'),'Telephone prompt is covered');
  await evaluate("document.querySelector('#choices button').click();document.querySelector('.telephone-text').value='Yon kabrit danse sou Lalin';[...document.querySelectorAll('#choices button')].at(-1).click()");
  assert(!(await evaluate("document.querySelector('#story-copy').textContent")).includes('kabrit'),'Telephone previous prompt is covered');
  await evaluate("document.querySelector('#close-story').click();document.querySelectorAll('.game')[4].click();[...document.querySelectorAll('#choices button')].find(b=>b.textContent.includes('Kontinye')).click();document.querySelector('#choices button').click()");
  assert((await evaluate("document.querySelector('.telephone-context').textContent")).includes('kabrit'),'Telephone resume reveals only immediate prompt');
  const drawingBox=await evaluate("document.querySelector('.telephone-canvas').getBoundingClientRect().toJSON()");
  for(const [type,x,y,buttons] of [['mousePressed',.2,.2,1],['mouseMoved',.4,.4,1],['mouseReleased',.6,.6,0]])await send('Input.dispatchMouseEvent',{type,x:drawingBox.x+drawingBox.width*x,y:drawingBox.y+drawingBox.height*y,button:'left',buttons,clickCount:1});
  assert(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.length===1"),'Telephone stroke saved');
  await evaluate("document.querySelector('.telephone-controls button').click()");
  assert(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.length===0"),'Telephone undo');
  for(const [type,x,y,buttons] of [['mousePressed',.2,.2,1],['mouseMoved',.4,.4,1],['mouseReleased',.6,.6,0]])await send('Input.dispatchMouseEvent',{type,x:drawingBox.x+drawingBox.width*x,y:drawingBox.y+drawingBox.height*y,button:'left',buttons,clickCount:1});
  await evaluate("document.querySelectorAll('.telephone-controls button')[1].click()");
  assert(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.length===0"),'Telephone clear');
  await evaluate("document.querySelector('.telephone-controls button').click();document.querySelectorAll('.telephone-toolbar .telephone-tool')[1].click()");
  for(const [type,x,y,buttons] of [['mousePressed',.4,.2,1],['mouseMoved',.5,.4,1],['mouseReleased',.6,.6,0]])await send('Input.dispatchMouseEvent',{type,x:drawingBox.x+drawingBox.width*x,y:drawingBox.y+drawingBox.height*y,button:'left',buttons,clickCount:1});
  assert(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.at(-1).tool==='erase'"),'Telephone eraser');
  await evaluate("document.querySelector('#language').click()");
  assert((await evaluate("document.querySelector('#story-copy').textContent")).includes('Draw the message'),'Telephone language switch');
  await evaluate("document.querySelector('#language').click()");
  assert.equal(await evaluate("JSON.parse(localStorage.getItem('jarvis_playhouse_telephone_v1')).draft.strokes.length"),2,'Telephone strokes survive language switch');
  await evaluate("[...document.querySelectorAll('#choices button')].at(-1).click();document.querySelector('#choices button').click()");
  assert(await evaluate("!!document.querySelector('.telephone-preview')"),'Telephone drawing visible to guesser');
  assert(!(await evaluate("document.querySelector('#choices').textContent")).includes('Yon kabrit danse'),'Guesser cannot see initial prompt');
  await evaluate("document.querySelector('.telephone-text').value='Yon kochon ap vole';[...document.querySelectorAll('#choices button')].at(-1).click()");
  assert.equal(await evaluate("document.querySelectorAll('.telephone-reveal-card').length"),3);
  const telephoneLayout=await evaluate("({scroll:document.documentElement.scrollWidth,cardWidth:document.querySelector('.story-card').clientWidth,cardContentWidth:document.querySelector('.story-card').scrollWidth})");
  assert(telephoneLayout.scroll<=width&&telephoneLayout.cardContentWidth<=telephoneLayout.cardWidth,'Telephone reveal fits viewport');
  if(width===390){await evaluate('new Promise(resolve=>setTimeout(resolve,250))');const shot=await send('Page.captureScreenshot',{format:'png'});await fs.writeFile(new URL('telephone-reveal-mobile.png',out),Buffer.from(shot.data,'base64'));}
  await evaluate("document.querySelector('#close-story').click()");
  if(width===390){
    await evaluate("document.querySelectorAll('.game')[4].click();document.querySelectorAll('#choices select')[1].value='2';document.querySelectorAll('#choices select')[1].dispatchEvent(new Event('change'));[...document.querySelectorAll('.telephone-names input')].forEach((input,i)=>input.value=['Ana','Ben'][i])");
    assert((await evaluate("document.querySelector('#choices').textContent")).includes('JARVIS bay yon sèn sekrè'));
    await evaluate("document.querySelector('#language').click()");
    assert((await evaluate("document.querySelector('#choices').textContent")).includes('JARVIS gives a secret scene'));
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.telephone-names input')].map(input=>input.value)"),['Ana','Ben']);
    await evaluate("document.querySelector('#language').click();[...document.querySelectorAll('#choices button')].find(b=>b.textContent.includes('Kòmanse jwèt')).click()");
    assert(!(await evaluate("document.querySelector('#choices').textContent")).includes('Sèn sekrè pou desine'));
    await evaluate("document.querySelector('#choices button').click()");
    assert(await evaluate("!!document.querySelector('.telephone-canvas')"));
    const duoBox=await evaluate("document.querySelector('.telephone-canvas').getBoundingClientRect().toJSON()");
    const secret=await evaluate("document.querySelector('.telephone-context p').textContent");
    for(const [type,x,y,buttons] of [['mousePressed',.2,.2,1],['mouseMoved',.4,.4,1],['mouseReleased',.6,.6,0]])await send('Input.dispatchMouseEvent',{type,x:duoBox.x+duoBox.width*x,y:duoBox.y+duoBox.height*y,button:'left',buttons,clickCount:1});
    await evaluate("[...document.querySelectorAll('#choices button')].at(-1).click()");
    assert(!(await evaluate("document.querySelector('#choices').textContent")).includes(secret));
    await evaluate("document.querySelector('#choices button').click()");
    assert(!(await evaluate("document.querySelector('#choices').textContent")).includes(secret));
    assert(await evaluate("!!document.querySelector('.telephone-preview')"));
    await evaluate("document.querySelector('.telephone-text').value='Yon chat nan lapli';[...document.querySelectorAll('#choices button')].at(-1).click()");
    assert((await evaluate("document.querySelector('#choices').textContent")).includes(secret));
    assert.equal(await evaluate("document.querySelectorAll('.telephone-reveal-card').length"),3);
    const duoLayout=await evaluate("({scroll:document.documentElement.scrollWidth,cardWidth:document.querySelector('.story-card').clientWidth,cardContentWidth:document.querySelector('.story-card').scrollWidth})");
    assert(duoLayout.scroll<=width&&duoLayout.cardContentWidth<=duoLayout.cardWidth,'Two-player reveal fits phone');
    await evaluate('new Promise(resolve=>setTimeout(resolve,250))');
    const duoShot=await send('Page.captureScreenshot',{format:'png'});
    await fs.writeFile(new URL('telephone-duo-mobile.png',out),Buffer.from(duoShot.data,'base64'));
    await evaluate("document.querySelector('#close-story').click()");
  }
  const metrics=await send('Page.getLayoutMetrics');
  const full=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width,height:metrics.cssContentSize.height,scale:1}});
  await fs.writeFile(new URL(width+'x'+height+'-full.png',out),Buffer.from(full.data,'base64'));
  results.push({viewport:width+'x'+height,layout,rivalryLayout,knowLayout,wouldLayout,telephoneLayout,story:true,language:true,planned:true,escapeFocus:true,keyboardContainment:true,reducedMotion:true,touchTargets:true,bluff,voteRounds:5});
 }
 const isExtensionNoise=entry=>{
  const detail=entry.exceptionDetails||{},description=detail.exception?.description||'';
  return (detail.url||'').startsWith('chrome-extension://') ||
    /(?:Could not establish connection\. Receiving end does not exist\.|The message port closed before a response was received\.|A listener indicated an asynchronous response by returning true, but the message channel closed)/.test(description);
 };
 const appErrors=errors.filter(entry=>!isExtensionNoise(entry));
 assert.equal(appErrors.length,0,JSON.stringify(appErrors));
 await fs.writeFile(new URL('browser-results.json',out),JSON.stringify({passed:true,results,errors:appErrors,ignoredExtensionErrors:errors.length-appErrors.length},null,2));
 console.log(JSON.stringify({passed:true,viewports:results.length,errors:appErrors.length,ignoredExtensionErrors:errors.length-appErrors.length}));
} finally {ws.close();server.closeAllConnections();server.close();await fetch('http://127.0.0.1:9337/json/close/'+tab.id)}
