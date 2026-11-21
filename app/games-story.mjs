// Shared authored content is also read by the server room engine.
import pack from './games-story.json' with {type: 'json'};
export const text = (ht, en) => ({ht, en});
export const scenes = pack;
export function choose(id, index) {
  const scene = scenes[id];
  if (!scene || !Number.isInteger(index) || !scene.choices[index]) throw new Error('Invalid story choice');
  return scene.choices[index].next;
}
