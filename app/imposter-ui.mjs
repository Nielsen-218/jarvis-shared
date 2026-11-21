// Imposter — Trust Nobody. The local host decides; this client only renders its own snapshot.
const KEY='jarvis_playhouse_imposter_seat_v1';
const $=selector=>document.querySelector(selector);
const content=$('#content'), notice=$('#notice'), headline=$('#headline'), announcement=$('#announcement');
let lang='ht',seat=null,room=null,pollTimer=null,busy=false,roleOpen=false,phaseKey='',draftTarget=null,draftUse=false,lastCue='',timerAt=0,timerSeconds=null;
try{if(localStorage.getItem('jarvis_playhouse_language')==='en')lang='en';seat=JSON.parse(localStorage.getItem(KEY)||'null');}catch{}
const say=(ht,en)=>lang==='ht'?ht:en;
const talkPrompts=[
  ['Bay yon alibi ak senk mo sèlman. Kiyès ki sonnen pi sispèk?','Give a five-word alibi. Who sounds most suspicious?'],
  ['Chak moun envante yon bagay yo jwenn bò kote sèn nan. Lòt moun yo ka poze yon kesyon.','Everyone invents one object found near the scene. The others may ask one question.'],
  ['Akize yon moun san w pa sèvi ak non li. Kite gwoup la devine ki moun ou vle di.','Accuse someone without saying their name. Let the group guess whom you mean.'],
  ['Defann moun ou plis sispèk la pandan dis segonn. Kisa ki pa mache nan defans ou?','Defend the player you suspect most for ten seconds. What breaks in your defense?'],
  ['Di ki kote w te ye lè limyè yo te etenn. Pèsonn pa ka repete menm kote a.','Say where you were when the lights went out. Nobody may repeat a place.'],
  ['Ki moun ki ta ka kache yon elefan nan salon an? Bay yon rezon serye.','Who could hide an elephant in the living room? Give a serious reason.'],
  ['Poze yon sèl kesyon ki ka fè yon moun chanje istwa li.','Ask one question that might make somebody change their story.'],
  ['Chak moun bay yon manti ridikil sou lannwit lan. Ki manti ki sonnen twò byen?','Everyone tells one ridiculous lie about the night. Which lie sounds too polished?'],
  ['Chwazi yon moun pou rakonte sa li tande. Yon lòt moun dwe di poukisa sa pa posib.','Choose someone to say what they heard. Another player explains why that cannot be true.'],
  ['Ki moun ki ta pi bon detektif la? Mande l poze premye kesyon an.','Who would make the best detective? Ask them to pose the first question.'],
  ['Bay de vèsyon alibi ou. Gwoup la chwazi sa ki pi difisil pou kwè.','Give two versions of your alibi. The group chooses the harder one to believe.'],
  ['Si sèn nan te yon fim, kiyès ki ta move pèsonaj sipriz la? Poukisa?','If this were a movie, who would be the surprise villain? Why?'],
];
const node=(tag,klass,text)=>{const item=document.createElement(tag);if(klass)item.className=klass;if(text!==undefined)item.textContent=text;return item;};
function addButton(text,callback,klass=''){const item=node('button',klass,text);item.type='button';item.onclick=callback;content.append(item);return item;}
function field(label,value='',kind='text'){const box=node('label','field',label),input=node('input');input.type=kind;input.value=value;input.autocomplete='off';box.append(input);content.append(box);return input;}
function remember(value){seat=value;try{value?localStorage.setItem(KEY,JSON.stringify(value)):localStorage.removeItem(KEY);}catch{}}
function warning(message){notice.textContent=message;}
const friendly={
  room_unavailable:['Salon an fèmen oswa li ekspire.','The room closed or expired.'],
  invalid_code:['Kòd la pa valab. Verifye sis karaktè yo.','Invalid code. Check all six characters.'],
  name_taken:['Non sa a deja nan salon an.','That name is already in the room.'],
  room_full:['Salon an plen.','The room is full.'],
  already_started:['Jwèt la deja kòmanse.','The game has already started.'],
  need_five_players:['Nou bezwen omwen senk jwè.','At least five players are needed.'],
  unbalanced_roles:['Detektif ak Gadyen bezwen omwen sèt jwè.','Detective and Guardian require at least seven players.'],
  stale_phase:['Moman jwèt la chanje; n ap mete ekran an ajou.','The phase changed; refreshing your screen.'],
  choice_locked:['Chwa sa a deja sele.','That choice is already sealed.'],
  vote_locked:['Vòt ou deja sele.','Your vote is already sealed.'],
  invalid_target:['Chwazi yon lòt jwè ki toujou nan jwèt la.','Choose another living player.'],
  invalid_name:['Ekri yon non 1–20 karaktè san siy < oswa >.','Enter a 1–20 character name without < or >.'],
  too_many_attempts:['Twòp tantativ; tann yon minit.','Too many attempts; wait a minute.'],
  room_limit:['Gen twòp salon ouvè kounye a.','Too many rooms are open right now.'],
  host_only:['Se moun k ap òganize a sèlman ki ka fè sa.','Only the host can do that.'],
  invalid_settings:['Règ sa yo pa valab. Verifye tan yo.','Invalid settings. Check the timers.'],
  game_started:['Pati a deja kòmanse.','The match already started.'],
  wrong_phase:['Etap sa a fini. Ekran an ap mete ajou.','That phase ended. Refreshing the screen.'],
  invalid_token:['Jeton sa a deja itilize oswa li pa valab.','That token is used or invalid.'],
  invalid_confirmation:['Konfime oswa retire chwa a.','Confirm or withdraw your choice.'],
  invalid_prediction:['Prediksyon an deja sele oswa li pa valab.','That prediction is already sealed or invalid.'],
  seat_token_required:['Retounen nan salon ou a pou rekonekte.','Resume your room to reconnect.'],
};
function errorText(error){return friendly[error.message]?say(...friendly[error.message]):
  say('Pa gen koneksyon ak òdinatè ki òganize a. Rete sou menm rezo lokal la epi eseye ankò.',
    'Cannot reach the host computer. Stay on the same local network and try again.');}
