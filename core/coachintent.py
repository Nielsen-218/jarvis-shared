"""Which trainer skill his words ask for, decided in code before any model chooses (as core/langintent does for the
language teacher). The same rule governs it: a workout never makes Jarvis deaf.

  * With no workout running, only a clear training request counts: "start my workout", "what's today's workout",
    "my goal is to build muscle", "build me a training plan", "change my plan to three days a week", "log a set of
    bench, 8 at 135", "how is my training going", "why did you cut my sets".
  * While a workout is running, a line is taken only if it LOOKS like a set report ("8 at 135", "135 for 8", "got 8,
    two in reserve", "failed at 6") or like session talk ("next exercise", "swap this for something else", "the bench
    is taken", "what's the next exercise", "end my workout"). The words that could be for anything else ("done",
    "next", "skip", "easy", a bare number, "repeat that", "what's next", "change this", "I can't do this", "that's it
    for today") count only as the reply to a workout line (session["prompted"]), and "the X is busy" only when X is
    gym equipment or an exercise. Everything else takes the normal path and the workout stays open.
  * A plan or a goal is his training plan or goal only when the sentence says so ("training plan", "fitness goal")
    or says nothing but training ("build me a plan to get stronger", "my goal is fat loss, four days a week"). "A plan
    to get stronger at chess" or "my goal is to get fit for the wedding" is left to the normal path: these skills
    replace his plan and his saved goal, so when in doubt they do not run from here.

call_for(text, session, active) -> (skill name, arguments) or None."""
import re

from core import coach_engine as coach

