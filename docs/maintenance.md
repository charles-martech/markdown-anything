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
      `.github/codeql/codeql-config.yml` turns off two queries and says why in
      full. Read that comment before adding anything network-facing to the
      app: the reasoning it rests on stops holding at that point.

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

**Tagging is publishing.** The installer asks GitHub for the newest release
tag and installs that, and the app's "Check for updates" button offers the same
tag to people who already have it. Nothing reaches anyone from `main` — so a
merge is safe, and a tag is the moment you ship.

That is a deliberate trade for the older arrangement, where the install line
served `main` and every merge was a release. Keep `main` green and protected
anyway: it is what the next tag will be cut from.

When something user-visible changes, bump the `VERSION` file at the root, give
the `CHANGELOG.md` section that version and the date it landed, and once it is
merged and CI is green on `main`, tag that commit:

```bash
git checkout main && git pull
git tag -a v1.0.4 -m "What changed" && git push origin v1.0.4
```

Then draft a release from the tag on GitHub, with that changelog section as the
notes. Open the app on a real Mac before tagging: a tag says "this one is
good", and CI can build the bundle but cannot double-click it.

`BUNDLE_FORMAT` at the root is a separate number, and it matters. An in-app
update replaces the app's Python and HTML, which live in the support folder,
but it cannot replace the launcher script, `Info.plist` or the icon inside the
bundle. **If you change any of those three, bump `BUNDLE_FORMAT`.** An update
carrying a higher number than the installed bundle refuses to install itself
and tells the person to run the install line instead, which is the only thing
that can replace a bundle. Forgetting to bump it is how someone ends up with
new code under an old launcher.

`scripts/doc2gfm.py` carries its own `VERSION`. That is the converter's, not
the app's — it is written into the front matter of every file it produces, so
it moves when the converter's output changes, not when the app is released.

## Every so often

- **Watch the Pandoc pin.** `PANDOC_FALLBACK` in `app/server.py` is what gets
  installed when GitHub's API is rate-limited or unreachable. It should be a
  version that still downloads.
- **Watch the reader libraries.** `READER_PACKAGES` in the same file has upper
  bounds so a new major version cannot arrive on someone's machine unannounced.
  Raising a bound means testing a PDF and a spreadsheet afterwards.
- **Raise the ruff pin** in `.github/workflows/ci.yml` now and then, and run
  `ruff check .` locally with the new version before pushing it. It is pinned
  so that a lint release cannot redden a pull request that changed nothing.
- **Read the Dependabot pull requests** for the actions. They are other
  people's code running with your repository's token.
- **Run `scripts/selftest.sh` on a real Mac** now and then. CI proves the app
  builds; only a Mac proves the icon still opens it.

## Answering issues

Most reports are "this file converted badly". Ask for the file. With the file
it is usually a routing-table line or a bug in one converter; without it, it is
a conversation. Both are fine, but only one ends in a fix.
