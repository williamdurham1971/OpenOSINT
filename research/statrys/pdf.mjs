// Render the print view of the site to a PDF with headless Chromium.
// usage: node pdf.mjs <url> <out.pdf> [toc_pages.json]
// PW_MODULE points at the playwright package directory (build_pdf.py sets it).
import { createRequire } from "module";
import { readFileSync } from "fs";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || "playwright");
const [url, out, tocJson] = process.argv.slice(2);

const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: "light", viewport: { width: 794, height: 1123 } });
const errors = [];
page.on("pageerror", e => errors.push(String(e)));
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForFunction(() => window.__printReady === true, null, { timeout: 90000 });
if (tocJson) await page.evaluate(m => window.setTocPages(m), JSON.parse(readFileSync(tocJson, "utf8")));
await page.emulateMedia({ media: "print" });
await page.waitForTimeout(300);
await page.pdf({
  path: out, format: "A4", printBackground: true, outline: true, tagged: true,
  margin: { top: "14mm", right: "13mm", bottom: "16mm", left: "13mm" },
  displayHeaderFooter: true,
  headerTemplate: "<span></span>",
  footerTemplate: `<div style="width:100%;font:8pt system-ui,sans-serif;color:#6b7b76;padding:0 13mm;display:flex;justify-content:space-between">
    <span>Statrys Fikir Defteri</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
});
await browser.close();
if (errors.length) { console.error("page errors:", errors.join("\n")); process.exit(2); }
