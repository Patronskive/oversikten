"""Henter grenser for grunnkretser og delområder i regionen fra Kartverkets WFS og lagrer dem i Supabase.

Kilde: https://wfs.geonorge.no/skwms1/wfs.grunnkretser (CC BY 4.0, SSB/Kartverket).
Tjenesten støtter ikke paging, så den spørres per kommune med kartutsnitt.
"""
import os
import re
import sys
import tempfile

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import MultiPolygon, Polygon

from ssb_rutenett import UA, finn_kolonne, koble_til, kommunepolygoner

WFS = "https://wfs.geonorge.no/skwms1/wfs.grunnkretser"


def hent_lag(lag, bbox):
    minx, miny, maxx, maxy = bbox
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": lag, "srsName": "urn:ogc:def:crs:EPSG::25833",
        "bbox": f"{minx},{miny},{maxx},{maxy},urn:ogc:def:crs:EPSG::25833",
    }
    r = requests.get(WFS, params=params, headers=UA, timeout=600)
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as f:
        f.write(r.content)
        sti = f.name
    try:
        gdf = gpd.read_file(sti)
    except Exception as e:
        print(f"  kunne ikke lese svar for {lag}: {e}\n  {r.text[:300]}")
        return gpd.GeoDataFrame()
    finally:
        os.unlink(sti)
    if gdf.crs is None:
        gdf = gdf.set_crs(25833)
    elif gdf.crs.to_epsg() != 25833:
        gdf = gdf.to_crs(25833)
    return gdf


def som_multi(g):
    if g is None:
        return None
    if isinstance(g, Polygon):
        return MultiPolygon([g])
    if isinstance(g, MultiPolygon):
        return g
    deler = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]
    return MultiPolygon(deler) if deler else None


def main():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")
    with koble_til(url) as conn:
        nummer = [r[0] for r in conn.execute("select kommunenr from kommune where i_region order by 1")]
        kommuner = kommunepolygoner(nummer)

        gk_deler, do_deler = [], []
        for _, k in kommuner.iterrows():
            bbox = k.geometry.bounds
            gk = hent_lag("app:Grunnkrets", bbox)
            do = hent_lag("app:Delområde", bbox)
            print(f"{k.kommunenr}: {len(gk)} grunnkretser, {len(do)} delområder i utsnittet")
            gk_deler.append(gk)
            do_deler.append(do)

        gk = pd.concat([d for d in gk_deler if not d.empty], ignore_index=True)
        do = pd.concat([d for d in do_deler if not d.empty], ignore_index=True)
        print(f"Kolonner grunnkrets: {list(gk.columns)}")
        print(f"Kolonner delområde: {list(do.columns)}")

        gk_nr = finn_kolonne(gk.columns, [r"^grunnkretsnummer$", r"grunnkretsnr", r"grunnkrets.*nummer"])
        gk_navn = finn_kolonne(gk.columns, [r"^grunnkretsnavn$", r"grunnkrets.*navn"])
        do_nr = finn_kolonne(do.columns, [r"^delområdenummer$", r"delomr.*nummer", r"delomr.*nr"])
        do_navn = finn_kolonne(do.columns, [r"^delområdenavn$", r"delomr.*navn"])
        if not gk_nr or not do_nr:
            sys.exit("Fant ikke nummerkolonner – se kolonnelistene over.")

        prefiks = tuple(nummer)
        gk = gk[gk[gk_nr].astype(str).str.startswith(prefiks)].drop_duplicates(subset=[gk_nr])
        do = do[do[do_nr].astype(str).str.startswith(prefiks)].drop_duplicates(subset=[do_nr])
        print(f"{len(gk)} grunnkretser og {len(do)} delområder i regionen")

        with conn.cursor() as cur:
            rader = []
            for _, r in do.iterrows():
                g = som_multi(r.geometry)
                if g is None:
                    continue
                nr = str(r[do_nr])
                rader.append((nr, str(r[do_navn]) if do_navn else None, nr[:4], g.wkt))
            cur.executemany(
                """insert into delomrade (delomradenr, navn, kommunenr, geom)
                   values (%s, %s, %s, extensions.st_geomfromtext(%s, 25833))
                   on conflict (delomradenr) do update set navn = excluded.navn,
                     kommunenr = excluded.kommunenr, geom = excluded.geom""",
                rader,
            )
            print(f"Lagret {len(rader)} delområder")

            rader = []
            for _, r in gk.iterrows():
                g = som_multi(r.geometry)
                if g is None:
                    continue
                nr = str(r[gk_nr])
                rader.append((nr, str(r[gk_navn]) if gk_navn else None, nr[:4], nr[:6] + "00", g.wkt))
            cur.executemany(
                """insert into grunnkrets (grunnkretsnr, navn, kommunenr, delomradenr, geom)
                   values (%s, %s, %s, %s, extensions.st_geomfromtext(%s, 25833))
                   on conflict (grunnkretsnr) do update set navn = excluded.navn, kommunenr = excluded.kommunenr,
                     delomradenr = excluded.delomradenr, geom = excluded.geom""",
                rader,
            )
            print(f"Lagret {len(rader)} grunnkretser")
            cur.execute(
                "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
                ("Kartverket WFS", "Grunnkretser og delområder", len(rader), f"{len(do)} delområder"),
            )
        conn.commit()


if __name__ == "__main__":
    main()
