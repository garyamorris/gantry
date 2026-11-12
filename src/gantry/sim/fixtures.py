"""The fictional league: six clubs, three fixtures, each with a scripted story.

A fixture's `beats` are the moments that MATTER (and carry the hidden ground truth `imp`, the importance a human
producer would give them). `gantry.sim.match` fills the minutes between them with procedural filler (corners, fouls,
shots into Row Z) so the ranking has noise to see through. Nothing here is read by a graph: graphs only ever see the
feed events the simulator emits, with the truth stripped.

Clock notation: `(period, minute, second)` where minute counts elapsed minutes of the match (0-44 first half, 45-89
second half; 45+ in period 1 and 90+ in period 2 are stoppage time).
"""

from __future__ import annotations

from typing import Any


def _squad(prefix: str, starters: list[tuple[int, str, str]], bench: list[tuple[int, str, str]]) -> dict[str, Any]:
    def mk(row: tuple[int, str, str], starting: bool) -> dict[str, Any]:
        num, name, pos = row
        return {"id": f"{prefix}{num}", "num": num, "name": name, "pos": pos, "starting": starting}

    return {"players": [mk(r, True) for r in starters] + [mk(r, False) for r in bench]}


TEAMS: dict[str, dict[str, Any]] = {
    "HAR": {
        "name": "Harbour City", "short": "HAR", "color": "#3b82f6", "nick": "the Dockers", "strength": 0.56,
        **_squad("HAR", [(1, "Lindqvist", "GK"), (2, "Adeyemi", "DF"), (4, "Kovac", "DF"), (5, "Brannigan", "DF"), (3, "Ito", "DF"),
                         (6, "Okonjo", "MF"), (8, "Reyes", "MF"), (10, "Valdes", "MF"), (7, "Sinclair", "FW"), (11, "Nakamura", "FW"),
                         (9, "Okafor", "FW")],
                 [(13, "Harte", "GK"), (14, "Dubois", "MF"), (17, "Petrov", "FW"), (19, "Mbeki", "FW"), (22, "Castellanos", "DF")]),
    },
    "IRN": {
        "name": "Ironbridge Athletic", "short": "IRN", "color": "#ef4444", "nick": "the Smiths", "strength": 0.50,
        **_squad("IRN", [(1, "Graves", "GK"), (2, "Halloran", "DF"), (5, "Dragovic", "DF"), (6, "Whitlock", "DF"), (3, "Farouk", "DF"),
                         (4, "Stone", "MF"), (8, "Bergstrom", "MF"), (10, "Lavelle", "MF"), (7, "Quaye", "FW"), (11, "Morrow", "FW"),
                         (9, "Tindall", "FW")],
                 [(12, "Pike", "GK"), (15, "Osei", "MF"), (18, "Varga", "FW"), (20, "Kirby", "FW"), (23, "Almeida", "DF")]),
    },
    "KPR": {
        "name": "Kestrel Park Rangers", "short": "KPR", "color": "#22c55e", "nick": "the Kestrels", "strength": 0.52,
        **_squad("KPR", [(1, "Sorensen", "GK"), (2, "Bailey", "DF"), (4, "Ndour", "DF"), (5, "Hargreaves", "DF"), (3, "Yilmaz", "DF"),
                         (6, "Prentice", "MF"), (8, "Aguilar", "MF"), (10, "Thackeray", "MF"), (7, "Idowu", "FW"), (11, "Lund", "FW"),
                         (9, "Maddox", "FW")],
                 [(13, "Cole", "GK"), (14, "Rasmussen", "MF"), (16, "Fofana", "FW"), (21, "Winter", "FW"), (24, "Greaves", "DF")]),
    },
    "SAL": {
        "name": "Saltmarsh Town", "short": "SAL", "color": "#eab308", "nick": "the Shrimpers", "strength": 0.46,
        **_squad("SAL", [(1, "Pemberton", "GK"), (2, "Oduya", "DF"), (5, "Marsh", "DF"), (6, "Karlsson", "DF"), (3, "Bello", "DF"),
                         (4, "Finch", "MF"), (8, "Santoro", "MF"), (10, "Abara", "MF"), (7, "Dunne", "FW"), (11, "Vega", "FW"),
                         (9, "Crowther", "FW")],
                 [(12, "Hale", "GK"), (15, "Novak", "MF"), (17, "Eze", "FW"), (19, "Tully", "FW"), (22, "Bright", "DF")]),
    },
    "NGU": {
        "name": "Northgate United", "short": "NGU", "color": "#a855f7", "nick": "the Gatekeepers", "strength": 0.55,
        **_squad("NGU", [(1, "Ashby", "GK"), (2, "Romero", "DF"), (4, "Hauge", "DF"), (5, "Okoro", "DF"), (3, "Lacroix", "DF"),
                         (6, "Sato", "MF"), (8, "Fenwick", "MF"), (10, "Zielinski", "MF"), (7, "Amadou", "FW"), (11, "Garland", "FW"),
                         (9, "Pryce", "FW")],
                 [(13, "Dean", "GK"), (14, "Moreau", "MF"), (18, "Banjo", "FW"), (20, "Hollis", "FW"), (23, "Ekstrom", "DF")]),
    },
    "VRO": {
        "name": "Vale Royal", "short": "VRO", "color": "#f97316", "nick": "the Royals", "strength": 0.54,
        **_squad("VRO", [(1, "Tanaka", "GK"), (2, "Whitby", "DF"), (5, "Diallo", "DF"), (6, "Radford", "DF"), (3, "Simeonov", "DF"),
                         (4, "Cruz", "MF"), (8, "Holloway", "MF"), (10, "Ashworth", "MF"), (7, "Babatunde", "FW"), (11, "Lorenz", "FW"),
                         (9, "Harrow", "FW")],
                 [(12, "Voss", "GK"), (15, "Ibarra", "MF"), (17, "Penrose", "FW"), (19, "Sesay", "FW"), (22, "Lindgren", "DF")]),
    },
}


