export class GestureZoom {
  private distance: number | null = null;
  private lastTime: number | null = null;

  reset() {
    this.distance = null;
    this.lastTime = null;
  }

  update(distance: number, time: number): number | null {
    // Ignore tiny shakes and lost tracking so the orb does not suddenly jump closer or farther away.
    if (!Number.isFinite(distance) || !Number.isFinite(time) || distance < 0.08) {
      this.reset();
      return null;
    }
    const previous = this.distance;
    const gap = this.lastTime === null ? Infinity : time - this.lastTime;
    this.lastTime = time;
    if (previous === null || gap <= 0 || gap > 250) {
      this.distance = distance;
      return null;
    }
    const change = Math.log(previous / distance);
    if (Math.abs(change) > Math.log(1.35)) {
      this.distance = distance;
      return null;
    }
    if (Math.abs(change) < 0.008) return null;
    const step = Math.max(-0.06, Math.min(0.06, change));
    this.distance = distance;
    return Math.exp(step);
  }
}
