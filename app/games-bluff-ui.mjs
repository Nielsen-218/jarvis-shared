import {bluffActs,bluffCardFor,bluffCards,newBluff,restoreBluff,stepBluff,storytellerSeat} from './games-bluff.mjs';

const KEY='jarvis_playhouse_bluff_v1';
export function installBluff({say,pick,button,title,body,label,buttons,focus}) {
  let state=null,revealed=false,storageFailed=false;
  try {
    const raw=localStorage.getItem(KEY);
    if(raw && raw.length<16000) state=restoreBluff(JSON.parse(raw));
  } catch {}
  const stop=()=>{revealed=false;};
  function save() {
    try {
      const {names,replay,actions}=state;
      localStorage.setItem(KEY,JSON.stringify({names,replay,actions}));
      storageFailed=false;
    } catch {storageFailed=true;}
  }
  function act(action,keepRevealed=false) {
    state=stepBluff(state,action);
    if(!keepRevealed) stop();
    save();render();
  }
  function score() {return state.names.map((name,i)=>name+' · '+state.players[i].score+say(' pwen',' points')).join('\n');}
  function menu(back) {
    stop();buttons.replaceChildren();
    title.textContent=say('Manti Oswa Verite?','Truth or Bluff?');
    label.textContent=say('2 jwè · 12 istwa · sou menm aparèy la','2 players · 12 stories · one device');
    body.textContent=say('Chak moun reponn yon ti kestyon an prive, epi li di yon fraz ki ka verite oswa manti. Lòt la gade figi l epi deside!',
      'Privately answer a playful question, then make a truthful or false claim. The other player watches your face and decides!');
    const inputs=[0,1].map(i=>{
      const field=document.createElement('label');field.textContent=say('Non jwè ','Player name ')+(i+1);
      const input=document.createElement('input');input.type='text';input.maxLength=20;input.autocomplete='off';
      input.className='rivalry-input';input.value=state?.names[i]??'';
      field.append(input);buttons.append(field);return input;
    });
    const error=document.createElement('p');error.setAttribute('role','status');buttons.append(error);
    button(say('Kòmanse match la','Start the match'),()=>{
      try {state=newBluff(inputs.map(input=>input.value),state?state.replay+1:0);}
      catch {error.textContent=say('Mete de non diferan.','Enter two different names.');return;}
      save();render();
    });
    if(state) {
      button(say('Relouvri match la','Resume saved match'),render);
      button(say('Efase match ki sove a','Delete saved match'),()=>{
        try {localStorage.removeItem(KEY);} catch {}
        state=null;menu(back);
      });
    }
    button(say('Chwazi yon lòt jwèt','Choose another game'),back);
    focus();
  }
  function privateTurn() {
    const storyteller=storytellerSeat(state),detecting=state.phase==='cover-detective'||state.phase==='detective';
    const seat=detecting?1-storyteller:storyteller;
    title.textContent=state.names[seat];
    if(!revealed) {
      body.textContent=say('Pase telefòn nan bay ','Pass the phone to ')+state.names[seat]+'. '+
        say('Lòt jwè a vire do li.','The other player looks away.');
      button(say('Mwen pare','I’m ready'),()=>{
        revealed=true;
        if(state.phase.startsWith('cover-')) act('ready',true);
        else render();
      });
      return;
    }
    const card=bluffCardFor(state);
    if(state.phase==='truth') {
      body.textContent=pick(card.prompt)+'\n\n'+say('Reponn onètman pou jwèt la mache. Se ou menm ki konnen verite a.',
        'Answer honestly so the game works. Only you know the truth.');
      button(say('Wi, sa rive m','Yes, I have'),()=>act({truth:true},true));
      button(say('Non, sa poko rive m','No, I have not'),()=>act({truth:false},true));
    } else if(state.phase==='claim') {
      body.textContent=pick(card.prompt)+'\n\n'+say('Repons prive ou: ','Your private answer: ')+
        (state.truth?say('Wi','Yes'):say('Non','No'))+'.\n\n'+
        say('Chwazi fraz w ap di byen fò. Si lòt jwè a twonpe, ou touche 2 pwen. Ou gen yon kat fou: yon manti ki pase bay 4 pwen, men si yo bare w, advèsè a pran 3.',
          'Choose a claim to say aloud. Fool your rival for 2 points. You have one wild card: a successful bluff earns 4, but getting caught gives your rival 3.');
      if(state.wild) body.textContent+='\n'+say('Kat fou aktive: chwazi manti a.','Wild card active: choose the bluff.');
      for(const [truth,claim] of [[true,card.yes],[false,card.no]]) {
        button(pick(claim),()=>act({claim:truth}));
        buttons.lastElementChild.disabled=state.wild && truth===state.truth;
      }
      if(state.players[seat].wild && !state.wild)
        button(say('Jwe kat fou a (1 fwa)','Play wild card (once)'),()=>act('wild',true));
    } else {
      body.textContent=state.names[storyteller]+say(' di: ',' says: ')+'“'+
        pick(state.claim?card.yes:card.no)+'”\n\n'+
        say('Mande l rakonte yon ti detay. Èske fraz li a se verite? Bon deteksyon bay 2 pwen.',
          'Ask for a little detail. Is the claim true? A correct call earns 2 points.');
      button(say('Mwen kwè li','I believe them'),()=>act({believe:true}));
      button(say('Se manti!','Call their bluff!'),()=>act({believe:false}));
    }
  }
  function render() {
    buttons.replaceChildren();
    document.querySelectorAll('.progress i').forEach((node,i)=>node.classList.toggle('on',i<=Math.floor(Math.min(state.round,11)/4)));
    label.textContent=state.phase==='end'?say('Match la fini','Match complete'):
      pick(bluffActs[Math.floor(state.round/4)])+' · '+(state.round+1)+' / '+bluffCards.length;
    if(['cover-storyteller','truth','claim','cover-detective','detective'].includes(state.phase)) privateTurn();
    if(state.phase==='result') {
      const entry=state.history.at(-1),card=bluffCardFor(state),storyteller=storytellerSeat(state);
      title.textContent=say('Verite a parèt!','The truth is out!');
      body.textContent=state.names[storyteller]+say(' te di: ',' claimed: ')+pick(entry.claim?card.yes:card.no)+'\n\n'+
        say('Verite: ','Truth: ')+(entry.truth?say('Wi','Yes'):say('Non','No'))+' · '+
        (entry.claim===entry.truth?say('Verite','Truth'):say('Manti','Bluff'))+'\n'+
        state.names[entry.winner]+' +'+entry.points+say(' pwen',' points')+
        (entry.wild?say(' · kat fou',' · wild card'):'')+'\n\n'+score()+'\n\n'+
        say('Ki detay ki te fè w kwè l?','Which detail convinced you?');
      button(say('Pwochen istwa','Next story'),()=>act('continue'));
    }
    if(state.phase==='end') {
      const [a,b]=state.players.map(player=>player.score);
      title.textContent=a===b?say('Nou egal!','It’s a tie!'):state.names[a>b?0:1]+say(' genyen!',' wins!');
      body.textContent=score()+'\n\n'+say('Douz istwa fini. Ki manti ki te pi komik?','Twelve stories down. Which bluff was funniest?');
      button(say('Jwe ankò nan lòt lòd','Rematch with a new order'),()=>{
        state=newBluff(state.names,state.replay+1);save();render();
      });
    }
    if(storageFailed) body.textContent+='\n\n'+say('Navigatè a pa t sove match la.','The browser could not save this match.');
    focus();
  }
  return {menu,stop};
}