async function request(path,data,withSeat=true){
  const response=await fetch('/api/playhouse/imposter/'+path,{method:data===undefined?'GET':'POST',
    headers:{'Content-Type':'application/json',...(withSeat&&seat?{'Authorization':'Bearer '+seat.token}:{})},
    body:data===undefined?undefined:JSON.stringify(data),cache:'no-store',signal:AbortSignal.timeout(8000)});
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw new Error(typeof detail==='string'?detail:'unavailable');}
  return response.json();
}
async function create(name){
  if(busy)return;busy=true;warning('');
  try{const result=await request('rooms',{name:name.trim()},false);remember({id:result.room.id,token:result.token});update(result.room);schedule();}
  catch(error){warning(errorText(error));}finally{busy=false;}
}
async function join(code,name){
  if(busy)return;busy=true;warning('');
  try{const result=await request('join',{code:code.trim().toUpperCase(),name:name.trim()},false);remember({id:result.room.id,token:result.token});update(result.room);schedule();}
  catch(error){warning(errorText(error));}finally{busy=false;}
}
async function refresh(){
  if(!seat)return;
  try{const next=await request('rooms/'+seat.id);update(next);$('#connection').textContent=say('Konekte ak òdinatè ki òganize a · rezo lokal','Connected to local host · LAN');}
  catch(error){
    if(error.message==='room_unavailable'){remember(null);room=null;clearTimeout(pollTimer);renderLanding();}
    $('#connection').textContent=errorText(error);
  }
}
function schedule(){clearTimeout(pollTimer);if(!seat)return;pollTimer=setTimeout(async()=>{if(!busy)await refresh();schedule();},document.hidden?2500:850);}
async function action(name,values={}){
  if(busy||!room)return;busy=true;warning('');
  try{
    const next=await request('rooms/'+room.id+'/'+name,{revision:room.revision,...values});
    if(next.closed){remember(null);room=null;clearTimeout(pollTimer);renderLanding();return;}
    update(next);schedule();
  }catch(error){warning(errorText(error));await refresh();}
  finally{busy=false;}
}
function timer(){const display=$('#countdown');if(!room?.deadline){display.textContent='';return;}
  const remaining=Math.max(0,Math.ceil(timerSeconds-(performance.now()-timerAt)/1000));
  display.textContent=remaining+say(' segonn rete',' seconds left');}
