"""A Haitian Creole phrase bank and reply check for the local model, whose Creole drifts into invented words.

NEEDS THE OWNER'S CORRECTION. He is the native speaker; these sentences were written carefully in standard
orthography but not by one. Every line he corrects ("yo pa di sa, yo di ...", "you should say ...") is kept in his own
bank (data/users/<uid>/kreyol-bank.json) and wins over what is written here.

Three uses, all local and all in code:
  relevant(said)   the handful of bank sentences closest to his line: few-shot grounding in the per-request language
                   note (audio.language.reply_note), never in the cached prompt.
  check(reply)     the words of a reply that are not Creole: French, Spanish or English forms (spelling Creole never
                   uses, or a word of those languages) and words in no list here (possibly invented).
  fallback(...)    what is said when a reply is still bad after one more try: its clean sentences, or a short plain
                   sentence from the bank. Never silence, never nonsense.
The bank is built from PAIRS below, the built-in course (core/lang_curriculum) and the recording script
(core/kreyol_script). The wordlist is all their words plus WORDS. A word list is a rough judge: a real but rarer word
is flagged as unknown too, which costs one more try in simpler words, the right direction for a model weak in Creole.
"""
from __future__ import annotations

import json
import math
import re
import time
from functools import lru_cache

# Creole | English. Everyday sentences an assistant hears and says.
PAIRS = """
Bonjou, kijan ou ye? | Hello, how are you?
Mwen byen, mèsi. E ou menm? | I am fine, thank you. And you?
Mwen la, m ap kenbe. | I am here, hanging on.
Sak pase? | What's up?
N ap boule. | We are getting by.
Bonswa, kijan jounen an te ye? | Good evening, how was the day?
Jounen an te byen pase. | The day went well.
Mwen kontan tande sa. | I am glad to hear that.
Mwen kontan wè ou. | I am happy to see you.
Sa fè lontan nou pa pale. | It has been a long time since we talked.
Kijan fanmi an ye? | How is the family?
Tout moun anfòm. | Everyone is well.
Mèsi anpil. | Thank you very much.
Pa gen pwoblèm. | No problem.
Pa gen anyen. | It is nothing.
Padon, mwen pa konprann. | Sorry, I do not understand.
Eskize m. | Excuse me.
Tanpri, repete sa. | Please repeat that.
Pale pi dousman, tanpri. | Speak more slowly, please.
Wi, mwen konprann. | Yes, I understand.
Non, mwen pa kwè sa. | No, I do not think so.
Dakò, mwen tande ou. | All right, I hear you.
Se vre. | That is true.
Ou gen rezon. | You are right.
Mwen pa dakò. | I do not agree.
Mwen pa konnen. | I do not know.
Mwen pa sèten. | I am not sure.
Petèt. | Maybe.
Se yon bon lide. | That is a good idea.
Sa bon. | That is good.
Sa pa bon. | That is not good.
Sa enteresan. | That is interesting.
Se pa fot ou. | It is not your fault.
Pa enkyete ou. | Do not worry.
Tout bagay ap byen pase. | Everything will go well.
Pran kouraj. | Take courage.
Mwen la pou ou. | I am here for you.
Mwen ka ede ou. | I can help you.
Kisa ou bezwen? | What do you need?
Kisa ou vle fè? | What do you want to do?
Kisa ou ap fè? | What are you doing?
M ap travay kounye a. | I am working now.
Mwen fini. | I am finished.
Li fèt. | It is done.
Mwen pral fè li demen. | I will do it tomorrow.
Mwen te fè li yè. | I did it yesterday.
Mwen poko fè li. | I have not done it yet.
Mwen fèk fini. | I just finished.
Tann yon ti moman. | Wait a little moment.
M ap vini. | I am coming.
Ann ale. | Let us go.
Ann kòmanse. | Let us start.
Ki lè li ye? | What time is it?
Li fè dizè. | It is ten o'clock.
Li bonè toujou. | It is still early.
Li ta. | It is late.
Ki jou jodi a ye? | What day is it today?
Jodi a se madi. | Today is Tuesday.
Demen se mèkredi. | Tomorrow is Wednesday.
Yè te dimanch. | Yesterday was Sunday.
Mwen fatige anpil. | I am very tired.
Ou bezwen repoze. | You need to rest.
Al dòmi bonè aswè a. | Go to sleep early tonight.
Mwen pa t dòmi byen. | I did not sleep well.
Eseye dòmi pi bonè. | Try to sleep earlier.
Bwè yon ti dlo. | Drink a little water.
Pran yon ti repo. | Take a little rest.
Mwen grangou. | I am hungry.
Mwen swaf. | I am thirsty.
Kisa ou vle manje? | What do you want to eat?
Ou ka kwit diri ak poul. | You can cook rice with chicken.
Diri ak pwa se yon bon manje. | Rice and beans is a good meal.
Manje a pare. | The food is ready.
Manje a gou anpil. | The food is very tasty.
Sa ap pran trant minit. | That will take thirty minutes.
Sa ap pran yon èdtan konsa. | That will take about an hour.
Mete dlo a bouyi. | Put the water to boil.
Lave diri a anvan ou kwit li. | Wash the rice before you cook it.
Mete yon ti sèl ak lwil. | Add a little salt and oil.
Mwen malad. | I am sick.
Tèt mwen ap fè m mal. | My head hurts.
Ou ta dwe wè yon doktè. | You should see a doctor.
Mwen santi m pi byen. | I feel better.
Mwen kontan. | I am happy.
Mwen tris. | I am sad.
Mwen fache. | I am angry.
Mwen pè. | I am afraid.
Mwen renmen sa. | I like that.
Mwen pa renmen sa. | I do not like that.
Mwen renmen ou. | I love you.
Li fè cho jodi a. | It is hot today.
Li fè frèt. | It is cold.
Lapli ap tonbe. | It is raining.
Solèy la cho anpil. | The sun is very hot.
Syèl la ble paske limyè solèy la gaye nan lè a. | The sky is blue because the sunlight spreads in the air.
Lannuit, syèl la nwa. | At night the sky is black.
Gen anpil van deyò a. | There is a lot of wind outside.
Ki kote ou ye? | Where are you?
Mwen lakay mwen. | I am at home.
Mwen nan travay. | I am at work.
Ki kote ou prale? | Where are you going?
Mwen pral nan mache. | I am going to the market.
Li pa lwen. | It is not far.
Li toupre. | It is nearby.
Vire a goch. | Turn left.
Vire a dwat. | Turn right.
Ale tou dwat. | Go straight ahead.
Konbyen sa koute? | How much does that cost?
Li twò chè. | It is too expensive.
Li pa chè. | It is not expensive.
Mwen pa gen kòb. | I have no money.
Konbyen ou genyen? | How many do you have?
Mwen gen de. | I have two.
Kapital Ayiti se Pòtoprens. | The capital of Haiti is Port-au-Prince.
Ayiti se yon bèl peyi. | Haiti is a beautiful country.
Mwen pale kreyòl ak angle. | I speak Creole and English.
Mwen pale yon ti kreyòl. | I speak a little Creole.
M ap aprann toujou. | I am still learning.
Korije m si mwen fè yon erè. | Correct me if I make a mistake.
Kijan yo di sa an kreyòl? | How do you say that in Creole?
Kisa mo sa a vle di? | What does this word mean?
Mwen pa konn mo sa a. | I do not know this word.
Aprann pran tan. | Learning takes time.
Li difisil, men ou ka fè li. | It is hard, but you can do it.
Fè yon ti kras chak jou. | Do a little every day.
Pa dekouraje. | Do not get discouraged.
Ou ap fè pwogrè. | You are making progress.
Kòmanse ak yon ti bagay ki senp. | Start with a small simple thing.
Lè ou fè erè, ou aprann. | When you make mistakes, you learn.
Poze m yon lòt kesyon. | Ask me another question.
Se yon bon kesyon. | That is a good question.
Mwen pa gen repons lan. | I do not have the answer.
Kite m chache li. | Let me look for it.
Mwen jwenn li. | I found it.
Mwen pa jwenn anyen. | I found nothing.
Mwen sonje sa. | I remember that.
Mwen bliye. | I forgot.
Pa bliye sa. | Do not forget that.
M ap fè ou sonje. | I will remind you.
Ou gen yon randevou demen maten. | You have an appointment tomorrow morning.
Ou gen yon nouvo mesaj. | You have a new message.
Pa gen nouvo mesaj. | There are no new messages.
Telefòn nan ap sonnen. | The phone is ringing.
Rele m pita. | Call me later.
M ap rele ou demen. | I will call you tomorrow.
Limyè a limen. | The light is on.
Limyè a etenn. | The light is off.
Mwen limen limyè a. | I turned the light on.
Mwen etenn limyè a. | I turned the light off.
Pòt la fèmen. | The door is closed.
Pòt la louvri. | The door is open.
Mizik la ap jwe. | The music is playing.
Mwen bese volim nan. | I lowered the volume.
Mwen monte volim nan. | I raised the volume.
Mwen mete yon revèy pou sizè. | I set an alarm for six o'clock.
Revèy la pare. | The alarm is ready.
Òdinatè a limen. | The computer is on.
Mwen louvri pwogram nan. | I opened the program.
Mwen fèmen li. | I closed it.
Sa pa mache. | That does not work.
Li mache byen kounye a. | It works well now.
Eseye ankò. | Try again.
Gen yon pwoblèm. | There is a problem.
M ap gade sa. | I am looking at that.
Mwen pa ka fè sa. | I cannot do that.
Mwen ka fè sa pou ou. | I can do that for you.
Ou vle m fè sa? | Do you want me to do that?
Wi, fè li. | Yes, do it.
Non, kite sa. | No, leave that.
Pa kounye a. | Not now.
Pita. | Later.
Talè. | In a moment.
Touswit. | Right away.
Bòn nwit, dòmi byen. | Good night, sleep well.
N a wè demen. | We will see each other tomorrow.
N a pale pita. | We will talk later.
Orevwa, pase yon bon jounen. | Goodbye, have a good day.
Pase yon bon aswè. | Have a good evening.
Pran swen tèt ou. | Take care of yourself.
Kenbe la. | Hang in there.
Bon travay. | Good work.
Bravo, ou fè sa byen. | Well done, you did that well.
Mwen fyè de ou. | I am proud of you.
Ou fè m ri. | You make me laugh.
Sa komik. | That is funny.
Rakonte m yon istwa. | Tell me a story.
Mwen pa konn anpil blag. | I do not know many jokes.
Kijan travay la ye? | How is work?
Travay la ap mache byen. | Work is going well.
Mwen gen anpil travay jodi a. | I have a lot of work today.
Mwen okipe kounye a. | I am busy now.
Mwen lib aswè a. | I am free tonight.
Kisa ou ap fè demen? | What are you doing tomorrow?
Mwen pral lekòl. | I am going to school.
Mwen pral legliz dimanch. | I am going to church on Sunday.
Timoun yo ap jwe deyò. | The children are playing outside.
Manman m ap kwit manje. | My mother is cooking.
Papa m ap travay. | My father is working.
Frè m pi gran pase m. | My brother is older than me.
Sè m rete Miyami. | My sister lives in Miami.
Zanmi m ap vini aswè a. | My friend is coming tonight.
Kijan ou rele? | What is your name?
Mwen rele Jarvis. | My name is Jarvis.
Ki laj ou? | How old are you?
Mwen se asistan ou. | I am your assistant.
Mwen toujou la lè ou bezwen m. | I am always here when you need me.
Di m kisa ki pase. | Tell me what happened.
Esplike m sa. | Explain that to me.
Mwen pral esplike ou sa byen senp. | I will explain that to you very simply.
Premye, fè sa. Apre sa, fè lòt la. | First, do this. After that, do the other one.
Se konsa li ye. | That is how it is.
Sa depann. | That depends.
Mwen panse se yon bon chwa. | I think it is a good choice.
Mwen pito lòt la. | I prefer the other one.
Tou de bon. | Both are good.
Sa pi bon. | That one is better.
Sa pi fasil. | That is easier.
Sa pi difisil. | That is harder.
Li enpòtan anpil. | It is very important.
Li pa enpòtan. | It is not important.
Fè atansyon. | Be careful.
Pa prese. | Do not hurry.
Fè vit. | Hurry up.
Nou gen tan. | We have time.
Nou pa gen tan. | We do not have time.
Dlo a frèt. | The water is cold.
Kafe a cho. | The coffee is hot.
Mwen ta renmen yon tas kafe. | I would like a cup of coffee.
Machin nan pare. | The car is ready.
Kay la pwòp. | The house is clean.
Chanm nan sal. | The room is dirty.
Liv la sou tab la. | The book is on the table.
Kle yo nan pòch ou. | The keys are in your pocket.
Mwen pa wè li. | I do not see it.
Gade isit la. | Look here.
Koute sa. | Listen to this.
Chita la. | Sit here.
Leve kanpe. | Stand up.
Vini isit. | Come here.
Rete la. | Stay there.
Bondye beni ou. | God bless you.
Si Bondye vle. | God willing.
Piti piti, zwazo fè nich li. | Little by little, the bird makes its nest.
Men anpil, chay pa lou. | Many hands make the load light.
"""

