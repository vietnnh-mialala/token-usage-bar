# winget manifests

> **Status: prepared, never submitted.** These manifests are kept in sync with
> each release, but no PR has gone to `microsoft/winget-pkgs`, so
> `winget install` / `winget upgrade` do **not** work for this package today —
> `winget search TokenUsageBar` finds nothing. Updating happens through the
> tray's *Check for updates…* instead. Submitting is worth it only if someone
> other than the author wants to install it.

Once submitted, these let people install with
`winget install vietnnh-mialala.TokenUsageBar` and update with `winget upgrade`.

## Publishing a version
1. Create the GitHub release `vX.Y.Z` with `TokenUsageBar.exe` attached
   (the `Release` GitHub Action does this on a `vX.Y.Z` tag).
2. Get the exe's SHA256 (printed by the Action, or
   `(Get-FileHash TokenUsageBar.exe -Algorithm SHA256).Hash`).
3. In all three YAML files bump `PackageVersion`, and in the installer manifest
   set the matching `InstallerUrl` and `InstallerSha256`.
4. Validate locally:  `winget validate .\winget`
   and (optional) `winget install --manifest .\winget`.
5. Submit the three files to <https://github.com/microsoft/winget-pkgs> under
   `manifests/v/vietnnh-mialala/TokenUsageBar/X.Y.Z/` (a PR; CI there auto-checks).

> First publish requires your `Publisher`/`PackageIdentifier` to pass winget's
> review once; later versions are mostly automated.
