/* Vitest unit tests for the pre-flight image quality kernel.
 *
 * `analyzePixels` is pure (ImageData in, metrics out), so the fixtures
 * are synthetic 64x64 ImageData objects — no PNG decoding, no canvas,
 * fast in plain Node. `assessImage` (the browser/canvas wrapper) is not
 * unit-tested here; it is exercised by the webapp e2e smoke suite.
 */
import { describe, it, expect } from 'vitest';
import { analyzePixels, THRESHOLDS } from '../quality.js';

// Build an ImageData-shaped object from a per-pixel [r, g, b] function.
function makeImage(w, h, fn) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const rgb = fn(x, y);
      const o = (y * w + x) * 4;
      data[o] = rgb[0];
      data[o + 1] = rgb[1];
      data[o + 2] = rgb[2];
      data[o + 3] = 255;
    }
  }
  return { data, width: w, height: h };
}

describe('analyzePixels', () => {
  it('flags a uniform (blurry) image', () => {
    const r = analyzePixels(makeImage(64, 64, () => [140, 140, 140]));
    expect(r.sharpness).toBeLessThan(THRESHOLDS.sharpnessMin);
    expect(r.warnings).toContain('blurry');
  });

  it('passes a sharp, mid-tone image with no warnings', () => {
    // Mid-tone checkerboard: high second-derivative response, but no
    // clipping and a comfortable mean luminance.
    const r = analyzePixels(
      makeImage(64, 64, (x, y) =>
        (x + y) % 2 ? [180, 180, 180] : [100, 100, 100]
      )
    );
    expect(r.sharpness).toBeGreaterThan(THRESHOLDS.sharpnessMin);
    expect(r.warnings).toEqual([]);
  });

  it('flags a too-dark image', () => {
    const r = analyzePixels(makeImage(64, 64, () => [10, 10, 10]));
    expect(r.luminance).toBeLessThan(THRESHOLDS.luminanceMin);
    expect(r.warnings).toContain('too dark');
  });

  it('flags a glary (over-clipped) image', () => {
    // ~70% of the frame pinned at white — clip ratio well over the cap.
    const r = analyzePixels(
      makeImage(64, 64, (x) => (x < 64 * 0.3 ? [20, 20, 20] : [255, 255, 255]))
    );
    expect(r.clip_high_ratio).toBeGreaterThan(THRESHOLDS.clipHighMax);
    expect(r.warnings).toContain('glare');
  });

  it('returns an empty result for a degenerate image', () => {
    const r = analyzePixels({
      data: new Uint8ClampedArray(0),
      width: 0,
      height: 0,
    });
    expect(r.warnings).toEqual([]);
    expect(r.sharpness).toBe(0);
  });
});
