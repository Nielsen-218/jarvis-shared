import {cardFor, knowCards, knowTopics, newKnow, restoreKnow, stepKnow, subjectSeat} from './games-know.mjs';

const KEY='jarvis_playhouse_know_v1';
export function installKnow({say,pick,button,title,body,label,buttons,focus}) {
  let state=null, revealed=false;
  let storageFailed=false;
  try {
    const raw=localStorage.getItem(KEY);
    if(raw && raw.length<16000) state=restoreKnow(JSON.parse(raw));
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
    state=stepKnow(state,action);
    if(!keepRevealed) stop();
    save();render();
  }
  function score() {
    return state.names.map((name,i)=>name+' · '+state.players[i].score+say(' pwen',' points')).join('\n');
  }
  function menu(back) {
    stop();buttons.replaceChildren();
    title.textContent=say('Ki Moun Ki Konnen M?','Who Knows Me Best?');
    label.textContent=say('2 jwè · 12 kestyon · sou menm aparèy la','2 players · 12 questions · one device');
    body.textContent=say('Chak moun chwazi pwòp repons li an prive. Lòt la devine. Nou bay pwen dapre repons moun nan, pa dapre sa JARVIS panse.',
      'Each person privately chooses their own answer. The other guesses. The person’s answer is the truth; JARVIS does not judge it.');
    const inputs=[0,1].map(i=>{
      const field=document.createElement('label');
      field.textContent=say('Non jwè ','Player name ')+(i+1);
      const input=document.createElement('input');
      input.type='text';input.maxLength=20;input.autocomplete='off';input.className='rivalry-input';
      input.value=state?.names[i]??'';
      field.append(input);buttons.append(field);return input;
    });
    const error=document.createElement('p');error.setAttribute('role','status');buttons.append(error);
    button(say('Kòmanse match la','Start the match'),()=>{
      try {state=newKnow(inputs.map(input=>input.value),state?state.replay+1:0);}
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
    const subject=subjectSeat(state),guessing=state.phase==='cover-guesser'||state.phase==='guesser';
    const seat=guessing?1-subject:subject;
    const name=state.names[seat];
    title.textContent=name;
    if(!revealed) {
      body.textContent=say('Pase telefòn nan bay ','Pass the phone to ')+name+'. '+
        say('Lòt jwè a vire do li.','The other player looks away.');
      button(say('Mwen pare','I’m ready'),()=>{
        revealed=true;
        if(state.phase.startsWith('cover-')) act('ready',true);
        else render();
      });
      return;
    }
    const card=cardFor(state);
    if(state.phase==='subject') {
      body.textContent=pick(card.question)+'\n\n'+say('Chwazi sa ki pi sanble ak repons pa w. Lòt jwè a pa dwe wè l.',
        'Choose the answer that fits you best. Keep it hidden from the other player.');
      card.choices.forEach((choice,i)=>button(pick(choice),()=>act({truth:i},true)));
    } else if(state.phase==='forecast') {
      body.textContent=pick(card.question)+'\n\n'+say('Repons ou: ','Your answer: ')+pick(card.choices[state.truth])+'.\n\n'+
        say('Èske lòt jwè a ap jwenn li? Si previzyon w bon, ou touche 1 pwen.',
          'Will the other player get it right? A correct prediction earns you 1 point.');
      button(say('Wi, l ap jwenn li','Yes, they’ll know'),()=>act({forecast:true}));
      button(say('Non, l ap rate','No, they’ll miss'),()=>act({forecast:false}));
    } else {
      body.textContent=pick(card.question)+'\n\n'+say('Devine repons ','Guess ')+state.names[subject]+'. '+
        say('Bon repons: 2 pwen. Yon endis bay 1 pwen si w jwenn li. Lè w gen omwen 1 pwen, paryaj doub bay 4 si w jwenn li, men retire 1 si w rate.',
          'Correct guess: 2 points. A hint makes a correct guess worth 1. Once you have a point, double makes it worth 4, but costs 1 on a miss.');
      if(state.hint) body.textContent+='\n'+say('Yon move chwa elimine.','One wrong choice is ruled out.');
      if(state.double) body.textContent+='\n'+say('Paryaj doub aktive.','Double is active.');
      card.choices.forEach((choice,i)=>{
        button(pick(choice),()=>act({guess:i}));
        buttons.lastElementChild.disabled=state.hint && i===(state.truth+1)%3;
      });
      if(state.players[seat].hint && !state.double)
        button(say('Sèvi ak yon endis (1 fwa)','Use a hint (once)'),()=>act('hint',true));
      if(state.players[seat].double && state.players[seat].score>=1 && !state.hint)
        button(say('Parye doub (1 fwa)','Double down (once)'),()=>act('double',true));
    }
  }
  function render() {
    buttons.replaceChildren();
    document.querySelectorAll('.progress i').forEach((node,i)=>node.classList.toggle('on',i<=Math.floor(Math.min(state.round,11)/4)));
    label.textContent=state.phase==='end'?say('Match la fini','Match complete'):
      pick(knowTopics[Math.floor(state.round/4)])+' · '+(state.round+1)+' / '+knowCards.length;
    if(['cover-subject','subject','forecast','cover-guesser','guesser'].includes(state.phase)) privateTurn();
    if(state.phase==='result') {
      const entry=state.history.at(-1),card=cardFor(state);
      const subject=subjectSeat(state),guesser=1-subject;
      title.textContent=say('Rezilta kestyon an','Question result');
      body.textContent=pick(card.question)+'\n\n'+state.names[subject]+say(' te chwazi: ',' chose: ')+
        pick(card.choices[entry.truth])+'\n'+state.names[guesser]+say(' te devine: ',' guessed: ')+
        pick(card.choices[entry.guess])+'\n\n'+
        (entry.right?say('Bon devinèt!','Great guess!'):say('Sipriz!','Surprise!'))+' '+
        state.names[guesser]+' '+(entry.guesserPoints>=0?'+':'')+entry.guesserPoints+say(' pwen. ',' points. ')+
        state.names[subject]+' '+(entry.forecastRight?'+1':'+0')+say(' pwen pou previzyon an.',' points for the prediction.')+
        '\n\n'+score()+'\n\n'+say('Pale sou poukisa ou te chwazi sa!','Tell each other why you chose it!');
      button(say('Pwochen kestyon','Next question'),()=>act('continue'));
    }
    if(state.phase==='end') {
      const [a,b]=state.players.map(player=>player.score);
      title.textContent=a===b?say('Nou egal!','It’s a tie!'):
        state.names[a>b?0:1]+say(' genyen!',' wins!');
      body.textContent=score()+'\n\n'+say('Douz kestyon, sis pou chak moun. Konbyen sipriz nou dekouvri?',
        'Twelve questions, six about each player. What surprised you most?');
      button(say('Jwe ankò nan lòt lòd','Rematch with a new order'),()=>{
        state=newKnow(state.names,state.replay+1);save();render();
      });
    }
    if(storageFailed) body.textContent+='\n\n'+say('Navigatè a pa t sove match la.','The browser could not save this match.');
    focus();
  }
  return {menu,stop};
}
