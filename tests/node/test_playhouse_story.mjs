import assert from 'node:assert/strict';
import {scenes,choose} from '../../app/games-story.mjs';
const endings=new Set();
for(const a of [0,1])for(const b of [0,1]){
 const middle=choose('kitchen',a),end=choose(middle,b);
 assert.equal(scenes[middle].chapter,2);
 assert.equal(scenes[end].chapter,3);
 assert.equal(scenes[end].choices.length,0);
 endings.add(end);
}
assert.equal(endings.size,3);
assert.notEqual(choose('kitchen',0),choose('kitchen',1));
for(const s of Object.values(scenes))for(const l of ['ht','en']){
 assert(s.title[l]&&s.body[l]);s.choices.forEach(c=>assert(c.label[l]&&scenes[c.next]));
}
for(const bad of [-1,2,NaN,'0',null])assert.throws(()=>choose('kitchen',bad));
assert.throws(()=>choose('missing',0));assert.throws(()=>choose('surprise',0));
console.log('Four story paths, three endings, bilingual content and invalid transitions passed.');
