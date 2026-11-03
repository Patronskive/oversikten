"""Finner koordinater for reisemål som mangler lon/lat, ved å slå opp navnet (osm_sok) i OpenStreetMap-utsnittet.

Bruk: python geokod.py region.osm.pbf
Velger treffet nærmest Oslo sentrum blant objekter med nøyaktig dette navnet; bygninger og områder gir midtpunkt.
"""
import json
import math
import os
import subprocess
import sys
import tempfile

from shapely.geometry import shape

from ssb_rutenett import koble_til

MIDT = (10.75, 59.91)


def finn(pbf, navn):
    with tempfile.TemporaryDirectory() as d:
        ut = os.path.join(d, "x.osm.pbf")
        geo = os.path.join(d, "x.geojsonseq")
        subprocess.run(["osmium", "tags-filter", pbf, f"nwr/name={navn}", "-o", ut, "--overwrite"], check=True)
        subprocess.run(["osmium", "export", ut, "-f", "geojsonseq", "-o", geo, "--overwrite"], check=True)
        treff = []
        with open(geo, encoding="utf-8") as f:
            for linje in f:
                linje = linje.strip().lstrip("\x1e")
                if not linje:
                    continue
                o = json.loads(linje)
                if (o.get("properties") or {}).get("name") != navn:
                    continue
                g = shape(o["geometry"])
                p = g.centroid if g.geom_type != "Point" else g
                avst = math.hypot((p.x - MIDT[0]) * math.cos(math.radians(59.9)), p.y - MIDT[1])
                prioritet = 0 if g.geom_type in ("Polygon", "MultiPolygon") else 1
                treff.append((prioritet, avst, p.x, p.y, o["properties"]))
    if not treff:
        return None
    treff.sort(key=lambda t: (t[1] > 0.3, t[0], t[1]))  # innenfor ca. 30 km, foretrekk områder/bygninger
    return treff[0]


def main():
    pbf = sys.argv[1]
    with koble_til(os.environ["SUPABASE_DB_URL"]) as conn:
        mangler = conn.execute("select id, osm_sok from reisemal where aktiv and (lon is null or lat is null) and osm_sok is not null").fetchall()
        for mid, sok in mangler:
            t = finn(pbf, sok)
            if not t:
                print(f"Fant ikke «{sok}» i OpenStreetMap")
                continue
            _, _, lon, lat, props = t
            print(f"{sok}: {lat:.5f}, {lon:.5f} ({', '.join(f'{k}={v}' for k, v in props.items() if k in ('amenity', 'shop', 'building', 'addr:street', 'addr:housenumber'))})")
            conn.execute("update reisemal set lon = %s, lat = %s where id = %s", (round(lon, 6), round(lat, 6), mid))
        conn.commit()


if __name__ == "__main__":
    main()
