"""Tiny nearest-neighbour index (pure numpy) — drop-in for the bit of scipy.cKDTree we use,
so the app needs no extra package on locked-down laptops."""
import math

import numpy as np


class NearTree:
    def __init__(self, xy, cell=None):
        self.xy = np.asarray(xy, float).reshape(-1, 2)
        n = len(self.xy)
        if cell is None:
            if n:
                span = np.ptp(self.xy, axis=0)
                area = max(float(span[0] * span[1]), 1.0)
                cell = max(math.sqrt(area / max(n, 1)) * 2.0, 0.25)
            else:
                cell = 1.0
        self.cell = cell
        self.buckets = {}
        if n:
            keys = np.floor(self.xy / cell).astype(np.int64)
            for i, (a, b) in enumerate(keys):
                self.buckets.setdefault((int(a), int(b)), []).append(i)
            self.bmin, self.bmax = keys.min(axis=0), keys.max(axis=0)
        self.maxring = int(max(np.max(self.bmax - self.bmin), 0)) + 2 if n else 0

    def _query1(self, p, k):
        cx, cy = int(math.floor(p[0] / self.cell)), int(math.floor(p[1] / self.cell))
        cand, r, need_r = [], 0, None
        while r <= self.maxring:
            if r == 0:
                cells = [(cx, cy)]
            else:
                cells = [(cx + dx, cy + r) for dx in range(-r, r + 1)] + [(cx + dx, cy - r) for dx in range(-r, r + 1)] \
                    + [(cx + r, cy + dy) for dy in range(-r + 1, r)] + [(cx - r, cy + dy) for dy in range(-r + 1, r)]
            for c in cells:
                b = self.buckets.get(c)
                if b:
                    cand.extend(b)
            if need_r is None and len(cand) >= k:
                d = np.hypot(*(self.xy[cand] - p).T)
                kth = np.partition(d, k - 1)[k - 1]
                need_r = r + int(math.ceil(kth / self.cell)) + 1
            if need_r is not None and r >= need_r:
                break
            r += 1
        if not cand:
            return np.full(k, np.inf), np.full(k, len(self.xy), dtype=np.int64)
        cand = np.array(cand)
        d = np.hypot(*(self.xy[cand] - p).T)
        o = np.argsort(d)[:k]
        dd, ii = d[o], cand[o]
        if len(dd) < k:
            dd = np.r_[dd, np.full(k - len(dd), np.inf)]
            ii = np.r_[ii, np.full(k - len(ii), len(self.xy))]
        return dd, ii

    def query(self, q, k=1):
        q = np.asarray(q, float).reshape(-1, 2)
        D = np.empty((len(q), k))
        I = np.empty((len(q), k), dtype=np.int64)
        for j, p in enumerate(q):
            D[j], I[j] = self._query1(p, k)
        if k == 1:
            return D[:, 0], I[:, 0]
        return D, I
