/* Photo OCR — pre-flight image quality gate (on-device).
 *
 * Scores a photo client-side the moment it is added to the strip, so a
 * blurry / dark / glary shot gets an advisory badge *before* the user
 * spends a hub round-trip on it. Advisory only — never blocks Extract.
 *
 * `analyzePixels(imageData)` is the pure kernel: plain ImageData in,
 * metrics + warnings out, no browser APIs — unit-tested with Vitest.
 * `assessImage(file)` is the browser wrapper that downscales the photo
 * onto a small offscreen canvas and hands the pixels to `analyzePixels`.
 *
 * All math is plain JS loops — no WebAssembly, no opencv.js.
 */

'use strict';

// Tunable thresholds. Calibration against a real archive sample is
// tracked as follow-up work; these are the issue #2 starting values.
// Bump sharpnessMin up to catch more blur, down to catch less.
export const THRESHOLDS = {
  sharpnessMin: 80,      // variance-of-Laplacian below this → "blurry"
  luminanceMin: 40,      // mean luminance below this → "too dark"
  clipHighMax: 0.25,     // fraction of near-white pixels above → "glare"
  analysisEdgePx: 256,   // long edge of the downscaled analysis canvas
};

// Rec. 601 luma weights — cheap, good enough for a quality proxy.
function luma(r, g, b) {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

function toGrayscale(data, count) {
  const gray = new Float64Array(count);
  for (let i = 0; i < count; i++) {
    const o = i * 4;
    gray[i] = luma(data[o], data[o + 1], data[o + 2]);
  }
  return gray;
}

// Variance of the Laplacian — the classic Pech-Pacheco focus measure.
// A sharp image has strong second-derivative response (high variance);
// a blurred one is smooth (low variance).
export function laplacianVariance(gray, w, h) {
  if (w < 3 || h < 3) return 0;
  let sum = 0;
  let sumSq = 0;
  let n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const lap =
        gray[i - 1] + gray[i + 1] + gray[i - w] + gray[i + w] - 4 * gray[i];
      sum += lap;
      sumSq += lap * lap;
      n++;
    }
  }
  if (n === 0) return 0;
  const mean = sum / n;
  return sumSq / n - mean * mean;
}

// Mean luminance + the fraction of near-white (clipped) pixels.
function luminanceStats(data, count) {
  let sum = 0;
  let clipHigh = 0;
  for (let i = 0; i < count; i++) {
    const o = i * 4;
    const l = luma(data[o], data[o + 1], data[o + 2]);
    sum += l;
    if (l >= 250) clipHigh++;
  }
  return {
    mean: count ? sum / count : 0,
    clipHighRatio: count ? clipHigh / count : 0,
  };
}

/**
 * Score an ImageData-shaped object: { data, width, height }.
 * Returns { sharpness, luminance, clip_high_ratio, warnings }.
 * `warnings` is a subset of: 'blurry', 'too dark', 'glare'.
 */
export function analyzePixels(imageData) {
  const w = imageData.width | 0;
  const h = imageData.height | 0;
  const data = imageData.data;
  const count = w * h;
  if (!count || !data || data.length < count * 4) {
    return { sharpness: 0, luminance: 0, clip_high_ratio: 0, warnings: [] };
  }
  const gray = toGrayscale(data, count);
  const sharpness = laplacianVariance(gray, w, h);
  const lum = luminanceStats(data, count);

  const warnings = [];
  if (sharpness < THRESHOLDS.sharpnessMin) warnings.push('blurry');
  if (lum.mean < THRESHOLDS.luminanceMin) warnings.push('too dark');
  if (lum.clipHighRatio > THRESHOLDS.clipHighMax) warnings.push('glare');

  return {
    sharpness,
    luminance: lum.mean,
    clip_high_ratio: lum.clipHighRatio,
    warnings,
  };
}

// Browser-only: decode `file`, downscale onto a small offscreen canvas,
// and analyze. Any failure resolves to an empty (no-warning) result —
// the gate is advisory and must never block capture.
export async function assessImage(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const longEdge = Math.max(bitmap.width, bitmap.height) || 1;
    const scale =
      longEdge > THRESHOLDS.analysisEdgePx
        ? THRESHOLDS.analysisEdgePx / longEdge
        : 1;
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));

    let canvas;
    if (typeof OffscreenCanvas !== 'undefined') {
      canvas = new OffscreenCanvas(w, h);
    } else {
      canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext('2d');
    ctx.drawImage(bitmap, 0, 0, w, h);
    if (bitmap.close) bitmap.close();
    return analyzePixels(ctx.getImageData(0, 0, w, h));
  } catch (_) {
    return { sharpness: 0, luminance: 0, clip_high_ratio: 0, warnings: [] };
  }
}
