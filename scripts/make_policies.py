"""Extract readable policy text from the fetched public HTML pages.

Usage:
    python scripts/make_policies.py

Writes plain-text policy excerpts into policy_sources/*.txt for review.
"""
import re
import html as html_mod
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE.parent / "policy_sources"

BLOCK_TAGS = re.compile(
    r"<(p|div|br|h[1-6]|li|tr|td|th|section|article|nav|header|footer)[^>]*>",
    re.I,
)
STRIP_TAGS = re.compile(r"<[^>]+>")
SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S)
HEADER_FOOTER_NAV = re.compile(
    r"<(header|footer|nav)[^>]*>.*?</\1>", re.S
)
CLASS_SEL = re.compile(r'<[^>]*class="[^"]*"[^>]*>')


def extract_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    raw = SCRIPT_STYLE.sub(" ", raw)
    raw = HEADER_FOOTER_NAV.sub(" ", raw)
    # Tease out main content: drop sidebar-ish divs heuristically by class.
    raw = CLASS_SEL.sub(lambda m: re.sub(r"<[^>]+>", " ", m.group(0)) or " ", raw)
    raw = BLOCK_TAGS.sub("\n", raw)
    raw = STRIP_TAGS.sub("", raw)
    raw = html_mod.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n", raw)
    lines = [ln.strip() for ln in raw.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def main() -> None:
    for html_path in sorted(SOURCES.glob("*.html")):
        text = extract_text(html_path)
        out = SOURCES.parent / (html_path.stem + ".txt")
        out.write_text(text, encoding="utf-8")
        print(f"{html_path.name}: {len(text)} chars -> {out.name}")


if __name__ == "__main__":
    main()