# More everyday Creole words (standard spelling) so that a correct word is not taken for an invented one.
WORDS = """
a ak akòz aksepte aksidan aktif ale alèz alo alò amelyore ane anfòm anba anbake anbrase andedan angle anile anime
ankouraje ankò anlè anmè annwiye anonse anpeche anpil ansanm anseye ansyen antann antre anvan anvi anviwon
anyen apa apèl aplike aprann apre apremidi apresye apwoche aranje arete rive asire asistan asosye asyèt
atak atansyon atè atik atmosfè atwosfè avanse avantaj avèk avi avni avril avyon ayè ayewopò ayisyen ayiti azot atòm
babay bagay bal balanse ban bank bat batay bati batri bay baz bebe bèf bèl bese bezwen bib bilding biwo blag blan blanch
ble blese bliye bò bon bonè bonjou bonswa bonte bòs bouch bouje boukan boule bouke boul bous bout boutèy bouton bouyi
bra branch bravo bri briye bwa bwat bwè bwose byen byento byenveni
cha chaj chak chaje chalè chans chanje chanjman chanm chans chante chantè chapo chat chay chè chèf chak chemen chemiz
chen cheri chita chif chache chèche cho chofe chofè chokola chwa chwazi chwal
dakò dan danje danse dat dat de debou defann defo deja dekouraje dekri delivre demach demann demen demi depann
depanse depi deranje desann desen deside desizyon desanm deside detay detwi devan devwa deyò dèyè dezi dezyèm di
diferan diferans difisil difikilte dijans diminye dimanch direksyon diri diskite distans dife divès dizè djòb
dlo do doktè dola domaj dòmi don dous dousman dout douvan douz dwa dwat dwe dwòl dyab dènye
e ebyen ede edmi efò egal egzak egzamen egzanp egzèsis ekip ekonomi ekran ekri ekriti egzakteman elektrik elektrisite
elèv elimine en endepandan enèji enfòmasyon enkyete enpe enpòtan enposib enprime enstale enteresan enterese entènèt
envite epi epis eple erè esè eseye eskiz eskize espas espere espesyal esplikasyon esplike espò estasyon
eta etaj etap etenn etidye etranj evite ewo
fache fanm fanmi fason fasil fatig fatige fè fèb fèk fèmen fen fenèt fèt fevriye fi fidèl figi fil fim fin fini fiti
flè fò fòk fòm fòme fon fonksyon fonksyone fòs foto fou foul frape frè frèt fre fri frijidè fwa fwi fwontyè fyè
gade gaj galon gason gate gato gaye gaz gen genyen gid glas goch gou goute gouvènman grangou gran grandi granmoun
gras grate gratis grav gri gwo gwoup
imaj imajine imedyatman imèl itil itilize ivè istwa isit isit
jaden jamè janm janvye je jedi jèn jenou jete jis jiska jiyè jodi jou jounal jounen jwe jwenn jwèt jwi jen jij jistis
ka kab kabann kache kado kafe kalite kalkile kalm kalme kamyon kanaval kanpe kapab kapasite kapital kat kase kay kaye
kè kèk kenbe kesyon ki kijan kilè kilòmèt kisa kite kiyès klas klè kle klere kliyan kloure kò kòb kochon kòd kole
kolè kòlè kolòn kòm kòman kòmande kòmanse kòmansman konbyen kondi konfime konfyans konn konnen konpayi konplè konplete
konplike konprann konsa konsèy konsève konsidere kont kontan kontinye kontre kontwole konvèsasyon kòrèk korije kote
kou kouche koulè kounye kouraj kouri kous kout koute kouto kouvri kouzen kouzin kras kraze kreye kreyòl kri kriye
kwè kwen kwit kwizin ki
la labank lach lafwa lage lajan laj lakay lakou lalin lame lang lanmè lanmò lannuit lanp lapè lapli lari larivyè
lasante laplas lave lavi le lè legliz legim lekòl lendi lespri lèt leve lib libète lide lis lit liv livre liy li lò
lòd lòt lonbraj long lontan lopital lou louvri lwa lwaye lwen lwil
ma machin mache machann madanm madi magazen majik mal malad maladi malè malerezman manje manke manman manyen mare
mari maryaj mas maten matant matematik mati me mèg mele men menm mennen mens mesaj mesye mèsi mèt mete metòd metye
mèkredi mezanmi mezi mi midi mil milye minit minwi mitan miwa miyò mizè mizik mo mòd modèl mon monn monte montre
moso moun mouri mouye mwa mwayen mwen mwens mwatye mache me
nan nanm nasyon nasyonal natirèl nati ne nesans nesesè nèf nen nenpòt nich nivo nimewo non nòmal nonm nou nouri
nouvo nouvèl novanm nwa nwaj nwit nwèl
objè obligatwa obsève òdinatè òdinè ofri ogmante oke oksijèn oktòb okipe opinyon oswa osi ou out ouvè ouvri oblije
pa padon pafwa paj pak pakèt pale pàn pandan panse pantalon papa papye paran parese parèt pare pasaj pase pasyans
pati patisipe pataje paske pè pèdi pen penti pèmèt pèmisyon pèsòn pèsonèl pèp peye peyi pi pik pil pita pitit
piti pito plan planifye plant plas plat plen plezi plim plis plizyè po pòch pòt pote poko pou poukisa poul poumon
pous pouse pousyè pouvwa poze pran pratik pratike premye prepare prese preske prete prèt prezan prezidan pri priye
pwa pwason pwen pwoblèm pwochen pwodui pwofesè pwofite pwogram pwogramasyon pwograme pwogrè pwojè pwomèt pwomès
pwòp pwoteje pwouve pye pyebwa
rad radyo rakonte ran randevou ranje rankontre ranmase rann rapid rapò rapòte rat rate ravin rayi rekòmanse rekonèt
rele relijyon remèd remèsye renmen repare repete repo reponn repons repoze resevwa resèt respè respekte respire
responsab rès restoran rete retounen rèv reve revèy reyalize reyèl reyini reyisi rezilta rezon rezoud ri rich rive
rivyè wo wòch won wonn wòl wout wouj wouvri rwa
sa sab sak sal sale salon salye samdi san sans sante santi santim sant savon se sè sèk sèl sèlman semèn sen senk
senp septanm sere seri sèten sèvi sèvis sezon si sifi siman sik sikile silans sis sistèm sitiyasyon siy siyen
sizè sèvyèt sijè sosyete sòlda solèy solisyon somèy son sonje sonnen sòt sòti sote sou soufri souke soulye soupe
souri souvan swa swaf swen swiv syèl sitou sipoze sipòte sispann
ta tab tablo tach talè tan tande tankou tann tanpri tante tas te tè tèlman telefòn televizyon tèm tèt ti tig tikè
timoun tire tit tiyo tonbe tonton tou touche touswit tout toujou toupre tounen touye tradiksyon tradui tranble
trankil travay travayè travèse trant trè trete tris twa twazyèm twò twonpe twou tay
vag vakans van vandredi vann vant vè vèb vèt vid vil vin vini vire vit vitès viv vizit vizite vle vòlè volim vwa
vwayaj vwayaje vwazen vyann vye vizaj
wè wi wo wont
yè yo yon youn
ke resèt ant aktivite dirèkteman pwosesis fòse lanse reflete tradisyonèl swete ba pò kiyè lak
bannann douch santiman jape videyo memwa selon jeneral kantite prefere vivan pèsonn twòp klasik pozisyon
bloke fiks netwaye kidonk poutèt relache detann gita ranpli tandiske miray lache akò sifi rantre
lannwit koulye koupe pon manti fizik pike vach bèt tablèt foutbòl match jwè balon chodyè fouchèt bòl sos zonyon lay piman tomat sitwon zoranj mango zaboka kokoye kann pat ze bè fwomaj farin mayi pitimi yanm patat manyòk griyo salad soup bouyon ji byè wonm dwèt cheve gòj koud lafyèv tous grip doulè medikaman grenn
piki enfimyè klinik geri lajwa enkyè strès jalou sezi etone achte voye rale soti naje vole reveye benyen abiye penyen griye kale melanje vide bale siye defèt mezire echwe konte etwat lejè tyèd lèd pòv kwochi fo posib raman segonn loraj zeklè tanpèt siklòn mòn
plaj fèy kabrit kòk bourik sourit koulèv krapo papiyon myèl mouch marengwen foumi klavye rezo modpas aplikasyon dosye
fichye klike telechaje chajè kouran anpoul twalèt mi planche chèz dra zòrye eskalye baryè goud dèt tifi tigason granmè granpè neve nyès menaj patwon anplwaye sekrè
verite donk sepandan malgre sinon olye anplis prèske apeprè vrèman reyèlman ni dans chante jwèt fèt koute
goute pòv tyèd kizin swe frape bwote plante rekòlte simen bouke kontre separe rasanble prepare sèvi itil danjere trankil serye senpleman jantiman tou senpleman kounyeya
zanmi zeb zegwi zepòl zero zèl zetwal zo zòn zòrèy zouti zwazo
uit uitè inè dezè twazè katrè senkè setè nevè onzè douzè mwa
en de twa kat senk sis sèt uit nèf dis onz douz trèz katòz kenz sèz disèt dizuit diznèf ven trant karant senkant
swasant katreven san mil premye dezyèm twazyèm
"""

