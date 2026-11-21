"""The beginner course the language teacher follows: eight can-do units, the same meanings in French, Spanish,
Mandarin Chinese, Japanese and Haitian Creole. Written by hand for this project (no imported word list).

Each row: (key, English, French, Spanish, Mandarin, Japanese, Haitian Creole).
  * English: "hello/hi" lists the meanings accepted when he is asked what a phrase means.
  * French, Spanish, Haitian Creole: "text" or "text/another accepted form". Creole uses the official orthography
    (òè, no silent letters). A trailing "..." marks an opening he completes ("Je m'appelle ...").
  * Mandarin: "simplified characters|pinyin with tone marks".
  * Japanese: "kana|romaji" or "kana|romaji|usual writing with kanji" (a recogniser writes kanji, he learns kana first).

A native reviewer has not checked the Haitian Creole or the Asian-language rows; they are standard beginner phrases
kept deliberately simple."""

LANGUAGES = {
    "fr": {"name": "French", "native": "français"},
    "es": {"name": "Spanish", "native": "español"},
    "zh": {"name": "Mandarin Chinese", "native": "中文"},
    "ja": {"name": "Japanese", "native": "日本語"},
    "ht": {"name": "Haitian Creole", "native": "kreyòl ayisyen"},
}
ORDER = ("fr", "es", "zh", "ja", "ht")
DIGITS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
          "nine": "9", "ten": "10"}

