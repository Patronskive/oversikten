"""Holdeplasser for kartet: togstasjoner, T-bane, trikk, buss og båt fra Entur sin GTFS.

Bruk: python holdeplasser.py gtfs_region.zip
Holdeplasser med felles stasjon (parent_station) slås sammen til én. Hver får et bitfelt for hvilke
transportmidler som stopper der: 1 tog, 2 T-bane, 4 trikk, 8 buss, 16 båt.
Lagres i tabellen holdeplass, som eksport.py tar med i stedsdata.json.
"""
import os
import sys
import zipfile

import pandas as pd

from ssb_rutenett import koble_til

TOG, TBANE, TRIKK, BUSS, BÅT = 1, 2, 4, 8, 16


def modus(rt):
    """GTFS route_type (både enkle og utvidede koder) → bit. Togbuss, taxi og fly tas ikke med."""
    rt = int(rt)
    if rt == 2 or 100 <= rt <= 117:
        return TOG
    if rt == 1 or 400 <= rt <= 405:
        return TBANE
    if rt == 0 or 900 <= rt <= 906:
        return TRIKK
    if rt == 4 or 1000 <= rt <= 1021 or rt == 1200:
        return BÅT
    if rt == 714:  # buss for tog
        return 0
    if rt == 3 or 200 <= rt <= 209 or 700 <= rt <= 716 or rt == 800:
        return BUSS
    return 0


def les(z, navn, **kw):
    with z.open(navn) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False, **kw)


def main():
    z = zipfile.ZipFile(sys.argv[1])
    stops = les(z, "stops.txt")
    routes = les(z, "routes.txt", usecols=["route_id", "route_type"])
    trips = les(z, "trips.txt", usecols=["trip_id", "route_id"])
    st = les(z, "stop_times.txt", usecols=["trip_id", "stop_id"]).drop_duplicates()

    routes["bit"] = routes.route_type.map(modus)
    tur_bit = trips.merge(routes[["route_id", "bit"]], on="route_id")[["trip_id", "bit"]]
    sb = st.merge(tur_bit, on="trip_id")[["stop_id", "bit"]].drop_duplicates()

    if "parent_station" not in stops.columns:
        stops["parent_station"] = ""
    forelder = dict(zip(stops.stop_id, stops.parent_station))
    sb["sted"] = sb.stop_id.map(lambda s: forelder.get(s) or s)
    bits = sb.groupby("sted").bit.agg(lambda b: int(pd.Series(b).astype(int).unique().sum()))
    bits = bits[bits > 0]

    info = stops.set_index("stop_id")
    rader = []
    for sid, b in bits.items():
        if sid not in info.index:
            continue
        r = info.loc[sid]
        rader.append((sid, r.stop_name, int(b), round(float(r.stop_lon), 5), round(float(r.stop_lat), 5)))
    antall = {navn: sum(1 for r in rader if r[2] & bit) for navn, bit in
              (("tog", TOG), ("T-bane", TBANE), ("trikk", TRIKK), ("buss", BUSS), ("båt", BÅT))}
    print(f"{len(rader)} holdeplasser: " + ", ".join(f"{v} {k}" for k, v in antall.items()))

    with koble_til(os.environ["SUPABASE_DB_URL"]) as conn, conn.cursor() as cur:
        cur.execute("""create table if not exists holdeplass (
                         id text primary key, navn text not null, typer int not null,
                         lon double precision not null, lat double precision not null)""")
        cur.execute("alter table holdeplass enable row level security")
        cur.execute("""do $$ begin
            if not exists (select 1 from pg_policies where tablename = 'holdeplass' and policyname = 'les') then
              create policy les on holdeplass for select using (true); end if; end $$""")
        cur.execute("truncate holdeplass")
        cur.executemany("insert into holdeplass (id, navn, typer, lon, lat) values (%s, %s, %s, %s, %s)", rader)
        cur.execute("insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
                    ("Entur GTFS", "Holdeplasser", len(rader), "Tog, T-bane, trikk, buss og båt"))
        conn.commit()


if __name__ == "__main__":
    main()
