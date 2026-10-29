# Generation Backend

Use this file before final image generation. The goal is to choose a working route without hard-coding credentials or forcing one local environment.

## Route Priority

1. Use an existing Xinghe deployment helper in the active workspace when present.
2. Use a fixed-interface generation script from a related Xinghe skill when it is present and configured.
3. Use built-in `image_gen` as the final fallback.

## Discovery

Search locally before choosing the backend:

```powershell
Get-ChildItem -Recurse -File -Include *generate*.py,*generate*.js,*image*.py,*image*.js,*fallback-generation.md
```

Prioritize files whose names or parent folders indicate:

- `xinghe`
- `deployment`
- `generate_image`
- `generate_batch`
- `fallback-generation`
- `image helper`

If a related skill provides `references/fallback-generation.md`, read it before using that route.

## Backend Contract

For each generated asset, preserve:

- one image per call
- requested asset type and final role
- Product Master Description
- product consistency and physics locks
- marketplace language
- output file path

Do not write credentials into files. Use existing `.env`, environment variables, or the current tool configuration only.

## Fallback Rules

If no fixed route is available, fails, or cannot save/display the result, use built-in `image_gen`.

When falling back, report:

- attempted route
- failure reason at a high level
- final route used