_UNITS = [
    ("greetings", "Greetings", "greet someone, thank them and say goodbye", [
        ("hello", "hello/hi/good morning/good day", "Bonjour", "Hola", "你好|nǐ hǎo", "こんにちは|konnichiwa|今日は", "Bonjou"),
        ("thanks", "thank you/thanks", "Merci", "Gracias", "谢谢|xièxie", "ありがとう|arigatō|有難う", "Mèsi"),
        ("please", "please", "S'il vous plaît/s'il te plaît", "Por favor", "请|qǐng", "おねがいします|onegaishimasu|お願いします", "Tanpri/souple"),
        ("how_are_you", "how are you?/how are you doing?", "Comment ça va ?/ça va ?", "¿Cómo estás?/¿Cómo está?", "你好吗？|nǐ hǎo ma",
         "おげんきですか|ogenki desu ka|お元気ですか", "Kijan ou ye?/Kouman ou ye?"),
        ("im_fine", "I am fine/I'm fine/I am well/I'm good", "Ça va bien/je vais bien", "Estoy bien", "我很好|wǒ hěn hǎo",
         "げんきです|genki desu|元気です", "Mwen byen"),
        ("sorry", "excuse me/sorry/I'm sorry", "Pardon/excusez-moi", "Perdón/disculpe", "对不起|duìbuqǐ", "すみません|sumimasen", "Eskize m/eskize mwen"),
        ("goodbye", "goodbye/bye", "Au revoir", "Adiós", "再见|zàijiàn", "さようなら|sayōnara", "Orevwa/babay"),
        ("good_night", "good night", "Bonne nuit", "Buenas noches", "晚安|wǎn'ān", "おやすみなさい|oyasuminasai|お休みなさい", "Bòn nwit"),
    ], ("how_are_you", "im_fine")),
    ("introductions", "Introductions", "say who you are and where you are from", [
        ("your_name", "what is your name?/what's your name?", "Comment tu t'appelles ?/Comment vous appelez-vous ?", "¿Cómo te llamas?/¿Cómo se llama?",
         "你叫什么名字？|nǐ jiào shénme míngzi", "おなまえはなんですか|onamae wa nan desu ka|お名前は何ですか", "Kijan ou rele?/Kouman ou rele?"),
        ("my_name_is", "my name is/I am called", "Je m'appelle ...", "Me llamo ...", "我叫 ...|wǒ jiào ...",
         "わたしのなまえは ...|watashi no namae wa ...|私の名前は ...", "Mwen rele ..."),
        ("nice_to_meet", "nice to meet you/pleased to meet you", "Enchanté/enchantée", "Mucho gusto", "很高兴认识你|hěn gāoxìng rènshi nǐ",
         "はじめまして|hajimemashite|初めまして", "Mwen kontan rankontre ou/Mwen kontan rekonèt ou"),
        ("where_from", "where are you from?", "D'où viens-tu ?/D'où venez-vous ?", "¿De dónde eres?/¿De dónde es?", "你是哪国人？|nǐ shì nǎ guó rén",
         "どこからきましたか|doko kara kimashita ka|どこから来ましたか", "Ki kote ou soti?"),
        ("i_am_from", "I am from/I come from", "Je viens de ...", "Soy de ...", "我来自 ...|wǒ láizì ...", "... からきました|... kara kimashita|... から来ました", "Mwen soti ..."),
        ("i_speak_a_little", "I speak a little {language}", "Je parle un peu français", "Hablo un poco de español", "我会说一点中文|wǒ huì shuō yìdiǎn Zhōngwén",
         "にほんごをすこしはなします|nihongo o sukoshi hanashimasu|日本語を少し話します", "Mwen pale yon ti kreyòl"),
        ("dont_understand", "I don't understand/I do not understand", "Je ne comprends pas", "No entiendo", "我不明白|wǒ bù míngbai",
         "わかりません|wakarimasen|分かりません", "Mwen pa konprann"),
    ], ("your_name", "my_name_is")),
    ("numbers", "Numbers", "count to ten and ask a price", [
        ("one", "one", "un", "uno", "一|yī", "いち|ichi|一", "en/youn"),
        ("two", "two", "deux", "dos", "二|èr", "に|ni|二", "de"),
        ("three", "three", "trois", "tres", "三|sān", "さん|san|三", "twa"),
        ("four", "four", "quatre", "cuatro", "四|sì", "よん|yon|四", "kat"),
        ("five", "five", "cinq", "cinco", "五|wǔ", "ご|go|五", "senk"),
        ("six", "six", "six", "seis", "六|liù", "ろく|roku|六", "sis"),
        ("seven", "seven", "sept", "siete", "七|qī", "なな|nana|七", "sèt"),
        ("eight", "eight", "huit", "ocho", "八|bā", "はち|hachi|八", "uit"),
        ("nine", "nine", "neuf", "nueve", "九|jiǔ", "きゅう|kyū|九", "nèf"),
        ("ten", "ten", "dix", "diez", "十|shí", "じゅう|jū|十", "dis"),
        ("how_much", "how much is it?/how much does it cost?/how much?", "C'est combien ?/Combien ça coûte ?", "¿Cuánto cuesta?/¿Cuánto es?", "多少钱？|duōshao qián",
         "いくらですか|ikura desu ka", "Konbyen li koute?/Konbyen sa koute?"),
    ], ("how_much", "ten")),
    ("family", "Family", "talk about the people in your family", [
        ("mother", "mother/mom/mum", "la mère/la maman", "la madre/la mamá", "妈妈|māma", "おかあさん|okāsan|お母さん", "manman"),
        ("father", "father/dad", "le père/le papa", "el padre/el papá", "爸爸|bàba", "おとうさん|otōsan|お父さん", "papa"),
        ("older_brother", "older brother/big brother", "le grand frère", "el hermano mayor", "哥哥|gēge", "おにいさん|onīsan|お兄さん", "gran frè"),
        ("older_sister", "older sister/big sister", "la grande sœur", "la hermana mayor", "姐姐|jiějie", "おねえさん|onēsan|お姉さん", "gran sè"),
        ("child", "child/kid", "l'enfant", "el niño/la niña", "孩子|háizi", "こども|kodomo|子供", "timoun"),
        ("friend", "friend", "l'ami/l'amie", "el amigo/la amiga", "朋友|péngyou", "ともだち|tomodachi|友達", "zanmi"),
        ("two_children", "I have two children/I have two kids", "J'ai deux enfants", "Tengo dos hijos", "我有两个孩子|wǒ yǒu liǎng ge háizi",
         "こどもがふたりいます|kodomo ga futari imasu|子供が二人います", "Mwen gen de timoun"),
        ("this_is_my_mother", "this is my mother/this is my mom", "C'est ma mère", "Esta es mi madre/Ella es mi madre", "这是我妈妈|zhè shì wǒ māma",
         "こちらはははです|kochira wa haha desu|こちらは母です", "Sa a se manman mwen/Sa se manman m"),
    ], (None, "two_children")),
    ("food", "Food and drink", "say you are hungry and ask for something", [
        ("water", "water", "l'eau/de l'eau", "el agua/agua", "水|shuǐ", "みず|mizu|水", "dlo"),
        ("bread", "bread", "le pain/du pain", "el pan/pan", "面包|miànbāo", "パン|pan", "pen"),
        ("rice", "rice", "le riz/du riz", "el arroz/arroz", "米饭|mǐfàn", "ごはん|gohan|ご飯", "diri"),
        ("coffee", "coffee", "le café/du café", "el café/café", "咖啡|kāfēi", "コーヒー|kōhī", "kafe"),
        ("hungry", "I am hungry/I'm hungry", "J'ai faim", "Tengo hambre", "我饿了|wǒ è le", "おなかがすきました|onaka ga sukimashita|お腹が空きました", "Mwen grangou"),
        ("water_please", "I would like water, please/I'd like water please/water please", "Je voudrais de l'eau, s'il vous plaît", "Quisiera agua, por favor/Quiero agua, por favor",
         "请给我水|qǐng gěi wǒ shuǐ", "みずをください|mizu o kudasai|水をください", "Mwen ta renmen dlo, tanpri/Mwen ta renmen dlo, souple"),
        ("delicious", "it is delicious/it's delicious/delicious/it tastes good", "C'est délicieux/C'est très bon", "Está delicioso/Está rico", "很好吃|hěn hǎochī",
         "おいしいです|oishii desu|美味しいです", "Li bon anpil/Li gou"),
    ], (None, "hungry")),
    ("directions", "Directions", "ask where something is and follow simple directions", [
        ("where_bathroom", "where is the bathroom?/where is the toilet?/where is the restroom?", "Où sont les toilettes ?", "¿Dónde está el baño?", "洗手间在哪里？|xǐshǒujiān zài nǎlǐ",
         "トイレはどこですか|toire wa doko desu ka", "Ki kote twalèt la ye?"),
        ("left", "left/on the left/to the left", "à gauche", "a la izquierda", "左边|zuǒbian", "ひだり|hidari|左", "agoch/a goch"),
        ("right", "right/on the right/to the right", "à droite", "a la derecha", "右边|yòubian", "みぎ|migi|右", "adwat/a dwat"),
        ("straight", "straight ahead/straight/go straight", "tout droit", "todo recto/derecho", "一直走|yìzhí zǒu", "まっすぐ|massugu|真っ直ぐ", "tou dwat"),
        ("here", "here", "ici", "aquí", "这里|zhèlǐ", "ここ|koko", "isit/isit la"),
        ("near", "near/close/nearby", "près", "cerca", "近|jìn", "ちかい|chikai|近い", "pre"),
        ("far", "far/far away", "loin", "lejos", "远|yuǎn", "とおい|tōi|遠い", "lwen"),
        ("lost", "I am lost/I'm lost", "Je suis perdu/Je suis perdue", "Estoy perdido/Estoy perdida", "我迷路了|wǒ mílù le",
         "みちにまよいました|michi ni mayoimashita|道に迷いました", "Mwen pèdi"),
    ], (None, "lost")),
    ("routine", "Daily routine", "describe an ordinary day", [
        ("today", "today", "aujourd'hui", "hoy", "今天|jīntiān", "きょう|kyō|今日", "jodi a/jodi"),
        ("every_day", "every day/each day/daily", "tous les jours/chaque jour", "todos los días/cada día", "每天|měitiān", "まいにち|mainichi|毎日", "chak jou"),
        ("get_up", "I get up at seven/I wake up at seven", "Je me lève à sept heures", "Me levanto a las siete", "我七点起床|wǒ qī diǎn qǐchuáng",
         "しちじにおきます|shichi-ji ni okimasu|七時に起きます", "Mwen leve a sèt è"),
        ("i_work", "I work/I am working", "Je travaille", "Trabajo/Yo trabajo", "我工作|wǒ gōngzuò", "はたらきます|hatarakimasu|働きます", "Mwen travay/M ap travay"),
        ("breakfast", "I eat breakfast/I have breakfast", "Je prends le petit déjeuner", "Desayuno/Yo desayuno", "我吃早饭|wǒ chī zǎofàn",
         "あさごはんをたべます|asagohan o tabemasu|朝ご飯を食べます", "Mwen pran dejene/Mwen manje maten"),
        ("go_sleep", "I go to sleep/I am going to sleep/I go to bed", "Je vais dormir/Je vais me coucher", "Me voy a dormir/Voy a dormir", "我去睡觉|wǒ qù shuìjiào",
         "ねます|nemasu|寝ます", "Mwen pral dòmi/M pral dòmi"),
        ("what_time", "what time is it?", "Quelle heure est-il ?/Il est quelle heure ?", "¿Qué hora es?", "现在几点？|xiànzài jǐ diǎn",
         "いまなんじですか|ima nanji desu ka|今何時ですか", "Ki lè li ye?"),
    ], (None, "i_work")),
    ("past_future", "Past and future", "say what you did and what you will do", [
        ("yesterday", "yesterday", "hier", "ayer", "昨天|zuótiān", "きのう|kinō|昨日", "yè"),
        ("tomorrow", "tomorrow", "demain", "mañana", "明天|míngtiān", "あした|ashita|明日", "demen"),
        ("i_ate", "I ate/I have eaten", "J'ai mangé", "Comí/Yo comí", "我吃了|wǒ chī le", "たべました|tabemashita|食べました", "Mwen te manje/M te manje"),
        ("went_market", "I went to the market", "Je suis allé au marché/Je suis allée au marché", "Fui al mercado", "我去了市场|wǒ qù le shìchǎng",
         "いちばにいきました|ichiba ni ikimashita|市場に行きました", "Mwen te ale nan mache/M te ale nan mache a"),
        ("go_tomorrow", "I will go tomorrow/I am going tomorrow", "J'irai demain/Je vais y aller demain", "Iré mañana/Voy a ir mañana", "我明天去|wǒ míngtiān qù",
         "あしたいきます|ashita ikimasu|明日行きます", "Mwen pral ale demen/M pral ale demen"),
        ("going_to_study", "I am going to study/I will study", "Je vais étudier", "Voy a estudiar", "我要学习|wǒ yào xuéxí", "べんきょうします|benkyō shimasu|勉強します",
         "Mwen pral etidye/M pral etidye"),
        ("see_you_tomorrow", "see you tomorrow", "À demain", "Hasta mañana", "明天见|míngtiān jiàn", "またあした|mata ashita|また明日", "N a wè demen/Na wè demen"),
    ], (None, "i_ate")),
]

