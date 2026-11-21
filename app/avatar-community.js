/* CC0 MakeHuman community clothing/hair/skin, fitted using its published MHCLO
 * barycentric bindings and MPFB game-engine skin weights. See assets/community.
 * No MakeHuman/MPFB application code is copied or executed in the browser.
 */
import {createCharacterRig} from './avatar-rig.js';
export function createCommunityHuman(T,asset,state){
  const pack=asset.community,group=new T.Group(),sex=state.sex==='f'?'female':'male';
  const shape={height:.5,muscle:.25,weight:.15,chest:0,nose:0,jaw:0,lips:0,chin:0,cheek:0,eyes:0,...state.shape};
  for(const k in shape)shape[k]=Math.max(0,Math.min(1,Number(shape[k])||0));
  const vertices=asset.positions.map((p,i)=>p.map((v,a)=>v+asset.macros[sex][i][a]));
  for(const [key,value]of [[sex+'_muscle',shape.muscle],[sex+'_weight',shape.weight],['chest',shape.chest],['nose',shape.nose],['jaw',shape.jaw],['lips',shape.lips*.55],['chin',shape.chin*.65],['cheek',shape.cheek*.65],['eyes',shape.eyes*.40]])
    for(const [i,x,y,z]of asset.morphs[key]){vertices[i][0]+=x*value;vertices[i][1]+=y*value;vertices[i][2]+=z*value;}
  const used=[...new Set(asset.triangles)],floor=Math.min(...used.map(i=>vertices[i][1])),ceiling=Math.max(...used.map(i=>vertices[i][1]));
  const scale=(2.34+(shape.height-.5)*.28)/(ceiling-floor);
  const point=p=>new T.Vector3(p[0]*scale,(p[1]-floor)*scale+.08,p[2]*scale);
  const body=vertices.map(point);
  const rig=createCharacterRig(T,asset,body,group);
  const fitCache=new WeakMap();
  function fitted(mesh){
    if(fitCache.has(mesh))return fitCache.get(mesh);
    const factors=['x','y','z'].map((axis,i)=>{const spec=mesh.scales[axis];return spec?Math.abs(vertices[spec[0]][i]-vertices[spec[1]][i])/spec[2]:1;});
    const controls=mesh.refs.map(r=>{
      const raw=[0,0,0];
      for(let k=0;k<3;k++){
        for(let a=0;a<3;a++)raw[a]+=vertices[r[k]][a]*r[3+k];
      }
      for(let a=0;a<3;a++)raw[a]+=r[6+a]*factors[a];
      return point(raw);
    });
    const result=mesh.subdivision?mesh.subdivision.map(row=>{const p=new T.Vector3();for(const [i,w]of row)p.addScaledVector(controls[i],w);return p;}):controls;
    fitCache.set(mesh,result);return result;
  }
  function bindings(mesh){const controls=mesh.refs.map(rig.blendWeights);return mesh.subdivision?mesh.subdivision.map(recipe=>rig.combine(controls,recipe)):controls;}
  const map=key=>pack.textures?.[key]||null;
  function geometry(positions,indices,uvs,uvIndices,crop=null,bindings=rig.baseWeights){
    // Compute normals across UV seams once, then expand corners with original UVs.
    const smooth=new T.BufferGeometry();smooth.setAttribute('position',new T.Float32BufferAttribute(positions.flatMap(p=>p.toArray()),3));smooth.setIndex(indices);smooth.computeVertexNormals();
    const normal=smooth.getAttribute('normal'),p=[],n=[],uv=[],joints=[],weights=[];
    for(let i=0;i<indices.length;i+=3){
      const clips=crop?.perTriangle?(crop.perTriangle[i/3]||[]).map(spec=>({...spec,heights:crop.heights})):crop?[crop]:[];
      let polygon=indices.slice(i,i+3).map((id,k)=>({v:[...positions[id].toArray(),normal.getX(id),normal.getY(id),normal.getZ(id),...(uvs[uvIndices[i+k]]||[0,0]),clips.length?crop.heights[id]:0],skin:bindings[id]}));
      for(const clip of clips){
        const clipped=[];
        for(let k=0;k<polygon.length;k++){
          const a=polygon[k],b=polygon[(k+1)%polygon.length],da=(a.v[8]-clip.bound)*clip.sign,db=(b.v[8]-clip.bound)*clip.sign;
          if(da>=0)clipped.push(a);
          if((da>=0)!==(db>=0)){const f=da/(da-db);clipped.push({v:a.v.map((v,j)=>v+(b.v[j]-v)*f),skin:rig.mix(a.skin,b.skin,f)});}
        }
        polygon=clipped;
      }
      for(let j=1;j<polygon.length-1;j++)for(const corner of [polygon[0],polygon[j],polygon[j+1]]){const v=corner.v;p.push(...v.slice(0,3));n.push(...v.slice(3,6));uv.push(...v.slice(6,8));joints.push(...corner.skin[0]);weights.push(...corner.skin[1]);}
    }
    const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute(p,3));g.setAttribute('normal',new T.Float32BufferAttribute(n,3));g.setAttribute('uv',new T.Float32BufferAttribute(uv,2));g.setAttribute('skinIndex',new T.Uint16BufferAttribute(joints,4));g.setAttribute('skinWeight',new T.Float32BufferAttribute(weights,4));smooth.dispose();return g;
  }
  const hasJacket=!!state.jacket||state.cut?.jacket==='blazer';
  const ownedTop=state.garments?.top;
  const inferredTop=ownedTop?(/tee|t-shirt|henley/.test(String(ownedTop.name||'').toLowerCase())?'casual':'tailored'):(sex==='female'?'keyhole':'polo');
  const topStyle=state.cut?.top||inferredTop;
  const topKey=hasJacket?'blazer':topStyle==='polo'?'polo':topStyle==='keyhole'?'keyhole':topStyle==='casual'||topStyle==='tee'?'female_outfit':'male_outfit';
  const bottomKey=sex+'_outfit';
  const top=pack.meshes[topKey],bottom=pack.meshes[bottomKey],shoe=pack.meshes.shoes;
  // Outfit assets contain masks for both halves. A jeans preview must never
  // carry its original shirt's arm/neck mask into an unrelated selected top.
  const hidden=new Set(),bottomHidden=new Set(),topHidden=new Set(),heightOf=i=>(vertices[i][1]-floor)/(ceiling-floor);
  const topFloor=['polo','keyhole'].includes(topKey)?Math.max(.53,Math.min(...fitted(top).map(p=>(p.y-.08)/(2.34+(shape.height-.5)*.28)))):.53;
  for(const i of top.hide)if(heightOf(i)>=topFloor){hidden.add(i);topHidden.add(i);}
  for(const i of bottom.hide)if(heightOf(i)<=.535){hidden.add(i);bottomHidden.add(i);}
  for(const i of shoe.hide)if(heightOf(i)<=.1)hidden.add(i);
  const hairKey=({long:'braid',afro:'afro',ponytail:'ponytail',messy:'messy',bob:'bob'})[state.hair]||'short';
  if(state.hair!=='bald')pack.meshes[hairKey].hide.forEach(i=>hidden.add(i));
  const skinIndices=[],skinUVs=[],skinBoundary=[];
  for(let i=0;i<asset.triangles.length;i+=3){
    const ids=asset.triangles.slice(i,i+3),masked=ids.filter(id=>hidden.has(id));
    // A cropped top exposes the waistband. Retain the visible part of a skin
    // triangle crossing the jeans edge instead of deleting its whole triangle.
    const boundary=masked.length>0&&topFloor>.535&&masked.every(id=>bottomHidden.has(id)||topHidden.has(id))&&ids.some(id=>heightOf(id)>.535)&&ids.some(id=>heightOf(id)<topFloor);
    if(masked.length&&!boundary)continue;
    skinIndices.push(...ids);skinUVs.push(...asset.triangleUVs.slice(i,i+3));
    skinBoundary.push(boundary?[{bound:.535,sign:1},{bound:topFloor,sign:-1}]:null);
  }
  const tint=new T.Color(state.skin),reference=new T.Color('#8d5a33');
  tint.r/=reference.r;tint.g/=reference.g;tint.b/=reference.b;
  const skin=new T.MeshPhysicalMaterial({map:map('skin_'+sex),normalMap:map('skin_detail'),normalScale:new T.Vector2(.12,.12),color:tint,roughness:.68,metalness:0,specularIntensity:.35,clearcoat:0,sheen:.08,sheenColor:new T.Color('#bb8068')});
  const skinMesh=rig.mesh(geometry(body,skinIndices,asset.uvs,skinUVs,{heights:vertices.map((_,i)=>heightOf(i)),perTriangle:skinBoundary}),skin);skinMesh.name='Textured human';skinMesh.castShadow=true;group.add(skinMesh);
  function addBound(key,slot){
    const mesh=pack.meshes[key],pos=fitted(mesh),indices=mesh.triangles,uvIndices=mesh.triangleUVs;
    // Clip exact waist/hidden-boot boundaries with interpolated UVs, avoiding
    // sawtooth hems and the boot shank poking through the denim above the ankle.
    const heights=pos.map(p=>(p.y-.08)/(2.34+(shape.height-.5)*.28));
    const crop=slot==='top'&&!['polo','keyhole'].includes(key)?{heights,bound:key==='blazer'?.505:.53,sign:1}:slot==='bottom'?{heights,bound:.535,sign:-1}:slot==='shoes'?{heights,bound:.075,sign:-1}:null;
    const material=new T.MeshPhysicalMaterial({map:map(key+'_diffuse'),normalMap:map(key+'_normal'),roughnessMap:map(key+'_roughness'),displacementMap:map(key+'_height'),displacementScale:.003,displacementBias:-.0015,alphaTest:.05,normalScale:new T.Vector2(.65,.65),roughness:key==='shoes'?.62:.86,metalness:0,side:T.DoubleSide,sheen:key==='polo'?.2:0,sheenRoughness:.85});
    // Artwork colours are the default; deliberate swatch changes tint the textile.
    const defaults={top:'#4a5565',bottom:'#2a3550',shoes:'#eef0f3'};
    const colorSlot=slot==='top'&&hasJacket?'jacket':slot;
    if(colorSlot&&state.col[colorSlot]&&state.col[colorSlot]!==defaults[colorSlot])material.color.set(state.col[colorSlot]).lerp(new T.Color('#ffffff'),.55);
    if(['braid','short','afro','ponytail','messy','bob'].includes(key)){
      const finished=['messy','bob'].includes(key);
      material.alphaTest=.45;material.depthWrite=true;material.roughness=finished?.52:.9;
      material.specularIntensity=finished?.5:.25;material.sheen=finished?.2:.12;material.anisotropy=finished?.55:0;
      material.color.set(state.hairColor||'#141414').lerp(new T.Color('#ffffff'),finished?.025:.32);
    }
    const object=rig.mesh(geometry(pos,indices,mesh.uvs,uvIndices,crop,bindings(mesh)),material);object.name=slot||key;object.castShadow=true;object.receiveShadow=true;group.add(object);
  }
  addBound(topKey,'top');addBound(bottomKey,'bottom');addBound('shoes','shoes');
  if(state.hair!=='bald')addBound(hairKey);
  const eyes=pack.meshes.eyes;
  const eyeKey=({'#8a6030':'eyes_brownlight','#315a41':'eyes_green','#36576e':'eyes_deepblue','#686972':'eyes_grey'})[state.eyeColor]||'eyes';
  const eyeMaterial=new T.MeshPhysicalMaterial({map:map(eyeKey),color:'#b9b3ad',alphaTest:.05,roughness:.28,clearcoat:.3,clearcoatRoughness:.18,envMapIntensity:.45});
  const eyeMesh=rig.mesh(geometry(fitted(eyes),eyes.triangles,eyes.uvs,eyes.triangleUVs,null,eyes.refs.map(rig.blendWeights)),eyeMaterial);eyeMesh.name='Authored eyes';group.add(eyeMesh);
  if(pack.meshes.brows){
    const brow=pack.meshes.brows,material=new T.MeshStandardMaterial({map:map('brows_diffuse'),alphaTest:.03,transparent:true,depthWrite:false,roughness:.9,side:T.DoubleSide});
    group.add(rig.mesh(geometry(fitted(brow),brow.triangles,brow.uvs,brow.triangleUVs,null,brow.refs.map(rig.blendWeights)),material));
  }
  if(pack.meshes.lashes){
    const lashes=pack.meshes.lashes,material=new T.MeshStandardMaterial({map:map('lashes_diffuse'),alphaTest:.03,transparent:true,depthWrite:false,roughness:.9,side:T.DoubleSide});
    const mesh=rig.mesh(geometry(fitted(lashes),lashes.triangles,lashes.uvs,lashes.triangleUVs,null,lashes.refs.map(rig.blendWeights)),material);mesh.name='Authored eyelashes';group.add(mesh);
  }
  for(const child of [...group.children])if(child.isBone){group.remove(child);group.add(child);}
  const pose=state.pose||'relaxed';rig.setPose(pose);group.updateMatrixWorld(true);rig.skeleton.update();
  group.userData={kind:'CC0 MakeHuman community wardrobe',triangles:group.children.reduce((n,o)=>n+(o.geometry?.getAttribute('position').count||0)/3,0),pose,animate:time=>rig.setPose(pose,time),dispose:rig.dispose};
  return group;
}
