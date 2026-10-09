#!/usr/bin/env python3
"""Discovery Engine V0 -- event-sourced, resumable, no LLM anywhere.

State lives in ./state (override with DE_STATE). Commands:
  plan       decide the next experiments and enqueue them (logged with a reason)
  work       run queued jobs; safe to kill at any moment, resume by running again
  analyze    estimate, test, write report.md
  verify     check the hash chain and every artifact hash
  status     queue summary
  selftest   null / planted / known-answer / crash-resume / second-engine tests
"""
import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

STATE = os.environ.get("DE_STATE", "state")
REPORT = os.environ.get("DE_REPORT", "report.md")
MAX_ATTEMPTS = 3
LEASE_SECONDS = float(os.environ.get("DE_LEASE", "300"))
ONSAGER_TC = 2.0 / math.log(1.0 + math.sqrt(2.0))  # used ONLY for after-the-fact comparison
SIZES = (8, 16, 32)
BUDGET = {8: (300, 4000), 16: (500, 5000), 32: (800, 6000)}  # (burn-in, measured sweeps)
COARSE = [round(1.8 + 0.1 * k, 4) for k in range(13)]


# ---------------------------------------------------------------- utilities
def sha(x):
    if isinstance(x, str):
        x = x.encode()
    return hashlib.sha256(x).hexdigest()


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def code_hash():
    with open(os.path.abspath(__file__), "rb") as f:
        return sha(f.read())[:16]


# ---------------------------------------------------------- append-only log
class Log:
    """Hash-chained JSONL event log + content-addressed artifact store."""

    def __init__(self, state=None):
        self.dir = state or STATE
        self.path = os.path.join(self.dir, "events.jsonl")
        self.art = os.path.join(self.dir, "artifacts")
        os.makedirs(self.art, exist_ok=True)
        self.events = self._load()
        self.seq = self.events[-1]["seq"] if self.events else 0
        self.prev = self.events[-1]["hash"] if self.events else "0" * 64

    def _load(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, "rb") as f:
            data = f.read()
        lines = [ln for ln in data.split(b"\n") if ln.strip()]
        events, torn = [], False
        for i, ln in enumerate(lines):
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                if i == len(lines) - 1:  # torn final write after a crash: drop it
                    torn = True
                else:
                    raise
        if torn or (data and not data.endswith(b"\n")):
            with open(self.path, "wb") as f:
                for e in events:
                    f.write((canon(e) + "\n").encode())
        return events

    def append(self, etype, payload):
        self.seq += 1
        body = {"seq": self.seq, "ts": round(time.time(), 3), "type": etype,
                "payload": payload, "prev": self.prev}
        body["hash"] = sha(canon(body))
        with open(self.path, "ab") as f:
            f.write((canon(body) + "\n").encode())
            f.flush()
            os.fsync(f.fileno())
        self.events.append(body)
        self.prev = body["hash"]
        return body

    def put_artifact(self, obj):
        data = canon(obj)
        h = sha(data)
        p = os.path.join(self.art, h + ".json")
        if not os.path.exists(p):
            tmp = p + ".tmp"
            with open(tmp, "w") as f:
                f.write(data)
            os.replace(tmp, p)
        return h

    def get_artifact(self, h):
        with open(os.path.join(self.art, h + ".json")) as f:
            return json.load(f)


def verify_chain(events):
    prev = "0" * 64
    for i, e in enumerate(events, 1):
        rest = {k: v for k, v in e.items() if k != "hash"}
        if e["seq"] != i or e["prev"] != prev or sha(canon(rest)) != e["hash"]:
            return False, i
        prev = e["hash"]
    return True, len(events)


def verify_artifacts(log):
    bad = []
    for e in log.events:
        if e["type"] == "JobCompleted":
            h = e["payload"]["artifact"]
            p = os.path.join(log.art, h + ".json")
            if not os.path.exists(p):
                bad.append((h, "missing"))
            else:
                with open(p) as f:
                    if sha(f.read()) != h:
                        bad.append((h, "hash mismatch"))
    return bad


