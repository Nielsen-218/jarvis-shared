/* Detailed CC0 MakeHuman surface + authored fitted garment shells.
 * Asset provenance and license: assets/avatar/. No MakeHuman application code.
 * Visual proportions only: not body-scan reconstruction or a physical fit simulator.
 */
import {createCommunityHuman} from './avatar-community.js';
export async function loadHuman(T){
  const r=await fetch('/assets/avatar/human.json');
  if(!r.ok)throw new Error('Human model could not load. Reload to retry.');
  const asset=await r.json();
  const base='/assets/community/';
  const get=async file=>{const res=await fetch(base+file);if(!res.ok)throw new Error('Community asset unavailable: '+file);return res.json();};
  const [manifest,armWeights,rig]=await Promise.all([get('manifest.json'),get('arm-weights.json'),get('rig.json')]);
  const meshes=Object.fromEntries(await Promise.all(Object.entries(manifest.assets).map(async([key,info])=>[key,await get(info.mesh)])));
  const textures={};
  if(T){
    const loader=new T.TextureLoader(),jobs=[];
    const load=(key,file,color)=>jobs.push(loader.loadAsync(base+file).then(texture=>{texture.colorSpace=color?T.SRGBColorSpace:T.NoColorSpace;texture.anisotropy=4;textures[key]=texture;}));
    for(const [key,info]of Object.entries(manifest.assets))for(const type of ['diffuse','normal','roughness','height'])if(info[type])load(key+'_'+type,info[type],type==='diffuse');
    const skinSuffix=matchMedia('(pointer:coarse)').matches?'_mobile':'';
    load('skin_male','skin_male'+skinSuffix+'.webp',true);load('skin_female','skin_female'+skinSuffix+'.webp',true);load('eyes','eyes.webp',true);
    for(const color of ['brownlight','green','deepblue','grey'])load('eyes_'+color,'eyes_'+color+'.webp',true);
    if(manifest.surface_detail)load('skin_detail',manifest.surface_detail.normal,false);
    await Promise.all(jobs);
    if(textures.skin_detail){textures.skin_detail.wrapS=textures.skin_detail.wrapT=T.RepeatWrapping;textures.skin_detail.repeat.set(32,32);}
  }
  asset.community={manifest,meshes,armWeights,rig,textures};return asset;
}

