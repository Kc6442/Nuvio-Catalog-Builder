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
        if isinstance(entry, str) and entry.strip():
            continue
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
        if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3):
            raise SystemExit(
                f"Mixed entry #{index} must be [movie|series, title] or [movie|series, title, year]"
            )
        media_type, title = entry[:2]
        if media_type not in ("movie", "series"):
            raise SystemExit(f"Mixed entry #{index} has invalid type {media_type!r}")
        if len(entry) == 2:
            validate_entries([title], f"Mixed entry #{index}")
        else:
            validate_entries([[title, entry[2]]], f"Mixed entry #{index}")

def mixed_release_order(ordered, base, slug):
    """Preserve input order and use TMDB-resolved years for title-only entries."""
    metas = {}
    for kind in ("movie", "series"):
        path = base / "catalog" / kind / (slug + ".json")
        if path.exists():
            for meta in json.loads(path.read_text())["metas"]:
                metas[(meta["type"], meta["name"])] = meta
    return [
        {"type": entry[0], "name": entry[1],
         "year": entry[2] if len(entry) == 3 else metas[(entry[0], entry[1])]["releaseInfo"]}
        for entry in ordered
    ]

def validate_metas(metas, catalog_label):
    """Reject broken generated metadata before writing published JSON."""
    if not metas:
        raise SystemExit(f"{catalog_label}: no generated catalog entries")
    seen_ids = set()
    for index, meta in enumerate(metas, 1):
        label = f"{catalog_label} entry #{index}"
        if not isinstance(meta, dict):
            raise SystemExit(f"{label}: metadata must be an object")
        for field in ("id", "name", "poster", "type"):
            if not isinstance(meta.get(field), str) or not meta[field].strip():
                raise SystemExit(f"{label}: missing or invalid {field}")
        if meta["type"] not in ("movie", "series"):
            raise SystemExit(f"{label}: unsupported type {meta['type']!r}")
        if not meta["poster"].startswith("https://image.tmdb.org/t/p/"):
            raise SystemExit(f"{label}: invalid TMDB poster URL")
        if meta["id"] in seen_ids:
            raise SystemExit(f"{label}: duplicate metadata ID {meta['id']}")
        seen_ids.add(meta["id"])

def build_actor_catalog(config, slug):
    """Build movie filmography from TMDB cast credits for any actor or actress."""
    person_id = config.get("person_id")
    actor = config.get("actor")
    if person_id is None:
        if not isinstance(actor, str) or not actor.strip():
            raise SystemExit(f"{slug}: actor must be a nonempty name, or set person_id")
        people = tmdb("search/person", {"query": actor}).get("results", [])
        matches = [p for p in people if norm(p.get("name", "")) == norm(actor)]
        if len(matches) != 1:
            raise SystemExit(
                f"{slug}: actor name is missing or ambiguous: {actor!r}. "
                "Specify person_id from TMDB to select the right person."
            )
        person_id = matches[0]["id"]
    if not str(person_id).isdigit():
        raise SystemExit(f"{slug}: person_id must be a numeric TMDB person ID")
    person = tmdb(f"person/{person_id}")
    credits = tmdb(f"person/{person_id}/movie_credits").get("cast", [])
    # Exclude unreleased entries and missing posters; avoid broken Nuvio metadata.
    unique = {}
    for movie in credits:
        date = movie.get("release_date") or ""
        if not (len(date) >= 4 and date[:4].isdigit() and movie.get("poster_path")):
            continue
        if movie.get("adult"):
            continue
        unique[movie["id"]] = movie
    ordered = sorted(unique.values(), key=lambda m: (m["release_date"], m.get("title", ""), m["id"]))
    metas = []
    for movie in ordered:
        external = tmdb(f"movie/{movie['id']}/external_ids")
        identifier = external.get("imdb_id") or "tmdb:" + str(movie["id"])
        metas.append({
            "id": identifier, "type": "movie",
            "name": movie.get("title") or movie.get("original_title") or str(movie["id"]),
            "poster": "https://image.tmdb.org/t/p/w500" + movie["poster_path"],
            "description": movie.get("overview") or "",
            "releaseInfo": movie["release_date"][:4],
        })
    validate_metas(metas, slug + "/actor")
    base = root / "published" / slug
    target = base / "catalog" / "movie" / (slug + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"metas": metas}, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "id": "com.kc6442." + slug.replace("-", "."),
        "version": "0.1.0", "name": config.get("name") or person["name"],
        "description": config.get("description") or ("Filmography of " + person["name"]),
        "resources": ["catalog"], "types": ["movie"],
        "catalogs": [{"type": "movie", "id": slug,
                      "name": config.get("name") or person["name"]}],
    }
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Built {len(metas)} films for {person['name']}: {target}")