# ------------------------------------------------------------- job queue
def project(events):
    """Current job table, rebuilt from the log. No other state exists."""
    jobs = {}
    for e in events:
        t, p = e["type"], e["payload"]
        if t == "JobEnqueued":
            jobs.setdefault(p["job_id"], {"spec": p["spec"], "state": "queued", "attempt": 0,
                                          "expiry": 0.0, "artifact": None})
        elif t == "JobLeased":
            j = jobs[p["job_id"]]
            j.update(state="leased", expiry=p["expiry"], attempt=j["attempt"] + 1)
        elif t == "JobCompleted":
            jobs[p["job_id"]].update(state="done", artifact=p["artifact"])
        elif t == "JobFailed":
            jobs[p["job_id"]].update(state="queued", error=p["error"])
    return jobs


def make_spec(J, L, T, seed, engine="fast"):
    burn, sweeps = BUDGET.get(L, (500, 5000))
    return {"sub": "ising2d", "engine": engine, "J": J, "L": L, "T": round(float(T), 4),
            "seed": seed, "burn": burn, "sweeps": sweeps}


def enqueue(log, specs):
    jobs = project(log.events)
    n = 0
    for spec in specs:
        jid = sha(canon(spec))[:16]
        if jid not in jobs:
            log.append("JobEnqueued", {"job_id": jid, "spec": spec})
            jobs[jid] = None
            n += 1
    return n


def runnable(log, reclaim, limit):
    now = time.time()
    out = []
    for jid, j in project(log.events).items():
        if j["attempt"] >= MAX_ATTEMPTS:
            continue
        if j["state"] == "queued" or (j["state"] == "leased" and (reclaim or j["expiry"] < now)):
            out.append((jid, j["spec"]))
            if len(out) >= limit:
                break
    return out


def work(log, budget, parallel, reclaim, owner="worker"):
    t0, done = time.time(), 0
    with ProcessPoolExecutor(max_workers=parallel) as ex:
        while time.time() - t0 < budget:
            batch = runnable(log, reclaim, parallel)
            if not batch:
                break
            futs = {}
            for jid, spec in batch:
                log.append("JobLeased", {"job_id": jid, "owner": owner,
                                         "expiry": time.time() + LEASE_SECONDS})
                futs[ex.submit(run_spec, spec)] = jid
            for fut in as_completed(futs):
                jid = futs[fut]
                try:
                    h = log.put_artifact(fut.result())
                    log.append("JobCompleted", {"job_id": jid, "artifact": h})
                    done += 1
                except Exception as err:  # recorded, never hidden
                    log.append("JobFailed", {"job_id": jid, "error": repr(err)[:300]})
    return done


# ------------------------------------------------------------ simulators
def _observe(s):
    n = s.size
    e = -float((s * (np.roll(s, -1, 0) + np.roll(s, -1, 1))).sum()) / n
    return e, float(s.sum()) / n


def sim_fast(L, T, J, burn, sweeps, rng):
    """Checkerboard Metropolis, vectorised. Needs even L, periodic boundary."""
    s = np.ones((L, L), dtype=np.int8)
    ii, jj = np.indices((L, L))
    masks = [((ii + jj) % 2) == 0, ((ii + jj) % 2) == 1]
    bJ = J / T
    e, m = np.empty(sweeps), np.empty(sweeps)
    for t in range(burn + sweeps):
        for mk in masks:
            nb = np.roll(s, 1, 0) + np.roll(s, -1, 0) + np.roll(s, 1, 1) + np.roll(s, -1, 1)
            dE = 2 * s * nb
            flip = mk & (rng.random((L, L)) < np.exp(-bJ * dE))
            s = np.where(flip, -s, s)
        if t >= burn:
            e[t - burn], m[t - burn] = _observe(s)
    return e, m


def sim_ref(L, T, J, burn, sweeps, rng):
    """Reference engine: plain single-spin Metropolis, typewriter order. Slow, readable."""
    s = [[1] * L for _ in range(L)]
    bJ = J / T
    acc = {4: math.exp(-4 * bJ), 8: math.exp(-8 * bJ)}
    n = L * L
    e, m = np.empty(sweeps), np.empty(sweeps)
    for t in range(burn + sweeps):
        u = rng.random(n).tolist()
        k = 0
        for i in range(L):
            row, up, dn = s[i], s[(i - 1) % L], s[(i + 1) % L]
            for j in range(L):
                dE = 2 * row[j] * (up[j] + dn[j] + row[(j - 1) % L] + row[(j + 1) % L])
                if dE <= 0 or u[k] < acc[dE]:
                    row[j] = -row[j]
                k += 1
        if t >= burn:
            M = sum(map(sum, s))
            E = -sum(s[i][j] * (s[(i + 1) % L][j] + s[i][(j + 1) % L])
                     for i in range(L) for j in range(L))
            e[t - burn], m[t - burn] = E / n, M / n
    return e, m


