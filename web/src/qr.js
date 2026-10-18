/* QR codes, drawn here rather than fetched.
 *
 * Pairing a phone means putting a daemon address AND its token on screen, and
 * the web client is one self-contained file with no network of its own. An
 * image service would therefore be handed the token on the way to the screen,
 * which is exactly the thing the token protects. So the squares are computed
 * locally: byte mode (UTF-8), ISO/IEC 18004 versions 1-40, every error
 * correction level — far more than any agentremote://profile?... payload needs.
 *
 * Structure follows the reference algorithm: encode -> error correction ->
 * place -> mask. The tables are the spec's own and are not negotiable.
 */

// Error-correction codewords per block, and how many blocks, indexed
// [level][version]; index 0 of each row is the unused version "0".
const ECC_PER_BLOCK = {
  L: [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30,
      28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
      30, 30, 30, 30],
  M: [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28,
      26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
      28, 28, 28, 28, 28],
  Q: [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28,
      28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30,
      30, 30, 30, 30, 30],
  H: [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28,
      28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
      30, 30, 30, 30, 30],
};

const EC_BLOCKS = {
  L: [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9,
      10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  M: [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17,
      17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47,
      49],
  Q: [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
      23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62,
      65, 68],
  H: [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
      25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74,
      77, 81],
};

// The 2-bit field each level is written as in the format information — not
// the same order as L/M/Q/H reads.
const EC_FORMAT_BITS = { L: 1, M: 0, Q: 3, H: 2 };

const PENALTY_N1 = 3, PENALTY_N2 = 3, PENALTY_N3 = 40, PENALTY_N4 = 10;

/** Modules a version holds before function patterns are taken out. */
function rawDataModules(ver) {
  let n = (16 * ver + 128) * ver + 64;
  if (ver >= 2) {
    const align = Math.floor(ver / 7) + 2;
    n -= (25 * align - 10) * align - 55;
    if (ver >= 7) n -= 36;
  }
  return n;
}

/** Usable data codewords (everything that is not error correction). */
function dataCodewords(ver, ec) {
  return Math.floor(rawDataModules(ver) / 8)
    - ECC_PER_BLOCK[ec][ver] * EC_BLOCKS[ec][ver];
}

/** Smallest version that holds `len` bytes at this level. */
function pickVersion(len, ec) {
  for (let v = 1; v <= 40; v++) {
    const countBits = v <= 9 ? 8 : 16;
    if (4 + countBits + 8 * len <= dataCodewords(v, ec) * 8) return v;
  }
  throw new Error("too much data for one QR code");
}

/**
 * Centres of the alignment patterns, including the two on the timing lines.
 *
 * They are spaced evenly from the LAST one backwards (the gap next to the
 * top-left finder is the one allowed to come out short), with the step
 * rounded up to an even number. Version 32 is the one case where that rule
 * disagrees with the spec's table, so it is written out.
 */
function alignPositions(ver) {
  if (ver === 1) return [];
  const count = Math.floor(ver / 7) + 2;
  const size = ver * 4 + 17;
  // span = (size - 7) - 6, shared over count - 1 gaps, rounded up to even.
  const step = ver === 32 ? 26
    : Math.ceil((ver * 4 + 4) / (count * 2 - 2)) * 2;
  const out = [6];
  for (let pos = size - 7; out.length < count; pos -= step) out.splice(1, 0, pos);
  return out;
}

// ------------------------------------------------------- GF(256) / Reed-Solomon

function gfMul(x, y) {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

/** Generator polynomial of the given degree, high term omitted. */
function rsDivisor(degree) {
  const out = new Uint8Array(degree);
  out[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      out[j] = gfMul(out[j], root);
      if (j + 1 < degree) out[j] ^= out[j + 1];
    }
    root = gfMul(root, 0x02);
  }
  return out;
}

function rsRemainder(data, divisor) {
  const out = new Uint8Array(divisor.length);
  for (const b of data) {
    const factor = b ^ out[0];
    out.copyWithin(0, 1);
    out[out.length - 1] = 0;
    for (let i = 0; i < out.length; i++) out[i] ^= gfMul(divisor[i], factor);
  }
  return out;
}

// ------------------------------------------------------------------- encoding

/** Data bytes -> the full interleaved codeword stream for this version. */
function codewords(bytes, ver, ec) {
  const capacity = dataCodewords(ver, ec) * 8;
  const bits = [];
  const push = (value, width) => {
    for (let i = width - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4);                       // byte mode
  push(bytes.length, ver <= 9 ? 8 : 16);
  for (const b of bytes) push(b, 8);
  push(0, Math.min(4, capacity - bits.length));   // terminator
  push(0, (8 - bits.length % 8) % 8);             // to a whole codeword
  for (let pad = 0xec; bits.length < capacity; pad ^= 0xec ^ 0x11) push(pad, 8);

  const data = new Uint8Array(bits.length / 8);
  bits.forEach((bit, i) => { data[i >>> 3] |= bit << (7 - (i & 7)); });

  const numBlocks = EC_BLOCKS[ec][ver];
  const eccLen = ECC_PER_BLOCK[ec][ver];
  const raw = Math.floor(rawDataModules(ver) / 8);
  const shortBlocks = numBlocks - raw % numBlocks;
  const shortLen = Math.floor(raw / numBlocks);
  const divisor = rsDivisor(eccLen);

  const blocks = [];
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const len = shortLen - eccLen + (i < shortBlocks ? 0 : 1);
    const dat = Array.from(data.slice(k, k + len));
    k += len;
    const ecc = Array.from(rsRemainder(dat, divisor));
    // Short blocks carry a placeholder so the interleave below can walk a
    // rectangle; the skip in the loop drops it again.
    if (i < shortBlocks) dat.push(0);
    blocks.push(dat.concat(ecc));
  }

  const out = [];
  for (let i = 0; i < blocks[0].length; i++) {
    for (let j = 0; j < blocks.length; j++) {
      if (i !== shortLen - eccLen || j >= shortBlocks) out.push(blocks[j][i]);
    }
  }
  return out;
}

// -------------------------------------------------------------------- drawing

const MASKS = [
  (x, y) => (x + y) % 2 === 0,
  (x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => (x * y) % 2 + (x * y) % 3 === 0,
  (x, y) => ((x * y) % 2 + (x * y) % 3) % 2 === 0,
  (x, y) => ((x + y) % 2 + (x * y) % 3) % 2 === 0,
];

function bitOf(value, i) { return ((value >>> i) & 1) !== 0; }

/**
 * The module grid for `text`.
 *
 * @returns {{size: number, dark: (x: number, y: number) => boolean,
 *            version: number, rows: boolean[][]}}
 */
export function qrMatrix(text, ec = "M") {
  if (!ECC_PER_BLOCK[ec]) throw new Error("unknown error correction level " + ec);
  const bytes = new TextEncoder().encode(String(text));
  const ver = pickVersion(bytes.length, ec);
  const size = ver * 4 + 17;

  const rows = [];
  const fixed = [];   // function patterns: never masked, never overwritten
  for (let y = 0; y < size; y++) {
    rows.push(new Array(size).fill(false));
    fixed.push(new Array(size).fill(false));
  }
  const setFn = (x, y, dark) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    rows[y][x] = dark;
    fixed[y][x] = true;
  };

  // Timing lines first; the finders and alignment squares paint over them.
  for (let i = 0; i < size; i++) {
    setFn(6, i, i % 2 === 0);
    setFn(i, 6, i % 2 === 0);
  }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const d = Math.max(Math.abs(dx), Math.abs(dy));
        setFn(cx + dx, cy + dy, d !== 2 && d !== 4);
      }
    }
  }
  const aligns = alignPositions(ver);
  for (let i = 0; i < aligns.length; i++) {
    for (let j = 0; j < aligns.length; j++) {
      // The three corners already hold finders.
      if ((i === 0 && j === 0) || (i === 0 && j === aligns.length - 1)
          || (i === aligns.length - 1 && j === 0)) continue;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          setFn(aligns[i] + dx, aligns[j] + dy,
                Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
        }
      }
    }
  }
  if (ver >= 7) {
    let rem = ver;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (ver << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const dark = bitOf(bits, i);
      const a = size - 11 + i % 3, b = Math.floor(i / 3);
      setFn(a, b, dark);
      setFn(b, a, dark);
    }
  }

  const drawFormat = (mask) => {
    const data = (EC_FORMAT_BITS[ec] << 3) | mask;
    let rem = data;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const bits = (((data << 10) | rem) ^ 0x5412) >>> 0;
    for (let i = 0; i <= 5; i++) setFn(8, i, bitOf(bits, i));
    setFn(8, 7, bitOf(bits, 6));
    setFn(8, 8, bitOf(bits, 7));
    setFn(7, 8, bitOf(bits, 8));
    for (let i = 9; i < 15; i++) setFn(14 - i, 8, bitOf(bits, i));
    for (let i = 0; i < 8; i++) setFn(size - 1 - i, 8, bitOf(bits, i));
    for (let i = 8; i < 15; i++) setFn(8, size - 15 + i, bitOf(bits, i));
    setFn(8, size - 8, true);           // the one module that is always dark
  };
  drawFormat(0);                        // reserve the strips before placing data

  // Zigzag up and down column pairs, right to left, skipping the timing column.
  const stream = codewords(bytes, ver, ec);
  let bit = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (!fixed[y][x] && bit < stream.length * 8) {
          rows[y][x] = bitOf(stream[bit >>> 3], 7 - (bit & 7));
          bit++;
        }
      }
    }
  }

  const applyMask = (mask) => {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (!fixed[y][x] && MASKS[mask](x, y)) rows[y][x] = !rows[y][x];
      }
    }
  };
  let best = 0, bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    applyMask(mask);
    drawFormat(mask);
    const score = penalty(rows, size);
    if (score < bestScore) { bestScore = score; best = mask; }
    applyMask(mask);                    // XOR again to undo
  }
  applyMask(best);
  drawFormat(best);

  return { size, version: ver, rows, dark: (x, y) => rows[y][x] };
}

