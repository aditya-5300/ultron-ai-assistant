import * as THREE from "three";

export function createChromeEnvironment(renderer: THREE.WebGLRenderer) {
  // Paint a small lighting scene that gives the metal rings their reflected colors.
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 512;
  const context = canvas.getContext("2d")!;
  context.fillStyle = "#020712";
  context.fillRect(0, 0, 1024, 512);
  const strip = context.createLinearGradient(0, 80, 0, 280);
  strip.addColorStop(0, "#061629");
  strip.addColorStop(0.45, "#217eb0");
  strip.addColorStop(0.5, "#dcf2ff");
  strip.addColorStop(0.56, "#142f52");
  strip.addColorStop(1, "#020712");
  context.fillStyle = strip;
  context.fillRect(0, 80, 1024, 200);
  for (const position of [50, 290, 570, 850]) {
    context.fillStyle = "#eaf6ff";
    context.fillRect(position, 130, 16, 170);
    context.fillStyle = "#406d9c";
    context.fillRect(position + 24, 110, 25, 200);
  }
  context.fillStyle = "#b39275";
  context.fillRect(720, 165, 45, 65);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.mapping = THREE.EquirectangularReflectionMapping;
  const generator = new THREE.PMREMGenerator(renderer);
  const target = generator.fromEquirectangular(texture);
  texture.dispose();
  generator.dispose();
  return target;
}

