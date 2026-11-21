const t = (ht, en) => ({ht, en});
const route = (ht, en) => ({kind:'route', title:t(ht,en)});
const puzzle = (kind, ht, en, answer, tiles) => ({kind, prompt:t(ht,en), answer, tiles});
const order = (ht,en,answer) => puzzle('order',ht,en,answer,['1','2','3']);
const code = (ht,en,answer) => puzzle('code',ht,en,answer);
const memory = (ht,en,answer) => puzzle('memory',ht,en,answer);
const wager = (ht,en,answer) => puzzle('wager',ht,en,answer);
export const acts = [t('Mache a','The market'),t('Tanpèt la','The storm'),t('Dènye parad la','The final parade')];
export const rounds = [
  route('Chwazi chemen pou rive nan fèt la','Choose your route to the festival'),
  order('Mete bwat yo depi pi lejè rive pi lou. Bwat 1 peze 6 kilo, bwat 2 peze 2, bwat 3 peze 4.',
    'Stack the crates from lightest to heaviest. Crate 1 weighs 6 kg, crate 2 weighs 2, and crate 3 weighs 4.','231'),
  memory('Gad la montre w twa chif. Sonje yo, paske l ap kache yo.','The guard flashes three digits. Remember them before they disappear.','472'),
  code('Ou gen 4 panyen ak 3 mango nan chak. Yon kabrit manje 2 mango. Konbyen ki rete?',
    'There are 4 baskets with 3 mangoes each. A goat eats 2 mangoes. How many remain?','10'),
  route('Lapli a kòmanse. Chwazi sa w ap pran nan depo a.','The storm hits. Choose what to take from the warehouse.'),
  order('Frè a pase anvan sè a. Sè a pase anvan kouzen an. Frè a gen nimewo 3, sè a gen 1, kouzen an gen 2.',
    'Brother crosses before Sister. Sister crosses before Cousin. Brother is 3, Sister is 1, Cousin is 2. Set the crossing order.','312'),
  wager('Ou jwenn 15 pyès. Ou depanse 4 epi ou jwenn 3 ankò. Konbyen ou genyen kounye a?',
    'You find 15 coins, spend 4, then find 3 more. How many do you have now?','14'),
  memory('Kabrit la pase ak twa etikèt sou do li. Sonje chif yo nan lòd.','The goat trots past with three numbered tags. Remember them in order.','805'),
  route('Foul la pare pou parad la. Chwazi dènye avantaj ou.','The crowd is ready. Choose your last advantage.'),
  {kind:'bluff'},
  order('Lantèn 2 dwe limen dènye. Lantèn 3 dwe limen anvan lantèn 1. Nan ki lòd w ap limen yo?',
    'Lantern 2 must be lit last. Lantern 3 must be lit before lantern 1. In what order do you light them?','312'),
  wager('Nan dènye defi a, chak pè tanbou gen 2 tanbou. Gen 9 pè, men 5 tanbou mouye. Konbyen ki rete sèk?',
    'In the showdown, each pair has 2 drums. There are 9 pairs, but 5 drums got wet. How many are dry?','13'),
];

