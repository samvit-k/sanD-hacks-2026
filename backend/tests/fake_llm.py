"""Deterministic stand-in for Gemini so the pipeline can be tested without credentials."""
import hashlib
import re
from typing import List

import numpy as np

from app.contracts import (ChatAnswer, Conditions, ExtractedClaim, ExtractionResult, ImpactJudgment, PairJudgment)

_LINE = re.compile(r"^\[([a-z0-9-]+:p\d+_s\d+)\] \((\w+)\) (.*)$")
_ID = re.compile(r"\[([a-z0-9-]+:p\d+_s\d+)\]")
_STOP = {"the", "a", "an", "of", "and", "in", "on", "for", "with", "to", "was", "were", "is", "after", "than", "by"}


class FakeLLM:
    model_name = "fake"

    def __init__(self, fabricate: bool = False, fail_pairs: bool = False, pair_label: str = "apparent_contradiction"):
        self.fabricate = fabricate      # cite a non-existent ID in judgments
        self.fail_pairs = fail_pairs    # raise on pair comparisons
        self.pair_label = pair_label
        self.chat_answer = None          # set to a ChatAnswer to script the next chat replies
        self.fail_chat = False
        self.prompts: List[str] = []

    def count(self, marker: str) -> int:
        return sum(marker in p for p in self.prompts)

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(768, dtype=np.float32)
            for w in re.findall(r"[a-z]+", t.lower()):
                if w not in _STOP:
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 768] += 1
            out.append((v / (np.linalg.norm(v) or 1)).tolist())
        return out

    def generate_json(self, prompt, model_cls):
        self.prompts.append(prompt)
        if model_cls is ExtractionResult:
            claims = []
            for line in prompt.splitlines():
                m = _LINE.match(line)
                if m and m.group(2) == "results" and re.search(r"\d", m.group(3)):
                    dur = re.search(r"(\d+ (weeks|months))", m.group(3))
                    claims.append(ExtractedClaim(text=m.group(3), sentence_ids=[m.group(1)],
                                                 conditions=Conditions(duration=dur.group(1) if dur else None)))
            return ExtractionResult(claims=claims)
        if model_cls is ChatAnswer:
            if self.fail_chat:
                raise RuntimeError("simulated Gemini outage")
            if self.chat_answer is not None:
                return self.chat_answer
            # Default: a grounded answer about the first claim shown, citing its first source sentence.
            claims = prompt.split("SOURCE SENTENCES:", 1)[1]
            token = re.search(r"\[\[(claim:\d+)\]\]", claims).group(1)
            sid = _ID.search(claims).group(1)
            return ChatAnswer(answer=f"According to [[{token}]], the reported result relates to [[q]].",
                              cited_sentence_ids=[sid], insufficient_evidence=False)
        first, second = self._two_blocks(prompt)
        ids = [first[0], second[0]] + (["zz-fake:p9_s9"] if self.fabricate else [])
        if model_cls is PairJudgment:
            if self.fail_pairs:
                raise RuntimeError("simulated Gemini outage")
            return PairJudgment(label=self.pair_label, explanation="Durations differ.",
                                key_differences=["duration"], cited_sentence_ids=ids)
        if model_cls is ImpactJudgment:
            return ImpactJudgment(impact="qualifies", rationale="The new study limits the earlier result.",
                                  cited_sentence_ids=ids)
        raise TypeError(model_cls)

    @staticmethod
    def _two_blocks(prompt):
        markers = ["CLAIM A", "CLAIM B"] if "CLAIM A" in prompt else ["EARLIER FINDING", "NEW FINDING"]
        a_part = prompt.split(markers[0], 1)[1].split(markers[1], 1)[0]
        b_part = prompt.split(markers[1], 1)[1].split("Condition comparison", 1)[0]
        return _ID.findall(a_part), _ID.findall(b_part)
