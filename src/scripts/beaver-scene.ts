import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

const MODEL_URL = "/models/beaver.glb";
const INK = new THREE.Color("#1a1110");
const LEAF_COLORS = ["#b4002b", "#d1361b", "#e2711d", "#eaa221", "#9e1a2a"];
// The drawing shows the beaver from its tail side, face turned to the right.
const BASE_YAW = 0.38;

interface Options {
  /** Where the beaver should sit, in viewport pixels (the rest is UI). */
  stage: () => DOMRect;
  reducedMotion: boolean;
  onTap: () => void;
}

/** Hard-stepped shading ramp: three flat tones like the drawing's cel shading. */
function toonRamp(): THREE.DataTexture {
  const tones = new Uint8Array([110, 190, 255]);
  const tex = new THREE.DataTexture(tones, tones.length, 1, THREE.RedFormat);
  tex.minFilter = tex.magFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  return tex;
}

/** Inverted-hull ink outline, pushed out along normals in view space so the
 * line weight is the same on every part regardless of mesh quantization. */
function outlineMaterial(thickness: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    side: THREE.BackSide,
    uniforms: { color: { value: INK }, thickness: { value: thickness } },
    vertexShader: /* glsl */ `
      uniform float thickness;
      void main() {
        vec4 p = vec4(position, 1.0);
        vec3 n = normal;
        #ifdef USE_INSTANCING
          p = instanceMatrix * p;
          n = mat3(instanceMatrix) * n;
        #endif
        vec4 mv = modelViewMatrix * p;
        mv.xyz += normalize(normalMatrix * n) * thickness;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 color;
      void main() { gl_FragColor = vec4(color, 1.0); }
    `,
  });
}

function shadowTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(70,40,15,0.55)");
  grad.addColorStop(1, "rgba(70,40,15,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}

/** Copy a (possibly quantized) geometry into plain float attributes with
 * `matrix` baked in, so it can be reused at any scale. */
function bakeGeometry(src: THREE.BufferGeometry, matrix: THREE.Matrix4) {
  const out = new THREE.BufferGeometry();
  for (const name of ["position", "normal"] as const) {
    const a = src.getAttribute(name);
    const arr = new Float32Array(a.count * 3);
    for (let i = 0; i < a.count; i++) {
      arr[i * 3] = a.getX(i);
      arr[i * 3 + 1] = a.getY(i);
      arr[i * 3 + 2] = a.getZ(i);
    }
    out.setAttribute(name, new THREE.BufferAttribute(arr, 3));
  }
  if (src.index) out.setIndex(Array.from(src.index.array));
  out.applyMatrix4(matrix);
  out.center();
  return out;
}

const damp = (from: number, to: number, rate: number, dt: number) =>
  THREE.MathUtils.lerp(from, to, 1 - Math.exp(-rate * dt));

export async function mountBeaverScene(canvas: HTMLCanvasElement, opts: Options) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(28, 1, 1.5, 60);

  scene.add(new THREE.AmbientLight("#fff3e0", 1.1));
  const sun = new THREE.DirectionalLight("#fff8ec", 2.4);
  sun.position.set(-3, 6, 5);
  scene.add(sun);

  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const gltf = await loader.loadAsync(MODEL_URL);
  const beaver = gltf.scene.getObjectByName("Beaver") ?? gltf.scene;

  // Toon materials + ink outlines in the style of the drawing
  const ramp = toonRamp();
  const ink = outlineMaterial(0.022);
  const pickables: THREE.Object3D[] = [];
  const meshes: THREE.Mesh[] = [];
  beaver.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh);
  });
  for (const mesh of meshes) {
    const src = mesh.material as THREE.MeshStandardMaterial;
    mesh.material =
      src.name === "Shine"
        ? new THREE.MeshBasicMaterial({ color: "#ffffff" })
        : new THREE.MeshToonMaterial({ color: src.color, map: src.map, gradientMap: ramp });
    const outline = mesh.userData.outline ?? mesh.parent?.userData.outline;
    if (outline) mesh.add(new THREE.Mesh(mesh.geometry, ink));
    pickables.push(mesh);
  }

  // Pivots for the idle animation
  const tail = beaver.getObjectByName("Tail")!;
  const tailPivot = new THREE.Group();
  const [px, py, pz] = tail.userData.pivot as number[]; // Blender Z-up coords
  tailPivot.position.set(px, pz, -py);
  beaver.add(tailPivot);
  tailPivot.attach(tail);

  const eyes = ["Eye_L", "Eye_R"].map((n) => beaver.getObjectByName(n)!);
  const eyeScale = eyes.map((e) => e.scale.y);
  const leaf = beaver.getObjectByName("LeafGroup")!;
  const leafRot = leaf.rotation.clone();

  // Turntable: yaw (drag/pointer) on the outside, hop on the inside
  const turntable = new THREE.Group();
  const hopper = new THREE.Group();
  hopper.add(beaver);
  turntable.add(hopper);
  scene.add(turntable);
  turntable.rotation.y = BASE_YAW;

  const shadow = new THREE.Mesh(
    new THREE.PlaneGeometry(3.6, 2.6),
    new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.set(-0.25, 0.005, -0.2);
  scene.add(shadow);

  const bounds = new THREE.Box3().setFromObject(turntable);
  const size = bounds.getSize(new THREE.Vector3());
  const center = bounds.getCenter(new THREE.Vector3());

  // Background: maple leaves drifting down behind the beaver
  const leafMesh = beaver.getObjectByName("Leaf") as THREE.Mesh;
  const fallingGeo = bakeGeometry(leafMesh.geometry, leafMesh.matrix);
  const COUNT = 42;
  const falling = new THREE.InstancedMesh(
    fallingGeo,
    new THREE.MeshToonMaterial({ gradientMap: ramp, side: THREE.DoubleSide }),
    COUNT,
  );
  const fallingInk = new THREE.InstancedMesh(fallingGeo, outlineMaterial(0.012), COUNT);
  const drift = Array.from({ length: COUNT }, (_, i) => ({
    x: THREE.MathUtils.randFloat(-9, 9),
    y: THREE.MathUtils.randFloat(-2, 9),
    z: THREE.MathUtils.randFloat(-12, -2.5),
    scale: THREE.MathUtils.randFloat(0.35, 0.7),
    speed: THREE.MathUtils.randFloat(0.25, 0.55),
    sway: THREE.MathUtils.randFloat(0.4, 1.2),
    phase: Math.random() * Math.PI * 2,
    spin: new THREE.Vector3(Math.random(), Math.random(), Math.random()).normalize(),
    spinRate: THREE.MathUtils.randFloat(0.4, 1.4) * (i % 2 ? 1 : -1),
  }));
  drift.forEach((_, i) =>
    falling.setColorAt(i, new THREE.Color(LEAF_COLORS[i % LEAF_COLORS.length])),
  );
  scene.add(falling, fallingInk);

  const dummy = new THREE.Object3D();
  function placeLeaves(t: number, dt: number) {
    drift.forEach((d, i) => {
      d.y -= d.speed * dt;
      if (d.y < -2.5) {
        d.y = 9;
        d.x = THREE.MathUtils.randFloat(-9, 9);
      }
      dummy.position.set(d.x + Math.sin(t * d.sway + d.phase) * 0.6, d.y, d.z);
      dummy.quaternion.setFromAxisAngle(d.spin, t * d.spinRate + d.phase);
      dummy.scale.setScalar(d.scale);
      dummy.updateMatrix();
      falling.setMatrixAt(i, dummy.matrix);
      fallingInk.setMatrixAt(i, dummy.matrix);
    });
    falling.instanceMatrix.needsUpdate = true;
    fallingInk.instanceMatrix.needsUpdate = true;
  }

  // Fit the beaver into the stage rect, the rest of the viewport stays free
  const elevation = THREE.MathUtils.degToRad(8);
  function resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const stage = opts.stage();
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const fitH = (size.y * h) / (0.88 * stage.height * 2 * tan);
    const fitW = (size.x * w) / (0.9 * stage.width * 2 * tan * camera.aspect);
    const dist = Math.max(fitH, fitW) + size.z / 2;
    camera.position.set(center.x, center.y + Math.sin(elevation) * dist, Math.cos(elevation) * dist);
    camera.lookAt(center.x, center.y, 0);
    const offY = h / 2 - (stage.top + stage.height / 2);
    const offX = w / 2 - (stage.left + stage.width / 2);
    camera.setViewOffset(w, h, offX, offY, w, h);
    camera.updateProjectionMatrix();
  }
  resize();
  window.addEventListener("resize", resize);

  // Interaction: pointer-follow, drag to spin, tap to hop
  let lookX = 0;
  let lookY = 0;
  let dragYaw = 0;
  let drag: { x: number; yaw: number; t: number; moved: number } | null = null;
  let hopT = -1;
  const ray = new THREE.Raycaster();

  window.addEventListener("pointermove", (e) => {
    lookX = (e.clientX / window.innerWidth) * 2 - 1;
    lookY = (e.clientY / window.innerHeight) * 2 - 1;
    if (drag) {
      drag.moved = Math.max(drag.moved, Math.abs(e.clientX - drag.x));
      dragYaw = drag.yaw + ((e.clientX - drag.x) / window.innerWidth) * Math.PI * 2;
    }
  });
  canvas.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, yaw: dragYaw, t: performance.now(), moved: 0 };
  });
  const endDrag = (e: PointerEvent) => {
    if (!drag) return;
    const tap = drag.moved < 6 && performance.now() - drag.t < 400;
    drag = null;
    if (!tap) return;
    const ndc = new THREE.Vector2(
      (e.clientX / window.innerWidth) * 2 - 1,
      -(e.clientY / window.innerHeight) * 2 + 1,
    );
    ray.setFromCamera(ndc, camera);
    if (ray.intersectObjects(pickables, false).length) {
      hopT = 0;
      opts.onTap();
    }
  };
  window.addEventListener("pointerup", endDrag);
  window.addEventListener("pointercancel", () => (drag = null));
  canvas.addEventListener("pointermove", (e) => {
    if (drag) return;
    const ndc = new THREE.Vector2(
      (e.clientX / window.innerWidth) * 2 - 1,
      -(e.clientY / window.innerHeight) * 2 + 1,
    );
    ray.setFromCamera(ndc, camera);
    canvas.style.cursor = ray.intersectObjects(pickables, false).length ? "pointer" : "grab";
  });

  // Idle life: breathing, blinking, tail sway, leaf jiggle
  let nextBlink = 1.5;
  const clock = new THREE.Clock();
  const motion = opts.reducedMotion ? 0 : 1;
  placeLeaves(0, 0);

  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;

    if (!drag) dragYaw = damp(dragYaw, 0, 1.2, dt);
    const yaw = BASE_YAW + dragYaw + lookX * 0.35 * motion;
    turntable.rotation.y = damp(turntable.rotation.y, yaw, 6, dt);
    turntable.rotation.x = damp(turntable.rotation.x, lookY * 0.08 * motion, 4, dt);

    const breathe = Math.sin(t * 2.2) * motion;
    beaver.scale.set(1 - breathe * 0.006, 1 + breathe * 0.012, 1 - breathe * 0.006);
    tailPivot.rotation.set(Math.sin(t * 1.1) * 0.05 * motion, Math.sin(t * 1.6) * 0.1 * motion, 0);
    leaf.rotation.set(leafRot.x, leafRot.y + Math.sin(t * 1.3) * 0.05 * motion, leafRot.z);

    // Blink every few seconds
    if (t > nextBlink) {
      const p = (t - nextBlink) / 0.16;
      const s = p >= 1 ? 1 : 1 - Math.sin(Math.PI * p) * 0.92;
      eyes.forEach((e, i) => (e.scale.y = eyeScale[i] * s));
      if (p >= 1) nextBlink = t + THREE.MathUtils.randFloat(2, 5.5);
    }

    // Hop with squash and stretch
    if (hopT >= 0) {
      hopT += dt / 0.6;
      const p = Math.min(hopT, 1);
      hopper.position.y = Math.sin(Math.PI * p) * 0.5;
      const squash = Math.sin(Math.PI * 2 * p) * 0.08;
      hopper.scale.set(1 - squash, 1 + squash, 1 - squash);
      if (hopT >= 1) {
        hopT = -1;
        hopper.position.y = 0;
        hopper.scale.set(1, 1, 1);
      }
    }

    if (motion) placeLeaves(t, dt);

    renderer.render(scene, camera);
  });
}
