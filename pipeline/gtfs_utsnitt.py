"""Lager et regionalt utsnitt av Entur sin GTFS for hele Norge, så r5py bygger et mindre nett.

Bruk: python gtfs_utsnitt.py inn.zip ut.zip [vest,sør,øst,nord]
Holdeplasser innenfor boksen beholdes; turer med minst to stopp i boksen beholdes (kuttet til de stoppene).
"""
import io
import sys
import zipfile

import pandas as pd

BOKS = (9.2, 58.7, 12.6, 60.55)


def les(z, navn):
    with z.open(navn) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def les_filtrert(z, navn, kol, behold):
    with z.open(navn) as f:
        return pd.concat(b[b[kol].isin(behold)]
                         for b in pd.read_csv(f, dtype=str, keep_default_na=False, chunksize=2_000_000))


def main():
    inn, ut = sys.argv[1], sys.argv[2]
    v, s, ø, n = map(float, sys.argv[3].split(",")) if len(sys.argv) > 3 else BOKS
    z = zipfile.ZipFile(inn)
    navn = set(z.namelist())

    stops = les(z, "stops.txt")
    lon, lat = stops.stop_lon.astype(float), stops.stop_lat.astype(float)
    inne = stops[(lon >= v) & (lon <= ø) & (lat >= s) & (lat <= n)]
    foreldre = set(inne.get("parent_station", pd.Series(dtype=str))) - {""}
    stops = stops[stops.stop_id.isin(set(inne.stop_id) | foreldre)]
    stopp_id = set(inne.stop_id)
    print(f"Holdeplasser: {len(stopp_id)} i regionen")

    st = les_filtrert(z, "stop_times.txt", "stop_id", stopp_id)
    antall = st.groupby("trip_id").size()
    st = st[st.trip_id.isin(antall[antall >= 2].index)]
    tur_id = set(st.trip_id)
    print(f"Stopptider: {len(st)}, turer: {len(tur_id)}")

    trips = les(z, "trips.txt")
    trips = trips[trips.trip_id.isin(tur_id)]
    routes = les(z, "routes.txt")
    routes = routes[routes.route_id.isin(set(trips.route_id))]
    tjenester = set(trips.service_id)

    filer = {"stops.txt": stops, "stop_times.txt": st, "trips.txt": trips, "routes.txt": routes,
             "agency.txt": les(z, "agency.txt")}
    for f in ("calendar.txt", "calendar_dates.txt"):
        if f in navn:
            d = les(z, f)
            filer[f] = d[d.service_id.isin(tjenester)]
    if "transfers.txt" in navn:
        d = les(z, "transfers.txt")
        d = d[d.from_stop_id.isin(stopp_id) & d.to_stop_id.isin(stopp_id) & (d.transfer_type != "")]
        filer["transfers.txt"] = d
    if "shapes.txt" in navn and "shape_id" in trips.columns:
        former = set(trips.shape_id) - {""}
        filer["shapes.txt"] = les_filtrert(z, "shapes.txt", "shape_id", former)
    for f in ("feed_info.txt",):
        if f in navn:
            filer[f] = les(z, f)

    with zipfile.ZipFile(ut, "w", zipfile.ZIP_DEFLATED) as zo:
        for f, d in filer.items():
            buf = io.StringIO()
            d.to_csv(buf, index=False)
            zo.writestr(f, buf.getvalue())
    print(f"Skrev {ut}")


if __name__ == "__main__":
    main()
