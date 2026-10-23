#!/usr/bin/env python3
"""Build a Nuvio-compatible metadata catalog from TMDB."""
import json, os, pathlib, time, urllib.parse, urllib.request
root = pathlib.Path(__file__).resolve().parent
configs = sorted((root / "catalogs").glob("*.json"))
if not configs:
    raise SystemExit("No catalog definitions found")
token = os.environ.get("TMDB_READ_TOKEN")
if not token:
    raise SystemExit("Missing TMDB_READ_TOKEN GitHub Actions secret")
def tmdb(endpoint, params=None):
    url = "https://api.themoviedb.org/3/" + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"Authorization":"Bearer "+token,"accept":"application/json"})
            with urllib.request.urlopen(req,timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == 3: raise
            time.sleep(2**attempt)
def norm(s):
    return "".join(c for c in s.casefold() if c.isalnum())
def build(config):
    slug = config["catalog_id"]
    if not slug or not all(c.islower() or c.isdigit() or c == "-" for c in slug):
        raise SystemExit("catalog_id must contain only lowercase letters, digits and hyphens")
    movies = config["movies"]
    if not movies:
        raise SystemExit("Movie list cannot be empty")
    metas = []
    for title,year in movies:
        results = tmdb("search/movie",{"query":title,"year":year}).get("results",[])
        matches = [m for m in results if norm(m.get("title","")) == norm(title) and (m.get("release_date") or "").startswith(str(year))]
        if not matches:
            raise SystemExit(f"No exact TMDB match: {title} ({year})")
        m = matches[0]
        imdb = tmdb(f"movie/{m['id']}/external_ids").get("imdb_id")
        if not imdb or not m.get("poster_path"):
            raise SystemExit(f"Missing IMDb ID or poster: {title} ({year})")
        metas.append({"id":imdb,"type":"movie","name":title,"poster":"https://image.tmdb.org/t/p/w500"+m["poster_path"],"description":m.get("overview",""),"releaseInfo":str(year)})
    if len({m["id"] for m in metas}) != len(metas):
        raise SystemExit("Duplicate movies found")
    base = root / "published" / slug
    target = base / "catalog" / "movie" / (slug+".json")
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({"metas":metas},ensure_ascii=False,indent=2)+"\n")
    manifest = {"id":"com.kc6442."+slug.replace("-","."),"version":"0.1.0","name":config["name"],"description":config["description"],"resources":["catalog"],"types":["movie"],"catalogs":[{"type":"movie","id":slug,"name":config["name"]}]}
    (base/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(f"Built {len(metas)} movies: {target}")
    
for config_path in configs:
    build(json.loads(config_path.read_text()))
