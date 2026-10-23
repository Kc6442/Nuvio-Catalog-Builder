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
    kind = config.get("type", "movie")
    if kind not in ("movie", "series"):
        raise SystemExit("type must be movie or series")
    entries = config.get("shows" if kind == "series" else "movies", [])
    if not entries:
        raise SystemExit("Catalog entries cannot be empty")
    metas = []
    for title,year in entries:
        tmdb_kind = "tv" if kind == "series" else "movie"
        date_key = "first_air_date" if kind == "series" else "release_date"
        title_key = "name" if kind == "series" else "title"
        results = tmdb("search/"+tmdb_kind,{"query":title,"first_air_date_year" if kind == "series" else "year":year}).get("results",[])
        matches = [m for m in results if norm(m.get(title_key,"")) == norm(title) and (m.get(date_key) or "").startswith(str(year))]
        if not matches and kind == "series":
            aliases = {"VisionQuest": ["Vision Quest", "Marvel Studios' VisionQuest", "Vision"]}
            for alt in aliases.get(title, []):
                more = tmdb("search/tv",{"query":alt}).get("results",[])
                matches = [m for m in more if norm(m.get("name","")) == norm(alt) and (m.get("first_air_date") or "").startswith(str(year))]
                if matches: break
        if not matches:
            raise SystemExit(f"No exact TMDB match: {title} ({year})")
        m = matches[0]
        external = tmdb(f"{tmdb_kind}/{m['id']}/external_ids")
        imdb = external.get("imdb_id")
        if not m.get("poster_path"):
            raise SystemExit(f"Missing poster: {title} ({year})")
        identifier = imdb or ("tmdb:"+str(m["id"]) if kind == "series" else None)
        if not identifier:
            raise SystemExit(f"Missing ID: {title} ({year})")
        metas.append({"id":identifier,"type":kind,"name":title,"poster":"https://image.tmdb.org/t/p/w500"+m["poster_path"],"description":m.get("overview",""),"releaseInfo":str(year)})
    if len({m["id"] for m in metas}) != len(metas):
        raise SystemExit("Duplicate movies found")
    base = root / "published" / slug
    target = base / "catalog" / kind / (slug+".json")
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({"metas":metas},ensure_ascii=False,indent=2)+"\n")
    manifest = {"id":"com.kc6442."+slug.replace("-","."),"version":"0.1.0","name":config["name"],"description":config["description"],"resources":["catalog"],"types":[kind],"catalogs":[{"type":kind,"id":slug,"name":config["name"]}]}
    (base/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(f"Built {len(metas)} {kind} entries: {target}")
    
for config_path in configs:
    build(json.loads(config_path.read_text()))
