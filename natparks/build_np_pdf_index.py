#!/usr/bin/env python3
"""
build_np_pdf_index.py

Reads a CSV (park,state,pdf_url), downloads the PDFs into a common directory,
and builds a JSON file that a companion static web app can load for client-side search.

Default layout created by this script:
  web/
    index.html                (optional; use --write-index to generate)
    np_trailmaps.json         (data index consumed by index.html)
    pdfs/                     (all PDFs stored here)

Usage:
  python3 build_np_pdf_index.py --csv national_parks_trailmaps.csv --out web --write-index
  python3 build_np_pdf_index.py --csv national_parks_trailmaps.csv --out /var/www/np --workers 6
  python3 build_np_pdf_index.py --csv national_parks_trailmaps.csv --out web --force

Dependencies:
  pip install requests
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Any
from urllib.parse import urlparse

import requests

LOCK = threading.Lock()

def slugify(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"[^\w\s\-]+", "", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+", "-", value.strip())
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")[:80] or "file"

def derive_title_from_url(url: str) -> str:
    name = os.path.basename(urlparse(url).path) or "Map"
    name = re.sub(r"\.pdf($|\?.*)", "", name, flags=re.I)
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 80:
        name = re.sub(r"[ _-]?(20\d{2}|19\d{2}|v\d+)$", "", name).strip()
    def smart_title(s: str) -> str:
        words = s.split()
        out = []
        for w in words:
            if w.isupper() and len(w) <= 4:
                out.append(w)
            else:
                out.append(w.capitalize())
        return " ".join(out)
    return smart_title(name) or "Map"

def secure_filename(park: str, url: str, existing: set) -> str:
    park_slug = slugify(park)
    base = os.path.basename(urlparse(url).path) or "map.pdf"
    base_slug = slugify(re.sub(r"\.pdf($|\?.*)", "", base, flags=re.I)) or "map"
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    guess = f"{park_slug}__{base_slug}__{h}.pdf"
    if guess in existing:
        i = 1
        while True:
            alt = f"{park_slug}__{base_slug}__{h}-{i}.pdf"
            if alt not in existing:
                return alt
            i += 1
    return guess

def read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    rows = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        expected = {"park", "state", "pdf_url"}
        if not expected.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"CSV must have columns: park,state,pdf_url (found {reader.fieldnames})")
        for r in reader:
            url = (r.get("pdf_url") or "").strip()
            park = (r.get("park") or "").strip()
            state = (r.get("state") or "").strip()
            if not url or not park:
                continue
            rows.append({"park": park, "state": state, "pdf_url": url})
    return rows

def split_states(state_field: str) -> List[str]:
    parts = []
    for token in re.split(r"[;,]", state_field or ""):
        token = token.strip()
        if token:
            parts.append(token)
    return parts or []

def download_one(session: requests.Session, url: str, dest: Path, timeout: int, verify_tls: bool=True) -> Tuple[bool, int, str]:
    try:
        with session.get(url, stream=True, timeout=timeout, allow_redirects=True, verify=verify_tls) as r:
            r.raise_for_status()
            hasher = hashlib.sha256()
            tmp = dest.with_suffix(dest.suffix + ".part")
            size = 0
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
            tmp.replace(dest)
            return True, size, hasher.hexdigest()
    except Exception:
        return False, 0, ""

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def build_index(rows: List[Dict[str, str]], pdf_dir: Path, force: bool, workers: int, timeout: int, verify_tls: bool=True) -> Dict[str, Any]:
    ensure_dir(pdf_dir)
    session = requests.Session()
    session.headers.update({"User-Agent": "prepper-disk-fetch/1.0 (+offline archival)"} )

    existing_names = set(os.listdir(pdf_dir)) if pdf_dir.exists() else set()
    items: List[Dict[str, Any]] = []
    seen_keys = set()

    def process_row(r: Dict[str, str]):
        park = r["park"]
        states = split_states(r["state"])
        url = r["pdf_url"]
        key = (park, url)
        if key in seen_keys:
            return
        seen_keys.add(key)

        title = derive_title_from_url(url)
        filename = secure_filename(park, url, existing_names)
        dest = pdf_dir / filename

        if dest.exists() and not force:
            size = dest.stat().st_size
            sha256_hex = ""
            item = {
                "id": hashlib.sha1(f"{park}|{url}".encode("utf-8")).hexdigest()[:12],
                "park": park,
                "states": states,
                "title": title,
                "filename": filename,
                "path": str(Path(pdf_dir.name) / filename),
                "size_bytes": int(size),
                "sha256": sha256_hex,
                "source_url": url,
                "download_ok": True
            }
            with LOCK:
                items.append(item)
            return

        ok, size, sha256_hex = download_one(session, url, dest, timeout, verify_tls=verify_tls)
        item = {
            "id": hashlib.sha1(f"{park}|{url}".encode("utf-8")).hexdigest()[:12],
            "park": park,
            "states": states,
            "title": title,
            "filename": filename,
            "path": str(Path(pdf_dir.name) / filename),
            "size_bytes": int(size),
            "sha256": sha256_hex,
            "source_url": url,
            "download_ok": bool(ok)
        }
        with LOCK:
            items.append(item)

    if workers <= 1:
        for r in rows:
            process_row(r)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for r in rows:
                ex.submit(process_row, r)

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": "",
        "total_items": len(items),
        "items": items
    }
    return index

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>U.S. National Parks — Trail Maps</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --muted: #9aa4b2;
      --text: #e5e7eb;
      --accent: #22c55e;
      --accent2: #3b82f6;
      --ring: rgba(59,130,246,.25);
      --card: #0b1220;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--text);
      font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
    }
    header {
      position: sticky; top: 0; z-index: 10;
      backdrop-filter: blur(8px);
      background: linear-gradient(180deg, rgba(17,24,39,.9), rgba(17,24,39,.6));
      border-bottom: 1px solid rgba(148,163,184,.15);
    }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 16px; }
    h1 { margin: 0 0 8px; font-size: 22px; letter-spacing: .4px; }
    .controls { display: grid; grid-template-columns: 1fr 180px 150px; gap: 10px; }
    input[type="search"] {
      width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid rgba(148,163,184,.2);
      background: #0b1120; color: var(--text); outline: none;
    }
    input[type="search"]:focus { border-color: var(--accent2); box-shadow: 0 0 0 3px var(--ring); }
    select, button {
      padding: 10px 12px; border-radius: 10px; border: 1px solid rgba(148,163,184,.2);
      background: #0b1120; color: var(--text);
    }
    button { cursor: pointer; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; padding: 18px 16px 40px; }
    .card {
      background: var(--card); border: 1px solid rgba(148,163,184,.12);
      border-radius: 16px; padding: 14px; box-shadow: 0 6px 20px rgba(0,0,0,.25);
      display: flex; flex-direction: column; gap: 8px;
    }
    .park { font-weight: 700; font-size: 16px; }
    .muted { color: var(--muted); font-size: 12px; }
    .chips { display: flex; gap: 6px; flex-wrap: wrap; }
    .chip {
      border: 1px solid rgba(148,163,184,.25); border-radius: 999px; padding: 2px 8px; font-size: 12px; color: var(--muted);
    }
    .actions { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
    .btn {
      padding: 8px 10px; border-radius: 10px; border: 1px solid rgba(148,163,184,.2);
      background: #0b1120; color: var(--text); text-decoration: none; text-align: center;
    }
    .btn:hover { border-color: var(--accent); }
    .count { color: var(--muted); font-size: 13px; }
    .footer { text-align: center; color: var(--muted); font-size: 12px; padding: 20px 0 30px; }
    @media (max-width: 720px) {
      .controls { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>U.S. National Parks — Trail & Wilderness Maps</h1>
      <div class="controls">
        <input id="q" type="search" placeholder="Search parks, states, titles…" />
        <select id="state">
          <option value="">All states</option>
        </select>
        <select id="sort">
          <option value="park-asc">Sort: Park (A→Z)</option>
          <option value="park-desc">Sort: Park (Z→A)</option>
          <option value="size-desc">Sort: Size (big→small)</option>
          <option value="size-asc">Sort: Size (small→big)</option>
        </select>
      </div>
      <div class="wrap" style="padding:8px 0 0 0;">
        <span class="count" id="count"></span>
      </div>
    </div>
  </header>

  <main class="wrap">
    <section id="results" class="grid"></section>
    <div class="footer">Works fully offline. Data from <code>np_trailmaps.json</code>.</div>
  </main>

<script>
const STATE_ORDER = ["AK","AL","AR","AS","AZ","CA","CO","CT","DC","DE","FL","GA","GU","HI","IA","ID","IL","IN","KS","KY","LA","MA","MD","ME","MI","MN","MO","MP","MS","MT","NC","ND","NE","NH","NJ","NM","NV","NY","OH","OK","OR","PA","PR","RI","SC","SD","TN","TX","UM","UT","VA","VI","VT","WA","WI","WV","WY"];

const DATA_URL = "np_trailmaps.json"; // same folder as this HTML

let DATA = [];
let FILTERED = [];

function norm(s){ return (s||"").toString().toLowerCase(); }

function populateStateFilter(items){
  const select = document.getElementById("state");
  const set = new Set();
  items.forEach(it => (it.states||[]).forEach(s => set.add(s)));
  const list = Array.from(set).sort((a,b)=>STATE_ORDER.indexOf(a)-STATE_ORDER.indexOf(b));
  list.forEach(code => {
    const opt = document.createElement("option");
    opt.value = code; opt.textContent = code;
    select.appendChild(opt);
  });
}

function card(item){
  const div = document.createElement("div");
  div.className = "card";
  const park = document.createElement("div");
  park.className = "park";
  park.textContent = item.park;
  const chips = document.createElement("div");
  chips.className = "chips";
  (item.states||[]).forEach(s=>{
    const c = document.createElement("span");
    c.className = "chip"; c.textContent = s; chips.appendChild(c);
  });
  const title = document.createElement("div");
  title.className = "muted";
  title.textContent = item.title || item.filename;
  const actions = document.createElement("div");
  actions.className = "actions";
  const a = document.createElement("a");
  a.href = item.path; a.className = "btn"; a.textContent = "Open PDF";
  a.setAttribute("download", "");
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.textContent = (item.size_bytes? (Math.round(item.size_bytes/1024/1024*10)/10 + " MB") : ""); 
  actions.appendChild(a);
  actions.appendChild(meta);
  div.appendChild(park);
  div.appendChild(chips);
  div.appendChild(title);
  div.appendChild(actions);
  return div;
}

function render(list){
  const root = document.getElementById("results");
  root.innerHTML = "";
  list.forEach(it => root.appendChild(card(it)));
  const count = document.getElementById("count");
  count.textContent = `${list.length} map${list.length===1?"":"s"} shown (of ${DATA.length})`;
}

function applyFilters(){
  const q = norm(document.getElementById("q").value);
  const state = document.getElementById("state").value;
  const sort = document.getElementById("sort").value;

  FILTERED = DATA.filter(it => {
    const txt = `${it.park} ${it.states.join(" ")} ${it.title} ${it.filename}`.toLowerCase();
    const passQ = q ? txt.includes(q) : true;
    const passState = state ? it.states.includes(state) : true;
    return passQ && passState;
  });

  const sorters = {
    "park-asc": (a,b)=> a.park.localeCompare(b.park),
    "park-desc": (a,b)=> b.park.localeCompare(a.park),
    "size-desc": (a,b)=> (b.size_bytes||0) - (a.size_bytes||0),
    "size-asc": (a,b)=> (a.size_bytes||0) - (b.size_bytes||0),
  };
  FILTERED.sort(sorters[sort] || sorters["park-asc"]);
  render(FILTERED);
}

async function boot(){
  try{
    const resp = await fetch(DATA_URL);
    DATA = (await resp.json()).items || [];
  }catch(e){
    console.error("Failed to load JSON", e);
    DATA = [];
  }
  populateStateFilter(DATA);
  applyFilters();
  document.getElementById("q").addEventListener("input", applyFilters);
  document.getElementById("state").addEventListener("change", applyFilters);
  document.getElementById("sort").addEventListener("change", applyFilters);
}
boot();
</script>
</body>
</html>
"""

