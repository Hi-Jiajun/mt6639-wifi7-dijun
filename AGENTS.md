# Fork maintenance

- This is the independent MT6639 research fork, not the TWRP workspace.
- `origin` is `xiatian410/mt6639-wifi7-dijun`; `upstream` is `Hi-Jiajun/mt6639-wifi7-dijun`.
- Keep original attribution and GPL license. Maintain conclusions with their exact version and evidence scope.
- Do not operate a phone, physical block device or wireless settings. Use offline inputs and fixtures.
- Follow the next steps in `docs/研究-OS4新版兼容与模块状态卡-20261115.md`; do not turn historical feature lists into repeated full audits.
- Run relevant checks once per implementation change; broaden only for a concrete failure or coverage gap. Distinguish fixture results from hardware behavior.
- Synchronize coherent source/docs/test batches to the authorized fork and verify remote main equals the local commit before claiming publication.
- Exclude raw device logs, identifiers, credentials, original firmware/drivers, native binaries and built ZIP/APK artifacts from Git. Keep local research inputs outside this checkout.