def _b(period: int, minute: int, second: int, type_: str, side: str | None = None, num: int | None = None, imp: float = 0.1, **extra: Any) -> dict[str, Any]:
    return {"p": period, "m": minute, "s": second, "type": type_, "side": side, "num": num, "imp": imp, **extra}


FIXTURES: dict[str, dict[str, Any]] = {
    "derby": {
        "title": "The Estuary Derby", "competition": "Coastal League, Matchday 12", "home": "HAR", "away": "IRN", "derby": True,
        "blurb": "A late, late show: an early sucker punch, a VAR heartbreak, a red card, a saved penalty and a 93rd-minute winner.",
        "tempo": 0.34, "added": (2, 5), "viewers": 18400,
        "beats": [
            _b(1, 3, 40, "chance.big", "home", 9, 0.40, keeper=1, how="one-on-one, smothered"),
            _b(1, 11, 20, "goal", "away", 9, 0.80, assist=10, how="a breakaway finish, against the run of play", tags=["against_run"]),
            _b(1, 18, 30, "card.yellow", "home", 4, 0.22, on=7, why="a late tackle"),
            _b(1, 26, 50, "shot.woodwork", "home", 10, 0.45, how="a free kick that rattles the crossbar"),
            _b(1, 32, 10, "save.big", "home", 1, 0.42, shooter=11, how="a fingertip save at full stretch"),
            _b(1, 37, 45, "goal", "home", 7, 0.85, assist=8, how="a first-time volley from the edge of the box"),
            _b(1, 43, 30, "card.yellow", "away", 4, 0.20, on=10, why="a cynical pull-back"),
            _b(2, 50, 10, "var.check", "home", 9, 0.60, about="goal", how="Okafor has the ball in the net, but the flag stays down and VAR is looking"),
            _b(2, 51, 30, "goal.disallowed", "home", 9, 0.80, why="offside by a toenail"),
            _b(2, 57, 20, "substitution", "away", 15, 0.18, off=8),
            _b(2, 62, 30, "card.red", "away", 5, 0.88, on=11, why="a last-man foul"),
            _b(2, 65, 40, "substitution", "home", 17, 0.20, off=11),
            _b(2, 70, 5, "penalty.awarded", "home", 10, 0.85, by=6, why="handball", urgent=True),
            _b(2, 71, 50, "penalty.saved", "away", 1, 0.92, taker=10, how="guesses right and palms it round the post"),
            _b(2, 77, 30, "substitution", "home", 19, 0.18, off=7),
            _b(2, 79, 40, "card.yellow", "home", 6, 0.18, on=10, why="dissent"),
            _b(2, 84, 20, "chance.big", "away", 7, 0.45, keeper=1, how="clean through on the counter, dragged wide"),
            _b(2, 87, 30, "shot.woodwork", "home", 17, 0.50, how="a curler that kisses the outside of the post"),
            _b(2, 92, 40, "goal", "home", 9, 1.00, assist=8, how="a towering header from the corner", tags=["set_piece", "header"]),
        ],
    },
    "stalemate": {
        "title": "Kestrel Park vs Saltmarsh", "competition": "Coastal League, Matchday 12", "home": "KPR", "away": "SAL", "derby": False,
        "blurb": "A cagey afternoon: long quiet stretches, one penalty shout, one late smash-and-grab. Tests what the producer does when nothing happens.",
        "tempo": 0.20, "added": (1, 4), "viewers": 7200,
        "beats": [
            _b(1, 26, 30, "shot.woodwork", "home", 9, 0.55, how="a looping header onto the top of the bar"),
            _b(1, 39, 10, "injury", "away", 4, 0.50, why="goes down holding his knee; the stretcher is on"),
            _b(1, 41, 40, "substitution", "away", 15, 0.25, off=4, tags=["forced"]),
            _b(2, 54, 20, "card.yellow", "away", 6, 0.22, on=7, why="a shirt pull"),
            _b(2, 60, 30, "substitution", "home", 16, 0.22, off=11),
            _b(2, 65, 10, "var.check", "home", 7, 0.65, about="penalty", how="Idowu goes down under a challenge from Marsh and VAR is checking for a penalty"),
            _b(2, 66, 40, "var.no_penalty", "home", 7, 0.72, why="minimal contact, play on"),
            _b(2, 71, 20, "substitution", "away", 17, 0.20, off=7),
            _b(2, 78, 30, "save.big", "away", 1, 0.50, shooter=16, how="a point-blank reflex stop"),
            _b(2, 83, 40, "goal", "away", 17, 1.00, assist=10, how="a smash-and-grab finish at the near post", tags=["against_run", "sub_scores"]),
            _b(2, 91, 10, "card.yellow", "home", 5, 0.18, on=17, why="frustration"),
            _b(2, 93, 20, "chance.big", "home", 9, 0.60, keeper=1, how="a free header, six yards out, over the bar"),
        ],
    },
    "goalfest": {
        "title": "Northgate vs Vale Royal", "competition": "Coastal League, Matchday 12", "home": "NGU", "away": "VRO", "derby": False,
        "blurb": "Seven goals, a comeback from 1-3 and a hat-trick. Tests poll fatigue: there is always another goal to ask about.",
        "tempo": 0.38, "added": (3, 6), "viewers": 23900,
        "beats": [
            _b(1, 4, 30, "goal", "home", 9, 0.70, assist=10, how="a tap-in after a slick move"),
            _b(1, 18, 20, "goal", "away", 9, 0.75, assist=8, how="a low drive into the corner"),
            _b(1, 23, 40, "card.yellow", "home", 5, 0.18, on=7, why="a trip"),
            _b(1, 32, 50, "goal", "away", 7, 0.78, assist=10, how="a solo run and a dinked finish"),
            _b(1, 40, 30, "chance.big", "home", 7, 0.40, keeper=1, how="a point-blank header, straight at the keeper"),
            _b(1, 43, 40, "goal", "away", 9, 0.80, assist=11, how="a thumping header for his second", tags=["brace"]),
            _b(2, 45, 30, "substitution", "home", 18, 0.25, off=11),
            _b(2, 57, 30, "goal", "home", 9, 0.82, assist=18, how="a poacher's finish to start the comeback", tags=["brace"]),
            _b(2, 63, 20, "card.yellow", "away", 4, 0.18, on=18, why="a late challenge"),
            _b(2, 68, 30, "penalty.awarded", "home", 10, 0.85, by=5, why="a clumsy trip on Banjo", urgent=True),
            _b(2, 70, 10, "goal", "home", 10, 0.88, how="sends the keeper the wrong way from the spot", tags=["penalty"]),
            _b(2, 76, 40, "shot.woodwork", "away", 11, 0.50, how="a volley that crashes off the bar"),
            _b(2, 80, 30, "substitution", "away", 19, 0.18, off=11),
            _b(2, 87, 20, "goal", "home", 9, 1.00, assist=8, how="a curling finish to complete the hat-trick and the comeback", tags=["hat_trick"]),
            _b(2, 93, 30, "save.big", "home", 1, 0.70, shooter=9, how="a flying save to deny Harrow his own hat-trick"),
        ],
    },
}

FIXTURE_IDS = list(FIXTURES)


def public_fixture(fixture_id: str) -> dict[str, Any]:
    """What the UI and the graphs may know about a fixture (no beats, no truth)."""
    f = FIXTURES[fixture_id]

    def team(code: str) -> dict[str, Any]:
        t = TEAMS[code]
        return {"id": code, "name": t["name"], "short": t["short"], "color": t["color"], "nick": t["nick"]}

    return {
        "id": fixture_id, "title": f["title"], "competition": f["competition"], "blurb": f["blurb"], "derby": f["derby"],
        "home": team(f["home"]), "away": team(f["away"]),
    }
