import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'

// ── Simplex noise (Ashima) ─────────────────────────────────────────────────────
const NOISE = /* glsl */`
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 permute(vec4 x){return mod289(((x*34.)+1.)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);
  vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;
  vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(
    i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
  float n_=0.142857142857;vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
  vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);
  m=m*m;
  return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`

// ── Shared vertex ──────────────────────────────────────────────────────────────
const VERT = /* glsl */`
varying vec2 vUv;
void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`

// ── Terrain nucleus ────────────────────────────────────────────────────────────
const NUCLEUS_FRAG = /* glsl */`
${NOISE}
uniform float uTime;
uniform float uPulse;
uniform vec2  uMouse;
varying vec2  vUv;

void main(){
  vec2 uv=(vUv-.5)*2.;
  float d=length(uv);
  if(d>1.)discard;

  // Parallax offset from mouse
  vec2 s=uv+uMouse*.06;

  // Multi-octave terrain noise
  float n =snoise(vec3(s*2.4,          uTime*.025));
  float n2=snoise(vec3(s*5.1+1.7,      uTime*.04));
  float n3=snoise(vec3(s*10.3+3.1,     uTime*.015));
  float n4=snoise(vec3(s*20.+6.,       uTime*.01));
  float terrain=n*.45+n2*.28+n3*.17+n4*.10;

  // Desert / Mars color ramp
  vec3 c0=vec3(.28,.15,.05);
  vec3 c1=vec3(.46,.27,.10);
  vec3 c2=vec3(.62,.42,.18);
  vec3 c3=vec3(.72,.54,.26);
  vec3 c4=vec3(.35,.22,.08);
  vec3 col=mix(c0,c1,smoothstep(-.9,-.3,terrain));
  col     =mix(col,c2,smoothstep(-.3, .2,terrain));
  col     =mix(col,c3,smoothstep( .2, .6,terrain));
  col     =mix(col,c4,smoothstep( .6, .9,terrain));

  // Atmospheric teal glow at center (AI core)
  float glow=exp(-d*2.8)*(.3+.12*sin(uTime*1.8+d*4.));
  col+=vec3(.04,.45,.65)*glow;

  // Fresnel rim glow
  float rim=pow(d,3.5)*uPulse;
  col+=vec3(.08,.6,.85)*rim*.55;

  // Edge vignette
  float edge=smoothstep(1.,.8,d);

  gl_FragColor=vec4(col,edge);
}`

// ── HUD dashed ring ────────────────────────────────────────────────────────────
const RING_FRAG = /* glsl */`
uniform float uTime;
uniform float uDashes;
uniform float uSpeed;
uniform float uThick;
uniform float uR;
uniform vec3  uColor;
uniform float uOpacity;
uniform float uPulse;
varying vec2  vUv;

void main(){
  vec2 uv=vUv-.5;
  float d=length(uv);
  float r2=smoothstep(uR+uThick,uR+uThick*.4,d)*smoothstep(uR-uThick,uR-uThick*.4,d);
  if(r2<.005)discard;

  float angle=atan(uv.y,uv.x);
  float norm=fract(angle/6.28318+.5);
  float dash=step(.35,fract(norm*uDashes+uTime*uSpeed));

  float pulse=.65+.35*sin(uTime*2.2+norm*12.566);
  float alpha=r2*dash*uOpacity*pulse*uPulse;
  if(alpha<.005)discard;
  gl_FragColor=vec4(uColor,alpha);
}`

// ── Tick marks ─────────────────────────────────────────────────────────────────
const TICK_FRAG = /* glsl */`
uniform float uTicks;
uniform float uR;
uniform float uTickLen;
uniform vec3  uColor;
varying vec2  vUv;

void main(){
  vec2 uv=vUv-.5;
  float d=length(uv);
  if(d<uR-uTickLen||d>uR+uTickLen)discard;

  float angle=atan(uv.y,uv.x);
  float norm=fract(angle/6.28318+.5);
  float sector=fract(norm*uTicks);

  // Thin line at each tick boundary
  float t=1.-smoothstep(0.,.018,sector);
  // Major tick every 4 (wider)
  float isMajor=step(.5,1.-step(.1,fract(norm*uTicks*.25)));
  float alpha=(t*.5+t*isMajor*.4);
  if(alpha<.01)discard;
  gl_FragColor=vec4(uColor,alpha);
}`

