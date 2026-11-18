# media-toolbox in the video-studio ecosystem

The toolbox is one of three repos released together. The full picture, the compatibility table and the release order
are in [video-studio's ECOSYSTEM.md](https://github.com/JohanDuran/video-studio/blob/main/ECOSYSTEM.md); this page lists
what touches the toolbox.

| Repo | Role |
|---|---|
| [video-studio](https://github.com/JohanDuran/video-studio) | The plugin and front door. Registers the toolbox's MCP server, **installs the toolbox** with `get.ps1` and pins its release |
| **media-toolbox** (this repo) | Makes media |
| [MangoAI](https://github.com/JohanDuran/MangoAI) | Edits and renders. Calls the toolbox's REST API from its Toolbox tab and reads/writes `short.json` |

## What others rely on here (change with care)

- **MCP tools** (`gateway/app/mcp_tools.py`): names, parameters and results. The video-studio skills call them by name.
- **REST API** (`gateway/app/main.py`): `GET /api/state`, `POST /api/jobs`, `POST /api/jobs/{id}/wait`,
  `GET /api/library`, `POST /api/library/mark`. MangoAI's `packages/server/src/toolbox.ts` uses them.
- **Address**: `http://localhost:8765`, MCP at `/mcp` (the plugin's `.mcp.json`; MangoAI's `MANGO_TOOLBOX_URL` default).
- **Version**: `version` in `/api/state` and `toolbox_status`, baked into the gateway image by the `images` workflow
  (`TOOLBOX_BUILD_VERSION`). `/studio-setup` compares it with the plugin's pinned minimum.
- **Installer**: `get.ps1 -Version vX.Y.Z` (downloads `install.ps1`, `docker-compose.yml`, `.env.example`,
  `docs/short-spec.md` from that tag into `~/.video-studio/media-toolbox`), `install.ps1`'s parameters and the
  `VIDEO_STUDIO_SETUP=1` quiet ending. `/studio-setup` runs them. A new file the install needs goes in `get.ps1`'s list
  (and in video-studio's `scripts/check-ecosystem.mjs`).
- **The `short.json` format** (`docs/short-spec.md`): MangoAI imports and exports it losslessly; the `/short` skill writes it.

## What the toolbox relies on elsewhere

- **The studio layout** chosen by `/studio-setup` (`sfx_library/`, `voices/`, `recordings/`, `_scratch/` under the
  studio folder), passed to `install.ps1` as `-StudioDir` / `-RecordingsDir`.

## Releasing the toolbox

Push tag `vX.Y.Z`; the `images` workflow builds and pushes every image. Then bump `media_toolbox` in video-studio's
`plugins/video-studio/versions.json` and run its `scripts/check-ecosystem.mjs`.
