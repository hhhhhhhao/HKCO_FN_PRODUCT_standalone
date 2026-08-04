# -*- coding: utf-8 -*-
"""Amount-free continuity between prior identities and current disclosure text."""
from difflib import SequenceMatcher
import re
import unicodedata


_TRANSLATION = str.maketrans(
    "臺裡裏為於與業務產銷售開發網據聯車醫藥護兒電纜風險資產物業項類體國華萬億圓號",
    "台里里为于与业务产销售开发网据联车医药护儿电缆风险资产物业项类体国华万亿圆号",
)


def identity_key(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION).lower()
    text = re.sub(
        r"\([^)]*(?:附註|附注|note|[ivx\d]+)[^)]*\)|"
        r"（[^）]*(?:附註|附注|note|[ivx\d]+)[^）]*）",
        "", text, flags=re.I,
    )
    return re.sub(r"[\s:：,，。;；、()（）\[\]【】/\\_\-–—]+", "", text)


def identity_similarity(left, right):
    left, right = identity_key(left), identity_key(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.8 + 0.15 * min(len(left), len(right)) / max(len(left), len(right))
    return SequenceMatcher(None, left, right).ratio()


def identity_matches(prior_names, current_names, threshold=0.6):
    """Maximum one-to-one matches; current names may add, disappear, or change."""
    return identity_match_profile(prior_names, current_names, threshold)[0]


def identity_match_profile(prior_names, current_names, threshold=0.6):
    """Return one-to-one hit count and match strength without reading amounts.

    Identity continuity is the primary table-location signal.  Count comes
    first; summed similarity only breaks ties between tables matching the same
    number of prior identities.  Current identities remain an open set.
    """
    pairs, prior, current = _identity_match_pairs(prior_names, current_names, threshold)
    return len(pairs), sum(score for score, _pi, _ci in pairs)


def identity_matched_current_keys(prior_names, current_names, threshold=0.6):
    """Current identity keys participating in the one-to-one continuity match."""
    pairs, _prior, current = _identity_match_pairs(prior_names, current_names, threshold)
    return tuple(current[ci] for _score, _pi, ci in pairs)


def _identity_match_pairs(prior_names, current_names, threshold):
    prior = list(dict.fromkeys(identity_key(name) for name in prior_names if identity_key(name)))
    current = list(dict.fromkeys(identity_key(name) for name in current_names if identity_key(name)))
    edges = sorted((
        (identity_similarity(left, right), pi, ci)
        for pi, left in enumerate(prior) for ci, right in enumerate(current)
        if identity_similarity(left, right) >= threshold
    ), reverse=True)
    used_prior, used_current, pairs = set(), set(), []
    for score, pi, ci in edges:
        if pi in used_prior or ci in used_current:
            continue
        used_prior.add(pi)
        used_current.add(ci)
        pairs.append((score, pi, ci))
    return pairs, prior, current
