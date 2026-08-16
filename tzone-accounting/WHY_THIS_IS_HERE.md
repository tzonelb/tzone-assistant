# Why this directory is here

`tzone-accounting` is a **separate product**, not part of the T-ZONE CRM platform. It is sitting
in this repository only because the GitHub integration used to build it could not create a new
repository (`403 Resource not accessible by integration`), and the work had to be preserved
somewhere durable rather than left in an ephemeral container.

**Intended home:** its own repository, `tzonelb/tzone-accounting`.

## Moving it out

Once the empty repository exists on GitHub:

```bash
git clone https://github.com/tzonelb/tzone-accounting.git
cp -r tzone-accounting/* tzone-accounting.git/       # everything except this file
cd tzone-accounting.git
git add -A && git commit -m "Modular offline-first accounting system"
git push -u origin main
```

Then delete this directory from `tzone-assistant` — nothing in the CRM platform imports it, and
nothing in it imports the CRM platform. The two share no code, no database and no build.

Start reading at [README.md](README.md), then [docs/MODULES.md](docs/MODULES.md).
