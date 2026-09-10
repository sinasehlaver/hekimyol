"""Clickable common-complaint starting points for the patient intake screen.

Plain data, not a pathway: `pathways.load()` validates every file in `pathways/`
as a full decision graph, so this list lives here instead. Each entry pre-fills the
free-text complaint box and pre-selects a pathway so the patient can proceed with
a single tap. `pathway_slug` must be one of the loaded pathway slugs
(chest_pain | headache | abdominal_pain | dyspnea).
"""

STARTING_POINTS: list[dict] = [
    {"label": "Göğsümde ağrı var",
     "complaint": "Göğsümde ağrı var.",
     "pathway_slug": "chest_pain"},
    {"label": "Göğsümde baskı/sıkışma hissediyorum",
     "complaint": "Göğsümde baskı ve sıkışma hissediyorum.",
     "pathway_slug": "chest_pain"},
    {"label": "Başım ağrıyor",
     "complaint": "Başım ağrıyor.",
     "pathway_slug": "headache"},
    {"label": "Aniden çok şiddetli baş ağrım oldu",
     "complaint": "Aniden çok şiddetli bir baş ağrım oldu.",
     "pathway_slug": "headache"},
    {"label": "Karnım ağrıyor",
     "complaint": "Karnım ağrıyor.",
     "pathway_slug": "abdominal_pain"},
    {"label": "Karnımın sağ alt tarafında ağrı var",
     "complaint": "Karnımın sağ alt tarafında ağrı var.",
     "pathway_slug": "abdominal_pain"},
    {"label": "Nefes almakta zorlanıyorum",
     "complaint": "Nefes almakta zorlanıyorum.",
     "pathway_slug": "dyspnea"},
    {"label": "Yürüyünce nefesim daralıyor",
     "complaint": "Yürüyünce nefesim daralıyor.",
     "pathway_slug": "dyspnea"},
]
