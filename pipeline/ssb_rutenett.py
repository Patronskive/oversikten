"""Henter SSBs befolkning på 250 m-rutenett for kommunene i regionen og lagrer i Supabase.

Kilde: SSBs WFS (https://ogc.ssb.no/wfs.ashx), NLOD-lisens.
Kommunegrenser: Kartverkets kommuneinfo-API.
Krever miljøvariabelen SUPABASE_DB_URL (Postgres-tilkobling, session pooler).
"""
import datetime as dt
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
import psycopg
import requests
from psycopg.types.json import Jsonb
from shapely.geometry import MultiPolygon, shape

WFS = "https://ogc.ssb.no/wfs.ashx"
KOMMUNEINFO = "https://ws.geonorge.no/kommuneinfo/v1/kommuner/{nr}/omrade"
UA = {"User-Agent": "stedsassistent/0.1 (privat prosjekt)"}
SIDE = 10000


def lokal(tag):
    return tag.rsplit("}", 1)[-1]


def velg_lag(capabilities_xml):
    """Finner nyeste lag med befolkning på 250 m-rutenett i GetCapabilities."""
    rot = ET.fromstring(capabilities_xml)
    kandidater = []
    for ft in rot.iter():
        if lokal(ft.tag) != "FeatureType":
            continue
        navn = tittel = ""
        for barn in ft:
            if lokal(barn.tag) == "Name":
                navn = (barn.text or "").strip()
            elif lokal(barn.tag) == "Title":
                tittel = (barn.text or "").strip()
        tekst = f"{navn} {tittel}".lower()
        if "befolk" in tekst and "250" in tekst and "1000" not in tekst and "5000" not in tekst:
            full = re.findall(r"(?:19|20)\d{2}", tittel or navn)
            kandidater.append((int(full[-1]) if full else 9999, navn, tittel))
    if not kandidater:
        raise SystemExit("Fant ikke noe lag for befolkning på 250 m-rutenett i WFS-tjenesten.")
    kandidater.sort()
    for k in kandidater:
        print(f"  kandidat: {k[1]}  ({k[2]})")
    valgt = kandidater[-1]
    print(f"Valgt lag: {valgt[1]} ({valgt[2]})")
    return valgt


def kommunepolygoner(nummer):
    rader = []
    for nr in nummer:
        r = requests.get(KOMMUNEINFO.format(nr=nr), params={"utkoordsys": 25833}, headers=UA, timeout=60)
        r.raise_for_status()
        j = r.json()
        rader.append({"kommunenr": nr, "geometry": shape(j.get("omrade", j))})
    gdf = gpd.GeoDataFrame(rader, geometry="geometry")
    minx = gdf.total_bounds[0]
    if abs(minx) < 200:  # kom tilbake som lengde/bredde, ikke UTM
        gdf = gdf.set_crs(4258).to_crs(25833)
    else:
        gdf = gdf.set_crs(25833)
    return gdf


def hent_ruter(lagnavn, bbox):
    minx, miny, maxx, maxy = bbox
    deler, sett = [], set()
    start = 0
    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": lagnavn, "srsName": "urn:ogc:def:crs:EPSG::25833",
            "bbox": f"{minx},{miny},{maxx},{maxy},urn:ogc:def:crs:EPSG::25833",
            "count": SIDE, "startIndex": start,
        }
        r = requests.get(WFS, params=params, headers=UA, timeout=600)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as f:
            f.write(r.content)
            sti = f.name
        try:
            gdf = gpd.read_file(sti)
        except Exception as e:  # tom side eller feilmelding fra tjenesten
            print(f"Kunne ikke lese side fra {start}: {e}")
            print(r.text[:500])
            break
        finally:
            os.unlink(sti)
        if gdf.empty:
            break
        nøkkel = tuple(gdf.iloc[0].astype(str).tolist()[:3])
        if nøkkel in sett:  # tjenesten ignorerer startIndex
            break
        sett.add(nøkkel)
        deler.append(gdf)
        print(f"  hentet {len(gdf)} ruter (fra {start})")
        if len(gdf) < SIDE:
            break
        start += SIDE
    if not deler:
        raise SystemExit("WFS-tjenesten returnerte ingen ruter.")
    alle = pd.concat(deler, ignore_index=True)
    return gpd.GeoDataFrame(alle, geometry="geometry", crs=deler[0].crs)