export function createGyroAssembly(scene: THREE.Scene, showPedestal = true) {
  const root = new THREE.Group();
  const gyroscope = new THREE.Group();
  root.add(gyroscope);
  scene.add(root);

  const chrome = new THREE.MeshStandardMaterial({ color: 0xffcf70, metalness: 1, roughness: 0.12, envMapIntensity: 1.3, emissive: 0x8c4700, emissiveIntensity: 0.15 });
  const brushed = new THREE.MeshStandardMaterial({ color: 0xb58a42, metalness: 1, roughness: 0.26, envMapIntensity: 1.3 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x030c18, metalness: 0.9, roughness: 0.21, envMapIntensity: 0.8 });
  const cyan = new THREE.MeshBasicMaterial({ color: new THREE.Color(0x009dff).multiplyScalar(1.5) });
  const white = new THREE.MeshBasicMaterial({ color: new THREE.Color(0x91ebff).multiplyScalar(1.8) });

  function band(radius: number, width: number, height: number, material: THREE.Material, start = 0, sweep = Math.PI * 2) {
    const inner = radius - width / 2;
    const outer = radius + width / 2;
    const bevel = Math.min(width / 6, height / 3);
    const profile = [
      new THREE.Vector2(inner + bevel, -height / 2),
      new THREE.Vector2(outer - bevel, -height / 2),
      new THREE.Vector2(outer, -height / 2 + bevel),
      new THREE.Vector2(outer, height / 2 - bevel),
      new THREE.Vector2(outer - bevel, height / 2),
      new THREE.Vector2(inner + bevel, height / 2),
      new THREE.Vector2(inner, height / 2 - bevel),
      new THREE.Vector2(inner, -height / 2 + bevel),
      new THREE.Vector2(inner + bevel, -height / 2),
    ];
    return new THREE.Mesh(new THREE.LatheGeometry(profile, Math.max(12, Math.ceil(sweep * 40)), start, sweep), material);
  }

  function rim(radius: number, material: THREE.Material, thickness = 0.008) {
    const mesh = new THREE.Mesh(new THREE.TorusGeometry(radius, thickness, 8, 220), material);
    mesh.rotation.x = Math.PI / 2;
    return mesh;
  }

  const rings: THREE.Group[] = [];
  const ringSpecs = [
    { radius: 2.24, width: 0.23, tilt: [0.12, 0.15, 0.16], height: -0.06 },
    { radius: 2.01, width: 0.26, tilt: [0.66, -0.28, -0.37], height: 0.3 },
    { radius: 1.64, width: 0.27, tilt: [1.48, 0.08, 0.2], height: 0 },
    { radius: 1.98, width: 0.19, tilt: [-0.64, 0.1, -0.24], height: -0.46 },
  ];
  for (const specification of ringSpecs) {
    const assembly = new THREE.Group();
    assembly.rotation.set(specification.tilt[0], specification.tilt[1], specification.tilt[2]);
    assembly.position.y = specification.height;
    assembly.add(band(specification.radius, specification.width, 0.095, chrome));
    assembly.add(band(specification.radius - specification.width / 2 - 0.045, 0.045, 0.065, dark));
    for (const offset of [-specification.width / 2, specification.width / 2]) {
      const trim = rim(specification.radius + offset, cyan, 0.007);
      trim.position.y = 0.041;
      assembly.add(trim);
    }
    for (let index = 0; index < 44; index++) {
      const angle = index * Math.PI * 2 / 44;
      const inset = band(specification.radius, specification.width * 0.64, 0.009, index % 4 === 0 ? brushed : dark, angle, 0.045);
      inset.position.y = 0.051;
      assembly.add(inset);
      if (index % 4 === 0) {
        const lamp = band(specification.radius, specification.width * 0.42, 0.012, index % 8 === 0 ? white : cyan, angle + 0.055, 0.022);
        lamp.position.y = 0.055;
        assembly.add(lamp);
      }
    }
    gyroscope.add(assembly);
    rings.push(assembly);
  }

  const nucleus = new THREE.Group();
  gyroscope.add(nucleus);
  const core = new THREE.Mesh(new THREE.SphereGeometry(0.38, 64, 48), new THREE.MeshBasicMaterial({ color: new THREE.Color(0x00bfff).multiplyScalar(1.7), transparent: true, opacity: 0.67 }));
  nucleus.add(core);
  const latticeMaterial = new THREE.LineBasicMaterial({ color: 0x85f5ff, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false });
  for (let latitude = -3; latitude <= 3; latitude++) {
    const angle = latitude * Math.PI / 9;
    const ring = rim(0.43 * Math.cos(angle), cyan, 0.003);
    ring.position.y = 0.43 * Math.sin(angle);
    nucleus.add(ring);
  }
  for (let longitude = 0; longitude < 8; longitude++) {
    const circle = new THREE.EllipseCurve(0, 0, 0.435, 0.435).getPoints(160);
    const geometry = new THREE.BufferGeometry().setFromPoints(circle.map(point => new THREE.Vector3(point.x, point.y, 0)));
    const meridian = new THREE.LineLoop(geometry, latticeMaterial);
    meridian.rotation.y = longitude * Math.PI / 8;
    nucleus.add(meridian);
  }
  for (const radius of [0.75, 1.08, 1.35]) {
    const technicalRing = rim(radius, cyan, 0.004);
    technicalRing.rotation.set(0, 0, 0);
    nucleus.add(technicalRing);
  }
  const shell = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.SphereGeometry(1.22, 24, 12)), new THREE.LineBasicMaterial({ color: 0x007dc8, transparent: true, opacity: 0.17, blending: THREE.AdditiveBlending, depthWrite: false }));
  gyroscope.add(shell);

  const glowCanvas = document.createElement("canvas");
  glowCanvas.width = glowCanvas.height = 256;
  const glowContext = glowCanvas.getContext("2d")!;
  const glow = glowContext.createRadialGradient(128, 128, 0, 128, 128, 128);
  glow.addColorStop(0, "rgba(255,255,255,1)");
  glow.addColorStop(0.035, "rgba(220,255,255,1)");
  glow.addColorStop(0.1, "rgba(45,230,255,0.95)");
  glow.addColorStop(0.3, "rgba(0,160,255,0.32)");
  glow.addColorStop(1, "rgba(0,90,255,0)");
  glowContext.fillStyle = glow;
  glowContext.fillRect(0, 0, 256, 256);
  const glowTexture = new THREE.CanvasTexture(glowCanvas);
  const flareMaterial = new THREE.SpriteMaterial({ map: glowTexture, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false });
  const coreFlare = new THREE.Sprite(flareMaterial);
  coreFlare.scale.set(2, 2, 1);
  gyroscope.add(coreFlare);
  for (const height of [-2.18, 2.65]) {
    if (!showPedestal && height < 0) continue;
    const flare = new THREE.Sprite(flareMaterial);
    flare.position.y = height;
    flare.scale.set(1.1, 1.1, 1);
    root.add(flare);
  }
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.007, 0.007, 5.5, 16), white);
  beam.position.y = 0.35;
  root.add(beam);
  const beamHalo = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 5.5, 16), new THREE.MeshBasicMaterial({ color: 0x009cff, transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending, depthWrite: false }));
  beamHalo.position.y = 0.35;
  root.add(beamHalo);

  const pedestal = new THREE.Group();
  pedestal.visible = showPedestal;
  pedestal.position.y = -2.25;
  root.add(pedestal);
  const base = new THREE.Mesh(new THREE.CylinderGeometry(2.45, 2.5, 0.14, 160), dark);
  base.position.y = -0.21;
  pedestal.add(base);
  for (let index = 0; index < 9; index++) {
    const radius = 0.3 + index * 0.25;
    const platformRing = band(radius, index % 2 ? 0.14 : 0.065, 0.035, index % 2 ? chrome : dark);
    platformRing.position.y = -0.012 * index;
    pedestal.add(platformRing);
    const trim = rim(radius + (index % 2 ? 0.075 : 0), index % 3 === 0 ? white : cyan, 0.006);
    trim.position.y = platformRing.position.y + 0.022;
    pedestal.add(trim);
  }
  const markerGeometry = new THREE.BoxGeometry(0.018, 0.016, 0.11);
  for (let index = 0; index < 80; index++) {
    const angle = index * Math.PI / 40;
    const marker = new THREE.Mesh(markerGeometry, index % 5 === 0 ? cyan : brushed);
    marker.position.set(2.3 * Math.cos(angle), -0.04, 2.3 * Math.sin(angle));
    marker.rotation.y = -angle;
    pedestal.add(marker);
  }

  const starPositions: number[] = [];
  for (let index = 0; index < 700; index++) {
    const angle = index * 2.399963;
    const radius = 3 + (index % 29) * 0.09;
    starPositions.push(Math.cos(angle) * radius, ((index * 37) % 101) / 13 - 3.5, Math.sin(angle) * radius - 2);
  }
  const starGeometry = new THREE.BufferGeometry();
  starGeometry.setAttribute("position", new THREE.Float32BufferAttribute(starPositions, 3));
  root.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: 0x359fff, size: 0.019, transparent: true, opacity: 0.65, blending: THREE.AdditiveBlending, depthWrite: false })));
  const streaks: number[] = [];
  for (let index = 0; index < 40; index++) {
    const angle = index * Math.PI / 20;
    const height = Math.sin(angle * 3) * 1.5;
    streaks.push(Math.cos(angle) * 2.7, height, -2 + Math.sin(angle) * 0.5, Math.cos(angle) * 6.5, height * 2, -3);
  }
  const streakGeometry = new THREE.BufferGeometry();
  streakGeometry.setAttribute("position", new THREE.Float32BufferAttribute(streaks, 3));
  root.add(new THREE.LineSegments(streakGeometry, new THREE.LineBasicMaterial({ color: 0x087dcc, transparent: true, opacity: 0.25, blending: THREE.AdditiveBlending, depthWrite: false })));

  return {
    update(time: number) {
      gyroscope.rotation.y = time * 0.22;
      gyroscope.rotation.z = Math.sin(time * 0.25) * 0.08;
      pedestal.rotation.y = -time * 0.09;
      rings.forEach((ring, index) => { ring.rotation.y = ringSpecs[index].tilt[1] + time * (index % 2 ? -0.05625 : 0.045); });
      shell.rotation.y = time * 0.135;
      nucleus.rotation.y = time * 0.18;
      coreFlare.scale.setScalar(2 + Math.sin(time * 1.3) * 0.07);
    },
  };
}