// ── Radar sweep ────────────────────────────────────────────────────────────────
const SWEEP_FRAG = /* glsl */`
uniform float uTime;
uniform float uSpeed;
uniform float uRadius;
varying vec2  vUv;

void main(){
  vec2 uv=vUv-.5;
  float d=length(uv);
  if(d>uRadius)discard;

  float angle=atan(uv.y,uv.x);
  float a=fract((angle+uTime*uSpeed)/6.28318);
  float trail=pow(1.-a,5.)*step(.005,a);
  float fade=smoothstep(uRadius,uRadius*.2,d);
  float alpha=trail*fade*.7;
  if(alpha<.005)discard;
  gl_FragColor=vec4(.05,.9,.95,alpha);
}`

// ── Outer arc (solid torus) ────────────────────────────────────────────────────
const ARC_FRAG = /* glsl */`
uniform float uTime;
uniform vec3  uColor;
varying vec2  vUv;

void main(){
  float pulse=.6+.4*sin(uTime*1.4+vUv.x*12.566);
  gl_FragColor=vec4(uColor,pulse);
}`

// ── Star particle ──────────────────────────────────────────────────────────────
const STAR_VERT = /* glsl */`
uniform float uTime;
attribute float aSize;
attribute float aPhase;
varying float vAlpha;

void main(){
  vAlpha=.35+.25*sin(uTime*.6+aPhase);
  vec4 mv=modelViewMatrix*vec4(position,1.);
  gl_PointSize=aSize*(280./-mv.z);
  gl_Position=projectionMatrix*mv;
}`

const STAR_FRAG = /* glsl */`
varying float vAlpha;
void main(){
  float d=length(gl_PointCoord-.5)*2.;
  float a=smoothstep(1.,0.,d)*vAlpha;
  if(a<.01)discard;
  gl_FragColor=vec4(.85,1.,1.,a);
}`