# Everyday words and loanwords in standard spelling that answers to ordinary questions use (food, time, weather, family,
# work, school, feelings): without them a correct reply was judged as invented words and sent for a retry (measured).
MORE_WORDS = """
dine dejene kolasyon bwason soupe peze fri griye kwit kole sòs sik vinèg makawoni pen ze lèt bè
salata krèm kouti tranpe fèy te
lis nòt rezime plan objektif priyorite reyinyon dokiman kontra salè konje pòz repo tensyon estrès detann medite
depans ekonomize prezantasyon prensipal ekspè paspò ekzamen leson konesans eksperyans reyalite imajinasyon
eksplike eksplikasyon edikasyon inivèsite diplòm valè enpòtans vokabilè gramè pwononse diksyonè kaye kreyon
tanperati degre imid imidite prentan otòn vwazinaj kominote vilaj otobis taksi bato bisiklèt
planèt eklips gravite matyè estomak eksak antrennman pasaje motè bann kant twal pap ete pyès
anglè reponn mande solitid lapenn espwa laperèz enkyetid tristès kontantman felisitasyon konsèy rezon vwayaj ekonomi
jiskaske kèlkeswa epitou epi tou anpil
rapid vit lou lejè long kout wo ba gwo piti gran ti
chimi fizik biyoloji istwa jewografi syans
panse reflechi chèche jwenn voye resevwa kenbe lage pran bay mete wete
"""
EXCHANGES = (
    ("Kisa pou m kwit pou manje midi?", "Ou ka fè diri jòn ak pwason. Se yon bon manje epi li pa lou."),
    ("Ki jan pou m fè te?", "Mete dlo a bouyi, voye l sou fèy te a, epi kite l tranpe senk minit."),
    ("Poukisa m santi m fatige?", "Petèt ou pa dòmi ase oswa ou pa bwè ase dlo. Eseye dòmi bonè aswè a."),
    ("Mwen gen yon entèvyou demen, kisa pou m fè?", "Dòmi bonè, prepare rad ou aswè a, epi rive bonè. Ou pral fè byen."),
    ("Kijan lapli fòme?", "Solèy la chofe dlo a, vapè a monte nan syèl la epi li tounen nwaj. Lè nwaj la lou, lapli tonbe."),
    ("Mwen tris jodi a.", "M regrèt tande sa. Mwen la, di m kisa ki pase."),
    ("Ki lè mache a louvri?", "Mwen pa konnen sa. Ou ka rele yo oswa gade sou entènèt."),
    ("Kisa pou m achte pou fèt zanmi m?", "Yon ti kado senp tankou yon liv oswa yon bèl chemiz ap fè l plezi."),
)

