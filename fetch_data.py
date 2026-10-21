"""Download and merge city market data into data/cities.json.

Sources (all free, no login):
  Rent    ACS 5-yr B25031 (E005)  median gross rent, 3-bedroom units (place level)
  Price   Zillow ZHVI, 3-bedroom homes, city level (latest month)
  Value growth   Zillow ZHVI all homes, latest month vs 12 months earlier
  Population + growth   Census PEP city estimates (incorporated places); ACS for CDPs
  Income + growth   ACS 5-yr B19013_001E, latest vintage vs prior vintage
  Vacancy   ACS 5-yr rental vacancy = for rent / (renter-occupied + for rent + rented not occupied)
  Unemployment + job growth   BLS LAUS cities >25k (trailing 12-month averages)
  Coordinates   Census Gazetteer places file

Usage: python3 fetch_data.py
"""
import csv, io, json, os, re, statistics, sys, urllib.request, zipfile

MIN_POP = 30000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE = os.path.join(OUT, "raw")
os.makedirs(CACHE, exist_ok=True)

BAD_LANDLORD = ["CA", "NY", "NJ", "OR", "WA", "MA", "IL", "MD", "VT", "DC"]

UA = f"rtp-map data build ({os.environ.get('BLS_EMAIL', 'research@example.com')})"
gaps = []  # human-readable notes on data we could not get


def get(url, name=None, binary=False):
    path = os.path.join(CACHE, name) if name else None
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read()
    else:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
        if path:
            with open(path, "wb") as f:
                f.write(raw)
    return raw if binary else raw.decode("utf-8-sig", errors="replace")


def try_get(urls, name, binary=False):
    last = None
    for u in urls:
        try:
            return get(u, name, binary), u
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"could not fetch {name}: {last}")


def num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x < -1e8 else x  # census uses -666666666 etc. for missing


def pct(new, old):
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


STATES = {}  # fips -> (abbr, name)
for line in """01 AL Alabama|02 AK Alaska|04 AZ Arizona|05 AR Arkansas|06 CA California|08 CO Colorado|09 CT Connecticut|10 DE Delaware|11 DC District of Columbia|12 FL Florida|13 GA Georgia|15 HI Hawaii|16 ID Idaho|17 IL Illinois|18 IN Indiana|19 IA Iowa|20 KS Kansas|21 KY Kentucky|22 LA Louisiana|23 ME Maine|24 MD Maryland|25 MA Massachusetts|26 MI Michigan|27 MN Minnesota|28 MS Mississippi|29 MO Missouri|30 MT Montana|31 NE Nebraska|32 NV Nevada|33 NH New Hampshire|34 NJ New Jersey|35 NM New Mexico|36 NY New York|37 NC North Carolina|38 ND North Dakota|39 OH Ohio|40 OK Oklahoma|41 OR Oregon|42 PA Pennsylvania|44 RI Rhode Island|45 SC South Carolina|46 SD South Dakota|47 TN Tennessee|48 TX Texas|49 UT Utah|50 VT Vermont|51 VA Virginia|53 WA Washington|54 WV West Virginia|55 WI Wisconsin|56 WY Wyoming""".split("|"):
    f, a, n = line.split(" ", 2)
    STATES[f] = (a, n)
ABBR2FIPS = {a: f for f, (a, _) in STATES.items()}

SUFFIX = re.compile(r"\s+(city and borough|consolidated government \(balance\)|metropolitan government \(balance\)|"
                    r"metro government \(balance\)|unified government \(balance\)|urban county|"
                    r"city \(balance\)|city|town|village|borough|municipality|CDP|township|plantation|comunidad|zona urbana)$", re.I)


def clean_name(n):
    n = n.split(",")[0].strip()
    n = SUFFIX.sub("", n).strip()
    return n


def norm(n):
    n = n.lower().replace("saint ", "st. ").replace("st ", "st. ").replace("ft. ", "fort ")
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


ALIASES = {  # census clean name -> zillow-style name
    ("Nashville-Davidson", "TN"): "Nashville",
    ("Louisville/Jefferson County", "KY"): "Louisville",
    ("Lexington-Fayette", "KY"): "Lexington",
    ("Urban Honolulu", "HI"): "Honolulu",
    ("Athens-Clarke County", "GA"): "Athens",
    ("Augusta-Richmond County", "GA"): "Augusta",
    ("Macon-Bibb County", "GA"): "Macon",
    ("Columbus", "GA"): "Columbus",
    ("Boise City", "ID"): "Boise",
    ("San Buenaventura (Ventura)", "CA"): "Ventura",
    ("Anchorage", "AK"): "Anchorage",
    ("Hallandale Beach", "FL"): "Hallandale",
    ("Carson", "NV"): "Carson City",
    ("Butte-Silver Bow (balance)", "MT"): "Butte",
}


