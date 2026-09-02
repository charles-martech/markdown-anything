## Why

<!-- The problem this removes, in one or two sentences. For a fix: what went
     wrong, and how it showed up. For an improvement: what was awkward before.
     Not "improves the code" — say what was wrong with it. -->

## What changed

<!-- File by file, or area by area. Name what you touched and what it now does,
     so a reviewer can read the diff alongside this and never wonder why a hunk
     is there. Constants and thresholds: give the value and where it came from. -->

## How it was verified

- [ ] `python3 -m unittest discover -s tests`
- [ ] `bash scripts/selftest.sh`
- [ ] `ruff check .`
- [ ] `shellcheck install.sh macos/build_app.sh scripts/setup.sh scripts/selftest.sh`
- [ ] Tried it in the app itself

<!-- For a fix, say how you reproduced the fault before changing anything and
     what the same check does now. Measurements beat adjectives. Say plainly if
     something could not be run here and why. -->

## What a reviewer should look at

<!-- Judgement calls, thresholds picked by eye, trade-offs accepted, anything
     you are unsure of. New files read, processes started, anything downloaded.
     What you deliberately did not do, where that matters. -->

<!-- Facts only. No account of how the work went, no restating the diff as
     prose, no attribution footer, and never a session link. -->
