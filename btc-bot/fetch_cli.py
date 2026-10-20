import sys, time
sys.path.insert(0, ".")
import data
iv = sys.argv[1] if len(sys.argv) > 1 else "60m"
t = time.time()
rows = data.fetch(iv)
g = data.gaps(rows, iv)
print(f"{iv}: {len(rows)} candles {time.strftime('%Y-%m-%d', time.gmtime(rows[0][0]/1000))} .. {time.strftime('%Y-%m-%d %H:%M', time.gmtime(rows[-1][0]/1000))} in {time.time()-t:.0f}s; gaps: {len(g)} ({sum(x[1] for x in g)} bars missing)")
for a, n in g[:8]:
    print("   gap after", time.strftime("%Y-%m-%d %H:%M", time.gmtime(a/1000)), f"({n} bars)")
