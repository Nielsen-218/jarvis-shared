"""The reading script for the owner's Haitian Creole voice recording (app/voice-record.html, core/kreyol_record).

About 130 short everyday sentences an assistant says, written in standard Creole spelling and chosen to cover the
sounds of the language: the nasal vowels (an, en, on, oun), ou, ui, ch, j, r, tch and dj, è and ò against e and o,
and the short forms (m ap, l ap, n ap, k ap, pa t). Numbers are written out, because the voice reads letters only.
He is the native speaker: every sentence can be corrected on the page before it is recorded, and his wording is
what the voice is trained on. An id never changes once recordings exist; a new sentence gets a new id at the end.
"""

_LINES = """
Bonjou, kijan ou ye jodi a?
Mwen kontan wè w ankò jodi a.
Mwen se asistan pèsonèl ou.
Ki lè li ye kounye a?
Tanpri, limen lanp nan chanm nan.
Fèmen pòt la dèyè ou, tanpri.
Mete mizik la yon ti jan pi fò.
Mwen bezwen yon ti bagay pou manje.
Demen, ap gen lapli nan Pòtoprens.
Pale ak mwen dousman, tanpri.
Mwen pral sòti bonè nan maten.
Ou gen yon nouvo mesaj.
Èske ou vle tande nouvèl yo?
Lapli a ap tonbe depi maten.
Chat la dòmi sou kanape a.
Liv sa a te bèl anpil.
Nou manje diri ak pwa nan mitan jounen an.
Timoun yo ap jwe nan lakou a.
Mwen te wè yon zwazo sou pyebwa a.
Dlo a frèt anpil kounye a.
Machin nan pa mache byen.
Ki kote sa a ye?
Se yon bon lide sa a.
Nou ale nan mache a chak samdi.
Madanm nan ap vann fwi fre.
Ti gason an kouri vit nan lari a.
Papa m travay nan yon gwo bilding.
Lekòl la louvri a uitè nan maten.
Mwen gen yon randevou a dizè.
Tanpri, rele m lè manje a pare.
Sa sanble yon bon istwa.
Li ekri yon lèt pou zanmi l yo.
Tanpri, pa bliye chaje telefòn nan.
Ou ka poze m nenpòt kesyon.
Mwen pa dakò ak sa.
Jodi a, mwen santi m an fòm.
Gade solèy la k ap kouche.
Mèsi anpil pou èd ou.
Bòn nwit, dòmi byen, n a wè demen.
Mwen pral chèche yon bon bagay pou ou.
Li fè twazè edmi nan apremidi.
Jodi a se lendi, premye jou nan semèn nan.
Madi ak mèkredi, mwen gen anpil travay.
Jedi swa, nou pral manje deyò.
Vandredi se dènye jou travay la.
Dimanch maten, tout fanmi an al legliz.
Nou nan mwa janvye, li fè fre.
Fèt ou se nan mwa jiyè.
Mwa septanm, oktòb ak novanm pase vit.
Nan mwa desanm, nou fete Nwèl ansanm.
En, de, twa, kat, senk, sis, sèt.
Uit, nèf, dis, onz, douz, trèz, katòz.
Kenz, sèz, disèt, dizuit, diznèf, ven.
Sa koute trannsenk dola ak senkant santim.
Gen karant moun nan sal la.
Li gen swasant an ane sa a.
Nou te mache katreven kilomèt nan de jou.
San moun te vini nan fèt la.
Sa fè de mil senk san goud.
Reveye m a sizè nan maten, tanpri.
Mwen mete alam nan pou setè eka.
Ou gen yon reyinyon nan dis minit.
Pa bliye pran medikaman ou apre manje.
M ap fè ou sonje sa demen maten.
Fòk ou rele manman ou aswè a.
Mwen ajoute pen ak lèt sou lis la.
Randevou doktè a se jedi a dezè.
Jodi a, li fè cho anpil deyò.
Syèl la klè, pa gen nyaj ditou.
Van an ap soufle fò bò lanmè a.
Pran parapli ou, lapli pral tonbe talè.
Tanperati a se ven degre kounye a.
Aswè a, ap gen loraj ak zeklè.
Demen maten, solèy la ap leve bonè.
Wi, mwen konprann sa ou di a.
Non, mwen pa t tande ou byen.
Dakò, m ap fè sa touswit.
Se fèt, mwen fin voye mesaj la.
Pa gen pwoblèm, m ap okipe sa.
Eskize m, èske ou ka repete sa?
Mwen pa sèten, kite m verifye anvan.
Li pa t la lè mwen rele l.
L ap vini pita, li di m sa.
Nou pa t konnen ou te malad.
Yo pa t janm di m anyen.
M ap tann ou devan pòt la.
N ap pale de sa demen, si Bondye vle.
Ki jan yo rele ou, souple?
Ki kote ou rete kounye a?
Konbyen sa koute nan boutik la?
Poukisa ou pa t vini yè swa?
Kilès ki te rele m talè a?
Kisa ou ta renmen manje aswè a?
Èske ou bezwen èd ak devwa a?
Ki dènye fwa ou te wè li?
Kouman jounen ou te pase jodi a?
Tcheke si pòt la byen fèmen.
Match foutbòl la ap kòmanse a senkè.
Djòb sa a mande anpil kouraj.
Mwen renmen manje diri djondjon ak pwason.
Grann mwen konn fè bon tchaka chak dimanch.
Kuizin nan santi bon, y ap kuit vyann.
Lannuit lan long, men jou a ap louvri.
Jodi a fè uit jou depi li pati.
Gen yon moun k ap frape nan pòt la.
Chak moun gen yon bagay pou yo di.
Joujou timoun yo toupatou nan chanm nan.
Jaden an plen flè wouj ak jòn.
Chofè a chanje wou machin nan byen vit.
Chen an ap jape chak fwa li wè chat la.
Rara a ap pase nan lari a ak tanbou.
Rivyè a gwo anpil apre gwo lapli a.
Radyo a ap bay bon mizik konpa.
Bonswa tout moun, mwen kontan nou la.
Bon apremidi, kòman fanmi an ye?
Sak pase? N ap boule, mèsi.
Orevwa, n a wè pita si Dye vle.
Pase yon bon jounen, pran swen tèt ou.
Frè m ak sè m rete tou pre lekòl la.
Li bo manman l anvan li al dòmi.
Gwo pòt la fèmen, men ti pòt la ouvè.
Mete kle a sou tab la, bò kote telefòn nan.
Lang kreyòl la se lang manman mwen.
Yon moun bezwen konprann anvan li reponn.
Kouzen mwen an vann pen chak maten.
Tonton m gen onz kochon ak yon bèf.
Mwen ka ede ou jwenn nenpòt enfòmasyon ou bezwen.
Si ou vle, mwen ka li nouvèl yo pou ou.
Mwen pa jwenn repons lan, men m ap kontinye chèche.
Lè ou fini, jis di m, epi m ap fèmen tout bagay.
"""

SENTENCES: list[dict] = [{"id": f"s{n:03d}", "text": line.strip()}
                         for n, line in enumerate((l for l in _LINES.splitlines() if l.strip()), 1)]

# Six sentences that are NOT in the script: the trained voice has never heard them, so they are a fair listen of the
# stock voice against the tuned one (scripts/kreyol_finetune.py writes both to WAV files).
AB_SENTENCES = [
    "Bonjou, mwen se Jarvis, asistan ou.",
    "Li fè dizè eka nan maten.",
    "Demen ap gen lapli, pran parapli ou.",
    "Mwen pa t konprann, èske ou ka repete?",
    "Ou gen de mesaj ak yon randevou jodi a.",
    "Dakò, m ap fèmen limyè yo kounye a.",
]

TARGET_SECONDS = 20 * 60          # about twenty minutes of kept takes