def write_index_html(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Download PDFs and build JSON for a static web viewer.")
    ap.add_argument("--csv", required=True, help="Input CSV with columns: park,state,pdf_url")
    ap.add_argument("--out", default="web", help="Output directory for site (default: web)")
    ap.add_argument("--pdf-dirname", default="pdfs", help="Subdirectory under --out to store PDFs (default: pdfs)")
    ap.add_argument("--json-name", default="np_trailmaps.json", help="JSON filename (default: np_trailmaps.json)")
    ap.add_argument("--workers", type=int, default=4, help="Parallel downloads (default: 4)")
    ap.add_argument("--timeout", type=int, default=60, help="Per-request timeout seconds (default: 60)")
    ap.add_argument("--force", action="store_true", help="Re-download even if the file exists")
    ap.add_argument("--write-index", action="store_true", help="Also write index.html into the output directory")
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification (not recommended)")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    out_root = Path(args.out)
    pdf_dir = out_root / args.pdf_dirname
    json_path = out_root / args.json_name

    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(2)

    rows = read_csv_rows(csv_path)
    index = build_index(
        rows, pdf_dir, args.force, args.workers, args.timeout,
        verify_tls=(not args.insecure)
    )
    index["source_csv"] = str(csv_path)

    out_root.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    if args.write_index:
        write_index_html(out_root)

    print(f"[OK] Downloaded {index['total_items']} PDF entries")
    print(f"[OK] Wrote JSON: {json_path}")
    print(f"[OK] PDFs in: {pdf_dir}")
    if args.write_index:
        print(f"[OK] Wrote web app: {out_root/'index.html'}")

if __name__ == "__main__":
    main()
