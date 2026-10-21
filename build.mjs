// Builds dist/index.html: embeds state shapes and projected city data into app.html.
import fs from "node:fs";
import { geoPath, geoAlbersUsa } from "d3-geo";
import { feature } from "topojson-client";

const dataFile = process.argv[2] || "data/cities.json";
const topo = JSON.parse(fs.readFileSync("build/states-albers-10m.json", "utf8"));
const path = geoPath(null).digits(1);
const NAME2ABBR = { Alabama: "AL", Alaska: "AK", Arizona: "AZ", Arkansas: "AR", California: "CA", Colorado: "CO", Connecticut: "CT", Delaware: "DE", "District of Columbia": "DC", Florida: "FL", Georgia: "GA", Hawaii: "HI", Idaho: "ID", Illinois: "IL", Indiana: "IN", Iowa: "IA", Kansas: "KS", Kentucky: "KY", Louisiana: "LA", Maine: "ME", Maryland: "MD", Massachusetts: "MA", Michigan: "MI", Minnesota: "MN", Mississippi: "MS", Missouri: "MO", Montana: "MT", Nebraska: "NE", Nevada: "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", Ohio: "OH", Oklahoma: "OK", Oregon: "OR", Pennsylvania: "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", Tennessee: "TN", Texas: "TX", Utah: "UT", Vermont: "VT", Virginia: "VA", Washington: "WA", "West Virginia": "WV", Wisconsin: "WI", Wyoming: "WY" };

const states = feature(topo, topo.objects.states).features
  .filter((f) => NAME2ABBR[f.properties.name])
  .map((f) => {
    const b = path.bounds(f);
    return { a: NAME2ABBR[f.properties.name], n: f.properties.name, d: path(f), b: b.flat().map((v) => Math.round(v * 10) / 10) };
  });

const proj = geoAlbersUsa().scale(1300).translate([487.5, 305]);
const { meta, cities } = JSON.parse(fs.readFileSync(dataFile, "utf8"));
const out = [];
for (const c of cities) {
  const p = proj([c.lon, c.lat]);
  if (!p) continue;
  const { lon, lat, ...rest } = c;
  out.push({ ...rest, x: Math.round(p[0] * 10) / 10, y: Math.round(p[1] * 10) / 10 });
}

const payload = JSON.stringify({ meta, states, cities: out });
const html = fs.readFileSync("app.html", "utf8").replace("/*__DATA__*/null", payload);
fs.mkdirSync("dist", { recursive: true });
fs.writeFileSync("dist/index.html", html);
console.log(`dist/index.html: ${states.length} states, ${out.length} cities, ${(html.length / 1024).toFixed(0)} KB`);
