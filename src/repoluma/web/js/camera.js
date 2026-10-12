// Global camera: screen = (world - (x, y)) * z. One scale for the whole map.

import { clamp, prefersReducedMotion } from "./util.js";

export class Camera {
  constructor(onChange) {
    this.x = 0;
    this.y = 0;
    this.z = 1;
    this.minZ = 0.01;
    this.maxZ = 100;
    this.vw = 1; // viewport size in CSS px
    this.vh = 1;
    this.onChange = onChange;
    this.anim = null;
  }

  resize(w, h) {
    this.vw = w;
    this.vh = h;
  }

  toScreen(wx, wy) {
    return [(wx - this.x) * this.z, (wy - this.y) * this.z];
  }

  toWorld(sx, sy) {
    return [sx / this.z + this.x, sy / this.z + this.y];
  }

  viewRect(margin = 0) {
    const m = margin / this.z;
    return { x: this.x - m, y: this.y - m, w: this.vw / this.z + 2 * m, h: this.vh / this.z + 2 * m };
  }

  get state() {
    return { x: this.x, y: this.y, z: this.z };
  }

  set(state, notify = true) {
    this.z = clamp(state.z, this.minZ, this.maxZ);
    this.x = state.x;
    this.y = state.y;
    if (notify) this.onChange();
  }

  stop() {
    if (this.anim) cancelAnimationFrame(this.anim.raf);
    this.anim = null;
  }

  zoomAt(sx, sy, factor) {
    this.stop();
    const [wx, wy] = this.toWorld(sx, sy);
    const z = clamp(this.z * factor, this.minZ, this.maxZ);
    this.z = z;
    this.x = wx - sx / z;
    this.y = wy - sy / z;
    this.onChange();
  }

  panBy(dxScreen, dyScreen) {
    this.stop();
    this.x -= dxScreen / this.z;
    this.y -= dyScreen / this.z;
    this.onChange();
  }

  /** Camera state that fits a world rect into the viewport (with padding fraction). */
  fitState(rect, padding = 0.1, insets = { left: 0, right: 0, top: 0, bottom: 0 }) {
    const availW = Math.max(50, this.vw - insets.left - insets.right);
    const availH = Math.max(50, this.vh - insets.top - insets.bottom);
    const z = clamp(Math.min(availW / (rect.w * (1 + 2 * padding)),
      availH / (rect.h * (1 + 2 * padding))), this.minZ, this.maxZ);
    const cx = rect.x + rect.w / 2;
    const cy = rect.y + rect.h / 2;
    const scx = insets.left + availW / 2;
    const scy = insets.top + availH / 2;
    return { x: cx - scx / z, y: cy - scy / z, z };
  }

  /**
   * Animate to a target state using van Wijk & Nuij's smooth zoom/pan path:
   * long moves zoom out, travel, and zoom back in, which preserves orientation.
   */
  flyTo(target, onDone) {
    this.stop();
    target = { ...target, z: clamp(target.z, this.minZ, this.maxZ) };
    if (prefersReducedMotion()) {
      this.set(target);
      onDone && onDone();
      return;
    }
    const w0 = this.vw / this.z, w1 = this.vw / target.z;
    const p0 = [this.x + w0 / 2, this.y + (this.vh / this.z) / 2, w0];
    const p1 = [target.x + w1 / 2, target.y + (this.vh / target.z) / 2, w1];
    const interp = interpolateZoom(p0, p1);
    const duration = clamp(interp.duration, 250, 1100);
    const start = performance.now();
    const step = (now) => {
      const t = clamp((now - start) / duration, 0, 1);
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const [cx, cy, w] = interp(e);
      const z = this.vw / w;
      this.z = z;
      this.x = cx - w / 2;
      this.y = cy - (this.vh / z) / 2;
      if (t >= 1) {
        this.set(target, false);
        this.anim = null;
        this.onChange();
        onDone && onDone();
        return;
      }
      this.onChange();
      this.anim.raf = requestAnimationFrame(step);
    };
    this.anim = { raf: requestAnimationFrame(step) };
  }
}

// van Wijk & Nuij, "Smooth and efficient zooming and panning" (2003); p = [cx, cy, width].
export function interpolateZoom(p0, p1) {
  const rho = Math.SQRT2, rho2 = 2, rho4 = 4;
  const [ux0, uy0, w0] = p0;
  const [ux1, uy1, w1] = p1;
  const dx = ux1 - ux0, dy = uy1 - uy0, d2 = dx * dx + dy * dy;
  let i, S;
  if (d2 < 1e-12 * w0 * w0) {
    S = Math.log(w1 / w0) / rho;
    i = (t) => [ux0 + t * dx, uy0 + t * dy, w0 * Math.exp(rho * t * S)];
  } else {
    const d1 = Math.sqrt(d2);
    const b0 = (w1 * w1 - w0 * w0 + rho4 * d2) / (2 * w0 * rho2 * d1);
    const b1 = (w1 * w1 - w0 * w0 - rho4 * d2) / (2 * w1 * rho2 * d1);
    const r0 = Math.log(Math.sqrt(b0 * b0 + 1) - b0);
    const r1 = Math.log(Math.sqrt(b1 * b1 + 1) - b1);
    S = (r1 - r0) / rho;
    i = (t) => {
      const s = t * S;
      const coshr0 = Math.cosh(r0);
      const u = (w0 / (rho2 * d1)) * (coshr0 * Math.tanh(rho * s + r0) - Math.sinh(r0));
      return [ux0 + u * dx, uy0 + u * dy, (w0 * coshr0) / Math.cosh(rho * s + r0)];
    };
  }
  i.duration = Math.abs(S) * 1000 * rho / Math.SQRT2 * 0.6;
  return i;
}
