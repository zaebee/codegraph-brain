"""Posts a ReviewResult as one GitHub review with inline comments (spec §6)."""

import json
import subprocess

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import render_inline_comment, render_review_body
from cgis.guardian.skeptic import visible_findings


def _anchored_line(finding: Finding, content: dict[int, str] | None) -> int | None:
    """Derive the inline line from the finding's verbatim quote, not the model's guess (#181).

    Searches the file's RIGHT-side lines for the one matching the finding's
    ``anchor`` (falling back to ``evidence``) and returns its real number:
    - no quote or no file content → keep the model's ``line`` (legacy behaviour);
    - the model's ``line`` is itself a match → trust it;
    - otherwise → the match nearest the model's guess;
    - quote present but found nowhere in the changed lines → ``None``, so a
      hallucinated coordinate demotes to a file-level body comment instead of a
      confidently-wrong inline anchor.
    """
    quote = (finding.anchor or finding.evidence or "").strip()
    if not content or not quote:
        return finding.line
    needle = next((seg.strip() for seg in quote.splitlines() if seg.strip()), "")
    if not needle:
        return finding.line
    matches = [
        n for n, text in content.items() if (s := text.strip()) and (needle in s or s in needle)
    ]
    if not matches:
        return None
    if finding.line in matches:
        return finding.line
    return min(matches, key=lambda n: (abs(n - (finding.line or matches[0])), n))


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
    content_by_file = diff_content or {}
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
