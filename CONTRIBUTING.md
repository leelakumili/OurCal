# Contributing to OurCal

Thanks for your interest. This document covers the two constraints that shape
every change, how to run things, and what a good pull request looks like.

## Two hard constraints

These are deliberate and predate any individual change. A pull request that
breaks either will be asked to change, however good the idea is.

**1. One file.** The entire application is `ourcal.py`, with the interface
embedded as an HTML/CSS/JS string. Do not split it into modules or extract the
frontend into separate `.js` / `.css` files. The whole point is that someone can
read, audit and run the thing without a build step or a package tree.

**2. Standard library only, plus two Google packages.** `DEPS` is
`google-api-python-client` and `google-auth-oauthlib`. No web framework, no
template engine, no CSS framework, no CDN, no bundler, no transpiler. Python
3.9+ syntax — the venv may be built on 3.9, so no `match` statements and no
`X | Y` unions.

## Getting set up

```bash
git clone <your-fork-url> && cd OurCal
OURCAL_DEMO=1 python3 ourcal.py     # runs against fixtures, no Google needed
```

Demo mode is the right way to develop. It exercises every flow — create, edit,
sync, delete — against an in-memory store, so you never risk a real calendar.

## Tests

```bash
python3 -m unittest discover tests -q
```

All tests live in `tests/test_ourcal.py` and run with **no network access**.
Anything that would talk to Google is either behind demo mode or uses the
`_FakeService` test double.

Expectations for a pull request:

- **New behaviour comes with tests**, written before the implementation.
- **Tests assert real behaviour**, not that a mock was called.
- **Test output stays pristine** — no new warnings.
- The **full suite passes** before you open the PR.

Never point tests at real credentials. A past session came close to writing to a
live calendar during "manual verification"; if you need to check something by
hand, use demo mode.

## Style

Follow what is already in the file rather than importing habits from elsewhere.

- **Docstrings explain *why*, not *what*.** The code already says what it does.
  The valuable comment is the one recording the constraint or the bug that made
  the code look the way it does.
- **Comment surprises, not syntax.** If a reader would reasonably ask "why not
  the obvious thing here?", answer it.
- Match the surrounding naming, spacing and section banners.

## Things worth knowing before you change them

- **`merge_events` decides which rows are the same appointment.** Its identity is
  `(title, start instant, end instant, allDay)` — deliberately not the iCalUID,
  because the same appointment typed separately into several accounts gets a
  different UID per copy. If you add a field to `normalize`, ask whether
  `merge_events` needs to merge it too; forgetting that once caused silent data
  loss.
- **Edits send only changed fields.** `build_patch_body` prunes to the payload's
  `changed` list, so an edit cannot overwrite a value on another copy of a merged
  row that the user never touched.
- **Writes use `events().patch()`, never `events().update()`.** `patch` is a
  partial update, so omitting `transparency` preserves an event's busy/free
  state. With `update` the same omission would clear it.
- **`sendUpdates="none"` on every write.** OurCal never emails an event's guests.

## Reporting bugs

Please include what you expected, what happened, whether it reproduces in demo
mode (`OURCAL_DEMO=1`), and your macOS and Python versions. Never paste the
contents of `credentials.json`, `accounts.json` or any `token_*.json` — they are
git-ignored for exactly this reason.

## License

This project does not yet carry a LICENSE file, which means default copyright
applies and the code cannot legally be reused, modified or redistributed. Adding
one is the maintainer's decision. Until then, please raise an issue before
building anything substantial on top of it.