def sim_null(L, T, J, burn, sweeps, rng):
    """Null substrate: independent random spins, no interaction, no transition."""
    e, m = np.empty(sweeps), np.empty(sweeps)
    for a in range(0, sweeps, 500):
        b = min(sweeps, a + 500)
        S = (rng.integers(0, 2, size=(b - a, L, L), dtype=np.int8) * 2 - 1).astype(np.int8)
        e[a:b] = -(S * (np.roll(S, -1, 1) + np.roll(S, -1, 2))).sum(axis=(1, 2)) / (L * L)
        m[a:b] = S.sum(axis=(1, 2)) / (L * L)
    return e, m


SIMS = {"fast": sim_fast, "ref": sim_ref, "null_iid": sim_null}


def tau_int(x):
    x = np.asarray(x, float) - np.mean(x)
    n = len(x)
    if n < 4 or x.var() == 0:
        return 0.5
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac /= ac[0]
    s = 0.5
    for k in range(1, n):
        if ac[k] < 0:
            break
        s += ac[k]
    return float(s)


def summarize(e, m, nblocks=10):
    n = len(m) // nblocks * nblocks
    a, m2 = np.abs(m[:n]), m[:n] ** 2

    def blk(x):
        return [float(v) for v in x.reshape(nblocks, -1).mean(axis=1)]

    return {"absm": blk(a), "m2": blk(m2), "m4": blk(m2 ** 2), "e": blk(e[:n])}


def run_spec(spec):
    """Pure function: spec -> result. Seeded only by the spec itself (counter-based RNG)."""
    rng = np.random.Generator(np.random.Philox(key=int(sha(canon(spec))[:32], 16)))
    e, m = SIMS[spec["engine"]](spec["L"], spec["T"], spec["J"], spec["burn"], spec["sweeps"], rng)
    return {"spec": spec, "blocks": summarize(e, m), "tau_absm": round(tau_int(np.abs(m)), 3),
            "n": int(len(m)),
            "prov": {"code": code_hash(), "numpy": np.__version__, "python": platform.python_version()}}


def run_many(specs, parallel):
    if parallel <= 1:
        return [run_spec(s) for s in specs]
    with ProcessPoolExecutor(max_workers=parallel) as ex:
        return list(ex.map(run_spec, specs, chunksize=2))


# -------------------------------------------------------------- analysis
def load_results(log, engine="fast", J=None):
    out = []
    for j in project(log.events).values():
        s = j["spec"]
        if j["state"] == "done" and s["engine"] == engine and (J is None or s["J"] == J):
            out.append(log.get_artifact(j["artifact"]))
    return out


def pool_blocks(results):
    g = {}
    for r in results:
        s = r["spec"]
        d = g.setdefault((s["L"], s["T"]), {k: [] for k in ("absm", "m2", "m4", "e")})
        for k in d:
            d[k] += r["blocks"][k]
    return g


def curves(g, rng=None):
    """(T, chi, binder, |m|) per size. chi and binder are computed from generic moments."""
    out = {}
    for (L, T), d in sorted(g.items()):
        n = len(d["m2"])
        idx = np.arange(n) if rng is None else rng.integers(0, n, n)
        absm, m2, m4 = (float(np.asarray(d[k])[idx].mean()) for k in ("absm", "m2", "m4"))
        out.setdefault(L, []).append((T, L * L * (m2 - absm ** 2) / T, 1 - m4 / (3 * m2 ** 2), absm))
    return {L: np.array(v) for L, v in out.items()}


