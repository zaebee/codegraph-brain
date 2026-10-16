"""Walk the seeded pool; the first candidate passing every pre-registered check is chosen."""
import json, os, re, shutil, subprocess, sys, time
W = os.path.dirname(os.path.abspath(__file__))
C = "/home/zaebee/bench-corpora/exp4-candidates"
os.makedirs(C, exist_ok=True)
pool = json.load(open(f"{W}/pool.json"))
log = open(f"{W}/walk-log.jsonl", "a")
GEN = re.compile(r"^// Code generated .* DO NOT EDIT\.?$", re.M)

def run(cmd, cwd, timeout=900):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "GOFLAGS": "-mod=mod", "GOOS": "linux", "GOARCH": "amd64"})
        return r.returncode, (r.stdout + r.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"

def go_files(root):
    nested = set()
    for d, dirs, fs in os.walk(root):
        if d != root and "go.mod" in fs:
            nested.add(d); dirs[:] = []; continue
        dirs[:] = [x for x in dirs if x not in ("vendor", "testdata") and not x.startswith(("_", "."))]
        for f in fs:
            if f.endswith(".go") and not f.endswith("_test.go"):
                yield os.path.join(d, f)

def check(name):
    meta = pool["meta"][name]
    rec = {"name": name, "stars": meta["stars"], "at": time.strftime("%FT%TZ", time.gmtime())}
    dest = os.path.join(C, name.replace("/", "__"))
    shutil.rmtree(dest, ignore_errors=True)
    code, out = run(["git", "clone", "-q", "--depth", "1", meta["clone"], dest], C, 1800)
    if code:
        rec["fail"] = f"clone: {out[-300:]}"; return rec, dest
    rec["sha"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True).stdout.strip()
    gomod = os.path.join(dest, "go.mod")
    if not os.path.exists(gomod):
        rec["fail"] = "no root go.mod"; return rec, dest
    mod = re.search(r"^module\s+(\S+)", open(gomod).read(), re.M).group(1)
    rec["module"] = mod
    loc = gen = 0
    for f in go_files(dest):
        text = open(f, errors="replace").read()
        n = text.count("\n")
        loc += n
        if GEN.search(text[:2000]): gen += n
    rec["loc"], rec["generated_frac"] = loc, round(gen / loc, 3) if loc else 0
    if not 15000 <= loc <= 60000:
        rec["fail"] = f"loc {loc}"; return rec, dest
    if loc and gen / loc > 0.30:
        rec["fail"] = f"generated {gen/loc:.2f}"; return rec, dest
    code, out = run(["go", "build", "./..."], dest)
    if code:
        rec["fail"] = f"build: {out[-400:]}"; return rec, dest
    code, out = run([f"{W}/ifc/ifc", dest, mod], dest)
    m = re.search(r"files=(\d+) generic_files=(\d+) ifaces=(\d+) multi_impl=(\d+)", out)
    if code or not m:
        rec["fail"] = f"ifc: {out[-400:]}"; return rec, dest
    files, generic, ifaces, multi = map(int, m.groups())
    rec.update(files=files, generic_files=generic, ifaces=ifaces, multi_impl=multi)
    if files and generic / files > 0.10:
        rec["fail"] = f"generic {generic}/{files}"; return rec, dest
    if multi < 10:
        rec["fail"] = f"multi_impl {multi}"; return rec, dest
    rec["pass"] = True
    return rec, dest

for name in pool["order"]:
    rec, dest = check(name)
    log.write(json.dumps(rec) + "\n"); log.flush()
    print(json.dumps(rec)[:300], flush=True)
    if rec.get("pass"):
        print("CHOSEN", name); break
    shutil.rmtree(dest, ignore_errors=True)
