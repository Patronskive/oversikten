"""Beregner reisetid med kollektiv fra hver 250 m-rute til faste mål (tabellen reisemal) med r5py.

Bruk: python reisetid.py region.osm.pbf gtfs.zip [--max-memory 13G]
Kilder: Entur GTFS for hele Norge (NLOD), OpenStreetMap (ODbL).

Reisetid = median over avreiser hvert minutt mellom 07:00 og 08:00 en tirsdag minst en uke fram,
inkludert gange til og fra holdeplass (4,8 km/t). Over 120 minutter lagres som tom verdi.
"""
import datetime as dt
import os
import sys
import time

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from ssb_rutenett import koble_til

FILER = [a for a in sys.argv[1:] if not a.startswith("--") and not a.endswith("G")]


def reisedag():
    d = dt.date.today() + dt.timedelta(days=7)
    while True:
        while d.weekday() != 1:
            d += dt.timedelta(days=1)
        juleferie = (d.month == 12 and d.day >= 18) or (d.month == 1 and d.day <= 4)
        sommerferie = d.month == 7
        påske_omtrent = d.month == 4 and d.day <= 10
        if not (juleferie or sommerferie or påske_omtrent):
            return d
        d += dt.timedelta(days=7)


def main():
    pbf, gtfs = FILER[0], FILER[1]
    import r5py  # leser --max-memory fra kommandolinjen

    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")
    with koble_til(url) as conn:
        mål = conn.execute("select id, navn, lon, lat from reisemal where aktiv order by rekkefolge").fetchall()
        ruter = conn.execute("""
            select ssbid,
                   extensions.st_x(extensions.st_transform(extensions.st_centroid(geom), 4326)),
                   extensions.st_y(extensions.st_transform(extensions.st_centroid(geom), 4326))
            from rute250 order by ssbid
        """).fetchall()

    fra = gpd.GeoDataFrame({"id": [r[0] for r in ruter]}, geometry=[Point(r[1], r[2]) for r in ruter], crs=4326)
    til = gpd.GeoDataFrame({"id": [m[0] for m in mål]}, geometry=[Point(m[2], m[3]) for m in mål], crs=4326)
    dag = reisedag()
    avreise = dt.datetime.combine(dag, dt.time(7, 0))
    print(f"{len(fra)} ruter til {len(til)} mål ({', '.join(m[1] for m in mål)}), avreise {avreise:%A %d.%m.%Y %H:%M}–08:00")

    t0 = time.time()
    nett = r5py.TransportNetwork(pbf, [gtfs], allow_errors=True) if "allow_errors" in r5py.TransportNetwork.__init__.__code__.co_varnames \
        else r5py.TransportNetwork(pbf, [gtfs])
    print(f"Transportnett bygget på {time.time() - t0:.0f} s")

    t0 = time.time()
    ttm = r5py.TravelTimeMatrix(
        nett,
        origins=fra,
        destinations=til,
        departure=avreise,
        departure_time_window=dt.timedelta(hours=1),
        transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
        max_time=dt.timedelta(minutes=120),
        speed_walking=4.8,
        snap_to_network=500,
    )
    df = ttm.compute_travel_times() if hasattr(ttm, "compute_travel_times") else pd.DataFrame(ttm)
    print(f"Reisetider beregnet på {time.time() - t0:.0f} s, kolonner {list(df.columns)}")
    tid_kol = "travel_time" if "travel_time" in df.columns else [c for c in df.columns if c not in ("from_id", "to_id")][0]

    for m in mål:
        v = df.loc[df["to_id"] == m[0], tid_kol]
        print(f"  {m[1]}: median {v.median():.0f} min, {v.notna().mean() * 100:.0f} % av rutene innen 120 min")

    rader = [(r.from_id, r.to_id, None if pd.isna(getattr(r, tid_kol)) else int(getattr(r, tid_kol)), dag)
             for r in df.itertuples(index=False)]
    with koble_til(url) as conn, conn.cursor() as cur:
        for s in range(0, len(rader), 5000):
            cur.executemany(
                """insert into rute_reisetid (ssbid, maal_id, minutter, beregnet_for) values (%s, %s, %s, %s)
                   on conflict (ssbid, maal_id) do update set minutter = excluded.minutter, beregnet_for = excluded.beregnet_for""",
                rader[s:s + 5000],
            )
        cur.execute(
            "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
            ("Entur GTFS + OSM (r5py)", "Reisetid kollektiv", len(rader), f"Avreise 07–08 {dag.isoformat()}"),
        )
        conn.commit()
    print(f"Ferdig: {len(rader)} reisetider lagret")


if __name__ == "__main__":
    main()
