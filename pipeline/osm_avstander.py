"""Beregner gangavstand og avstand til natur for hver 250 m-rute fra OpenStreetMap.

Bruk: python osm_avstander.py region.osm.pbf
Kilde: OpenStreetMap-bidragsytere (ODbL), via Geofabrik.

- Gangnett: alle highway-veier som kan gås (ikke motorvei, ikke privat/forbudt for gående)
- Gangdistanse: korteste vei i nettet fra nærmeste mål til rutens midtpunkt (Dijkstra fra alle mål samtidig)
- Sjø: luftlinje til kystlinjen (natural=coastline)
- Storskog: luftlinje til sammenhengende skog (landuse=forest / natural=wood) på minst 1 km²
"""
import os
import sys
import time
from array import array

import numpy as np
import osmium
import shapely
from pyproj import Transformer
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

KATEGORIER = ["tog", "tbane", "buss", "dagligvare", "skole", "barnehage"]
GRENSE_M = {"tog": 6000, "tbane": 6000, "buss": 3000, "dagligvare": 6000, "skole": 6000, "barnehage": 6000}
IKKE_GANG = {"motorway", "motorway_link", "trunk", "trunk_link", "construction", "proposed",
             "raceway", "bus_guideway", "escape", "abandoned", "razed", "planned"}
MAKS_FEST_MÅL = 300     # m fra et mål til nærmeste node i gangnettet
MAKS_FEST_RUTE = 500    # m fra rutens midtpunkt til gangnettet
MIN_KOMPONENT = 200     # noder; mindre, isolerte biter av nettet ignoreres
MIN_SKOG_DEL = 10_000   # m², enkeltpolygoner under dette tas ikke med
MIN_STORSKOG = 1_000_000  # m² etter sammenslåing

TIL_UTM = Transformer.from_crs(4326, 25833, always_xy=True)


def kategori(t):
    rw = t.get("railway")
    if rw in ("station", "halt"):
        if t.get("station") == "subway" or t.get("subway") == "yes":
            return "tbane"
        if t.get("station") in ("light_rail", "tram", "funicular", "monorail") or t.get("usage") == "tourism":
            return None
        return "tog"
    if t.get("highway") == "bus_stop":
        return "buss"
    if t.get("shop") == "supermarket":
        return "dagligvare"
    a = t.get("amenity")
    if a == "school":
        return "skole"
    if a == "kindergarten":
        return "barnehage"
    return None


def gangbar(t):
    hw = t.get("highway")
    if not hw or hw in IKKE_GANG:
        return False
    foot = t.get("foot")
    if foot in ("no", "private", "use_sidepath"):
        return False
    if t.get("access") in ("no", "private") and foot not in ("yes", "designated", "permissive"):
        return False
    return True


