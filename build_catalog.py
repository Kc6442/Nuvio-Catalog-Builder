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
    if kind not in ("movie", "series", "mixed"):
        raise SystemExit("type must be movie, series or mixed")
    if kind == "mixed":
        ordered = config.get("entries", [])
        if not ordered:
            raise SystemExit("Mixed catalog entries cannot be empty")
        by_type = {"movie": [], "series": []}
        for item in ordered:
            media_type, title, year = item
            if media_type not in by_type:
                raise SystemExit("Invalid mixed catalog media type")
            by_type[media_type].append([title, year])
        for media_type, entries_for_type in by_type.items():
            if entries_for_type:
                child = dict(config, type=media_type)
                child["movies" if media_type == "movie" else "shows"] = entries_for_type
                build(child)
        base = root / "published" / slug
        manifest = {"id":"com.kc6442."+slug.replace("-","."),"version":"0.1.0","name":config["name"],"description":config["description"],"resources":["catalog"],"types":[t for t in by_type if by_type[t]],"catalogs":[{"type":t,"id":slug,"name":config["name"]} for t in by_type if by_type[t]]}
        (base/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
        (base/"release_order.json").write_text(json.dumps({"entries":[{"type":t,"name":n,"year":y} for t,n,y in ordered]},indent=2)+"\n")
        return
    entries = config.get("shows" if kind == "series" else "movies", [])
    if not entries:
        raise SystemExit("Catalog entries cannot be empty")
    metas = []
    for title,year in entries:
        tmdb_kind = "tv" if kind == "series" else "movie"
        date_key = "first_air_date" if kind == "series" else "release_date"
        title_key = "name" if kind == "series" else "title"
        results = tmdb("search/"+tmdb_kind,{"query":title,"first_air_date_year" if kind == "series" else "year":year}).get("results",[])
        if not results and kind == "series":
            results = tmdb("search/tv",{"query":title}).get("results",[])
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

# Publish the separately installable single-row mixed test catalog.
# The original two-row addon remains unchanged.
mixed_source = root / "published" / "alien-vs-predator"
if (root / "catalogs" / "alien-vs-predator.json").exists():
    slug = "alien-vs-predator-mixed-test"
    movies = json.loads((mixed_source / "catalog" / "movie" / "alien-vs-predator.json").read_text())["metas"]
    shows = json.loads((mixed_source / "catalog" / "series" / "alien-vs-predator.json").read_text())["metas"]
    names = [
        "Alien", "Aliens", "Alien³", "Alien Resurrection", "Prometheus",
        "Alien: Covenant", "Alien: Romulus",
        "Alien: Isolation – The Digital Series", "Alien: Earth",
        "Predator", "Predator 2", "Predators", "The Predator", "Prey",
        "Predator: Killer of Killers", "Predator: Badlands",
        "AVP: Alien vs. Predator", "Aliens vs. Predator: Requiem",
    ]
    all_metas = {item["name"]: item for item in movies + shows}
    if len(all_metas) != len(names) or set(all_metas) != set(names):
        raise SystemExit("Mixed test catalog titles do not match expected 18 entries")
    mixed_base = root / "published" / slug
    mixed_target = mixed_base / "catalog" / "movie" / (slug + ".json")
    mixed_target.parent.mkdir(parents=True, exist_ok=True)
    mixed_target.write_text(json.dumps({"metas": [all_metas[name] for name in names]}, ensure_ascii=False, indent=2) + "\n")
    mixed_manifest = {
        "id": "com.kc6442.alien.vs.predator.mixed.test",
        "version": "0.1.1",
        "name": "Alien vs Predator MIXED TEST",
        "description": "Experimental single movie catalog row with mixed movie and series items",
        "resources": ["catalog"],
        "types": ["movie"],
        "catalogs": [{"type": "movie", "id": slug, "name": "Alien vs Predator MIXED TEST"}],
    }
    (mixed_base / "manifest.json").write_text(json.dumps(mixed_manifest, ensure_ascii=False, indent=2) + "\n")
    print("Built mixed test catalog with 18 entries")
