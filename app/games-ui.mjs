import {installRooms} from './games-rooms.mjs';
import {installMystery} from './games-mystery-ui.mjs';
import {installRivalry} from './games-rivalry-ui.mjs';
import {installKnow} from './games-know-ui.mjs';
import {installBluff} from './games-bluff-ui.mjs';
import {installWould} from './games-would-ui.mjs';
import {installTelephone} from './games-telephone-ui.mjs';
import {dilemmas, facts, startVote, castVote, readyVoter, tally, nextVote,
        startQuiz, answerQuiz, nextQuiz, quizScore} from './games-party.mjs';

let lang = 'ht', opener, mode = 'mystery';
try {if(localStorage.getItem('jarvis_playhouse_language')==='en') lang='en';} catch {}
let party = null;
let roomUI;
const $ = selector => document.querySelector(selector);
const modal = $('#story');
const pick = value => value[lang];
const say = (ht, en) => lang === 'ht' ? ht : en;
const content = $('.shell');
const mobile = $('.mobile-nav');
const buttons = $('#choices');
const label = $('#chapter');
const title = $('#story-title');
const body = $('#story-copy');

function open(trigger, kind) {
  opener = trigger; mode = kind;
  modal.classList.add('open');
  content.inert = mobile.inert = true;
  document.body.style.overflow = 'hidden';
  $('#close-story').focus();
}
function close() {
  roomUI?.stop();
  mystery.stop();
  rivalry.stop();
  know.stop();
  bluff.stop();
  would.stop();
  telephone.stop();
  if (['mystery','rivalry','know','bluff','would','would-menu','adventure','telephone'].includes(mode)) {
    title.textContent = '';
    body.textContent = '';
    buttons.replaceChildren();
  }
  modal.classList.remove('open');
  content.inert = mobile.inert = false;
  document.body.style.overflow = '';
  opener?.focus();
}
function button(text, action) {
  const node = document.createElement('button');
  node.textContent = text;
  node.onclick = action;
  buttons.append(node);
}
const mystery = installMystery({open,close,say,pick,button,title,body,label,buttons,
  focus:() => buttons.querySelector('button')?.focus()});
const rivalry = installRivalry({say,pick,button,title,body,label,buttons,
  focus:() => buttons.querySelector('button:not(:disabled)')?.focus()});
const know = installKnow({say,pick,button,title,body,label,buttons,
  focus:() => buttons.querySelector('button:not(:disabled)')?.focus()});
const bluff = installBluff({say,pick,button,title,body,label,buttons,
  focus:() => buttons.querySelector('button:not(:disabled)')?.focus()});
const would = installWould({say,pick,button,title,body,label,buttons,
  focus:() => buttons.querySelector('button:not(:disabled)')?.focus()});
const telephone = installTelephone({say,pick,button,title,body,label,buttons,
  focus:() => buttons.querySelector('button:not(:disabled)')?.focus()});
