"""LLM backends. The pipeline only needs generate(system, messages) -> JSON string."""
from __future__ import annotations

import os
import re
import time


class LLM:
    name = "base"

    def generate(self, system: str, messages: list[dict]) -> str:
        raise NotImplementedError


def _merge_same_role(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + m["content"]
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class GeminiLLM(LLM):
    """Gemini. Uses the current `google-genai` SDK when installed, else the legacy `google-generativeai` (end-of-life, still works)."""

    def __init__(self, model: str | None = None):
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY (or run with LLM_BACKEND=fake)")
        self.model_name = model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        self.name = f"gemini:{self.model_name}"
        self._key, self._last, self._new, self._old = key, 0.0, None, None
        want = os.environ.get("GEMINI_SDK", "auto")          # auto | new | legacy
        if want in ("auto", "new"):
            try:
                from google import genai
                self._new = genai.Client(api_key=key)
            except ImportError:
                if want == "new":
                    raise RuntimeError("pip install google-genai")
        if self._new is None:
            self._legacy()

    def _legacy(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import google.generativeai as genai
        genai.configure(api_key=self._key)
        self._old = genai
        self._new = None

    def _call(self, system: str, contents: list[dict]) -> str:
        if self._new is not None:
            from google.genai import types
            cfg = types.GenerateContentConfig(system_instruction=system, temperature=0.0, response_mime_type="application/json")
            parts = [types.Content(role=c["role"], parts=[types.Part(text=c["parts"][0])]) for c in contents]
            return self._new.models.generate_content(model=self.model_name, contents=parts, config=cfg).text or ""
        model = self._old.GenerativeModel(self.model_name, system_instruction=system,
                                          generation_config={"temperature": 0.0, "response_mime_type": "application/json"})
        return model.generate_content(contents).text or ""

    def generate(self, system: str, messages: list[dict]) -> str:
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
                    for m in _merge_same_role(messages)]
        # Free tier allows ~5 requests/min: optionally space calls, and retry on 429 using the server's hint.
        gap = float(os.environ.get("GEMINI_MIN_INTERVAL", "0"))
        wait = gap - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            try:
                self._last = time.time()
                return self._call(system, contents)
            except Exception as e:
                rate = "429" in str(e) or "ResourceExhausted" in type(e).__name__ or "RESOURCE_EXHAUSTED" in str(e)
                if not rate:
                    if self._new is not None and os.environ.get("GEMINI_SDK", "auto") == "auto":
                        try:   # new SDK failed for a non-quota reason: fall back to the legacy one once
                            self._legacy()
                            continue
                        except ImportError:
                            pass
                    raise
                if attempt == 3:
                    raise
                m = re.search(r"retry in ([\d.]+)s", str(e))
                time.sleep(min(float(m.group(1)) + 1 if m else 15, 60))
        return ""


def get_llm() -> LLM:
    backend = os.environ.get("LLM_BACKEND", "gemini").lower()
    if backend == "fake":
        from fake_llm import HeuristicLLM
        return HeuristicLLM()
    return GeminiLLM()