def estimate(cv):
    Ls = sorted(cv)
    peaks = {}
    for L in Ls:
        T, chi = cv[L][:, 0], cv[L][:, 1]
        i = int(np.argmax(chi))
        Tp, hp, edge = float(T[i]), float(chi[i]), i in (0, len(T) - 1)
        if not edge:
            c = np.polyfit(T[i - 1:i + 2], chi[i - 1:i + 2], 2)
            if c[0] < 0:
                x = -c[1] / (2 * c[0])
                if T[i - 1] <= x <= T[i + 1]:
                    Tp, hp = float(x), float(np.polyval(c, x))
        peaks[L] = (Tp, hp, edge)
    g_nu = float(np.polyfit(np.log(Ls), np.log([peaks[L][1] for L in Ls]), 1)[0])
    tp = float(np.mean([peaks[L][0] for L in Ls]))
    picked = []
    for L1, L2 in zip(Ls[:-1], Ls[1:]):
        a, b = cv[L1], cv[L2]
        if len(a) != len(b) or not np.allclose(a[:, 0], b[:, 0]):
            continue
        d, T = a[:, 2] - b[:, 2], a[:, 0]
        cr = [float(T[i] + (T[i + 1] - T[i]) * (-d[i]) / (d[i + 1] - d[i]))
              for i in range(len(d) - 1) if d[i] < 0 <= d[i + 1]]
        if cr:
            picked.append(min(cr, key=lambda x: abs(x - tp)))
    tc = float(np.mean(picked)) if picked else None
    b_nu = None
    if tc is not None:
        am = [float(np.interp(tc, cv[L][:, 0], cv[L][:, 3])) for L in Ls]
        if min(am) > 0:
            b_nu = float(-np.polyfit(np.log(Ls), np.log(am), 1)[0])
    return {"peaks": peaks, "g_nu": g_nu, "tp": tp, "tc": tc, "b_nu": b_nu,
            "edge": any(p[2] for p in peaks.values())}


def analyze_results(results, B=100, seed=0):
    g = pool_blocks(results)
    est = estimate(curves(g))
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(B):
        try:
            boots.append(estimate(curves(g, rng)))
        except (ValueError, np.linalg.LinAlgError):
            pass

    def ci(key):
        v = np.array([b[key] for b in boots if b[key] is not None and np.isfinite(b[key])])
        if len(v) < 10:
            return (None, None)
        return (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))

    est["ci"] = {k: ci(k) for k in ("g_nu", "tc", "b_nu", "tp")}
    lo = est["ci"]["g_nu"][0]
    est["detected"] = bool(lo is not None and lo > 0.5 and est["tc"] is not None and not est["edge"])
    return est


def r2_check(log):
    ref, fast = pool_blocks(load_results(log, "ref", 1.0)), pool_blocks(load_results(log, "fast", 1.0))
    rows = []
    for (L, T), d in sorted(ref.items()):
        f = fast.get((L, T))
        if not f:
            continue
        for k in ("absm", "e"):
            a, b = np.array(d[k]), np.array(f[k])
            se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            rows.append((L, T, k, float(a.mean()), float(b.mean()), abs(a.mean() - b.mean()) / max(se, 1e-12)))
    return bool(rows) and all(r[-1] < 5 for r in rows), rows


# ------------------------------------------------------------- selector
def plan(log):
    jobs = project(log.events)
    if any(j["state"] in ("queued", "leased") and j["attempt"] < MAX_ATTEMPTS for j in jobs.values()):
        return "queue not empty; nothing to plan"
    plans = [e["payload"] for e in log.events if e["type"] == "Plan"]
    stage = max((p["stage"] for p in plans), default=0)
    if stage == 0:
        specs = [make_spec(1.0, L, T, s) for L in SIZES for T in COARSE for s in range(3)]
        specs += [make_spec(1.0, 8, T, s, "ref") for T in (1.8, 2.3, 3.0) for s in range(3)]
        reason = ("stage 1: no data yet -> space-filling coarse sweep T=1.8..3.0 (step 0.1), "
                  "3 sizes x 3 seeds, plus reference-engine jobs (L=8) for the R2 check")
        temps = []
    else:
        est = analyze_results(load_results(log, "fast", 1.0), B=60)
        last = [p for p in plans if p["stage"] == stage][-1]
        if stage == 1:
            tp = est["tp"]
            temps = [round(float(t), 4) for t in tp + np.linspace(-0.2, 0.2, 11)]
            specs = [make_spec(1.0, L, T, s) for L in SIZES for T in temps for s in range(3)]
            reason = (f"stage 2: susceptibility-like peak sits near T={tp:.3f} on the coarse grid "
                      f"-> refine +-0.2 around it (11 points) to resolve the peak and the crossings")
        else:
            lo, hi = est["ci"]["g_nu"]
            nseed = last["seed_max"] + 1
            if lo is None or (hi - lo) <= 0.35 or nseed >= 9:
                return (f"converged enough for V0 (CI width of exponent = "
                        f"{'n/a' if lo is None else round(hi - lo, 3)}, seeds={nseed}); nothing to add")
            temps = last["temps"]
            specs = [make_spec(1.0, L, T, s) for L in SIZES for T in temps for s in range(nseed, nseed + 3)]
            reason = (f"stage {stage + 1}: exponent CI width {hi - lo:.2f} > 0.35 -> 3 more seeds "
                      f"at the refined temperatures to cut statistical error")
    stage_new = stage + 1
    n = enqueue(log, specs)
    seed_max = max(s["seed"] for s in specs)
    log.append("Plan", {"stage": stage_new, "reason": reason, "n_jobs": n, "temps": temps,
                         "seed_max": seed_max})
    return f"{reason} [{n} jobs enqueued]"


