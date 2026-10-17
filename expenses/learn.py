"""Learned categorisation (spec section 5.1).

A text classifier suggests a category when no rule matches. It learns from the user's own
labels: categories set by hand (weight 3) and by rules (weight 1) — never from its own
predictions. Features: TF-IDF word 1-2-grams + character 3-5-grams of the cleaned narration,
plus a debit/credit marker. Classifier: Complement Naive Bayes (chosen over logistic
regression and a calibrated linear SVM by a benchmark comparison; see build_pipeline).

Everything runs locally and in memory; logs carry counts only (privacy rule).
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field

from expenses import db

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.60
THRESHOLD_RANGE = (0.40, 0.95)
MIN_EXAMPLES = 20
MIN_PER_CATEGORY = 3
MIN_CATEGORIES = 2
MANUAL_WEIGHT = 3.0
TEST_SHARE = 0.25
SEED = 0
CURVE_SIZES = (10, 25, 50, 100, 200, 400, 800, 1600)
THRESHOLD_TABLE = (0.40, 0.50, 0.60, 0.70, 0.80, 0.90)

# Payment-channel words and handles: present on every kind of transaction, so they carry no signal.
_NOISE = re.compile(
    r"\b(?:UPI|POS|IMPS|NEFT|RTGS|NACH|ACH|ECS|BILLPAY|PAYMENT|PAY|PAID|VIA|APP|REF|TXN|SYN|"
    r"OK[A-Z]+|YBL|PAYTM|IBL|AXL|APL|IND|INDIA|PVT|LTD)\b"
)
_KEEP = re.compile(r"[^A-Z ]+")

Predictor = Callable[[str, int], "tuple[int, float] | None"]


@dataclass
class Example:
    text: str  # normalised description (upper case)
    merchant: str  # grouping key for the evaluation split
    amount_minor: int
    label: Hashable  # category id (or name, for the synthetic benchmark)
    manual: bool = False


def clean(description_norm: str, amount_minor: int) -> str:
    """'UPI/R7/BLUE TOKAI/OKICICI/Coffee' (debit) -> 'DEBITTXN BLUE TOKAI COFFEE'."""
    text = _KEEP.sub(" ", description_norm.upper())
    text = " ".join(_NOISE.sub(" ", text).split())
    marker = "CREDITTXN" if amount_minor > 0 else "DEBITTXN"
    return f"{marker} {text}"


def _allowed(kind: str, amount_minor: int) -> bool:
    """Domain constraint: debits are expenses or transfers, credits are income or transfers."""
    if kind == "transfer" or amount_minor == 0:
        return True
    return kind == ("income" if amount_minor > 0 else "expense")


@dataclass
class Model:
    pipeline: object = field(repr=False)
    labels: list  # classes, in the pipeline's order
    kinds: dict  # label -> 'expense' | 'income' | 'transfer'
    n_examples: int
    per_label: dict  # label -> training examples

    def predict_many(
        self, texts: Sequence[str], amounts: Sequence[int]
    ) -> list[tuple[Hashable, float] | None]:
        probabilities = self.pipeline.predict_proba(
            [clean(t, a) for t, a in zip(texts, amounts, strict=True)]
        )
        out = []
        for row, amount in zip(probabilities, amounts, strict=True):
            allowed = [
                (p, label)
                for p, label in zip(row, self.labels, strict=True)
                if _allowed(self.kinds.get(label, "expense"), amount)
            ]
            total = sum(p for p, _ in allowed)
            if not allowed or total <= 0:
                out.append(None)
                continue
            best_p, best = max(allowed, key=lambda pair: pair[0])
            out.append((best, best_p / total))  # re-normalised over the allowed categories
        return out

    def predict(self, text: str, amount_minor: int) -> tuple[Hashable, float] | None:
        return self.predict_many([text], [amount_minor])[0]


def _trainable(examples: Sequence[Example]) -> list[Example]:
    counts = Counter(e.label for e in examples)
    kept = [e for e in examples if counts[e.label] >= MIN_PER_CATEGORY]
    if len(kept) < MIN_EXAMPLES or len({e.label for e in kept}) < MIN_CATEGORIES:
        return []
    return kept


CLASSIFIERS = ("nb", "logreg", "svm")  # "nb" is used by the app; the others are benchmark baselines


def build_pipeline(classifier: str = "nb"):
    """TF-IDF word 1-2-grams + character 3-5-grams, then the classifier.

    "nb": Complement Naive Bayes — chosen after a 10-benchmark comparison (docs/model-evaluation.md):
    as accurate as the others, the best coverage at the default threshold, and the fastest.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import ComplementNB
    from sklearn.pipeline import FeatureUnion, Pipeline
    from sklearn.svm import LinearSVC

    models = {
        "nb": lambda: ComplementNB(alpha=0.3),
        "logreg": lambda: LogisticRegression(C=5.0, class_weight="balanced", max_iter=3000),
        "svm": lambda: CalibratedClassifierCV(LinearSVC(C=1.0, class_weight="balanced"), cv=3),
    }
    words = TfidfVectorizer(
        token_pattern=r"\b[A-Z]{2,}\b", lowercase=False, ngram_range=(1, 2), sublinear_tf=True
    )
    chars = TfidfVectorizer(analyzer="char_wb", lowercase=False, ngram_range=(3, 5), sublinear_tf=True)
    return Pipeline(
        [
            ("features", FeatureUnion([("words", words), ("chars", chars)])),
            ("classifier", models[classifier]()),
        ]
    )


