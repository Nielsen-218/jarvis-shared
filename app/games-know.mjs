const t = (ht,en) => ({ht,en});
export const knowCards = [
  {question:t('Si mwen gen yon samdi lib, kisa m ap chwazi?','If I have a free Saturday, what would I choose?'),choices:[t('Fè yon bagay bon pou manje','Cook something tasty'),t('Ale nan yon pak','Visit a park'),t('Gade yon fim','Watch a movie')]},
  {question:t('Ki ti goute mwen ta pran pou sware jwèt la?','Which snack would I pick for game night?'),choices:[t('Pòpkòn','Popcorn'),t('Fwi','Fruit'),t('Bonbon','Cookies')]},
  {question:t('Ki ti pouvwa mwen ta renmen genyen?','Which small superpower would I want?'),choices:[t('Kanpe tan an','Pause time'),t('Pale ak bèt yo','Talk to animals'),t('Telepòte yon ti distans','Teleport a short distance')]},
  {question:t('Lè lapli tonbe, kisa m ta pito fè?','What would I rather do on a rainy day?'),choices:[t('Fè yon kastèt','Do a puzzle'),t('Danse lakay','Dance at home'),t('Gade yon fim anba dra','Watch a movie under a blanket')]},
  {question:t('Ki bèt imajinè mwen ta chwazi?','Which imaginary pet would I choose?'),choices:[t('Yon ti dragon','A tiny dragon'),t('Yon jako ki pale anpil','A chatty parrot'),t('Yon kabrit ak chapo','A goat with a hat')]},
  {question:t('Ki kote mwen ta renmen pase yon wikenn?','Where would I spend a weekend?'),choices:[t('Nan yon kay sou mòn','In a mountain cabin'),t('Bò lanmè','At the beach'),t('Nan yon gwo mache','At a big market')]},
  {question:t('Ki sipriz fèt mwen ta pi renmen?','Which party surprise would I like most?'),choices:[t('Karaoke','Karaoke'),t('Yon lachas trezò','A treasure hunt'),t('Yon fèt ak kostim','A costume party')]},
  {question:t('Nan yon mistè, ki endis mwen ta chèche an premye?','In a mystery, which lead would I follow first?'),choices:[t('Mak pye yo','Footprints'),t('Moun ki te wè sa','Witnesses'),t('Kat kote a','A map of the place')]},
  {question:t('Ki travay mwen ta bay yon robo lakay?','Which chore would I give a home robot?'),choices:[t('Pliye chosèt','Fold socks'),t('Rakonte blag','Tell jokes'),t('Veye ti goute yo','Guard the snacks')]},
  {question:t('Si m gen yon machin pou vwayaje nan tan, ki kote m prale?','If I had a time machine, where would I go?'),choices:[t('Yon fèt nan lavni','A future carnival'),t('Yon plaj ak dinozò','A dinosaur beach'),t('Yè pou manje ankò','Yesterday for another meal')]},
  {question:t('Kisa m ta mete nan yon fò fanmi nou bati?','What would I add to a family fort?'),choices:[t('Ti limyè','Fairy lights'),t('Anpil zòrye','Lots of pillows'),t('Yon pòt sekrè','A secret door')]},
  {question:t('Nan yon defi ekip, ki wòl mwen ta pran?','Which team challenge would I choose?'),choices:[t('Bati yon pon','Build a bridge'),t('Dekode yon mesaj','Decode a message'),t('Jwe yon ti sèn','Act out a scene')]},
];
export const knowTopics = [t('Ti plezi yo','Little joys'),t('Avanti dwòl','Wild adventures'),t('Ekip fanmi an','Family team')];
export function newKnow(names,replay=0) {
  if (!Array.isArray(names) || names.length!==2 || names.some(name=>typeof name!=='string'||!name.trim()||name.length>20) ||
      names[0].trim().toLowerCase()===names[1].trim().toLowerCase() || !Number.isSafeInteger(replay) || replay<0)
    throw new Error('Invalid players');
  return {names:names.map(name=>name.trim()),replay,round:0,phase:'cover-subject',truth:null,forecast:null,
    hint:false,double:false,players:[{score:0,hint:true,double:true},{score:0,hint:true,double:true}],
    history:[],actions:[]};
}
export function subjectSeat(state) {return (state.round+state.replay)%2;}
export function cardFor(state) {return knowCards[(state.round+state.replay*5)%knowCards.length];}
export function stepKnow(state,action) {
  const next={...state,actions:[...state.actions,action]};
  if (state.phase==='cover-subject' && action==='ready') return {...next,phase:'subject'};
  if (state.phase==='subject' && Number.isInteger(action?.truth) && action.truth>=0 && action.truth<3)
    return {...next,phase:'forecast',truth:action.truth};
  if (state.phase==='forecast' && typeof action?.forecast==='boolean')
    return {...next,phase:'cover-guesser',forecast:action.forecast};
  if (state.phase==='cover-guesser' && action==='ready') return {...next,phase:'guesser'};
  if (state.phase==='guesser') {
    const guesser=1-subjectSeat(state);
    const player=state.players[guesser];
    if (action==='hint' && player.hint && !state.double)
      return {...next,hint:true,players:state.players.map((p,i)=>i===guesser?{...p,hint:false}:p)};
    if (action==='double' && player.double && player.score>=1 && !state.hint)
      return {...next,double:true,players:state.players.map((p,i)=>i===guesser?{...p,double:false}:p)};
    if (Number.isInteger(action?.guess) && action.guess>=0 && action.guess<3 &&
        (!state.hint || action.guess!==(state.truth+1)%3)) {
      const right=action.guess===state.truth;
      const forecastRight=state.forecast===right;
      const guesserPoints=right?(state.hint?1:state.double?4:2):state.double?-1:0;
      const players=state.players.map((p,i)=>i===guesser?{...p,score:Math.max(0,p.score+guesserPoints)}:
        {...p,score:p.score+(forecastRight?1:0)});
      return {...next,phase:'result',players,history:[...state.history,{round:state.round,truth:state.truth,
        guess:action.guess,right,forecastRight,hint:state.hint,double:state.double,
        guesserPoints:players[guesser].score-player.score}]};
    }
  }
  if (state.phase==='result' && action==='continue') {
    const round=state.round+1;
    return {...next,round,phase:round===knowCards.length?'end':'cover-subject',truth:null,
      forecast:null,hint:false,double:false};
  }
  throw new Error('Invalid Know Me action');
}
export function restoreKnow(save) {
  if (!save || !Array.isArray(save.actions) || save.actions.length>84) throw new Error('Invalid save');
  return save.actions.reduce(stepKnow,newKnow(save.names,save.replay));
}