# ---------------------------------------------------------------- report
def fmt(x, nd=3):
    return "n/a" if x is None else f"{x:.{nd}f}"


def write_report(log):
    jobs = project(log.events)
    states = {}
    for j in jobs.values():
        states[j["state"]] = states.get(j["state"], 0) + 1
    ok, n = verify_chain(log.events)
    lines = [f"# รายงาน Discovery Engine V0",
             "",
             f"สร้างเมื่อ {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC · code `{code_hash()}`",
             "",
             "ผลทั้งหมดนี้เป็นผลของ simulation ภายใต้ model และ assumption ด้านล่าง ไม่ใช่กฎของธรรมชาติ",
             "",
             "## สถานะ",
             f"- events {n} รายการ · hash chain: {'ผ่าน' if ok else 'ไม่ผ่าน'}",
             f"- jobs: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())),
             ""]
    results = load_results(log, "fast", 1.0)
    if len({r["spec"]["L"] for r in results}) < 2:
        lines += ["ยังมีข้อมูลไม่พอสำหรับวิเคราะห์ (ต้องรัน plan และ work ก่อน)"]
    else:
        est = analyze_results(results)
        r2ok, rows = r2_check(log)
        ci = est["ci"]
        lines += ["## ผลการประมาณ (substrate: Ising-type บนตาราง 2 มิติ, ขนาด L = "
                  + ", ".join(str(L) for L in sorted(est["peaks"])) + ")",
                  "",
                  "| ปริมาณ | ค่าประมาณ | 95% CI (bootstrap) |",
                  "|---|---|---|",
                  f"| T ที่ susceptibility สูงสุด (เฉลี่ยทุกขนาด) | {fmt(est['tp'])} | {fmt(ci['tp'][0])} ถึง {fmt(ci['tp'][1])} |",
                  f"| T ที่เส้น Binder ตัดกัน | {fmt(est['tc'])} | {fmt(ci['tc'][0])} ถึง {fmt(ci['tc'][1])} |",
                  f"| เลขชี้กำลังของยอด susceptibility เทียบ L | {fmt(est['g_nu'])} | {fmt(ci['g_nu'][0])} ถึง {fmt(ci['g_nu'][1])} |",
                  f"| เลขชี้กำลังของ order statistic ที่จุดตัด | {fmt(est['b_nu'])} | {fmt(ci['b_nu'][0])} ถึง {fmt(ci['b_nu'][1])} |",
                  "",
                  "## คำตัดสิน"]
        if est["detected"]:
            level = "L2 (ชั่วคราว)" if r2ok else "L1"
            lines += [f"พบ candidate ของ phase transition ระดับ **{level}**: ยอด susceptibility สูงขึ้นตามขนาดระบบ "
                      "(CI ล่างของเลขชี้กำลัง > 0.5) และเส้น Binder ตัดกัน",
                      "",
                      "L2 ต้องผ่าน R2 และมี ≥ 3 ขนาด; R2 ที่นี่เขียนโดยผู้เขียนเดียวกัน ไม่ใช่ clean-room (R3) "
                      "จึงเป็น L2 แบบชั่วคราว ยังไม่มีสิทธิ์ขึ้น L3"]
        else:
            lines += ["ยังไม่พบสัญญาณที่ผ่านเกณฑ์ (ไม่ถือว่าพิสูจน์ว่าไม่มี)"]
        lines += ["", "## R2: reference engine เทียบ fast engine (L=8)",
                  f"ผล: {'ผ่าน' if r2ok else 'ไม่ผ่านหรือยังไม่มีข้อมูล'} (เกณฑ์ |z| < 5)", "",
                  "| L | T | observable | ref | fast | z |", "|---|---|---|---|---|---|"]
        lines += [f"| {L} | {T} | {k} | {a:.4f} | {b:.4f} | {z:.2f} |" for L, T, k, a, b, z in rows]
        lines += ["", "## Assumption ledger",
                  "- กฎ: Metropolis บน Ising 2 มิติ, J=1, boundary แบบ periodic, เริ่มจากสถานะเรียงตัวทั้งหมด",
                  "- ขนาด L = 8, 16, 32 เท่านั้น (finite-size effects ยังมีผล)",
                  "- ตารางอุณหภูมิถูกเลือกโดย selector ตามเหตุผลในหัวข้อถัดไป",
                  "- analysis ไม่ได้รับค่าที่รู้ล่วงหน้าใด ๆ; ใช้ moment ทั่วไปของสถานะ (ค่าเฉลี่ย |m|, m², m⁴)",
                  "", "## เทียบความรู้เดิม (ทำหลังวิเคราะห์ ระบบไม่ได้ถูกบอกค่าเหล่านี้)",
                  f"- Onsager exact: Tc = {ONSAGER_TC:.4f}, γ/ν = 1.75, β/ν = 0.125",
                  f"- ความต่างจากค่าประมาณ: Tc {fmt(None if est['tc'] is None else est['tc'] - ONSAGER_TC)}, "
                  f"γ/ν {fmt(est['g_nu'] - 1.75)}, β/ν {fmt(None if est['b_nu'] is None else est['b_nu'] - 0.125)}"]
    plans = [e["payload"] for e in log.events if e["type"] == "Plan"]
    if plans:
        lines += ["", "## ประวัติการเลือกการทดลอง"]
        lines += [f"- {p['reason']} ({p['n_jobs']} jobs)" for p in plans]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return REPORT


