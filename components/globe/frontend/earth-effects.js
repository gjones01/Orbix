import * as THREE from './vendor/three.module.min.js';

/** Lightweight display effects in the same Earth-fixed frame as the globe. */
export function createEarthEffects(globe, sunDirection) {
  const radius = globe.getGlobeRadius();
  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(radius*1.008, 96, 64),
    new THREE.ShaderMaterial({
      transparent:true, depthWrite:false, side:THREE.DoubleSide,
      uniforms:{sunDirection:{value:sunDirection}, radius:{value:radius}},
      vertexShader:`
        varying vec3 world;
        void main(){
          world=(modelMatrix*vec4(position,1.0)).xyz;
          gl_Position=projectionMatrix*viewMatrix*vec4(world,1.0);
        }`,
      fragmentShader:`
        uniform vec3 sunDirection;
        uniform float radius;
        varying vec3 world;
        void main(){
          bool outside=length(cameraPosition)>radius*1.008;
          if(outside != gl_FrontFacing) discard;
          vec3 n=normalize(world), view=normalize(cameraPosition-world);
          float grazing=pow(1.0-abs(dot(n,view)),4.0);
          float daylight=smoothstep(-0.12,0.35,dot(n,sunDirection));
          float twilight=exp(-pow(dot(n,sunDirection)/0.13,2.0));
          vec3 color=mix(vec3(0.14,0.43,1.0),vec3(1.0,0.46,0.19),twilight*0.42);
          float alpha=grazing*(0.015+0.50*daylight);
          gl_FragColor=vec4(color,alpha);
        }`,
    })
  );
  atmosphere.material.forceSinglePass=true;
  atmosphere.name='orbix-atmosphere';
  atmosphere.raycast=()=>{};
  globe.scene().add(atmosphere);
  const surface=globe.globeMaterial();
  const black=new THREE.DataTexture(new Uint8Array([0,0,0,255]),1,1);
  black.needsUpdate=true;
  const uniforms={
    oxSun:{value:sunDirection}, oxEnhanced:{value:1},
    oxNight:{value:black}, oxWater:{value:black}, oxWaterReady:{value:0},
  };
  const assets={night:'loading',water:'loading'};
  surface.onBeforeCompile=shader=>{
    Object.assign(shader.uniforms,uniforms);
    shader.vertexShader='varying vec3 oxWorld;\nvarying vec2 oxUv;\n'+shader.vertexShader;
    shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',
      '#include <begin_vertex>\noxWorld=(modelMatrix*vec4(position,1.0)).xyz; oxUv=uv;');
    shader.fragmentShader=`
      varying vec3 oxWorld;
      varying vec2 oxUv;
      uniform vec3 oxSun;
      uniform float oxEnhanced, oxWaterReady;
      uniform sampler2D oxNight, oxWater;
    `+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <emissivemap_fragment>',`
      #include <emissivemap_fragment>
      if(oxEnhanced>0.5){
        float night=1.0-smoothstep(-0.18,0.04,dot(normalize(oxWorld),oxSun));
        vec3 lights=texture2D(oxNight,oxUv).rgb;
        // Suppress the composite's dark terrain background, retaining observed lights.
        lights*=smoothstep(0.06,0.18,max(lights.r,max(lights.g,lights.b)));
        lights=max(lights-vec3(0.015),vec3(0.0));
        totalEmissiveRadiance+=lights*night*0.65;
      }
    `);
    shader.fragmentShader=shader.fragmentShader.replace('#include <specularmap_fragment>',`
      #include <specularmap_fragment>
      if(oxEnhanced>0.5 && oxWaterReady>0.5){
        float water=texture2D(oxWater,oxUv).r;
        specularStrength*=mix(0.035,1.0,water);
      }
    `);
  };
  surface.customProgramCacheKey=()=> 'orbix-earth-v1';
  surface.needsUpdate=true;
  let cinematic=true;
  function setQuality(value) {
    cinematic=value!=='basic'; atmosphere.visible=cinematic;
    uniforms.oxEnhanced.value=cinematic?1:0;
    globe.lights().find(l=>l.name==='orbix-night-fill').intensity=cinematic?.12:.3;
    surface.shininess=cinematic?56:9;
    surface.specular.setScalar(cinematic?(assets.water==='ready'?.025:.012):.045);
    surface.bumpScale=cinematic?.08:.28;
  }
  const select=document.getElementById('lighting-quality');
  select.addEventListener('change',()=>setQuality(select.value));
  const loader=new THREE.TextureLoader();
  function load(name,path,uniform,colorSpace){
    loader.load(path,texture=>{
      texture.colorSpace=colorSpace;
      texture.anisotropy=1; // Mipmaps keep the optional maps inexpensive in close views.
      uniform.value=texture; assets[name]='ready';
      if(name==='water') uniforms.oxWaterReady.value=1;
      setQuality(cinematic?'cinematic':'basic');
    },undefined,()=>{
      assets[name]='unavailable';
      select.title='Optional surface imagery unavailable; basic sunlight remains available.';
    });
  }
  load('night','./assets/earth-night-2016.jpg',uniforms.oxNight,THREE.SRGBColorSpace);
  load('water','./assets/earth-water-mask.png',uniforms.oxWater,THREE.NoColorSpace);
  return {setQuality, get cinematic(){return cinematic;}, atmosphere, assets};
}