# Consolidated city-counties: BLS reports them under a different place code than Census uses.
LAUS_CODE = {
    ("Nashville-Davidson", "TN"): "4752004", ("Louisville/Jefferson County", "KY"): "2148003",
    ("Athens-Clarke County", "GA"): "1303436", ("Augusta-Richmond County", "GA"): "1304200",
    ("Urban Honolulu", "HI"): "1516999", ("Butte-Silver Bow (balance)", "MT"): "3011390", ("Milford", "CT"): "0947500",
}


def display_name(name, abbr):
    n = ALIASES.get((name, abbr), name)
    n = re.sub(r"\s*\(balance\)$", "", n)
    n = re.sub(r"\s+Town$", "", n)
    m = re.match(r".*\(([^)]+)\)$", n)
    return m.group(1) if m else n


def zillow_keys(name, abbr):
    """Candidate Zillow names, most specific first."""
    cands = [ALIASES.get((name, abbr), name)]
    m = re.search(r"\(([^)]+)\)", name)
    if m:
        cands.append(m.group(1))
    cands.append(re.sub(r"\s*\(.*\)", "", name))
    cands.append(re.sub(r"\s+Town$", "", name))
    return [(norm(c), abbr) for c in cands]


# ---------- Census ACS (bulk summary files; the ACS API now needs a key) ----------
def sf(year, table):
    url = f"https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/5YRData/acsdt5y{year}-{table}.dat"
    txt = get(url, f"sf{year}-{table}.dat")
    rd = csv.reader(io.StringIO(txt), delimiter="|")
    hdr = next(rd)
    out = {}
    for r in rd:
        if r[0].startswith("1600000US"):
            out[r[0][9:]] = dict(zip(hdr, r))
    return out


def latest_acs_year():
    for y in (2025, 2024, 2023):
        try:
            sf(y, "b01003")
            return y
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Census ACS summary files unreachable")


Y = latest_acs_year()
print("ACS vintage", Y)
TABLES = {"pop": ("b01003", "B01003_E001"), "inc": ("b19013", "B19013_E001"), "rent3": ("b25031", "B25031_E005"),
          "lf": ("b23025", "B23025_E003"), "emp": ("b23025", "B23025_E004"), "unemp": ("b23025", "B23025_E005"),
          "renter": ("b25003", "B25003_E003"), "forrent": ("b25004", "B25004_E002"), "rentednocc": ("b25004", "B25004_E003")}


def acs_year(year, keys):
    tabs = {t: sf(year, t) for t in {TABLES[k][0] for k in keys}}
    out = {}
    for k in keys:
        t, col = TABLES[k]
        for geo, row in tabs[t].items():
            out.setdefault(geo, {})[k] = num(row.get(col))
    return out


cur = acs_year(Y, ["pop", "inc", "rent3", "renter", "forrent", "rentednocc", "lf", "emp", "unemp"])
prev = acs_year(Y - 1, ["pop", "inc", "emp"])


def rental_vacancy(d):
    fr, ro, rn = d.get("forrent"), d.get("renter"), d.get("rentednocc")
    if None in (fr, ro, rn) or fr + ro + rn == 0:
        return None
    return fr / (fr + ro + rn) * 100


