import {DRAW_COLORS,newTelephone,playerFor,restoreTelephone,roundCount,stageCount,stageKind,starterFor,stepTelephone,validDrawing} from './games-telephone.mjs';

const KEY='jarvis_playhouse_telephone_v1';
const make=(tag,klass,text)=>{const node=document.createElement(tag);if(klass)node.className=klass;if(text!==undefined)node.textContent=text;return node;};
function paint(canvas,strokes){
  const rect=canvas.getBoundingClientRect(),scale=Math.min(devicePixelRatio||1,2);
  canvas.width=Math.round(rect.width*scale);canvas.height=Math.round(rect.height*scale);
  const ctx=canvas.getContext('2d');ctx.setTransform(canvas.width/1000,0,0,canvas.height/1000,0,0);
  ctx.fillStyle='#fff9ec';ctx.fillRect(0,0,1000,1000);
  for(const stroke of strokes){
    ctx.save();
    ctx.strokeStyle=stroke.tool==='erase'?'#fff9ec':stroke.color;ctx.fillStyle=ctx.strokeStyle;
    ctx.lineWidth=stroke.width;ctx.lineCap=ctx.lineJoin='round';
    const points=stroke.points;ctx.beginPath();ctx.moveTo(...points[0]);
    if(points.length===1){ctx.arc(points[0][0],points[0][1],stroke.width/2,0,2*Math.PI);ctx.fill();}
    else {for(const point of points.slice(1))ctx.lineTo(...point);ctx.stroke();}
    ctx.restore();
  }
}
export function installTelephone({say,pick,button,title,body,label,buttons,focus}){
  let state=null,draft=null,uncovered=false,storageFailed=false,menuBack=()=>{},menuVisible=false,liveStroke=null,canvas=null,resizeObserver=null;
  try{const raw=localStorage.getItem(KEY);if(raw&&raw.length<900000){const save=JSON.parse(raw);state=restoreTelephone(save);draft=save.draft||null;}}catch{state=null;draft=null;}
  function stop(){uncovered=false;menuVisible=false;liveStroke=null;resizeObserver?.disconnect();resizeObserver=null;canvas=null;buttons.replaceChildren();body.textContent='';}
  function save(){
    if(!state)return;
    try{localStorage.setItem(KEY,JSON.stringify({names:state.names,length:state.length,seed:state.seed,variant:state.variant,actions:state.actions,draft}));storageFailed=false;}
    catch{storageFailed=true;}
  }
  function act(action){state=stepTelephone(state,action);draft=null;uncovered=false;save();render();}
  function menu(back){
    stop();menuVisible=true;menuBack=back;label.textContent=say('Jwèt desen · 2–8 jwè','Drawing game · 2–8 players');title.textContent=say('Telefòn Fou','Drawing Telephone');
    body.textContent=say('Pou 3–8 moun: youn ekri yon lide, pwochen an desine, epi pwochen an devine. Pou de moun: jwèt la bay yon lide sekrè; youn desine, lòt la devine, epi nou chanje wòl. Pase telefòn nan an prive.',
      'For 3–8 people: write a prompt, pass it to a drawer, then pass the drawing to a guesser. With two: the game gives a secret prompt, one draws, the other guesses, then you swap. Pass the phone privately.');
    const lengthLabel=make('label','telephone-field',say('Dire jwèt la','Game length'));
    const select=make('select','rivalry-input');
    for(const [value,ht,en] of [['quick','Rapid · 1 chèn · 5–10 min','Quick · 1 chain · 5–10 min'],['standard','Nòmal · 2 chèn · 10–20 min','Standard · 2 chains · 10–20 min']]){
      const option=make('option','',say(ht,en));option.value=value;select.append(option);
    }
    select.value=state?.length||'quick';lengthLabel.append(select);buttons.append(lengthLabel);
    const countLabel=make('label','telephone-field',say('Konbyen jwè?','How many players?'));
    const count=make('select','rivalry-input');for(let n=2;n<=8;n++){const option=make('option','',`${n}`);option.value=n;count.append(option);}count.value=state?.names.length||3;countLabel.append(count);buttons.append(countLabel);
    const fields=make('div','telephone-names');buttons.append(fields);
    const how=make('p','telephone-explain');buttons.append(how);
    function names(){const duo=Number(count.value)===2;
      select.options[0].textContent=duo?say('Rapid · 2 desen · 5–10 min','Quick · 2 sketches · 5–10 min'):say('Rapid · 1 chèn · 5–10 min','Quick · 1 chain · 5–10 min');
      select.options[1].textContent=duo?say('Nòmal · 4 desen · 10–20 min','Standard · 4 sketches · 10–20 min'):say('Nòmal · 2 chèn · 10–20 min','Standard · 2 chains · 10–20 min');
      how.textContent=duo?say('De moun: JARVIS bay yon sèn sekrè. Premye moun nan desine; dezyèm nan devine. Nou gade repons lan, epi nou chanje wòl.',
        'Two players: JARVIS gives a secret scene. First player draws; second guesses. Reveal the answer, then swap roles.'):
        say('3–8 moun: ekri → desine → devine. Chak moun wè sèlman dènye kontribisyon an.','3–8 players: write → draw → guess. Each person sees only the previous contribution.');
      fields.replaceChildren();for(let i=0;i<Number(count.value);i++){
      const item=make('label','telephone-field',say('Jwè ','Player ')+(i+1));const input=make('input','rivalry-input');
      input.type='text';input.maxLength=20;input.autocomplete='off';input.value=state?.names[i]||'';item.append(input);fields.append(item);
    }}count.onchange=names;names();
    const error=make('p','telephone-error');error.setAttribute('role','status');buttons.append(error);
    button(say('Kòmanse jwèt la','Start game'),()=>{
      try{const seed=new Uint32Array(1);crypto.getRandomValues(seed);state=newTelephone([...fields.querySelectorAll('input')].map(input=>input.value),select.value,seed[0]);}
      catch{error.textContent=say('Mete 2–8 non diferan.','Enter 2–8 different names.');return;}
      menuVisible=false;draft=null;save();render();
    });
    if(state){button(state.variant==='legacy'?say('Kontinye ansyen jwèt 2 moun nan','Resume older 2-player game'):say('Kontinye jwèt ki sove a','Resume saved game'),()=>{menuVisible=false;uncovered=false;render();});
      button(say('Efase jwèt ki sove a','Delete saved game'),()=>{try{localStorage.removeItem(KEY);}catch{}state=null;draft=null;menu(back);});}
    button(say('Tounen nan jwèt yo','Back to games'),back);focus();
  }
  function card(entry){
    const wrap=make('div','telephone-reveal-card');
    const name=entry.player===null?'JARVIS':state.names[entry.player];wrap.append(make('strong','',name+' · '+say(entry.kind==='drawing'?'Desen':entry.kind==='prompt'?'Lide':'Devinèt',entry.kind==='drawing'?'Drawing':entry.kind==='prompt'?'Prompt':'Guess')));
    if(entry.kind==='drawing'){const view=make('canvas','telephone-preview');view.setAttribute('aria-label',say('Desen ','Drawing by ')+name);wrap.append(view);requestAnimationFrame(()=>paint(view,entry.strokes));}
    else wrap.append(make('p','',typeof entry.text==='string'?entry.text:pick(entry.text)));return wrap;
  }
  function handoff(){
    const name=state.names[playerFor(state)],kind=stageKind(state.stage,state.variant);
    title.textContent=name;body.textContent=say('Pase telefòn nan bay ','Pass the phone to ')+name+'.\n'+say('Lòt moun yo vire do yo. Travay sa a rete sekrè jiskaske nou revele chèn nan.',
      'Everyone else looks away. This turn stays secret until the group reveal.');
    label.textContent=say(state.variant==='duo'?'Desen ':'Tou ',state.variant==='duo'?'Sketch ':'Chain ')+(state.round+1)+' / '+roundCount(state)+' · '+say(kind==='drawing'?'Desine':kind==='prompt'?'Ekri lide':'Devine',kind==='drawing'?'Draw':kind==='prompt'?'Write a prompt':'Guess');
    button(say('Mwen pare · montre m','I’m ready · show me'),()=>{uncovered=true;act('ready');uncovered=true;render();});
  }
  function entry(){
    const kind=stageKind(state.stage,state.variant),previous=state.entries.at(-1),name=state.names[playerFor(state)];
    title.textContent=name;label.textContent=say(state.variant==='duo'?'Desen ':'Tou ',state.variant==='duo'?'Sketch ':'Chain ')+(state.round+1)+' / '+roundCount(state)+' · '+(state.stage+1)+' / '+stageCount(state.names.length,state.variant);
    body.textContent=state.variant==='duo'&&kind==='drawing'?say('JARVIS ba ou sèn sa a an prive. Desine li san lòt jwè a pa wè pawòl yo.',
      'JARVIS gave you this scene privately. Draw it without letting the guesser see the words.'):
      state.variant==='duo'&&kind==='guess'?say('Gade desen an sèlman. Ekri sa ou panse li ye. Apre sa, n ap revele sèn sekrè a.',
        'Look only at the drawing. Write what you think it shows. Then reveal the secret scene.'):
      kind==='prompt'?say('Envante yon sèn etranj. Pwochen jwè a ap desine li san li pa wè rès chèn nan.',
      'Invent a strange scene. The next player will draw it without seeing the rest of the chain.'):
      kind==='drawing'?say('Desine mesaj ki anba a. Ou ka sèvi ak koulè, gonm, ak bouton defè. Pa montre desen an bay lòt moun.',
        'Draw the message below. Use colors, eraser and undo. Keep your drawing hidden from everyone else.'):
      say('Ki sa desen sa a reprezante? Ekri devinèt ou; pwochen jwè a ap wè pawòl ou sèlman.',
        'What does this drawing show? Write your guess; the next player will see only your words.');
    if(state.variant==='duo'&&kind==='drawing'){
      const context=make('div','telephone-context');context.append(make('strong','',say('Sèn sekrè pou desine','Secret scene to draw')),
        make('p','',pick(starterFor(state))));buttons.append(context);
    }
    if(previous){const context=make('div','telephone-context');context.append(make('strong','',say(previous.kind==='drawing'?'Desen pou devine':'Mesaj pou desine',previous.kind==='drawing'?'Drawing to guess':'Message to draw')));
      if(previous.kind==='drawing'){const view=make('canvas','telephone-preview');view.setAttribute('aria-label',say('Desen pou devine','Drawing to guess'));context.append(view);requestAnimationFrame(()=>paint(view,previous.strokes));}
      else context.append(make('p','',previous.text));buttons.append(context);}
    if(kind==='drawing'){drawing();return;}
    const input=make('textarea','telephone-text');input.maxLength=80;input.rows=3;input.placeholder=say('Ekri 3–80 karaktè...','Write 3–80 characters...');
    input.value=draft?.stage===state.stage&&draft?.kind===kind?draft.text:'';
    input.oninput=()=>{draft={stage:state.stage,kind,text:input.value};save();};buttons.append(input);
    if(kind==='prompt'){
      const starters=make('div','telephone-starters');starters.append(make('span','',say('Bezwen yon lide?','Need an idea?')));
      for(let i=0;i<3;i++){const seed=starterFor({...state,seed:state.seed+i});const chip=make('button','telephone-starter',pick(seed));chip.onclick=()=>{input.value=pick(seed);input.oninput();input.focus();};starters.append(chip);}buttons.append(starters);
    }
    const error=make('p','telephone-error');error.setAttribute('role','status');buttons.append(error);
    button(say('Voye an prive','Submit privately'),()=>{try{act({submit:true,text:input.value});}catch{error.textContent=say('Ekri 3–80 karaktè san siy < oswa >.','Write 3–80 characters without < or >.');}});
    input.focus();
  }
  function drawing(){
    let strokes=draft?.stage===state.stage&&draft?.kind==='drawing'&&Array.isArray(draft.strokes)?draft.strokes:[];
    if(strokes.length>80||strokes.reduce((n,s)=>n+(Array.isArray(s.points)?s.points.length:3001),0)>3000)strokes=[];
    const history=[];let tool='pen',color=DRAW_COLORS[0],width=8;
    const toolbar=make('div','telephone-toolbar');buttons.append(toolbar);
    for(const [value,ht,en] of [['pen','✎ Plim','✎ Pen'],['erase','◯ Gonm','◯ Eraser']]){
      const item=make('button','telephone-tool',say(ht,en));item.type='button';item.setAttribute('aria-pressed',String(tool===value));item.onclick=()=>{tool=value;toolbar.querySelectorAll('.telephone-tool').forEach(node=>node.setAttribute('aria-pressed',String(node===item)));};toolbar.append(item);
    }
    for(const shade of DRAW_COLORS){const item=make('button','telephone-swatch');item.type='button';item.style.background=shade;item.setAttribute('aria-label',say('Koulè ','Color ')+shade);item.setAttribute('aria-pressed',String(color===shade));item.onclick=()=>{color=shade;tool='pen';toolbar.querySelectorAll('.telephone-swatch').forEach(node=>node.setAttribute('aria-pressed',String(node===item)));toolbar.querySelectorAll('.telephone-tool').forEach(node=>node.setAttribute('aria-pressed',String(node===toolbar.firstChild)));};toolbar.append(item);}
    const size=make('button','telephone-tool',say('Gwo plim','Thick pen'));size.type='button';size.setAttribute('aria-pressed','false');size.onclick=()=>{width=width===8?18:8;size.setAttribute('aria-pressed',String(width===18));};toolbar.append(size);
    canvas=make('canvas','telephone-canvas');canvas.setAttribute('aria-label',say('Espas pou desine','Drawing canvas'));buttons.append(canvas);
    const current=canvas;const repaint=()=>{if(canvas===current)paint(current,liveStroke?[...strokes,liveStroke]:strokes);};
    requestAnimationFrame(repaint);resizeObserver?.disconnect();resizeObserver=new ResizeObserver(repaint);resizeObserver.observe(current);
    const point=event=>{const box=current.getBoundingClientRect();return [Math.round(Math.max(0,Math.min(1000,(event.clientX-box.left)*1000/box.width))),Math.round(Math.max(0,Math.min(1000,(event.clientY-box.top)*1000/box.height)))];};
    current.onpointerdown=event=>{if(strokes.length>=80||strokes.reduce((n,s)=>n+s.points.length,0)>=2999||liveStroke)return;event.preventDefault();current.setPointerCapture(event.pointerId);liveStroke={tool,color,width,points:[point(event)]};repaint();};
    current.onpointermove=event=>{if(!liveStroke||!current.hasPointerCapture(event.pointerId))return;event.preventDefault();const p=point(event),last=liveStroke.points.at(-1);if((Math.abs(p[0]-last[0])+Math.abs(p[1]-last[1]))>=3&&liveStroke.points.length<240&&strokes.reduce((n,s)=>n+s.points.length,liveStroke.points.length)<2999){liveStroke.points.push(p);repaint();}};
    function finish(event,commit){if(!liveStroke||!current.hasPointerCapture(event.pointerId))return;event.preventDefault();if(commit){const p=point(event);if(liveStroke.points.length<240)liveStroke.points.push(p);strokes=[...strokes,liveStroke];draft={stage:state.stage,kind:'drawing',strokes};save();}liveStroke=null;repaint();}
    current.onpointerup=event=>finish(event,true);current.onpointercancel=event=>finish(event,false);
    const controls=make('div','telephone-controls');buttons.append(controls);
    const undo=make('button','',say('↶ Defè','↶ Undo'));undo.onclick=()=>{if(!strokes.length&&history.length){strokes=history.pop();}else if(strokes.length)strokes=strokes.slice(0,-1);draft={stage:state.stage,kind:'drawing',strokes};save();repaint();};controls.append(undo);
    const clear=make('button','',say('Efase tout','Clear all'));clear.onclick=()=>{if(strokes.length)history.push(strokes);strokes=[];draft={stage:state.stage,kind:'drawing',strokes};save();repaint();};controls.append(clear);
    const error=make('p','telephone-error');error.setAttribute('role','status');buttons.append(error);
    button(say('Voye desen an an prive','Submit drawing privately'),()=>{if(!validDrawing(strokes)){error.textContent=say('Fè yon desen anvan ou voye li.','Draw something before submitting.');return;}act({submit:true,strokes});});
  }
  function reveal(){
    title.textContent=say('Gwo revelasyon an!','The big reveal!');label.textContent=say(state.variant==='duo'?'Desen ':'Chèn ',state.variant==='duo'?'Sketch ':'Chain ')+(state.round+1)+' / '+roundCount(state);
    const duo=state.variant==='duo',first=duo?pick(starterFor(state)):state.entries[0].text,last=state.entries.at(-1).text;
    body.textContent=duo?say('Men sa JARVIS te mande a, ak sa nou te devine. Kounye a, chanje wòl!',
      'Here is the secret scene and the guess. Now swap roles!'):
      first===last?say('Lide a te siviv vwayaj la!','The idea somehow survived!'):
      say('Nou te pati ak: “','We started with: “')+first+'”\n'+say('Nou rive nan: “','We ended at: “')+last+'”';
    const gallery=make('div','telephone-gallery');
    if(duo)gallery.append(card({player:null,kind:'prompt',text:starterFor(state)}));
    for(const item of state.entries)gallery.append(card(item));buttons.append(gallery);
    button(duo&&state.round<roundCount(state)-1?say('Chanje wòl · pwochen desen','Swap roles · next sketch'):
      !duo&&state.length==='standard'&&state.round===0?say('Dezyèm chèn · lòt moun kòmanse','Second chain · new starter'):
        say('Fini epi wè rezilta a','Finish and view results'),()=>act('next'));
  }
  function end(){
    title.textContent=state.variant==='duo'?say('Ki desen ki pi fè nou ri?','Which sketch made you laugh most?'):
      say('Ki chèn ki pi fè nou ri?','Which chain made you laugh most?');label.textContent=say('Jwèt la fini','Game complete');
    body.textContent=state.variant==='duo'?say('Nou chak desine epi devine. Ki sèn ki te pi difisil pou desine?',
      'You both drew and guessed. Which scene was hardest to draw?'):
      say('Chak moun te ajoute yon sipriz. Rakonte ki desen oswa devinèt ki chanje istwa a plis!',
        'Everyone added a surprise. Talk about the drawing or guess that changed the story most!');
    state.chains.forEach(chain=>{buttons.append(make('h3','telephone-round-title',say(state.variant==='duo'?'Desen ':'Chèn ',state.variant==='duo'?'Sketch ':'Chain ')+(chain.round+1)));
      const gallery=make('div','telephone-gallery');if(state.variant==='duo')gallery.append(card({player:null,kind:'prompt',text:chain.prompt}));
      chain.entries.forEach(entry=>gallery.append(card(entry)));buttons.append(gallery);});
    button(say('Jwe yon lòt pati','Play another game'),()=>menu(menuBack));
  }
  function render(){
    if(menuVisible){
      const selects=[...buttons.querySelectorAll('select')].map(node=>node.value);
      const entered=[...buttons.querySelectorAll('.telephone-names input')].map(node=>node.value);
      menu(menuBack);
      const next=buttons.querySelectorAll('select');
      if(selects.length===2){next[0].value=selects[0];next[1].value=selects[1];next[1].dispatchEvent(new Event('change'));}
      buttons.querySelectorAll('.telephone-names input').forEach((node,i)=>node.value=entered[i]||'');
      return;
    }
    resizeObserver?.disconnect();resizeObserver=null;canvas=null;buttons.replaceChildren();
    if(!state)return;
    if(state.phase==='handoff')handoff();else if(state.phase==='entry'&&uncovered)entry();
    else if(state.phase==='entry'){title.textContent=state.names[playerFor(state)];body.textContent=say('Pase telefòn nan bay jwè sa a epi kache ekran an.','Pass the phone to this player and keep the screen covered.');button(say('Mwen pare · montre m','I’m ready · show me'),()=>{uncovered=true;render();});}
    else if(state.phase==='reveal')reveal();else end();
    if(storageFailed)buttons.append(make('p','telephone-error',say('Navigatè a pa t kapab sove jwèt la.','The browser could not save this game.')));
    focus();
  }
  return {menu,stop,render};
}
