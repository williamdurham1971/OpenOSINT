import sys, time
sys.path.insert(0, ".")
import data
for sym in sys.argv[1:]:
    t = time.time()
    try:
        rows = data.fetch("4h", symbol=sym, verbose=False)
    except Exception as e:
        print(sym, "FAILED", e, flush=True); continue
    if rows:
        g = data.gaps(rows, "4h")
        print(f"{sym}: {len(rows)} 4h bars {time.strftime('%Y-%m-%d', time.gmtime(rows[0][0]/1000))}..{time.strftime('%Y-%m-%d', time.gmtime(rows[-1][0]/1000))}, {len(g)} gaps ({sum(x[1] for x in g)} bars), {time.time()-t:.0f}s", flush=True)
    else:
        print(sym, "no data", flush=True)