# One short English note per unit and language, said when the same slip keeps coming back.
TIPS = {
    "greetings": {
        "fr": "French greetings are fixed phrases: learn each one as a single block of sound.",
        "es": "Spanish questions open with an upside-down mark in writing; in speech, just let your voice rise at the end.",
        "zh": "Every syllable has a tone. In nǐ hǎo both are third tones, so the first one rises instead.",
        "ja": "Japanese has no stress accent: give every syllable the same length, kon-ni-chi-wa.",
        "ht": "Creole is written as it sounds: every letter is pronounced, and è is open as in 'bed'."},
    "introductions": {
        "fr": "Je m'appelle is literally 'I call myself'; the name comes straight after it.",
        "es": "Me llamo is literally 'I call myself'; the double l sounds like the y in 'yes'.",
        "zh": "Wǒ jiào plus your name: jiào means 'to be called'. No word for 'is' is needed.",
        "ja": "The name comes first and desu closes the sentence. Wa marks what the sentence is about.",
        "ht": "Mwen rele plus your name: rele means 'to be called'. Verbs never change their form."},
    "numbers": {
        "zh": "Use liǎng, not èr, when you count things: liǎng ge is 'two of them'.",
        "ja": "Four is yon and seven is nana in everyday counting.",
        "ht": "Creole numbers are close to French ones, written the Creole way: twa, kat, senk."},
    "family": {
        "fr": "Each noun has a gender: la mère, le père. Learn the little word with the noun.",
        "es": "Nouns ending in o are usually masculine (el hermano), in a feminine (la hermana).",
        "zh": "Family words double the syllable: māma, bàba, gēge. The second syllable is light.",
        "ja": "Okāsan and otōsan are polite. For your own mother to others you say haha.",
        "ht": "The owner comes after the thing: manman mwen is 'my mother', literally 'mother me'."},
    "food": {
        "fr": "J'ai faim is literally 'I have hunger': French uses 'have', not 'am'.",
        "es": "Tengo hambre is literally 'I have hunger': Spanish uses 'have', not 'am'.",
        "zh": "Le at the end marks a change: wǒ è le is 'I have become hungry'.",
        "ja": "Add o kudasai after a thing to ask for it: mizu o kudasai.",
        "ht": "Mwen grangou has no word for 'am': the describing word comes right after mwen."},
    "directions": {
        "fr": "Où means 'where'. A gauche and à droite keep the à in front.",
        "es": "Dónde está asks where one thing is; the stress falls on the marked vowel.",
        "zh": "Zài nǎlǐ comes at the end: 'the bathroom is at where?'",
        "ja": "Wa doko desu ka follows the place you are looking for.",
        "ht": "Ki kote opens a 'where' question and ye closes it: Ki kote ... ye?"},
    "routine": {
        "fr": "Je me lève has a little 'me' because you get yourself up.",
        "es": "Me levanto has a little 'me' because you get yourself up.",
        "zh": "Time goes before the verb: wǒ qī diǎn qǐchuáng, 'I seven o'clock get up'.",
        "ja": "The verb comes last, and -masu makes it polite: okimasu, tabemasu, nemasu.",
        "ht": "Ap before a verb means it is happening now: m ap travay, 'I am working'."},
    "past_future": {
        "fr": "The past here is two words: j'ai mangé. The near future is je vais plus the verb.",
        "es": "The near future is voy a plus the verb: voy a estudiar.",
        "zh": "Le after the verb marks a finished action; a time word like míngtiān is enough for the future.",
        "ja": "-mashita is the past of -masu: tabemasu becomes tabemashita.",
        "ht": "Te before the verb makes the past and pral makes the future: mwen te manje, mwen pral manje."},
}