/** How badly a masked grid scans: lower is better (spec rules N1-N4). */
function penalty(rows, size) {
  let score = 0;

  const addHistory = (run, history) => {
    if (history[0] === 0) run += size;  // the quiet zone counts as light
    history.pop();
    history.unshift(run);
  };
  const countFinders = (h) => {
    const n = h[1];
    const core = n > 0 && h[2] === n && h[3] === n * 3 && h[4] === n && h[5] === n;
    return (core && h[0] >= n * 4 && h[6] >= n ? 1 : 0)
      + (core && h[6] >= n * 4 && h[0] >= n ? 1 : 0);
  };

  for (let pass = 0; pass < 2; pass++) {
    for (let a = 0; a < size; a++) {
      let runColor = false, run = 0;
      const history = [0, 0, 0, 0, 0, 0, 0];
      for (let b = 0; b < size; b++) {
        const dark = pass === 0 ? rows[a][b] : rows[b][a];
        if (dark === runColor) {
          run++;
          if (run === 5) score += PENALTY_N1;
          else if (run > 5) score++;
        } else {
          addHistory(run, history);
          if (!runColor) score += countFinders(history) * PENALTY_N3;
          runColor = dark;
          run = 1;
        }
      }
      if (runColor) { addHistory(run, history); run = 0; }
      addHistory(run + size, history);
      score += countFinders(history) * PENALTY_N3;
    }
  }

  let dark = 0;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (rows[y][x]) dark++;
      if (y + 1 < size && x + 1 < size && rows[y][x] === rows[y][x + 1]
          && rows[y][x] === rows[y + 1][x] && rows[y][x] === rows[y + 1][x + 1]) {
        score += PENALTY_N2;
      }
    }
  }
  const total = size * size;
  score += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * PENALTY_N4;
  return score;
}

/**
 * `text` as an <svg> string, sized in CSS pixels by the caller.
 *
 * Always dark-on-white with a quiet zone, whatever the page theme: an
 * inverted code is a coin flip across phone cameras, and the whole point is
 * that a stranger's phone reads it on the first try.
 */
export function qrSvg(text, { ec = "M", quiet = 4, dark = "#000", light = "#fff" } = {}) {
  const { size, rows } = qrMatrix(text, ec);
  const span = size + quiet * 2;
  let path = "";
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (rows[y][x]) path += `M${x + quiet} ${y + quiet}h1v1h-1z`;
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${span} ${span}" `
    + `shape-rendering="crispEdges" role="img" aria-label="Pairing QR code">`
    + `<rect width="${span}" height="${span}" fill="${light}"/>`
    + `<path d="${path}" fill="${dark}"/></svg>`;
}
