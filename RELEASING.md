# Publishing Mind updates

Mind checks the latest GitHub Release in `rileously/Mind` at startup.

For every new version:

1. Increase `__version__` in `mind/__init__.py`.
2. Run `./build_exe.ps1`.
3. Test the executable in `artifacts/Mind.exe`.
4. Run `./publish_release.ps1 -Version 0.3.2 -Notes "Describe the changes"`.

The publishing script uploads both `Mind.exe` and `Mind.exe.sha256`. Installed copies detect a newer semantic version, download the executable over HTTPS, verify the published SHA-256 checksum, preserve the previous executable as `Mind.previous.exe`, install the update, and reopen Mind.
