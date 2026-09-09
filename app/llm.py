"""The three LLM-touching edges, behind one interface. MVP ships deterministic stubs.

Why an interface: the handoff pins "LLM only at the edges, navigation is deterministic"
and "de-identification is a network boundary, not a policy" — identifiable text must be
scrubbed here before anything could leave the region. Swap `StubEdges` for a real
implementation (self-hosted 7-8B in-region, or a frontier API on the de-identified JSON
once a KVKK standard contract is on file) without touching main.py.

    HEKIMYOL_LLM=stub   (default)
"""
import os
import re

# TCKN (11 digits), TR mobile, e-mail, dd.mm.yyyy dates.
_PII = [
    (re.compile(r"\b[1-9]\d{10}\b"), "[TCKN]"),
    (re.compile(r"(?:\+90|0)?\s?5\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b"), "[TELEFON]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EPOSTA]"),
    (re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"), "[TARIH]"),
]

# Stub pathway router: first keyword hit wins. Root fragments, not whole words, because
# Turkish suffixes mutate the stem ("göğüs" -> "göğsüm", "karın" -> "karnım").
# Real version = a symptom-extraction model.
_KEYWORDS = {
    "chest_pain": ["göğs", "göğüs", "gögs", "gogus", "kalb", "kalp", "çarpıntı", "carpinti"],
    "headache": ["baş ağr", "başağr", "bas agr", "başım", "basim", "migren", "kafa"],
    "abdominal_pain": ["karn", "karın", "karin", "mide", "göbek", "gobek", "kasık", "kasik"],
    "dyspnea": ["nefes", "soluk", "boğul", "bogul", "hava açlığı", "hava aclii", "tıkanıyor"],
}


class StubEdges:
    def deidentify(self, text: str) -> tuple[str, int]:
        n = 0
        for rx, repl in _PII:
            text, k = rx.subn(repl, text)
            n += k
        # ponytail: no name NER. Person names pass through. Ceiling: swap in a TR NER
        # model (or the self-hosted de-id model) before real patient traffic.
        return text.strip(), n

    def route(self, deid_text: str) -> str | None:
        low = deid_text.lower()
        for slug, kws in _KEYWORDS.items():
            if any(k in low for k in kws):
                return slug
        return None

    def report(self, *, pathway: dict, complaint_deid: str, trail: list[dict],
               outcome: dict) -> str:
        # ponytail: string template, not generated prose. Swap for an LLM call on this
        # exact (already de-identified) payload when report quality matters.
        lines = [
            f"# Ön Konsültasyon Raporu — {pathway['title']}",
            "",
            "> Bu rapor hastanın kendi beyanına ve deterministik bir triyaj akışına "
            "dayanır. Teşhis değildir; hekim değerlendirmesinin yerine geçmez.",
            "",
            f"**Hasta şikâyeti (kimliksizleştirilmiş):** {complaint_deid or '—'}",
            "",
            "## Yanıt Akışı",
        ]
        for i, step in enumerate(trail, 1):
            flag = f"  ⚠️ {step['risk_flag']}" if step.get("risk_flag") else ""
            lines.append(f"{i}. {step['q']}\n   → {step['a']}{flag}")
        flags = sorted({s["risk_flag"] for s in trail if s.get("risk_flag")})
        lines += [
            "",
            "## Sonuç",
            f"- **Aciliyet:** {outcome['urgency']}",
            f"- **Önerilen birim:** {outcome['department']}",
            f"- **Kırmızı bayraklar:** {', '.join(flags) if flags else 'yok'}",
            f"- **Ayırıcı tanı (akış temelli):** "
            f"{', '.join(outcome.get('differential', [])) or '—'}",
            "",
            f"## Kaynak\n{pathway['source']} (pathway v{pathway['version']})",
        ]
        return "\n".join(lines)


def get_edges() -> StubEdges:
    kind = os.environ.get("HEKIMYOL_LLM", "stub")
    if kind == "stub":
        return StubEdges()
    raise ValueError(f"unknown HEKIMYOL_LLM={kind!r} (only 'stub' implemented)")


def demo() -> None:
    e = StubEdges()
    clean, n = e.deidentify("Ben Ali, TCKN 12345678901, tel 0532 111 22 33, göğsüm ağrıyor")
    assert "[TCKN]" in clean and "[TELEFON]" in clean and n == 2, (clean, n)
    assert e.route(clean) == "chest_pain", e.route(clean)
    assert e.route("hiçbir anahtar kelime yok") is None
    print("llm.demo OK")


if __name__ == "__main__":
    demo()