function startTelephone(trigger){open(trigger,'telephone');telephone.menu(close);}
function startKnow(trigger) {
  open(trigger,'know');
  know.menu(close);
}
function startBluff(trigger) {
  open(trigger,'bluff');
  bluff.menu(close);
}
function startWould(trigger) {
  title.textContent=say('Sa W Ta Chwazi?','Would You Rather?');
  label.textContent=say('Chwazi mòd la','Choose a mode');
  body.textContent=say('Konpetisyon pou de moun oswa vòt rapid pou yon gwoup.','A two-player duel or a quick group vote.');
  buttons.replaceChildren();
  button(say('Konpetisyon · 2 jwè · 12 chwa','Duel · 2 players · 12 choices'),()=>{
    mode='would';would.menu(()=>startWould(trigger));
  });
  button(say('Vòt gwoup · 2–8 jwè · 5 chwa','Group vote · 2–8 players · 5 choices'),()=>startParty(trigger,'vote'));
  open(trigger,'would-menu');
}
function start(trigger) {
  title.textContent = say('Chwazi avanti nou an','Choose your adventure');
  label.textContent = say('Misyon Fanmi','Family Adventure');
  body.textContent = say('Pase menm telefòn nan youn bay lòt.','Pass one phone between players.');
  buttons.replaceChildren();
  document.querySelectorAll('.progress i').forEach(node=>node.classList.remove('on'));
  button(say('Konpetisyon · Rivalite Lantèn · 2 jwè · 12 defi','Competitive · Lantern Rivalry · 2 players · 12 challenges'),()=>{
    mode='rivalry';
    rivalry.menu(()=>start(trigger));
  });
  button(say('An ekip · Mistè Lantèn · 2–8 jwè · 6 desizyon','Cooperative · The Missing Lantern · 2–8 players · 6 decisions'),()=>mystery.start(trigger));
  open(trigger,'adventure');
}
function renderParty(focus = false) {
  buttons.replaceChildren();
  const quiz = mode === 'quiz', rounds = quiz ? facts : dilemmas;
  title.textContent = quiz ? say('Manti Oswa Verite?', 'Truth or Bluff?') : say('Sa W Ta Chwazi?', 'Would You Rather?');
  label.textContent = say('Sou aparèy sa a', 'On this device');
  document.querySelectorAll('.progress i').forEach((node, i) => {
    node.classList.toggle('on', !!party && i < Math.ceil((party.round + 1) * 3 / rounds.length));
  });
  if (!party) {
    body.textContent = say('Konbyen moun k ap jwe? Pase menm aparèy la bay chak moun. Pa gen salon sou entènèt.', 'How many players? Pass this device to each person. This is not an online room.');
    for(let players=2;players<=8;players++) button(say(players+' jwè',players+' players'),()=>{party=startVote(players);renderParty(true);});
  } else if (party.phase === 'complete') {
    label.textContent = say('Pati a fini', 'Game complete');
    body.textContent = quiz
      ? say('Ou jwenn '+quizScore(party)+' sou '+facts.length+' repons kòrèk.', 'You got '+quizScore(party)+' of '+facts.length+' answers right.')
      : say('Mèsi paske nou te jwe ansanm! Men vòt chak tou:', 'Thanks for playing together! Here are the votes for each round:')+'\n\n'+party.results.map(r=>(r.round+1)+'. '+pick(dilemmas[r.round].question)+'\n'+dilemmas[r.round].choices.map((c,i)=>pick(c)+': '+r.counts[i]).join(' · ')).join('\n\n');
    button(say('Jwe ankò', 'Play again'),()=>{party=quiz?startQuiz():null;renderParty(true);});
    button(say('Retounen nan jwèt yo', 'Back to games'),close);
  } else if (!quiz && party.phase === 'handoff') {
    label.textContent = say('Pase aparèy la', 'Pass the device');
    body.textContent = say('Bay jwè '+(party.votes.length+1)+' aparèy la. Vòt yo ap parèt lè tout moun fin chwazi.', 'Hand the device to player '+(party.votes.length+1)+'. Votes appear after everyone has chosen.');
    button(say('Mwen pare', 'I’m ready'),()=>{party=readyVoter(party);renderParty(true);});
  } else if (!quiz && party.phase === 'result') {
    const result=tally(party);
    label.textContent = say('Rezilta tou '+(party.round+1), 'Round '+(party.round+1)+' results');
    body.textContent = pick(rounds[party.round].question)+'\n\n'+rounds[party.round].choices.map((c,i)=>pick(c)+': '+result.counts[i]).join('\n')+'\n\n'+(result.tie?say('Egalite! Pa gen gayan.', 'A tie! Neither choice wins.'):say('Chwa ki gen plis vòt: ', 'Most votes: ')+pick(rounds[party.round].choices[result.winner]));
    button(say('Kontinye', 'Continue'),()=>{party=nextVote(party);renderParty(true);});
  } else if (quiz && party.phase === 'answer') {
    label.textContent = party.answers.at(-1)?say('Kòrèk!', 'Correct!'):say('Ann aprann!', 'Let’s learn!');
    body.textContent = pick(facts[party.round].explanation);
    button(say('Kontinye', 'Continue'),()=>{party=nextQuiz(party);renderParty(true);});
  } else {
    label.textContent = say('Tou ', 'Round ')+(party.round+1)+' / '+rounds.length+(quiz?'':say(' · Jwè ', ' · Player ')+(party.votes.length+1));
    body.textContent = pick(rounds[party.round].question);
    const options=quiz?[say('Verite', 'True'),say('Manti', 'Bluff')]:rounds[party.round].choices.map(pick);
    options.forEach((option,i)=>button(option,()=>{
      party=quiz?answerQuiz(party,i):castVote(party,i);renderParty(true);
    }));
  }
  if(focus) buttons.firstElementChild?.focus();
}
function startParty(trigger, kind) {
  mode=kind;party=kind==='quiz'?startQuiz():null;
  renderParty();open(trigger,kind);
}
function planned(trigger) {
  title.textContent = say('Ap vini', 'Coming soon');
  label.textContent = say('An preparasyon', 'Planned');
  body.textContent = say('Pati sa a poko disponib. Ou kapab jwe Mistè Lantèn nan sou aparèy sa a.', 'This part is not available yet. You can play The Missing Lantern on this device.');
  buttons.replaceChildren();
  document.querySelectorAll('.progress i').forEach(node => node.classList.remove('on'));
  button(say('Retounen', 'Back'), close);
  open(trigger, 'planned');
}
$('#start-story').onclick = event => start(event.currentTarget);
document.querySelectorAll('.game').forEach((node, i) => {
  node.onclick = event => {
    if(i===0) start(event.currentTarget);
    else if(i===1) startKnow(event.currentTarget);
    else if(i===2) startBluff(event.currentTarget);
    else if(i===3) startWould(event.currentTarget);
    else if(i===4) startTelephone(event.currentTarget);
    else planned(event.currentTarget);
  };
  node.querySelector('small').textContent = i===0?'Avanti chwa':i<=4?'Sou aparèy sa a':'Ap vini';
});
document.querySelectorAll('.round, .privacy, .chip').forEach(node => node.onclick = event => planned(event.currentTarget));
$('.chip').onclick = event => start(event.currentTarget);
document.querySelectorAll('.rail nav button, .mobile-nav button').forEach((node, i) => {
  node.onclick = event => {
    if (i === 0 || i === 9) window.scrollTo({top: 0, behavior: 'instant'});
    else if (i === 1 || i === 10) $('#catalog').scrollIntoView();
    else if (i === 2 || i === 11) start(event.currentTarget);
    else if (i === 3) startWould(event.currentTarget);
    else if (i === 7) startBluff(event.currentTarget);
    else planned(event.currentTarget);
  };
});
$('#close-story').onclick = close;
modal.onclick = event => {if (event.target === modal) close();};
modal.addEventListener('keydown', event => {
  if (event.key === 'Escape') close();
  if (event.key !== 'Tab') return;
  const all = [...modal.querySelectorAll('button:not(:disabled),input:not([hidden]),summary,a[href]')];
  const first = all[0], last = all.at(-1);
  if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus();}
  else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
});