def finn_kolonne(kolonner, mønstre, utelat=()):
    for m in mønstre:
        for k in kolonner:
            if re.search(m, k, re.I) and k not in utelat:
                return k
    return None


def main():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")
    with psycopg.connect(url, prepare_threshold=None) as conn:
        nummer = [r[0] for r in conn.execute("select kommunenr from kommune where i_region order by 1")]
        print(f"{len(nummer)} kommuner i regionen")

        kommuner = kommunepolygoner(nummer)
        bbox = tuple(kommuner.total_bounds)

        cap = requests.get(WFS, params={"service": "WFS", "request": "GetCapabilities"}, headers=UA, timeout=120)
        cap.raise_for_status()
        aar_lag, lagnavn, tittel = velg_lag(cap.content)

        ruter = hent_ruter(lagnavn, bbox)
        if ruter.crs is None:
            ruter = ruter.set_crs(25833)
        elif ruter.crs.to_epsg() != 25833:
            ruter = ruter.to_crs(25833)
        print(f"Kolonner fra SSB: {list(ruter.columns)}")

        id_kol = finn_kolonne(ruter.columns, [r"^ssbid", r"ru250", r"ruteid", r"^id$"], utelat=("gml_id",))
        pop_kol = finn_kolonne(ruter.columns, [r"pop_tot", r"bosatt", r"befolk", r"antall", r"^pop"])
        aar_kol = finn_kolonne(ruter.columns, [r"^aar$", r"^year$", r"statistikkaar"])
        if not id_kol or not pop_kol:
            sys.exit(f"Fant ikke id- eller befolkningskolonne. Kolonner: {list(ruter.columns)}")
        print(f"Bruker id={id_kol}, befolkning={pop_kol}, år={aar_kol or tittel}")

        sentre = ruter.copy()
        sentre["geometry"] = ruter.geometry.representative_point()
        koblet = gpd.sjoin(sentre, kommuner, how="inner", predicate="within")
        ruter = ruter.loc[koblet.index].assign(kommunenr=koblet["kommunenr"].values)
        print(f"{len(ruter)} ruter ligger i regionen")

        standard_aar = aar_lag if aar_lag != 9999 else dt.date.today().year
        ekstra = [k for k in ruter.columns if k not in (id_kol, pop_kol, aar_kol, "geometry", "kommunenr", "gml_id")]

        rader = []
        for _, rad in ruter.iterrows():
            g = rad.geometry
            if isinstance(g, MultiPolygon) and len(g.geoms) == 1:
                g = g.geoms[0]
            if g.geom_type != "Polygon":
                continue
            attrs = {}
            for k in ekstra:
                v = rad[k]
                if pd.notna(v):
                    attrs[k] = v.item() if hasattr(v, "item") else (v if isinstance(v, (int, float, str)) else str(v))
            aar = int(rad[aar_kol]) if aar_kol and pd.notna(rad[aar_kol]) else standard_aar
            pop = int(rad[pop_kol]) if pd.notna(rad[pop_kol]) else None
            rader.append((str(rad[id_kol]), rad["kommunenr"], g.wkt, aar, pop, Jsonb(attrs)))

        sql = """
            insert into rute250 (ssbid, kommunenr, geom, aar, bosatte, attrs)
            values (%s, %s, extensions.st_geomfromtext(%s, 25833), %s, %s, %s)
            on conflict (ssbid) do update set
              kommunenr = excluded.kommunenr, geom = excluded.geom, aar = excluded.aar,
              bosatte = excluded.bosatte, attrs = excluded.attrs
        """
        with conn.cursor() as cur:
            for i in range(0, len(rader), 2000):
                cur.executemany(sql, rader[i:i + 2000])
                print(f"  lagret {min(i + 2000, len(rader))}/{len(rader)}")
            cur.execute(
                "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
                ("SSB WFS", lagnavn, len(rader), tittel),
            )
        conn.commit()
        print(f"Ferdig: {len(rader)} ruter lagret.")


if __name__ == "__main__":
    main()
