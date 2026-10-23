#!/usr/bin/env python3
"""Build a Nuvio-compatible metadata catalog from TMDB."""
import json, os, pathlib, time, urllib.parse, urllib.request, difflib
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
    return "".join(c for c in str(s).casefold() if c.isalnum())

def validate_entries(entries, kind):
    """Give actionable errors instead of unpacking/type errors."""
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise SystemExit(
                f"{kind} entry #{index} must be [title, year]; got {entry!r}"
            )
        title, year = entry
        if not isinstance(title, str) or not title.strip():
            raise SystemExit(f"{kind} entry #{index} has a missing or invalid title")
        if not str(year).isdigit() or len(str(year)) != 4:
            raise SystemExit(f"{kind} entry #{index} ({title!r}) needs a four-digit year")

def validate_mixed_entries(entries):
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise SystemExit(
                f"Mixed entry #{index} must be [movie|series, title, year]; got {entry!r}"
            )
        media_type, title, year = entry
        if media_type not in ("movie", "series"):
            raise SystemExit(f"Mixed entry #{index} has invalid type {media_type!r}")
        validate_entries([[title, year]], f"Mixed entry #{index}")

def build(config):
    slug = config["catalog_id"]
    if not slug or not all(c.islower() or c.isdigit() or c == "-" for c in slug):
        raise SystemExit("catalog_id must contain only lowercase letters, digits and hyphens")
    kind = config.get("type", "movie")
    if kind not in ("movie", "series", "mixed", "mixed_row"):
        raise SystemExit("type must be movie, series, mixed or mixed_row")
    if kind == "mixed_row":
        ordered = config.get("entries", [])
        if not ordered:
            raise SystemExit("Mixed-row entries cannot be empty")
        validate_mixed_entries(ordered)
        grouped = {"movie": [], "series": []}
        for item in ordered:
            if len(item) != 3 or item[0] not in grouped:
                raise SystemExit("Each mixed-row entry must be [movie|series, title, year]")
            media_type, title, year = item
            grouped[media_type].append([title, year])
        base = root / "published" / slug
        all_metas = {}
        for media_type, titles in grouped.items():
            if not titles:
                continue
            child = dict(config, type=media_type)
            child["movies" if media_type == "movie" else "shows"] = titles
            build(child)
            generated = base / "catalog" / media_type / (slug + ".json")
            for meta in json.loads(generated.read_text())["metas"]:
                key = (meta["type"], meta["name"])
                if key in all_metas:
                    raise SystemExit("Duplicate mixed-row title: " + meta["name"])
                all_metas[key] = meta
        ordered_metas = [all_metas[(t, title)] for t, title, year in ordered]
        target = base / "catalog" / "movie" / (slug + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"metas": ordered_metas}, ensure_ascii=False, indent=2) + "\n")
        # The working Nuvio single-row format declares only the movie catalog.
        manifest = {"id": "com.kc6442." + slug.replace("-", "."),
                    "version": "1.0.0", "name": config["name"],
                    "description": config["description"], "resources": ["catalog"],
                    "types": ["movie"],
                    "catalogs": [{"type": "movie", "id": slug, "name": config["name"]}]}
        (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        (base / "release_order.json").write_text(json.dumps({"entries": [
            {"type": t, "name": title, "year": year} for t, title, year in ordered
        ]}, ensure_ascii=False, indent=2) + "\n")
        print(f"Built {len(ordered_metas)} mixed-row entries: {target}")
        return
    if kind == "mixed":
        ordered = config.get("entries", [])
        if not ordered:
            raise SystemExit("Mixed catalog entries cannot be empty")
        validate_mixed_entries(ordered)
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
    validate_entries(entries, kind)
    metas = []
    for title,year in entries:
        tmdb_kind = "tv" if kind == "series" else "movie"
        date_key = "first_air_date" if kind == "series" else "release_date"
        title_key = "name" if kind == "series" else "title"
        # Search without a year restriction as a fallback: TMDB sometimes lists
        # a different release year or an alternate/original title.
        results = tmdb("search/" + tmdb_kind, {
            "query": title,
            "first_air_date_year" if kind == "series" else "year": year,
        }).get("results", [])
        results += tmdb("search/" + tmdb_kind, {"query": title}).get("results", [])
        original_key = "original_name" if kind == "series" else "original_title"
        def match_score(item):
            names = [item.get(title_key) or "", item.get(original_key) or ""]
            similarity = max(difflib.SequenceMatcher(None, norm(title), norm(name)).ratio()
                             for name in names)
            exact = any(norm(name) == norm(title) for name in names)
            date = str(item.get(date_key) or "")[:4]
            year_delta = abs(int(date) - int(year)) if date.isdigit() else 999
            # Never silently replace a requested title with an unrelated result.
            if not exact and (similarity < 0.90 or year_delta > 1):
                return None
            if year_delta > 1:
                return None
            return (int(exact), int(year_delta == 0), similarity)
        # The year-filtered and unfiltered searches often return the same TMDB ID.
        # Deduplicate before ambiguity checks so one movie is not counted twice.
        unique_results = {item["id"]: item for item in results if item.get("id")}
        candidates = [(match_score(item), item) for item in unique_results.values()]
        candidates = [(score, item) for score, item in candidates if score is not None]
        if not candidates:
            raise SystemExit(
                f"No safe TMDB match for {title!r} ({year}). "
                "Check the title/year or supply a TMDB ID."
            )
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        m = candidates[0][1]
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0] and candidates[0][1]["id"] != candidates[1][1]["id"]:
            raise SystemExit(f"Ambiguous TMDB match for {title!r} ({year}); supply a TMDB ID.")
        if not m.get("poster_path"):
            raise SystemExit(f"Missing poster: {title} ({year})")
        external = tmdb(f"{tmdb_kind}/{m['id']}/external_ids")
        imdb = external.get("imdb_id")
        # TMDB IDs are stable identifiers when IMDb IDs are missing.
        identifier = imdb or "tmdb:" + str(m["id"])
        metas.append({"id":identifier,"type":kind,"name":title,"poster":"https://image.tmdb.org/t/p/w500"+m["poster_path"],"description":m.get("overview",""),"releaseInfo":str(year)})
    if len({m["id"] for m in metas}) != len(metas):
        raise SystemExit("Duplicate TMDB/IMDb IDs found in catalog")
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
        "name": "Alien vs Predator",
        "description": "Experimental single movie catalog row with mixed movie and series items",
        "resources": ["catalog"],
        "types": ["movie"],
        "catalogs": [{"type": "movie", "id": slug, "name": "Alien vs Predator"}],
    }
    (mixed_base / "manifest.json").write_text(json.dumps(mixed_manifest, ensure_ascii=False, indent=2) + "\n")
    print("Built mixed test catalog with 18 entries")
