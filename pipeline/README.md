# Stedsdata

Henter åpne stedsdata for områdefinneren og stedsprofilen i Johans guide, og lagrer dem i Supabase-prosjektet `stedsassistent` (PostGIS).

## Kjøre innhentingen

1. Legg inn tilkoblingen til databasen som secret i repoet: **Settings → Secrets and variables → Actions → New repository secret**, navn `SUPABASE_DB_URL`. Verdien finner du i Supabase under **Connect → Session pooler** (husk å sette inn passordet).
2. Gå til **Actions → Stedsdata → Run workflow** og velg steg:
   - `tabeller`: lister SSB-tabeller på grunnkrets- og bydelsnivå (trenger ingen secret)
   - `rutenett`: henter befolkning på 250 m-ruter for regionen
   - `alt`: begge

## Datakilder

| Datasett | Kilde | Lisens |
| --- | --- | --- |
| Befolkning på 250 m-rutenett | SSB WFS (ogc.ssb.no) | NLOD |
| Kommunegrenser | Kartverket kommuneinfo-API | CC BY 4.0 |

## Tabeller i databasen

- `kommune`: kommunene i regionen (Oslo, Akershus, Østfold)
- `rute250`: én rad per 250 m-rute med bosatte og øvrige SSB-felt i `attrs`
- `grunnkrets`, `grunnkrets_stat`: klare for neste steg
- `kilde_logg`: når hvert datasett sist ble hentet