# ---------- Census PEP city population ----------
pep = {}
pep_years = None
for v in (2025, 2024):
    try:
        txt, _ = try_get([f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{v}/cities/totals/sub-est{v}.csv"],
                         f"sub-est{v}.csv")
    except Exception:  # noqa: BLE001
        continue
    for r in csv.DictReader(io.StringIO(txt)):
        if r["SUMLEV"] in ("162", "170", "172"):
            pep[r["STATE"] + r["PLACE"]] = (num(r[f"POPESTIMATE{v}"]), num(r[f"POPESTIMATE{v-1}"]))
    pep_years = (v - 1, v)
    break
if not pep:
    gaps.append("Census PEP city population estimates unavailable; used ACS population for every city.")

# ---------- Gazetteer coordinates ----------
coords = {}
for gy in (2025, 2024, 2023):
    try:
        raw, _ = try_get([f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{gy}_Gazetteer/{gy}_Gaz_place_national.zip"],
                         f"gaz{gy}.zip", binary=True)
    except Exception:  # noqa: BLE001
        continue
    z = zipfile.ZipFile(io.BytesIO(raw))
    txt = z.read(z.namelist()[0]).decode("latin-1")
    rd = csv.reader(io.StringIO(txt), delimiter="|" if "|" in txt.split("\n", 1)[0] else "\t")
    hdr = [h.strip() for h in next(rd)]
    for r in rd:
        d = dict(zip(hdr, (c.strip() for c in r)))
        coords[d["GEOID"]] = (float(d["INTPTLONG"]), float(d["INTPTLAT"]), d["NAME"])
    break

# ---------- Zillow ----------
def zillow(fname, kind="zhvi", by_zip=False):
    txt = get(f"https://files.zillowstatic.com/research/public_csvs/{kind}/{fname}", fname)
    rd = csv.reader(io.StringIO(txt))
    hdr = next(rd)
    dates = [i for i, h in enumerate(hdr) if re.match(r"\d{4}-\d{2}-\d{2}", h)]
    out = {}
    for r in rd:
        d = dict(zip(hdr, r))
        vals = [num(r[i]) if r[i] else None for i in dates]
        out[d["RegionName"].zfill(5) if by_zip else (norm(d["RegionName"]), d["State"])] = vals
    return out, [hdr[i] for i in dates]


z3, z3dates = zillow("City_zhvi_bdrmcnt_3_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv")
zall, zdates = zillow("City_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv")
zip3, _ = zillow("Zip_zhvi_bdrmcnt_3_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv", by_zip=True)
zipall, _ = zillow("Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv", by_zip=True)
zori, zori_dates = zillow("City_zori_uc_sfrcondomfr_sm_month.csv", "zori")
zipzori, _ = zillow("Zip_zori_uc_sfrcondomfr_sm_month.csv", "zori", by_zip=True)

# ZIP codes overlapping each place, weighted by shared land area (Census 2020 relationship file)
place_zips = {}
rel = get("https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_place20_natl.txt",
          "tab20_zcta520_place20_natl.txt")
for r in csv.DictReader(io.StringIO(rel), delimiter="|"):
    z, pl, a = r["GEOID_ZCTA5_20"], r["GEOID_PLACE_20"], num(r["AREALAND_PART"])
    if z and pl and a:
        place_zips.setdefault(pl, []).append((z, a))


def latest(vals):
    return next((v for v in reversed(vals) if v), None) if vals else None


def growth12(vals):
    if vals and len(vals) >= 13 and vals[-1] and vals[-13]:
        return pct(vals[-1], vals[-13])
    return None


# ACS 5-yr rents are an average over the survey years; scale them to today with Zillow's rent index.
ACS_WINDOW = [i for i, d in enumerate(zori_dates) if Y - 4 <= int(d[:4]) <= Y]


def rent_factor(vals):
    if not vals:
        return None
    base = [vals[i] for i in ACS_WINDOW if i < len(vals) and vals[i]]
    now = latest(vals)
    if len(base) < 24 or not now:
        return None
    return now / statistics.mean(base)


def zip_weighted(geo, fn, table):
    """Area-weighted average of fn(series) across the ZIPs overlapping a place."""
    tot = wsum = 0
    for z, a in place_zips.get(geo, []):
        v = fn(table.get(z))
        if v is not None:
            tot += v * a
            wsum += a
    return tot / wsum if wsum else None


# ---------- BLS LAUS cities ----------
laus = {}
try:
    area_txt = get("https://download.bls.gov/pub/time.series/la/la.area", "la.area")
    data_txt = get("https://download.bls.gov/pub/time.series/la/la.data.65.City", "la.data.65.City")
    area2place = {}
    for r in csv.DictReader(io.StringIO(area_txt), delimiter="\t"):
        r = {k.strip(): (v or "").strip() for k, v in r.items()}
        m = re.match(r"CT(\d{7})", r.get("area_code", ""))
        if m:
            area2place[r["area_code"]] = m.group(1)
    series = {}
    for r in csv.DictReader(io.StringIO(data_txt), delimiter="\t"):
        r = {k.strip(): (v or "").strip() for k, v in r.items()}
        sid, per = r["series_id"], r["period"]
        if not sid.startswith("LAU") or per == "M13":
            continue
        area, measure = sid[3:-2], sid[-2:]
        if measure not in ("03", "05") or area not in area2place:
            continue
        v = num(r["value"])
        if v is not None:
            series.setdefault((area2place[area], measure), {})[(int(r["year"]), int(per[1:]))] = v
    for (geo, measure), pts in series.items():
        keys = sorted(pts)
        if len(keys) < 24:
            continue
        last12 = [pts[k] for k in keys[-12:]]
        prior12 = [pts[k] for k in keys[-24:-12]]
        laus.setdefault(geo, {})["unemp" if measure == "03" else "emp"] = (statistics.mean(last12), statistics.mean(prior12))
    laus_through = "%d-%02d" % max(k for pts in series.values() for k in pts)
except Exception as e:  # noqa: BLE001
    laus_through = None
    gaps.append(f"BLS LAUS city data unavailable ({e}); job growth and unemployment missing everywhere.")

# ---------- Merge ----------
cities, unmatched_zillow, no_rent = [], [], []
for geo, r in cur.items():
    st = geo[:2]
    if geo not in coords:
        continue
    if st not in STATES:
        continue  # Puerto Rico etc.
    abbr = STATES[st][0]
    acs_pop = r.get("pop")
    pop, pop_prev = pep.get(geo, (None, None))
    pop_src = "pep"
    if pop is None:
        pop, pop_prev, pop_src = acs_pop, prev.get(geo, {}).get("pop"), "acs"
    if pop is None or pop < MIN_POP:
        continue
    name = clean_name(coords[geo][2])
    keys = zillow_keys(name, abbr)
    z3v = next((z3[k] for k in keys if k in z3), None)
    zav = next((zall[k] for k in keys if k in zall), None)
    src = {}
    price = latest(z3v)
    if price is None:
        price = zip_weighted(geo, latest, zip3)
        if price is not None:
            src["price"] = "zip"
    hv = latest(zav)  # median value of all homes (used by the Appreciation view)
    if hv is None:
        hv = zip_weighted(geo, latest, zipall)
        if hv is not None:
            src["hv"] = "zip"
    vg = growth12(zav)
    if vg is None:
        vg = zip_weighted(geo, growth12, zipall)
        if vg is not None:
            src["vg"] = "zip"
    if not z3v and not zav:
        unmatched_zillow.append(f"{name}, {abbr}")
    rent_acs = r.get("rent3")
    if rent_acs is None:
        no_rent.append(f"{name}, {abbr}")
    zkeys = keys
    rf = next((rent_factor(zori[k]) for k in zkeys if k in zori and rent_factor(zori[k])), None)
    rsrc = "city"
    if rf is None:
        rf, rsrc = zip_weighted(geo, rent_factor, zipzori), "zip"
    inc = r.get("inc")
    l = laus.get(LAUS_CODE.get((name, abbr), geo), {})
    emp, un = l.get("emp"), l.get("unemp")
    jg = pct(emp[0], emp[1]) if emp else None
    unr = un[0] if un else None
    if jg is None:
        jg = pct(r.get("emp"), prev.get(geo, {}).get("emp"))
        if jg is not None:
            src["jg"] = "acs"
    if unr is None and r.get("lf"):
        unr = (r.get("unemp") or 0) / r["lf"] * 100
        src["un"] = "acs"
    lonlat = coords[geo]
    cities.append({
        "id": geo, "n": display_name(name, abbr), "s": abbr, "lon": round(lonlat[0], 4), "lat": round(lonlat[1], 4),
        "pop": int(pop),
        "rentAcs": rent_acs, "rf": rf, "rs": rsrc, "price": round(price) if price else None, "hv": round(hv) if hv else None,
        "pg": pct(pop, pop_prev), "vg": vg,
        "jg": jg,
        "ig": pct(inc, prev.get(geo, {}).get("inc")),
        "vac": rental_vacancy(r),
        "un": unr,
        "inc": inc, "ps": pop_src, "src": src,
    })

# Cities with no Zillow rent index nearby get their state's median rent adjustment.
state_rf = {}
for c in cities:
    if c["rf"]:
        state_rf.setdefault(c["s"], []).append(c["rf"])
all_rf = statistics.median([f for v in state_rf.values() for f in v])
for c in cities:
    if not c["rf"]:
        c["rf"], c["rs"] = (statistics.median(state_rf[c["s"]]) if c["s"] in state_rf else all_rf), "state"
    c["rent"] = round(c["rentAcs"] * c["rf"]) if c["rentAcs"] else None
    if c["rs"] != "city":
        c["src"]["rent"] = c["rs"]
    c["rf"] = round(c["rf"], 3)
    del c["rs"]
    if not c["src"]:
        del c["src"]

for c in cities:
    for k in ("pg", "vg", "jg", "ig", "vac", "un"):
        if c[k] is not None:
            c[k] = round(c[k], 2)

missing = {k: sum(1 for c in cities if c[k] is None) for k in ("rent", "price", "pg", "vg", "jg", "ig", "vac", "un")}
meta = {
    "acsYear": Y, "pepYears": pep_years, "zillowMonth": zdates[-1] if zdates else None, "zoriMonth": zori_dates[-1],
    "rentFactorMedian": round(statistics.median(c["rf"] for c in cities), 3),
    "lausThrough": laus_through, "badLandlord": BAD_LANDLORD, "count": len(cities), "missing": missing,
}
with open(os.path.join(OUT, "cities.json"), "w") as f:
    json.dump({"meta": meta, "cities": cities}, f, separators=(",", ":"))
with open(os.path.join(OUT, "gaps.json"), "w") as f:
    json.dump({"notes": gaps, "missing": missing, "unmatchedZillow": unmatched_zillow, "noRent": no_rent}, f, indent=1)
print(json.dumps(meta, indent=1))
print(f"{len(unmatched_zillow)} cities not found in Zillow; {len(no_rent)} without ACS 3BR rent")