# -------------------------------------------------------------- selftest
def selftest(quick):
    parallel = os.cpu_count() or 1
    tmp = tempfile.mkdtemp(prefix="de-selftest-")
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

    # 1. hash chain detects tampering
    lg = Log(os.path.join(tmp, "chain"))
    for i in range(5):
        lg.append("Note", {"i": i})
    ok1, _ = verify_chain(lg.events)
    lg.events[2]["payload"]["i"] = 99
    ok2, at = verify_chain(lg.events)
    check("hash chain: valid log passes, tampered log fails", ok1 and not ok2, f"(tamper caught at seq {at})")

    # 2. determinism
    spec = make_spec(1.0, 8, 2.3, 0)
    spec.update(burn=50, sweeps=400)
    check("determinism: same spec -> identical result", canon(run_spec(spec)) == canon(run_spec(spec)))

    # 3. crash / resume
    cdir = os.path.join(tmp, "crash")
    lg = Log(cdir)
    specs = []
    for T in COARSE:
        for s in range(2):
            sp = make_spec(1.0, 8, T, s)
            sp.update(burn=100, sweeps=1500)
            specs.append(sp)
    enqueue(lg, specs)
    env = dict(os.environ, DE_STATE=cdir)
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "work", "--parallel", "1",
                             "--budget-seconds", "600"], env=env, stdout=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 60:
        time.sleep(0.2)
        with open(os.path.join(cdir, "events.jsonl"), "rb") as f:
            if f.read().count(b'"JobCompleted"') >= 3:
                break
    proc.kill()  # SIGKILL: no cleanup of any kind
    proc.wait()
    lg = Log(cdir)
    before = sum(1 for j in project(lg.events).values() if j["state"] == "done")
    work(lg, 600, parallel, reclaim=True, owner="resumed")
    lg = Log(cdir)
    jobs = project(lg.events)
    completes = [e["payload"]["job_id"] for e in lg.events if e["type"] == "JobCompleted"]
    ok_chain, _ = verify_chain(lg.events)
    check("crash/resume: kill -9 mid-run, rerun finishes every job exactly once",
          all(j["state"] == "done" for j in jobs.values()) and len(completes) == len(set(completes)) == len(specs)
          and ok_chain and not verify_artifacts(lg),
          f"(done before kill: {before}/{len(specs)})")

    # 4. second engine agreement (R2)
    rdir = Log(os.path.join(tmp, "r2"))
    r2specs = []
    for T in (1.8, 2.3, 3.0):
        for s in range(3):
            for eng in ("fast", "ref"):
                sp = make_spec(1.0, 8, T, s, eng)
                if quick:
                    sp.update(burn=100, sweeps=2000)
                r2specs.append(sp)
    enqueue(rdir, r2specs)
    work(rdir, 3600, parallel, reclaim=False)
    ok, rows = r2_check(rdir)
    check("R2: reference engine and fast engine agree (|z| < 5)", ok,
          f"(max |z| = {max(r[-1] for r in rows):.2f} over {len(rows)} comparisons)")

    # 5. null battery: no transition exists, pipeline must not claim one
    R = 8 if quick else 20
    fp = 0
    for r in range(R):
        sp = [dict(make_spec(1.0, L, T, r, "null_iid"), burn=0, sweeps=2000) for L in SIZES for T in COARSE]
        fp += analyze_results(run_many(sp, 1), B=60, seed=r)["detected"]
    check("null battery: false-positive rate <= 10%", fp / R <= 0.10, f"({fp}/{R} false detections)")

    # 6. planted law: same physics, coupling J=1.5 -> transition must move to 1.5 x Onsager
    grid = [round(2.0 + 0.05 * k, 4) for k in range(13)]
    sweepscale = 0.5 if quick else 1.0

    def sweep_specs(J, seeds):
        out = []
        for L in SIZES:
            for T in grid:
                for s in range(seeds):
                    sp = make_spec(J, L, round(J * T, 4), s)
                    sp["sweeps"] = int(sp["sweeps"] * sweepscale)
                    out.append(sp)
        return out

    est = analyze_results(run_many(sweep_specs(1.5, 2), parallel), B=100)
    target = 1.5 * ONSAGER_TC
    check("planted law (J=1.5): detected, transition found at 1.5 x known value",
          est["detected"] and est["tc"] is not None and abs(est["tc"] - target) < 0.12,
          f"(found {fmt(est['tc'])}, expected {target:.3f})")

    # 7. known answer: rediscover Ising numbers without being told them
    est = analyze_results(run_many(sweep_specs(1.0, 3), parallel), B=100)
    ok = (est["detected"] and abs(est["tc"] - ONSAGER_TC) < 0.06 and abs(est["g_nu"] - 1.75) < 0.3
          and est["b_nu"] is not None and abs(est["b_nu"] - 0.125) < 0.07)
    check("known answer: Tc, gamma/nu, beta/nu close to exact values", ok,
          f"(Tc {fmt(est['tc'])} vs {ONSAGER_TC:.3f}; g/nu {fmt(est['g_nu'])} vs 1.75; b/nu {fmt(est['b_nu'])} vs 0.125)")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return all(results)


