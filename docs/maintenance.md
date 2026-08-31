# Looking after this repository

The code in the repository is only half of what keeps a public project safe and
working. The other half is a handful of settings on GitHub that no file can
turn on for you. This is the list, and why each one is here.

## Turn these on once

**Settings → General → Features**

- [ ] **Issues** on. The bug reports are the point of publishing this.
- [ ] **Discussions** off unless you want to run them. An unanswered forum
      reads worse than no forum.
- [ ] **Wikis** off. The docs live in the repository, where they get reviewed.

**Settings → Code security**

- [ ] **Private vulnerability reporting** on. This is what `SECURITY.md` and
      the issue-template link both point at; without it, the link 404s and
      people report holes in public issues instead.
- [ ] **Dependabot alerts** and **security updates** on.
- [ ] **Secret scanning** and **push protection** on. Free on public
      repositories, and it stops a key going out in a commit.
- [ ] **CodeQL**: the workflow in `.github/workflows/codeql.yml` runs it, so
      "Advanced" setup, not "Default", or the two will run twice.

**Settings → Actions → General**

- [ ] Workflow permissions: **read-only**, and leave "allow GitHub Actions to
      create and approve pull requests" off. Nothing here needs to write.
- [ ] Fork pull requests: require approval for **all outside collaborators**,
      so a stranger's first pull request cannot run in CI unread.

**Settings → Rules → Rulesets** (or branch protection on `main`)

- [ ] Require a pull request before merging.
- [ ] Require status checks to pass: `Test on Python 3.11`, `Lint`,
      `Build the Mac app`.
- [ ] Block force pushes and deletion of `main`.

Even working alone, this is worth it: the checks are what stop a tired evening
from shipping a broken installer to everyone who runs the one-line install.

## The release

There is no build to publish. The install line points at `main`, so **whatever
is on `main` is what people install**, immediately, on their own machines.
That is the whole reason CI has to stay green and `main` has to stay protected.

When something user-visible changes, bump `VERSION` in `macos/build_app.sh` and
add a line to `CHANGELOG.md`. Tag it if you want a point to refer back to:

```bash
git tag -a v1.0.3 -m "What changed" && git push origin v1.0.3
```

## Every so often

- **Watch the Pandoc pin.** `PANDOC_FALLBACK` in `app/server.py` is what gets
  installed when GitHub's API is rate-limited or unreachable. It should be a
  version that still downloads.
- **Watch the reader libraries.** `READER_PACKAGES` in the same file has upper
  bounds so a new major version cannot arrive on someone's machine unannounced.
  Raising a bound means testing a PDF and a spreadsheet afterwards.
- **Read the Dependabot pull requests** for the actions. They are other
  people's code running with your repository's token.
- **Run `scripts/selftest.sh` on a real Mac** now and then. CI proves the app
  builds; only a Mac proves the icon still opens it.

## Answering issues

Most reports are "this file converted badly". Ask for the file. With the file
it is usually a routing-table line or a bug in one converter; without it, it is
a conversation. Both are fine, but only one ends in a fix.