def _forms(cell: str) -> list[str]:
    return [f.strip() for f in cell.split("/") if f.strip()]


def _item(unit: str, row: tuple, lang: str) -> dict:
    key, english, cell = row[0], row[1], row[2 + ORDER.index(lang)]
    name = LANGUAGES[lang]["name"].replace("Mandarin Chinese", "Chinese")
    meanings = [m.replace("{language}", name) for m in _forms(english)]
    roman, extra = "", []
    if lang in ("zh", "ja"):
        parts = cell.split("|")
        text, roman, extra = parts[0], parts[1], parts[2:]
        forms = [text]
    else:
        forms = _forms(cell)
        text = forms[0]
    accept = forms + ([roman] if roman else []) + extra + ([DIGITS[key]] if key in DIGITS else [])
    return {"id": f"{lang}:{key}", "key": key, "lang": lang, "unit": unit, "text": text, "roman": roman, "english": meanings[0],
            "meanings": meanings, "accept": accept, "open": text.endswith("...") or text.startswith("..."),
            "question": meanings[0].endswith("?")}


def units(lang: str) -> list[dict]:
    """The course for one language: [{"id", "title", "can_do", "items": [...], "ask": item id or None, "reply": item id,
    "tip"}], in teaching order."""
    out = []
    for uid, title, can_do, rows, (ask, reply) in _UNITS:
        out.append({"id": uid, "title": title, "can_do": can_do, "items": [_item(uid, r, lang) for r in rows],
                    "ask": f"{lang}:{ask}" if ask else None, "reply": f"{lang}:{reply}", "tip": TIPS.get(uid, {}).get(lang, "")})
    return out


def items(lang: str) -> dict:
    """Every course item of a language by id, in teaching order."""
    return {i["id"]: i for u in units(lang) for i in u["items"]}
