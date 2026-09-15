"""Official scoring helpers aligned with each dataset's paper / released code.

LoCoMo  — snap-research/locomo task_eval/evaluation.py  (token F1)
LongMemEval — xiaowu0162/LongMemEval evaluate_qa.py     (type-specific yes/no)
VehicleMemBench — evaluation/eval_utils.score_tool_calls (tool P/R/F1)
CarMem — COLING 2025 hierarchical extraction + retrieval hit + Pass/Update/Append
"""

from __future__ import annotations

import json
import re
import string
from collections import Counter


# ---------------------------------------------------------------------------
# Porter stemmer (subset of the official NLTK Porter used by LoCoMo)
# ---------------------------------------------------------------------------

class _PorterStemmer:
    """Compact Porter stemmer; used when nltk is not installed."""

    _c = "[^aeiou]"
    _v = "[aeiouy]"
    _C = _c + "[^aeiouy]*"
    _V = _v + "[aeiou]*"
    _mgr0 = re.compile(f"^({_C})?{_V}{_C}")
    _meq1 = re.compile(f"^({_C})?{_V}{_C}({_V})?$")
    _mgr1 = re.compile(f"^({_C})?{_V}{_C}{_V}{_C}")
    _vowely = re.compile(f"^({_C})?{_v}")

    def stem(self, w: str) -> str:
        w = (w or "").lower()
        if len(w) <= 2:
            return w
        w = self._step1ab(w)
        w = self._step1c(w)
        w = self._step2(w)
        w = self._step3(w)
        w = self._step4(w)
        w = self._step5(w)
        return w

    def _m_gt0(self, stem: str) -> bool:
        return bool(self._mgr0.match(stem))

    def _m_eq1(self, stem: str) -> bool:
        return bool(self._meq1.match(stem))

    def _m_gt1(self, stem: str) -> bool:
        return bool(self._mgr1.match(stem))

    def _has_vowel(self, stem: str) -> bool:
        return bool(self._vowely.match(stem))

    def _replace(self, w, suffix, repl, pred) -> str:
        if not w.endswith(suffix):
            return w
        stem = w[: -len(suffix)]
        return stem + repl if pred(stem) else w

    def _step1ab(self, w: str) -> str:
        if w.endswith("s"):
            if w.endswith("sses"):
                w = w[:-2]
            elif w.endswith("ies"):
                w = w[:-2]
            elif not w.endswith("ss") and w.endswith("s"):
                w = w[:-1]
        if w.endswith("eed"):
            if self._m_gt0(w[:-3]):
                w = w[:-1]
        elif w.endswith("ed"):
            stem = w[:-2]
            if self._has_vowel(stem):
                w = self._step1b_fix(stem)
        elif w.endswith("ing"):
            stem = w[:-3]
            if self._has_vowel(stem):
                w = self._step1b_fix(stem)
        return w

    def _step1b_fix(self, stem: str) -> str:
        if stem.endswith(("at", "bl", "iz")):
            return stem + "e"
        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiouylsz":
            return stem[:-1]
        if self._m_eq1(stem) and re.search(f"{self._c}{self._v}{self._c}$", stem) and not stem.endswith("wxy"):
            return stem + "e"
        return stem

    def _step1c(self, w: str) -> str:
        if w.endswith("y") and self._has_vowel(w[:-1]):
            return w[:-1] + "i"
        return w

    def _step2(self, w: str) -> str:
        mapping = [
            ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
            ("izer", "ize"), ("abli", "able"), ("alli", "al"), ("entli", "ent"),
            ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
            ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
            ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
        ]
        for suf, repl in mapping:
            nw = self._replace(w, suf, repl, self._m_gt0)
            if nw != w:
                return nw
        return w

    def _step3(self, w: str) -> str:
        mapping = [
            ("icate", "ic"), ("ative", ""), ("alize", "al"),
            ("iciti", "ic"), ("ical", "ic"), ("ful", ""), ("ness", ""),
        ]
        for suf, repl in mapping:
            nw = self._replace(w, suf, repl, self._m_gt0)
            if nw != w:
                return nw
        return w

    def _step4(self, w: str) -> str:
        suffixes = (
            "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
            "ment", "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize",
        )
        for suf in suffixes:
            if w.endswith(suf):
                stem = w[: -len(suf)]
                if suf == "ion":
                    if self._m_gt1(stem) and stem.endswith(("s", "t")):
                        return stem
                elif self._m_gt1(stem):
                    return stem
        return w

    def _step5(self, w: str) -> str:
        if w.endswith("e"):
            stem = w[:-1]
            if self._m_gt1(stem) or (self._m_eq1(stem) and not (
                re.search(f"{self._c}{self._v}{self._c}$", stem) and not stem.endswith("wxy")
            )):
                w = stem
        if w.endswith("ll") and self._m_gt1(w):
            w = w[:-1]
        return w


