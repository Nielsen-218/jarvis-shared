import {badges, bluffCard, challenge, movesFor, newRivalry, objectives, restoreRivalry, scenarios, stepRivalry} from './games-rivalry.mjs';

const KEY = 'jarvis_playhouse_rivalry_v1';
const newSeed = () => crypto.getRandomValues(new Uint32Array(1))[0];

export function installRivalry({say, pick, button, title, body, label, buttons, focus}) {
  let state = null;
  let revealed = false;
  let timer = null;
  let storageFailed = false;
  try {
    const raw = localStorage.getItem(KEY);
    if (raw && raw.length < 12000) state = restoreRivalry(JSON.parse(raw));
  } catch {}

  function stop() {
    clearTimeout(timer);
    timer = null;
    revealed = false;
  }

  function save() {
    try {
      const {names,replay,seed,scenario,actions} = state;
      localStorage.setItem(KEY,JSON.stringify({names,replay,seed,scenario,actions}));
      storageFailed = false;
    } catch {storageFailed = true;}
  }

  function act(action, keepRevealed = false) {
    state = stepRivalry(state, action);
    if (!keepRevealed) stop();
    save();
    render();
  }

  function score() {
    return state.players.map((player,index)=>
      (player.role===0?'🐐 ':'🦜 ') + state.names[index] + ' · ' + player.score +
      say(' pwen · ',' points · ') + player.coins + say(' pyès',' coins') +
      (player.shield ? say(' · 2yèm chans',' · second chance') : ''),
    ).join('\n');
  }

  function menu(back) {
    stop();
    buttons.replaceChildren();
    title.textContent = say('Rivalite Lantèn','Lantern Rivalry');
    label.textContent = say('2 jwè · 3 senaryo · 12 defi chak','2 players · 3 scenarios · 12 challenges each');
    body.textContent = say(
      'Nou sou menm telefòn nan. Chwazi yon senaryo, mete non nou, epi pase aparèy la pou chak objektif sekrè.',
      'Share one phone. Choose a scenario, enter your names, then pass the device for each secret objective.',
    );
    const scenarioField = document.createElement('label');
    scenarioField.textContent = say('Senaryo','Scenario');
    const scenarioSelect = document.createElement('select');
    scenarioSelect.className = 'rivalry-input';
    scenarios.forEach((scenario,index)=>{
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = pick(scenario.title);
      scenarioSelect.append(option);
    });
    scenarioSelect.value = String(state?.scenario ?? 0);
    scenarioField.append(scenarioSelect);
    buttons.append(scenarioField);
    const inputs = [0,1].map(index=>{
      const field = document.createElement('label');
      field.textContent = say('Non jwè ','Player name ') + (index+1);
      const input = document.createElement('input');
      input.type = 'text';
      input.maxLength = 20;
      input.value = state?.names[index] ?? '';
      input.autocomplete = 'off';
      input.className = 'rivalry-input';
      field.append(input);
      buttons.append(field);
      return input;
    });
    const error = document.createElement('p');
    error.setAttribute('role','status');
    buttons.append(error);
    button(say('Kòmanse yon match','Start a match'),()=>{
      try {state = newRivalry(inputs.map(input=>input.value),state ? state.replay+1 : 0,newSeed(),Number(scenarioSelect.value));}
      catch {error.textContent = say('Mete de non diferan.','Enter two different names.');return;}
      save();
      render();
    });
    if (state) button(say('Relouvri match la','Open saved match'),render);
    button(say('Chwazi yon lòt mòd','Choose another mode'),back);
    focus();
  }

  function privateTurn() {
    const {seat,round,players,phase} = state;
    const name = state.names[seat];
    title.textContent = (players[seat].role===0?'🐐 ':'🦜 ') + name;
    if (!revealed) {
      body.textContent = say('Pase telefòn nan bay ','Pass the phone to ') + name + '. ' +
        say('Lòt jwè a vire do li.','The other player looks away.');
      button(say('Mwen pare','I’m ready'),()=>{revealed=true;render();});
      return;
    }
    if (phase === 'brief') {
      body.textContent = pick(objectives[players[seat].role]) + '\n\n' + say(
        'Pi gwo kantite pwen genyen. Bon repons bay 2 pwen, oswa 4 nan final la. Nan chwa chemen yo, ou ka ranmase pyès, achte 1 pwen, pran yon dezyèm chans, oswa voye kabrit la pran pyès nan men advèsè a. Nan de defi paryaj yo, riske 2 pyès pou touche 2 pwen ak 2 pyès anplis. Si w rate, ou pèdi paryaj la. Yon dezyèm chans bay 1 pwen epi pwoteje pyès ou lè w rate, yon sèl fwa. Objektif sekrè a ka ba w 3 pwen nan fen match la. Si gen egalite, plis bon repons genyen, epi plis pyès.',
        'Highest score wins. Correct answers earn 2 points, or 4 in the showdown. On route turns, collect coins, buy 1 point, take a second chance, or send the goat after your rival’s coins. On two wager challenges, risk 2 coins to earn 2 extra points and 2 coins. A miss costs the stake. A second chance gives 1 point and protects your coins on one miss. Your secret objective can add 3 points at the end. Ties go to more correct answers, then more coins.',
      );
      button(say('Eseye yon paryaj pou pratike','Try a practice wager'),()=>{
        body.textContent = say('Ou gen 2 pyès. Si w parye yo epi ou jwenn bon repons lan, konbyen pwen w ap touche nan yon defi nòmal?',
          'You have 2 coins. If you stake them and answer correctly, how many points do you earn in an ordinary challenge?');
        buttons.replaceChildren();
        for (const amount of [2,4]) button(amount + say(' pwen',' points'),()=>{
          body.textContent = amount===4 ? say('Kòrèk! 2 pwen pou repons lan ak 2 pwen pou paryaj la.',
            'Correct! 2 points for the answer and 2 more for the wager.') :
            say('Ou touche 4 pwen: 2 pou repons lan ak 2 pou paryaj la.','You earn 4 points: 2 for the answer and 2 for the wager.');
          buttons.replaceChildren();
          button(say('Mwen konprann','I understand'),()=>act('ready'));
          focus();
        });
        focus();
      });
      button(say('Mwen konprann objektif mwen','I understand my objective'),()=>act('ready'));
      return;
    }
    const step = challenge(state);
    const player = players[seat];
    if (step.kind==='bluff') {
      const {actual,clue} = bluffCard(state);
      if (state.claim===null) {
        body.textContent = say('Senbòl ou se ','Your badge is a ') + pick(badges[actual]) + '. ' +
          say('Lòt jwè a pral konnen ou pa gen ','Your rival will be told you do not have a ') + pick(clue) + '. ' +
          say('Chwazi sa w ap di li. Ou touche 2 pwen si li devine mal.',
            'Choose what to claim. You earn 2 points if the other player guesses wrong.');
        badges.forEach((badge,index)=>button(pick(badge),()=>act({claim:index})));
      } else {
        body.textContent = say('Lòt jwè a di li gen ','Your rival claims to have a ') + pick(badges[state.claim]) + '.\n\n' +
          say('Endis: Li pa gen ','Clue: They do not have a ') + pick(clue) + '. ' +
          say('Obsève figi li! Ou touche 2 pwen si w devine byen.','Watch their face! A correct call earns 2 points.');
        button(say('Mwen kwè li','I believe them'),()=>act({believe:true}));
        button(say('Se blòf','Call their bluff'),()=>act({believe:false}));
      }
      return;
    }
    const heading = step.kind==='route' ? step.title : step.prompt;
    body.textContent = pick(heading);
    if (step.kind==='route') {
      movesFor(round,state.scenario).forEach((move,index)=>{
        button(pick(move),()=>act({move:index}));
        buttons.lastElementChild.disabled = index!==0 && (!player.coins || (index===2 && player.shield));
      });
      return;
    }
    if (step.kind==='order') {
      let order = '';
      const sequence = document.createElement('p');
      sequence.setAttribute('aria-live','polite');
      buttons.append(sequence);
      const tiles = [];
      const refresh = () => {
        sequence.textContent = say('Lòd ou: ','Your order: ') + (order || '—');
        tiles.forEach((tile,i)=>tile.disabled=order.includes(step.tiles[i]));
        submit.disabled = order.length!==3;
      };
      for (const number of step.tiles) {
        button(number,()=>{order+=number;refresh();(tiles.find(tile=>!tile.disabled) || submit).focus();});
        tiles.push(buttons.lastElementChild);
      }
      button(say('Rekòmanse lòd la','Reset order'),()=>{order='';refresh();tiles[0].focus();});
      button(say('Valide lòd la','Submit order'),()=>act({answer:order}));
      const submit = buttons.lastElementChild;
      refresh();
      return;
    }
    if (step.kind==='memory') {
      button(say('Montre chif yo pandan 4 segonn','Show the digits for 4 seconds'),()=>{
        buttons.replaceChildren();
        body.textContent = step.answer.split('').join('  ');
        const hide = () => {
          clearTimeout(timer);
          body.textContent = say('Ekri chif ou sonje yo nan lòd.','Enter the digits in order.');
          buttons.replaceChildren();
          answerInput();
          focus();
        };
        button(say('Kache chif yo','Hide digits'),hide);
        focus();
        timer=setTimeout(hide,4000);
      });
      return;
    }
    if (step.kind==='wager') {
      if (state.stake!==null) {
        body.textContent += '\n\n' + say('Paryaj ou: ','Your stake: ') + state.stake + say(' pyès',' coins');
        answerInput();
        return;
      }
      body.textContent = say('Chwazi paryaj ou anvan ou wè defi a.','Set your stake before seeing your puzzle.');
      const chooseStake = stake => act({stake},true);
      button(say('Jwe san paryaj','Play without a stake'),()=>chooseStake(0));
      button(say('Riske 2 pyès pou 2 pwen anplis','Risk 2 coins for 2 bonus points'),()=>chooseStake(2));
      buttons.lastElementChild.disabled = player.coins<2;
      return;
    }
    answerInput();
  }

  function answerInput() {
    const field = document.createElement('label');
    field.textContent = say('Repons an chif','Answer in digits');
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.maxLength = 3;
    input.className = 'rivalry-input';
    field.append(input);
    buttons.append(field);
    button(say('Valide repons lan','Submit answer'),()=>act({answer:input.value.trim()}));
    const submit = buttons.lastElementChild;
    submit.disabled = true;
    input.addEventListener('input',()=>{submit.disabled=!/^\d{1,3}$/.test(input.value.trim());});
    input.addEventListener('keydown',event=>{
      if(event.key==='Enter' && !submit.disabled){event.preventDefault();submit.click();}
    });
  }

  function render() {
    buttons.replaceChildren();
    const {round,phase,names,end,players} = state;
    document.querySelectorAll('.progress i').forEach((node,index)=>node.classList.toggle('on',index<=Math.floor(round/4)));
    label.textContent = phase==='brief' ? pick(scenarios[state.scenario].title) + ' · ' + say('Objektif sekrè','Secret objective') :
      pick(scenarios[state.scenario].acts[Math.floor(round/4)]) + ' · ' + (round+1) + ' / 12';
    if (phase==='brief' || phase==='play') privateTurn();
    if (phase==='result') {
      title.textContent = say('Rezilta defi a','Challenge results');
      body.textContent = state.log.slice(-2).map(entry=>{
        if (entry.bluff) return names[entry.winner] + say(' · +2 pwen · Senbòl la: ',' · +2 points · Actual badge: ') + pick(badges[entry.actual]);
        const result = entry.right===null ? pick(movesFor(round,state.scenario)[entry.move]) :
          (entry.right ? say('Kòrèk','Correct') : say('Rate','Missed')) +
          say(' · repons: ',' · answer: ') + entry.answer +
          (entry.shieldUsed ? say(' · dezyèm chans',' · second chance') : '');
        return names[entry.seat] + ' · ' + result + (entry.toll!==undefined ? say(' · Pyès kabrit la pran: ',' · Coins the goat took: ')+entry.toll : '') + ' · ' +
          (entry.points>=0?'+':'') + entry.points + say(' pwen',' points') + ' · ' +
          (entry.coins>=0?'+':'') + entry.coins + say(' pyès',' coins');
      }).join('\n') + '\n\n' + score();
      button(say('Kontinye','Continue'),()=>act('continue'));
    }
    if (phase==='end') {
      title.textContent = end===null ? say('Nou egal!','A shared victory!') : names[end] + say(' genyen!',' wins!');
      const reason = end===null ? say('Menm pwen, menm kantite bon repons, menm kantite pyès.',
        'Equal points, correct answers, and coins.') : players[0].score!==players[1].score ?
        say('Plis pwen apre bonis objektif yo.','More points after objective bonuses.') :
        players[0].correct!==players[1].correct ? say('Menm pwen. Plis bon repons.','Equal points. More correct answers.') :
        say('Menm pwen ak bon repons. Plis pyès.','Equal points and correct answers. More coins.');
      const endings = scenarios[state.scenario].endings;
      body.textContent = reason + '\n\n' + score() + '\n\n' + players.map((player,index)=>
        names[index] + ' · ' + pick(objectives[player.role]) + ' · +' + player.bonus + say(' pwen',' points'),
      ).join('\n') + '\n\n' + pick(endings[end===null ? 0 : players[end].coins>=4 ? 1 : 2]);
      button(say('Pwochen senaryo','Next scenario'),()=>{
        state=newRivalry(names,state.replay+1,newSeed(),(state.scenario+1)%scenarios.length);
        save();render();
      });
    }
    if (storageFailed) body.textContent += '\n\n' + say('Navigatè a pa t sove match la.','The browser could not save this match.');
    focus();
  }
  return {menu, stop};
}