// ── Component ──────────────────────────────────────────────────────────────────
export function NucleusAndromeda({ className = '' }: { className?: string }) {
  const mountRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = mountRef.current
    if (!el) return
    const W = el.clientWidth, H = el.clientHeight

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(W, H)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.1
    el.appendChild(renderer.domElement)

    // Scene & Camera
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x060a0e)
    const camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 100)
    camera.position.set(0, 0, 6)

    // Bloom composer
    const composer = new EffectComposer(renderer)
    composer.addPass(new RenderPass(scene, camera))
    const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), 2.2, 0.65, 0.06)
    composer.addPass(bloom)

    // Disposables
    const geos: THREE.BufferGeometry[] = []
    const mats: THREE.Material[] = []

    const G = <T extends THREE.BufferGeometry>(g: T): T => { geos.push(g); return g }
    const M = <T extends THREE.Material>(m: T): T   => { mats.push(m); return m }

    // Helper — ShaderMaterial factory
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mkShader = (frag: string, unis: Record<string, { value: any }>, additive = false) =>
      M(new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: frag,
        uniforms: unis,
        transparent: true,
        depthWrite: false,
        blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
        side: THREE.DoubleSide,
      }))

    // ── Main nucleus disk ──────────────────────────────────────────────────────
    const nucUnis = {
      uTime:  { value: 0 },
      uPulse: { value: 1 },
      uMouse: { value: new THREE.Vector2() },
    }
    const nucMesh = new THREE.Mesh(G(new THREE.CircleGeometry(1.8, 128)), mkShader(NUCLEUS_FRAG, nucUnis))
    nucMesh.position.set(.3, -.1, 0)
    scene.add(nucMesh)

    // ── HUD rings (all share same large plane) ─────────────────────────────────
    // UV-space ring radii (plane is 4×4 world, so UV dist = world dist / 4)
    const hudPlane = G(new THREE.PlaneGeometry(4, 4))
    const ringDefs = [
      { r:.115, thick:.0040, dashes: 72, speed: .09,  color:[.20,.85,.95], op:.90 },
      { r:.175, thick:.0025, dashes: 48, speed:-.07,  color:[.10,.65,.82], op:.60 },
      { r:.220, thick:.0030, dashes: 90, speed: .05,  color:[.28,.92,1.0], op:.50 },
      { r:.275, thick:.0020, dashes:120, speed:-.11,  color:[.00,.52,.72], op:.42 },
      { r:.335, thick:.0030, dashes: 36, speed: .06,  color:[.22,.75,.85], op:.70 },
      { r:.400, thick:.0025, dashes: 28, speed:-.04,  color:[.10,.52,.72], op:.55 },
    ]
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ringUnis: Array<Record<string, { value: any }>> = []
    for (const d of ringDefs) {
      const u = {
        uTime:    { value: 0 },
        uDashes:  { value: d.dashes },
        uSpeed:   { value: d.speed },
        uThick:   { value: d.thick },
        uR:       { value: d.r },
        uColor:   { value: new THREE.Vector3(...d.color as [number,number,number]) },
        uOpacity: { value: d.op },
        uPulse:   { value: 1 },
      }
      ringUnis.push(u)
      const mesh = new THREE.Mesh(hudPlane, mkShader(RING_FRAG, u, true))
      mesh.position.set(.3, -.1, .1)
      scene.add(mesh)
    }

    // ── Tick marks ─────────────────────────────────────────────────────────────
    const tickDefs = [
      { r:.115, ticks: 72, len:.022 },
      { r:.220, ticks: 36, len:.030 },
      { r:.400, ticks: 18, len:.038 },
    ]
    for (const d of tickDefs) {
      const u = {
        uTicks:   { value: d.ticks },
        uR:       { value: d.r },
        uTickLen: { value: d.len },
        uColor:   { value: new THREE.Vector3(.90, .96, 1.) },
      }
      const mesh = new THREE.Mesh(hudPlane, mkShader(TICK_FRAG, u, true))
      mesh.position.set(.3, -.1, .15)
      scene.add(mesh)
    }

    // ── Radar sweep ────────────────────────────────────────────────────────────
    const sweepUnis = { uTime: { value: 0 }, uSpeed: { value: 1.8 }, uRadius: { value: .40 } }
    const sweepMesh = new THREE.Mesh(hudPlane, mkShader(SWEEP_FRAG, sweepUnis, true))
    sweepMesh.position.set(.3, -.1, .08)
    scene.add(sweepMesh)

    // ── Outer arc ──────────────────────────────────────────────────────────────
    const arcGeo = G(new THREE.TorusGeometry(2.26, .013, 8, 256, Math.PI * 1.45))
    const arcUnis = { uTime: { value: 0 }, uColor: { value: new THREE.Vector3(.15, .72, .92) } }
    const arcMesh = new THREE.Mesh(arcGeo, mkShader(ARC_FRAG, arcUnis, true))
    arcMesh.position.set(.3, -.1, .25)
    arcMesh.rotation.z = -Math.PI * .12
    scene.add(arcMesh)

    // Small arc accent (inner, shorter)
    const arc2Geo = G(new THREE.TorusGeometry(1.92, .008, 8, 256, Math.PI * .6))
    const arc2Mesh = new THREE.Mesh(arc2Geo, mkShader(ARC_FRAG, { uTime: { value: 0 }, uColor: { value: new THREE.Vector3(.12, .60, .82) } }, true))
    arc2Mesh.position.set(.3, -.1, .2)
    arc2Mesh.rotation.z = Math.PI * .8
    scene.add(arc2Mesh)

    // ── Satellite disk — LEFT ──────────────────────────────────────────────────
    const satLUnis = {
      uTime:  { value: 0 },
      uPulse: { value: .9 },
      uMouse: { value: new THREE.Vector2() },
    }
    const satLMesh = new THREE.Mesh(G(new THREE.CircleGeometry(.55, 64)), mkShader(NUCLEUS_FRAG, satLUnis))
    satLMesh.position.set(-2.2, .3, 0)
    scene.add(satLMesh)

    const satLPlane = G(new THREE.PlaneGeometry(1.3, 1.3))
    const satLRingUnis = {
      uTime:    { value: 0 },
      uDashes:  { value: 32 },
      uSpeed:   { value: .14 },
      uThick:   { value: .026 },
      uR:       { value: .44 },
      uColor:   { value: new THREE.Vector3(.20, .82, .92) },
      uOpacity: { value: .80 },
      uPulse:   { value: 1 },
    }
    const satLRing = new THREE.Mesh(satLPlane, mkShader(RING_FRAG, satLRingUnis, true))
    satLRing.position.set(-2.2, .3, .1)
    scene.add(satLRing)

    // Tick marks on satellite
    const satLTickUnis = {
      uTicks:   { value: 24 },
      uR:       { value: .44 },
      uTickLen: { value: .040 },
      uColor:   { value: new THREE.Vector3(.9, .96, 1.) },
    }
    const satLTick = new THREE.Mesh(satLPlane, mkShader(TICK_FRAG, satLTickUnis, true))
    satLTick.position.set(-2.2, .3, .15)
    scene.add(satLTick)

    // ── Satellite disk — BOTTOM-RIGHT ──────────────────────────────────────────
    const satRUnis = {
      uTime:  { value: 0 },
      uPulse: { value: .85 },
      uMouse: { value: new THREE.Vector2() },
    }
    const satRMesh = new THREE.Mesh(G(new THREE.CircleGeometry(.38, 64)), mkShader(NUCLEUS_FRAG, satRUnis))
    satRMesh.position.set(1.9, -1.65, 0)
    scene.add(satRMesh)

    const satRPlane = G(new THREE.PlaneGeometry(.95, .95))
    const satRRingUnis = {
      uTime:    { value: 0 },
      uDashes:  { value: 20 },
      uSpeed:   { value: -.18 },
      uThick:   { value: .028 },
      uR:       { value: .43 },
      uColor:   { value: new THREE.Vector3(.10, .72, .92) },
      uOpacity: { value: .72 },
      uPulse:   { value: 1 },
    }
    const satRRing = new THREE.Mesh(satRPlane, mkShader(RING_FRAG, satRRingUnis, true))
    satRRing.position.set(1.9, -1.65, .1)
    scene.add(satRRing)

    // ── Star particles ─────────────────────────────────────────────────────────
    const STARS = 800
    const sPos = new Float32Array(STARS * 3)
    const sSz  = new Float32Array(STARS)
    const sPh  = new Float32Array(STARS)
    for (let i = 0; i < STARS; i++) {
      sPos[i*3]   = (Math.random() - .5) * 20
      sPos[i*3+1] = (Math.random() - .5) * 14
      sPos[i*3+2] = (Math.random() - .5) * 6 - 4
      sSz[i]  = Math.random() * 1.6 + .4
      sPh[i]  = Math.random() * Math.PI * 2
    }
    const starGeo = G(new THREE.BufferGeometry())
    starGeo.setAttribute('position', new THREE.BufferAttribute(sPos, 3))
    starGeo.setAttribute('aSize',    new THREE.BufferAttribute(sSz, 1))
    starGeo.setAttribute('aPhase',   new THREE.BufferAttribute(sPh, 1))
    const starUnis = { uTime: { value: 0 } }
    scene.add(new THREE.Points(starGeo, M(new THREE.ShaderMaterial({
      vertexShader: STAR_VERT,
      fragmentShader: STAR_FRAG,
      uniforms: starUnis,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }))))

    // ── Mouse ──────────────────────────────────────────────────────────────────
    const mouse = new THREE.Vector2()
    const onMouseMove = (e: MouseEvent) => {
      const r = el.getBoundingClientRect()
      mouse.x =  ((e.clientX - r.left) / r.width)  * 2 - 1
      mouse.y = -((e.clientY - r.top)  / r.height)  * 2 + 1
    }
    el.addEventListener('mousemove', onMouseMove)

    // ── Resize ─────────────────────────────────────────────────────────────────
    const onResize = () => {
      const w = el.clientWidth, h = el.clientHeight
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
      composer.setSize(w, h)
    }
    const ro = new ResizeObserver(onResize)
    ro.observe(el)

    // ── Animation loop ─────────────────────────────────────────────────────────
    const clock = new THREE.Clock()
    const camTarget = new THREE.Vector3(0, 0, 6)
    let raf: number

    const animate = () => {
      raf = requestAnimationFrame(animate)
      const t = clock.getElapsedTime()

      // Nucleus
      nucUnis.uTime.value  = t
      nucUnis.uPulse.value = .82 + .18 * Math.sin(t * 1.3)
      nucUnis.uMouse.value.copy(mouse)

      // HUD rings
      for (const u of ringUnis) {
        u.uTime.value  = t
        u.uPulse.value = .68 + .32 * Math.sin(t * 1.9 + (u.uR.value as number) * 8)
      }

      // Sweep
      sweepUnis.uTime.value = t

      // Arc pulse
      arcUnis.uTime.value = t

      // Satellites
      satLUnis.uTime.value  = t + 2.3
      satLUnis.uMouse.value.copy(mouse)
      satLRingUnis.uTime.value = t

      satRUnis.uTime.value  = t + 5.1
      satRUnis.uMouse.value.copy(mouse)
      satRRingUnis.uTime.value = t

      // Stars
      starUnis.uTime.value = t

      // Outer arc slow drift
      arcMesh.rotation.z  += .0007
      arc2Mesh.rotation.z -= .0012

      // Camera parallax (smooth lerp toward mouse offset)
      camTarget.set(mouse.x * .45, mouse.y * .28, 6)
      camera.position.lerp(camTarget, .035)
      camera.lookAt(0, 0, 0)

      composer.render()
    }
    animate()

    // ── Cleanup ────────────────────────────────────────────────────────────────
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      el.removeEventListener('mousemove', onMouseMove)
      geos.forEach(g => g.dispose())
      mats.forEach(m => m.dispose())
      composer.dispose()
      renderer.dispose()
      if (el.contains(renderer.domElement)) el.removeChild(renderer.domElement)
    }
  }, [])

  return (
    <div ref={mountRef} className={`relative overflow-hidden ${className}`}>
      {/* ── HTML data overlay ── */}
      <div className="absolute inset-0 pointer-events-none select-none font-mono">

        {/* Top-left: coordinates */}
        <div className="absolute top-3 left-3 text-[9px] text-cyan-400/45 leading-4">
          <div>N° 34.944.226  5°988r.408</div>
          <div className="opacity-55 mt-0.5">SYS.CORE.ACTIVE</div>
        </div>

        {/* Top-right: version tag */}
        <div className="absolute top-3 right-3 text-[9px] text-cyan-300/35 text-right leading-4">
          <div>EXIMIA AI</div>
          <div className="opacity-60">v9.1</div>
        </div>

        {/* Center-right: title */}
        <div className="absolute top-[34%] right-[15%] text-right">
          <div className="text-[8px] text-cyan-400/35 mb-0.5 tracking-widest">9.1</div>
          <div className="text-[1.55rem] font-bold text-white/80 tracking-wider leading-none">
            Andromeda
          </div>
        </div>

        {/* Center-right: metrics */}
        <div className="absolute top-[56%] right-[13%] space-y-1">
          {([
            ['Precisión fiscal', '99.8%'],
            ['Latencia IA',      '14 ms'],
            ['Cumplimiento',     '100%' ],
          ] as const).map(([label, val]) => (
            <div key={label} className="flex items-center gap-2 justify-end text-[8px]">
              <span className="block w-8 h-px bg-cyan-400/25 flex-shrink-0" />
              <span className="text-white/35">{label}</span>
              <span className="text-cyan-400/70">{val}</span>
            </div>
          ))}
        </div>

        {/* Bottom strip */}
        <div className="absolute bottom-2 left-3 text-[7px] text-white/12 uppercase tracking-widest">
          SISTEMA ACTIVO
        </div>
        <div className="absolute bottom-2 right-3 text-[7px] text-white/12">
          @EXIMIA_AI
        </div>

        {/* Left satellite label */}
        <div className="absolute left-[5%] top-[35%] text-[7px] text-cyan-400/40 text-center leading-3">
          <div>SUB</div>
          <div>NODE</div>
          <div className="mt-0.5 text-white/20">A2</div>
        </div>

        {/* Bottom-right satellite label */}
        <div className="absolute right-[16%] bottom-[14%] text-[7px] text-cyan-400/35 text-right leading-3">
          <div>D9</div>
          <div className="text-white/20">ENERGY</div>
        </div>
      </div>
    </div>
  )
}
