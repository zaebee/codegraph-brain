"""Posts a ReviewResult as one GitHub review with inline comments (spec §6)."""

import json
import subprocess

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import render_inline_comment, render_review_body
from cgis.guardian.skeptic import visible_findings

#: Below this length a quote is too generic to substring-match safely — short
#: keywords/patterns (``)``, ``else:``, ``self.``, ``return``) would collide with
#: unrelated lines (``something_else:``, ``myself.foo``). Such a quote may only
#: anchor via an EXACT line equality, never a substring (#181 review). A real
#: anchor is a full statement, comfortably longer than this.
_MIN_SUBSTRING_ANCHOR = 10


def _anchored_line(finding: Finding, content: dict[int, str] | None) -> int | None:
    """Derive the inline line from the finding's verbatim quote, not the model's guess (#181).

    Searches the file's RIGHT-side lines for the one matching the finding's
    ``anchor`` (falling back to ``evidence``) and returns its real number:
    - no quote or no file content → keep the model's ``line`` (legacy behaviour);
    - an **exact** stripped-line match wins; only if there is none do we fall back
      to a substring match (``needle in line``), and only for quotes long enough
      that a substring is meaningful — so a ``)`` or ``else:`` can never pull a
      comment onto an unrelated trivial line;
    - among the candidates: the model's ``line`` if it is one, else the nearest;
    - quote present but located nowhere → ``None``, demoting to a body comment
      instead of a confidently-wrong inline anchor.

    A finding the model marked file-level (``line is None``) with no explicit
    ``anchor`` stays file-level: ``evidence`` is supporting text, not a
    positional signal, so it must not promote a file-level note to an inline
    comment at a coincidental textual match (#243 review).
    """
    if finding.line is None and finding.anchor is None:
        return None
    quote = (finding.anchor or finding.evidence or "").strip()
    if not content or not quote:
        return finding.line
    needle = next((seg.strip() for seg in quote.splitlines() if seg.strip()), "")
    if not needle:
        return finding.line
    matches = [n for n, text in content.items() if text.strip() == needle]
    if not matches and len(needle) >= _MIN_SUBSTRING_ANCHOR:
        matches = [n for n, text in content.items() if needle in text.strip()]
    if not matches:
        return None
    if finding.line is None:
        return matches[0]
    model_line = finding.line  # bound local so the closure narrows it to int (mypy)
    if model_line in matches:
        return model_line
    return min(matches, key=lambda n: (abs(n - model_line), n))


def build_review(
    result: ReviewResult,
    *,
    diff_index: dict[str, set[int]],
    skeptic_model: str | None,
    footer: str = "",
    diff_content: dict[str, dict[int, str]] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    """Split findings into inline comments vs body text (pure, spec §6.2).

    A finding whose line is commentable becomes an inline comment; line=None
    or out-of-diff findings land in the review body. Nothing is lost. The
    footer (model/tokens/coverage) is appended to the body: before inline
    posting existed it always reached the PR via the fallback comment, but a
    successful inline post skips that comment — so it must travel here too.

    When ``diff_content`` is supplied, each finding's line is first re-anchored
    from its verbatim quote (#181) so the comment lands on the real line rather
    than the model's estimate; an unlocatable quote demotes to a body comment.
    """
    content_by_file = diff_content if diff_content is not None else {}
    inline: list[Finding] = []
    outside: list[Finding] = []
    for raw in visible_findings(result.findings):
        line = _anchored_line(raw, content_by_file.get(raw.file))
        finding = raw if line == raw.line else raw.model_copy(update={"line": line})
        if finding.line is not None and finding.line in diff_index.get(finding.file, set()):
            inline.append(finding)
        else:
            outside.append(finding)
    comments: list[dict[str, object]] = [
        {
            "path": f.file,
            "line": f.line,
            "side": "RIGHT",
            "body": render_inline_comment(f, skeptic_model=skeptic_model),
        }
        for f in inline
    ]
    return render_review_body(result, outside=outside) + footer, comments


def post_inline_review(
    *,
    repo: str,
    pr: int,
    result: ReviewResult,
    diff_index: dict[str, set[int]],
    skeptic_model: str | None,
    footer: str = "",
    diff_content: dict[str, dict[int, str]] | None = None,
) -> None:
    """POST one COMMENT review via `gh api` (auto-auth in Actions, spec §6.4).

    Always event=COMMENT, never REQUEST_CHANGES — guardian is an advisor,
    not a gate. Raises CalledProcessError on API rejection; the caller
    decides the fallback (spec §6.5).
    """
    body, comments = build_review(
        result,
        diff_index=diff_index,
        skeptic_model=skeptic_model,
        footer=footer,
        diff_content=diff_content,
    )
    payload = {"event": "COMMENT", "body": body, "comments": comments}
    subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews", "-X", "POST", "--input", "-"],
        input=json.dumps(payload),
        text=True,
        check=True,
        capture_output=True,
    )