_NUM = {"zero": 0, "no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "eighteen": 18, "twenty": 20}
_N = r"(?:\d{1,3}|" + "|".join(_NUM) + ")"
_W = r"\d{1,4}(?:\.\d)?"
_LEAD = (r"^\W*(?:(?:hey|ok|okay|so|now|alright|all right|and)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?(?:please\s+)?"
         r"(?:(?:can|could|would|will) (?:you|we)\s+(?:please\s+)?|i(?: would|'d) like (?:you )?to\s+|i (?:want|need) (?:you )?to\s+|let'?s\s+|"
         r"(?:it'?s\s+)?time to\s+|i'?m ready to\s+)?")
_END = r"(?:\s+(?:please|now|today|tonight|for (?:me|today)|jarvis|thanks|thank you))*\W*$"
_SESSION_NOUN = r"(?:workout|work out|training(?: session)?|gym session|lifting session|lift|session)"
_START_NOUN = r"(?:workout|work out|training session|gym session|lifting session)"
_PLAN_NOUN = r"(?:(?:training|workout|work out|gym|lifting|fitness|exercise|strength)\s+)?(?:plan|program|programme|routine|split)"

_START = re.compile(_LEAD + r"(?:(?:start|begin|do|run|resume|continue|guide me through|walk me through|coach me through|take me through)\s+"
                    r"(?:my|the|today'?s|this|our|a)\s+" + _START_NOUN + r"|(?:start|begin) (?:training|lifting|working out)|"
                    r"i'?m (?:at|in) the gym(?:,? (?:let'?s go|ready))?|"
                    r"i'?m ready (?:to (?:train|lift|work out)|for (?:my|the|today'?s) " + _START_NOUN + r"))" + _END, re.I)
_START2 = re.compile(r"^\W*(?:(?:hey|ok|okay|alright|all right)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?let'?s (?:work out|train|lift|hit the gym|get (?:this|my) workout (?:in|started|going))" + _END, re.I)
_TODAY = re.compile(r"^\W*(?:(?:hey |ok |okay |so )?jarvis[,.!]?\s+)?(?:what(?:'s| is) (?:my|today'?s|the) (?:workout|training|training session|gym session|lift)"
                    r"(?: (?:for )?today| tonight| this (?:morning|afternoon|evening))?|what(?:'s| is) on (?:my|the) (?:training |workout )(?:plan|schedule)"
                    r"(?: (?:for )?today)?|what (?:am i|are we) (?:training|lifting|doing at the gym|doing in the gym)(?: today| tonight)?|"
                    r"what (?:workout|training) do i have (?:today|tonight)|do i (?:have a workout|train|lift|work out) today|"
                    r"(?:show|tell) me (?:my|today'?s) (?:workout|training session))" + _END, re.I)
_GOAL = re.compile(_LEAD + r"(?:(?:set|change|update|make)\s+my (?:(?:training|fitness|workout|gym|lifting)\s+)?goal\s+(?:to|as)\s+(?P<a>.+?)|"
                   r"my (?:(?:new|main|training|fitness|workout|gym|lifting)\s+)*goal (?:is|is now|will be)\s+(?:to\s+)?(?P<b>.+?)|"
                   r"train me (?:for|to)\s+(?P<c>.+?)|i(?:'m| am) training (?:for|to)\s+(?P<d>.+?))\W*$", re.I)
_BUILD = re.compile(_LEAD + r"(?:(?:build|make|create|design|write|generate|give|set up|put together|plan)\s+(?:me\s+)?(?:a|an|my|the|a new|another|my new)\s+"
                    r"(?:\w+[- ]\w+\s+|\w+\s+)?" + _PLAN_NOUN + r"|(?:change|switch|update|rebuild|redo|adjust|rework|remake)\s+(?:my|the)\s+" + _PLAN_NOUN +
                    r"|i (?:want|need) (?:a|a new|another) " + _PLAN_NOUN + r")(?P<rest>\b.*)?$", re.I)
_DAYS_NOW = re.compile(r"^\W*(?:jarvis[,.!]?\s+)?i can (?:only |now )?(?:train|work out|lift|make it to the gym|go to the gym)\s+(?:only\s+)?(?P<n>" + _N +
                       r")\s+(?:days?|times)(?: a| per| each)? week\b.*$", re.I)
_DAYS = re.compile(r"\b(" + _N + r")\s*(?:days?|times|x)(?: a| per| each|/)\s*week\b", re.I)
_MINUTES = re.compile(r"\b(\d{2,3})[- ]?(?:minutes?|mins?)\b|\b(an hour|one hour|half an hour)\b", re.I)
_GEAR = re.compile(r"\b(?:at home|home gym|full gym|(?:the |a )?gym|dumb ?bells?|body ?weight(?: only)?|no equipment|kettle ?bells?|bands?|barbell)\b", re.I)
_PROGRESS = re.compile(r"^\W*(?:(?:hey |ok |okay |so )?jarvis[,.!]?\s+)?(?:how am i (?:progressing|doing|coming along) (?:in the gym|at the gym|with (?:my )?"
                       r"(?:training|lifting|lifts|workouts?|plan|strength)|on (?:my )?(?P<lift1>[\w -]+?))|"
                       r"how(?:'s| is| are) my (?:training|lifting|lifts|workouts?|strength|gains|(?P<lift2>[\w -]+?))(?: progress)? (?:going|progressing|coming along|coming|doing|looking)|"
                       r"how(?:'s| is) my (?:training|lifting|workout|strength|gym) progress|am i getting (?:stronger|bigger|fitter)|"
                       r"(?:show|tell) me my (?:training|lifting|workout|strength|gym) progress|"
                       r"what(?:'s| is) my (?:estimated |one[- ]rep )?max (?:on|for|in) (?:the )?(?P<lift3>[\w -]+?))" + _END, re.I)
_PROGRESS_BARE = re.compile(r"^\W*(?:(?:hey |ok |okay |so )?jarvis[,.!]?\s+)?(?:how am i progressing|how(?:'s| is) my progress|am i making progress|"
                            r"(?:show me |what(?:'s| is) )?my progress)" + _END, re.I)
_WHY = re.compile(r"^\W*(?:(?:hey |ok |okay |so |and |but )?jarvis[,.!]?\s+)?(?:why (?:did|have|would) you (?:change|changed|cut|drop|dropped|lower|lowered|reduce|reduced|move|moved|swap|"
                  r"swapped|shorten|shortened|trim|trimmed|take|taken|remove|removed|skip|skipped|add|added|raise|raised|increase|increased)\s+(?P<a>.+?)|"
                  r"why (?:is|was|are|were) (?P<b>.+?) (?:lighter|shorter|easier|different|less|lower|fewer|heavier|higher|gone|missing|moved|changed)(?: today| now| this week)?|"
                  r"why (?:am i doing|do i have|did i get|are there) (?P<c>fewer|less|more|only) .+?|"
                  r"why did (?P<d>my .+?|the .+?) (?:go|come|get|drop|change|move)(?: (?:down|up|lighter|heavier|shorter))?)\W*$", re.I)
_TRAIN_WORD = r"(?:training|workout|work out|gym|lifting|fitness|exercise|strength|muscle)"
_TRAINED_PLAN = re.compile(r"\b" + _TRAIN_WORD + r"\s+(?:plan|program|programme|routine|split)\b", re.I)
_TRAINED_GOAL = re.compile(r"\b" + _TRAIN_WORD + r"\s+goal\b|^\W*(?:[\w',]+\s+){0,8}?train me\b", re.I)
# a plan with no training word in front of it: only these words may stand between "a" and "plan", and "the plan" is not his
_BARE_BUILD = re.compile(_LEAD + r"(?:(?:build|make|create|design|write|generate|give|set up|put together|plan)\s+(?:me\s+)?(?:a|an|my|a new|another|my new)\s+"
                         r"(?:(?:new|better|different|weekly|" + _N + r"[- ]days?|full[- ]body|upper[- ]lower|push[- ]pull(?:[- ]legs)?)\s+)?"
                         r"|(?:change|switch|update|rebuild|redo|adjust|rework|remake)\s+my\s+|i (?:want|need) (?:a|a new|another)\s+)"
                         r"(?:plan|program|programme|routine|split)\b", re.I)
# the only words a sentence that is nothing but training may hold besides the days, the minutes and the equipment
_PLAN_TALK = frozenset((
    "to for and that so i can want would like with a an the my me in into on of it is be get getting become build building gain gaining put add "
    "lose losing drop burn improve increase stay keep look looking grow pass ace prepare prep focus focused focusing train training work "
    "working lift lifting some more better good overall general body upper lower full lot bit much really just only now please today instead "
    "new sessions session each per week day days around about based goal goals weight loss army combat fitness test pt one rep max pounds "
    "lbs up down out mainly mostly main also but then while").split())


def _only_training(words: str) -> bool:
    """Is this nothing but training talk? Every word left after the days, the minutes and the equipment must be a
    goal word or one of the small words around one; "at chess", "for the wedding", "money" make it someone else's."""
    rest = _GEAR.sub(" ", _MINUTES.sub(" ", _DAYS.sub(" ", words or "")))
    return all(w.isdigit() or w in _PLAN_TALK or coach.parse_goal(w) for w in re.findall(r"[a-z0-9']+", rest.lower()))
_WHY_ABOUT = re.compile(r"\b(?:workout|training|session|plan|program|routine|sets?|reps?|load|loads|weights?|deload|exercises?|lift|lifts|rest day)\b", re.I)

# -- inside a workout ---------------------------------------------------------------------------------------------
_FEEL = (r"(?:(?:and |but |,\s*)?(?:(?:that|it|this) (?:was|felt|is|were)|felt|pretty|really|very|kind of|a bit|way)?\s*"
         r"(?P<feel>too easy|easy|light|fine|good|okay|ok|solid|hard|tough|heavy|brutal|rough|a grind|a struggle|barely|too heavy|too hard|too light))")
_RIR = (r"(?:(?:with |and |,\s*)?(?P<rir>" + _N + r")\s+(?:more\s+)?(?:reps?\s+)?(?:left\s+)?(?:in (?:reserve|the tank)|left(?: in the tank)?|rir)|rir\s+(?P<rir2>\d))")
_TAIL = r"(?:[,.]?\s*(?:" + _FEEL + "|" + _RIR + r"))?\W*$"
_HEAD = r"^\W*(?:(?:ok|okay|alright|jarvis|so|and)[,.!]?\s+)*(?:(?:i\s+)?(?:got|did|hit|made|managed|logged?|log|that(?:'s| was| is)|it was|just did)\s+)?"
_UNIT = r"(?:\s*(?:pounds?|lbs?|lb|kilos?|kg))?"
_REPS_AT = re.compile(_HEAD + r"(?P<reps>" + _N + r")(?:\s+reps?)?\s*(?:at|@|with|on)\s*(?P<w>" + _W + ")" + _UNIT + _TAIL, re.I)
_W_FOR = re.compile(_HEAD + r"(?P<w>" + _W + ")" + _UNIT + r"\s+(?:for|times)\s+(?P<reps>" + _N + r")(?:\s+reps?)?" + _TAIL, re.I)
_BY = re.compile(_HEAD + r"(?P<a>" + _W + r")\s*(?:x|by)\s*(?P<b>" + _W + ")" + _UNIT + _TAIL, re.I)
_REPS = re.compile(_HEAD + r"(?P<reps>" + _N + r")\s+reps?" + _TAIL, re.I)
_BARE = re.compile(r"^\W*(?:(?:i\s+)?(?:got|did|hit)\s+)?(?P<reps>" + _N + ")" + _TAIL, re.I)
_FAILED = re.compile(r"^\W*(?:i\s+)?(?:failed|missed|could ?n'?t (?:finish|get)|did ?n'?t (?:finish|get))(?: it| them| the set| the last (?:rep|one|set))?"
                     r"(?:\s+(?:at|on|after|,)?\s*(?:rep\s+)?(?P<reps>" + _N + r")(?:\s+reps?)?)?(?:\s*(?:at|with)\s*(?P<w>" + _W + ")" + _UNIT + r")?\W*$", re.I)
_DONE = re.compile(r"^\W*(?:(?:ok|okay|alright|and)[,.!]?\s+)?(?:(?:i'?m |that'?s (?:the |that )?|set (?:is )?|all )?(?:done|finished|complete|completed)(?: with (?:that|the) set)?|"
                   r"did it|got it|got (?:them|em) all|that'?s (?:the|that) set|set done|set complete|next set)" + _TAIL, re.I)
_FEEL_ONLY = re.compile(r"^\W*" + _FEEL + r"\W*$", re.I)
_SKIP = re.compile(r"^\W*(?:let'?s\s+)?(?:skip|pass on|drop|leave out)\s+(?:this|that|the)(?: exercise| one| lift)?(?: today| for today)?\W*$|"
                   r"^\W*(?:next exercise|next lift|move on(?: to the next (?:one|exercise))?|skip (?:it|ahead)|let'?s move on)\W*$", re.I)
_SKIP_BARE = re.compile(r"^\W*(?:skip|next|pass)\W*$", re.I)
_SWAP = re.compile(r"^\W*(?:(?:can|could) (?:you|we|i)\s+)?(?:please\s+)?(?:(?:swap|switch|substitute|replace|change)\s+(?:this|that|it|the exercise|(?P<what>[\w -]+?))"
                   r"(?:\s+(?:exercise|one|out))?(?:\s+(?:for|with|to)\s+(?:something else|another (?:one|exercise)|a different (?:one|exercise)|(?P<to>[\w -]+?)))?|"
                   r"(?:give me|i (?:want|need)|do you have|is there)\s+(?:something else|an alternative|another (?:option|exercise)|a different (?:exercise|one)|a substitute)|"
                   r"i (?:can'?t|cannot|don'?t want to) do (?:this|that|these|those|(?P<what2>[\w -]+?))(?: one| exercise)?(?: today| right now| here)?|"
                   r"(?:the|all the|every) (?P<busy>[\w -]+?) (?:is|are) (?:all )?(?:taken|busy|in use|being used|broken|occupied|full)|"
                   r"(?P<gear>(?:i|we) (?:don'?t|do not) have (?:a |an |the |any )?(?:barbell|bench|cables?|machine|pull[- ]?up bar|dumb ?bells?|kettle ?bells?|sled|rack|trap bar|bands?)(?: here| today)?|"
                   r"(?:there(?:'s| is) no|no) (?:barbell|bench|cables?|machine|pull[- ]?up bar|dumb ?bells?|kettle ?bells?|sled|rack|trap bar|bands?)(?: here| today)?))\W*$", re.I)
# what can be "taken" or "busy" in a gym; a bare "machine" or "bar" could be the coffee machine or the pub
_EQUIP = re.compile(r"(?:(?:squat|power|smith|cable|pull[- ]?up|chin[- ]?up|dip|trap|leg press|lat pull ?down|flat|incline|adjustable|weight)\s+)?"
                    r"(?:bench|rack|barbell|cable|station|dumb ?bell|kettle ?bell|sled|band|platform|plate|weight|treadmill|rower)(?:es|s)?|"
                    r"(?:pull[- ]?up|chin[- ]?up|dip|trap)\s+bars?|"
                    r"(?:smith|cable|leg press|lat pull ?down|chest press|leg curl|leg extension|rowing)\s+machines?", re.I)
_SWAP_CLEAR = re.compile(r"\b(?:exercises?|lift|movement)\b", re.I)
_STOP_CLEAR = re.compile(r"\b(?:workout|work out|working out|training|gym session|lifting)\b", re.I)
_STATUS_CLEAR = re.compile(r"\b(?:exercises?|sets?|lift|rest)\b", re.I)
_STOP = re.compile(r"^\W*(?:(?:ok|okay|alright)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?(?:(?:let'?s\s+)?(?:end|stop|finish|quit|cancel|wrap up)\s+(?:my|the|this|our)?\s*" + _SESSION_NOUN +
                   r"|i'?m (?:done|finished) (?:with (?:my|the|this) " + _SESSION_NOUN + r"|training|lifting|working out|for (?:today|the day|tonight))|"
                   r"that'?s (?:it|all|enough) for (?:today|the day|tonight|this " + _SESSION_NOUN + r")|(?:my |the )?" + _SESSION_NOUN + r" (?:is )?(?:done|over|finished|complete)|"
                   r"(?:let'?s\s+)?call it(?: a day| there| here)?)" + _END, re.I)
_STATUS = re.compile(r"^\W*(?:(?:ok|okay|so)[,.!]?\s+)?(?:jarvis[,.!]?\s+)?(?:what(?:'s| is) next|what(?:'s| is) (?:the|my) (?:next|current) (?:exercise|set|lift)|what (?:exercise|set|lift) (?:is it|am i on|are we on)|"
                     r"where (?:was i|were we|am i|are we)|how many (?:sets|exercises) (?:are |do i have )?left|how many more sets|what did i (?:do|get|lift) last time|"
                     r"what(?:'s| is| was) (?:the|my) (?:target|weight|load)(?: (?:for|on) (?:this|that)(?: set| one)?)?|how (?:much|long) (?:is|should) (?:my|the|i) rest(?: be)?|"
                     r"how long (?:do|should) i rest|what weight(?: should i use| am i using)?|how much weight)" + _END, re.I)
_REPEAT = re.compile(r"^\W*(?:(?:can|could) you\s+)?(?:please\s+)?(?:repeat(?: that)?|say (?:that|it) again|what was that|come again|again)(?: please)?\W*$", re.I)
_LOG_HEAD = re.compile(r"^\W*(?:(?:hey |ok |okay )?jarvis[,.!]?\s+)?(?:please\s+)?(?:log|record|add|write down|put down|save)\s+(?:a|my|this|that|one)?\s*(?:set|lift)\b[:,]?\s*(?:of\s+|for\s+|on\s+)?", re.I)
_DID = re.compile(r"^\W*(?:(?:hey |ok |okay )?jarvis[,.!]?\s+)?i (?:just\s+)?(?:did|got|hit|benched|pressed|squatted|deadlifted|lifted)\s+", re.I)
_MAYBE = re.compile(r"\b(?:work ?out|workouts?|train(?:ing|ed)?|gym|lift(?:s|ing|ed)?|goal|plan|program(?:me)?|routine|split|sets?|reps?|progress(?:ing)?|stronger|bigger|fitter|"
                    r"max|deload|benched|squatted|deadlifted|pressed|days? a week|times a week|" +
                    "|".join(sorted({a for a in coach._ALIAS if len(a) > 3}, key=len, reverse=True)) + r")\b", re.I)


def maybe(text: str, pca_on: bool = False) -> bool:
    """Could this line be a trainer request at all, with no workout running? The program's wider words (run, test,
    hurts...) count only when his plan IS Captain America (pca_on); its name counts for anyone."""
    text = text or ""
    return bool(_MAYBE.search(text) or _CAPTAIN.search(text) or pca_on and _PCA_MAYBE.search(text))


# -- Project Captain America (tools/pca) ---------------------------------------------------------------------------
# Taken only when his plan IS the program (pca["on"]), or for the briefing/start/status when the sentence names it
# ("captain america", "my mission"). Every sentence also has a normal meaning ("I ran into Mike", "the test is
# tomorrow", "run it again", "my back is killing me" from a long day): a line is taken only when its shape is
# unmistakable (a number with a unit, a body part with a pain word, 'as planned'), else it takes the normal path.
_BODY = ("knee", "ankle", "foot", "feet", "shin", "calf", "calves", "hamstring", "quad", "hip", "groin", "achilles", "leg", "glute", "thigh",
         "toe", "heel", "it band", "back", "lower back", "upper back", "spine", "shoulder", "rotator cuff", "neck", "chest", "pec", "rib",
         "elbow", "wrist", "hand", "forearm", "finger", "bicep", "tricep", "arm")
_AREA = r"(?:(?:left|right)\s+)?(?:(?:lower|upper|inner|outer|front|back of (?:my|the))\s+)?(?:" + "|".join(
    sorted((re.escape(b) for b in _BODY), key=len, reverse=True)) + r")s?"
_N10 = r"(?:10|[0-9]|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_PRE = r"^\W*(?:(?:hey|ok|okay|so|alright|all right|and|but|well|also|oh|yeah)[,.!]?\s+)*(?:jarvis[,.!]?\s+)?(?:please\s+)?"
_LVL = r"(?:[,.;:]?\s*(?:(?:about|around|maybe|like|probably|it'?s|at|i'?d say|call it)\s+)*(?:a |an )?(?P<lvl>" + _N10 + r")(?:\s*(?:out of|/)\s*(?:10|ten))?)?"
_LIMP = r"(?:[,.]?\s*(?:and\s+)?(?P<moves>i'?m limping|i can'?t (?:put weight on it|run|press|bend it|straighten it)|it changes how i (?:move|walk|run)))?"
_PCA_MAYBE = re.compile(r"\b(?:mission|brief(?:ing)?|captain america|ran|run|runs|running|ruck(?:ed|ing)?|pt|hurts?|hurting|sore|tight|pain|aches?|"
                        r"test|time trial|two[- ]mile|push[- ]?ups?|plank|sprint[- ]drag|as planned|full (?:session|workout)|easier|lighter|shorter|"
                        r"fine now|songs?|music|tracks?|tunes?|playing|speech|cadences?|this one|skip it|something else|" + "|".join(re.escape(b) for b in _BODY) + r")s?\b", re.I)

_CAPTAIN = re.compile(r"\bcaptain america\b", re.I)          # the only words that route anything for a user without the program
_BRIEF = re.compile(_PRE + r"(?:what(?:'s| is) (?:my|the|today'?s) mission(?: (?:for )?today| for the day)?|"
                    r"(?:give me |let'?s (?:have|get|do) |read me )?(?:my |the |today'?s )?mission brief(?:ing)?|brief me(?: on (?:my|the|today'?s) mission)?|"
                    r"give me (?:my|the|today'?s) briefing|what(?:'s| is) on (?:the|my) mission(?: today)?)" + _END, re.I)
_START_MISSION = re.compile(_LEAD + r"(?:(?:start|begin|launch|resume|kick off)\s+(?:my|the|today'?s)\s+mission|let'?s (?:start|begin|do) (?:the |my |today'?s )?mission|"
                            r"mission (?:start|go))" + _END, re.I)
_BEGIN_PCA = re.compile(_LEAD + r"(?:start|begin|launch|kick off)\s+(?:project\s+)?captain america" + _END, re.I)
_PCA_STATUS = re.compile(_PRE + r"(?:how(?:'s| is) (?:project )?captain america (?:going|coming along)|where am i in (?:project )?captain america|"
                         r"what week (?:am i|are we) (?:in|on)(?: (?:of|in) (?:the program|captain america))?)" + _END, re.I)
_PCA_PROGRESS = re.compile(_PRE + r"(?:how am i (?:doing|progressing) (?:in|with) (?:the program|captain america|my mission)|show (?:me )?my test results)" + _END, re.I)
_OVERRIDE = re.compile(_PRE + r"(?:i feel (?:fine|good|great|ok|okay|strong)[,.;]?\s*(?:so\s+)?)?(?:(?:just\s+)?(?:run|do|keep|leave|take) (?:it|today|the session|the workout|everything) "
                       r"(?:as planned|as written|like planned|the way it was planned)|give me the (?:full|whole|original|planned) (?:session|workout|mission)|"
                       r"(?:i'?ll|let me|i want to|i'?m going to) do the (?:full|whole|original|planned) (?:session|workout)|"
                       r"(?:cancel|undo|drop|ignore) (?:the|your|those|that|today'?s) (?:changes?|adjustments?))" + _END, re.I)
_PAIN = re.compile(_PRE + r"(?:(?:my|the|this|i'?ve got (?:a|some)|i have (?:a|some))\s+)?(?P<area>" + _AREA + r")(?:\s+(?:pain|is in pain))?\s+"
                   r"(?:(?:really |still |kind of |a bit |a little |pretty )*(?:hurts|is hurting|is sore|feels sore|is tight|feels tight|feels tweaked|is aching|aches|"
                   r"is (?:killing|bugging|bothering) me|is acting up|is flaring up|is (?:a bit |kind of |pretty |really |very )?(?:sore|tight|achy|stiff)|"
                   r"feels (?:a bit |kind of |pretty |really |very )?(?:sore|tight|achy|stiff|off)))(?:\s+(?:a bit|a little|a lot|today|right now|again|now))*"
                   + _LVL + _LIMP + r"\W*$", re.I)
_PAIN_IN = re.compile(_PRE + r"(?:i (?:have|feel|'?ve got)|there'?s|i'?m feeling)\s+(?:some |a (?:bit of |little )?)?(?:pain|soreness|tightness)\s+in\s+(?:my |the )?(?P<area>"
                      + _AREA + r")(?:\s+(?:today|right now|again|now))*" + _LVL + _LIMP + r"\W*$", re.I)
_PAIN_DID = re.compile(_PRE + r"i (?:think i )?(?:hurt|tweaked|strained|pulled|twisted|rolled|jammed) my (?P<area>" + _AREA + r")(?:\s+(?:today|this morning|again))*"
                       + _LVL + _LIMP + r"\W*$", re.I)
_CLEAR = re.compile(_PRE + r"(?:(?:my|the)\s+)?(?P<area>" + _AREA + r")(?:\s+pain)?\s+(?:is (?:fine|good|better|ok|okay|all good|healed|back to normal|gone)|"
                    r"feels (?:fine|good|better|normal)|(?:doesn'?t|does not) hurt(?: any ?more)?|(?:has )?(?:gone away|cleared up|stopped hurting))"
                    r"(?:\s+(?:now|again|today|any ?more))*\W*$|" + _PRE + r"(?:clear|remove|close) (?:the |my )?(?P<area2>" + _AREA + r")? ?pain(?: flag)?" + _END, re.I)
_LEVEL_REPLY = re.compile(r"^\W*(?:(?:about|around|maybe|like|probably|it'?s|i'?d say|call it|a|an)\s+)*(?P<lvl>" + _N10 + r")(?:\s*(?:out of|/)\s*(?:10|ten))?\W*$", re.I)
# a number with what it measures
_NUMW = r"(?:\d{1,3}(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|forty-five|fifty|sixty|ninety)"
_MIN = re.compile(r"\b(?P<n>" + _NUMW + r")[- ]?(?:minutes?|mins?)\b|\b(?P<h>(?:an|one) hour(?: and a half)?|half an hour|an hour and a half|(?:1|one) and a half hours?|two hours)\b", re.I)
_MILES = re.compile(r"\b(?P<n>" + _NUMW + r"|half a)[- ]?(?:miles?|mi)\b|\b(?P<km>\d{1,2}(?:\.\d+)?)\s?(?:k|km|kilometers?|kilometres?)\b", re.I)
_CLOCK = re.compile(r"\b(?:in|at|of|was|time(?: of)?|ran|did)\s+(?P<m>\d{1,2}):(?P<s>[0-5]\d)\b", re.I)
_RPE = re.compile(r"\b(?:effort|rpe|intensity)(?: (?:of|was|at))?\s*(?:a |an )?(?P<a>" + _N10 + r")\b|\b(?:a |an |at )?(?P<b>" + _N10 + r")\s*(?:out of|/)\s*(?:10|ten)\b|"
                  r"\bat (?:a |an )?(?P<c>" + _N10 + r")\b(?! ?(?:minutes?|mins?|miles?|mi|k|km|am|pm|o'?clock|:))", re.I)
_RPE_WORD = re.compile(r"\b(?P<w>really easy|super easy|very easy|easy|light|chill|moderate|medium|steady|very hard|really hard|super hard|brutal|smoked|smoker|hard|tough)\b", re.I)
_RPE_OF = {"really easy": 2, "super easy": 2, "very easy": 2, "easy": 3, "light": 3, "chill": 3, "moderate": 5, "medium": 5, "steady": 5,
           "hard": 7, "tough": 7, "very hard": 9, "really hard": 9, "super hard": 9, "brutal": 9, "smoked": 9, "smoker": 9}
_RUN_KIND = re.compile(r"\b(easy|long|recovery|tempo|intervals?|fartlek)\b", re.I)
_RUN = re.compile(_PRE + r"(?:(?:log|record|add|save) (?:my|a|the|this|that|today'?s) (?:\w+ )?run\b|i (?:just )?(?:ran|jogged|did (?:a|my|the) (?:\w+ )?run)\b|"
                  r"(?:i )?went for an? (?:\w+ )?run\b|(?:my|that|the|today'?s) (?:\w+ )?run (?:was|took)\b|(?:just )?ran\b)(?P<rest>.*)$", re.I)
_TWO_MILE_WAS = re.compile(_PRE + r"(?:my|the|that) (?:two|2)[- ]mile(?: run)?(?: time)? (?:was|took|came in at|is)\s+(?P<m>\d{1,2}):(?P<s>[0-5]\d)\W*$", re.I)
_TEST_WORD = re.compile(r"\b(?:test(?:ed)?|time trial|trial)\b", re.I)
_EVENT = (("run", re.compile(r"\b(?:two|2)[- ]mile|\btime trial\b|\b2mr\b", re.I)), ("hrp", re.compile(r"\b(?:hand[- ]release )?push[- ]?ups?\b|\bhrp\b", re.I)),
          ("plk", re.compile(r"\bplank\b|\bplk\b", re.I)), ("sdc", re.compile(r"\bsprint[- ]drag(?:[- ]carry)?\b|\bsdc\b", re.I)),
          ("mdl", re.compile(r"\bdead ?lift\b|\bmdl\b|\btrap[- ]bar\b", re.I)))
_TIME_VALUE = re.compile(r"\b(?P<m>\d{1,2}):(?P<s>[0-5]\d)\b")
_INT_VALUE = re.compile(r"(?<![\d:.])\b(?P<n>\d{1,3})\b(?![:.]\d)(?:\s*(?:lbs?|pounds?|reps?|push[- ]?ups?))?(?:\s+for\s+(?P<reps>\d))?")
# "pt" alone is as often physical therapy ("I had pt with my therapist"): only the unit's PT is taken
_EXTERNAL_PT = re.compile(_PRE + r"(?:(?:we|i) (?:just )?(?:had|did|finished)|(?:just )?(?:had|did)) (?:our |the )?"
                          r"(?P<who>(?:unit|company|platoon|squad|battalion) pt|pt formation|pt with (?:the|my|our) (?:unit|company|platoon|squad)|morning pt)\b"
                          r"(?P<rest>.*)$", re.I)
_EXTERNAL_RUCK = re.compile(_PRE + r"(?:(?:we|i) (?:just )?rucked\s+(?P<miles>" + _NUMW + r")[- ]?(?:miles?|mi)\b|"
                            r"(?:(?:we|i) (?:just )?(?:did|had|went on|finished) )?an? (?P<miles2>" + _NUMW + r")[- ]?mile ruck(?: march)?\b)(?P<rest>.*)$", re.I)
_EXTERNAL_UNIT = re.compile(_PRE + r"(?:(?:we|i) (?:just )?(?:had|did) )?(?:a |an )?(?:unit|company|platoon) (?P<what>leg day|strength(?: session)?|lift(?:ing)?(?: session)?|field day)(?P<rest>.*)$", re.I)
_PAST_DAY = re.compile(r"\b(?:yesterday|last night|last week|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                       r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday) (?:morning|night|evening))\b", re.I)
_GO = re.compile(r"^\W*(?:(?:ok|okay|alright|and)[,.!]?\s+)?(?:go|going|let'?s go|i'?m set|set|ready|starting(?: now)?|start(?:ing)? now|now)\W*$", re.I)
_GO_CLEAR = re.compile(r"^\W*(?:(?:ok|okay|alright)[,.!]?\s+)?(?:start the (?:hold|plank|clock|timer)|start (?:the )?(?:first )?(?:rep|interval)|i'?m (?:starting|going) (?:the|my) (?:hold|plank|first rep))\W*$", re.I)
_REP = re.compile(r"^\W*(?:(?:ok|okay|alright|and)[,.!]?\s+)?(?:(?:that'?s )?(?:the |that |this )?rep (?:is )?(?:done|finished|complete)|next rep|"
                  r"(?:done|finished) (?:with )?(?:the |that |this )?rep|rep)(?:\s+\d{1,2})?\W*$", re.I)


def _num(word) -> float | None:
    if word is None:
        return None
    word = str(word).lower().strip()
    try:
        return float(word)
    except ValueError:
        pass
    return {"a": 1, "an": 1, "half a": 0.5, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
            "fifty": 50, "sixty": 60, "ninety": 90}.get(word, _NUM.get(word))


def _minutes(text: str) -> float | None:
    m = _MIN.search(text)
    if not m:
        return None
    if m["h"]:
        h = m["h"].lower()
        return 30.0 if h.startswith("half") else 120.0 if h.startswith("two") else 90.0 if "half" in h else 60.0
    return _num(m["n"])


def _miles(text: str) -> float | None:
    m = _MILES.search(text)
    if not m:
        return None
    return round(float(m["km"]) / 1.609344, 2) if m["km"] else _num(m["n"])


def _rpe(text: str) -> int | None:
    m = _RPE.search(text)
    if m:
        v = _n(m["a"] or m["b"] or m["c"])
        return v if v is not None and 1 <= v <= 10 else None
    w = _RPE_WORD.search(text)
    return _RPE_OF[w["w"].lower()] if w else None


def _event(text: str) -> str | None:
    return next((ev for ev, rx in _EVENT if rx.search(text)), None)


def _test_value(ev: str, text: str) -> str | None:
    """The result as log_test takes it: m:ss for the timed events, a whole number for push-ups, pounds for a 3-rep deadlift."""
    if ev in ("run", "plk", "sdc"):
        m = _TIME_VALUE.search(text)
        return f"{int(m['m'])}:{m['s']}" if m else None
    for m in _INT_VALUE.finditer(text):
        if ev == "mdl" and m["reps"] and m["reps"] != "3":
            return None                                           # "340 for 5" is not a three-rep max
        if ev == "hrp" and 1 <= int(m["n"]) <= 150 or ev == "mdl" and 95 <= int(m["n"]) <= 700:
            return m["n"]
    return None


def _run_args(rest: str) -> dict:
    args = {}
    clock = _CLOCK.search(" " + rest)
    minutes = _minutes(rest)
    if clock:
        args["minutes"] = round(int(clock["m"]) + int(clock["s"]) / 60, 2)
    elif minutes:
        args["minutes"] = minutes
    miles = _miles(rest)
    if miles:
        args["distance_mi"] = miles
    rpe = _rpe(rest)
    if rpe:
        args["rpe"] = rpe
    kind = _RUN_KIND.search(rest)
    if kind:
        args["kind"] = {"interval": "intervals"}.get(kind.group(1).lower(), kind.group(1).lower())
    return args


_RUN_TALK = frozenset((
    "in for about around roughly almost over under just at a an the of and with my this that it was felt pretty really very kind bit "
    "effort rpe intensity out ten minutes minute min mins miles mile mi km k kilometers kilometres hour hours half total time pace "
    "easy light chill moderate medium steady hard tough brutal super smoked long tempo recovery interval intervals fartlek run jog "
    "today tonight morning evening afternoon now so then took").split())


_EXT_TALK = _RUN_TALK | frozenset("ruck march legs leg day strength session lifting lift weights squats sprints unit pt workout".split())


def _only_run(rest: str, vocabulary: frozenset = _RUN_TALK) -> bool:
    """Is what follows 'I ran' nothing but the run's numbers? 'into Mike', 'the numbers' and 'late' make it something else."""
    words = re.findall(r"[a-z']+", re.sub(r"\d+(?:[.:]\d+)?", " ", rest.lower()))
    return all(w in vocabulary or w in _NUM for w in words)


def _external(said: str):
    """Unit PT, a ruck, a unit strength session -> log_unit_pt arguments (what he left out is asked by the skill)."""
    if _PAST_DAY.search(said):
        return None                                               # the skill logs today; another day goes the normal way
    m = _EXTERNAL_RUCK.match(said)
    if m:
        rest = m["rest"] or ""
        args = {"kind": "ruck", "miles": _num(m["miles"] or m["miles2"])}
    else:
        m = _EXTERNAL_UNIT.match(said)
        if m:
            what = m["what"].lower()
            rest = m["rest"] or ""
            args = {"kind": "field" if "field" in what else "unit_strength"}
        else:
            m = _EXTERNAL_PT.match(said)
            if not m:
                return None
            rest = m["rest"] or ""
            low = rest.lower()
            if m["who"].lower() == "morning pt" and not re.search(r"\b(?:run|ran|running|ruck(?:ed)?|workout|miles?|sprints?|legs?|strength)\b", low):
                return None
            kind = "ruck" if "ruck" in low else "unit_strength" if re.search(r"\b(?:legs?|strength|lift(?:ing)?|weights|squats?|deadlifts?)\b", low) else \
                "unit_run" if re.search(r"\b(?:run|ran|running|miles?|sprints?)\b", low) else "field" if "field" in low else ""
            args = {"kind": kind}
    if not _only_run(rest, _EXT_TALK):
        return None                                               # "company field day was fun": talk about it, not a log
    minutes, rpe = _minutes(rest), _rpe(rest)
    miles = args.get("miles") or _miles(rest)
    args.update({k: v for k, v in (("minutes", minutes), ("rpe", rpe), ("miles", miles)) if v})
    return "log_unit_pt", {k: v for k, v in args.items() if v not in (None, "", 0)}


def _answer(said: str, ask: dict):
    """His reply to the one question the coach asked ('How bad is it, 0 to 10?' -> 'about a 5'). Anything else: None,
    and the question is not asked again."""
    need, args, skill = ask.get("need"), dict(ask.get("args") or {}), ask.get("skill")
    if need == "level":
        m = _LEVEL_REPLY.match(said) or re.match(_PRE + r"(?:it'?s |it is )?(?:about |around )?(?:a |an )?(?P<lvl>" + _N10 + r")(?:\s*(?:out of|/)\s*(?:10|ten))\W*$", said, re.I)
        if m and _n(m["lvl"]) is not None and 0 <= _n(m["lvl"]) <= 10:
            return skill, {**args, "level": _n(m["lvl"])}
        return None
    got = {}
    if need == "minutes":
        m = _ANSWER_MIN.fullmatch(said)
        if m:
            got["minutes"] = _minutes(m["min"]) or _num(re.sub(r"^(?:a|an)\s+", "", m["min"].strip(), flags=re.I))
            if m["effort"] is not None:                       # a tail is allowed only when it IS the effort, said as one
                effort = _TAIL_RPE.fullmatch(m["effort"].strip())
                if not effort:
                    return None
                said_n = effort["r"] or effort["n2"]
                got["rpe"] = _n(said_n) if said_n else _answer_rpe(effort)
    elif need == "rpe":
        m = _ANSWER_RPE.fullmatch(said)
        if m:
            got["rpe"] = _answer_rpe(m)
    elif need == "kind":
        m = _ANSWER_KIND.fullmatch(said)
        if m:
            what = m["kind"].lower()
            got["kind"] = "ruck" if "ruck" in what else "unit_run" if re.match(r"run|running|unit run", what) else \
                "field" if what in ("field", "field day", "mixed") else "unit_strength"
    if not got.get(need) or got.get("rpe") is not None and not 1 <= got["rpe"] <= 10:
        return None
    return skill, {**args, **{k: (int(v) if k == "rpe" else v) for k, v in got.items()}}


# The whole utterance must be one of these answer shapes; "set a timer for 5 minutes" or "turn on the light" is not an
# answer, and drops the question (it was handed over once) so the sentence takes its normal path.
_ANS_HEAD = r"\W*(?:(?:about|around|maybe|like|roughly|probably|i'?d say|call it|it was|it took|that was|we did|i did)\s+)*"
_ANSWER_RPE = re.compile(_ANS_HEAD + r"(?:(?:a|an)\s+)?(?:(?P<n>" + _N10 + r")(?:\s*(?:out of|/)\s*(?:10|ten))?|"
                         r"(?:(?:pretty|really|very|kind of|super|fairly)\s+)?(?P<w>easy|light|moderate|medium|steady|hard|tough|brutal))\W*", re.I)
_ANSWER_MIN = re.compile(_ANS_HEAD + r"(?P<min>(?:a |an )?(?:\d{1,3}(?:\.\d)?|" + "|".join(_NUM) + r"|thirty|forty|forty-five|fifty|sixty|ninety)"
                         r"(?:\s*(?:minutes?|mins?))?|(?:an|one) hour(?: and a half)?|half an hour|an hour and a half)"
                         r"(?:\s*[,;]\s*(?P<effort>.+?))?[.!?\s]*", re.I)
# an effort after the minutes must say it is one: never a bare number ("1,5" is not 1 minute at 5)
_TAIL_RPE = re.compile(r"(?:(?:rpe|effort|intensity)\s*(?P<r>" + _N10 + r")|(?:(?:a|an)\s+)?(?P<n>" + _N10 + r")\s*(?:out of|/)\s*(?:10|ten)|"
                       r"(?:a|an)\s+(?P<n2>" + _N10 + r")|(?:(?:pretty|really|very|kind of|super|fairly)\s+)?(?P<w>easy|light|moderate|medium|steady|hard|tough|brutal))"
                       r"[.!?\s]*", re.I)
_ANSWER_KIND = re.compile(r"\W*(?:(?:it was|that was|we did|we had|just)\s+)?(?:(?:a|an|the)\s+)?(?P<kind>unit run|run|running|ruck(?: march)?|"
                          r"strength(?: session)?|leg day|lifting|weights|field day|field|mixed)(?:\s+(?:session|pt|workout))?\W*", re.I)


_CHEST = re.compile(_PRE + r"(?:(?:i(?:'m| am) having|i have|i(?:'ve| have) got|i(?:'m| am) feeling|i feel|there'?s|got)\s+)?(?:(?:some|a|a bit of|a little)\s+)?"
                    r"(?:chest (?:pains?|tightness|pressure)|(?:pains?|tightness|pressure) in (?:my|the) chest|"
                    r"my chest (?:hurts|is hurting|is tight|feels tight|feels heavy|aches|is aching)(?: when i (?:breathe|breath|move|walk|run|lie down))?)"
                    r"(?:\s+(?:right now|now|today|again))?\W*$", re.I)
# A result for THE test on now, as the whole utterance: "44", "I got 44", "44 push-ups", "2:05", "plank 2:05", "17:52".
_RES_HEAD = (r"^\W*(?:(?:ok|okay|so|alright)[,.!]?\s+)?(?:(?:i\s+)?(?:got|did|hit|made|managed|held(?: it for)?|ran(?: it in)?)\s+|"
             r"(?:it|that|my (?:result|time|score)|the (?:result|time|score)) (?:was|is)\s+)?")
_RES_NAME = {"hrp": r"(?:hand[- ]release )?push[- ]?ups?", "plk": r"plank", "sdc": r"sprint[- ]drag[- ]carry|sprint drag carry|sdc",
             "run": r"(?:two|2)[- ]mile(?: run)?|time trial", "mdl": r"(?:trap[- ]bar )?dead ?lift"}


def _result(ev: str, said: str, prompted: bool) -> str | None:
    name = _RES_NAME[ev]
    if ev in ("plk", "sdc", "run"):
        m = re.fullmatch(_RES_HEAD + r"(?:(?P<name>" + name + r")(?: test)?(?:\s+(?:was|in|at))?[:,]?\s+)?(?P<m>\d{1,2}):(?P<s>[0-5]\d)(?:\s+on the test)?\W*", said, re.I)
        return f"{int(m['m'])}:{m['s']}" if m and (m["name"] or prompted) else None
    unit = r"reps?|" + name if ev == "hrp" else r"lbs?|pounds?"
    m = re.fullmatch(_RES_HEAD + r"(?:(?P<name>" + name + r")(?: test)?[:,]?\s+)?(?P<n>\d{1,3})(?:\s*(?P<unit>" + unit + r"))?"
                     + (r"(?:\s+for\s+(?:3|three)|\s+triple)?" if ev == "mdl" else "") + r"(?:\s+on the test)?\W*", said, re.I)
    if not m or not (m["name"] or m["unit"] or prompted):
        return None
    n = int(m["n"])
    return m["n"] if (1 <= n <= 150 if ev == "hrp" else 95 <= n <= 700) else None


def _answer_rpe(m) -> int:
    if m["n"] is not None:
        return _n(m["n"])
    word = m["w"].lower()
    hard = re.search(r"\b(?:really|very|super)\s+" + word, m.group(0), re.I)
    return {"easy": 2, "hard": 9, "tough": 9}.get(word, 9) if hard and word in ("easy", "hard", "tough") else _RPE_OF.get(word, 5)


# Jarvis's workout music (tools/pca_music). Taken only while the program is on, the DJ is switched on (music["enabled"]) and a
# session is open or a pick was made in the last 30 minutes; whole sentences only. MUSIC is checked in this order and the
# first match wins. A phrase that names the music (song, track, tune, music, beat, speech, cadence) is enough on its own; a
# bare one ("skip it", "I don't like this one", "like this one") counts only when the LAST thing Jarvis said was a music
# pick (music["prompted"], set by brain/llm) and never as the reply to a workout line. When unsure: not captured.
# "change the song" is a move on (neutral), never a skip: a skip bans the song for good.
_SONG = r"(?:song|track|tune|music|beat)"
_MUSIC = (
    ("music_next", False, re.compile(_PRE + r"(?:next (?:song|track|tune)|play (?:me )?(?:another|a different) (?:song|track|tune)|(?:give me )?(?:a different|another) (?:song|track|tune)|"
                                     r"(?:change|switch) (?:the|this|that|up the) (?:song|track|tune|music)|play (?:me )?(?:some )?(?:different|other) music)" + _END, re.I)),
    ("music_skip", False, re.compile(_PRE + r"(?:(?:skip|pass on) (?:this|that|the) (?:song|track|tune)|i (?:really )?(?:don'?t|do not) (?:like|want) (?:this|that) " + _SONG + r"|"
                                     r"i (?:hate|can'?t stand) (?:this|that) " + _SONG + r"|not (?:this|that) (?:song|track|tune))" + _END, re.I)),
    ("music_like", False, re.compile(_PRE + r"(?:i (?:really )?(?:like|love|dig) (?:this|that) " + _SONG + r"|(?:like|love) this (?:song|track|tune)|"
                                     r"(?:this|that) (?:song|track) (?:is fire|slaps|goes hard|is good|is great)|good song)" + _END, re.I)),
    ("music_speech", False, re.compile(_PRE + r"(?:play|put on|give me) (?:me )?(?:a |some )?(?:motivational |motivation |pump[- ]up )?speech" + _END, re.I)),
    ("music_cadence", False, re.compile(_PRE + r"(?:play|put on|give me) (?:me )?(?:an? |some )?(?:(?:army|military|running|marching|ranger|airborne) )+cadences?(?: calls?)?" + _END, re.I)),
    # Jarvis Link (the phone app): pause / resume / what is playing. Worded only: each names the music.
    ("music_pause", False, re.compile(_PRE + r"(?:pause (?:the |my |this )?(?:music|song|track|tune)|(?:put|hold) (?:the|my) music on pause)" + _END, re.I)),
    ("music_resume", False, re.compile(_PRE + r"(?:(?:resume|unpause|continue) (?:the |my )?(?:music|song|track|tune)|"
                                       r"(?:play|start|turn) (?:the|my) music (?:again|back on)|(?:turn|put) (?:the|my) music back on)" + _END, re.I)),
    ("music_what", False, re.compile(_PRE + r"(?:what(?:'s| is) (?:this|that|the) (?:song|track|tune)(?: called)?|what (?:song|track|tune) is (?:this|that|playing|on)|"
                                     r"what(?:'s| is) (?:the name of )?(?:this|that) (?:song|track|tune)|who (?:sings|is singing) (?:this|that)(?: song| track)?|"
                                     r"what(?:'s| is) playing(?: right now| now)?)" + _END, re.I)),
    ("music_next", True, re.compile(_PRE + r"(?:play (?:me )?(?:something else|something different|another one)|next one)" + _END, re.I)),
    ("music_skip", True, re.compile(_PRE + r"(?:skip it|skip that(?: one)?|i (?:really )?(?:don'?t|do not) (?:like|want) (?:this|that) one|not this one)" + _END, re.I)),
    ("music_like", True, re.compile(_PRE + r"(?:i (?:really )?(?:like|love|dig) (?:this|that) one|(?:like|love) this one|good pick)" + _END, re.I)),
)


_LINK_ONLY = ("music_pause", "music_resume", "music_what")    # need music["link"]: the phone's Link paired and reachable


def _music_call(said: str, session: dict | None, pca: dict):
    music = pca.get("music") or {}
    if not music.get("enabled") or not (pca.get("open") or music.get("recent")):
        return None
    bare_ok = bool(music.get("prompted")) and not (session or {}).get("prompted")
    for skill, bare, rx in _MUSIC:
        if rx.match(said) and (bare_ok or not bare):
            if skill in _LINK_ONLY and not music.get("link"):
                return None                                       # no reachable Jarvis Link: his own player answers
            return skill, {}
    return None


def _pca_call(said: str, session: dict | None, pca: dict | None):
    """A Captain America line -> (skill, args), or None for the normal path."""
    pca = pca or {}
    on = bool(pca.get("on"))
    if pca.get("ask"):
        hit = _answer(said, pca["ask"])
        if hit:
            return hit
    if _BEGIN_PCA.match(said):
        return ("captain_start", {}) if on else ("captain_begin", {})
    if _PCA_STATUS.match(said) and (on or _CAPTAIN.search(said)):
        return "captain_status", {}
    if not on:
        return None                                               # "my mission", "the mission": his only when the program runs
    hit = _music_call(said, session, pca)
    if hit:
        return hit
    if _START_MISSION.match(said):
        return "captain_start", {}
    if _BRIEF.match(said):
        return "captain_briefing", {}
    current = pca.get("current") or {}
    prompted = bool((session or {}).get("prompted"))
    # inside an open session: 'go', 'rep done', a test result said as a bare value
    if current:
        if (current.get("kind") == "hold" or current.get("timed")) and (_GO_CLEAR.match(said) or prompted and _GO.match(said)):
            return "captain_go", {}
        held = re.fullmatch(r"\W*(?:(?:i\s+)?(?:held(?: it)?|got|did)\s+)?(\d{1,3}|\d:[0-5]\d)(?:\s*(?:seconds?|secs?))?\W*", said, re.I)
        if current.get("kind") == "hold" and held and (prompted or "held" in said.lower()):   # the bare-number rule stops at 60
            v = held.group(1)
            secs = int(v.split(":")[0]) * 60 + int(v.split(":")[1]) if ":" in v else int(v)
            if 0 < secs <= 600:
                return "workout_set", {"reps": secs}
        if current.get("reps") and _REP.match(said):
            return "captain_rep", {}
        if current.get("kind") == "test" and current.get("test"):
            value = _result(current["test"], said, prompted)
            if value:
                return "log_fitness_test", {"event": current["test"], "value": value}
    if _PCA_PROGRESS.match(said) or _PROGRESS_BARE.match(said):
        return "captain_progress", {}
    m = _PROGRESS.match(said)
    if m and not any(m[g] for g in ("lift1", "lift2", "lift3")):
        return "captain_progress", {}
    if pca.get("adjusted") and _OVERRIDE.match(said):
        return "captain_override", {}
    if _WHY.match(said):                                          # 'why is today easier': the program's changes are the trainer's
        hit = _request(said, session, True)
        if hit and hit[0] == "why_training_change":
            return hit
    if _CHEST.match(said):                                        # any chest-pain wording: the fixed safety line (tools/pca)
        return "report_pain", {"area": "chest"}
    for rx in (_PAIN, _PAIN_IN, _PAIN_DID):
        m = rx.match(said)
        if m:
            args = {"area": re.sub(r"\s+", " ", m["area"].strip().lower())}
            if m["lvl"] is not None:
                args["level"] = _n(m["lvl"])
            if m["moves"]:
                args["changes_movement"] = True
            return "report_pain", args
    m = _CLEAR.match(said)
    if m and pca.get("pain"):
        area = (m["area"] or m["area2"] or "").strip().lower()
        return "clear_pain", {"area": area or "all"}
    # a test: the sentence says so (or the current item is one), names the event and gives the result
    ev = _event(said)
    if ev and _TEST_WORD.search(said):
        value = _test_value(ev, said)
        if value:
            return "log_fitness_test", {"event": ev, "value": value}
        return None
    hit = _external(said)
    if hit:
        return hit
    m = _TWO_MILE_WAS.match(said)
    if m:                                                         # no 'test' said: an ordinary two-mile run
        return "log_run", {"minutes": round(int(m["m"]) + int(m["s"]) / 60, 2), "distance_mi": 2}
    m = _RUN.match(said)
    if m and not _PAST_DAY.search(said):
        args = _run_args(m["rest"] or "") if _only_run(m["rest"] or "") else {}
        if args.get("minutes") or args.get("distance_mi") or (args.get("rpe") and re.match(_PRE + r"(?:my|that|the|today'?s) (?:\w+ )?run", said, re.I)):
            return "log_run", args
    return None


def _n(word) -> int | None:
    if word is None:
        return None
    word = str(word).lower()
    return int(word) if word.isdigit() else _NUM.get(word)


def _feel(match) -> dict:
    g = match.groupdict()
    out = {}
    rir = _n(g.get("rir")) if g.get("rir") else _n(g.get("rir2"))
    feel = (g.get("feel") or "").lower()
    if rir is not None:
        out["rir"] = min(rir, 6)
    elif feel in ("too easy", "easy", "light", "too light"):
        out["rir"] = 4
    elif feel in ("hard", "tough", "heavy", "brutal", "rough", "a grind", "a struggle", "barely", "too heavy", "too hard"):
        out["rir"] = 0
    if feel:
        out["feel"] = feel
    return out


def parse_set(text: str, prompted: bool = True) -> dict | None:
    """A set report -> {"reps", "weight_lbs", "rir", "failed", "feel"} (only the parts he said), or None.
    The forms that are just a word or a number ("done", "8", "easy") count only when prompted."""
    said = (text or "").strip()
    if not said or len(said) > 90:
        return None
    m = _REPS_AT.match(said)
    if m:
        return {"reps": _n(m["reps"]), "weight_lbs": float(m["w"]), **_feel(m)}
    m = _W_FOR.match(said)
    if m:
        return {"reps": _n(m["reps"]), "weight_lbs": float(m["w"]), **_feel(m)}
    m = _BY.match(said)
    if m:
        a, b = float(m["a"]), float(m["b"])
        reps, weight = (a, b) if a <= b else (b, a)            # the bigger number is the load
        if reps <= 50 and reps == int(reps):
            return {"reps": int(reps), "weight_lbs": weight, **_feel(m)}
    m = _FAILED.match(said)
    if m:
        out = {"failed": True, "rir": 0}
        if m["reps"]:
            out["reps"] = _n(m["reps"])
        if m["w"]:
            out["weight_lbs"] = float(m["w"])
        return out
    m = _REPS.match(said)
    if m:
        return {"reps": _n(m["reps"]), **_feel(m)}
    if not prompted:
        return None
    m = _BARE.match(said)
    if m and (_n(m["reps"]) or 0) <= 60:
        return {"reps": _n(m["reps"]), **_feel(m)}
    m = _DONE.match(said)
    if m:
        return {"done": True, **_feel(m)}
    m = _FEEL_ONLY.match(said)
    if m:
        return {"done": True, **_feel(m)}
    return None


def _plan_args(text: str) -> dict:
    args = {}
    goal = coach.parse_goal(re.sub(r"\b(?:fitness|training|workout|work out|gym|exercise|strength)\s+(?:plan|program|programme|routine|goal)\b", " ", text, flags=re.I))
    if goal:
        args["goal"] = goal
    days = _DAYS.search(text)
    if days and _n(days.group(1)):
        args["days_per_week"] = _n(days.group(1))
    minutes = _MINUTES.search(text)
    if minutes:
        args["session_minutes"] = int(minutes.group(1)) if minutes.group(1) else 30 if "half" in minutes.group(2).lower() else 60
    gear = _GEAR.search(text)
    if gear:
        args["equipment"] = gear.group(0).lower()
    return args


def _log_set(said: str):
    """'log a set of bench, 8 at 135' / 'I did 8 reps of bench at 135' / 'I benched 185 for 5' -> log_set arguments."""
    head = _LOG_HEAD.match(said)
    did = None if head else _DID.match(said)
    if not head and not did:
        return None
    body = said[(head or did).end():]
    verb = (did.group(0).split()[-1].lower() if did else "")
    key = {"benched": "bench_press", "squatted": "squat", "deadlifted": "deadlift"}.get(verb, "")
    found = re.search(r"(?P<reps>" + _N + r")(?:\s+reps?)?(?:\s+(?:of|on|for)\s+(?:the\s+)?(?P<ex>[a-z][a-z -]*?))?\s*(?:at|@|with)\s*(?P<w>" + _W + ")" + _UNIT, body, re.I) or \
        re.search(r"(?P<w>" + _W + ")" + _UNIT + r"\s+(?:for|x|times)\s+(?P<reps>" + _N + r")(?:\s+reps?)?(?:\s+(?:of|on)\s+(?:the\s+)?(?P<ex>[a-z][a-z -]*))?", body, re.I)
    if not found:
        return None
    name = found.group("ex") or body[:found.start()] or body[found.end():]
    name = re.sub(r"^\W*(?:of|on|for|the|my)\s+|\W+$", "", name.strip(), flags=re.I)
    name = re.sub(r"\s+(?:today|just now|this morning|earlier)$", "", name, flags=re.I)
    key = coach.key_of(name) or key
    if not key:
        return None
    args = {"exercise": name if coach.key_of(name) else coach.TAGS[key]["name"], "weight_lbs": float(found.group("w")), "reps": _n(found.group("reps"))}
    rir = re.search(_RIR, body[found.end():], re.I)
    if rir:
        args["rir"] = _n(rir.group("rir")) if rir.group("rir") else int(rir.group("rir2"))
    return "log_set", args


def _request(said: str, session: dict | None, active: bool):
    if _START.match(said) or _START2.match(said):
        return "start_workout", {}
    if _TODAY.match(said):
        return "today_workout", {}
    m = _GOAL.match(said)
    if m:
        words = next(g for g in m.groups() if g)
        if coach.parse_goal(words) and _only_training(words) and (_TRAINED_GOAL.search(said) or coach.parse_goal(words) != "general"
                                                                    or re.search(r"\b(?:fit(?:ter|ness)?|shape|conditioning)\b", words, re.I)):
            return "set_training_goal", {"goal": coach.parse_goal(words), **{k: v for k, v in _plan_args(words).items() if k != "goal"}}
        return None
    m = _BUILD.match(said)
    if m and _TRAINED_PLAN.search(said[:m.start("rest")] if m["rest"] is not None else said):
        return "build_training_plan", _plan_args(m["rest"] or "")
    if m and _BARE_BUILD.match(said) and _only_training(m["rest"] or "") and _plan_args(m["rest"] or ""):
        return "build_training_plan", _plan_args(m["rest"] or "")
    m = _DAYS_NOW.match(said)
    if m and _n(m["n"]):
        return "build_training_plan", {"days_per_week": _n(m["n"])}
    m = _PROGRESS.match(said)
    if m:
        lift = next((g for g in (m["lift1"], m["lift2"], m["lift3"]) if g), "")
        if lift and not coach.key_of(lift):
            return None                                       # "how is my Spanish going" is not for the trainer
        return "fitness_progress", {}
    if (active or session) and _PROGRESS_BARE.match(said):
        return "fitness_progress", {}
    m = _WHY.match(said)
    if m:
        what = next((g for g in (m["a"], m["b"], m["c"], m["d"]) if g), "")
        if session or _WHY_ABOUT.search(said) and not re.search(r"\bvolume\b(?! per| for (?:my|the) )", said, re.I) or coach.key_of(what) \
                or active and re.fullmatch(r"today(?:'?s (?:workout|session))?", what.strip(), re.I):
            return "why_training_change", {"what": re.sub(r"^(?:my|the|today'?s)\s+", "", what.strip(), flags=re.I)[:60]}
    return _log_set(said)


def call_for(text: str, session: dict | None = None, active: bool = False, pca: dict | None = None):
    """(skill, arguments) for a line the trainer should take, else None. `session` is the running workout ({"prompted":
    the last thing Jarvis said was a workout line}) or None; `active` says he has a plan or logged training; `pca` is
    tools/pca.voice_context() (None: no Captain America routing beyond a sentence that names it)."""
    said = re.sub(r"\s+", " ", (text or "").strip())
    if not said or len(said) > 200:
        return None
    if pca is not None or _CAPTAIN.search(said):
        hit = _pca_call(said, session, pca)
        if hit:
            return hit
    if session is None:
        return _request(said, None, active) if maybe(said) else None
    prompted = bool(session.get("prompted"))
    if _STOP.match(said) and (prompted or _STOP_CLEAR.search(said)):         # "that's it for today" alone could end anything
        return "end_workout", {}
    m = _SWAP.match(said)
    if m:
        named = m["to"] or ""
        what = m["what"] or m["what2"] or ""
        if m["busy"] is not None:                                             # "the printer is busy" is not about the workout
            clear = ok = bool(coach.key_of(m["busy"]) or _EQUIP.fullmatch(m["busy"].strip()))
        else:
            ok = (not named or bool(coach.key_of(named))) and (not what or bool(coach.key_of(what)))
            clear = bool(named or what or m["gear"] or _SWAP_CLEAR.search(said))   # "change this" alone: only as a reply
        if ok and (clear or prompted):
            return "swap_exercise", ({"to": named} if named else {}) | ({"exercise": what} if what else {})
    if _SKIP.match(said) or prompted and _SKIP_BARE.match(said):
        return "workout_skip", {}
    if _STATUS.match(said) and (prompted or _STATUS_CLEAR.search(said)) or prompted and _REPEAT.match(said):
        return "workout_status", {}
    report = parse_set(said, prompted)
    if report is not None:
        args = {k: report[k] for k in ("reps", "weight_lbs", "rir", "failed", "feel") if report.get(k) is not None}
        return "workout_set", args
    return _request(said, session, True) if maybe(said) else None
