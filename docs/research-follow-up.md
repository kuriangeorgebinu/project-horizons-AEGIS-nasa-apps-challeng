# Research follow-up

Before making public-facing NASA or health claims, review these items:

- In `eval/run_eval.py`, correct or clarify the scenario label that calls the NASA SPE limit “250 mSv.” The README and `aegis_mission.py` describe a 30-day limit in mGy-Eq.
- Describe IL-6 alert rules as modeled demo heuristics. The project draws on Inspiration4 data, but this does not clinically validate the live model or its alerts.
- Keep Curiosity RAD’s 1.84 mSv/day figure framed as a mission-model background value, not a live individual sensor reading.
- Clarify that this project’s AEGIS name is its own acronym; NASA/JPL uses AEGIS for a separate Mars rover system.

Sources:
- NASA human performance standard: https://www.nasa.gov/reference/4-0-human-performance/
- NASA/JPL Curiosity radiation measurements: https://www.jpl.nasa.gov/news/data-from-nasa-rovers-voyage-to-mars-aids-planning/
- NASA OSDR Inspiration4 study OSD-575: https://osdr.nasa.gov/bio/repo/data/studies/OSD-575
- NASA/JPL AEGIS rover project: https://superintelligence.jpl.nasa.gov/public/projects/aegis/
- Project Horizons challenge: https://www.spaceappschallenge.org/2026/find-a-team/project-horizons/?tab=details
