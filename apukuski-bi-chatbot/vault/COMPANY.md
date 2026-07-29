# COMPANY — Apukuski

Apukuski (Apukuski Logistics / Hybrid Ventures Oy, Y-tunnus 3130645-9,
Mäntyhaantie 4 A 12, 33800 Tampere) on suomalainen muutto- ja
kuljetuspalvelu. Toimintaa etupäässä Pirkanmaalla ja Uudellamaalla,
myös Varsinais-Suomessa.

**Palvelut:** muutot, tavarakuljetukset, kierrätyspalvelu, pakkausmateriaalit,
pianosiirrot (maantasalta maantasalle).

**Toimintamalli:** Apukuski hoitaa myynnin, asiakaspalvelun ja keikkojen
koordinaation; työn tekevät omat kuskit ja kumppaniyritykset (hubit).

**Järjestelmät:**
- **Backoffice API** (AWS Lambda) — live totuuslähde tilauksille
- **Supabase** — synkronoitu analytiikkakanta (N8N pipeline, repo:
  `api_data_parser`)
- **N8N** — scheduled sync hot/warm/cool
- **Lovable** — operatiivinen dashboard

**AI-tiimi:** Aada (asiakaspalvelu), Patu (kumppanit), Veeti (puhelin),
Eetu (sähköposti), Veera (WhatsApp) — ja minä, Bertta (BI).

Yhteystiedot: info@apukuski.com | 044 7747744 | apukuski.com
