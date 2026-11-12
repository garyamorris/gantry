from __future__ import annotations

from gantry.engine import policy, polls, ranking
from gantry.live.windows import sample_window


def _c(i, text, likes=0, badge=None):
    return {"id": f"c{i}", "vt": i, "author": {"id": f"u{i}", "name": f"user{i}", "badge": badge}, "text": text, "likes": likes}


LEX = {"players": ["okafor"], "teams": ["harbour", "ironbridge"]}


def test_moment_scores_explain_themselves():
    w = sample_window("derby", 7)["window"]
    moments = ranking.rank_moments(w["events"], w["timeline"], w["comments"], w["chat"]["baseline_per_min"])
    top = moments[0]
    assert top["type"] == "goal" and top["tier"] == "headline" and top["score"] >= 0.95
    labels = [x["label"] for x in top["why"]]
    assert labels[0] == "goal" and "stoppage-time drama" in labels and "puts them ahead" in labels
    assert abs(min(1.0, sum(x["delta"] for x in top["why"])) - top["score"]) < 0.01, "the score is the sum of its reasons"
    assert all(m["tier"] == "routine" for m in moments if m["type"] in ("corner", "foul", "stat.update"))


def test_a_hat_trick_and_a_comeback_are_recognised():
    w = sample_window("goalfest", 7)["window"]
    top = ranking.rank_moments(w["events"], w["timeline"], w["comments"], w["chat"]["baseline_per_min"])[0]
    labels = [x["label"] for x in top["why"]]
    assert "a hat-trick" in labels and "completes a comeback" in labels


def test_comment_scoring_separates_the_crowd():
    seen: dict[str, int] = {}
    insight = ranking.score_comment(_c(1, "Okafor only needed half a yard because nobody tracked his run", likes=20), LEX, seen)
    react = ranking.score_comment(_c(2, "GOOOOOAL OKAFOR!!!!"), LEX, seen)
    spam = ranking.score_comment(_c(3, "FREE betting tips 100% WIN rate check my channel"), LEX, seen)
    smart = ranking.score_comment(_c(4, "Great breakdown on my channel of why Harbour can't defend set pieces, link in my bio"), LEX, seen)
    toxic = ranking.score_comment(_c(5, "the ref is a brain-dead clown"), LEX, seen)
    harsh = ranking.score_comment(_c(6, "Okafor has been dreadful today, he has to come off"), LEX, seen)
    q = ranking.score_comment(_c(7, "Was Okafor offside there? Looked tight on the replay"), LEX, seen)
    assert insight["label"] == "keep" and insight["score"] > 0.7 and "makes an argument" in insight["reasons"]
    assert react["score"] < insight["score"] - 0.3
    assert spam["label"] == smart["label"] == "hide" and "spam" in spam["reasons"][0]
    assert toxic["label"] == "hide" and "abusive" in toxic["reasons"][0]
    assert harsh["label"] == "keep", "harsh but fair criticism is not abuse"
    assert q["question"] and "asks a real question" in q["reasons"]


def test_repeats_are_folded_and_the_shortlist_is_diverse():
    seen: dict[str, int] = {}
    a = ranking.score_comment(_c(1, "LETS GO HAR"), LEX, seen)
    b = ranking.score_comment(_c(2, "lets goooo HAR!!!"), LEX, seen)
    assert a["label"] == "keep" and b["label"] == "collapse" and a["key"] == b["key"]
    # across shards (separate `seen`), the dedupe pass settles it
    s1 = ranking.score_comment(_c(3, "Okafor only needed half a yard because nobody tracked his run"), LEX, {})
    s2 = ranking.score_comment(_c(4, "Okafor only needed half a yard because nobody tracked his run"), LEX, {})
    merged = ranking.dedupe_pass([s2, s1])
    assert [c["label"] for c in merged] == ["keep", "collapse"]
    many = [ranking.score_comment({**_c(10 + i, f"Okafor should start because his movement is better, point {i}", likes=10), "author": {"id": "same", "name": "same"}}, LEX, {}) for i in range(4)]
    assert len(ranking.shortlist(many)) == 1, "one author gets one slot"


