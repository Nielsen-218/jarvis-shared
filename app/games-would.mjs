const t=(ht,en)=>({ht,en});
export const wouldActs=[t('Chwa komik','Silly choices'),t('Avanti ansanm','Adventures together'),t('Gwo final','The grand finale')];
export const wouldCards=[
  {question:t('Nan yon mistè, kiyès ou ta pito gen nan ekip ou?','In a mystery, whom would you rather have on your team?'),choices:[t('Yon kabrit ki pale','A talking goat'),t('Yon jako ki renmen teyat','A dramatic parrot')]},
  {question:t('Telepòtasyon an fè yon ti erè. Ki kote w prale?','The teleport goes slightly wrong. Where would you land?'),choices:[t('Sou yon plaj anba lapli','On a rainy beach'),t('Nan yon chato nèj ak pijama','In a snowy castle wearing pajamas')]},
  {question:t('Ki zouti majik ou ta chwazi pou kwizin nan?','Which magic kitchen tool would you choose?'),choices:[t('Asyèt ki lave tèt yo','Dishes that wash themselves'),t('Yon bwat ti goute ki plen ankò','A snack box that refills itself')]},
  {question:t('Nan fèt la, ki wòl ou ta pito pran?','At the party, which role would you prefer?'),choices:[t('Mennen yon dans','Lead a dance'),t('Envante yon chante komik','Invent a silly song')]},
  {question:t('Kat la pèdi. Ki gid ou ta fè konfyans?','The map is gone. Which guide would you trust?'),choices:[t('Yon konpa ki etènye','A sneezing compass'),t('Yon lantèn k ap chante','A humming lantern')]},
  {question:t('Ki kote w ta pase yon wikenn etranj?','Where would you spend a strange weekend?'),choices:[t('Yon kay nan pyebwa ak glisad','A treehouse with slides'),t('Yon bato ak planche an vè','A boat with a glass floor')]},
  {question:t('Pou yon sèl jou, ki kapasite ou ta pran?','For one day, which ability would you take?'),choices:[t('Pale tout lang','Speak every language'),t('Konprann tout bèt lakay','Understand every pet')]},
  {question:t('Ki defi fanmi ou ta chwazi?','Which family challenge would you choose?'),choices:[t('Bati yon fò ak dra nan 10 minit','Build a blanket fort in 10 minutes'),t('Swiv yon seri devinèt','Follow a riddle trail')]},
  {question:t('Ki soulye komik ou ta mete?','Which silly shoes would you wear?'),choices:[t('Bòt ki fè w sote wo','Bouncing boots'),t('Soulye ki jwe mizik','Shoes that play music')]},
  {question:t('Yon pòt sekrè louvri. Ki kote w antre?','A secret door opens. Where do you enter?'),choices:[t('Yon bibliyotèk plen istwa','A library full of stories'),t('Yon jaden plant ki klere','A garden of glowing plants')]},
  {question:t('Ki misyon ekip la ta pran an premye?','Which team mission comes first?'),choices:[t('Kouri dèyè gato k ap woule a','Chase a runaway cake'),t('Sove balon ki chape yo','Rescue escaped balloons')]},
  {question:t('Pou fini fèt la, ki spektak ou ta chwazi?','For the party finale, which show would you choose?'),choices:[t('Fedatifis ki fèt ak ti boul savon','Fireworks made of bubbles'),t('Yon parad robo k ap danse','A parade of dancing robots')]},
];
export function newWould(names,replay=0) {
  if(!Array.isArray(names)||names.length!==2||names.some(name=>typeof name!=='string'||!name.trim()||name.length>20)||
     names[0].trim().toLowerCase()===names[1].trim().toLowerCase()||!Number.isSafeInteger(replay)||replay<0)
    throw new Error('Invalid players');
  return {names:names.map(name=>name.trim()),replay,round:0,seat:replay%2,turns:0,phase:'cover',
    choices:[null,null],predictions:[null,null],risks:[false,false],
    players:[{score:0,double:true},{score:0,double:true}],history:[],actions:[]};
}
export function wouldCardFor(state){return wouldCards[(state.round+state.replay*5)%wouldCards.length];}
function finishTurn(state){
  if(!state.turns) return {...state,seat:1-state.seat,turns:1,phase:'cover'};
  const players=state.players.map((player,i)=>{
    const right=state.predictions[i]===state.choices[1-i];
    const change=right?(state.risks[i]?4:2):state.risks[i]?-1:0;
    return {...player,score:player.score+change};
  });
  return {...state,phase:'result',players,history:[...state.history,{round:state.round,
    choices:state.choices,predictions:state.predictions,risks:state.risks,
    changes:players.map((player,i)=>player.score-state.players[i].score)}]};
}
export function stepWould(state,action){
  const next={...state,actions:[...state.actions,action]};
  if(state.phase==='cover'&&action==='ready')return {...next,phase:'choice'};
  if(state.phase==='choice'&&Number.isInteger(action?.choice)&&action.choice>=0&&action.choice<2){
    const choices=[...state.choices];choices[state.seat]=action.choice;
    return {...next,choices,phase:'prediction'};
  }
  if(state.phase==='prediction'&&Number.isInteger(action?.predict)&&action.predict>=0&&action.predict<2){
    const predictions=[...state.predictions];predictions[state.seat]=action.predict;
    const chosen={...next,predictions};
    return state.players[state.seat].double&&state.players[state.seat].score>=1?
      {...chosen,phase:'risk'}:finishTurn(chosen);
  }
  if(state.phase==='risk'&&typeof action?.double==='boolean'){
    const risks=[...state.risks];risks[state.seat]=action.double;
    const players=state.players.map((p,i)=>i===state.seat&&action.double?{...p,double:false}:p);
    return finishTurn({...next,risks,players});
  }
  if(state.phase==='result'&&action==='continue'){
    const round=state.round+1;
    return {...next,round,seat:(round+state.replay)%2,turns:0,
      phase:round===wouldCards.length?'end':'cover',choices:[null,null],predictions:[null,null],risks:[false,false]};
  }
  throw new Error('Invalid Would You Rather action');
}
export function restoreWould(save){
  if(!save||!Array.isArray(save.actions)||save.actions.length>110)throw new Error('Invalid save');
  return save.actions.reduce(stepWould,newWould(save.names,save.replay));
}
