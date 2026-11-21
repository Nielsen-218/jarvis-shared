const t=(ht,en)=>({ht,en});
export const bluffActs=[t('Ti sipriz','Little surprises'),t('Abitid komik','Silly habits'),t('Istwa pou rakonte','Stories to tell')];
export const bluffCards=[
  {prompt:t('Èske w deja manje desè anvan dine?','Have you ever eaten dessert before dinner?'),yes:t('Mwen deja manje desè anvan dine.','I have eaten dessert before dinner.'),no:t('Mwen poko janm manje desè anvan dine.','I have never eaten dessert before dinner.')},
  {prompt:t('Èske w deja bay yon moun yon ti non komik?','Have you ever given someone a silly nickname?'),yes:t('Mwen deja bay yon moun yon ti non komik.','I have given someone a silly nickname.'),no:t('Mwen poko janm bay yon moun yon ti non komik.','I have never given anyone a silly nickname.')},
  {prompt:t('Èske w deja chante pandan w ap fè travay lakay?','Have you ever sung while doing chores?'),yes:t('Mwen deja chante pandan m ap fè travay lakay.','I have sung while doing chores.'),no:t('Mwen poko janm chante pandan m ap fè travay lakay.','I have never sung while doing chores.')},
  {prompt:t('Èske w deja mete de chosèt ki pa menm?','Have you ever worn mismatched socks?'),yes:t('Mwen deja mete de chosèt ki pa menm.','I have worn mismatched socks.'),no:t('Mwen poko janm mete de chosèt ki pa menm.','I have never worn mismatched socks.')},
  {prompt:t('Èske w deja pale ak yon plant?','Have you ever talked to a plant?'),yes:t('Mwen deja pale ak yon plant.','I have talked to a plant.'),no:t('Mwen poko janm pale ak yon plant.','I have never talked to a plant.')},
  {prompt:t('Èske w deja bay yon jwèt oswa yon bagay yon non?','Have you ever named a toy or an object?'),yes:t('Mwen deja bay yon jwèt oswa yon bagay yon non.','I have named a toy or an object.'),no:t('Mwen poko janm bay yon jwèt oswa yon bagay yon non.','I have never named a toy or an object.')},
  {prompt:t('Èske w deja danse san mizik?','Have you ever danced without music?'),yes:t('Mwen deja danse san mizik.','I have danced without music.'),no:t('Mwen poko janm danse san mizik.','I have never danced without music.')},
  {prompt:t('Èske w deja fè yon ti goute ak yon melanj dwòl?','Have you ever made a snack with a strange combination?'),yes:t('Mwen deja fè yon ti goute ak yon melanj dwòl.','I have made a snack with a strange combination.'),no:t('Mwen poko janm fè yon ti goute ak yon melanj dwòl.','I have never made a snack with a strange combination.')},
  {prompt:t('Èske w deja ri tèlman fò ou bliye sa w t ap rakonte?','Have you ever laughed so hard you forgot your story?'),yes:t('Mwen deja ri tèlman fò mwen bliye sa m t ap rakonte.','I have laughed so hard I forgot my story.'),no:t('Mwen poko janm ri tèlman fò mwen bliye sa m t ap rakonte.','I have never laughed so hard I forgot my story.')},
  {prompt:t('Èske w deja fè yon grimas nan yon foto?','Have you ever made a funny face in a photo?'),yes:t('Mwen deja fè yon grimas nan yon foto.','I have made a funny face in a photo.'),no:t('Mwen poko janm fè yon grimas nan yon foto.','I have never made a funny face in a photo.')},
  {prompt:t('Èske w deja fè tankou yon dra se yon kap superhero?','Have you ever pretended a blanket was a superhero cape?'),yes:t('Mwen deja fè tankou yon dra se yon kap superhero.','I have pretended a blanket was a superhero cape.'),no:t('Mwen poko janm fè tankou yon dra se yon kap superhero.','I have never pretended a blanket was a superhero cape.')},
  {prompt:t('Èske w deja envante yon règ jwèt sou plas?','Have you ever invented a game rule on the spot?'),yes:t('Mwen deja envante yon règ jwèt sou plas.','I have invented a game rule on the spot.'),no:t('Mwen poko janm envante yon règ jwèt sou plas.','I have never invented a game rule on the spot.')},
];
export function newBluff(names,replay=0) {
  if (!Array.isArray(names)||names.length!==2||names.some(name=>typeof name!=='string'||!name.trim()||name.length>20)||
      names[0].trim().toLowerCase()===names[1].trim().toLowerCase()||!Number.isSafeInteger(replay)||replay<0)
    throw new Error('Invalid players');
  return {names:names.map(name=>name.trim()),replay,round:0,phase:'cover-storyteller',truth:null,claim:null,wild:false,
    players:[{score:0,wild:true},{score:0,wild:true}],history:[],actions:[]};
}
export function storytellerSeat(state) {return (state.round+state.replay)%2;}
export function bluffCardFor(state) {return bluffCards[(state.round+state.replay*5)%bluffCards.length];}
export function stepBluff(state,action) {
  const next={...state,actions:[...state.actions,action]};
  if(state.phase==='cover-storyteller' && action==='ready') return {...next,phase:'truth'};
  if(state.phase==='truth' && typeof action?.truth==='boolean') return {...next,phase:'claim',truth:action.truth};
  if(state.phase==='claim') {
    const seat=storytellerSeat(state);
    if(action==='wild' && state.players[seat].wild && !state.wild)
      return {...next,wild:true,players:state.players.map((p,i)=>i===seat?{...p,wild:false}:p)};
    if(typeof action?.claim==='boolean' && (!state.wild || action.claim!==state.truth))
      return {...next,phase:'cover-detective',claim:action.claim};
  }
  if(state.phase==='cover-detective' && action==='ready') return {...next,phase:'detective'};
  if(state.phase==='detective' && typeof action?.believe==='boolean') {
    const storyteller=storytellerSeat(state),detective=1-storyteller;
    const right=action.believe===(state.claim===state.truth);
    const points=state.wild?(right?3:4):2;
    const winner=right?detective:storyteller;
    const players=state.players.map((p,i)=>i===winner?{...p,score:p.score+points}:p);
    return {...next,phase:'result',players,history:[...state.history,{round:state.round,truth:state.truth,
      claim:state.claim,believe:action.believe,wild:state.wild,right,winner,points}]};
  }
  if(state.phase==='result' && action==='continue') {
    const round=state.round+1;
    return {...next,round,phase:round===bluffCards.length?'end':'cover-storyteller',
      truth:null,claim:null,wild:false};
  }
  throw new Error('Invalid Bluff action');
}
export function restoreBluff(save) {
  if(!save||!Array.isArray(save.actions)||save.actions.length>84) throw new Error('Invalid save');
  return save.actions.reduce(stepBluff,newBluff(save.names,save.replay));
}
