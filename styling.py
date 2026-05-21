"""
Terminal styling helpers — '70s mainframe meets 2026 minimal-cool.

Used by the ingest + eval scripts for consistent banners and inline progress.
ANSI color is automatically disabled when stdout is not a TTY or when
NO_COLOR is set (per https://no-color.org/).
"""
from __future__ import annotations
import os
import re
import sys

_USE_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")


def _c(code: str) -> str:
    return f"\033[{code}m" if _USE_COLOR else ""


RESET = _c("0")
BOLD = _c("1")
DIM = _c("2")
RED = _c("31")
GREEN = _c("32")
YELLOW = _c("33")
BLUE = _c("34")
MAGENTA = _c("35")
CYAN = _c("36")
GRAY = _c("90")
BRIGHT_CYAN = _c("96")
BRIGHT_GREEN = _c("92")

# Glyphs picked for the aesthetic
ARROW = "▸"
DOT = "●"
PENDING = "·"
CHECK = "✓"
CROSS = "✗"
BLOCK = "█"
SHADE = "▒"

_ANSI_RE = re.compile(r"\033\[[\d;]*m")


def visible_len(s: str) -> int:
    """Length of string after stripping ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s))


_LABEL_COL_W = 13
_LEADING = 2          # "  " indent before label
_TRAILING = 2         # trailing space before right border


def banner(title: str, fields: list[tuple[str, str]],
           min_width: int = 72, max_width: int = 110) -> None:
    """Print a double-line ANSI banner with title and key=value rows.

    Banner width auto-fits the widest field (within [min_width, max_width]).
    Values longer than what max_width allows are word-wrapped onto continuation
    rows under the label column.
    """
    # Compute the natural width required by the longest field
    title_natural = _LEADING + visible_len(title) + _TRAILING
    field_natural = max(
        (_LEADING + _LABEL_COL_W + visible_len(v) + _TRAILING for _, v in fields),
        default=0,
    )
    width = max(min_width, min(max_width, max(title_natural, field_natural)))
    inner_w = width - 2

    border_top = f"{BRIGHT_CYAN}╔{'═' * inner_w}╗{RESET}"
    border_bot = f"{BRIGHT_CYAN}╚{'═' * inner_w}╝{RESET}"
    blank_row = f"{BRIGHT_CYAN}║{RESET}{' ' * inner_w}{BRIGHT_CYAN}║{RESET}"

    print(border_top)
    title_inner = f"  {BOLD}{title.upper()}{RESET}"
    pad = inner_w - visible_len(title_inner)
    print(f"{BRIGHT_CYAN}║{RESET}{title_inner}{' ' * max(pad, 0)}{BRIGHT_CYAN}║{RESET}")
    print(blank_row)

    value_col_w = inner_w - _LEADING - _LABEL_COL_W - _TRAILING
    for label, value in fields:
        wrapped = _wrap_ansi_value(value, value_col_w)
        for i, segment in enumerate(wrapped):
            if i == 0:
                row = f"  {DIM}{label:<{_LABEL_COL_W}}{RESET}{segment}"
            else:
                row = f"  {' ' * _LABEL_COL_W}{segment}"
            pad = inner_w - visible_len(row)
            print(f"{BRIGHT_CYAN}║{RESET}{row}{' ' * max(pad, 0)}{BRIGHT_CYAN}║{RESET}")
    print(border_bot)


def _wrap_ansi_value(value: str, max_w: int) -> list[str]:
    """Word-wrap an (ANSI-styled) string to fit `max_w` visible columns.

    Splits on spaces. Preserves ANSI codes positionally — at each line break we
    track which color is currently 'open' and re-emit it on the next line so
    styling doesn't bleed onto borders.
    """
    if visible_len(value) <= max_w:
        return [value]

    tokens = value.split(" ")
    lines: list[str] = []
    current: list[str] = []
    current_visible = 0
    open_code: str = ""  # last unclosed ANSI code, replayed on continuation lines

    for tok in tokens:
        tok_visible = visible_len(tok)
        # +1 for the space separator (if there's already content)
        added = tok_visible + (1 if current else 0)
        if current and current_visible + added > max_w:
            # Flush current line. If we had an open color, close it for safety.
            joined = " ".join(current)
            if open_code:
                joined += RESET
            lines.append(joined)
            current = []
            current_visible = 0
            # Start next line with replay of open color, if any
            if open_code:
                current.append(open_code)
                # That replay contributes 0 visible width but is a token
                # to ensure styling resumes; subsequent tokens will append
                # with spaces after this.
                # Trick: we want the next "real" token to flow as if it's
                # the first on this line, so reset bookkeeping.
                # Append the replay code directly into current_visible=0
        current.append(tok)
        current_visible += added

        # Track ANSI state: remember the LAST open code in the token if not closed
        for m in _ANSI_RE.finditer(tok):
            code = m.group(0)
            if code == RESET:
                open_code = ""
            else:
                open_code = code

    if current:
        lines.append(" ".join(current))
    return lines


def hr(width: int = 72, char: str = "─") -> str:
    return f"{DIM}{char * width}{RESET}"


def section(title: str, width: int = 72) -> str:
    """Single-line section divider with a title bar."""
    label = f" {title} "
    pad = width - len(label)
    left = pad // 2
    right = pad - left
    return f"{DIM}{'─' * left}{RESET}{BOLD}{label}{RESET}{DIM}{'─' * right}{RESET}"


def summary(headline: str, lines: list[str], width: int = 72) -> None:
    """Print a closing summary block: separator, headline, indented body lines."""
    print(hr(width))
    print(f"  {BRIGHT_GREEN}{CHECK}{RESET} {BOLD}{headline}{RESET}")
    for line in lines:
        print(f"    {DIM}·{RESET} {line}")
    print(hr(width))


def progress_glyph(success: bool = True) -> str:
    """Inline glyph emitted by contextual_retrieval per call."""
    if success:
        return f"{GREEN}{DOT}{RESET}"
    return f"{RED}{CROSS}{RESET}"
