"""The live chat: a deterministic crowd reacting to the match, YouTube-live-chat style.

`chat_for(match)` returns every message of the broadcast in order. A steady baseline of chatter runs all match;
every important feed event sets off a burst (a goal is worth forty messages in under a minute). The crowd is written
to be HARD to curate: emoji floods and copy-paste chants, spam that talks about football, criticism that is harsh but
not abusive, abuse that collects likes, and a minority of comments that are actually worth reading.

Each message carries `truth` (kind, quality 0..1, hide, the event it reacts to). `public()` strips it, and the
author's allegiance with it: a platform does not know who you support, so neither does the ranking.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from gantry.common.rng import pick, roll, weighted
from gantry.sim.match import state_at

# (handle, side, badge, bias)  bias: analyst | fan | casual | troll | bot
_PEOPLE: list[tuple[str, str, str | None, str]] = [
    ("northstand_nev", "home", "member", "fan"), ("xG_enjoyer", "neutral", "member", "analyst"), ("tactics_tina", "neutral", "mod", "analyst"),
    ("TerraceTom", "home", None, "fan"), ("awaydays_amy", "away", "member", "fan"), ("pressbox_pat", "neutral", None, "analyst"),
    ("var_victim", "away", None, "fan"), ("late_winner_lou", "home", None, "fan"), ("the_false_nine", "neutral", "member", "analyst"),
    ("halfspace_hana", "home", None, "analyst"), ("BigSwede9", "away", None, "casual"), ("sunday_league_sid", "home", None, "casual"),
    ("keeper_union", "neutral", None, "analyst"), ("mia_from_oslo", "neutral", None, "casual"), ("luca_calcio", "away", None, "casual"),
    ("footy_dad_42", "home", "member", "casual"), ("golazo_gabi", "away", None, "fan"), ("ultra_1987", "home", None, "fan"),
    ("quiet_viewer", "neutral", None, "casual"), ("tikitaka_tom", "away", None, "analyst"), ("long_ball_larry", "home", None, "casual"),
    ("south_stand_sue", "away", "member", "fan"), ("pies_and_bovril", "home", None, "casual"), ("the_gaffer_knows", "away", None, "analyst"),
    ("set_piece_steve", "home", None, "analyst"), ("row_z_rita", "away", None, "casual"), ("matchday_mo", "home", None, "fan"),
    ("clean_sheet_chloe", "neutral", None, "casual"), ("nutmeg_noah", "away", None, "fan"), ("injury_time_ivy", "home", None, "fan"),
    ("offside_trap", "neutral", None, "analyst"), ("old_school_olu", "away", None, "casual"), ("first_touch_fay", "home", None, "casual"),
    ("wind_up_wes", "away", None, "troll"), ("angry_alan", "home", None, "troll"), ("ref_hater_99", "home", None, "troll"),
    ("keyboard_kyle", "away", None, "troll"), ("crypto_k1ng", "neutral", None, "bot"), ("promo_bot_77", "neutral", None, "bot"),
    ("str3am_hd", "neutral", None, "bot"), ("tipster_tony", "neutral", None, "bot"), ("vibes_only_val", "neutral", None, "casual"),
]
_COLORS = ["#f87171", "#fb923c", "#fbbf24", "#a3e635", "#4ade80", "#2dd4bf", "#38bdf8", "#818cf8", "#c084fc", "#f472b6"]
AUTHORS: list[dict[str, Any]] = [
    {"id": f"u{i:02d}", "name": n, "badge": b, "color": _COLORS[i % len(_COLORS)], "_side": s, "_bias": bias} for i, (n, s, b, bias) in enumerate(_PEOPLE)
]

_KIND_WEIGHTS = {
    "analyst": {"insight": 42, "question": 14, "prediction": 8, "banter": 14, "reaction": 8, "harsh": 8, "offtopic": 2, "flood": 2, "smart_spam": 0, "spam": 0, "toxic": 0},
    "fan": {"insight": 10, "question": 8, "prediction": 10, "banter": 24, "reaction": 18, "harsh": 8, "offtopic": 4, "flood": 16, "smart_spam": 0, "spam": 0, "toxic": 2},
    "casual": {"insight": 6, "question": 12, "prediction": 8, "banter": 26, "reaction": 16, "harsh": 4, "offtopic": 18, "flood": 10, "smart_spam": 0, "spam": 0, "toxic": 0},
    "troll": {"insight": 0, "question": 2, "prediction": 4, "banter": 22, "reaction": 8, "harsh": 10, "offtopic": 4, "flood": 10, "smart_spam": 0, "spam": 0, "toxic": 40},
    "bot": {"insight": 0, "question": 0, "prediction": 0, "banter": 0, "reaction": 0, "harsh": 0, "offtopic": 0, "flood": 4, "smart_spam": 30, "spam": 66, "toxic": 0},
}
# truth: (quality lo, quality hi, hide)
_TRUTH = {
    "insight": (0.75, 0.95, False), "question": (0.55, 0.75, False), "prediction": (0.35, 0.5, False), "harsh": (0.4, 0.55, False),
    "banter": (0.22, 0.42, False), "reaction": (0.18, 0.32, False), "offtopic": (0.08, 0.15, False), "flood": (0.04, 0.1, False),
    "spam": (0.0, 0.0, True), "smart_spam": (0.0, 0.0, True), "toxic": (0.0, 0.0, True),
}

_BASE: dict[str, list[str]] = {
    "insight": [
        "{team} are overloading the left again, {player} keeps getting a 2v1 against the full-back",
        "{player} is dropping so deep that {opp}'s midfield has nobody to press",
        "xG is {xgh}-{xga} so far, that tells you who is creating the real chances",
        "{opp} have had {oposs}% of the ball and done nothing with it",
        "If {opp} keep giving {player} that much space between the lines this only ends one way",
        "{team}'s press is leaving a huge gap behind the midfield, {opp} should go direct more often",
        "{player} has won every header so far, {opp} need to stop lumping it up to him",
        "The full-backs are pinned back, that is why {team} can't get out of their own half",
        "{team} need a runner beyond {player}, he is completely isolated up there",
        "{player} is quietly running this game, every {team} attack goes through him",
        "{shots} shots for {team} but how many were actually on target? Quantity over quality",
        "Set pieces are the route here, {opp} look shaky on every corner they defend",
    ],
    "question": [
        "Why isn't {bench} starting? Is he carrying a knock?",
        "Does anyone know if {player} is on a yellow?",
        "How many subs do {team} have left?",
        "What formation are {team} actually playing, 4-3-3 or 4-2-3-1?",
        "When was the last time {team} won this fixture?",
        "Anyone else think {player} looks off the pace today?",
        "Who takes the penalties for {team} if they get one?",
        "Is {bench} likely to come on, or is he not fit?",
    ],
    "prediction": [
        "{ph}-{pa} final score, calling it now", "{team} win this late, book it", "{player} scores next, I can feel it",
        "{opp} nick it on the break, seen this film before", "next goal wins this", "draw written all over it",
        "{player} to get the winner, you heard it here first",
    ],
    "harsh": [
        "{player} has been dreadful today, he has to come off", "{team} have been second best all over the pitch, no excuses",
        "That is a shocking decision from the referee, got it badly wrong", "{player} is having a nightmare, can't keep the ball",
        "Worst half {team} have played all season, honestly", "{player} needs to be dropped after this, not good enough",
    ],
    "banter": [
        "{opp} fans very quiet all of a sudden 🤫", "my nan could finish better than {player}", "this is why I have trust issues with {team}",
        "{player} couldn't pass water today lol", "another classic {team} performance 😂", "can we just skip to the pub",
        "I've seen better touches from a trampoline", "{team} till I die, sadly", "woke up early for THIS", "who else is watching from work 👀",
        "the commentator has said 'in and around' forty times", "the grass is the best player on the pitch today",
    ],
    "reaction": ["COME ON {TEAM}!!!", "ohhhh so close", "😬😬😬", "let's goooo", "this game man", "nervous", "🔥🔥🔥", "what a game", "ffs", "unreal scenes"],
    "offtopic": [
        "anyone watching the darts later?", "what's everyone having for tea", "is the stream lagging for anyone else?", "hello from Canada 🇨🇦",
        "first time here, love this channel", "can someone explain offside to my girlfriend", "volume is a bit low on my end",
        "who else is here after the podcast", "greetings from Lagos 🇳🇬", "mods can you turn on slow mode",
    ],
    "flood": ["{TEAM} {TEAM} {TEAM} 🔵🔵🔵", "LETS GO {SHORT} 🔴🔴🔴", "W", "L", "⚽⚽⚽", "{SHORT} {SHORT} {SHORT}", "REF!!!", "👏👏👏"],
    "spam": [
        "🔥 FREE betting tips 100% WIN rate 🔥 check my channel", "sub4sub anyone?? subbing back instantly", "I made $4,300 this week from home, dm me how",
        "WATCH FULL HD STREAM HERE >>> bit.ly/str3am-hd", "giveaway!!! first 50 to follow get free kits www.kitz-promo.com",
        "CHEAP followers and views, link in my bio", "telegram @surewins for fixed matches 💰", "earn crypto watching football >>> coin-kick.io",
    ],
    "smart_spam": [
        "Great breakdown on my channel of why {team} can't defend set pieces, link in my bio", "{player} highlights and skills compilation on my page, sub for more",
        "I predicted this {team} result, follow me for free tips on every game", "full match analysis of {team} vs {opp} dropping on my channel, go subscribe",
    ],
    "toxic": [
        "{player} is a useless waste of space, get out of my club", "the ref is a brain-dead clown", "{opp} fans are scum, always have been",
        "shut up you absolute idiot", "sack the manager and the whole pathetic squad", "{player} is a disgrace of a human being",
        "hope the ref gets what's coming to him", "{player} is trash, pure garbage, I hate him",
    ],
}

# event-specific lines; keys: for / against = reactions by allegiance, insight, question, prediction, harsh, toxic
_EVENT: dict[str, dict[str, list[str]]] = {
    "goal": {
        "for": ["GOOOOOAL {PLAYER}!!!!", "WHAT A GOAL 🔥🔥🔥", "{player}!!!! get in!!!", "I'm in tears 😭😭", "scenes!!!", "YESSSSS", "that's been coming!!!", "GET IN THERE"],
        "against": ["no no no no", "how is he unmarked??", "defending is a joke", "typical", "😡😡😡", "wake up {opp}!!", "saw that coming a mile off"],
        "insight": [
            "{assist} made that goal, the weight of that pass took three defenders out of the game", "That is on the centre-backs, nobody tracked {player}'s run",
            "{player}'s movement off the shoulder there is textbook centre-forward play", "Third time {team} have got in down that side, that goal was coming",
            "Keeper could do better there, he is beaten at his near post", "{player} only needed half a yard, {opp} gave him two",
        ],
        "question": ["Was {player} offside there? Looked tight on the replay", "Who was meant to be marking {player}?", "How many is that for {player} this season?"],
        "prediction": ["{team} go on and win this now", "{opp} will hit back, this isn't over", "more goals in this, no chance it stays {h}-{a}"],
    },
    "card.red": {
        "for": ["OFF OFF OFF", "he's gone!!! 🟥", "RED!!!!", "bye bye 👋", "see ya"],
        "against": ["never a red", "ref has ruined the game", "you're joking", "🤦🤦🤦", "soft as anything"],
        "insight": [
            "Clear denial of a goalscoring opportunity, {player} was the last man, no complaints", "Harsh for me, the covering defender was getting across",
            "{team} have to go to a back four now and sacrifice a striker", "Game changer: {opp} have the rest of the match against ten men",
        ],
        "question": ["Is that a straight red or two yellows?", "Does {player} miss the next match for that?"],
        "toxic": ["the ref is a brain-dead clown", "{player} is a thug, ban him for life the scumbag"],
    },
    "penalty.awarded": {
        "for": ["PENALTY!!!", "stonewall penalty", "YES REF", "clear as day"],
        "against": ["never a pen", "what a dive 🤿", "soft soft soft", "he's conned the ref"],
        "insight": [
            "Arm is away from the body, that is handball every day under the current rule", "From that distance he can't get his arm out of the way, very soft",
            "There is contact but he has gone looking for it, I have seen them not given",
        ],
        "question": ["Who's taking it?", "Does {player} usually go left or right?"],
        "prediction": ["{player} scores this, he never misses", "keeper saves this, calling it", "he's putting this over the bar"],
    },
    "penalty.saved": {
        "for": ["SAVED!!!!", "WHAT A SAVE {PLAYER}", "GET IN {PLAYER}!!!", "justice 😤"],
        "against": ["bottled it", "nooooo", "awful penalty", "why is he taking them"],
        "insight": ["{taker} always goes to his right, the keeper has clearly done his homework", "Poor penalty, it's at a lovely height for the goalkeeper", "{player} stays big and waits, that is how you save them"],
        "question": ["Was the keeper off his line?", "Why is {taker} still on penalties?"],
    },
    "var.check": {
        "for": ["please please please", "let it stand!!"], "against": ["offside surely", "chalk it off"],
        "insight": ["If you need five replays to see it, it's not clear and obvious", "The line gets drawn from his shoulder, this is going to be millimetres"],
        "question": ["Why is this taking so long?", "What are they even checking?"],
        "prediction": ["this is getting ruled out, watch", "goal stands, he's level"],
    },
    "goal.disallowed": {
        "for": ["ROBBED", "VAR is killing the game", "that's never offside", "absolute joke", "😡😡😡"],
        "against": ["correct decision tbf", "hahaha get in", "clear offside", "VAR finally gets one right 😂"],
        "insight": ["Offside by the rules even if it is marginal, his shoulder is beyond the last defender", "You can't re-referee the game on a toenail, the technology isn't that precise"],
        "question": ["Which frame do they freeze it on though?"],
        "toxic": ["whoever is in the VAR room is a brain-dead clown"],
    },
    "var.no_penalty": {
        "for": ["ROBBED", "how is that not a pen??", "stonewall"], "against": ["never a pen", "he dived", "correct"],
        "insight": ["There is a touch on the hip but not enough to bring him down, right call", "You see those given every week, the inconsistency is the problem"],
        "question": ["So what counts as contact now?"],
    },
    "shot.woodwork": {
        "for": ["OFF THE BAR!!", "how has that not gone in", "inches away 😱", "so unlucky"], "against": ["phew", "close one 😅", "lucky boys"],
        "insight": ["{player} has been getting closer every time, the keeper never moved for that one"], "question": ["Did the keeper get a touch on that?"],
    },
    "save.big": {
        "for": ["WHAT A SAVE", "{player} is a wall today", "🧤🧤🧤"], "against": ["how did he save that", "robbed by the keeper"],
        "insight": ["That is {player}'s positioning, he is set before the shot is taken", "Brilliant save but why is the striker free in the six-yard box?"], "question": [],
    },
    "chance.big": {
        "for": ["HOW has he missed that", "sitter!!", "has to score", "unbelievable"], "against": ["let off", "hahahaha", "thank you {player} 😂"],
        "insight": ["{player} takes one touch too many, that is the difference at this level", "Great counter, wrong decision at the end: the square ball was on"],
        "question": [], "harsh": ["{player} has to bury that, simply not good enough"],
    },
    "substitution": {
        "for": ["about time", "good change", "finally"], "against": ["why is he taking {off} off??", "strange one"],
        "insight": ["{player} gives them legs in behind, {off} had run himself into the ground", "Odd change that, {off} was their best outlet today"],
        "question": ["Is {off} injured or is that tactical?"],
    },
    "injury": {
        "for": ["hope he's ok 🙏", "that looked bad", "get well soon {player}", "awful to see"], "against": ["hope he's ok 🙏", "never want to see that", "respect, get well soon"],
        "insight": ["{team} lose their only holding midfielder if he can't continue, big reshuffle coming"], "question": ["is that his knee again?"],
    },
    "match.halftime": {
        "for": ["half time thoughts?", "need a brew after that", "breathe"], "against": ["half time thoughts?", "big team talk needed"],
        "insight": ["{team} have to change something, {opp} have been the better side", "Best player of the half? {player} for me, by a distance", "{opp} are winning every second ball, that is the whole story of the half"],
        "question": ["Any changes at the break do we think?"], "prediction": ["second half will be very different", "{team} come out and score early, watch"],
    },
    "match.fulltime": {
        "for": ["WHAT A GAME", "see you all next week", "we go again 💪", "full time!!"], "against": ["gutted", "see you all next week", "painful"],
        "insight": ["{team} deserved that over ninety minutes, the xG says the same", "Man of the match {player} and it isn't close"],
        "question": ["Who's your man of the match?"],
    },
    "card.yellow": {"for": ["good, book him"], "against": ["never a yellow", "soft"], "insight": ["{player} has to be careful now, another one like that and he walks"], "question": []},
}
_BURST_WEIGHTS = {"react": 44, "insight": 18, "question": 11, "prediction": 7, "harsh": 4, "toxic": 5, "flood": 9, "spam": 2}
_FLOOD_AFTER = {"goal": ["GOAL GOAL GOAL", "⚽⚽⚽", "{SHORT} {SHORT} {SHORT}", "W"], "card.red": ["🟥🟥🟥", "OFF OFF OFF"], "goal.disallowed": ["VAR OUT", "VAR OUT VAR OUT", "L"], "penalty.awarded": ["PEN PEN PEN", "🤿🤿🤿"]}


def _fill(tpl: str, ctx: dict[str, Any]) -> str:
    try:
        return tpl.format(**ctx)
    except (KeyError, IndexError):
        return tpl


def _context(match: dict[str, Any], vt: int, side: str, *labels: Any) -> dict[str, Any]:
    """Names and numbers a fan on `side` would plausibly mention at `vt`."""
    st = state_at(match, vt)
    fx = match["fixture"]
    mine = side if side in ("home", "away") else ("home" if roll(*labels, "ns") < 0.5 else "away")
    theirs = "away" if mine == "home" else "home"
    subject = mine if roll(*labels, "subj") < 0.6 else theirs  # fans mostly talk about their own team
    other = "away" if subject == "home" else "home"
    outfield = [p for p in st["on_pitch"][subject] if p["pos"] != "GK"] or st["on_pitch"][subject]
    bench = st["bench"][subject] or outfield
    h, a = st["score"]["home"], st["score"]["away"]
    return {
        "team": fx[subject]["name"], "opp": fx[other]["name"], "TEAM": fx[subject]["name"].upper(), "SHORT": fx[mine]["short"],
        "player": pick(outfield, *labels, "p")["name"], "bench": pick(bench, *labels, "b")["name"],
        "xgh": f"{st['stats']['xg']['home']:.1f}", "xga": f"{st['stats']['xg']['away']:.1f}", "oposs": st["stats"]["possession"][other],
        "shots": st["stats"]["shots"][subject], "h": h, "a": a, "ph": h + int(roll(*labels, "ph") * 2), "pa": a + int(roll(*labels, "pa") * 2),
    }


def _event_context(match: dict[str, Any], ev: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    fx = match["fixture"]
    team = ev["team"] or "home"
    other = "away" if team == "home" else "home"
    player = ev["player"]["name"] if ev["player"] else base["player"]
    second = ev["player2"]["name"] if ev["player2"] else base["bench"]
    return {**base, "team": fx[team]["name"], "opp": fx[other]["name"], "TEAM": fx[team]["name"].upper(), "player": player, "PLAYER": player.upper(),
            "assist": second, "off": second, "taker": second, "h": ev["score"]["home"], "a": ev["score"]["away"]}


def _mk(n: int, vt: int, author: dict[str, Any], kind: str, text: str, ref: str | None, *labels: Any) -> dict[str, Any]:
    lo, hi, hide = _TRUTH[kind]
    quality = round(lo + (hi - lo) * roll(*labels, "q"), 3)
    r = roll(*labels, "likes")
    if kind in ("spam", "smart_spam"):
        likes = 0
    elif kind == "toxic":
        likes = int(r * 9)  # abuse collects likes too: engagement is not quality
    elif kind == "flood":
        likes = int(r * 3)
    else:
        likes = int(quality * 44 * (0.25 + r)) + (3 if author["badge"] else 0)
    return {
        "id": f"c{n:05d}", "vt": vt, "author": {k: v for k, v in author.items() if not k.startswith("_")}, "text": text, "likes": likes,
        "truth": {"kind": kind, "quality": quality, "hide": hide, "ref": ref, "side": author["_side"]},
    }


@lru_cache(maxsize=32)
def _chat(fixture_id: str, seed: int) -> tuple[dict[str, Any], ...]:
    from gantry.sim.match import simulate

    match = simulate(fixture_id, seed)
    tm = match["timing"]
    raw: list[tuple[int, dict[str, Any], str, str, str | None]] = []  # (vt, author, kind, text, ref)

    # --- baseline chatter ------------------------------------------------------------------------------------
    vt, i = 8, 0
    while vt < tm["end_vt"] + 90:
        labels = (seed, fixture_id, "base", i)
        author = pick(AUTHORS, *labels, "who")
        weights = _KIND_WEIGHTS[author["_bias"]]
        kind = weighted(list(weights), list(weights.values()), *labels, "kind")
        ctx = _context(match, vt, author["_side"], *labels)
        raw.append((vt, author, kind, _fill(pick(_BASE[kind], *labels, "txt"), ctx), None))
        quiet = tm["p1_end_vt"] <= vt < tm["p2_start_vt"]
        vt += int((7 + roll(*labels, "gap") * 9) * (1.6 if quiet else 1.0))
        i += 1

    # --- bursts after the moments that matter ----------------------------------------------------------------
    for ev in match["events"]:
        lines = _EVENT.get(ev["type"])
        imp = ev["truth"]["importance"]
        if not lines or (imp < 0.3 and ev["type"] not in ("card.yellow",)):
            continue
        n = int(6 + imp * 34) if ev["type"] != "card.yellow" else 4
        for j in range(n):
            labels = (seed, fixture_id, "burst", ev["id"], j)
            author = pick(AUTHORS, *labels, "who")
            if author["_bias"] == "bot" and roll(*labels, "botskip") < 0.8:
                author = pick([a for a in AUTHORS if a["_bias"] != "bot"], *labels, "who2")
            at = ev["vt"] + 2 + int((roll(*labels, "t") ** 1.8) * 55)
            ctx = _event_context(match, ev, _context(match, ev["vt"], author["_side"], *labels))
            want = weighted(list(_BURST_WEIGHTS), list(_BURST_WEIGHTS.values()), *labels, "kind")
            if author["_bias"] == "analyst" and roll(*labels, "an") < 0.55:
                want = "insight"
            if author["_bias"] == "troll" and roll(*labels, "tr") < 0.4:
                want = "toxic"
            if want == "react":
                happy = ev["type"] in ("injury", "match.halftime", "match.fulltime") or author["_side"] == ev["team"] or (author["_side"] == "neutral" and roll(*labels, "n") < 0.5)
                if ev["type"] in ("penalty.saved", "save.big", "card.red"):  # good news for the OTHER side of the incident
                    happy = author["_side"] == ev["team"] if ev["type"] != "card.red" else author["_side"] not in (ev["team"], "neutral")
                pool, kind = lines["for" if happy else "against"], "reaction"
            elif want == "flood":
                pool, kind = _FLOOD_AFTER.get(ev["type"], _BASE["flood"]), "flood"
            elif want == "spam":
                pool, kind = _BASE["spam"], "spam"
            elif want == "toxic":
                pool, kind = lines.get("toxic") or _BASE["toxic"], "toxic"
            else:
                pool, kind = lines.get(want) or [], want
                if not pool:
                    pool, kind = lines["for"], "reaction"
            raw.append((at, author, kind, _fill(pick(pool, *labels, "txt"), ctx), ev["id"]))

    raw.sort(key=lambda r: r[0])
    return tuple(_mk(n, vt, author, kind, text, ref, seed, fixture_id, "msg", n) for n, (vt, author, kind, text, ref) in enumerate(raw))


def chat_for(match: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Every chat message of the broadcast, in order. Cached and shared: treat as read-only."""
    return _chat(match["fixture_id"], match["seed"])


def public(msg: dict[str, Any], *, now_vt: float | None = None) -> dict[str, Any]:
    """A chat message as the platform sees it. Likes accrue with age (a fresh message has none yet)."""
    out = {k: v for k, v in msg.items() if k != "truth"}
    if now_vt is not None:
        age = max(0.0, now_vt - msg["vt"])
        out["likes"] = int(msg["likes"] * min(1.0, age / 75.0))
    return out