def _get_stemmer():
    try:
        from nltk.stem import PorterStemmer
        return PorterStemmer()
    except Exception:
        return _PorterStemmer()


_STEMMER = _get_stemmer()


def locomo_normalize_answer(s: str) -> str:
    """Official LoCoMo normalize_answer (regex → stdlib re)."""
    s = str(s or "").replace(",", "")

    def remove_articles(text):
        return re.sub(r"\b(a|an|the|and)\b", " ", text, flags=re.IGNORECASE)

    def white_space_fix(text):
        return " ".join(text.split())

    def remove_punc(text):
        exclude = set(string.punctuation)
        return "".join(ch for ch in text if ch not in exclude)

    return white_space_fix(remove_articles(remove_punc(s.lower())))


def locomo_f1_score(prediction: str, ground_truth: str) -> float:
    """Official f1_score: Porter-stemmed token F1 after normalize_answer."""
    pred_tokens = [_STEMMER.stem(w) for w in locomo_normalize_answer(prediction).split()]
    gold_tokens = [_STEMMER.stem(w) for w in locomo_normalize_answer(ground_truth).split()]
    common = Counter(pred_tokens) & Counter(gold_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    return (2 * precision * recall) / (precision + recall)


def locomo_multihop_f1(prediction: str, ground_truth: str) -> float:
    """Official f1(): split on commas, mean of max F1 over gold sub-answers."""
    preds = [p.strip() for p in str(prediction).split(",") if p.strip()] or [str(prediction)]
    golds = [g.strip() for g in str(ground_truth).split(",") if g.strip()] or [str(ground_truth)]
    scores = []
    for gt in golds:
        scores.append(max(locomo_f1_score(p, gt) for p in preds))
    return sum(scores) / len(scores) if scores else 0.0


_ADVERSARIAL_OK = (
    "no information available",
    "not mentioned",
)


def locomo_adversarial_score(prediction: str) -> float:
    """Official cat-5: 1 iff prediction contains the abstention phrases."""
    low = (prediction or "").lower()
    if any(p in low for p in _ADVERSARIAL_OK):
        return 1.0
    # Chinese abstention produced by bilingual models
    if any(p in (prediction or "") for p in ("没有相关信息", "未提及", "无信息")):
        return 1.0
    return 0.0


def locomo_qa_score(prediction: str, ground_truth, category: int) -> float:
    """Official eval_question_answering branch for one sample."""
    cat = int(category)
    gold = str(ground_truth)
    if cat == 3:
        gold = gold.split(";")[0].strip()
    if cat in (2, 3, 4):
        return locomo_f1_score(prediction, gold)
    if cat == 1:
        return locomo_multihop_f1(prediction, gold)
    if cat == 5:
        return locomo_adversarial_score(prediction)
    return locomo_f1_score(prediction, gold)


# ---------------------------------------------------------------------------
# LongMemEval official yes/no judge prompts
# ---------------------------------------------------------------------------

def longmemeval_judge_prompt(task: str, question: str, answer: str, response: str,
                             abstention: bool = False) -> str:
    """Exact templates from LongMemEval src/evaluation/evaluate_qa.py."""
    if abstention:
        return (
            "I will give you an unanswerable question, an explanation, and a response from a model. "
            "Please answer yes if the model correctly identifies the question as unanswerable. "
            "The model could say that the information is incomplete, or some other information is given "
            "but the asked information is not.\n\n"
            f"Question: {question}\n\nExplanation: {answer}\n\nModel Response: {response}\n\n"
            "Does the model correctly identify the question as unanswerable? Answer yes or no only."
        )
    if task in ("single-session-user", "single-session-assistant", "multi-session"):
        return (
            "I will give you a question, a correct answer, and a response from a model. "
            "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
            "If the response is equivalent to the correct answer or contains all the intermediate "
            "steps to get the correct answer, you should also answer yes. If the response only "
            "contains a subset of the information required by the answer, answer no. \n\n"
            f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    if task == "temporal-reasoning":
        return (
            "I will give you a question, a correct answer, and a response from a model. "
            "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
            "If the response is equivalent to the correct answer or contains all the intermediate "
            "steps to get the correct answer, you should also answer yes. If the response only "
            "contains a subset of the information required by the answer, answer no. "
            "In addition, do not penalize off-by-one errors for the number of days. "
            "If the question asks for the number of days/weeks/months, etc., and the model makes "
            "off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's "
            "response is still correct. \n\n"
            f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    if task == "knowledge-update":
        return (
            "I will give you a question, a correct answer, and a response from a model. "
            "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
            "If the response contains some previous information along with an updated answer, "
            "the response should be considered as correct as long as the updated answer is the "
            "required answer.\n\n"
            f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    if task == "single-session-preference":
        return (
            "I will give you a question, a rubric for desired personalized response, and a "
            "response from a model. Please answer yes if the response satisfies the desired "
            "response. Otherwise, answer no. The model does not need to reflect all the points "
            "in the rubric. The response is correct as long as it recalls and utilizes the user's "
            "personal information correctly.\n\n"
            f"Question: {question}\n\nRubric: {answer}\n\nModel Response: {response}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
    # fallback: same as multi-session
    return longmemeval_judge_prompt("multi-session", question, answer, response, False)


def longmemeval_label_yes(eval_response: str) -> bool:
    """Official: `'yes' in eval_response.lower()`."""
    return "yes" in (eval_response or "").lower()


# ---------------------------------------------------------------------------
# VehicleMemBench tool-call scoring (eval_utils.score_tool_calls)
# ---------------------------------------------------------------------------

_ARG_PATTERN = re.compile(
    r'(\w+)=("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|True|False|true|false|-?[\d.]+)'
)


def _coerce_arg_value(value_str: str):
    if value_str.startswith('"') or value_str.startswith("'"):
        return value_str[1:-1]
    low = value_str.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"-?\d+", value_str):
        return int(value_str)
    if re.fullmatch(r"-?\d+\.\d+", value_str):
        return float(value_str)
    return value_str


def parse_answer_to_tools(answer_list) -> list:
    """Official VehicleMemBench parse_answer_to_tools."""
    if isinstance(answer_list, str):
        answer_list = [answer_list]
    tools = []
    for answer_str in answer_list or []:
        match = re.match(r"(\w+)\((.*)\)", str(answer_str).strip())
        if not match:
            continue
        func_name, args_str = match.group(1), match.group(2)
        args = {}
        if args_str.strip():
            for arg_match in _ARG_PATTERN.finditer(args_str):
                args[arg_match.group(1)] = _coerce_arg_value(arg_match.group(2))
        tools.append({"name": func_name, "args": args})
    return tools


def parse_pred_tools(text: str) -> list:
    """Extract carcontrol_*() calls from free-form model output."""
    raw = text or ""
    calls = re.findall(r"(carcontrol_\w+\([^;)\n]*\))", raw)
    if not calls:
        calls = [p.strip() for p in re.split(r"[;\n]", raw) if "carcontrol_" in p]
    return parse_answer_to_tools(calls)


def score_tool_calls(pred_calls, ref_calls) -> dict:
    """Official eval_utils.score_tool_calls: (name, json.dumps(args, sort_keys=True))."""
    def to_key(item):
        name = item.get("name")
        args = item.get("args", {})
        return (name, json.dumps(args, sort_keys=True, ensure_ascii=False))

    pred_set = [to_key(c) for c in pred_calls]
    ref_set = [to_key(c) for c in ref_calls]
    tp = 0
    ref_used = set()
    for p in pred_set:
        if p in ref_set and p not in ref_used:
            tp += 1
            ref_used.add(p)
    fp = max(0, len(pred_set) - tp)
    fn = max(0, len(ref_set) - tp)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def tool_exact_match(scored: dict) -> bool:
    """Proxy for official state exact-match when VehicleWorld is not executed:
    predicted tool set equals gold tool set (F1==1 and no extras/missing).
    """
    return scored.get("f1", 0) >= 0.999 and scored.get("fp", 0) == 0 and scored.get("fn", 0) == 0


# ---------------------------------------------------------------------------
# CarMem hierarchical preference scoring
# ---------------------------------------------------------------------------

def parse_pref_fields(text: str) -> list[str]:
    """Gold format: 'Main; Sub; Detail; Attribute'."""
    parts = [p.strip() for p in re.split(r"[;|]", str(text or "")) if p.strip()]
    while len(parts) < 4:
        parts.append("")
    return parts[:4]


def _norm_field(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def carmem_extraction_score(pred: str, gold: str) -> dict:
    """Official In-Schema: main/sub/detail match. Attribute is extra."""
    p, g = parse_pref_fields(pred), parse_pref_fields(gold)
    main = _norm_field(p[0]) == _norm_field(g[0]) and bool(g[0])
    sub = _norm_field(p[1]) == _norm_field(g[1]) and bool(g[1])
    detail = _norm_field(p[2]) == _norm_field(g[2]) and bool(g[2])
    attr = _norm_field(p[3]) == _norm_field(g[3]) and bool(g[3])
    schema = main and sub and detail
    levels = (int(main), int(sub), int(detail))
    return {
        "correct": schema,
        "score": sum(levels) / 3.0,
        "main": main,
        "sub": sub,
        "detail": detail,
        "attr_correct": attr,
        "full_correct": schema and attr,
        "metric": "carmem_extraction",
    }


def carmem_retrieval_hit(retrieved_text: str, gold: str) -> bool:
    """Proxy for official top-n embedding hit: gold attribute (and detail) in retrieved blob."""
    _, _, detail, attr = parse_pref_fields(gold)
    blob = (retrieved_text or "").lower()
    if attr and _norm_field(attr) in blob:
        return True
    if detail and _norm_field(detail) in blob and attr:
        # weak: detail present plus most attr tokens
        tokens = [t for t in re.findall(r"[a-z0-9]+", attr.lower()) if len(t) > 2]
        if tokens and sum(t in blob for t in tokens) >= max(1, len(tokens) - 1):
            return True
    return False


def carmem_maintenance_label(kind: str, pref_type: str) -> str:
    """Official Table 4: equal→pass; negate→update; different→append(MP)/update(SP)."""
    kind = (kind or "").lower()
    ptype = (pref_type or "SP").upper()
    if "equal" in kind:
        return "pass"
    if "negate" in kind:
        return "update"
    if "different" in kind:
        return "append" if ptype == "MP" else "update"
    return "pass"


def parse_maintenance_action(text: str) -> str:
    low = (text or "").strip().lower()
    for name in ("append", "update", "pass"):
        if re.search(rf"\b{name}\b", low):
            return name
    if "追加" in (text or "") or "新增" in (text or ""):
        return "append"
    if "更新" in (text or "") or "替换" in (text or ""):
        return "update"
    if "保持" in (text or "") or "忽略" in (text or "") or "跳过" in (text or ""):
        return "pass"
    return ""
