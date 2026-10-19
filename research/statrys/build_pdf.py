"""Render site/ (print view) to Statrys-Fikir-Defteri.pdf.

Two passes: the first PDF's outline (one bookmark per video heading) gives the page
numbers, which are written into the table of contents before the second, final pass.
Needs: node + the playwright package (global install is fine), Chromium for it, pypdf.
"""
import datetime, functools, http.server, json, os, pathlib, re, subprocess, sys, threading

ROOT = pathlib.Path(__file__).resolve().parent
SITE, OUT = ROOT / "site", ROOT / "Statrys-Fikir-Defteri.pdf"
WRAP = SITE / ".print.html"  # local stand-in for the artifact's page skeleton; gitignored
SKELETON = ('<!doctype html><html lang="tr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>body{margin:0;font:14px system-ui}img{max-width:100%}[hidden]{display:none!important}</style>'
            '</head><body>{page}</body></html>')


def playwright_module():
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    for cand in [pathlib.Path(root) / "playwright", ROOT / "node_modules" / "playwright"]:
        if (cand / "package.json").exists():
            return str(cand)
    sys.exit("playwright npm package not found; run: npm i -g playwright@1.56.1 (browsers: PLAYWRIGHT_BROWSERS_PATH)")


def serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def render(url, out, pw, toc=None):
    cmd = ["node", str(ROOT / "pdf.mjs"), url, str(out)] + ([str(toc)] if toc else [])
    subprocess.run(cmd, check=True, env={**os.environ, "PW_MODULE": pw})


def squash(s):
    # Chromium drops the space at a line wrap inside a bookmark title, so compare without whitespace
    return re.sub(r"\s+", "", str(s))


def outline_pages(pdf, title_to_id):
    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    pages = {}

    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
                continue
            vid = title_to_id.get(squash(it.title))
            if vid and vid not in pages:
                pages[vid] = reader.get_destination_page_number(it) + 1
    walk(reader.outline)
    return pages, len(reader.pages)


def tidy_outline_titles(writer):
    """Chromium occasionally emits a bookmark title twice over ("AbcAbc"); collapse those."""
    from pypdf.generic import NameObject, TextStringObject
    node = writer._root_object.get("/Outlines")
    node = node.get_object().get("/First") if node else None
    stack = [node]
    while stack:
        item = stack.pop()
        while item:
            item = item.get_object()
            t = str(item.get("/Title", ""))
            if len(t) % 2 == 0 and t[: len(t) // 2] == t[len(t) // 2:] and t:
                item[NameObject("/Title")] = TextStringObject(t[: len(t) // 2])
            if item.get("/First"):
                stack.append(item["/First"])
            item = item.get("/Next")


def main():
    data = json.loads((SITE / "data.json").read_text())
    title_to_id = {squash(v.get("title_tr") or v["title"]): v["id"] for v in data["videos"]}
    title_to_id[squash("Tüm iş fikirleri (sermaye ihtiyacına göre)")] = "fikirler"
    WRAP.write_text(SKELETON.replace("{page}", (SITE / "index.html").read_text()))
    pw = playwright_module()
    srv = serve(SITE)
    url = f"http://127.0.0.1:{srv.server_port}/{WRAP.name}#print"
    tmp_pdf, toc_json = ROOT / ".pass1.pdf", ROOT / ".toc_pages.json"
    try:
        render(url, tmp_pdf, pw)
        pages, n = outline_pages(tmp_pdf, title_to_id)
        toc_json.write_text(json.dumps(pages))
        render(url, tmp_pdf, pw, toc_json)
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter(clone_from=str(tmp_pdf))
        tidy_outline_titles(writer)
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        writer.add_metadata({"/Title": "Statrys Fikir Defteri", "/Author": "Claude (OpenOSINT research)",
                             "/Subject": "@Statrys videolarından Türkçe özetler, grafikler, şemalar ve iş fikirleri",
                             "/CreationDate": datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")})
        writer.write(str(OUT))
        def count(items):
            return sum(count(it) if isinstance(it, list) else 1 for it in items)
        kept = count(PdfReader(str(OUT)).outline)
        missing = [v["id"] for v in data["videos"] if v["id"] not in pages]
        print(json.dumps({"pdf": str(OUT.relative_to(ROOT)), "pages": n, "bookmarks": kept,
                          "toc_without_page": missing, "bytes": OUT.stat().st_size}, ensure_ascii=False))
    finally:
        srv.shutdown()
        for p in (WRAP, tmp_pdf, toc_json):
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