class Leser(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.idx = {}
        self.lon = array("d")
        self.lat = array("d")
        self.ea = array("q")
        self.eb = array("q")
        self.mål = {k: [] for k in KATEGORIER}
        self.kyst = []
        self.skog = []
        self.wkb = osmium.geom.WKBFactory()

    def _nodeidx(self, nr):
        i = self.idx.get(nr.ref)
        if i is None:
            i = len(self.lon)
            self.idx[nr.ref] = i
            self.lon.append(nr.location.lon)
            self.lat.append(nr.location.lat)
        return i

    def node(self, n):
        k = kategori(n.tags)
        if k and n.location.valid():
            self.mål[k].append((n.location.lon, n.location.lat))

    def way(self, w):
        t = w.tags
        if gangbar(t):
            forrige = None
            for nr in w.nodes:
                if not nr.location.valid():
                    forrige = None
                    continue
                i = self._nodeidx(nr)
                if forrige is not None and forrige != i:
                    self.ea.append(forrige)
                    self.eb.append(i)
                forrige = i
        if t.get("natural") == "coastline":
            pkt = [(nr.location.lon, nr.location.lat) for nr in w.nodes if nr.location.valid()]
            if len(pkt) > 1:
                self.kyst.append(pkt)
        k = kategori(t)
        if k:
            pkt = [(nr.location.lon, nr.location.lat) for nr in w.nodes if nr.location.valid()]
            if pkt:
                self.mål[k].append((sum(p[0] for p in pkt) / len(pkt), sum(p[1] for p in pkt) / len(pkt)))

    def area(self, a):
        t = a.tags
        if t.get("landuse") == "forest" or t.get("natural") == "wood":
            try:
                self.skog.append(self.wkb.create_multipolygon(a))
            except Exception:
                pass


def utm(lon, lat):
    x, y = TIL_UTM.transform(np.asarray(lon), np.asarray(lat))
    return np.column_stack([x, y])


def bygg_nett(les):
    xy = utm(les.lon, les.lat)
    a = np.frombuffer(les.ea, dtype=np.int64)
    b = np.frombuffer(les.eb, dtype=np.int64)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    par = np.unique(np.column_stack([lo, hi]), axis=0)
    lengde = np.hypot(*(xy[par[:, 0]] - xy[par[:, 1]]).T)
    n = len(xy)
    g = coo_matrix((lengde, (par[:, 0], par[:, 1])), shape=(n, n)).tocsr()
    _, merke = connected_components(g, directed=False)
    størrelse = np.bincount(merke)
    gyldig = størrelse[merke] >= MIN_KOMPONENT
    print(f"Gangnett: {n} noder, {len(par)} kanter, {gyldig.sum()} noder i store nok sammenhengende nett")
    return g, xy, gyldig


def beregn(les, g, xy, gyldige, tre, pxy):
    fest_d, fest_i = tre.query(pxy)
    rute_node = gyldige[fest_i]
    rute_ok = fest_d <= MAKS_FEST_RUTE

    resultat = {}
    for k in KATEGORIER:
        if not les.mål[k]:
            resultat[k] = np.full(len(pxy), np.nan)
            continue
        mxy = utm(*zip(*les.mål[k]))
        d, i = tre.query(mxy)
        kilder = np.unique(gyldige[i[d <= MAKS_FEST_MÅL]])
        t1 = time.time()
        avst = dijkstra(g, directed=False, indices=kilder, min_only=True, limit=GRENSE_M[k])
        verdi = avst[rute_node] + fest_d
        verdi[~rute_ok | ~np.isfinite(verdi) | (verdi > GRENSE_M[k])] = np.nan
        resultat[k] = verdi
        print(f"  {k}: {len(kilder)} mål i nettet, median {np.nanmedian(verdi):.0f} m, "
              f"{np.isfinite(verdi).mean() * 100:.0f} % av rutene innen {GRENSE_M[k]} m ({time.time() - t1:.0f} s)")

    # Sjø
    if les.kyst:
        linjer = [shapely.LineString(utm(*zip(*l))) for l in les.kyst]
        st = shapely.STRtree(linjer)
        punkter = shapely.points(pxy)
        (pi, _), dist = st.query_nearest(punkter, return_distance=True, all_matches=False)
        sjø = np.full(len(pxy), np.nan)
        sjø[pi] = dist
    else:
        sjø = np.full(len(pxy), np.nan)
    print(f"  sjø: median {np.nanmedian(sjø):.0f} m")

    # Storskog
    storskog = np.full(len(pxy), np.nan)
    if les.skog:
        t1 = time.time()
        geo = shapely.from_wkb(np.array(les.skog, dtype=object))
        geo = shapely.transform(geo, lambda c: utm(c[:, 0], c[:, 1]))
        geo = geo[shapely.area(geo) >= MIN_SKOG_DEL]
        geo = shapely.make_valid(geo)
        samlet = shapely.get_parts(shapely.union_all(geo, grid_size=1.0))
        stor = samlet[shapely.area(samlet) >= MIN_STORSKOG]
        print(f"  storskog: {len(geo)} skogpolygoner -> {len(stor)} områder over 1 km² ({time.time() - t1:.0f} s)")
        if len(stor):
            st = shapely.STRtree(stor)
            (pi, _), dist = st.query_nearest(shapely.points(pxy), return_distance=True, all_matches=False)
            storskog[pi] = dist
    print(f"  storskog: median {np.nanmedian(storskog):.0f} m")

    return resultat, sjø, storskog


def main():
    pbf = sys.argv[1] if len(sys.argv) > 1 else "region.osm.pbf"
    t0 = time.time()
    les = Leser()
    les.apply_file(pbf, locations=True, idx="flex_mem")
    print(f"Lest {pbf} på {time.time() - t0:.0f} s: " + ", ".join(f"{k} {len(v)}" for k, v in les.mål.items())
          + f", kystlinjer {len(les.kyst)}, skogpolygoner {len(les.skog)}")

    g, xy, gyldig = bygg_nett(les)
    gyldige = np.flatnonzero(gyldig)
    tre = cKDTree(xy[gyldige])

    from ssb_rutenett import koble_til
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")
    with koble_til(url) as conn:
        ruter = conn.execute(
            "select ssbid, extensions.st_x(extensions.st_centroid(geom)), extensions.st_y(extensions.st_centroid(geom)) from rute250"
        ).fetchall()
        ider = [r[0] for r in ruter]
        pxy = np.array([[r[1], r[2]] for r in ruter])
        print(f"{len(ider)} ruter")

        resultat, sjø, storskog = beregn(les, g, xy, gyldige, tre, pxy)

        def heltall(v):
            return None if not np.isfinite(v) else int(round(v))

        rader = []
        for j, sid in enumerate(ider):
            rader.append((sid, *(heltall(resultat[k][j]) for k in KATEGORIER), heltall(sjø[j]), heltall(storskog[j])))
        with conn.cursor() as cur:
            for s in range(0, len(rader), 5000):
                cur.executemany(
                    """insert into rute_avstand (ssbid, gange_tog_m, gange_tbane_m, gange_buss_m, gange_dagligvare_m,
                         gange_skole_m, gange_barnehage_m, luft_sjo_m, luft_storskog_m, oppdatert)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                       on conflict (ssbid) do update set gange_tog_m = excluded.gange_tog_m,
                         gange_tbane_m = excluded.gange_tbane_m, gange_buss_m = excluded.gange_buss_m,
                         gange_dagligvare_m = excluded.gange_dagligvare_m, gange_skole_m = excluded.gange_skole_m,
                         gange_barnehage_m = excluded.gange_barnehage_m, luft_sjo_m = excluded.luft_sjo_m,
                         luft_storskog_m = excluded.luft_storskog_m, oppdatert = now()""",
                    rader[s:s + 5000],
                )
            cur.execute(
                "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
                ("OpenStreetMap (Geofabrik)", "Gangavstander og natur per rute", len(rader),
                 ", ".join(f"{k}: {len(v)}" for k, v in les.mål.items())),
            )
        conn.commit()
        print(f"Ferdig: {len(rader)} ruter oppdatert på {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