# Words the checker never questions: his assistant's name, places and a few names of things said as they are.
NAMES = set("jarvis ayiti pòtoprens miyami okay ok".split())
SHORT = set("m w l n y k t a e o s".split())             # the short forms: m ap, w ap, l ap, n a, k ap, pa t
_TOKEN = re.compile(r"[a-zàâçéèêëîïôûùüÿœæñáíóúò]+(?:['’-][a-zàâçéèêëîïôûùüÿœæñáíóúò]+)*", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Spelling standard Creole never uses: c without h, q, x, a lone u (not ou, not ui), French accents and endings,
# doubled consonants other than nn, ph, th, oi, ai, eu, au.
_NOT_CREOLE = re.compile(r"c(?!h)|q|x|(?<!o)u(?!i)|[éêâîôûùçœïëà]|ss|ll|tt|mm|rr|pp|ff|ph|th|oi|ai|eu|au|^h|"
                         r"tion$|ment$|eux$|eau$")
_FRENCH_END = re.compile(r"(?:tion|ment|eux|eau|ique|aire|ais|ait|ez|er)s?$")
STORE = "kreyol-bank.json"
MAX_KEPT = 400


def _tokens(text: str) -> list:
    return [t.lower().replace("’", "'") for t in _TOKEN.findall(text or "")]


def _parts(token: str) -> list:
    return [p for p in re.split(r"['-]", token) if p]


@lru_cache(maxsize=1)
def builtin() -> tuple:
    """Every built-in sentence: ({"ht", "en", "source"}, ...). PAIRS first, then the course, then the recording script
    (which has no English: it grounds spelling and words)."""
    out, seen = [], set()

    def add(ht, en, source):
        ht = " ".join(str(ht).split())
        if ht and "..." not in ht and ht.lower() not in seen:
            seen.add(ht.lower())
            out.append({"ht": ht, "en": " ".join(str(en).split()), "source": source})
    for line in PAIRS.strip().splitlines():
        ht, _, en = line.partition("|")
        add(ht, en, "bank")
    try:
        from core import lang_curriculum
        for item in lang_curriculum.items("ht").values():
            for form in item["accept"]:
                if not form.isdigit():
                    add(form, item["english"], "course")
    except Exception:  # noqa: BLE001  (the bank stands without the course)
        pass
    try:
        from core import kreyol_script
        for row in kreyol_script.SENTENCES:
            add(row["text"] if isinstance(row, dict) else row, "", "script")
    except Exception:  # noqa: BLE001
        pass
    return tuple(out)


@lru_cache(maxsize=1)
def _base_words() -> frozenset:
    words = set(WORDS.split()) | set(MORE_WORDS.split()) | NAMES | SHORT
    for question, answer in EXCHANGES:
        for token in _tokens(question + " " + answer):
            words.add(token)
            words.update(_parts(token))
    for row in builtin():
        for token in _tokens(row["ht"]):
            words.add(token)
            words.update(_parts(token))
    try:
        from audio import language
        words |= {w for w in language._SETS["ht"] if not _NOT_CREOLE.search(w)}
    except Exception:  # noqa: BLE001
        pass
    return frozenset(words)


# ---- his own corrections ------------------------------------------------------------------------------------------
def _path(uid=None):
    from core import storage
    return storage.user_dir(uid) / STORE


def kept(uid=None) -> list:
    """His corrections, oldest first: [{"right", "wrong", "about", "at"}]."""
    try:
        rows = json.loads(_path(uid).read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("right")]
    except (OSError, ValueError):
        return []


def count(uid=None) -> int:
    return len(kept(uid))


def learn(right: str, wrong: str = "", about: str = "", uid=None, at=None) -> dict:
    """Keep one corrected phrase in his bank (the same phrase again replaces the older row). Used from then on: it is
    the first example in the language note when his line is near it, its words are Creole to the checker, and the
    wrong wording is replaced wherever a reply uses it."""
    right, wrong = _clean(right), _clean(wrong)
    row = {"right": right, "wrong": wrong, "about": " ".join(str(about or "").split())[:200],
           "at": time.time() if at is None else at}
    rows = [r for r in kept(uid) if r["right"].lower() != right.lower()] + [row]
    path = _path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows[-MAX_KEPT:], ensure_ascii=False, indent=1), encoding="utf-8")
    return row


