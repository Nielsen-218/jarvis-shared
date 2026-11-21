import {newWould,restoreWould,stepWould,wouldActs,wouldCardFor,wouldCards} from './games-would.mjs';

const KEY='jarvis_playhouse_would_v1';
export function installWould({say,pick,button,title,body,label,buttons,focus}){
  let state=null,revealed=false,storageFailed=false;
  try{const raw=localStorage.getItem(KEY);if(raw&&raw.length<18000)state=restoreWould(JSON.parse(raw));}catch{}
  const stop=()=>{revealed=false;};
  function save(){
    try{const {names,replay,actions}=state;localStorage.setItem(KEY,JSON.stringify({names,replay,actions}));storageFailed=false;}
    catch{storageFailed=true;}
  }
  function act(action,keepRevealed=false){
    state=stepWould(state,action);
    if(!keepRevealed||!['choice','prediction','risk'].includes(state.phase))stop();
    save();render();
  }
  function score(){return state.names.map((name,i)=>name+' · '+state.players[i].score+say(' pwen',' points')).join('\n');}
  function menu(back){
    stop();buttons.replaceChildren();
    title.textContent=say('Sa W Ta Chwazi?','Would You Rather?');
    label.textContent=say('Konpetisyon 2 jwè · 12 chwa','2-player duel · 12 choices');
    body.textContent=say('Chak moun chwazi an prive epi devine sa lòt la ap chwazi. Bon devinèt bay 2 pwen. Apre w gen 1 pwen, ou ka jwe yon sèl paryaj doub: +4 si w bon, −1 si w rate.',
      'Each player chooses privately and predicts the other’s choice. A correct prediction earns 2 points. After you have a point, you may make one double bet: +4 if right, −1 if wrong.');
    const inputs=[0,1].map(i=>{
      const field=document.createElement('label');field.textContent=say('Non jwè ','Player name ')+(i+1);
      const input=document.createElement('input');input.type='text';input.maxLength=20;input.autocomplete='off';
      input.className='rivalry-input';input.value=state?.names[i]??'';
      field.append(input);buttons.append(field);return input;
    });
    const error=document.createElement('p');error.setAttribute('role','status');buttons.append(error);
    button(say('Kòmanse konpetisyon an','Start the duel'),()=>{
      try{state=newWould(inputs.map(input=>input.value),state?state.replay+1:0);}
      catch{error.textContent=say('Mete de non diferan.','Enter two different names.');return;}
      save();render();
    });
    if(state){
      button(say('Relouvri konpetisyon an','Resume saved duel'),render);
      button(say('Efase konpetisyon ki sove a','Delete saved duel'),()=>{
        try{localStorage.removeItem(KEY);}catch{}
        state=null;menu(back);
      });
    }
    button(say('Chwazi yon lòt mòd','Choose another mode'),back);
    focus();
  }
  function privateTurn(){
    const name=state.names[state.seat];title.textContent=name;
    if(!revealed){
      body.textContent=say('Pase telefòn nan bay ','Pass the phone to ')+name+'. '+
        say('Lòt jwè a vire do li.','The other player looks away.');
      button(say('Mwen pare','I’m ready'),()=>{revealed=true;act('ready',true);});
      return;
    }
    const card=wouldCardFor(state);
    if(state.phase==='choice'){
      body.textContent=pick(card.question)+'\n\n'+say('Chwazi sa ou menm ou ta pito. Lòt jwè a pa dwe wè chwa a.',
        'Choose what you would rather do. Keep your choice hidden from the other player.');
      card.choices.forEach((choice,i)=>button(pick(choice),()=>act({choice:i},true)));
    }else if(state.phase==='prediction'){
      body.textContent=pick(card.question)+'\n\n'+say('Chwa ou: ','Your choice: ')+pick(card.choices[state.choices[state.seat]])+
        '\n\n'+say('Ki chwa ','Which choice will ')+state.names[1-state.seat]+say(' ap fè?',' make?');
      card.choices.forEach((choice,i)=>button(pick(choice),()=>act({predict:i},true)));
    }else{
      body.textContent=say('Ou gen ','You have ')+state.players[state.seat].score+say(' pwen. Èske w ap riske 1 pwen pou w ka touche 4?',
        ' points. Risk 1 point for a chance to earn 4?');
      button(say('Jwe san paryaj · +2 si m bon','Play safe · +2 if right'),()=>act({double:false}));
      button(say('Paryaj doub · +4 / −1','Double · +4 / −1'),()=>act({double:true}));
    }
  }
  function render(){
    buttons.replaceChildren();
    document.querySelectorAll('.progress i').forEach((node,i)=>node.classList.toggle('on',i<=Math.floor(Math.min(state.round,11)/4)));
    label.textContent=state.phase==='end'?say('Konpetisyon an fini','Duel complete'):
      pick(wouldActs[Math.floor(state.round/4)])+' · '+(state.round+1)+' / '+wouldCards.length;
    if(['cover','choice','prediction','risk'].includes(state.phase))privateTurn();
    if(state.phase==='result'){
      const card=wouldCardFor(state),entry=state.history.at(-1);
      title.textContent=say('Men chwa nou yo!','Here are your choices!');
      body.textContent=pick(card.question)+'\n\n'+state.names.map((name,i)=>
        name+say(' te chwazi: ',' chose: ')+pick(card.choices[entry.choices[i]])+'\n'+
        say('Li te devine pou ','Predicted for ')+state.names[1-i]+': '+pick(card.choices[entry.predictions[i]])+
        ' · '+(entry.changes[i]>=0?'+':'')+entry.changes[i]+say(' pwen',' points')+
        (entry.risks[i]?say(' · doub',' · double'):''),
      ).join('\n\n')+'\n\n'+score()+'\n\n'+say('Poukisa nou te chwazi konsa?','Why did you choose those?');
      button(say('Pwochen chwa','Next choice'),()=>act('continue'));
    }
    if(state.phase==='end'){
      const [a,b]=state.players.map(player=>player.score);
      title.textContent=a===b?say('Nou egal!','It’s a tie!'):state.names[a>b?0:1]+say(' genyen!',' wins!');
      body.textContent=score()+'\n\n'+say('Douz chwa fini. Ki chwa ki te pi difisil?',
        'Twelve choices down. Which was the hardest?');
      button(say('Jwe ankò nan lòt lòd','Rematch with a new order'),()=>{
        state=newWould(state.names,state.replay+1);save();render();
      });
    }
    if(storageFailed)body.textContent+='\n\n'+say('Navigatè a pa t sove konpetisyon an.','The browser could not save this duel.');
    focus();
  }
  return {menu,stop};
}