setInterval(timer,250);
function cue(kind){
  if(!room?.settings.audio||lastCue===`${room.match}:${room.round}:${kind}`)return;
  lastCue=`${room.match}:${room.round}:${kind}`;
  try{const Context=window.AudioContext||window.webkitAudioContext;if(!Context)return;const audio=new Context();
    const oscillator=audio.createOscillator(),gain=audio.createGain();oscillator.type='sine';
    oscillator.frequency.value=kind==='end'?660:kind==='morning'?440:520;
    gain.gain.setValueAtTime(0.0001,audio.currentTime);gain.gain.exponentialRampToValueAtTime(.08,audio.currentTime+.03);
    gain.gain.exponentialRampToValueAtTime(.0001,audio.currentTime+.25);
    oscillator.connect(gain).connect(audio.destination);oscillator.start();oscillator.stop(audio.currentTime+.26);
    oscillator.onended=()=>audio.close();}catch{}
}
function update(next){
  const before=room;room=next;
  timerAt=performance.now();timerSeconds=room.seconds_left;
  const key=`${room.match}:${room.round}:${room.phase}`;
  if(key!==phaseKey){roleOpen=false;draftTarget=null;draftUse=false;phaseKey=key;}
  const signature=value=>JSON.stringify(value&&{...value,seconds_left:0});
  if(signature(before)!==signature(room))render();
  timer();
  if(before?.phase!==room.phase&&['morning','verdict','end'].includes(room.phase))cue(room.phase);
}
function setHeader(phase,title,copy){$('#phase-label').textContent=phase;headline.textContent=title;announcement.textContent=copy;}
function nameOf(index){return index===null||index===undefined?say('Pèsonn','Nobody'):room.players[index]?.name||'?';}
function roster(){const wrap=node('div','roster');room.players.forEach((person,index)=>{
  const row=node('div','person'+(person.alive?'':' dead'));row.append(node('strong','',person.name+(index===room.seat?' · '+say('ou','you'):'')));
  row.append(node('span','badge',person.alive?say('La','Here'):say('Elimine','Out')));wrap.append(row);});content.append(wrap);}
function roleName(role){return ({imposter:say('Impostè','Imposter'),detective:say('Detektif','Detective'),guardian:say('Gadyen','Guardian'),innocent:say('Inosan','Innocent')})[role]||'?';}
function roleRules(){const role=room.role;
  const rules={
    imposter:["Chwazi yon viktim lannwit. Ekip la ap wè yon sèl sib pou konfime; yon tiraj sekrè rezoud egalite. Plis pase mwatye impostè yo dwe konfime li. Jeton yon sèl fwa a bay yon pwen si atak la mache; si sabotaj aktive, li kache endis piblik la tou.",
      'Choose a victim at night. The team sees one target to confirm; a secret draw breaks nomination ties. More than half the imposters must confirm. Your one-use token earns a point for a successful attack; if sabotage is enabled, it also hides public evidence.'],
    detective:["Yon sèl fwa, sèvi ak jeton an sou yon moun. Nan maten, ou menm sèlman ap wè si li nan ekip impostè a. Ou ka rakonte sa w aprann; pèsonn pa oblije kwè w.",
      'Once per match, use your token on someone. In the morning only you learn whether they are on the imposter team. You may tell the group; they do not have to believe you.'],
    guardian:["Yon sèl fwa, sèvi ak jeton an pou pwoteje moun ou chwazi a kont atak lannwit lan. Ou pa ka pwoteje tèt ou.",
      'Once per match, use your token to protect your chosen player from the night attack. You cannot protect yourself.'],
    innocent:["Chèche enkonpatibilite nan istwa moun yo. Chwazi yon moun ou sispèk lannwit. Si w konfime sispèk ou epi w gen rezon, w ap jwenn pwen; jeton yon sèl fwa a double bonis sa a.",
      'Look for contradictions. Choose someone you suspect at night. A confirmed correct suspicion earns points; your one-use token doubles that bonus.']};
  return say(...rules[role]);}