def _clean(phrase: str) -> str:
    return " ".join(str(phrase or "").split()).strip(" \"'“”‘’«».,;:!")


_THAT = {"", "sa", "sa a", "konsa", "that", "it", "this", "li"}
_SAYS = r"(?:yo|nou|ou|moun|mwen|m)"
_CORRECTIONS = (
    # "Yo pa di 'X', yo di 'Y'."  /  "Nou pa di sa, nou di Y."
    re.compile(rf"\b{_SAYS}\s+pa\s+di\s+(?P<wrong>.+?)\s*[,;.:]\s*(?:se\s+)?{_SAYS}\s+di\s*:?\s+(?P<right>.+)$", re.I),
    # "Pa di X, di Y."  /  "Don't say X, say Y."
    re.compile(r"\b(?:pa\s+di|don'?t\s+say|do\s+not\s+say)\s+(?P<wrong>.+?)\s*[,;.:]\s*(?:di|say)\s*:?\s+(?P<right>.+)$", re.I),
    # "You should say Y."  "In Creole we say Y."  "An kreyòl yo di Y."
    # (Not "ou dwe di ..." / "fòk ou di ...": "ou dwe di m sa ou vle" is "you must tell me what you want".)
    re.compile(r"\b(?:you\s+should(?:\s+have)?\s+sa(?:y|id)|the\s+(?:right|correct|proper)\s+way\s+to\s+say\s+(?:it|that)\s+is|"
               r"in\s+(?:haitian\s+)?creole,?\s+(?:we|you|they|people)\s+say|an\s+krey[oò]l,?\s+" + _SAYS + r"\s+di|"
               r"yo\s+di\s+li\s+konsa)\s*:?\s+(?P<right>.+?)"
               r"(?:\s*[,;]\s*(?:not|pa|e\s+pa|and\s+not)\s+(?P<wrong>.+))?$", re.I),
)