def train(examples: Sequence[Example], kinds: dict, classifier: str = "nb") -> Model | None:
    """Fit the classifier; None when there isn't enough data (see MIN_* constants)."""
    kept = _trainable(examples)
    if not kept:
        return None
    pipeline = build_pipeline(classifier)
    texts = [clean(e.text, e.amount_minor) for e in kept]
    weights = [MANUAL_WEIGHT if e.manual else 1.0 for e in kept]
    pipeline.fit(texts, [e.label for e in kept], classifier__sample_weight=weights)
    # numpy scalars -> plain Python values (category ids stay ints)
    labels = [x.item() if hasattr(x, "item") else x for x in pipeline.named_steps["classifier"].classes_]
    return Model(pipeline, labels, dict(kinds), len(kept), dict(Counter(e.label for e in kept)))


# --- the user's data ------------------------------------------------------------------------------


def examples_from_db(conn: sqlite3.Connection) -> list[Example]:
    return [
        Example(
            r["description_norm"],
            r["merchant"] or r["description_norm"],
            r["amount_minor"],
            r["category_id"],
            r["category_source"] == "manual",
        )
        for r in db.labelled_rows(conn)
    ]


def category_info(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """(id -> kind, id -> name)."""
    rows = db.list_categories(conn)
    return {r["id"]: r["kind"] for r in rows}, {r["id"]: r["name"] for r in rows}


def settings(conn: sqlite3.Connection) -> tuple[bool, float]:
    """(enabled, threshold)."""
    enabled = db.get_setting(conn, "model_enabled", "1") == "1"
    try:
        threshold = float(db.get_setting(conn, "model_threshold", str(DEFAULT_THRESHOLD)))
    except ValueError:
        threshold = DEFAULT_THRESHOLD
    low, high = THRESHOLD_RANGE
    return enabled, min(max(threshold, low), high)


def set_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    db.set_setting(conn, "model_enabled", "1" if enabled else "0")


def set_threshold(conn: sqlite3.Connection, threshold: float) -> None:
    low, high = THRESHOLD_RANGE
    if not low <= threshold <= high:
        raise ValueError(f"threshold must be between {low} and {high}")
    db.set_setting(conn, "model_threshold", f"{threshold:.2f}")


_cache: dict[str, Model | None] = {}  # digest of the training data -> model
_CACHE_SIZE = 8


def _digest(examples: Sequence[Example], kinds: dict) -> str:
    """Identical training data -> identical digest (so a cached model is never another user's)."""
    h = hashlib.sha256()
    for e in examples:
        h.update(f"{e.text}\x1f{e.amount_minor > 0}\x1f{e.label}\x1f{e.manual}\x1e".encode())
    h.update(repr(sorted(kinds.items(), key=lambda kv: str(kv[0]))).encode())
    return h.hexdigest()


def model_for(conn: sqlite3.Connection) -> Model | None:
    """The model trained on this database's labels (cached until they change), or None."""
    examples = examples_from_db(conn)
    kinds, _ = category_info(conn)
    key = _digest(examples, kinds)
    if key not in _cache:
        if len(_cache) >= _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = train(examples, kinds)
        model = _cache[key]
        log.info(
            "category model trained examples=%d categories=%d",
            model.n_examples if model else 0,
            len(model.labels) if model else 0,
        )  # counts only (privacy rule)
    return _cache[key]


def predictor(conn: sqlite3.Connection) -> Predictor | None:
    """(description_norm, amount_minor) -> (category_id, confidence) above the threshold, else None.

    None when the switch is off or there isn't enough data yet.
    """
    enabled, threshold = settings(conn)
    if not enabled:
        return None
    model = model_for(conn)
    if model is None:
        return None

    def suggest(description_norm: str, amount_minor: int) -> tuple[int, float] | None:
        found = model.predict(description_norm, amount_minor)
        if found is None or found[1] < threshold:
            return None
        return found

    return suggest


# --- evaluation -------------------------------------------------------------------------------------


@dataclass
class CategoryScore:
    label: Hashable
    name: str
    precision: float
    recall: float
    support: int


@dataclass
class Evaluation:
    n_train: int
    n_test: int
    train_merchants: int
    test_merchants: int
    shared_merchants: int  # always 0: the split is grouped by merchant
    accuracy: float  # forced choice on merchants never seen in training
    macro_f1: float
    per_category: list[CategoryScore]
    confusion_labels: list[str]
    confusion: list[list[int]]
    thresholds: list[tuple[float, float, float | None]]  # (threshold, coverage, accuracy of those labelled)
    curve: list[tuple[int, float]]  # (training examples, accuracy)
    threshold: float
    coverage: float  # at `threshold`
    accuracy_at_threshold: float | None


class NotEnoughData(ValueError):
    """Too few labelled transactions (or merchants) to train and test."""


def _split(examples: Sequence[Example]) -> tuple[list[Example], list[Example]]:
    """Merchant-grouped split: every merchant is entirely in train or entirely in test."""
    merchants = sorted({e.merchant for e in examples})
    random.Random(SEED).shuffle(merchants)
    n_test = max(1, round(len(merchants) * TEST_SHARE))
    test_merchants = set(merchants[:n_test])
    train_part = [e for e in examples if e.merchant not in test_merchants]
    test_part = [e for e in examples if e.merchant in test_merchants]
    return train_part, test_part


def _accuracy(model: Model, test: Sequence[Example]) -> float:
    predictions = model.predict_many([e.text for e in test], [e.amount_minor for e in test])
    return sum(1 for p, e in zip(predictions, test, strict=True) if p and p[0] == e.label) / len(test)


def evaluate(
    examples: Sequence[Example],
    kinds: dict,
    names: dict | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    classifier: str = "nb",
) -> Evaluation:
    """Train on 75 % of the merchants, test on the other 25 % (never seen in training)."""
    from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support

    names = names or {}
    if len({e.merchant for e in examples}) < 8:
        raise NotEnoughData("need labelled transactions from at least 8 different merchants")
    train_part, test_part = _split(examples)
    model = train(train_part, kinds, classifier)
    if model is None or not test_part:
        raise NotEnoughData(
            f"need at least {MIN_EXAMPLES} labelled transactions in {MIN_CATEGORIES}+ categories "
            f"({MIN_PER_CATEGORY}+ each) to train"
        )

    predictions = model.predict_many([e.text for e in test_part], [e.amount_minor for e in test_part])
    truth = [e.label for e in test_part]
    guessed = [p[0] if p else None for p in predictions]
    confidence = [p[1] if p else 0.0 for p in predictions]
    labels = sorted({*truth, *(g for g in guessed if g is not None)}, key=lambda x: str(names.get(x, x)))
    as_text = [str(x) for x in labels]
    truth_s = [str(x) for x in truth]
    guessed_s = [str(g) if g is not None else "none" for g in guessed]

    correct = [g == t for g, t in zip(guessed, truth, strict=True)]
    precision, recall, _, support = precision_recall_fscore_support(
        truth_s, guessed_s, labels=as_text, zero_division=0
    )
    per_category = [
        CategoryScore(label, str(names.get(label, label)), float(p), float(r), int(s))
        for label, p, r, s in zip(labels, precision, recall, support, strict=True)
    ]
    matrix = confusion_matrix(truth_s, guessed_s, labels=as_text).tolist()

    table = []
    for t in THRESHOLD_TABLE:
        taken = [c for c, conf in zip(correct, confidence, strict=True) if conf >= t]
        table.append((t, len(taken) / len(test_part), (sum(taken) / len(taken)) if taken else None))
    taken = [c for c, conf in zip(correct, confidence, strict=True) if conf >= threshold]

    order = list(train_part)
    random.Random(SEED).shuffle(order)
    curve = []
    for size in [s for s in CURVE_SIZES if s < len(order)] + [len(order)]:
        partial = train(order[:size], kinds, classifier)
        if partial is not None:
            curve.append((size, _accuracy(partial, test_part)))

    return Evaluation(
        n_train=len(train_part),
        n_test=len(test_part),
        train_merchants=len({e.merchant for e in train_part}),
        test_merchants=len({e.merchant for e in test_part}),
        shared_merchants=len({e.merchant for e in train_part} & {e.merchant for e in test_part}),
        accuracy=sum(correct) / len(test_part),
        macro_f1=float(f1_score(truth_s, guessed_s, labels=as_text, average="macro", zero_division=0)),
        per_category=per_category,
        confusion_labels=[str(names.get(x, x)) for x in labels],
        confusion=matrix,
        thresholds=table,
        curve=curve,
        threshold=threshold,
        coverage=len(taken) / len(test_part),
        accuracy_at_threshold=(sum(taken) / len(taken)) if taken else None,
    )


_eval_cache: dict[tuple[str, float], Evaluation | NotEnoughData] = {}


def evaluate_db(conn: sqlite3.Connection) -> Evaluation:
    """Evaluate on this database's own labels; cached until the labels or threshold change."""
    kinds, names = category_info(conn)
    _, threshold = settings(conn)
    data = examples_from_db(conn)
    key = (_digest(data, {**kinds, **{f"name:{k}": v for k, v in names.items()}}), threshold)
    if key not in _eval_cache:
        if len(_eval_cache) >= _CACHE_SIZE:
            _eval_cache.pop(next(iter(_eval_cache)))
        try:
            _eval_cache[key] = evaluate(data, kinds, names, threshold)
        except NotEnoughData as exc:
            _eval_cache[key] = exc
    found = _eval_cache[key]
    if isinstance(found, NotEnoughData):
        raise found
    return found