export function createHuman(T,asset,state){
  if(asset.community&&state.surface!=='templates')return createCommunityHuman(T,asset,state);
  const group=new T.Group(), sex=state.sex==='f'?'female':'male';
  const shape={muscle:.25,weight:.15,chest:0,nose:0,jaw:0,height:.5,lips:0,chin:0,cheek:0,eyes:0,...state.shape};
  const clamp=v=>Math.max(0,Math.min(1,Number(v)||0));
  for(const key of Object.keys(shape))shape[key]=clamp(shape[key]);
  const vertices=asset.positions.map((p,i)=>p.map((v,a)=>v+asset.macros[sex][i][a]));
  for(const [name,amount] of [[sex+'_muscle',shape.muscle],[sex+'_weight',shape.weight],['chest',shape.chest],['nose',shape.nose],['jaw',shape.jaw]]){
    for(const [i,x,y,z] of asset.morphs[name]){
      vertices[i][0]+=x*amount;vertices[i][1]+=y*amount;vertices[i][2]+=z*amount;
    }
  }
  for(const [name,amount]of [['lips',shape.lips*.55],['chin',shape.chin*.65],['cheek',shape.cheek*.65],['eyes',shape.eyes*.40]])for(const [i,x,y,z]of asset.morphs[name]){vertices[i][0]+=x*amount;vertices[i][1]+=y*amount;vertices[i][2]+=z*amount;}
  const used=[...new Set(asset.triangles)];
  const minY=Math.min(...used.map(i=>vertices[i][1]));
  const maxY=Math.max(...used.map(i=>vertices[i][1]));
  const height=2.34+(shape.height-.5)*.28, scale=height/(maxY-minY);
  const positions=new Float32Array(vertices.length*3);
  vertices.forEach((p,i)=>{positions[i*3]=p[0]*scale;positions[i*3+1]=(p[1]-minY)*scale+.08;positions[i*3+2]=p[2]*scale;});
  const geometry=new T.BufferGeometry();
  geometry.setAttribute('position',new T.BufferAttribute(positions,3));
  geometry.setIndex(asset.triangles);geometry.computeVertexNormals();
  const normals=geometry.getAttribute('normal').array;
  const nameFor=slot=>String(state.garments?.[slot]?.name||'').toLowerCase();
  const topCut=state.cut?.top||(/hoodie/.test(nameFor('top'))?'hoodie':/polo/.test(nameFor('top'))?'polo':/long|sweater/.test(nameFor('top'))?'long':'tee');
  const longSleeve=['long','hoodie','polo'].includes(topCut);
  const shorts=state.cut?.bottom==='shorts'||(!state.cut?.bottom&&/short/.test(nameFor('bottom')));
  const skirt=state.cut?.bottom==='skirt'||(!state.cut?.bottom&&/skirt|dress/.test(nameFor('bottom')));
  const hasJacket=!!state.jacket;
  const bareLegLimit=shorts?.30:skirt?.43:.055;
  const skinGeometry=clippedGeometry(positions,[
    [[1,.08+.535*height,1]], [[1,.08+bareLegLimit*height,-1]],
    [[0,.36,1],[1,.08+.535*height,-1],[1,.08+bareLegLimit*height,1]],
    [[0,-.36,-1],[1,.08+.535*height,-1],[1,.08+bareLegLimit*height,1]],
  ]);
  geometry.dispose();
  const skinMat=new T.MeshPhysicalMaterial({color:state.skin,roughness:.6,metalness:0,clearcoat:.06});
  const body=new T.Mesh(skinGeometry,skinMat);body.castShadow=true;group.add(body);
  const fabric=new T.DataTexture(new Uint8Array([235,235,235,255,255,255,255,255,247,247,247,255,255,255,255,255]),2,2);
  fabric.wrapS=fabric.wrapT=T.RepeatWrapping;fabric.repeat.set(60,60);fabric.needsUpdate=true;
  function clippedGeometry(source,regions){
    const out=[],index=[],lookup=new Map();
    for(const region of regions){
      for(let t=0;t<asset.triangles.length;t+=3){
        let polygon=asset.triangles.slice(t,t+3).map(i=>[source[i*3],source[i*3+1],source[i*3+2]]);
        for(const [axis,bound,sign] of region){
          if(!polygon.length)break;
          const next=[];
          for(let k=0;k<polygon.length;k++){
            const a=polygon[k],b=polygon[(k+1)%polygon.length],da=(a[axis]-bound)*sign,db=(b[axis]-bound)*sign;
            if(da>=0)next.push(a);
            if((da>=0)!==(db>=0)){const f=da/(da-db);next.push(a.map((v,i)=>v+(b[i]-v)*f));}
          }
          polygon=next;
        }
        const ids=polygon.map(p=>{const key=p.map(v=>v.toFixed(5)).join(',');if(!lookup.has(key)){lookup.set(key,out.length/3);out.push(...p);}return lookup.get(key);});
        for(let i=1;i<ids.length-1;i++)index.push(ids[0],ids[i],ids[i+1]);
      }
    }
    const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute(out,3));g.setIndex(index);g.computeVertexNormals();
    const uv=[];for(let i=0;i<out.length;i+=3)uv.push(out[i],out[i+1]);g.setAttribute('uv',new T.Float32BufferAttribute(uv,2));return g;
  }
  function shell(slot,offset){
    const p=positions.slice();
    for(let i=0;i<p.length;i+=3){
      p[i]+=normals[i]*offset;p[i+1]+=normals[i+1]*offset;p[i+2]+=normals[i+2]*offset;
      // Smooth close-fitting cloth over the torso; do not model fabric as painted skin.
      if(slot==='top'||slot==='jacket'){
        const y=(p[i+1]-.08)/height;
        if(y>.54&&y<.81&&Math.abs(p[i])<.24&&p[i+2]>0)p[i+2]=Math.max(p[i+2],.09+offset);
      }
      if(slot==='bottom'){
        const y=(p[i+1]-.08)/height;
        // Tailored pants do not trace the underlying groin anatomy.
        if(y>.43&&y<.54&&p[i+2]>0)p[i+2]=Math.min(p[i+2],.13+offset);
        // Loosen lower legs into straight trousers rather than muscle-shaped leggings.
        if(y>.07&&y<.36){const center=Math.sign(p[i])*.145;p[i]=center+(p[i]-center)*1.12;}
      }
    }
    const bounds=(xmin,xmax,ymin,ymax)=>[[0,xmin,1],[0,xmax,-1],[1,.08+ymin*height,1],[1,.08+ymax*height,-1]];
    let regions;
    if(slot==='top')regions=[bounds(-.28,.28,.53,.855),bounds(.28,.66,longSleeve?.51:.705,.855),bounds(-.66,-.28,longSleeve?.51:.705,.855)];
    if(slot==='bottom')regions=[bounds(-.36,.36,bareLegLimit,.535)];
    if(slot==='shoes')regions=[bounds(-.4,.4,0,.07)];
    if(slot==='jacket')regions=[bounds(-.67,-.028,.49,.85),bounds(.028,.67,.49,.85),[...bounds(-.028,.028,.49,.85),[2,.02,-1]]];
    const g=clippedGeometry(p,regions);
    const m=new T.MeshStandardMaterial({color:state.col[slot],roughness:slot==='shoes'?.48:.92,side:T.DoubleSide,bumpMap:fabric,bumpScale:.0006});
    const mesh=new T.Mesh(g,m);mesh.castShadow=true;group.add(mesh);
  }
  shell('top',.018);shell('bottom',.025);shell('shoes',.018);
  if(topCut==='polo'||topCut==='hoodie'){
    const neck=centerEarly('joint-neck');
    const collar=new T.Mesh(new T.TorusGeometry(topCut==='hoodie'?.092:.061,topCut==='hoodie'?.027:.009,10,36),new T.MeshStandardMaterial({color:state.col.top,roughness:.9}));
    collar.rotation.x=Math.PI/2;collar.scale.y=1.15;collar.position.copy(neck).add(new T.Vector3(0,-.014,.008));group.add(collar);
  }
  function centerEarly(key){const ids=asset.landmarks[key],c=new T.Vector3();ids.forEach(i=>c.add(new T.Vector3(positions[i*3],positions[i*3+1],positions[i*3+2])));return c.divideScalar(ids.length);}
  for(const side of [-1,1]){
    const foot=used.filter(i=>(positions[i*3+1]-.08)/height<.045&&positions[i*3]*side>0);
    const xs=foot.map(i=>positions[i*3]),zs=foot.map(i=>positions[i*3+2]);
    const minX=Math.min(...xs),maxX=Math.max(...xs),minZ=Math.min(...zs),maxZ=Math.max(...zs);
    const outline=new T.Shape();outline.absellipse(0,0,(maxX-minX)/2+.014,(maxZ-minZ)/2+.025,0,Math.PI*2,false,0);
    const sole=new T.Mesh(new T.ExtrudeGeometry(outline,{depth:.035,bevelEnabled:true,bevelSize:.008,bevelThickness:.006,bevelSegments:2,steps:1}),new T.MeshStandardMaterial({color:'#dedbd5',roughness:.85}));
    sole.rotation.x=-Math.PI/2;sole.position.set((minX+maxX)/2,.07,(minZ+maxZ)/2);group.add(sole);
    const upper=new T.Mesh(new T.SphereGeometry(1,28,16,0,Math.PI*2,0,Math.PI/2),new T.MeshStandardMaterial({color:state.col.shoes,roughness:.65}));
    upper.scale.set((maxX-minX)/2+.012,.075,(maxZ-minZ)/2+.02);upper.position.set((minX+maxX)/2,.105,(minZ+maxZ)/2);group.add(upper);
  }
  if(hasJacket)shell('jacket',.045);
  // A tailored skirt is an independent flared surface, not recolored trouser legs.
  if(skirt){
    const hip=used.filter(i=>{const y=(positions[i*3+1]-.08)/height;return y>.43&&y<.55;});
    const radius=Math.max(.25,...hip.map(i=>Math.abs(positions[i*3])))+.035;
    const points=[new T.Vector2(radius,.55*height),new T.Vector2(radius+.025,.49*height),new T.Vector2(radius+.06,.36*height),new T.Vector2(radius+.09,.28*height)];
    const g=new T.LatheGeometry(points,48);g.scale(1,1,.72);g.translate(0,.08,.01);
    group.add(new T.Mesh(g,new T.MeshStandardMaterial({color:state.col.bottom,side:T.DoubleSide,roughness:.9})));
  }
  const center=key=>{
    const ids=asset.landmarks[key];const p=new T.Vector3();
    ids.forEach(i=>p.add(new T.Vector3(positions[i*3],positions[i*3+1],positions[i*3+2])));return p.divideScalar(ids.length);
  };
  for(const key of ['joint-l-eye','joint-r-eye']){
    const c=center(key),eye=new T.Mesh(new T.SphereGeometry(.017,20,14),new T.MeshStandardMaterial({color:'#e4ded3',roughness:.25}));
    eye.position.copy(c);group.add(eye);
    const iris=new T.Mesh(new T.SphereGeometry(.008,16,12),new T.MeshStandardMaterial({color:state.eyeColor||'#33241b',roughness:.3}));
    iris.scale.z=.3;iris.position.copy(c).add(new T.Vector3(0,0,.016));group.add(iris);
    const pupil=new T.Mesh(new T.SphereGeometry(.0036,12,8),new T.MeshBasicMaterial({color:'#080808'}));
    pupil.scale.z=.25;pupil.position.copy(c).add(new T.Vector3(0,0,.018));group.add(pupil);
  }
  if(state.hair!=='bald'){
    const indices=[];
    for(let i=0;i<asset.triangles.length;i+=3){
      const ids=asset.triangles.slice(i,i+3);
      const y=ids.reduce((n,j)=>n+(positions[j*3+1]-.08)/height,0)/3;
      const z=ids.reduce((n,j)=>n+positions[j*3+2],0)/3;
      if(y>.976||(y>.935&&z<.12))indices.push(...ids);
    }
    const hp=positions.slice();
    for(let i=0;i<hp.length;i++)hp[i]+=normals[i]*.009;
    const hg=new T.BufferGeometry();hg.setAttribute('position',new T.BufferAttribute(hp,3));hg.setIndex(indices);hg.computeVertexNormals();
    group.add(new T.Mesh(hg,new T.MeshStandardMaterial({color:state.hairColor,roughness:.95,side:T.DoubleSide})));
    if(state.hair==='long'){
      const c=center('joint-head'),g=new T.SphereGeometry(.13,32,24,0,Math.PI*2,0,Math.PI*.8);
      g.scale(1,.95,1);const h=new T.Mesh(g,new T.MeshStandardMaterial({color:state.hairColor,roughness:.9}));h.position.copy(c).add(new T.Vector3(0,.07,-.025));group.add(h);
    }
  }
  group.userData={vertexCount:used.length,triangleCount:asset.triangles.length/3,kind:'CC0 MakeHuman adult mesh'};
  return group;
}