def correction(said: str, creole: bool = False, before: str = ""):
    """(right, wrong) when his line corrects Jarvis's Creole, else None. The corrected phrase must itself be Creole (or
    the conversation is in Creole): "you should say sorry" teaches nothing here. before: what Jarvis said just before.
    A "they do not say X, they say Y" is a correction only when X is "that" or is in what Jarvis just said: "yo pa di
    anyen, yo di y ap vini" tells a story."""
    text = " ".join(str(said or "").replace("’", "'").split())
    if not text or len(text.split()) > 30:
        return None
    for n, pattern in enumerate(_CORRECTIONS):
        hit = pattern.search(text)
        if not hit:
            continue
        right, wrong = _clean(hit.group("right")), _clean(hit.groupdict().get("wrong") or "")
        quoted = bool(re.search(r"[\"“«']\s*" + re.escape(right), text))
        if wrong.lower() in _THAT:
            wrong = ""
            if n < 2 and not str(before or "").strip():
                return None                              # "they do not say that" with nothing said before it
        elif n < 2 and not quoted and wrong.lower() not in str(before or "").lower():
            return None
        if n == 2 and not creole and len(_tokens(right)) < 2 and not quoted:
            return None                                  # "you should say no": one bare word in an English conversation
        words = _tokens(right)
        if not words or len(words) > 12 or right.lower() in _THAT:
            return None
        if any(_NOT_CREOLE.search(p) and p not in NAMES for w in words for p in _parts(w)):
            return None                                  # French or English spelling: not a Creole phrase to keep
        known = sum(all(p in _base_words() for p in _parts(w)) for w in words)
        other = _other_languages()
        if known * 2 >= len(words) or (creole and not any(p in other for w in words for p in _parts(w))):
            return right, wrong                          # mostly words known as Creole; or, in a Creole conversation,
        return None                                      # a new word of his that is no English, French or Spanish one
    return None


def never(row: dict) -> bool:
    """Is the wording he corrected wrong wherever it comes: does it hold a word that is no Creole word here ('resip')?
    'Pa di bonjou, di bonswa' said one evening corrects the moment: 'bonjou' is good Creole, so it stays a lesson in
    the note and is never swapped in a reply (review 2026-11-12: it rewrote every morning greeting)."""
    wrong = row.get("wrong") or ""
    if not wrong or wrong.lower() == row["right"].lower():
        return False
    return any(p not in _base_words() for t in _tokens(wrong) for p in _parts(t))


def apply(reply: str, uid=None) -> str:
    """The reply with every wording he corrected that is no Creole replaced by what he said instead (whole phrases
    only). A correction of one good Creole wording by another is not applied here: see never()."""
    for row in kept(uid):
        wrong = row.get("wrong") or ""
        if never(row):
            reply = re.sub(rf"(?<![\w']){re.escape(wrong)}(?![\w'])", row["right"], reply, flags=re.I)
    return reply


# ---- the check ----------------------------------------------------------------------------------------------------
def vocabulary(uid=None) -> frozenset:
    extra = {p for row in kept(uid) for t in _tokens(row["right"]) for p in [t, *_parts(t)]}
    return _base_words() | extra if extra else _base_words()


@lru_cache(maxsize=1)
def _other_languages() -> frozenset:
    try:
        from audio import language
        return frozenset((language._SETS["fr"] | language._SETS["es"] | language._SETS["en"]) - _base_words())
    except Exception:  # noqa: BLE001
        return frozenset()


_SYON = re.compile(r"^[a-zèòà]{3,}syon$")


def _creole_form(token: str) -> bool:
    """A word that is spelled the Creole way by its ending: prezantasyon, edikasyon, enfòmasyon (the French -tion is
    never spelled so, and is flagged by its own spelling). Such a word is a Creole form of a word every speaker knows."""
    return bool(_SYON.match(token)) and token not in _other_languages()