function roleCard(){const wrap=node('div','role-card');wrap.append(node('strong','',roleName(room.role)),node('p','',roleRules()));
  if(room.role==='imposter'&&room.teammates.length)wrap.append(node('p','',say('Patnè sekrè: ','Secret teammates: ')+room.teammates.map(nameOf).join(', ')));
  content.append(wrap);
}
function peek(){addButton(roleOpen?say('Kache wòl mwen','Hide my role'):say('Gade wòl mwen an prive','Privately check my role'),()=>{roleOpen=!roleOpen;render();},'secondary');if(roleOpen)roleCard();}
function renderLanding(){room=null;phaseKey='';document.documentElement.lang=lang;document.title=say('Imposter — Pa fè pèsonn konfyans · JARVIS Playhouse','Imposter — Trust Nobody · JARVIS Playhouse');$('#language').textContent=lang==='ht'?'Kreyòl / English':'English / Kreyòl';$('#countdown').setAttribute('aria-label',say('Tan ki rete','Time remaining'));content.replaceChildren();warning('');
  setHeader(say('Jwèt fèt · 5–12 moun','Party game · 5–12 people'),say('IMPOSTER · Pa fè pèsonn konfyans','IMPOSTER · Trust Nobody'),
    say('Yon moun bò tab la ap bay manti. Chak telefòn gen yon sekrè. Nan maten, pale fasafas epi chèche laverite.',
      'Someone at the table is lying. Every phone holds a secret. Talk face to face at dawn and hunt for the truth.'));
  const intro=node('p','muted',say('Tout moun dwe sou menm rezo lokal la ak òdinatè JARVIS k ap òganize a. Entènèt pa nesesè; òdinatè a dwe rete limen.',
    'Everyone must reach the JARVIS host on the same local network. Internet is unnecessary; the host computer must stay on.'));content.append(intro);
  const name=field(say('Non ou','Your name'));name.maxLength=20;name.autocomplete='nickname';
  addButton(say('Kreye yon salon prive','Create a private room'),()=>create(name.value),'primary');
  const code=field(say('Antre kòd 6 karaktè a','Enter the 6-character code'));code.maxLength=6;code.autocapitalize='characters';code.spellcheck=false;
  addButton(say('Antre nan salon an','Join the room'),()=>join(code.value,name.value),'secondary');
  if(seat)addButton(say('Retounen nan jwèt mwen an','Resume my game'),()=>{refresh();schedule();});
  $('#connection').textContent=say('Jwèt la mache sou rezo lokal san entènèt.','Plays over a local network without internet.');
}
function lobby(){setHeader(say('Salon prive · ', 'Private room · ')+room.players.length+'/12',say('Rasanble ekip la','Gather the players'),
  say('Pataje kòd sa a ak moun bò kote w yo. Senk moun pou kòmanse; douz maksimòm.',
    'Share this code with the people beside you. Five to start; twelve maximum.'));
  content.append(node('strong','room-code',room.code));roster();
  if(room.host){
    const settings=room.settings;
    content.append(node('h2','',say('Règ salon an','Room rules')));
    content.append(node('p','muted',say('Detektif ak Gadyen bezwen omwen 7 jwè pou kenbe pati a ekilibre.',
      'Detective and Guardian require at least 7 players to keep the match balanced.')));
    const grid=node('div','form-grid');content.append(grid);
    const numbers={discussion:[say('Diskisyon (segonn)','Discussion (seconds)'),[30,45,60,90,120,180]],
      vote:[say('Vòt (segonn)','Voting (seconds)'),[10,15,20,30,45,60]],
      night:[say('Lannwit (segonn)','Night (seconds)'),[25,35,45,60,90]],
      confirm:[say('Konfimasyon (segonn)','Confirmation (seconds)'),[8,12,20,30]]};
    const selects={};for(const [key,[caption,values]] of Object.entries(numbers)){
      const wrap=node('label','field',caption),select=node('select');for(const value of values){const option=node('option','',String(value));option.value=value;select.append(option);}select.value=settings[key];wrap.append(select);grid.append(wrap);selects[key]=select;}
    const tieWrap=node('label','field',say('Si vòt yo egal','If votes tie')),tie=node('select');
    for(const [value,ht,en] of [['no_elimination','Pèsonn pa elimine','Nobody is eliminated'],['runoff','Dezyèm vòt','Runoff vote'],['random','Tiraj o aza','Random draw']]){const option=node('option','',say(ht,en));option.value=value;tie.append(option);}
    tie.value=settings.tie;tieWrap.append(tie);grid.append(tieWrap);
    const checks={};for(const [key,ht,en] of [['evidence','Endis limite nan maten','Limited morning evidence'],['detective','Detektif','Detective'],['guardian','Gadyen','Guardian'],['sabotage','Sabotaj','Sabotage'],['audio','Siyal son','Audio cues']]){
      const row=node('label','setting-row',say(ht,en)),check=node('input');check.type='checkbox';check.checked=settings[key];row.append(check);content.append(row);checks[key]=check;}
    addButton(say('Sove règ salon an','Save room settings'),()=>action('settings',{settings:{...Object.fromEntries(Object.entries(selects).map(([key,value])=>[key,Number(value.value)])),tie:tie.value,...Object.fromEntries(Object.entries(checks).map(([key,value])=>[key,value.checked]))}}),'secondary');
    addButton(say('Kòmanse pati a','Start the match'),()=>action('start'),'primary');
    addButton(say('Fèmen salon an','Close room'),()=>action('close'),'danger');
  }else content.append(node('p','muted',say('N ap tann moun k ap òganize a kòmanse.','Waiting for the host to start.')));
}
function reveal(){setHeader(say('Wòl sekrè · Pati ','Secret role · Match ')+(room.match+1),say('Tout moun bese tèt yo','Everyone, heads down'),
  say('Gade wòl ou an prive. Pa montre telefòn nan. Lè w pare, kache li epi tann lòt moun yo.',
    'View your role privately. Do not show your phone. When ready, cover it and wait for everyone else.'));
  if(!room.ready){peek();addButton(say('Mwen wè wòl mwen · kache ekran an','I saw my role · cover the screen'),()=>action('ready'),'primary');}
  else content.append(node('div','event-card safe',say('Wòl ou sele. Kenbe tèt ou bese jouk tout moun pare.','Your role is sealed. Keep your head down until everyone is ready.')));
}
function deadPrediction(){
  if(room.alive)return;
  content.append(node('div','event-card',say('Ou elimine, men ou ka toujou genyen yon pwen anplis: devine ki ekip k ap genyen an an prive. Pa bay jwè vivan yo okenn siy.',
    'You are out, but you can still earn a bonus point: privately predict the winning team. Do not signal anything to living players.')));
  if(room.prediction)content.append(node('p','muted',say('Prediksyon ou sele: ','Your prediction is sealed: ')+teamName(room.prediction)));
  else {const grid=node('div','choice-grid');content.append(grid);
    for(const [team,ht,en] of [['innocents','Inosan yo','Innocents'],['imposters','Impostè yo','Imposters']]){
      const choice=node('button','',say(ht,en));choice.onclick=()=>action('predict',{prediction:team});grid.append(choice);}}
}
function teamName(team){return team==='imposters'?say('Impostè yo','The imposters'):say('Inosan yo','The innocents');}
function targetPicker(allowed){const wrap=node('label','field',say('Chwazi yon moun','Choose a player')),select=node('select');
  const pass=node('option','',say('Pèsonn · pase','Nobody · pass'));pass.value='';select.append(pass);
  room.players.forEach((person,index)=>{if(!person.alive||index===room.seat||(allowed&&!allowed.includes(index)))return;
    const option=node('option','',person.name);option.value=index;select.append(option);});
  select.value=draftTarget===null?'':String(draftTarget);select.onchange=()=>{draftTarget=select.value===''?null:Number(select.value);};
  wrap.append(select);content.append(wrap);return select;}
