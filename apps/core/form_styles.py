"""Widget classes shared by every form in the project.

One definition, because four copies drifted apart the moment the palette
changed: a form is the place a visitor is most likely to be stuck, and a field
that looks different from the field above it reads as a different kind of
field. The tokens (``line``, ``brand``, ``ink``) are defined in
``tailwind.config.js``; see docs/DESIGN_SYSTEM.md.

These strings are scanned by Tailwind through the ``content`` globs in
``tailwind.config.js``, which include ``apps/**/*.py`` - if that glob is ever
narrowed, these classes silently stop being built.
"""

from __future__ import annotations

from typing import Final

#: Text inputs, textareas, selects and number inputs.
INPUT_CLASSES: Final = (
    "w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink "
    "placeholder:text-ink-faint transition "
    "focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30 "
    "disabled:cursor-not-allowed disabled:bg-surface-sunken disabled:text-ink-faint"
)

#: Checkboxes and radios, which must not be full width.
CHECKBOX_CLASSES: Final = "rounded border-line-strong text-brand-600 focus:ring-brand-500"

#: File inputs. The button half is styled through ``file:`` so an upload field
#: does not arrive as the one unstyled control on an otherwise finished form.
FILE_CLASSES: Final = (
    "w-full text-sm text-ink-soft "
    "file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-2 "
    "file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
)