def check(reply: str, uid=None, said: str = "") -> dict:
    """{"words": how many, "foreign": [...], "unknown": [...]} for one reply. foreign: a French, Spanish or English
    word, or spelling Creole never uses. unknown: Creole-looking but in no list here (possibly invented). A name
    (capitalised inside a sentence) and a number are never judged; nor is a word he used himself in the line being
    answered (said), nor a capitalised word that opens a sentence, is spelled like no Creole word and is in none of
    the other languages' lists: "Python se yon lang." names a thing (review 2026-11-12)."""
    known, other = vocabulary(uid), _other_languages()
    his = {p for t in _tokens(said) for p in [t, *_parts(t)]}
    foreign, unknown, total = [], [], 0
    for sentence in _SENTENCE.split(reply or ""):
        first = True
        for match in _TOKEN.finditer(sentence):
            raw, token = match.group(0), match.group(0).lower().replace("’", "'")
            name = raw[:1].isupper() and (not first or (token not in other and bool(_NOT_CREOLE.search(token))
                                                        and token.isascii() and "'" not in token and len(token) > 3
                                                        and not _FRENCH_END.search(token)))
            first = False
            total += 1
            if token in known or token in NAMES or name or token in his or _creole_form(token):
                continue
            for part in _parts(token):
                if part in known or part in NAMES or part in his or _creole_form(part):
                    continue
                if part in other or _NOT_CREOLE.search(part) or len(part) == 1:
                    foreign.append(token)
                else:
                    unknown.append(token)
                break
    return {"words": total, "foreign": sorted(set(foreign)), "unknown": sorted(set(unknown))}


def bad(found: dict) -> bool:
    """Is this reply worth one more try? Any foreign form (French, Spanish, English or their spelling: never let
    through), or a cluster of words no list knows: three, or two in a short reply (one may be a real rarer word, and two
    in a long answer are likely real too: a word list is rough, and an unknown word is no other language)."""
    unknown = len(found["unknown"])
    return bool(found["foreign"]) or unknown >= 3 or (unknown == 2 and unknown * 7 >= found["words"])


def flagged(found: dict) -> list:
    return found["foreign"] + found["unknown"]


# What French, Spanish and English drift into most, and the Creole word for it: named in the retry so the model has the
# right word in front of it, never swapped into the reply (a swap would turn a French sentence into "Creole").
SAY_INSTEAD = {
    "je": "mwen", "vous": "ou", "tu": "ou", "nous": "nou", "est": "se", "c'est": "se", "très": "anpil", "tres": "anpil",
    "avec": "avèk", "pour": "pou", "aussi": "tou", "oui": "wi", "merci": "mèsi", "bien": "byen",
    "pas": "pa", "mais": "men", "et": "e / epi", "dans": "nan", "sur": "sou", "que": "ke", "qui": "ki",
    "pourquoi": "poukisa", "comment": "kijan", "quoi": "kisa", "aujourd'hui": "jodi a", "demain": "demen",
    "hier": "yè", "maintenant": "kounye a", "toujours": "toujou", "beaucoup": "anpil", "peut": "ka", "peux": "ka",
    "dois": "dwe", "doit": "dwe", "faire": "fè", "dire": "di", "manger": "manje", "dormir": "dòmi", "aller": "ale",
    "avoir": "genyen", "être": "se", "important": "enpòtan", "attention": "atansyon", "question": "kesyon",
    "information": "enfòmasyon", "problème": "pwoblèm", "problem": "pwoblèm", "the": "la", "and": "e", "you": "ou",
    "with": "ak", "because": "paske", "please": "tanpri", "thanks": "mèsi", "yes": "wi", "no": "non", "very": "anpil",
    "good": "bon", "today": "jodi a", "tomorrow": "demen", "ahora": "kounye a", "hoy": "jodi a", "muy": "anpil",
    "también": "tou", "porque": "paske", "gracias": "mèsi",
}


def again_note(found: dict, said: str = "") -> str:
    """The note for the one more try: what was wrong, the Creole word where it is known, the question again and how to
    answer it (to the point, in everyday words)."""
    bits = []
    foreign = found["foreign"][:8]
    if foreign:
        named = ", ".join(f"'{w}'" + (f" (say '{SAY_INSTEAD[w]}')" if w in SAY_INSTEAD else "") for w in foreign)
        bits.append(f"These are French, Spanish or English words or spellings, not Creole: {named}.")
    unknown = found["unknown"][:8]
    if unknown:
        bits.append("These words are not everyday Creole words: " + ", ".join(f"'{w}'" for w in unknown)
                    + ". Say that another, simpler way with common words.")
    question = " ".join(str(said or "").split())[:200]
    ask = f' Answer this to the point: "{question}".' if question else ""
    return ("(Your last draft was not good Haitian Creole. " + " ".join(bits) + ask + " Write the reply again in one or "
            "two short sentences, only everyday Creole words you are sure of, standard Creole spelling (k not c or qu, w "
            "not v, no silent letters). If you do not know the Creole word for something, say it another, simpler way. "
            "No French, Spanish or English words.)")


_GREETS = re.compile(r"\b(?:bonjou|alo|sak\s+pase|sa\s+k\s+ap\s+f[eè]t|kijan\s+ou\s+ye|k[oò]man\s+ou\s+ye)\b", re.I)
_EVENING = re.compile(r"\bbonswa\b", re.I)
_THANKS = re.compile(r"\bm[eè]si\b", re.I)
_BYE = re.compile(r"\b(?:orevwa|babay|b[oò]n\s+nwit|bonnwit|n\s+a\s+w[eè]|n\s+a\s+pale|pita)\b", re.I)
_ASKS = re.compile(r"\?|^\W*(?:kisa|ki|kijan|k[oò]man|kote|poukisa|konbyen|[eè]ske|kil[eè]|kiy[eè]s|sa\s+k)\b", re.I)


HONEST = ("Mwen pa gen bon mo kreyòl pou sa. Mande m li an anglè, mwen ap reponn ou.",
          "Mwen pa jwenn bon mo kreyòl pou sa. Mande m li an anglè, m ap di w li.")
_NON_ANSWER = re.compile(r"^\s*(?:m|mwen)\s+(?:pa\s+)?tande\b", re.I)    # "I hear you ask about ...": says nothing


def salvage(*drafts: str, uid=None) -> str:
    """The clean sentences of the better draft (at most two), or ''. A sentence that only says it heard the question is
    no answer and is left out."""
    best = []
    for draft in drafts:
        clean = [s.strip() for s in _SENTENCE.split(draft or "") if s.strip() and len(_tokens(s)) >= 2
                 and not flagged(check(s, uid)) and not _NON_ANSWER.match(s)]
        if len(clean) > len(best):
            best = clean
    return " ".join(best[:2])