function night(){setHeader(say('Lannwit ','Night ')+room.round,say('Tèt bese. Ekran an prive.','Heads down. Screens private.'),
  say('Chak moun fè yon sèl chwa an sekrè. Tout telefòn yo sèvi ak menm ekran an. Nan pwochen etap la, w ap konfime oswa retire chwa w.',
    'Everyone makes one secret choice. All phones use the same screen. Next, you will confirm or withdraw your choice.'));
  if(!room.alive){deadPrediction();return;}
  if(room.submitted){content.append(node('div','event-card safe',say('Chwa ou sele. Tann lòt telefòn yo.',
    'Your choice is sealed. Waiting for the other phones.')));return;}
  peek();targetPicker();
  const token=node('label','token-row'),check=node('input');check.type='checkbox';check.checked=draftUse;
  check.disabled=room.used_power;check.onchange=()=>{draftUse=check.checked;};
  token.append(check,node('span','',room.used_power?say('Jeton ou deja itilize','Your token is already used'):
    say('Sèvi ak jeton yon sèl fwa mwen an','Use my one-time token')));content.append(token);
  addButton(say('Sele chwa prive mwen','Seal my private choice'),()=>{
    if(draftUse&&draftTarget===null){warning(say('Chwazi yon moun anvan ou sèvi ak jeton an.','Choose a player before using your token.'));return;}
    action('night',{target:draftTarget,use:draftUse});},'primary');
}
function confirmNight(){setHeader(say('Lannwit ','Night ')+room.round+' · '+say('konfimasyon','confirmation'),
  say('Yon dènye chwa','One final decision'),say('Èske w ap konfime sib sa a? Tout moun jwenn menm etap sa a; sèlman sa ou chwazi a gen efè.',
    'Will you confirm this target? Everyone gets the same step; only your own choice has an effect.'));
  if(!room.alive){deadPrediction();return;}
  if(room.submitted){content.append(node('div','event-card safe',say('Desizyon ou sele. Maten ap vini pou tout moun ansanm.',
    'Your decision is sealed. Dawn will arrive for everyone together.')));return;}
  content.append(node('div','event-card',say('Sib final: ','Final target: ')+nameOf(room.confirmation_target)));
  addButton(say('Wi · konfime sib la','Yes · confirm target'),()=>action('confirm',{confirm:true}),'primary');
  addButton(say('Non · retire chwa mwen','No · withdraw my choice'),()=>action('confirm',{confirm:false}),'secondary');
}
function morningMessage(event){
  if(!event||event.kind!=='morning')return '';
  let text=event.victim===null?say('Tout moun la toujou. Èske ekip la te ezite? Èske yon moun te pwoteje yon lòt?',
      'Everyone is still here. Did the team hesitate? Did someone protect a friend?'):
    nameOf(event.victim)+say(' disparèt pandan lannwit lan. Yo pa ka vote ankò.',' vanished during the night. They can no longer vote.');
  if(event.silence)text+='\n'+say('Yon siyal te koupe tras piblik yo.','A signal jammed the public trace.');
  else if(event.evidence)text+='\n'+say('Tras ki rete: omwen ','A surviving trace: at least ')+event.evidence.strength+
      say(' moun te sispèk menm moun nan. Ki moun sa a?',' people suspected the same person. Who was it?');
  else text+='\n'+say('Pa gen endis piblik ase pou maten sa a.','There is not enough public evidence this morning.');
  return text;
}
function morning(){setHeader(say('Maten ','Morning ')+room.round,say('Solèy la leve','The sun rises'),
  say('Gade sa maten pote. Pale sou sa nou konnen, pa sou ekran pèsonn.','See what dawn brought. Talk about what you know, not anyone’s screen.'));
  content.append(node('div','event-card',morningMessage(room.event)));
  if(room.clue?.round===room.round&&room.alive){
    addButton(say('Gade endis detektif mwen an an prive','Privately view my detective clue'),()=>{
      const clue=node('div','role-card',nameOf(room.clue.target)+' · '+
        (room.clue.alignment==='imposter'?say('Nan ekip impostè a','On the imposter team'):say('Nan ekip inosan yo','On the innocent team')));
      content.append(clue);},'secondary');
  }
  roster();deadPrediction();
}
function discussion(){setHeader(say('Jounen ','Day ')+room.round+' · '+say('diskisyon','discussion'),
  say('Pale. Bay manti. Tande.','Talk. Bluff. Listen.'),
  say('Gen 60 segonn nòmalman pou pale fasafas. Ki moun ki chanje istwa li? Ki moun ki twò sèten? Pa montre okenn ekran.',
    'You normally have 60 seconds to talk face to face. Who changed their story? Who sounds too sure? Do not show screens.'));
  content.append(node('div','event-card',morningMessage(room.event)));
  if(Number.isInteger(room.talk_prompt)&&talkPrompts[room.talk_prompt]){
    const card=node('div','role-card');card.append(node('strong','',say('Defi alibi · si nou vle','Optional alibi challenge')),
      node('p','',say(...talkPrompts[room.talk_prompt])));content.append(card);
  }
  roster();deadPrediction();
}
function voting(){const runoff=room.phase==='runoff';setHeader(say('Jounen ','Day ')+room.round+' · '+say(runoff?'dezyèm vòt':'vòt sekrè',runoff?'runoff vote':'private vote'),
  runoff?say('Egalite! Yon dezyèm vòt.','A tie! Vote again.'):say('Kiyès nou voye deyò?','Who leaves the circle?'),
  say('Chak moun ki toujou nan jwèt la vote an prive. Rezilta a parèt sou tout telefòn yo ansanm.',
    'Every living player votes privately. The result appears on all phones together.'));
  if(!room.alive){deadPrediction();return;}
  if(room.submitted){content.append(node('div','event-card safe',say('Vòt ou sele. Tann lòt moun yo.','Your vote is sealed. Waiting for everyone else.')));return;}
  const grid=node('div','choice-grid');content.append(grid);
  room.players.forEach((person,index)=>{if(!person.alive||index===room.seat||(runoff&&!room.runoff.includes(index)))return;
    const choice=node('button','',person.name);choice.onclick=()=>action('vote',{target:index});grid.append(choice);});
  addButton(say('Mwen pa vote pou pèsonn','I abstain'),()=>action('vote',{target:null}),'secondary');
}
function verdict(){const event=room.event;const person=event?.ejected;
  setHeader(say('Jounen ','Day ')+room.round+' · '+say('desizyon','verdict'),say('Vòt yo tonbe','The votes are in'),
    person===null?say('Pèsonn pa elimine. Konfyans ap vin pi difisil.','Nobody was eliminated. Trust gets harder.'):
      nameOf(person)+say(' kite sèk la.',' leaves the circle.'));
  content.append(node('div','event-card',person===null?
    (event?.tie?say('Vòt yo te egal. Pèsonn pa soti.','The vote tied. Nobody leaves.'):
      say('Pèsonn pa resevwa ase vòt.','Nobody received enough votes.')):
    nameOf(person)+' · '+(event.alignment==='imposter'?say('Se te yon impostè!','They were an imposter!'):
      say('Se te yon inosan.','They were innocent.'))));
  if(event?.tie&&person!==null)content.append(node('p','muted',say('Yon tiraj o aza te rezoud egalite a.','A random draw resolved the tie.')));
  const tally=Object.entries(event?.tally||{}).sort((a,b)=>b[1]-a[1]);
  if(tally.length)content.append(node('p','muted',say('Vòt yo: ','Votes: ')+tally.map(([seat,count])=>nameOf(Number(seat))+' '+count).join(' · ')));
  roster();deadPrediction();
}
function end(){const event=room.event;setHeader(say('Pati ','Match ')+(room.match+1)+' · '+say('fen','end'),
  teamName(event.winner)+say(' genyen!',' win!'),
  say('Tout sekrè yo louvri. Ki moun ki te konvenk ou? Ki moun ki te twonpe tout tab la?',
    'Every secret is out. Who convinced you? Who fooled the whole table?'));
  if(event.last?.kind==='morning')content.append(node('div','event-card',morningMessage(event.last)));
  if(event.last?.kind==='verdict')content.append(node('div','event-card',event.last.ejected===null?
    say('Dènye vòt la pa t elimine pèsonn.','The last vote eliminated nobody.'):
    nameOf(event.last.ejected)+' · '+(event.last.alignment==='imposter'?say('Impostè','Imposter'):say('Inosan','Innocent'))));
  content.append(node('h2','',say('Tout wòl ak pwen yo','All roles and session scores')));
  const board=node('div','scoreboard');room.players.forEach(player=>{
    const row=node('div','score');row.append(node('strong','',player.name+' · '+roleName(player.role)),
      node('span','badge',player.score+say(' pwen',' points')));board.append(row);});content.append(board);
  if(event.awards?.length){content.append(node('h2','',say('Ti prim ki fè nou ri','Funny awards')));
    const awards={eagle_eyes:["Je malfini","Eagle Eyes"],jury_star:["Wa jiri a","Jury Star"],plot_armor:["Pwoteksyon senaryo a","Plot Armor"],ghost_whisperer:["Zonbi ki te konnen","Ghost Who Knew"]};
    for(const award of event.awards)content.append(node('div','history-entry',say(...awards[award.kind])+' · '+nameOf(award.seat)));}
  content.append(node('p','muted',say('Pwen yo kontinye si nou jwe ankò nan menm salon an. Wòl impostè yo vire pou bay tout moun yon chans.',
    'Scores carry into a rematch in this room. Imposter roles rotate so more people get a turn.')));
  if(room.host){addButton(say('Jwe ankò · nouvo wòl','Rematch · new roles'),()=>action('rematch'),'primary');
    addButton(say('Fèmen salon an','Close room'),()=>action('close'),'danger');}
}
function render(){document.documentElement.lang=lang;document.title=say('Imposter — Pa fè pèsonn konfyans · JARVIS Playhouse','Imposter — Trust Nobody · JARVIS Playhouse');$('#language').textContent=lang==='ht'?'Kreyòl / English':'English / Kreyòl';$('#countdown').setAttribute('aria-label',say('Tan ki rete','Time remaining'));
  if(!room){renderLanding();return;}
  content.replaceChildren();notice.textContent='';
  switch(room.phase){case'lobby':lobby();break;case'reveal':reveal();break;case'night':night();break;
    case'confirm':confirmNight();break;case'morning':morning();break;case'discussion':discussion();break;
    case'vote':case'runoff':voting();break;case'verdict':verdict();break;case'end':end();break;}
}
$('#language').onclick=()=>{lang=lang==='ht'?'en':'ht';try{localStorage.setItem('jarvis_playhouse_language',lang);}catch{}render();};
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&seat)refresh();schedule();});
renderLanding();if(seat){refresh();schedule();}
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