# ------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Discovery Engine V0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    w = sub.add_parser("work")
    w.add_argument("--budget-seconds", type=float, default=600)
    w.add_argument("--parallel", type=int, default=os.cpu_count() or 1)
    w.add_argument("--reclaim", action="store_true",
                   help="treat unfinished leases as dead (use when only one worker can exist)")
    for c in ("analyze", "verify", "status"):
        sub.add_parser(c)
    s = sub.add_parser("selftest")
    s.add_argument("--quick", action="store_true")
    a = ap.parse_args()

    if a.cmd == "selftest":
        sys.exit(0 if selftest(a.quick) else 1)
    log = Log()
    if a.cmd == "plan":
        print(plan(log))
    elif a.cmd == "work":
        print(f"completed {work(log, a.budget_seconds, a.parallel, a.reclaim)} jobs")
    elif a.cmd == "analyze":
        print("wrote", write_report(log))
    elif a.cmd == "status":
        st = {}
        for j in project(log.events).values():
            st[j["state"]] = st.get(j["state"], 0) + 1
        print(st or "empty")
    elif a.cmd == "verify":
        ok, n = verify_chain(log.events)
        bad = verify_artifacts(log)
        print(f"chain: {'OK' if ok else 'BROKEN at seq ' + str(n)} ({len(log.events)} events); "
              f"artifacts: {'OK' if not bad else bad}")
        sys.exit(0 if ok and not bad else 1)


if __name__ == "__main__":
    main()
