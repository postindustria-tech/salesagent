"""Guard: no template opens a <form> inside another <form>.

WHY THIS EXISTS. The favicon forms on Settings -> Account were written inside
``account-settings-form``. The HTML parser ignores a nested ``<form>`` start tag, and the first
inner ``</form>`` then closes the OUTER form. Everything after it - the Custom Domain field and
the "Save Account Settings" button - belonged to no form, so Save did nothing at all: no request,
no error, the value simply never left the browser. The stray favicon buttons meanwhile submitted
the account form instead of their own.

A route test cannot see this, because the server is never called. This reads the template
source instead and tracks ``<form>`` depth line by line. A control that must sit visually inside
another form belongs to a standalone form through the ``form="<id>"`` attribute.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates"

FORM_TAG = re.compile(r"<(/?)form\b", re.IGNORECASE)


def _nested_forms(template_dir: Path = TEMPLATE_DIR) -> list[str]:
    found: list[str] = []
    for path in sorted(template_dir.rglob("*.html")):
        depth = 0
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in FORM_TAG.finditer(line):
                if match.group(1):
                    depth = max(depth - 1, 0)
                    continue
                if depth:
                    found.append(f"{path.relative_to(template_dir).as_posix()}:{lineno}")
                depth += 1
    return found


def test_no_template_nests_a_form() -> None:
    """A nested <form> silently detaches every control after it from the outer form."""
    nested = _nested_forms()
    assert not nested, (
        "Nested <form> tags (move the inner form outside and point its controls at it with "
        'form="<id>"):\n  ' + "\n  ".join(nested)
    )


def test_guard_detects_a_nested_form(tmp_path: Path) -> None:
    """The scan must flag the shape that broke Settings -> Account, or the guard proves nothing."""
    (tmp_path / "page.html").write_text(
        '<form id="outer">\n  <form action="/inner"></form>\n  <button>Save</button>\n</form>\n',
        encoding="utf-8",
    )

    assert _nested_forms(tmp_path) == ["page.html:2"]