// Every visible phrase is translated; original text nodes preserve icon markup.
const translations = new Map([
  ['Sou aparèy sa a','On this device'],
  ['JWE FASAFAS · 5–12 JWÈ','FACE TO FACE · 5–12 PLAYERS'],
  ['Pa fè pèsonn konfyans','Trust Nobody'],
  ['Wòl sekrè, chwa lannwit, diskisyon ki fè bri, epi vòt prive sou chak telefòn.','Secret roles, night choices, lively discussion and private votes on each phone.'],
  ['Antre nan salon an →','Enter the room →'],
  ['Jwèt Popilè Fanmi an','Popular family games'], ['Kategori Jwèt','Game categories'],
  ['Akèy','Home'], ['Tout Jwèt','All games'], ['Avanti Istwa','Story adventures'], ['Jwèt Fanmi','Family games'], ['Kwen Lanmou','Love Corner'], ['Jwèt Konvèsasyon','Conversation games'], ['Kwiz & Konesans','Quizzes & knowledge'], ['Defi & Aksyon','Challenges & action'], ['Pati Gwoup','Party games'],
  ['Yon espas pou ri, jwe, epi rakonte istwa ansanm.','A place to laugh, play and tell stories together.'], ['Bon swa, fanmi!','Good evening, family!'], ['JARVIS pare pou jwèt la.','JARVIS is ready for game night.'], ['Kreye yon salon','Create a room'], ['Jwèt aswè a','Tonight’s games'], ['Ki istwa n ap viv ansanm?','Which story will we live together?'],
  ['Chwazi yon jwèt, pran telefòn nou yo, epi kite JARVIS mennen lannwit lan.','Choose a story and share an adventure together on this device.'],
  ['AVANTI ISTWA · 2–8 JWÈ','STORY ADVENTURE · LOCAL PREVIEW'], ['Misyon','Family'], ['Fanmi','Adventure'], ['Gato fèt la disparèt! Fè chwa ansanm pou jwenn li anvan fèt la kòmanse.','The party cake is missing! Choose together and find it before the party begins.'], ['Kòmanse avanti →','Start adventure →'], ['POU NOU DE','FOR TWO'], ['Ti kestyon dous, defi ansanm, ak souvni ki rete prive.','Private partner questions and shared memories. Coming soon.'], ['◒ Antre an prive','◒ Private entrance'], ['Jwe kounye a','Explore the games'], ['Chwazi yon bagay ki fè nou souri','Choose something that makes you smile'], ['Wè tout jwèt yo →','See all games →'], ['Misyon Fanmi','Family Adventure'], ['Avanti chwa','Choice adventure'], ['Ki Moun Ki Konnen M?','Who Knows Me Best?'], ['Manti Oswa Verite?','Truth or Bluff?'], ['Sa W Ta Chwazi?','Would You Rather?'], ['Telefòn Fou','Drawing Telephone'], ['Kiyès Mwen Ye?','Who Am I?'], ['Ap vini','Coming soon'], ['Eksplore','Explore'], ['Yon lannwit, anpil fason pou jwe','One night, many ways to play'], ['Konvèsasyon','Conversations'], ['Jwèt','Games'], ['Avanti','Stories'], ['Lanmou','Love'],
]);
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
const nodes = [];
while (walker.nextNode()) {
  const node = walker.currentNode, key = node.textContent.trim();
  if (translations.has(key)) nodes.push([node, key, node.textContent]);
}
// Honest preview labels replace multiplayer/privacy promises until implemented.
$('.hero .eyebrow').textContent = 'AVANTI ISTWA · SOU APARÈY SA A';
const heroDescription = $('.hero-copy p:not(.eyebrow)');
heroDescription.textContent = 'Lantèn fèt la disparèt! Mete endis nou yo ansanm anvan lapli a.';
$('.subtitle').textContent = 'Mete endis nou yo ansanm epi vote sou menm aparèy la.';
$('.love-card p:not(.eyebrow)').textContent = 'Kesyon pou koup ak souvni prive. Ap vini.';
$('.room').textContent = 'Salon';
const preview = document.createElement('p');
preview.className = 'preview-status';
preview.textContent = 'An devlopman · Istwa sou aparèy sa a';
$('.main').append(preview);
function applyLanguage() {
  document.documentElement.lang = lang;
  nodes.forEach(([node, ht, original]) => {
    node.textContent = lang === 'ht' ? original : original.replace(ht, translations.get(ht));
  });
  $('.hero .eyebrow').textContent = say('AVANTI ISTWA · SOU APARÈY SA A','STORY ADVENTURE · ON THIS DEVICE');
  heroDescription.textContent = say('Lantèn fèt la disparèt! Mete endis nou yo ansanm anvan lapli a.', 'The festival lantern is missing! Piece together your clues before the storm.');
  $('.subtitle').textContent = say('Mete endis nou yo ansanm epi vote sou menm aparèy la.','Put your clues together and vote on one device.');
  $('.love-card p:not(.eyebrow)').textContent = say('Kesyon pou koup ak souvni prive. Ap vini.','Private couple questions and memories. Coming soon.');
  $('.room').textContent = say('Salon','Rooms');
  mobileRoom.textContent = say('Salon','Rooms');
  preview.textContent = say('An devlopman · Istwa sou aparèy sa a','In development · Story plays on this device');
  $('#language').textContent = lang === 'ht' ? 'Kreyòl / English' : 'English / Kreyòl';
  $('#language').setAttribute('aria-label', say('Chanje lang','Change language'));
  $('#close-story').setAttribute('aria-label', say('Fèmen','Close'));
  $('.rail').setAttribute('aria-label', say('Navigasyon jwèt','Game navigation'));
  mobile.setAttribute('aria-label', say('Navigasyon mobil','Mobile navigation'));
  $('.hero-grid').setAttribute('aria-label', say('Jwèt vedèt','Featured games'));
  $('#catalog').closest('section').setAttribute('aria-label', say('Katalòg jwèt','Game catalog'));
  $('.chips').closest('section').setAttribute('aria-label', say('Kategori jwèt','Game categories'));
  $('.round').setAttribute('aria-label', say('Notifikasyon','Notifications'));
  $('.progress').setAttribute('aria-label', say('Pwogrè','Progress'));
  if (modal.classList.contains('open') && (mode === 'quiz' || mode === 'vote')) renderParty();
  if (modal.classList.contains('open') && mode === 'room') roomUI.render();
  if (modal.classList.contains('open') && mode === 'telephone') telephone.render();
}
$('#language').onclick = () => {
  lang = lang === 'ht' ? 'en' : 'ht';
  try {localStorage.setItem('jarvis_playhouse_language',lang);} catch {}
  applyLanguage();
};
if(location.hash.startsWith('#t=')){
  try{localStorage.setItem('jarvis_token',decodeURIComponent(location.hash.slice(3)))}catch{}
  history.replaceState(null,'',location.pathname);
}
roomUI=installRooms({open,close,say,pick,button,title,body,label,buttons});
$('.room').onclick=event=>roomUI.menu(event.currentTarget);
// Mobile exposes rooms without crowding the reference's compact header.
const mobileRoom=document.createElement('button');
mobileRoom.textContent='Salon';
mobileRoom.onclick=event=>roomUI.menu(event.currentTarget);
mobile.append(mobileRoom);
applyLanguage();
// Reuse the existing PWA worker, including direct visits to Playhouse.
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
