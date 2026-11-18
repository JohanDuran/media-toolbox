# media-toolbox: notes for Claude

**This repo is one of three that work together** (video-studio plugin, media-toolbox, MangoAI). Before changing an MCP
tool, the REST API, the port, the installers (`get.ps1`, `install.ps1`, `docker-compose.yml`, `.env.example`), the
reported `version` or `docs/short-spec.md`, read [docs/ECOSYSTEM.md](docs/ECOSYSTEM.md): those are contracts the other
repos depend on. Say in the PR which other repo needs a matching change.

- A release is a `vX.Y.Z` tag: the `images` workflow builds every image with that version baked in.
- Keep `install.ps1` and `get.ps1` ASCII-only (Windows PowerShell 5.1 reads BOM-less files as ANSI) and LF
  (`.gitattributes`).
- `install.ps1` must work both from a clone and from the clone-free folder `get.ps1` creates (no `workers/` there).