// Each player receives a parallel challenge, so passing the phone cannot give
// away the other player's answer. Replay reverses the assignments.
const alternate = [
  null,
  order('Bwat 1 peze 5 kilo, bwat 2 peze 7, bwat 3 peze 3. Mete yo depi pi lejè rive pi lou.',
    'Crate 1 weighs 5 kg, crate 2 weighs 7, crate 3 weighs 3. Stack them lightest to heaviest.','312'),
  memory('Sonje twa chif ki sou kat gad la.','Remember the three digits on the guard’s card.','639'),
  code('Gen 4 sak ak 4 zoranj nan chak. Kabrit la manje 3. Konbyen ki rete?',
    'Four bags hold 4 oranges each. The goat eats 3. How many remain?','13'),
  null,
  order('Madanm 2 pase anvan mesye 1. Timoun 3 pase anvan madanm 2. Mete yo nan lòd.',
    'Woman 2 crosses before Man 1. Child 3 crosses before Woman 2. Set the order.','321'),
  wager('Ou jwenn 16 pyès. Ou depanse 5 epi ou jwenn 2 ankò. Konbyen ou genyen kounye a?',
    'You find 16 coins, spend 5, then find 2 more. How many do you have now?','13'),
  memory('Sonje twa chif kabrit la pote yo.','Remember the three digits the goat is carrying.','206'),
  null,
  null,
  order('Lantèn 1 dwe limen dènye. Lantèn 2 dwe limen anvan lantèn 3. Mete yo nan lòd.',
    'Lantern 1 must be lit last. Lantern 2 must be lit before lantern 3. Set the order.','231'),
  wager('Gen 8 pè tanbou. Chak pè gen 2 tanbou. Kat mouye. Konbyen tanbou ki rete sèk?',
    'There are 8 pairs of drums. Each pair has 2 drums. Four got wet. How many are dry?','12'),
];
export const scenarios = [
  {
    title:t('Lantèn nan fèt la','Festival Lantern'), acts, rounds, alternate,
    endings:[
      t('De lantèn rive ansanm. Kabrit la pran toude riban yo!','Both lanterns arrive together. The goat takes both ribbons!'),
      t('Gayan an sèvi ak pyès li pou fè tout moun jwenn ti gato. Kabrit la mande yon dezyèm!',
        'The winner buys pastries for everyone. The goat asks for seconds!'),
      t('Gayan an limen dènye lantèn nan. Kabrit la danse ak riban an!',
        'The winner lights the last lantern. The goat dances away with the ribbon!'),
    ],
  },
  {
    title:t('Mache lannwit lan','The Night Market'),
    acts:[t('Pòt mache a','The market gate'),t('Limyè yo etenn','The lights go out'),t('Gwo sèn nan','The main stage')],
    rounds:[
      route('Mache lannwit lan ouvè. Chwazi kijan w ap antre.','The night market opens. Choose how to enter.'),
      order('Lanp 1 dire 3 èdtan, lanp 2 dire 5, lanp 3 dire 2. Mete yo depi sa ki dire pi piti a.',
        'Lamp 1 lasts 3 hours, lamp 2 lasts 5, lamp 3 lasts 2. Order shortest to longest.','312'),
      memory('Machann nan montre yon tikè ak twa chif. Sonje yo anvan li vire l.','The seller flashes a three-digit ticket. Remember it before she flips it.','581'),
      code('Gen 6 tab ak 4 ti gato sou chak. Kabrit la manje 5. Konbyen ti gato ki rete?',
        'Six tables have 4 pastries each. The goat eats 5. How many remain?','19'),
      route('Kouran an koupe. Chwazi kijan w ap ede moun sou sèn nan.','The power goes out. Choose how to help the stage crew.'),
      order('Lanp 2 dwe limen anvan lanp 1. Lanp 1 dwe limen anvan lanp 3. Mete yo nan lòd.',
        'Lamp 2 must light before lamp 1, and lamp 1 before lamp 3. Set the order.','213'),
      wager('Gen 9 bwat ak 2 ti limyè nan chak. Kat pa mache. Konbyen ki mache?',
        'Nine boxes hold 2 spark lights each. Four are broken. How many work?','14'),
      memory('Gad sèn nan montre twa chif pou ouvri pòt la. Sonje lòd la.','The stage guard shows three digits for the door. Remember their order.','740'),
      route('Limyè yo retounen. Chwazi dènye avantaj ou anvan konkou a.','The lights return. Choose your last advantage before the contest.'),
      {kind:'bluff'},
      order('Sèn 1 ouvè apre sèn 2, men anvan sèn 3. Nan ki lòd yo ouvè?',
        'Stage 1 opens after stage 2 but before stage 3. What is their order?','213'),
      wager('Gen 12 panyen ak 2 riban nan chak. Sèt riban pèdi. Konbyen ki rete?',
        'Twelve baskets hold 2 ribbons each. Seven are lost. How many remain?','17'),
    ],
    alternate:[
      null,
      order('Lanp 1 dire 4 èdtan, lanp 2 dire 2, lanp 3 dire 6. Mete yo depi sa ki dire pi piti a.',
        'Lamp 1 lasts 4 hours, lamp 2 lasts 2, lamp 3 lasts 6. Order shortest to longest.','213'),
      memory('Sonje twa chif ki sou tikè machann nan.','Remember the three digits on the seller’s ticket.','947'),
      code('Gen 5 ti boutik ak 3 drapo nan chak. Van an pote 4 ale. Konbyen ki rete?',
        'Five stalls have 3 flags each. Wind carries 4 away. How many remain?','11'),
      null,
      order('Lanp 3 dwe limen anvan lanp 2. Lanp 2 dwe limen anvan lanp 1. Mete yo nan lòd.',
        'Lamp 3 must light before lamp 2, and lamp 2 before lamp 1. Set the order.','321'),
      wager('Gen 8 kòd ak 3 klòch sou chak. Senk klòch pèdi. Konbyen ki rete?',
        'Eight cords hold 3 bells each. Five bells go missing. How many remain?','19'),
      memory('Gad sèn nan montre yon lòt kòd twa chif. Sonje lòd la.','The stage guard shows another three-digit code. Remember its order.','286'),
      null,null,
      order('Sèn 3 ouvè anvan sèn 1. Sèn 1 ouvè anvan sèn 2. Nan ki lòd yo ouvè?',
        'Stage 3 opens before stage 1, and stage 1 before stage 2. Set the order.','312'),
      wager('Gen 7 pakèt ak 3 lantèn nan chak. Senk rete etenn. Konbyen ki limen?',
        'Seven bundles hold 3 lanterns each. Five stay unlit. How many are lit?','16'),
    ],
    endings:[
      t('De ekip yo limen mache a ansanm. Kabrit la kouri ak de riban!','Both teams light up the market. The goat runs off with two ribbons!'),
      t('Gayan an achte ti gato pou foul la. Tout moun mande yon lòt tou!',
        'The winner buys pastries for the crowd. Everyone asks for another round!'),
      t('Gayan an limen gwo sèn nan. Kabrit la monte sou sèn pou danse!',
        'The winner lights the main stage. The goat climbs up to dance!'),
    ],
  },
  {
    title:t('Fèt bò rivyè a','The River Festival'),
    acts:[t('Bò dlo a','By the water'),t('Pon an anba lapli','The rainy bridge'),t('Parad sou rivaj la','The shore parade')],
    rounds:[
      route('Fèt bò rivyè a kòmanse. Chwazi kijan w ap rive nan waf la.','The river festival begins. Choose how to reach the dock.'),
      order('Bato 1 gen 5 plas, bato 2 gen 3, bato 3 gen 7. Mete yo depi sa ki gen mwens plas la.',
        'Boat 1 has 5 seats, boat 2 has 3, boat 3 has 7. Order fewest to most.','213'),
      memory('Kapitèn nan montre twa chif sou yon kat. Sonje yo nan lòd.','The captain flashes three digits on a card. Remember their order.','362'),
      code('Gen 5 kannòt ak 4 zaviwon pou chak. Twa zaviwon kase. Konbyen ki rete bon?',
        'Five rafts have 4 paddles each. Three break. How many still work?','17'),
      route('Lapli frape pon an. Chwazi sa w ap pran pou ede ekip la.','Rain hits the bridge. Choose what to take to help the crew.'),
      order('Moun 3 travèse anvan moun 1. Moun 1 travèse anvan moun 2. Mete yo nan lòd.',
        'Person 3 crosses before person 1, and person 1 before person 2. Set the order.','312'),
      wager('Gen 6 pè zaviwon. Twa zaviwon pèdi. Konbyen zaviwon ki rete?',
        'There are 6 pairs of paddles. Three go missing. How many remain?','9'),
      memory('Gad pon an montre twa chif pou ouvri baryè a. Sonje yo.','The bridge keeper shows three digits to open the gate. Remember them.','109'),
      route('Solèy la retounen sou rivaj la. Chwazi dènye avantaj ou.','The sun returns to the shore. Choose your last advantage.'),
      {kind:'bluff'},
      order('Limyè 2 dwe limen dènye. Limyè 1 dwe limen anvan limyè 3. Mete yo nan lòd.',
        'Light 2 must be lit last, and light 1 before light 3. Set the order.','132'),
      wager('Gen 9 panyen ak 3 zoranj nan chak. Yo manje 8. Konbyen ki rete?',
        'Nine baskets hold 3 oranges each. Eight get eaten. How many remain?','19'),
    ],
    alternate:[
      null,
      order('Bato 1 gen 4 plas, bato 2 gen 8, bato 3 gen 6. Mete yo depi sa ki gen mwens plas la.',
        'Boat 1 has 4 seats, boat 2 has 8, boat 3 has 6. Order fewest to most.','132'),
      memory('Sonje twa chif kapitèn nan montre sou yon lòt kat.','Remember the three digits on the captain’s other card.','914'),
      code('Gen 4 tant ak 5 gode nan chak. Yo pran 6 gode. Konbyen ki rete?',
        'Four tents have 5 cups each. Six are taken. How many remain?','14'),
      null,
      order('Moun 1 travèse anvan moun 3. Moun 3 travèse anvan moun 2. Mete yo nan lòd.',
        'Person 1 crosses before person 3, and person 3 before person 2. Set the order.','132'),
      wager('Gen 5 bato ak 3 jilè sovtaj sou chak. Kat jilè mouye. Konbyen ki rete sèk?',
        'Five boats have 3 life jackets each. Four get wet. How many stay dry?','11'),
      memory('Gad pon an montre yon lòt kòd twa chif. Sonje lòd la.','The bridge keeper shows another three-digit code. Remember its order.','684'),
      null,null,
      order('Limyè 3 dwe limen dènye. Limyè 2 dwe limen anvan limyè 1. Mete yo nan lòd.',
        'Light 3 must be lit last, and light 2 before light 1. Set the order.','213'),
      wager('Gen 8 ti bato ak 3 drapo sou chak. Sis drapo pèdi. Konbyen ki rete?',
        'Eight small boats have 3 flags each. Six are lost. How many remain?','18'),
    ],
    endings:[
      t('De ekip yo rive bò dlo a ansanm. Kabrit la pran de kouwòn yo!',
        'Both teams reach the shore together. The goat grabs both crowns!'),
      t('Gayan an sèvi ak pyès li pou fè yon fèt sou rivaj la. Kabrit la pran premye dans lan!',
        'The winner funds a shore picnic. The goat takes the first dance!'),
      t('Gayan an limen dènye limyè bò dlo a. Foul la bat bravo!',
        'The winner lights the last riverside lamp. The crowd cheers!'),
    ],
  },
];
export const objectives = [
  t('🐐 Gid la · Rezoud omwen 2 defi lòd pou touche 3 pwen.','🐐 Guide · Solve at least 2 ordering puzzles to earn 3 points.'),
  t('🦜 Kontab la · Gen omwen 4 pyès lè parad la fini pou touche 3 pwen.','🦜 Treasurer · Finish with at least 4 coins to earn 3 points.'),
];
export const routeMoves = [
  t('Fè yon ti travay nan mache a · +2 pyès','Help at a stall · +2 coins'),
  t('Pran yon rakousi · +1 pwen, peye 1 pyès','Take a shortcut · +1 point, spend 1 coin'),
  t('Prepare yon dezyèm chans · peye 1 pyès','Prepare a second chance · spend 1 coin'),
  t('Voye kabrit la pran jiska 2 pyès nan men lòt jwè a · peye 1 pyès',
    'Send the goat after up to 2 of your rival’s coins · spend 1 coin'),
];
const scenarioMoves = [
  null,
  [
    [routeMoves[0],t('Pran ti pasaj la · +1 pwen, −1 pyès','Take the side passage · +1 point, −1 coin'),
      t('Pran yon lanp rezèv · −1 pyès','Take a spare lamp · −1 coin')],
    [t('Ede ekip la relimen mache a · +2 pyès','Help relight the market · +2 coins'),
      t('Pran pasaj dèyè sèn nan · +1 pwen, −1 pyès','Take the backstage passage · +1 point, −1 coin'),
      t('Pran yon kòd sekou · −1 pyès','Take a rescue rope · −1 coin'),routeMoves[3]],
    [t('Ede ekip parad la · +2 pyès','Help the parade crew · +2 coins'),
      t('Monte sou sèn an premye · +1 pwen, −1 pyès','Reach the stage first · +1 point, −1 coin'),
      t('Pran yon parapli rezèv · −1 pyès','Take a spare umbrella · −1 coin'),routeMoves[3]],
  ],
  [
    [t('Ede ekip waf la · +2 pyès','Help the dock crew · +2 coins'),
      t('Pran ti chemen bò dlo a · +1 pwen, −1 pyès','Take the waterside path · +1 point, −1 coin'),
      t('Pran yon kòd rezèv · −1 pyès','Take a spare rope · −1 coin')],
    [t('Ede ekip pon an · +2 pyès','Help the bridge crew · +2 coins'),
      t('Lwe yon kannòt · +1 pwen, −1 pyès','Rent a canoe · +1 point, −1 coin'),
      t('Pran yon jilè sovtaj · −1 pyès','Take a life jacket · −1 coin'),routeMoves[3]],
    [t('Ede ekip parad bò dlo a · +2 pyès','Help the shore parade crew · +2 coins'),
      t('Antre nan parad la an premye · +1 pwen, −1 pyès','Enter the parade first · +1 point, −1 coin'),
      t('Pran yon lanp rezèv · −1 pyès','Take a spare lamp · −1 coin'),routeMoves[3]],
  ],
];
export function movesFor(round, scenario = 0) {
  if (scenario) return scenarioMoves[scenario][round/4];
  if (round===0) return routeMoves.slice(0,3);
  if (round===4) return [
    t('Ede machann yo kouvri machandiz yo · +2 pyès','Help cover the market stalls · +2 coins'),
    t('Peye pou travèse sou yon ti bato · +1 pwen, −1 pyès','Pay for a skiff crossing · +1 point, −1 coin'),
    t('Pran yon kòd pou yon dezyèm chans · −1 pyès','Take a rescue rope for a second chance · −1 coin'),
    routeMoves[3],
  ];
  return [
    t('Ede ekip la prepare parad la · +2 pyès','Help the parade crew · +2 coins'),
    t('Peye pou antre an premye · +1 pwen, −1 pyès','Pay to enter first · +1 point, −1 coin'),
    t('Pran yon parapli pou yon dezyèm chans · −1 pyès','Take an umbrella for a second chance · −1 coin'),
    routeMoves[3],
  ];
}
export const badges = [t('zetwal','star'),t('lalin','moon'),t('solèy','sun')];
export function bluffCard(state) {
  // Null preserves the badge sequence in saves made before match seeds existed.
  const actual = state.seed === null ? (state.replay + state.turns) % badges.length :
    (Math.imul(state.seed ^ (state.turns + 1), 0x9e3779b1) >>> 0) % badges.length;
  return {actual, clue:badges[(actual+1)%badges.length]};
}
export function challenge(state) {
  const pack = scenarios[state.scenario];
  const step = pack.rounds[state.round];
  return (state.seat + state.replay) % 2 && pack.alternate[state.round] || step;
}
export function newRivalry(names, replay = 0, seed = null, scenario = 0) {
  if (!Array.isArray(names) || names.length !== 2 || names.some(name => typeof name !== 'string' || !name.trim() || name.length > 20) ||
      names[0].trim().toLowerCase() === names[1].trim().toLowerCase() || !Number.isSafeInteger(replay) || replay < 0 ||
      (seed !== null && (!Number.isInteger(seed) || seed < 0 || seed > 0xffffffff)) ||
      !Number.isInteger(scenario) || scenario < 0 || scenario >= scenarios.length)
    throw new Error('Invalid players');
  return {names:names.map(name=>name.trim()), replay, seed, scenario, round:0, seat:replay%2, turns:0, phase:'brief',
    players:Array.from({length:2},(_,i)=>({score:0,coins:2,shield:false,orders:0,correct:0,role:(i+replay)%2})),
    log:[], actions:[], end:null, claim:null, stake:null};
}
export function stepRivalry(state, action) {
  const next = {...state, actions:[...state.actions,action]};
  if (state.phase === 'brief' && action === 'ready') {
    return state.turns ? {...next,phase:'play',turns:0,seat:1-state.seat} :
      {...next,turns:1,seat:1-state.seat};
  }
  if (state.phase === 'result' && action === 'continue') {
    if (state.round < 11) return {...next,round:state.round+1,phase:'play',turns:0};
    const players = state.players.map(player=>{
      const bonus = (player.role===0 ? player.orders>=2 : player.coins>=4) ? 3 : 0;
      return {...player,bonus,score:player.score+bonus};
    });
    const [a,b] = players;
    const diff = a.score-b.score || a.correct-b.correct || a.coins-b.coins;
    return {...next,players,phase:'end',end:diff===0?null:diff>0?0:1};
  }
  if (state.phase !== 'play' || !action || typeof action !== 'object') throw new Error('Invalid turn');
  const step = challenge(state);
  if (step.kind === 'bluff') {
    const {actual} = bluffCard(state);
    if (state.claim === null && [0,1,2].includes(action.claim))
      return {...next,claim:action.claim,seat:1-state.seat};
    if (state.claim === null || typeof action.believe !== 'boolean') throw new Error('Invalid bluff');
    const right = action.believe === (state.claim === actual);
    const winner = right ? state.seat : 1-state.seat;
    const players = state.players.map((player,index)=>index===winner ? {...player,score:player.score+2} : player);
    const entry = {round:state.round,seat:state.seat,right,bluff:true,winner,actual,claim:state.claim};
    return {...next,players,log:[...state.log,entry],claim:null,turns:state.turns+1,
      phase:state.turns ? 'result' : 'play'};
  }
  const player = {...state.players[state.seat]};
  const before = {...player};
  if (step.kind==='wager' && state.stake===null) {
    if (![0,2].includes(action.stake) || action.stake>player.coins) throw new Error('Invalid stake');
    return {...next,stake:action.stake};
  }
  let right = null;
  if (step.kind === 'route') {
    const move = action.move;
    if (!Number.isInteger(move) || move<0 || move>=movesFor(state.round,state.scenario).length ||
        (move!==0 && !player.coins) || (move===2 && player.shield))
      throw new Error('Invalid route');
    player.coins += move===0?2:-1;
    if (move===1) player.score++;
    if (move===2) player.shield=true;
  } else {
    if (typeof action.answer !== 'string' || !/^\d{1,3}$/.test(action.answer)) throw new Error('Invalid answer');
    const stake = state.stake ?? 0;
    right = action.answer === step.answer;
    if (right) {
      player.correct++;
      if (step.kind==='order') player.orders++;
      player.score += (state.round===11 ? 4 : 2) + stake;
      player.coins += stake;
    } else {
      if (player.shield) {player.shield=false;player.score++;}
      else player.coins -= stake;
    }
  }
  const entry = {round:state.round,seat:state.seat,right,answer:step.answer,
    points:player.score-before.score,coins:player.coins-before.coins,move:action.move,
    shieldUsed:before.shield && !player.shield};
  let players = state.players.map((previous,i)=>i===state.seat?player:previous);
  let log = [...state.log,entry];
  if (state.turns && step.kind==='route') {
    const resolved = log.slice(-2).map(turn=>{
      if (turn.move!==3) return turn;
      const opponent = players[1-turn.seat];
      const toll = Math.min(2,opponent.coins);
      players = players.map((p,i)=>i===turn.seat?p:{...p,coins:p.coins-toll});
      return {...turn,toll};
    });
    log = [...log.slice(0,-2),...resolved];
  }
  return {...next,players,log,seat:1-state.seat,turns:state.turns+1,stake:null,
    phase:state.turns ? 'result' : 'play'};
}
export function restoreRivalry(save) {
  if (!save || !Array.isArray(save.actions) || save.actions.length>44) throw new Error('Invalid save');
  return save.actions.reduce(stepRivalry,newRivalry(save.names,save.replay,save.seed ?? null,save.scenario ?? 0));
}
