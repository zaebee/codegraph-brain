"""Posts a ReviewResult as one GitHub review with inline comments (spec §6)."""

import json
import subprocess

from cgis.guardian.findings import Finding, ReviewResult
from cgis.guardian.render import render_inline_comment, render_review_body
from cgis.guardian.skeptic import visible_findings


def build_review(
    result: ReviewResult,
    *,
    diff_index: dict[str, set[int]],
    skeptic_model: str | None,
    footer: str = "",
) -> tuple[str, list[dict[str, object]]]:
    """Split findings into inline comments vs body text (pure, spec §6.2).

    A finding whose line is commentable becomes an inline comment; line=None
    or out-of-diff findings land in the review body. Nothing is lost. The
    footer (model/tokens/coverage) is appended to the body: before inline
    posting existed it always reached the PR via the fallback comment, but a
    successful inline post skips that comment — so it must travel here too.
    """
    inline: list[Finding] = []
    outside: list[Finding] = []
    for finding in visible_findings(result.findings):
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
) -> None:
    """POST one COMMENT review via `gh api` (auto-auth in Actions, spec §6.4).

    Always event=COMMENT, never REQUEST_CHANGES — guardian is an advisor,
    not a gate. Raises CalledProcessError on API rejection; the caller
    decides the fallback (spec §6.5).
    """
    body, comments = build_review(
        result, diff_index=diff_index, skeptic_model=skeptic_model, footer=footer
    )
    payload = {"event": "COMMENT", "body": body, "comments": comments}
    subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews", "-X", "POST", "--input", "-"],
        input=json.dumps(payload),
        text=True,
        check=True,
        capture_output=True,
    )