def test_themes_come_from_what_the_chat_says():
    texts = ["VAR is killing the game", "that's never offside", "the line is drawn from his shoulder, offside by the rules", "ROBBED", "correct decision tbf",
             "why is this replay taking so long?"]
    scored = [ranking.score_comment(_c(i, t), LEX, {}) for i, t in enumerate(texts)]
    themes = ranking.find_themes(scored)
    assert themes and themes[0]["key"] == "var" and themes[0]["count"] >= 4 and themes[0]["question"]


def _plan(fixture, minute, **kw):
    w = sample_window(fixture, 7, minute=minute)
    moments = ranking.rank_moments(w["window"]["events"], w["window"]["timeline"], w["window"]["comments"], w["window"]["chat"]["baseline_per_min"])
    return polls.plan(moments, [], w["match_state"], w["window"]["trigger"], w["window"]["to_vt"], **kw)


def test_the_planner_matches_the_poll_to_the_moment():
    assert _plan("derby", 52)[0]["template"] == "decision"
    assert _plan("derby", 63)[0]["template"] == "red_card"
    pen = _plan("derby", 71)[0]
    assert pen["template"] == "penalty_outcome" and pen["ttl_s"] <= 90, "a penalty poll is urgent"
    assert {c["template"] for c in _plan("goalfest", 44)} >= {"hattrick_watch"}
    assert all(c["topic"] and c["wording"].keys() >= {"analyst", "hype", "terrace"} for c in _plan("derby", 63))


def test_memory_changes_the_ranking():
    cold = _plan("stalemate", 20)
    assert cold and cold[0]["template"] == "next_goal" and not cold[0]["weak"]
    ignored = {"next_goal": {"n": 3, "mean": 0.05, "approved": 0, "edited": 0, "rejected": 0}}
    warm = {c["template"]: c for c in _plan("stalemate", 20, prefs=ignored)}
    assert warm["next_goal"]["weak"] and warm["next_goal"]["value"] < cold[0]["value"], "a kind of poll this audience ignores is no longer worth the slot"
    loved = {"next_goal": {"n": 3, "mean": 0.33}}
    assert _plan("stalemate", 20, prefs=loved)[0]["value"] > cold[0]["value"]
    assert polls.expected_engagement("x", {}) == 0.5
    refused = {"x": {"n": 0, "rejected": 3, "approved": 0, "edited": 0}}
    assert polls.expected_engagement("x", refused) < 0.5, "humans rejecting a kind of poll is a preference too"
    tired = {c["template"]: c for c in _plan("stalemate", 20, used={"next_goal": 1})}
    assert tired["next_goal"]["fatigue"] > 0 and tired["next_goal"]["value"] < cold[0]["value"]


def test_validate_and_the_rubric():
    cand = _plan("derby", 63)[0]
    ok = polls.validate({"question": "  Red for Dragovic:   deserved? ", "options": ["Yes", "yes", " No ", ""]}, cand)
    assert ok == {"question": "Red for Dragovic: deserved?", "options": ["Yes", "No"]}
    for bad in ({"question": "", "options": ["a", "b"]}, {"question": "q?", "options": ["only"]}, {"question": "q?", "options": "ab"}):
        try:
            polls.validate(bad, cand)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    names = {"allowed_options": ["Reyes", "Okafor"]}
    assert polls.validate({"question": "Best?", "options": ["Okafor", "Messi", "Reyes"]}, names)["options"] == ["Okafor", "Reyes"], "a model cannot invent a player"
    good = polls.rubric(polls.baseline_draft(cand, "terrace"), cand)
    long = polls.rubric({"question": "x" * 120 + " Dragovic?", "options": ["a", "b"]}, cand)
    leading = polls.rubric({"question": "Surely Dragovic was robbed there?", "options": ["a", "b"]}, cand)
    unnamed = polls.rubric({"question": "Was that a red card?", "options": ["a", "b"]}, cand)
    stale = polls.rubric(polls.baseline_draft(cand, "terrace"), cand, avoid=[polls.baseline_draft(cand, "terrace")["question"]])
    assert good["passed"] and not long["passed"] and any("characters" in n for n in long["notes"])
    assert leading["parts"]["neutral"] < 1 and unnamed["parts"]["grounded"] < 1 and stale["parts"]["novelty"] < 1