def build(config):
    slug = config["catalog_id"]
    if not slug or not all(c.islower() or c.isdigit() or c == "-" for c in slug):
        raise SystemExit("catalog_id must contain only lowercase letters, digits and hyphens")
    kind = config.get("type", "movie")
    if kind == "actor":
        build_actor_catalog(config, slug)
        return
    if kind not in ("movie", "series", "mixed", "mixed_row"):
        raise SystemExit("type must be movie, series, mixed, mixed_row or actor")
    if kind == "mixed_row":
        ordered = config.get("entries", [])
        if not ordered:
            raise SystemExit("Mixed-row entries cannot be empty")
        validate_mixed_entries(ordered)
        grouped = {"movie": [], "series": []}
        for item in ordered:
            media_type, title = item[:2]
            grouped[media_type].append([title, item[2]] if len(item) == 3 else title)
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
        ordered_metas = [all_metas[(entry[0], entry[1])] for entry in ordered]
        validate_metas(ordered_metas, slug)
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
            *mixed_release_order(ordered, base, slug)
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
            media_type, title = item[:2]
            by_type[media_type].append([title, item[2]] if len(item) == 3 else title)
        for media_type, entries_for_type in by_type.items():
            if entries_for_type:
                child = dict(config, type=media_type)
                child["movies" if media_type == "movie" else "shows"] = entries_for_type
                build(child)
        base = root / "published" / slug
        manifest = {"id":"com.kc6442."+slug.replace("-","."),"version":"0.1.0","name":config["name"],"description":config["description"],"resources":["catalog"],"types":[t for t in by_type if by_type[t]],"catalogs":[{"type":t,"id":slug,"name":config["name"]} for t in by_type if by_type[t]]}
        (base/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
        (base/"release_order.json").write_text(json.dumps({"entries":mixed_release_order(ordered, base, slug)},indent=2)+"\n")
        return
    entries = config.get("shows" if kind == "series" else "movies", [])
    if not entries:
        raise SystemExit("Catalog entries cannot be empty")
    validate_entries(entries, kind)
    metas = []
    for entry in entries:
        title, year = (entry, None) if isinstance(entry, str) else entry
        tmdb_kind = "tv" if kind == "series" else "movie"
        date_key = "first_air_date" if kind == "series" else "release_date"
        title_key = "name" if kind == "series" else "title"
        # Search without a year restriction as a fallback: TMDB sometimes lists
        # a different release year or an alternate/original title.
        results = []
        if year is not None:
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
            year_delta = abs(int(date) - int(year)) if year is not None and date.isdigit() else (0 if year is None else 999)
            # Never silently replace a requested title with an unrelated result.
            if not exact and (similarity < (1.0 if year is None else 0.90) or year_delta > 1):
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
        # Prefer the most established TMDB result for equal title/year scores.
        # Popularity breaks ties for remakes and identically named releases;
        # the TMDB ID makes ordering deterministic even on exact ties.
        candidates.sort(
            key=lambda pair: (
                pair[0],
                float(pair[1].get("popularity") or 0),
                int(pair[1]["id"]),
            ),
            reverse=True,
        )
        m = candidates[0][1]
        if year is None:
            # Exact-title duplicates are common on TMDB. Prefer the established
            # result, but require a year if competing releases are close in popularity.
            exact_matches = [item for score, item in candidates if score[0]]
            if len(exact_matches) > 1:
                leader = float(exact_matches[0].get("popularity") or 0)
                runner_up = float(exact_matches[1].get("popularity") or 0)
                if leader <= 0 or runner_up >= leader * 0.5:
                    raise SystemExit(
                        f"Multiple plausible TMDB releases match {title!r}; specify [title, year]."
                    )
            year = str(m.get(date_key) or "")[:4]
            if not year.isdigit() or len(year) != 4:
                raise SystemExit(f"Missing TMDB release year for {title!r}; specify [title, year].")
        if not m.get("poster_path"):
            raise SystemExit(f"Missing poster: {title} ({year})")
        external = tmdb(f"{tmdb_kind}/{m['id']}/external_ids")
        imdb = external.get("imdb_id")
        # TMDB IDs are stable identifiers when IMDb IDs are missing.
        identifier = imdb or "tmdb:" + str(m["id"])
        metas.append({"id":identifier,"type":kind,"name":title,"poster":"https://image.tmdb.org/t/p/w500"+m["poster_path"],"description":m.get("overview",""),"releaseInfo":str(year)})
    if len({m["id"] for m in metas}) != len(metas):
        raise SystemExit("Duplicate TMDB/IMDb IDs found in catalog")
    validate_metas(metas, slug + "/" + kind)
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
    validate_metas([all_metas[name] for name in names], slug)
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
