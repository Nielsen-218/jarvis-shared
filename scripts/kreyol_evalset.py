"""A fixed offline set of everyday Haitian Creole questions and requests, written for measuring how often the local
model's Creole reply is replaced by the canned fallback (scripts/kreyol_eval2.py). Synthetic: none of it is the owner's
logged speech. Each item is (category, line, prime): prime is an earlier line of the same conversation (not scored) that
a follow-up needs, or "". Spelling variants on purpose: some lines have no accents, some mix French habits.
"""

ITEMS = [
    # food
    ("food", "Kisa m ta dwe manje aswè a?", ""),
    ("food", "Ki jan pou m fè diri kole?", ""),
    ("food", "Eske ou ka ban m yon lide pou dine?", ""),
    ("food", "Mwen gen ze ak tomat, kisa mwen ka fè avèk yo?", ""),
    ("food", "Konbyen tan pou m kwit pwa wouj?", ""),
    # time
    ("time", "Ki lè li ye kounye a?", ""),
    ("time", "Ki jou nou ye jodi a?", ""),
    ("time", "Konbyen tan ki rete anvan minwi?", ""),
    ("time", "Eske li twò ta pou m rele yon zanmi?", ""),
    # weather talk
    ("weather", "Ki jan tan an ye deyò?", ""),
    ("weather", "Eske ou panse lapli ap tonbe demen?", ""),
    ("weather", "Poukisa li fè cho konsa nan mwa out?", ""),
    ("weather", "Mwen pa renmen frèt la, ki sa pou m fè?", ""),
    # family
    ("family", "Kijan pou m fè manman m kontan pou fèt li?", ""),
    ("family", "Pitit mwen pa vle manje legim, kisa pou m fè?", ""),
    ("family", "Frè m ap vin wè nou dimanch, kisa nou ka fè ansanm?", ""),
    ("family", "Kisa m ta ka achte pou kado Nwèl madanm mwen?", ""),
    # work
    ("work", "Mwen gen yon reyinyon demen maten, kijan pou m prepare m?", ""),
    ("work", "Patwon an mande m travay plis, kisa pou m di li?", ""),
    ("work", "Kijan pou m òganize jounen travay mwen?", ""),
    ("work", "Mwen bouke ak travay la, ou gen yon konsèy?", ""),
    # school
    ("school", "Mwen gen yon egzamen vandredi, kijan pou m etidye?", ""),
    ("school", "Kisa pou m fè pou m konprann matematik pi byen?", ""),
    ("school", "Eske ou ka ede m ak devwa mwen?", ""),
    ("school", "Ki pi bon fason pou m aprann yon nouvo lang?", ""),
    # feelings
    ("feelings", "Mwen santi m sèl jodi a.", ""),
    ("feelings", "Mwen enkyete m anpil pou demen.", ""),
    ("feelings", "Mwen kontan paske mwen jwenn yon nouvo travay!", ""),
    ("feelings", "Mwen fache epi mwen pa konnen kisa pou m fè.", ""),
    # how / why
    ("howwhy", "Poukisa lanmè a sale?", ""),
    ("howwhy", "Kijan avyon ka vole?", ""),
    ("howwhy", "Poukisa nou bezwen dòmi?", ""),
    ("howwhy", "Kisa ki fè lalin lan chanje fòm?", ""),
    ("howwhy", "Kijan pou m fè yon bon kafe?", ""),
    # follow-ups (the prime line comes first, only the follow-up is scored)
    ("followup", "E si m pa gen diri?", "Kisa m ta dwe manje aswè a?"),
    ("followup", "Konbyen tan sa ap pran?", "Mwen vle fè yon gato pou fèt la."),
    ("followup", "Poukisa sa?", "Mwen panse mwen ta dwe dòmi bonè."),
    ("followup", "Ou ka esplike m sa pi senp?", "Poukisa syèl la ble?"),
    ("followup", "Ak apre sa?", "Mwen fèk fini travay mwen."),
    # no accents typed, mixed spelling
    ("noaccent", "Kisa m ta dwe fe pou dine aswe a?", ""),
    ("noaccent", "Ki jan mwen ka dòmi pi byen? Mwen pa konn dòmi anyen.", ""),
    ("noaccent", "Poukisa mwen toujou fatige apre manje midi?", ""),
    ("noaccent", "Eske ou konnen ki lè mache a louvri?", ""),
    ("noaccent", "Ban m yon ti konsey pou m ekonomize kob.", ""),
    ("noaccent", "Mwen vle ale nan lopital la, ki kote li ye?", ""),
    # mixed Creole and French habits
    ("mixed", "Mwen bezwen aprann kijan pou m fè yon bon prezantasyon.", ""),
    ("mixed", "Kisa ou konseye m pou m fè avèk lajan m?", ""),
    ("mixed", "Eske li posib pou m vwayaje san paspò?", ""),
    ("mixed", "Ki diferans ant yon ekspè ak yon debutan?", ""),
]