def honest(said: str = "") -> str:
    """When no draft holds an answer and none can be made clean: a short, true Creole sentence that says so and tells
    him how to get the answer (in English), not a request to rephrase. The two wordings alternate by his line."""
    return HONEST[sum(map(ord, said or "")) % len(HONEST)]


def fallback(said: str, *drafts: str, uid=None) -> str:
    """What is said when the drafts are still bad: the clean sentences of the better draft (at most two), or a short
    plain sentence that fits his line, or for a question the honest line (honest()). Never empty."""
    best = salvage(*drafts, uid=uid)
    if best:
        return best
    said = said or ""
    if _THANKS.search(said):
        return "Pa gen pwoblèm."
    if _BYE.search(said):
        return "Dakò. N a pale pita."
    if _EVENING.search(said):
        return "Bonswa. Mwen la, mwen byen."
    if _GREETS.search(said):
        return "Bonjou. Mwen la, mwen byen."
    if _ASKS.search(said):
        return honest(said)
    return "Dakò, mwen tande ou."


def settle(text: str, said: str, regenerate, uid=None, audit=lambda what: None) -> str:
    """The reply to say, from the model's draft: as written when it is Creole; else written once more through
    regenerate(note) (the note names what was wrong and puts the question again; it returns the new text, or '' when the
    model failed); else the clean sentences of the drafts, else the honest line. A draft with French, Spanish or English
    in it is never the result: only its clean sentences are."""
    text = apply(text, uid)
    found = check(text, uid, said)
    if not bad(found):
        return text
    audit("kreyol_again")
    second = apply(regenerate(again_note(found, said)) or "", uid)   # regenerate handles a model failure itself
    if second.strip() and "[ignore]" not in second.lower() and not bad(check(second, uid, said)):
        return second
    audit("kreyol_fallback")
    out = fallback(said, second, text, uid=uid)
    if out in HONEST:
        audit("kreyol_honest")
    return out


# ---- retrieval ----------------------------------------------------------------------------------------------------
_COMMON = set("mwen m ou w li l nou n yo y se pa ki sa a la an nan lan ak avèk pou te ap pral ka yon e epi men si ke "
              "the a an is are i you it to of and in on for do what how my me".split())
ALWAYS = ("Mwen pa konnen.", "Mwen ka ede ou.", "Dakò, mwen tande ou.")
EXAMPLES = 5


@lru_cache(maxsize=1)
def _index() -> tuple:
    rows = builtin()
    frequency: dict = {}
    keyed = []
    for row in rows:
        keys = {t for t in _tokens(row["ht"]) + _tokens(row["en"]) if t not in _COMMON}
        keyed.append(keys)
        for key in keys:
            frequency[key] = frequency.get(key, 0) + 1
    return rows, tuple(keyed), frequency


def relevant(said: str, uid=None, count: int = EXAMPLES) -> list:
    """The bank sentences nearest his line, best first: his own corrections ahead of the built-in ones, then by the
    rarer words they share with what he said. Filled up with a few plain sentences when little matches."""
    rows, keyed, frequency = _index()
    asked = {t for t in _tokens(said) if t not in _COMMON}
    scored = []
    for n, (row, keys) in enumerate(zip(rows, keyed)):
        shared = asked & keys
        if shared:
            score = sum(1.0 / (1.0 + math.log(frequency[k])) for k in shared) / math.sqrt(len(keys) or 1)
            scored.append((-score, n, row))
    mine = []
    for row in reversed(kept(uid)):
        keys = {t for t in _tokens(row["right"]) + _tokens(row.get("wrong", "")) + _tokens(row.get("about", ""))} - _COMMON
        if asked & keys or len(mine) < 2:                # the two newest corrections always ride along
            mine.append({"ht": row["right"], "en": "", "source": "owner", "wrong": row.get("wrong", ""),
                         "never": never(row)})
    out = mine[:count] + [row for _, _, row in sorted(scored)[:count]]
    have = {row["ht"] for row in out}
    out += [row for row in rows if row["ht"] in ALWAYS and row["ht"] not in have]
    return out[:count]


def exchanges(said: str = "", count: int = 3) -> list:
    """Question and answer pairs in everyday Creole that show the style: the answer is to the point, short and plain.
    The ones nearest his line first, then the first ones."""
    asked = {t for t in _tokens(said) if t not in _COMMON}

    def near(n):
        return -len(asked & {t for t in _tokens(EXCHANGES[n][0]) if t not in _COMMON})
    return [EXCHANGES[n] for n in sorted(range(len(EXCHANGES)), key=lambda n: (near(n), n))[:count]]


def grounding(said: str, uid=None) -> str:
    """The few-shot lines for the language note: model sentences near his line, how to keep it simple, and a few full
    exchanges that show the shape of a good answer."""
    lines = []
    for row in relevant(said, uid):
        if row["source"] == "owner":
            lines.append(f"'{row['ht']}'" + (f" (they taught you this; never '{row['wrong']}')" if row.get("never")
                                             else f" (they taught you this, where you had said '{row['wrong']}')"
                                             if row.get("wrong") else " (they taught you this)"))
        else:
            lines.append(f"'{row['ht']}'" + (f" ({row['en']})" if row["en"] else ""))
    shown = " ".join(f"Q: {q} A: {a}" for q, a in exchanges(said))
    return ("Answer what they asked, to the point, in one or two short, simple sentences in everyday Creole words, twenty "
            "words at most in all: give the answer or one useful suggestion, never say you did not hear and never talk "
            "about yourself instead. If you do "
            "not know the Creole word for something, say it another, simpler way: never invent a word and never use a "
            "French or Spanish one. Examples of good answers: " + shown + " Correct Creole to model your words and "
            "spelling on: " + "; ".join(lines) + ".")


def is_note(language_note: str) -> bool:
    """Is this per-request language note the Creole one (audio.language.reply_note)?"""
    return str(language_note or "").startswith("(Language: they are speaking Haitian Creole")