POLL = {"kind": "poll", "topic": "decision:e1", "template": "decision", "question": "Right call?", "priority": 0.75, "judge": 1.0, "expected": 0.5, "risk": "medium", "expires_vt": 2000}
STATE = {"live": {"active_poll": None, "last_poll_closed_vt": None}}


def _screen(action=POLL, mode="autopilot", now=1000, state=STATE, ledger=(), similar=None):
    return policy.screen(dict(action), mode=mode, now_vt=now, state=state, ledger=list(ledger), similar=similar)


def test_policy_modes():
    assert _screen(mode="manual")["verdict"] == "ask"
    assert _screen(mode="assisted")["verdict"] == "ask"
    assert _screen(mode="autopilot")["verdict"] == "publish"
    timid = {**POLL, "priority": 0.3, "judge": 0.6}
    assert _screen(timid)["verdict"] == "ask", "autopilot still asks when it is not confident about a medium-risk poll"
    assert _screen({**POLL, "risk": "high"})["verdict"] == "ask"
    pin = {"kind": "pin_comment", "topic": "pin:1", "priority": 0.8}
    assert _screen(pin, mode="assisted")["verdict"] == "publish" and _screen(pin, mode="manual")["verdict"] == "ask"


def test_policy_never_repeats_itself_and_respects_humans():
    done = [{"kind": "poll", "topic": "decision:e1", "template": "decision", "status": "published"}]
    assert "already done" in _screen(ledger=done)["reason"]
    assert "rejected" in _screen(ledger=[{**done[0], "status": "rejected"}])["reason"]
    twice = [{"kind": "poll", "topic": f"decision:x{i}", "template": "decision", "status": "published"} for i in range(2)]
    assert "times already" in _screen(ledger=twice)["reason"]
    similar = [{"id": "old", "score": 0.9, "status": "published", "topic": "decision:e0", "question": "Right call?"}]
    v = _screen(similar=similar)
    assert v["verdict"] == "suppress" and "too similar" in v["reason"] and v["similar_to"] == "old"
    assert _screen(similar=[{**similar[0], "score": 0.4}])["verdict"] == "publish"
    assert _screen(now=2000)["reason"] == "the moment has passed"


def test_policy_timing_one_poll_at_a_time_with_a_gap_and_a_way_back():
    live = {"live": {"active_poll": {"id": "p", "closes_vt": 1050, "opened_vt": 900, "priority": 0.9}, "last_poll_closed_vt": None}}
    v = _screen(state=live)
    assert v["verdict"] == "suppress" and v["reason"] == "a poll is already live" and v["retry_vt"] == 1050 + policy.POLL_GAP_BIG
    small_live = {"live": {"active_poll": {"id": "p", "closes_vt": 1050, "opened_vt": 900, "priority": 0.45}, "last_poll_closed_vt": None}}
    v = _screen(state=small_live)
    assert v["verdict"] == "publish" and v["preempt"], "a big moment closes a small poll early"
    assert _screen({**POLL, "priority": 0.5}, state=small_live)["verdict"] == "suppress", "a small one does not"
    just_now = {"live": {"active_poll": None, "last_poll_closed_vt": 1000}}
    assert "only 1s since the last poll closed" in _screen(state=just_now)["reason"], "never 'only 0s'"
    recent = {"live": {"active_poll": None, "last_poll_closed_vt": 990}}
    v = _screen(state=recent)
    assert v["verdict"] == "suppress" and "since the last poll closed" in v["reason"] and v["retry_vt"] == 990 + policy.POLL_GAP_BIG
    lull = {**POLL, "priority": 0.45, "risk": "low", "template": "next_goal", "topic": "next_goal:1"}
    assert _screen(lull, state={"live": {"active_poll": None, "last_poll_closed_vt": 1000 - policy.POLL_GAP + 5}})["verdict"] == "suppress"
    hush = {**STATE, "recent_injuries": [{"vt": 900}]}
    assert "being treated" in _screen({**lull, "template": "vibe_check"}, state=hush)["reason"]
    assert _screen(POLL, state=hush)["verdict"] == "publish", "a serious poll is still fine"
