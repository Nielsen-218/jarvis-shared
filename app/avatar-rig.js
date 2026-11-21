/* Independent browser binding of CC0 MPFB game-engine skeleton/weights.
 * Joint locations follow the morphed body. No Blender/MPFB program code.
 */
export function createCharacterRig(T,asset,body,group){
  const data=asset.community.rig,names=data.bones.map(b=>b.name),bones=data.bones.map(b=>{const bone=new T.Bone();bone.name=b.name;return bone;}),byName=Object.fromEntries(bones.map(b=>[b.name,b]));
  function location(spec){
    const indices=spec.vertex_indices||asset.landmarks[spec.cube_name];
    if(!indices?.length)throw new Error('Missing rig landmark: '+spec.cube_name);
    const p=new T.Vector3();for(const i of indices)p.add(body[i]);return p.divideScalar(indices.length);
  }
  const heads=data.bones.map(b=>location(b.head));
  data.bones.forEach((b,i)=>{const parentIndex=names.indexOf(b.parent);bones[i].position.copy(heads[i]);if(parentIndex>=0){bones[i].position.sub(heads[parentIndex]);bones[parentIndex].add(bones[i]);}else group.add(bones[i]);});
  group.updateMatrixWorld(true);
  const skeleton=new T.Skeleton(bones),baseWeights=data.weights;
  function blendWeights(ref){
    const values=new Map();
    for(let k=0;k<3;k++){const [ids,weights]=baseWeights[ref[k]];for(let j=0;j<4;j++)values.set(ids[j],(values.get(ids[j])||0)+weights[j]*ref[3+k]);}
    return normalize(values);
  }
  function normalize(values){
    const row=[...values].filter(([,w])=>w>0).sort((a,b)=>b[1]-a[1]).slice(0,4),sum=row.reduce((n,[,w])=>n+w,0)||1;
    return [row.map(([i])=>i).concat([0,0,0,0]).slice(0,4),row.map(([,w])=>w/sum).concat([0,0,0,0]).slice(0,4)];
  }
  function mix(a,b,f){const values=new Map();for(const [row,scale]of [[a,1-f],[b,f]])for(let j=0;j<4;j++)values.set(row[0][j],(values.get(row[0][j])||0)+row[1][j]*scale);return normalize(values);}
  function combine(rows,recipe){const values=new Map();for(const [i,scale]of recipe)for(let j=0;j<4;j++)values.set(rows[i][0][j],(values.get(rows[i][0][j])||0)+rows[i][1][j]*scale);return normalize(values);}
  function mesh(geometry,material){const m=new T.SkinnedMesh(geometry,material);m.bind(skeleton,new T.Matrix4());m.frustumCulled=false;return m;}
  function setPose(pose,time=0){
    for(const bone of bones)bone.quaternion.identity();
    if(pose==='fitting')return;
    const portrait=pose==='portrait',wave=Math.sin(time*.0011),breath=Math.sin(time*.0017);
    byName.upperarm_l.rotation.z=portrait?-.68:-.64;byName.upperarm_r.rotation.z=portrait?.57:.62;
    byName.lowerarm_l.rotation.x=-.14;byName.lowerarm_r.rotation.x=-.10;
    byName.hand_l.rotation.x=-.06;byName.hand_r.rotation.x=-.05;
    // Fingers curl around a local axis perpendicular to each authored finger.
    for(const info of data.bones){
      if(!/^(index|middle|ring|pinky)_0[123]_/.test(info.name))continue;
      const i=names.indexOf(info.name),axis=location(info.tail).sub(heads[i]).normalize().cross(new T.Vector3(0,0,1)).normalize();
      byName[info.name].quaternion.setFromAxisAngle(axis,.12);
    }
    if(byName.spine_03)byName.spine_03.rotation.z=(portrait?.018:0)+breath*.002;
    if(byName.head){byName.head.rotation.y=(portrait?.10:0)+wave*.008;byName.head.rotation.z=portrait?-.015:0;}
    if(byName.neck_01)byName.neck_01.rotation.x=breath*.002;
  }
  return {skeleton,bones,baseWeights,blendWeights,mix,combine,mesh,setPose,dispose:()=>skeleton.dispose()};
}
